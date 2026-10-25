"""Text-turn terminal decisions and bounded tool-argument generation."""

import asyncio
import json

import pytest
from fastapi.responses import StreamingResponse

from inference_api_support import (
    EDIT,
    READ,
    body,
    state,
    inference_app as inference_app,
    inference_server as inference_server,
)


def tool_payload(env, name="Read", *, failed=True, stream=False):
    payload = body(env, "Inspect the fixture file", [READ, EDIT], stream=stream)
    payload["messages"] += [
        {
            "role": "assistant",
            "content": [
                {
                    "type": "tool_use",
                    "id": "fixture-call",
                    "name": name,
                    "input": {"file_path": str(env.root / "sample.py")},
                }
            ],
        },
        {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": "fixture-call",
                    "content": "<tool_use_error>fixture failure</tool_use_error>"
                    if failed
                    else "1→fixture data",
                    "is_error": failed,
                }
            ],
        },
    ]
    return payload


async def render(api, current):
    response = await api._handle_text_turn(None, current)
    if not isinstance(response, StreamingResponse):
        return (
            "".join(
                x.get("text", "")
                for x in response["content"]
                if x.get("type") == "text"
            ),
            response["stop_reason"],
            response,
        )
    chunks = []
    async for part in response.body_iterator:
        chunks.append(part.decode() if isinstance(part, bytes) else part)
    events = [
        json.loads(line[6:])
        for line in "".join(chunks).splitlines()
        if line.startswith("data: ")
    ]
    text = "".join(event.get("delta", {}).get("text", "") for event in events)
    stop = next(
        e["delta"]["stop_reason"] for e in events if e["type"] == "message_delta"
    )
    assert events[-1]["type"] == "message_stop"
    return text, stop, events


@pytest.mark.parametrize("stream", [False, True])
@pytest.mark.parametrize(
    "branch",
    ["suggestion", "recovery_stop", "recovery_retry", "local_blocked", "policy_denial"],
)
def test_terminal_text_decision_never_calls_model(inference_server, branch, stream):
    env = inference_server
    payload = tool_payload(env, stream=stream)
    payload["max_tokens"] = 1
    current = state(env, payload)
    current.requires_tool = False
    if branch == "suggestion":
        current.normalized.mode = env.api.PromptMode.SUGGESTION
        current.current_workflow = None
    elif branch.startswith("recovery"):
        current.current_workflow = "failure_recovery"
        current.workflow_step = "stop" if branch == "recovery_stop" else "retry_once"
        current.last_workflow_error = "fixture failure"
        current.collected_summaries = ["Read fixture failed"]
    elif branch == "local_blocked":
        current.current_workflow = "local_implementation"
        current.workflow_step = "blocked"
        current.last_workflow_error = "<tool_use_error>fixture failure</tool_use_error>"
    else:
        current.current_workflow = None
        current.policy_denial = "user_forbids_subagents"
    text, stop, _ = asyncio.run(render(env.api, current))
    assert stop == "end_turn" and env.generator.calls == []
    if branch == "suggestion":
        assert text == ""
    elif branch.startswith("recovery") or branch == "local_blocked":
        assert "fixture failure" in text
    else:
        assert text.strip()


@pytest.mark.parametrize("stream", [False, True])
def test_project_summary_clears_stale_denial_and_generates_requested_answer(
    inference_server, monkeypatch, stream
):
    env = inference_server
    current = state(env, tool_payload(env, failed=False, stream=stream))
    current.current_workflow = "project_analysis"
    current.workflow_step = "summarize"
    current.collected_summaries = ["src contains fixture entrypoints"]
    current.covered_directories = ["src", "tests"]
    current.covered_deep_branches = ["src/core"]
    current.policy_denial = "stale fixture denial"
    current.missing_file_target = True
    env.generator.outputs.append("Fixture project summary")
    env.generator.stream_chunks = ["Fixture project", " summary"]
    monkeypatch.setattr(env.cuda, "is_available", lambda: True)
    text, stop, _ = asyncio.run(render(env.api, current))
    assert text == "Fixture project summary" and stop == "end_turn"
    assert current.policy_denial is None and current.missing_file_target is False
    assert (
        len(env.generator.calls) == 1
        and env.generator.calls[0]["params"].max_tokens <= 768
    )
    assert env.cuda.empty_cache.call_count == (0 if stream else 1)


@pytest.mark.parametrize("stream", [False, True])
def test_blocked_state_without_tool_error_can_still_answer(inference_server, stream):
    env = inference_server
    current = state(env, tool_payload(env, failed=False, stream=stream))
    current.current_workflow = "local_implementation"
    current.workflow_step = "blocked"
    current.requires_tool = False
    env.generator.outputs.append("Fixture explanation")
    env.generator.stream_chunks = ["Fixture explanation"]
    text, stop, _ = asyncio.run(render(env.api, current))
    assert (
        text == "Fixture explanation"
        and stop == "end_turn"
        and len(env.generator.calls) == 1
    )


