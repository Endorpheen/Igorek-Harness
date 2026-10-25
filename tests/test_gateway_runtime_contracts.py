"""Gateway lifecycle and event contracts with real isolated task persistence."""

import io
import json
from unittest.mock import Mock
from urllib import error

import pytest

from factory.core import gateway_runtime as g
from factory.core.schema import initialize_database, open_db
from factory.core.runtime import get_db_path


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    data = tmp_path / "data"
    profiles = data / "profiles"
    profiles.mkdir(parents=True)
    (profiles / "fixture.json").write_text(
        json.dumps(
            {
                "backend": "cc-backend",
                "model": "fixture-model",
                "base_url": "http://127.0.0.1:1",
            }
        )
    )
    monkeypatch.setenv("FACTORY_DATA_DIR", str(data))
    monkeypatch.setenv("IGOREK_FACTORY_HOME", str(data))
    monkeypatch.setenv("FACTORY_PROFILES_DIR", str(profiles))
    initialize_database(get_db_path())
    result = g.GatewayRuntime(
        profile="fixture",
        workdir=str(tmp_path),
        runtime_api_base_url="http://127.0.0.1:1",
        runtime_api_key="synthetic-key",
    )
    result.app_server = Mock()
    return result


@pytest.fixture
def source():
    return g.GatewaySource(
        platform="fixture",
        chat_id="chat-1",
        user_id="user-1",
        username="fixture",
        chat_session="session-1",
    )


@pytest.mark.parametrize(
    "backend,provider,expected",
    [
        ("cc-backend", "local", True),
        ("codex", "openrouter", True),
        ("codex", "openai", False),
        ("claude", "z.ai", False),
    ],
)
def test_transport_choice_respects_backend(runtime, backend, provider, expected):
    assert (
        runtime.use_task_queue_transport(
            profile_config={"backend": backend}, provider_name=provider
        )
        is expected
    )
    assert runtime.get_profile_config()["model"] == "fixture-model"


@pytest.mark.parametrize(
    "status", ["ok", "fail", "dead_letter", "waiting_approval", "cancelled"]
)
def test_handle_message_persists_metadata_and_stops_at_terminal_state(
    runtime, source, tmp_path, monkeypatch, status
):
    original_get = g.get_task
    monkeypatch.setattr(
        g, "get_task", lambda identifier: {**original_get(identifier), "status": status}
    )
    attachment = tmp_path / "artifact.txt"
    attachment.write_text("fixture")
    result = runtime.handle_message(
        source=source,
        message=g.GatewayMessage(
            "question", [{"path": str(attachment), "name": "artifact.txt"}]
        ),
        provider_override="fixture-provider",
        model_override="fixture-model",
        workspace_task_id="parent-fixture",
        auto_approve=True,
        history_context=[{"role": "user", "text": "earlier"}],
    )
    assert (
        result.transport == "task_queue" and result.status == status and result.task_id
    )
    saved = original_get(result.task_id)
    metadata = saved["metadata"]
    assert (
        metadata["original_message_text"] == "question"
        and metadata["session"] == "session-1"
    )
    assert (
        metadata["chat_id"] == "chat-1"
        and metadata["user_id"] == "user-1"
        and metadata["username"] == "fixture"
    )
    assert (
        metadata["auto_approve"] and metadata["workspace_task_id"] == "parent-fixture"
    )
    assert (
        metadata["provider_override"] == "fixture-provider"
        and metadata["model_override"] == "fixture-model"
    )
    with open_db(get_db_path()) as conn:
        assert (
            conn.execute(
                "SELECT parent_id FROM tasks WHERE task_id=?", (result.task_id,)
            ).fetchone()[0]
            == "parent-fixture"
        )
    assert "earlier" in saved["input_text"]
    assert metadata["attachments"][0]["path"] == str(attachment)


def test_missing_task_id_is_actionable_before_polling(runtime, source, monkeypatch):
    monkeypatch.setattr(g, "create_task", lambda **_: {})
    with pytest.raises(RuntimeError, match="task_id"):
        runtime.handle_message(source=source, message=g.GatewayMessage("question"))


def test_queue_progress_only_forwards_structured_tools(runtime, source, monkeypatch):
    monkeypatch.setattr(g, "get_task", lambda _: {"status": "ok"})
    monkeypatch.setattr(
        g,
        "_read_trace_events",
        lambda *_: (
            [
                {
                    "kind": g.CcTraceEvent.TOOL_REQUEST_BATCH,
                    "tools": [
                        None,
                        {"name": "Read", "input": {"file_path": "fixture"}},
                    ],
                },
                {"kind": "unrelated"},
            ],
            2,
        ),
    )
    callback = Mock()
    result = runtime.handle_message(
        source=source, message=g.GatewayMessage("question"), progress_callback=callback
    )
    assert result.status == "ok"
    callback.assert_called_once_with(
        "tool.started", tool_name="Read", args={"file_path": "fixture"}
    )


