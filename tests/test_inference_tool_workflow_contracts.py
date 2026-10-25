"""HTTP tool plans, tool-result continuation and bounded input generation."""

import asyncio
import json

import pytest

from inference_api_support import (
    READ,
    WRITE,
    BASH,
    EDIT,
    body,
    state,
    inference_app as inference_app,
    inference_server as inference_server,
)


def wire_events(response):
    return [
        json.loads(line[6:])
        for line in response.text.splitlines()
        if line.startswith("data: ")
    ]


@pytest.mark.parametrize("stream", [False, True])
def test_explicit_existing_file_emits_bounded_read_without_reading_on_server(
    inference_server, stream
):
    env = inference_server
    path = env.root / "sample.py"
    path.write_text("private fixture file body")
    env.generator.outputs.extend(['{"intent":"other"}', "Read fixture file"])
    response = env.client.post(
        "/v1/messages", json=body(env, "Read sample.py", [READ], stream=stream)
    )
    assert response.status_code == 200, response.text
    if stream:
        events = wire_events(response)
        block = next(
            e["content_block"]
            for e in events
            if e["type"] == "content_block_start"
            and e["content_block"]["type"] == "tool_use"
        )
        argument = json.loads(
            next(
                e["delta"]["partial_json"]
                for e in events
                if e.get("delta", {}).get("type") == "input_json_delta"
            )
        )
        assert block["name"] == "Read"
    else:
        tool = next(
            item for item in response.json()["content"] if item["type"] == "tool_use"
        )
        assert response.json()["stop_reason"] == "tool_use" and tool["name"] == "Read"
        argument = tool["input"]
    assert argument == {"file_path": str(path), "offset": 1, "limit": 400}
    assert "private fixture file body" not in response.text
    assert path.read_text() == "private fixture file body"


@pytest.mark.parametrize("stream", [False, True])
def test_write_plan_fills_embedded_content_but_never_executes_write(
    inference_server, stream
):
    env = inference_server
    env.generator.outputs.extend(
        [
            '{"intent":"local_implementation"}',
            json.dumps(
                {"tool": "Write", "input": {"file_path": str(env.root / "hello.txt")}}
            ),
            "Create fixture file",
        ]
    )
    payload = body(
        env,
        "Create hello.txt with this content:\n```text\nhello fixture\n```",
        [WRITE],
        stream=stream,
    )
    response = env.client.post("/v1/messages", json=payload)
    assert response.status_code == 200, response.text
    if stream:
        events = wire_events(response)
        tool_input = json.loads(
            next(
                e["delta"]["partial_json"]
                for e in events
                if e.get("delta", {}).get("type") == "input_json_delta"
            )
        )
    else:
        tool = next(
            block for block in response.json()["content"] if block["type"] == "tool_use"
        )
        assert tool["name"] == "Write"
        tool_input = tool["input"]
    assert tool_input == {
        "file_path": str(env.root / "hello.txt"),
        "content": "hello fixture",
    }
    assert not (env.root / "hello.txt").exists()


def test_bash_plan_preserves_command_without_spawning_any_process(inference_server):
    env = inference_server
    env.generator.outputs.extend(
        [
            '{"intent":"execute_command"}',
            '{"intent":"execute_command"}',
            '{"tool":"Bash","input":{"command":"ls"}}',
            "List fixture files",
        ]
    )
    response = env.client.post(
        "/v1/messages", json=body(env, "Run the shell command ls", [BASH])
    )
    assert response.status_code == 200, response.text
    tool = next(
        block for block in response.json()["content"] if block["type"] == "tool_use"
    )
    assert tool["name"] == "Bash" and tool["input"]["command"] == "ls"
    assert response.json()["stop_reason"] == "tool_use"


@pytest.mark.parametrize(
    "output,expected",
    [
        ("plain fixture output", "plain fixture output"),
        ("", "не вывела ничего"),
        ('{"fixture":1}', '"fixture": 1'),
        ("[1,2]", "[\n  1,\n  2\n]"),
        ('{"symbol":"FIXTURE","price":"7"}', "FIXTURE"),
    ],
)
def test_bash_shortcut_returns_exact_result_when_no_workflow_owns_turn(
    inference_server, output, expected
):
    env = inference_server
    payload = body(env, "Run pwd", [BASH])
    payload["messages"] = [
        {"role": "user", "content": "Run pwd"},
        {
            "role": "assistant",
            "content": [
                {
                    "type": "tool_use",
                    "id": "call-1",
                    "name": "Bash",
                    "input": {"command": "pwd"},
                }
            ],
        },
        {
            "role": "user",
            "content": [
                {"type": "tool_result", "tool_use_id": "call-1", "content": output}
            ],
        },
    ]
    current = state(env, payload)
    current.current_workflow = None
    handled, text = asyncio.run(
        env.api._maybe_short_circuit_execute_command_tool_result(None, current)
    )
    assert handled and expected in text
    assert env.generator.calls == []


