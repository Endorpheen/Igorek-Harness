"""Exercise MCP tool refresh through the real executor with synthetic boundaries."""

import copy
import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from factory.core import cc_backend_executor as e


SUGGEST = {
    "name": "suggest_enable_mcp",
    "description": "Fixture MCP activation.",
    "input_schema": {
        "type": "object",
        "properties": {"server_name": {"type": "string"}, "reason": {"type": "string"}},
        "required": ["server_name"],
    },
}
DYNAMIC = {
    "name": "mcp__fixture__lookup",
    "description": "Fixture lookup.",
    "input_schema": {"type": "object", "properties": {"query": {"type": "string"}}},
}


def tool_names(payload):
    return [
        item.get("name") or item.get("function", {}).get("name")
        for item in payload.get("tools", [])
    ]


@pytest.fixture
def flow(tmp_path, monkeypatch):
    monkeypatch.setattr(
        e, "_resolve_backend_context_window", lambda **_: (65536, "fixture")
    )
    monkeypatch.setattr(e, "is_interrupt_requested", lambda: False)
    monkeypatch.setattr(e, "task_should_stop", lambda _: False)
    monkeypatch.setattr(
        e,
        "get_tool_schemas",
        lambda: copy.deepcopy(
            [SUGGEST, DYNAMIC, {"description": "Nameless fixture schema"}]
        ),
    )

    def run(api, response, *, vault=False):
        requests = []
        raw = response if isinstance(response, str) else json.dumps(response)
        execute = Mock(return_value=raw)
        monkeypatch.setattr(e, "execute_tool", execute)
        enabled = copy.deepcopy([SUGGEST])
        before = copy.deepcopy(enabled)

        def model(url, payload, headers, *args, **kwargs):
            requests.append(copy.deepcopy(payload))
            first = len(requests) == 1
            arguments = {
                "server_name": "fixture-server",
                "reason": "fixture requested capability",
            }
            if api == "openai":
                message = {"role": "assistant", "content": "Fixture finished."}
                if first:
                    message = {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [
                            {
                                "id": "fixture-call",
                                "type": "function",
                                "function": {
                                    "name": "suggest_enable_mcp",
                                    "arguments": json.dumps(arguments),
                                },
                            }
                        ],
                    }
                return {
                    "choices": [
                        {
                            "message": message,
                            "finish_reason": "tool_calls" if first else "stop",
                        }
                    ],
                    "usage": {"prompt_tokens": 1, "completion_tokens": 2},
                }, 1
            blocks = (
                [
                    {
                        "type": "tool_use",
                        "id": "fixture-call",
                        "name": "suggest_enable_mcp",
                        "input": arguments,
                    }
                ]
                if first
                else [{"type": "text", "text": "Fixture finished."}]
            )
            return {
                "content": blocks,
                "stop_reason": "tool_use" if first else "end_turn",
                "usage": {"input_tokens": 1, "output_tokens": 2},
            }, 1

        monkeypatch.setattr(e, "_http_post", model)
        monkeypatch.setattr(e, "_http_post_streaming_openai", model)
        artifacts = tmp_path / "artifacts"
        artifacts.mkdir(exist_ok=True)
        result = e.execute_cc_backend_task(
            base_url="http://127.0.0.1:1/v1",
            model="fixture",
            system_prompt="Use permitted fixture tools.",
            user_message="Use the requested fixture capability.",
            workdir=str(tmp_path),
            artifact_workdir=str(artifacts),
            data_root=str(tmp_path / "data"),
            api_format=api,
            enabled_tools=enabled,
            checkpoint_config={"enabled": False},
            vault_store_provider=(lambda _: "unused fixture") if vault else None,
        )
        return SimpleNamespace(
            result=result,
            requests=requests,
            execute=execute,
            enabled=enabled,
            before=before,
        )

    return run


@pytest.mark.parametrize("api", ["anthropic", "openai", "openai-responses"])
@pytest.mark.parametrize("vault", [False, True])
def test_successful_reload_updates_next_payload_without_mutating_caller(
    flow, api, vault
):
    value = flow(
        api,
        {
            "decision": "enabled",
            "hot_reload": {
                "status": "ok",
                "tools": [
                    "suggest_enable_mcp",
                    " mcp__fixture__lookup ",
                    "mcp__fixture__lookup",
                    "unknown-fixture",
                    "",
                    None,
                ],
            },
        },
        vault=vault,
    )
    assert value.result["status"] == "ok" and len(value.requests) == 2
    assert tool_names(value.requests[0]) == ["suggest_enable_mcp"]
    assert sorted(tool_names(value.requests[1])) == [
        "mcp__fixture__lookup",
        "suggest_enable_mcp",
    ]
    assert value.enabled == value.before
    value.execute.assert_called_once()


@pytest.mark.parametrize("api", ["anthropic", "openai", "openai-responses"])
@pytest.mark.parametrize(
    "response",
    [
        "broken json",
        None,
        [],
        {"hot_reload": None},
        {"hot_reload": []},
        {"hot_reload": {"status": "error", "tools": ["mcp__fixture__lookup"]}},
        {"hot_reload": {"status": "ok", "tools": None}},
        {"hot_reload": {"status": "ok", "tools": []}},
        {"hot_reload": {"status": "ok", "tools": ["unknown-fixture"]}},
    ],
)
def test_unusable_reload_never_invents_enabled_tools(flow, api, response):
    value = flow(api, response)
    assert value.result["status"] == "ok" and len(value.requests) == 2
    assert tool_names(value.requests[1]) == ["suggest_enable_mcp"]
    assert value.enabled == value.before


@pytest.mark.parametrize("api", ["anthropic", "openai", "openai-responses"])
def test_pending_approval_stops_before_another_model_round(flow, api):
    value = flow(
        api,
        {
            "decision": "pending",
            "request_id": " request-fixture ",
            "server_name": " fixture-server ",
            "profile": "fixture",
            "reason": "fixture reason",
            "description": "fixture description",
            "unexpected_field": "fixture-internal",
        },
    )
    assert value.result["status"] == "waiting_approval"
    assert len(value.requests) == 1
    assert value.result["approval_blocked_tools"][0]["request_id"] == "request-fixture"
    assert value.result["approval_blocked_tools"][0]["server_name"] == "fixture-server"
    assert "unexpected_field" not in json.dumps(value.result)
    value.execute.assert_called_once()