def test_foundation_failure_does_not_break_message_submission(runtime):
    runtime.memory_manager = Mock()
    runtime.memory_manager.foundation_context.side_effect = OSError(
        "fixture unavailable"
    )
    assert runtime.foundation_context() == ""


def test_trace_reader_ignores_bad_lines_and_non_objects(tmp_path):
    path = tmp_path / "trace"
    path.write_text('bad\n[]\n{"kind":"one"}\n\n')
    assert g._read_trace_events(path, 0) == ([{"kind": "one"}], 4)
    assert g._read_trace_events(tmp_path / "missing", 7) == ([], 7)


def test_live_session_reuses_transport_without_starting_duplicate(runtime, source):
    state = g.GatewayAppServerState(
        runtime_session_id="existing", thread_id="thread", event_after_seq=8
    )
    assert runtime.ensure_app_server_session(state=state, source=source) is state
    runtime.app_server.get_events.assert_called_once_with(
        "existing", after_seq=8, limit=1
    )
    runtime.app_server.start_session.assert_not_called()


def test_broken_session_reconnects_and_resets_transient_state(runtime, source):
    state = g.GatewayAppServerState(
        runtime_session_id="broken",
        thread_id="thread",
        event_after_seq=8,
        recent_file_paths=["old"],
    )
    runtime.app_server.get_events.side_effect = OSError("fixture gone")
    runtime.app_server.start_session.return_value = {
        "session_id": "new",
        "thread_id": "thread",
    }
    result = runtime.ensure_app_server_session(
        state=state, source=source, provider_override="fixture", model_override="model"
    )
    assert (
        result.runtime_session_id == "new"
        and result.event_after_seq == 0
        and not result.recent_file_paths
    )
    assert runtime.app_server.start_session.call_args.kwargs["thread_id"] == "thread"
    assert (
        runtime.app_server.start_session.call_args.kwargs["provider_override"]
        == "fixture"
    )


@pytest.mark.parametrize(
    "message",
    [
        "no rollout found for thread id fixture",
        "thread/resume failed",
        "thread not found",
    ],
)
def test_stale_thread_is_retried_once_as_fresh_session(runtime, source, message):
    state = g.GatewayAppServerState(thread_id="stale")
    runtime.app_server.start_session.side_effect = [
        RuntimeError(message),
        {"session_id": "new", "thread_id": "fresh"},
    ]
    result = runtime.ensure_app_server_session(state=state, source=source)
    assert (
        result.thread_id == "fresh" and runtime.app_server.start_session.call_count == 2
    )
    assert runtime.app_server.start_session.call_args.kwargs["thread_id"] is None


def test_unrelated_start_failure_is_not_relabelled_as_stale_thread(runtime, source):
    runtime.app_server.start_session.side_effect = RuntimeError("permission denied")
    with pytest.raises(RuntimeError, match="permission denied"):
        runtime.ensure_app_server_session(
            state=g.GatewayAppServerState(thread_id="thread"), source=source
        )
    assert runtime.app_server.start_session.call_count == 1


def notification(method, **params):
    return {"kind": "notification", "method": method, "params": params}


def event(runtime, state, item, **kwargs):
    return runtime._handle_app_server_event(
        state=state,
        event=item,
        auto_approve=kwargs.get("auto", False),
        auto_approve_scope=kwargs.get("scope", "session"),
    )


def approval(identifier="approval-1"):
    return {
        "kind": "server_request",
        "request_id": identifier,
        "security_approval_id": "security-1",
        "method": "item/commandExecution/requestApproval",
        "params": {"command": "echo fixture", "cwd": "/tmp"},
    }


def test_approval_is_deduplicated_and_resolution_only_removes_matching_id(runtime):
    state = g.GatewayAppServerState(runtime_session_id="session")
    assert event(runtime, state, approval())["waiting_on_approval"]
    event(runtime, state, approval())
    event(runtime, state, approval("approval-2"))
    assert (
        len(state.pending_approvals) == 2
        and state.pending_approvals[0].security_approval_id == "security-1"
    )
    runtime.resolve_app_server_approval(
        state=state, request_id="approval-1", approved=False, remember_for_session=False
    )
    assert [item.request_id for item in state.pending_approvals] == ["approval-2"]
    runtime.app_server.resolve_approval.assert_called_once_with(
        "session", "approval-1", approved=False, remember_for_session=False
    )