@pytest.mark.parametrize("stream", [False, True])
def test_single_successful_write_can_end_after_planner_completion(
    inference_server, stream
):
    env = inference_server
    env.generator.outputs.append('{"tool":null}')
    payload = body(env, "Create hello.txt", [WRITE], stream=stream)
    payload["messages"] = [
        {"role": "user", "content": "Create hello.txt"},
        {
            "role": "assistant",
            "content": [
                {
                    "type": "tool_use",
                    "id": "call-1",
                    "name": "Write",
                    "input": {
                        "file_path": str(env.root / "hello.txt"),
                        "content": "hello",
                    },
                }
            ],
        },
        {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": "call-1",
                    "content": f"File created successfully at: {env.root}/hello.txt",
                }
            ],
        },
    ]
    response = env.client.post("/v1/messages", json=payload)
    assert response.status_code == 200, response.text
    if stream:
        assert wire_events(response)[-1]["type"] == "message_stop"
    else:
        assert response.json()["stop_reason"] == "end_turn" and all(
            not block.get("text") for block in response.json()["content"]
        )
    assert len(env.generator.calls) == 1


@pytest.mark.parametrize(
    "first",
    [
        "not-json",
        "{}",
        '<tool_call>{"name":"Edit"}</tool_call>',
        json.dumps({"file_path": "sample.py", "old_string": 3, "new_string": "new"}),
    ],
)
def test_invalid_edit_input_is_retried_with_real_schema_and_policy(
    inference_server, first
):
    env = inference_server
    path = env.root / "sample.py"
    path.write_text("old")
    current = state(env, body(env, "Replace old in sample.py", [EDIT]))
    env.generator.outputs.extend(
        [
            first,
            json.dumps(
                {"file_path": str(path), "old_string": "old", "new_string": "new"}
            ),
        ]
    )
    result = asyncio.run(
        env.api._generate_validated_tool_input(None, current, EDIT, filename=str(path))
    )
    assert result == {"file_path": str(path), "old_string": "old", "new_string": "new"}
    assert len(env.generator.calls) == 2 and path.read_text() == "old"
    assert (
        "previous" in env.generator.calls[-1]["prompt"].lower()
        or "error" in env.generator.calls[-1]["prompt"].lower()
    )


def test_invalid_input_retry_is_bounded_at_two_attempts(inference_server):
    env = inference_server
    path = env.root / "sample.py"
    path.write_text("old")
    current = state(env, body(env, "Edit sample.py", [EDIT]))
    env.generator.outputs.extend(["not-json", "not-json", "must remain unused"])
    assert (
        asyncio.run(
            env.api._generate_validated_tool_input(
                None, current, EDIT, filename=str(path)
            )
        )
        is None
    )
    assert len(env.generator.calls) == 2 and list(env.generator.outputs) == [
        "must remain unused"
    ]


def test_generated_edit_cannot_escape_project_root(inference_server):
    env = inference_server
    project = env.root / "project"
    project.mkdir()
    (project / "sample.py").write_text("old")
    other = env.root / "outside.py"
    other.write_text("outside")
    payload = body(env, "Edit sample.py", [EDIT])
    payload["system"] = f"Primary working directory: {project}"
    current = state(env, payload)
    assert current.project_root == str(project)
    env.generator.outputs.extend(
        [
            json.dumps(
                {
                    "file_path": str(other),
                    "old_string": "outside",
                    "new_string": "changed",
                }
            )
        ]
        * 2
    )
    assert (
        asyncio.run(env.api._generate_validated_tool_input(None, current, EDIT)) is None
    )
    assert current.policy_denial and other.read_text() == "outside"


def test_model_file_content_is_unfenced_and_empty_output_rejected(inference_server):
    env = inference_server
    current = state(env, body(env, "Create a greeting file", [WRITE]))
    env.generator.outputs.extend(['```python\nprint("hello")\n```', "   "])
    assert (
        asyncio.run(env.api._generate_file_content_from_request(current, "hello.py"))
        == 'print("hello")'
    )
    assert (
        asyncio.run(env.api._generate_file_content_from_request(current, "hello.py"))
        is None
    )