@pytest.mark.parametrize(
    "first",
    [
        "not JSON",
        '{"old_string":"old"}',
        "[tool_use]\nEdit\n[/tool_use]",
    ],
)
def test_tool_argument_retry_is_bounded_and_second_valid_result_is_used(
    inference_server, first
):
    env = inference_server
    path = env.root / "sample.py"
    path.write_text("old")
    current = state(env, body(env, "Modify the fixture file", [EDIT]))
    expected = {"file_path": str(path), "old_string": "old", "new_string": "new"}
    env.generator.outputs.extend([first, json.dumps(expected)])
    result = asyncio.run(
        env.api._generate_validated_tool_input(None, current, EDIT, filename=str(path))
    )
    assert (
        result == expected
        and len(env.generator.calls) == 2
        and path.read_text() == "old"
    )
    assert (
        "Previous" in env.generator.calls[1]["prompt"]
        or "previous" in env.generator.calls[1]["prompt"]
    )


@pytest.mark.parametrize(
    "responses",
    [
        ["bad one", "bad two"],
        ['{"old_string":"old"}', "{}"],
    ],
)
def test_exhausted_argument_attempts_return_no_false_tool(inference_server, responses):
    env = inference_server
    current = state(env, body(env, "Modify fixture", [EDIT]))
    env.generator.outputs.extend(responses)
    result = asyncio.run(
        env.api._generate_validated_tool_input(
            None, current, EDIT, filename=str(env.root / "sample.py")
        )
    )
    assert result is None and len(env.generator.calls) == 2


@pytest.mark.parametrize("replacement", ["", "Привет 🐙\nnext line", "new"])
def test_valid_argument_generation_preserves_text_and_fills_target(
    inference_server, replacement
):
    env = inference_server
    path = env.root / "sample.py"
    path.write_text("old")
    current = state(env, body(env, "Modify fixture", [EDIT]))
    env.generator.outputs.append(
        json.dumps({"old_string": "old", "new_string": replacement}, ensure_ascii=False)
    )
    result = asyncio.run(
        env.api._generate_validated_tool_input(None, current, EDIT, filename=str(path))
    )
    assert result == {
        "file_path": str(path),
        "old_string": "old",
        "new_string": replacement,
    }
    assert len(env.generator.calls) == 1 and path.read_text() == "old"


def test_argument_generation_rejects_outside_path_on_every_retry(inference_server):
    env = inference_server
    project = env.root / "project"
    project.mkdir()
    path = env.root / "outside.py"
    path.write_text("old")
    payload = body(env, "Modify the fixture project", [EDIT])
    payload["system"] = f"Primary working directory: {project}"
    current = state(env, payload)
    args = {"file_path": str(path), "old_string": "old", "new_string": "new"}
    env.generator.outputs.extend([json.dumps(args), json.dumps(args)])
    assert (
        asyncio.run(env.api._generate_validated_tool_input(None, current, EDIT)) is None
    )
    assert len(env.generator.calls) == 2 and path.read_text() == "old"
    assert "path_outside_project_root" in current.policy_denial


def test_argument_generation_uses_the_target_files_context_in_model_prompt(
    inference_server,
):
    env = inference_server
    first, second = env.root / "one/main.py", env.root / "two/main.py"
    for path in [first, second]:
        path.parent.mkdir()
        path.write_text("old")
    payload = body(env, "Modify first fixture file", [READ, EDIT])
    for index, (path, text) in enumerate(
        [(first, "correct file context"), (second, "unrelated file context")]
    ):
        payload["messages"] += [
            {
                "role": "assistant",
                "content": [
                    {
                        "type": "tool_use",
                        "id": str(index),
                        "name": "Read",
                        "input": {"file_path": str(path)},
                    }
                ],
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": str(index),
                        "content": "1→" + text,
                    }
                ],
            },
        ]
    current = state(env, payload)
    expected = {"file_path": str(first), "old_string": "old", "new_string": "new"}
    env.generator.outputs.append(json.dumps(expected))
    assert (
        asyncio.run(
            env.api._generate_validated_tool_input(
                None, current, EDIT, filename=str(first)
            )
        )
        == expected
    )
    prompt = env.generator.calls[0]["prompt"]
    assert "correct file context" in prompt and "unrelated file context" not in prompt
    assert first.read_text() == second.read_text() == "old"


@pytest.mark.parametrize("stream", [False, True])
def test_http_finished_bash_error_has_same_stop_reason_as_stream(
    inference_server, stream
):
    env = inference_server
    payload = body(env, "Inspect fixture command", [], max_tokens=1, stream=stream)
    payload["messages"] += [
        {
            "role": "assistant",
            "content": [
                {
                    "type": "tool_use",
                    "id": "failed-command",
                    "name": "Bash",
                    "input": {"command": "fixture-failed-command"},
                }
            ],
        },
        {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": "failed-command",
                    "content": "<tool_use_error>fixture command failed</tool_use_error>",
                    "is_error": True,
                }
            ],
        },
    ]
    response = env.client.post("/v1/messages", json=payload)
    assert response.status_code == 200, response.text
    if stream:
        events = [
            json.loads(line[6:])
            for line in response.text.splitlines()
            if line.startswith("data: ")
        ]
        assert (
            next(
                e["delta"]["stop_reason"]
                for e in events
                if e["type"] == "message_delta"
            )
            == "end_turn"
        )
        text = "".join(e.get("delta", {}).get("text", "") for e in events)
    else:
        assert response.json()["stop_reason"] == "end_turn"
        text = "".join(e.get("text", "") for e in response.json()["content"])
    assert "fixture command failed" in text and env.generator.calls == []


