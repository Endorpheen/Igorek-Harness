"""Malformed protocol frames must not kill a bundled MCP adapter."""

import json

import pytest

from factory.mcp_servers import coingecko_server as coins
from factory.mcp_servers import tavily_server as tavily
from test_bundled_mcp_stdio_contracts import (
    exchange,
    isolate_provider_environment as isolate_provider_environment,
)


@pytest.mark.parametrize("module", [coins, tavily], ids=["coingecko", "tavily"])
@pytest.mark.parametrize("frame", [[], [1], None, 42, "bad-request", True])
def test_nonobject_request_returns_error_then_serves_next_frame(
    module, monkeypatch, frame
):
    responses = exchange(
        module,
        monkeypatch,
        [json.dumps(frame), {"id": "next-valid", "method": "tools/list"}],
    )
    assert len(responses) == 2
    assert responses[0]["id"] is None
    assert responses[0]["error"]["code"] == -32600
    assert "object" in responses[0]["error"]["message"].lower()
    assert responses[1]["id"] == "next-valid" and responses[1]["result"]["tools"]


@pytest.mark.parametrize("module", [coins, tavily], ids=["coingecko", "tavily"])
@pytest.mark.parametrize("params", [["bad"], "bad", 7, True, [], "", 0, False])
def test_nonobject_call_params_preserve_request_id_and_keep_loop_alive(
    module, monkeypatch, params
):
    responses = exchange(
        module,
        monkeypatch,
        [
            {"id": "invalid-params", "method": "tools/call", "params": params},
            {"id": "next-valid", "method": "tools/list"},
        ],
    )
    assert len(responses) == 2
    assert responses[0]["id"] == "invalid-params"
    assert responses[0]["error"]["code"] == -32602
    assert "params" in responses[0]["error"]["message"].lower()
    assert responses[1]["id"] == "next-valid" and responses[1]["result"]["tools"]


def test_tavily_parse_error_does_not_reuse_previous_request_id(monkeypatch):
    responses = exchange(
        tavily,
        monkeypatch,
        [{"id": "previous", "method": "ping"}, "{", {"id": "next", "method": "ping"}],
    )
    assert [row["id"] for row in responses] == ["previous", None, "next"]
    assert responses[1]["error"]["code"] == -32000
    assert responses[2]["result"] == {}


@pytest.mark.parametrize("module", [coins, tavily], ids=["coingecko", "tavily"])
@pytest.mark.parametrize("optional", [{}, {"params": None}])
def test_omitted_and_null_params_keep_existing_tool_error_and_next_request(
    module, monkeypatch, optional
):
    replies = exchange(
        module,
        monkeypatch,
        [
            {"id": "empty-call", "method": "tools/call", **optional},
            {"id": "next", "method": "tools/list"},
        ],
    )
    assert replies[0]["id"] == "empty-call"
    assert replies[0]["error"]["code"] == (-32601 if module is coins else -32000)
    assert replies[1]["id"] == "next" and replies[1]["result"]["tools"]
