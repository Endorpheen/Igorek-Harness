"""Managed app-server state uses real temporary approval storage."""

import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from factory.core import codex_app_server_adapter as adapter_module
from factory.core import codex_app_server_runtime as m
from factory.core.approval_runtime import ApprovalRuntimeBridge
from factory.core.schema import initialize_database
from factory.core.security_store import SecurityStore


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    db = tmp_path / "security.db"
    initialize_database(str(db))
    bridge = ApprovalRuntimeBridge(SecurityStore(str(db)))
    monkeypatch.setattr(m, "get_data_root", lambda: tmp_path)
    monkeypatch.setattr(m, "ApprovalRuntimeBridge", lambda: bridge)
    monkeypatch.setattr(
        m,
        "get_profile_config",
        lambda name: {"backend": "codex", "model": "profile-model"},
    )
    settings = Mock(
        return_value={
            "model": " launch-model ",
            "provider_display": "Fixture provider",
            "env": {"FIXTURE_FLAG": "yes"},
        }
    )
    monkeypatch.setattr(m, "resolve_codex_launch_settings", settings)
    monkeypatch.setattr(
        m, "foundation_prompt_context", lambda root: "Fixture foundation"
    )
    instances = []

    class Adapter:
        resolve_server_request = (
            adapter_module.CodexAppServerSession.resolve_server_request
        )

        def __init__(self, **kwargs):
            self.kwargs = kwargs
            self.thread_id = None
            self.closed = False
            self.sent = []
            self.start_error = None
            self.message_error = None
            instances.append(self)

        def start(self, thread_id=None):
            self.thread_id = thread_id or "fixture-thread"

        def close(self):
            self.closed = True

        def send_user_message(self, text):
            if self.message_error:
                raise self.message_error
            self.last_text = text
            return {"turn": {"id": "fixture-turn"}}

        def _send(self, payload):
            self.sent.append(payload)

    monkeypatch.setattr(m, "CodexAppServerSession", Adapter)
    return SimpleNamespace(
        manager=m.CodexAppServerRuntime(),
        bridge=bridge,
        instances=instances,
        settings=settings,
        root=tmp_path,
    )


def start(env, **kwargs):
    result = env.manager.start_session(profile="fixture", cwd=str(env.root), **kwargs)
    return result, env.instances[-1]


def emit_request(
    adapter, identity=7, method="item/commandExecution/requestApproval", params=None
):
    adapter.kwargs["server_request_handler"](
        method,
        identity,
        params if params is not None else {"command": ["fixture-no-exec", "arg"]},
    )


@pytest.mark.parametrize("resume", [None, "existing-thread"])
def test_start_binds_profile_model_foundation_and_session_identity(runtime, resume):
    result, adapter = start(
        runtime,
        thread_id=resume,
        chat_session="fixture-chat",
        provider_override="override",
        model_override="override-model",
    )
    assert result["profile"] == "fixture" and result["chat_session"] == "fixture-chat"
    assert (
        result["model"] == "launch-model" and result["provider"] == "Fixture provider"
    )
    assert (
        result["thread_id"] == (resume or "fixture-thread")
        and result["pending_requests"] == []
    )
    assert adapter.kwargs["model_provider"] is None
    assert adapter.kwargs["developer_instructions"] == "Fixture foundation"
    assert adapter.kwargs["env"] == {"FIXTURE_FLAG": "yes"}
    events = runtime.manager.list_events(result["session_id"])
    assert events[-1]["event"] == ("session_resumed" if resume else "session_started")
    assert events[-1]["cwd"] == str(runtime.root)
    assert runtime.settings.call_args.kwargs == {
        "provider_override": "override",
        "model_override": "override-model",
    }


def test_non_codex_profile_is_rejected_without_creating_adapter(runtime, monkeypatch):
    monkeypatch.setattr(m, "get_profile_config", lambda name: {"backend": "cc-backend"})
    with pytest.raises(ValueError, match="only for codex profiles"):
        start(runtime)
    assert not runtime.instances