@pytest.mark.parametrize("scope,remember", [("session", True), ("turn", False)])
def test_explicit_auto_approval_obeys_requested_scope(runtime, scope, remember):
    state = g.GatewayAppServerState(runtime_session_id="session")
    result = event(runtime, state, approval(), auto=True, scope=scope)
    assert not result["waiting_on_approval"] and state.pending_approvals == []
    assert (
        runtime.app_server.resolve_approval.call_args.kwargs["remember_for_session"]
        is remember
    )


def test_failed_auto_approval_remains_pending_for_owner(runtime):
    state = g.GatewayAppServerState(runtime_session_id="session")
    runtime.app_server.resolve_approval.side_effect = OSError("fixture failure")
    assert event(runtime, state, approval(), auto=True)["waiting_on_approval"]
    assert len(state.pending_approvals) == 1


def test_approval_without_runtime_session_is_explicit(runtime):
    with pytest.raises(RuntimeError, match="не активна"):
        runtime.resolve_app_server_approval(
            state=g.GatewayAppServerState(),
            request_id="one",
            approved=True,
            remember_for_session=False,
        )


def test_agent_delta_cache_preserves_whitespace_between_chunks(runtime):
    state = g.GatewayAppServerState()
    event(
        runtime,
        state,
        notification(
            "item/started", item={"id": "message", "type": "agentMessage", "text": ""}
        ),
    )
    event(
        runtime,
        state,
        notification("item/agentMessage/delta", itemId="message", delta="Hello "),
    )
    event(
        runtime,
        state,
        notification("item/agentMessage/delta", itemId="message", delta="world"),
    )
    assert state.item_cache["message"]["text"] == "Hello world"


def test_completed_message_and_turn_flags_are_distinct(runtime):
    state = g.GatewayAppServerState()
    result = event(
        runtime,
        state,
        notification(
            "item/completed",
            item={"id": "one", "type": "agentMessage", "text": "answer"},
        ),
    )
    assert result["assistant_messages"] == ["answer"] and not result["turn_completed"]
    assert event(runtime, state, notification("turn/completed"))["turn_completed"]
    assert event(
        runtime,
        state,
        notification(
            "thread/status/changed", status={"activeFlags": ["waitingOnApproval"]}
        ),
    )["waiting_on_approval"]
    assert event(runtime, state, {"kind": "error", "message": "fixture error"})[
        "assistant_messages"
    ] == ["Runtime error: fixture error"]
    assert event(runtime, state, {"kind": "diagnostic"})["assistant_messages"] == []


def test_file_change_deliverables_exclude_internal_artifacts(runtime, tmp_path):
    normal = tmp_path / "answer.txt"
    normal.write_text("answer")
    internal = tmp_path / ".tool_results"
    internal.mkdir()
    (internal / "secret.txt").write_text("internal")
    state = g.GatewayAppServerState()
    changes = [
        {"path": "answer.txt"},
        {"path": ".tool_results/secret.txt"},
        {"path": "absent"},
        None,
        {"path": "answer.txt"},
    ]
    event(
        runtime,
        state,
        notification(
            "item/completed",
            item={"id": "files", "type": "fileChange", "changes": changes},
        ),
    )
    assert state.recent_file_paths == [str(normal)]
    event(runtime, state, approval())
    event(
        runtime, state, notification("serverRequest/resolved", requestId="approval-1")
    )
    assert state.pending_approvals == []


def test_continue_turn_aggregates_unique_messages_and_updates_sequence(runtime):
    state = g.GatewayAppServerState(runtime_session_id="session")
    runtime.app_server.get_events.return_value = {
        "events": [
            {
                "seq": 4,
                **notification(
                    "item/completed", item={"type": "agentMessage", "text": "answer"}
                ),
            },
            {
                "seq": 2,
                **notification(
                    "item/completed", item={"type": "agentMessage", "text": "answer"}
                ),
            },
            {"seq": 5, **notification("turn/completed")},
        ]
    }
    outcome = runtime.continue_app_server_turn(state=state, turn_timeout_s=2)
    assert (
        outcome["status"] == "completed"
        and outcome["messages"] == ["answer"]
        and state.event_after_seq == 5
    )