def test_generated_preamble_failure_does_not_abort_tool_turn(inference_server):
    env = inference_server
    current = state(env, body(env, "Read sample.py", [READ]))
    env.generator.outputs.append(RuntimeError("fixture unavailable"))
    assert (
        asyncio.run(
            env.api._generate_tool_preamble(
                current, {"name": "Read", "input": {"file_path": "sample.py"}}
            )
        )
        == ""
    )


@pytest.mark.parametrize(
    "intent,workflow",
    [
        ("project_analysis", "project_analysis"),
        ("file_investigation", "file_investigation"),
        ("local_implementation", "local_implementation"),
        ("session_recall", "session_recall"),
        ("tavily_research", "tavily_research"),
        ("other", None),
    ],
)
def test_initial_workflow_is_selected_by_model_intent_result(
    inference_server, intent, workflow
):
    env = inference_server
    current = state(env, body(env, "Fixture request", [READ, BASH, WRITE]))
    env.generator.outputs.append(json.dumps({"intent": intent}))
    asyncio.run(env.api._maybe_start_initial_workflow(None, current))
    assert current.current_workflow == workflow and len(env.generator.calls) == 1


def test_unknown_intent_falls_back_to_other(inference_server):
    env = inference_server
    env.generator.outputs.append('{"intent":"invented"}')
    assert asyncio.run(env.api._route_user_intent(None, "Fixture request")) == "other"


def test_required_tool_retry_is_bounded_and_reuses_policy(inference_server):
    env = inference_server
    current = state(env, body(env, "Run pwd", [BASH]))
    env.generator.outputs.extend(
        ["no valid plan", '{"tool":"Bash","input":{"command":"pwd"}}']
    )
    result = asyncio.run(
        env.api._maybe_model_tool_use(None, current, require_tool=True)
    )
    assert (
        result["name"] == "Bash"
        and result["input"]["command"] == "pwd"
        and len(env.generator.calls) == 2
    )


def test_unknown_planned_tool_is_not_emitted(inference_server):
    env = inference_server
    current = state(env, body(env, "Fixture request", [READ]))
    env.generator.outputs.append('{"tool":"Unknown","input":{}}')
    assert asyncio.run(env.api._maybe_model_tool_use(None, current)) is None


@pytest.mark.parametrize("is_error", [False, True])
@pytest.mark.parametrize("tool_count", [1, 2])
def test_write_shortcut_understands_result_marker_and_preserves_error_flag(
    inference_server, is_error, tool_count
):
    env = inference_server
    payload = body(env, "Create fixture files", [WRITE])
    calls = [
        {
            "type": "tool_use",
            "id": f"call-{i}",
            "name": "Write",
            "input": {
                "file_path": str(env.root / f"file-{i}.txt"),
                "content": "fixture",
            },
        }
        for i in range(tool_count)
    ]
    payload["messages"] = [
        {"role": "user", "content": "Create fixture files"},
        {"role": "assistant", "content": calls},
        {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": "call-0",
                    "content": f"File created successfully at: {env.root}/file-0.txt",
                    "is_error": is_error,
                }
            ],
        },
    ]
    current = state(env, payload)
    handled, _ = env.api._should_short_circuit_tool_result(current)
    assert handled is (tool_count == 1 and not is_error)


def test_bash_result_in_active_workflow_is_given_to_model_without_losing_facts(
    inference_server,
):
    env = inference_server
    payload = body(env, "Run pwd", [BASH])
    payload["messages"] = [
        {"role": "user", "content": "Run pwd"},
        {
            "role": "assistant",
            "content": [
                {
                    "type": "tool_use",
                    "id": "call-1",
                    "name": "Bash",
                    "input": {"command": "pwd"},
                }
            ],
        },
        {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": "call-1",
                    "content": "/tmp/fixture-project",
                }
            ],
        },
    ]
    env.generator.outputs.extend(['{"tool":null}', "/tmp/fixture-project"])
    response = env.client.post("/v1/messages", json=payload)
    assert (
        response.status_code == 200
        and response.json()["content"][0]["text"] == "/tmp/fixture-project"
    )
    assert all("/tmp/fixture-project" in call["prompt"] for call in env.generator.calls)
