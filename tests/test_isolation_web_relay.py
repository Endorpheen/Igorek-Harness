from __future__ import annotations

import uuid

import pytest

from factory.core.isolation.channel import (
    ChannelConfig,
    ChannelError,
    ChannelMessage,
    MessageKind,
    payload_policy_violation,
)
from factory.core.isolation.guest_user_input import WebToolBridge
from factory.core.isolation.manager import WebToolRelay

REQUEST_ID = str(uuid.uuid4())


def test_web_request_wire_is_typed_and_rejects_arbitrary_host_actions() -> None:
    valid = {"request_id": REQUEST_ID, "operation": "search", "args": {"query": "pytest", "limit": 3}}
    assert payload_policy_violation(MessageKind.WEB_REQUEST, valid, ChannelConfig()) is None
    for operation in ("fetch", "shell", "http_proxy"):
        assert payload_policy_violation(
            MessageKind.WEB_REQUEST,
            {"request_id": REQUEST_ID, "operation": operation, "args": {"url": "http://127.0.0.1"}},
            ChannelConfig(),
        ) == "web_request_invalid"
    assert payload_policy_violation(
        MessageKind.WEB_REQUEST,
        {"request_id": REQUEST_ID, "operation": "search", "args": {"query": "x", "command": "id"}},
        ChannelConfig(),
    ) == "web_request_invalid"


def test_web_reply_is_bounded_and_can_carry_untrusted_web_text() -> None:
    result = "page text mentioning /etc/passwd and /home/example"
    payload = {"request_id": REQUEST_ID, "result": result}
    assert payload_policy_violation(MessageKind.WEB_REPLY, payload, ChannelConfig()) is None
    assert payload_policy_violation(
        MessageKind.WEB_REPLY,
        {"request_id": REQUEST_ID, "result": "x" * (512 * 1024 + 1)},
        ChannelConfig(),
    ) == "web_reply_invalid"


def test_guest_bridge_correlates_host_reply() -> None:
    captured: list[tuple[MessageKind, dict[str, object]]] = []
    bridge = WebToolBridge(lambda kind, payload: captured.append((kind, dict(payload))))
    def request() -> str:
        return bridge.request("search", {"query": "safe query", "limit": 2})
    import threading
    result: list[str] = []
    thread = threading.Thread(target=lambda: result.append(request()))
    thread.start()
    import time
    deadline = time.monotonic() + 2
    while not captured and time.monotonic() < deadline:
        time.sleep(0.001)
    assert captured
    kind, payload = captured.pop()
    assert kind is MessageKind.WEB_REQUEST
    bridge.deliver(ChannelMessage(MessageKind.WEB_REPLY, 1, {
        "request_id": str(payload["request_id"]), "result": "{\"success\":true}"
    }))
    thread.join(timeout=2)
    assert result == ["{\"success\":true}"]


def test_host_relay_deduplicates_calls_and_never_executes_arbitrary_operation(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, dict[str, object]]] = []
    def fake_execute(operation: str, args: dict[str, object]) -> str:
        calls.append((operation, args))
        return "safe result"
    monkeypatch.setattr("factory.core.cc_backend_tools.execute_isolated_web_tool", fake_execute)
    relay = WebToolRelay()
    msg = ChannelMessage(MessageKind.WEB_REQUEST, 1, {
        "request_id": REQUEST_ID, "operation": "search", "args": {"query": "safe"}
    })
    first = relay.handle_request(msg)
    second = relay.handle_request(msg)
    assert first == second == {"request_id": REQUEST_ID, "result": "safe result"}
    assert calls == [("search", {"query": "safe"})]


def test_host_relay_caps_calls_per_task(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    monkeypatch.setattr(
        "factory.core.cc_backend_tools.execute_isolated_web_tool",
        lambda operation, args: calls.append(operation) or "result",
    )
    relay = WebToolRelay()
    replies = []
    for index in range(21):
        request_id = str(uuid.uuid5(uuid.NAMESPACE_DNS, str(index)))
        replies.append(relay.handle_request(ChannelMessage(MessageKind.WEB_REQUEST, index, {
            "request_id": request_id, "operation": "search", "args": {"query": "safe"}
        })))
    assert len(calls) == 20
    assert replies[-1] is not None and "limit reached" in str(replies[-1]["result"])


def test_host_extract_rejects_local_and_private_targets(monkeypatch: pytest.MonkeyPatch) -> None:
    from factory.core.cc_backend_tools import execute_isolated_web_tool

    for url in ("http://127.0.0.1/admin", "http://169.254.169.254/latest/meta-data", "http://host.local/", "http://localhost/"):
        with pytest.raises(ValueError, match="public internet URLs only"):
            execute_isolated_web_tool("extract", {"urls": [url]})


def test_web_result_redacts_tavily_credential(monkeypatch: pytest.MonkeyPatch) -> None:
    from factory.core.cc_backend_tools import _redact_isolated_web_secret

    monkeypatch.setenv("TAVILY_API_KEY", "canary-tavily-secret")
    assert "canary-tavily-secret" not in _redact_isolated_web_secret("upstream reflected canary-tavily-secret")