def test_continue_turn_returns_pending_approval_without_spinning(runtime):
    state = g.GatewayAppServerState(runtime_session_id="session")
    runtime.app_server.get_events.return_value = {"events": [{"seq": 1, **approval()}]}
    outcome = runtime.continue_app_server_turn(state=state, turn_timeout_s=2)
    assert outcome["status"] == "waiting_on_approval" and len(outcome["approvals"]) == 1
    assert runtime.app_server.get_events.call_count == 1


def test_zero_timeout_preserves_existing_deliverables(runtime):
    state = g.GatewayAppServerState(
        runtime_session_id="session", recent_file_paths=["fixture"]
    )
    result = runtime.continue_app_server_turn(state=state, turn_timeout_s=0)
    assert result == {"status": "timeout", "messages": [], "files": ["fixture"]}


def test_message_forwarding_uses_runtime_and_returns_same_state(runtime, source):
    state = g.GatewayAppServerState(runtime_session_id="session")
    runtime.app_server.get_events.side_effect = [
        {"events": []},
        {"events": [notification("turn/completed")]},
    ]
    actual, outcome = runtime.handle_app_server_message(
        state=state, source=source, message=g.GatewayMessage("question")
    )
    assert actual is state and outcome["status"] == "completed"
    runtime.app_server.send_message.assert_called_once_with("session", "question")
    reset = runtime.close_app_server_session(state)
    assert reset.runtime_session_id is None and reset.pending_approvals == []
    runtime.app_server.close_session.assert_called_once_with("session")


def test_close_failure_does_not_keep_stale_local_state(runtime):
    runtime.app_server.close_session.side_effect = OSError("fixture gone")
    assert (
        runtime.close_app_server_session(
            g.GatewayAppServerState(runtime_session_id="session")
        ).runtime_session_id
        is None
    )


class Response:
    def __init__(self, body):
        self.body = body

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def read(self):
        return self.body


@pytest.mark.parametrize(
    "body,expected",
    [(b'{"ok":true}', {"ok": True}), (b"[]", {"result": []}), (b"", {})],
)
def test_runtime_client_serializes_auth_and_json(monkeypatch, body, expected):
    calls = []

    def open_url(request, **kwargs):
        calls.append((request, kwargs))
        return Response(body)

    monkeypatch.setattr(g.urllib_request, "urlopen", open_url)
    client = g._RuntimeAppServerApiClient("http://127.0.0.1:1/", "synthetic-key")
    assert client._request_json("POST", "/fixture", {"text": "русский"}) == expected
    request, options = calls[0]
    assert request.get_header("X-api-key") == "synthetic-key" and json.loads(
        request.data
    ) == {"text": "русский"}
    assert request.full_url == "http://127.0.0.1:1/fixture" and options["timeout"] == 60


@pytest.mark.parametrize(
    "failure,match",
    [
        (error.URLError("fixture offline"), "unavailable"),
        (
            error.HTTPError(
                "http://fixture", 403, "denied", {}, io.BytesIO(b"fixture denied")
            ),
            "HTTP 403",
        ),
    ],
)
def test_runtime_client_transport_errors_preserve_cause(monkeypatch, failure, match):
    monkeypatch.setattr(g.urllib_request, "urlopen", Mock(side_effect=failure))
    client = g._RuntimeAppServerApiClient("http://127.0.0.1:1", "")
    with pytest.raises(RuntimeError, match=match) as raised:
        client.get_events("session", after_seq=1, limit=2)
    assert raised.value.__cause__ is failure


def test_runtime_client_endpoint_packets_have_exact_scopes(monkeypatch):
    client = g._RuntimeAppServerApiClient("http://127.0.0.1:1", "")
    send = Mock(return_value={"ok": True})
    monkeypatch.setattr(client, "_request_json", send)
    client.start_session(
        profile="fixture",
        cwd="/tmp",
        thread_id="thread",
        chat_session="chat",
        provider_override="provider",
        model_override="model",
    )
    assert send.call_args.args == (
        "POST",
        "/app-server/sessions",
        {
            "profile": "fixture",
            "cwd": "/tmp",
            "chat_session": "chat",
            "thread_id": "thread",
            "provider_override": "provider",
            "model_override": "model",
        },
    )
    client.send_message("session", "text")
    assert send.call_args.args == (
        "POST",
        "/app-server/sessions/session/messages",
        {"text": "text"},
    )
    client.resolve_approval(
        "session", "request", approved=False, remember_for_session=True
    )
    assert send.call_args.args[2] == {"action": "reject", "remember_for_session": True}
    client.close_session("session")
    assert send.call_args.args == ("DELETE", "/app-server/sessions/session")