@pytest.mark.parametrize("stream", [False, True])
def test_generated_response_keeps_real_token_limit_stop_reason(
    inference_server, stream
):
    env = inference_server
    env.generator.outputs.append("X")
    env.generator.stream_chunks = ["X"]
    response = env.client.post(
        "/v1/messages",
        json=body(env, "Answer the fixture question", [], max_tokens=1, stream=stream),
    )
    assert response.status_code == 200, response.text
    if stream:
        events = [
            json.loads(line[6:])
            for line in response.text.splitlines()
            if line.startswith("data: ")
        ]
        assert (
            next(
                e["delta"]["stop_reason"]
                for e in events
                if e["type"] == "message_delta"
            )
            == "max_tokens"
        )
    else:
        assert response.json()["stop_reason"] == "max_tokens"
    assert len(env.generator.calls) == 1


def recovery_state(env, name, error, *, tools=None, exists=True):
    from inference_api_support import WRITE

    path = env.root / "sample.py"
    if exists:
        path.write_text("old fixture")
    payload = tool_payload(env, name)
    payload["tools"] = tools if tools is not None else [READ, EDIT, WRITE]
    payload["messages"][-1]["content"][0]["content"] = (
        f"<tool_use_error>{error}</tool_use_error>"
    )
    current = state(env, payload)
    current.current_workflow = "local_implementation"
    current.workflow_step = "continue"
    return current, path


def test_recovery_reads_existing_file_when_edit_requires_prior_read(inference_server):
    env = inference_server
    current, path = recovery_state(env, "Edit", "File has not been read yet")
    tool = asyncio.run(
        env.api._local_implementation_recoverable_tool_use(None, current)
    )
    assert tool["name"] == "Read" and tool["input"] == {
        "file_path": str(path),
        "offset": 1,
        "limit": 400,
    }
    assert env.generator.calls == [] and path.read_text() == "old fixture"


@pytest.mark.parametrize(
    "workflow,mode,error",
    [
        ("other", "tool_result", "File has not been read yet"),
        ("local_implementation", "normal", "File has not been read yet"),
        ("local_implementation", "tool_result", "Permission denied"),
    ],
)
def test_recovery_does_not_rewrite_unrelated_states_or_errors(
    inference_server, workflow, mode, error
):
    env = inference_server
    current, _ = recovery_state(env, "Edit", error)
    current.current_workflow = workflow
    if mode == "normal":
        current.normalized.mode = state(
            env, body(env, "Ordinary fixture question", [])
        ).normalized.mode
    assert (
        asyncio.run(env.api._local_implementation_recoverable_tool_use(None, current))
        is None
    )
    assert env.generator.calls == []


@pytest.mark.parametrize("valid", [True, False])
def test_replace_not_found_regenerates_bounded_input_or_falls_back_to_read(
    inference_server, valid
):
    env = inference_server
    current, path = recovery_state(env, "Edit", "String to replace not found")
    expected = {
        "file_path": str(path),
        "old_string": "old fixture",
        "new_string": "new fixture",
    }
    env.generator.outputs.extend(
        [json.dumps(expected)] if valid else ["not JSON", "still not JSON"]
    )
    tool = asyncio.run(
        env.api._local_implementation_recoverable_tool_use(None, current)
    )
    assert tool["name"] == ("Edit" if valid else "Read")
    assert tool["input"] == (
        expected if valid else {"file_path": str(path), "offset": 1, "limit": 400}
    )
    assert len(env.generator.calls) == (1 if valid else 2)
    assert "copy old_string EXACTLY" in env.generator.calls[0]["prompt"]
    assert path.read_text() == "old fixture"


@pytest.mark.parametrize("exists", [False, True])
def test_write_recovery_emits_complete_input_without_executing_it(
    inference_server, exists
):
    env = inference_server
    current, path = recovery_state(
        env, "Write", "required parameter `content` is missing", exists=exists
    )
    expected = {"file_path": str(path), "content": "fixture replacement"}
    env.generator.outputs.append(
        json.dumps(expected) if exists else "fixture replacement"
    )
    tool = asyncio.run(
        env.api._local_implementation_recoverable_tool_use(None, current)
    )
    assert tool["name"] == "Write" and tool["input"] == expected
    assert len(env.generator.calls) == 1
    assert (path.read_text() == "old fixture") if exists else not path.exists()


def test_recovery_does_not_invent_an_unadvertised_edit_tool(inference_server):
    env = inference_server
    current, _ = recovery_state(env, "Edit", "InputValidationError", tools=[READ])
    assert (
        asyncio.run(env.api._local_implementation_recoverable_tool_use(None, current))
        is None
    )
    assert env.generator.calls == []