@pytest.mark.parametrize("settings", [{"env": []}, {"model": "", "env": None}])
def test_missing_launch_model_and_invalid_env_use_existing_fallbacks(runtime, settings):
    runtime.settings.return_value = settings
    result, adapter = start(runtime)
    assert result["model"] == "profile-model" and adapter.kwargs["env"] == {}


def test_native_notifications_and_errors_are_events_not_model_messages(runtime):
    result, adapter = start(runtime)
    adapter.kwargs["notification_handler"]("fixture/notice", {"value": 1})
    adapter.kwargs["error_handler"]("codex app-server stderr: fixture diagnostic")
    adapter.kwargs["error_handler"]("fixture connection closed")
    events = runtime.manager.list_events(result["session_id"])
    assert [event["kind"] for event in events] == [
        "lifecycle",
        "notification",
        "diagnostic",
        "error",
    ]
    assert [event["seq"] for event in events] == [1, 2, 3, 4]
    assert events[1]["params"] == {"value": 1}
    copied = runtime.manager.list_events(result["session_id"])
    copied[0]["kind"] = "changed outside"
    assert runtime.manager.list_events(result["session_id"])[0]["kind"] == "lifecycle"


@pytest.mark.parametrize("identity", [0, 7, "external-id"])
def test_remote_resolution_clears_the_matching_pending_request_even_for_zero(
    runtime, identity
):
    result, adapter = start(runtime)
    emit_request(adapter, identity)
    assert runtime.manager.get_session(result["session_id"])["pending_requests"][0][
        "request_id"
    ] == str(identity)
    adapter.kwargs["notification_handler"]("serverRequest/resolved", {"id": identity})
    assert runtime.manager.get_session(result["session_id"])["pending_requests"] == []


@pytest.mark.parametrize(
    "approved,remember", [(False, False), (False, True), (True, False), (True, True)]
)
def test_resolution_persists_user_decision_then_formats_native_reply(
    runtime, approved, remember
):
    result, adapter = start(runtime)
    emit_request(adapter, "request-7")
    pending = runtime.manager.get_session(result["session_id"])["pending_requests"][0]
    assert pending["native_policy_type"] == "codex_command_execution"
    decision = runtime.manager.resolve_request(
        result["session_id"],
        request_id="request-7",
        approved=approved,
        remember_for_session=remember,
    )
    assert (
        decision["ok"]
        and decision["security_approval_id"] == pending["security_approval_id"]
    )
    rows = runtime.bridge.store.list_approvals(
        session_id=result["session_id"], limit=10
    )
    row = next(
        row for row in rows if row["approval_id"] == pending["security_approval_id"]
    )
    assert row["status"] == ("approved" if approved else "rejected")
    expected = (
        "decline" if not approved else "acceptForSession" if remember else "accept"
    )
    assert adapter.sent == [{"id": "request-7", "result": {"decision": expected}}]
    assert runtime.manager.get_session(result["session_id"])["pending_requests"] == []
    assert (
        runtime.manager.list_events(result["session_id"])[-1]["kind"]
        == "client_resolution"
    )


def test_unknown_method_is_recorded_and_awaits_owner_without_execution(runtime):
    result, adapter = start(runtime)
    emit_request(
        adapter, 8, method="fixture/unknown", params={"reason": "fixture only"}
    )
    pending = runtime.manager.get_session(result["session_id"])["pending_requests"][0]
    assert pending["native_policy_type"] == "codex_unknown"
    assert adapter.sent == []
    recorded = json.loads(runtime.manager._unknown_methods_path.read_text())
    assert recorded["methods"]["fixture/unknown"]["count"] == 1
    assert recorded["methods"]["fixture/unknown"]["last_params_sample"] == {
        "reason": "fixture only"
    }


def test_send_message_records_input_and_result(runtime):
    result, adapter = start(runtime)
    assert runtime.manager.send_message(result["session_id"], "Привет 🐙") == {
        "ok": True,
        "thread_id": "fixture-thread",
    }
    assert adapter.last_text == "Привет 🐙"
    events = runtime.manager.list_events(result["session_id"])
    assert events[-2]["text"] == "Привет 🐙" and events[-1]["result"] == {
        "turn": {"id": "fixture-turn"}
    }
    adapter.message_error = RuntimeError("fixture turn failed")
    with pytest.raises(RuntimeError, match="fixture turn failed"):
        runtime.manager.send_message(result["session_id"], "again")
    assert (
        runtime.manager.list_events(result["session_id"])[-1]["kind"]
        == "client_message"
    )


