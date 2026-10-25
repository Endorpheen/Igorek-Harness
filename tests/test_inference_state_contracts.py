"""Session metadata, read continuation and workflow summary contracts."""

import asyncio
import json

import pytest

from inference_api_support import (
    inference_app as inference_app,
    inference_server as inference_server,
)
from inference_api_support import READ, body, state


@pytest.mark.parametrize(
    "user_id", ["123", "true", "false", "null", "[]", '"quoted"', "plain-user"]
)
def test_opaque_metadata_user_id_does_not_crash_http_request(inference_server, user_id):
    env = inference_server
    response = env.client.post(
        "/v1/messages",
        json={
            "messages": [{"role": "user", "content": "question"}],
            "metadata": {"user_id": user_id},
        },
    )
    assert response.status_code == 200, response.text
    assert response.json()["content"][0]["text"] == "Fixture answer"


def test_session_root_cache_reuses_only_matching_session_and_explicit_root_wins(
    inference_server,
):
    env = inference_server
    one = env.root / "one"
    two = env.root / "two"
    one.mkdir()
    two.mkdir()
    payload = body(
        env,
        "Fixture request",
        [],
        metadata={"user_id": json.dumps({"session_id": "one"})},
    )
    payload["system"] = f"Primary working directory: {one}"
    first = state(env, payload)
    assert first.project_root == str(one) and first.session_id == "one"
    payload.pop("system")
    assert state(env, payload).project_root == str(one)
    payload["metadata"]["user_id"] = json.dumps({"session_id": "unrelated"})
    assert state(env, payload).project_root is None
    payload["metadata"]["user_id"] = json.dumps({"session_id": "one"})
    payload["system"] = f"Primary working directory: {two}"
    assert state(env, payload).project_root == str(two)


def test_blank_session_id_does_not_create_cache_entry(inference_server):
    env = inference_server
    payload = body(
        env, "Fixture request", [], metadata={"user_id": '{"session_id":" "}'}
    )
    assert state(env, payload).session_id is None


def read_payload(env, read_entries):
    payload = body(env, "Inspect project files", [READ])
    messages = [{"role": "user", "content": "Inspect project files"}]
    for index, (path, offset, limit, result) in enumerate(read_entries):
        messages.extend(
            [
                {
                    "role": "assistant",
                    "content": [
                        {
                            "type": "tool_use",
                            "id": f"read-{index}",
                            "name": "Read",
                            "input": {
                                "file_path": str(path),
                                "offset": offset,
                                "limit": limit,
                            },
                        }
                    ],
                },
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": f"read-{index}",
                            "content": result,
                        }
                    ],
                },
            ]
        )
    payload["messages"] = messages
    return payload


@pytest.mark.parametrize("stream", [False, True])
def test_full_read_chunk_continues_at_next_line_with_same_limit(
    inference_server, stream
):
    env = inference_server
    path = env.root / "large.py"
    path.write_text("\n".join(f"value_{i}=0" for i in range(1000)))
    lines = "\n".join(f"{i}→value_{i}=0" for i in range(1, 401))
    payload = read_payload(env, [(path, 1, 400, lines)])
    payload["stream"] = stream
    env.generator.outputs.append("Read next fixture chunk")
    response = env.client.post("/v1/messages", json=payload)
    assert response.status_code == 200, response.text
    if stream:
        events = [
            json.loads(line[6:])
            for line in response.text.splitlines()
            if line.startswith("data: ")
        ]
        tool_input = json.loads(
            next(
                event["delta"]["partial_json"]
                for event in events
                if event.get("delta", {}).get("type") == "input_json_delta"
            )
        )
    else:
        tool = next(
            item for item in response.json()["content"] if item["type"] == "tool_use"
        )
        assert tool["name"] == "Read"
        tool_input = tool["input"]
    assert tool_input == {"file_path": str(path), "offset": 401, "limit": 400}
    assert len(env.generator.calls) == 1


def test_two_complete_file_reads_switch_to_bounded_summary(inference_server):
    env = inference_server
    first = env.root / "first.py"
    second = env.root / "second.py"
    first.write_text("def first(): pass")
    second.write_text("def second(): pass")
    payload = read_payload(
        env,
        [
            (first, 1, 400, "1→def first():\n2→    pass"),
            (second, 1, 400, "1→def second():\n2→    pass"),
        ],
    )
    payload["max_tokens"] = 10000
    current = state(env, payload)
    assert (
        current.current_workflow == "file_investigation"
        and current.workflow_step == "summarize"
    )
    effective = env.api._effective_normalized_for_text_turn(current)
    assert (
        str(first) in effective.prompt
        and str(second) in effective.prompt
        and "first" in effective.prompt
        and "second" in effective.prompt
    )
    assert env.api._build_generation_params(current).max_tokens == 2500
    env.generator.outputs.append("Bounded file summary")
    response = env.client.post("/v1/messages", json=payload)
    assert response.status_code == 200, response.text
    assert response.json()["content"] == [
        {"type": "text", "text": "Bounded file summary"}
    ]
    assert env.generator.calls[-1]["params"].max_tokens == 2500


@pytest.mark.parametrize(
    "workflow,limit",
    [("project_analysis", 768), ("session_recall", 10000), ("tavily_research", 10000)],
)
def test_summary_prompts_use_collected_evidence_and_correct_generation_budget(
    inference_server, workflow, limit
):
    env = inference_server
    current = state(env, body(env, "Fixture request", []))
    current.req.max_tokens = 10000
    current.current_workflow = workflow
    current.workflow_step = "summarize"
    current.normalized.mode = "tool_result"
    current.collected_summaries = [
        "old-omitted-evidence",
        "summary-one",
        "summary-two",
        "summary-three",
        "summary-four",
    ]
    current.covered_directories = ["src"]
    current.covered_deep_branches = ["src/core"]
    current.collected_targets = ["https://example.test/source"]
    effective = env.api._effective_normalized_for_text_turn(current)
    assert effective is not current.normalized and "summary-four" in effective.prompt
    if workflow == "project_analysis":
        assert (
            "old-omitted-evidence" not in effective.prompt
            and "src/core" in effective.prompt
        )
    assert env.api._build_generation_params(current).max_tokens == limit


def test_invalid_model_tool_json_never_creates_a_tool_response(inference_server):
    env = inference_server
    current = state(env, body(env, "Fixture request", [READ]))
    env.generator.outputs.extend(["not-json", "not-json"])
    assert (
        asyncio.run(env.api._maybe_model_tool_use(None, current, require_tool=True))
        is None
    )
    assert len(env.generator.calls) == 2


def test_error_write_result_never_uses_success_shortcut(inference_server):
    env = inference_server
    payload = body(env, "Fixture request", [])
    payload["messages"] = [
        {
            "role": "assistant",
            "content": [
                {
                    "type": "tool_use",
                    "id": "one",
                    "name": "Write",
                    "input": {
                        "file_path": str(env.root / "file"),
                        "content": "fixture",
                    },
                }
            ],
        },
        {
            "role": "user",
            "content": [
                {
                    "type": "tool_result",
                    "tool_use_id": "one",
                    "is_error": True,
                    "content": "File created successfully at: misleading",
                }
            ],
        },
    ]
    current = state(env, payload)
    assert env.api._should_short_circuit_tool_result(current) == (False, "")