def test_closing_one_session_rejects_its_pending_requests_only(runtime):
    first, adapter1 = start(runtime)
    second, adapter2 = start(runtime)
    emit_request(adapter1, 1)
    emit_request(adapter2, 2)
    runtime.manager.close_session(first["session_id"])
    assert adapter1.closed and not adapter2.closed
    assert (
        runtime.bridge.store.list_approvals(session_id=first["session_id"], limit=10)[
            0
        ]["status"]
        == "rejected"
    )
    assert (
        runtime.bridge.store.list_approvals(session_id=second["session_id"], limit=10)[
            0
        ]["status"]
        == "pending"
    )
    assert runtime.manager.get_session(second["session_id"])["pending_requests"]


def test_closing_still_releases_adapter_if_pending_storage_cleanup_fails(
    runtime, monkeypatch
):
    result, adapter = start(runtime)
    monkeypatch.setattr(
        runtime.bridge,
        "reject_pending_for_session",
        Mock(side_effect=OSError("fixture DB unavailable")),
    )
    with pytest.raises(OSError, match="fixture DB unavailable"):
        runtime.manager.close_session(result["session_id"])
    assert adapter.closed


@pytest.mark.parametrize("operation", ["get", "events", "message", "resolve", "close"])
def test_missing_session_is_not_recreated_implicitly(runtime, operation):
    calls = {
        "get": lambda: runtime.manager.get_session("missing"),
        "events": lambda: runtime.manager.list_events("missing"),
        "message": lambda: runtime.manager.send_message("missing", "text"),
        "resolve": lambda: runtime.manager.resolve_request(
            "missing", request_id="request", approved=False, remember_for_session=False
        ),
        "close": lambda: runtime.manager.close_session("missing"),
    }
    with pytest.raises(KeyError, match="session not found"):
        calls[operation]()
    assert not runtime.instances


def test_event_ring_is_bounded_and_cursor_does_not_repeat_records(runtime):
    result, adapter = start(runtime)
    session = runtime.manager._require_session(result["session_id"])
    for i in range(505):
        session.append_event({"kind": "fixture", "index": i})
    events = runtime.manager.list_events(result["session_id"], after_seq=0, limit=1000)
    assert len(events) == 500 and events[0]["seq"] == 7 and events[-1]["seq"] == 506
    page = runtime.manager.list_events(result["session_id"], after_seq=503, limit=2)
    assert [row["seq"] for row in page] == [504, 505]


@pytest.mark.parametrize(
    "initial", [{}, {"methods": []}, {"methods": {"fixture": "damaged"}}]
)
def test_unknown_method_journal_recovers_supported_metadata_shapes(runtime, initial):
    path = runtime.manager._unknown_methods_path
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(initial))
    runtime.manager._record_unknown_method(method="fixture", params={"value": 1})
    runtime.manager._record_unknown_method(method="fixture", params={"value": 2})
    row = json.loads(path.read_text())["methods"]["fixture"]
    assert row["count"] == 2 and row["last_params_sample"] == {"value": 2}


def test_unknown_method_journal_bounds_distinct_entries(runtime, monkeypatch):
    for i in range(55):
        monkeypatch.setattr(m, "time", SimpleNamespace(time=lambda i=i: 1000 + i))
        runtime.manager._record_unknown_method(method=f"fixture-{i}", params={})
    methods = json.loads(runtime.manager._unknown_methods_path.read_text())["methods"]
    assert len(methods) == 50 and "fixture-0" not in methods and "fixture-54" in methods


def test_unknown_method_journal_write_failure_remains_best_effort(runtime):
    path = runtime.manager._unknown_methods_path
    path.parent.mkdir(parents=True)
    path.mkdir()
    assert runtime.manager._record_unknown_method(method="fixture", params={}) is None
