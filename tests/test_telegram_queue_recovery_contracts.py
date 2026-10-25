"""Queued Telegram turns preserve approval recovery, state and owned cleanup."""

import json
import sqlite3
import threading
from contextlib import closing
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from test_telegram_workflow_contracts import bridge as bridge, session as session
from factory.core import mcp_config as mcp, telegram_bridge as t
from factory.core.cli_tasks import create_task, get_task
from factory.core.runtime import get_db_path, get_profile_path
from factory.core.user_input_store import (
    create_user_input_request,
    get_user_input_request,
)


def approval_task(bridge, session, server="fixture-server"):
    task = create_task(
        "Original fixture instruction",
        profile=bridge.profile,
        metadata={"workspace_task_id": "stable-workspace", "source": "fixture"},
    )
    with closing(sqlite3.connect(get_db_path())) as conn:
        conn.execute(
            "UPDATE tasks SET status='waiting_approval', started_at=42, completed_at=43, error_message='old error' WHERE task_id=?",
            (task["task_id"],),
        )
        conn.commit()
    session.pending_task_id = task["task_id"]
    request = create_user_input_request(
        task_id=task["task_id"],
        payload={"context": {"server_name": server, "reason": "fixture"}},
    )
    return task, request


@pytest.mark.parametrize("action", ["once", "session"])
def test_real_approval_enables_registered_server_and_resumes_same_task(
    bridge, session, action
):
    mcp.add_stdio_server("fixture-server", ["fixture-never-executed"], disabled=True)
    task, request = approval_task(bridge, session)
    before = get_task(task["task_id"])
    bridge._resolve_queued_mcp_approval(session, task_id=task["task_id"], action=action)
    after = get_task(task["task_id"])
    assert after["status"] == "queued"
    assert (
        after["input_text"] == before["input_text"]
        and after["metadata"] == before["metadata"]
    )
    assert (
        after["started_at"] is None
        and after["completed_at"] is None
        and after.get("error_message") is None
    )
    assert get_user_input_request(request["request_id"])["status"] == "submitted"
    assert "fixture-server" in mcp.get_profile_mcp_servers(bridge.profile)
    assert not mcp.list_mcp_servers()["fixture-server"].get("disabled", False)
    assert session.pending_task_id is None
    bridge._update_executor.submit.assert_called_once_with(
        bridge._watch_resumed_task, session, task["task_id"]
    )


@pytest.mark.parametrize("action", ["cancel", "reject", "unknown-action"])
def test_negative_decision_cancels_task_without_enabling_server(
    bridge, session, action
):
    mcp.add_stdio_server("fixture-server", ["fixture-never-executed"], disabled=True)
    task, request = approval_task(bridge, session)
    bridge._resolve_queued_mcp_approval(session, task_id=task["task_id"], action=action)
    assert get_user_input_request(request["request_id"])["status"] == "cancelled"
    assert get_task(task["task_id"])["status"] in {"cancelling", "cancelled"}
    assert mcp.list_mcp_servers()["fixture-server"]["disabled"] is True
    assert mcp.get_profile_mcp_servers(bridge.profile) == []
    assert session.pending_task_id is None
    bridge._update_executor.submit.assert_not_called()


@pytest.mark.parametrize("status", [None, "ok", "fail", "queued", "cancelled"])
def test_stale_callback_cannot_resolve_or_enable(bridge, session, monkeypatch, status):
    mcp.add_stdio_server("fixture-server", ["fixture-never-executed"], disabled=True)
    task, request = approval_task(bridge, session)
    monkeypatch.setattr(
        t, "get_task", lambda _: None if status is None else {"status": status}
    )
    bridge._resolve_queued_mcp_approval(session, task_id=task["task_id"], action="once")
    assert get_user_input_request(request["request_id"])["status"] == "pending"
    assert mcp.get_profile_mcp_servers(bridge.profile) == []
    bridge._update_executor.submit.assert_not_called()


def test_waiting_task_without_request_is_not_resumed(bridge, session):
    task, request = approval_task(bridge, session)
    t.resolve_user_input_request(request["request_id"], response=None, cancelled=True)
    bridge._resolve_queued_mcp_approval(session, task_id=task["task_id"], action="once")
    assert get_task(task["task_id"])["status"] == "waiting_approval"
    bridge._update_executor.submit.assert_not_called()


@pytest.mark.parametrize("failure", ["missing-server", "desktop-owner", "io"])
def test_failed_enable_keeps_request_pending_for_retry(
    bridge, session, monkeypatch, failure
):
    mcp.add_stdio_server("fixture-server", ["fixture-never-executed"], disabled=True)
    task, request = approval_task(
        bridge,
        session,
        server="unknown-server" if failure == "missing-server" else "fixture-server",
    )
    registry_path = mcp.get_mcp_registry_path()
    before = registry_path.read_bytes()
    profile_path = get_profile_path(bridge.profile)
    profile_before = profile_path.read_bytes()
    if failure == "desktop-owner":
        (mcp.get_mcp_dir() / "access.json").write_text(
            json.dumps({"version": 1, "grants": {}})
        )
    elif failure == "io":
        monkeypatch.setattr(
            mcp,
            "accept_mcp_enable_suggestion",
            Mock(side_effect=OSError("synthetic unavailable storage")),
        )
    with pytest.raises((KeyError, PermissionError, OSError)):
        bridge._resolve_queued_mcp_approval(
            session, task_id=task["task_id"], action="once"
        )
    assert get_user_input_request(request["request_id"])["status"] == "pending"
    assert get_task(task["task_id"])["status"] == "waiting_approval"
    assert session.pending_task_id == task["task_id"]
    assert registry_path.read_bytes() == before
    assert profile_path.read_bytes() == profile_before
    bridge._update_executor.submit.assert_not_called()


def test_desktop_policy_denial_never_changes_global_registry(bridge):
    mcp.add_stdio_server("fixture-server", ["fixture-never-executed"], disabled=True)
    registry = mcp.get_mcp_registry_path()
    before = registry.read_bytes()
    (mcp.get_mcp_dir() / "access.json").write_text('{"version":1,"grants":{}}')
    with pytest.raises(PermissionError, match="desktop_owner_required"):
        mcp.accept_mcp_enable_suggestion(bridge.profile, "fixture-server")
    assert registry.read_bytes() == before


def test_failed_resume_does_not_spawn_a_duplicate_watcher(bridge, session, monkeypatch):
    mcp.add_stdio_server("fixture-server", ["fixture-never-executed"], disabled=True)
    task, request = approval_task(bridge, session)
    monkeypatch.setattr(t, "resume_waiting_approval_task", lambda _: False)
    bridge._resolve_queued_mcp_approval(session, task_id=task["task_id"], action="once")
    assert get_user_input_request(request["request_id"])["status"] == "submitted"
    bridge._update_executor.submit.assert_not_called()
    assert any(
        "не удалось вернуть" in str(c.args[1])
        for c in bridge.telegram.send_message.call_args_list
    )


@pytest.fixture
def queued(bridge, monkeypatch):
    progress = Mock()
    stop = Mock()
    outcome = Mock()
    monkeypatch.setattr(t, "TelegramToolProgress", lambda *args: progress)
    monkeypatch.setattr(bridge, "_start_typing_keepalive", Mock(return_value=stop))
    monkeypatch.setattr(bridge, "_send_task_outcome", outcome)
    return SimpleNamespace(progress=progress, stop=stop, outcome=outcome)


@pytest.mark.parametrize("status", ["ok", "waiting_approval", "fail", None])
@pytest.mark.parametrize("existing_workspace", [False, True])
def test_gateway_queue_result_preserves_overrides_and_owned_cleanup(
    bridge, session, queued, status, existing_workspace
):
    session.workspace_task_id = "previous-task" if existing_workspace else None
    session.provider_override = "fixture-provider"
    session.model_override = "fixture-model"
    task = (
        None
        if status is None
        else {"task_id": "new-task", "status": status, "output_text": "fixture result"}
    )
    bridge.gateway_runtime.handle_message = Mock(
        return_value=SimpleNamespace(task_id="new-task", task=task, status=status)
    )
    attachments = [{"type": "document", "path": "fixture.txt", "name": "fixture.txt"}]
    original = json.dumps(attachments)
    bridge._forward_user_message_via_task_queue(
        session, "fixture request", attachments=attachments
    )
    options = bridge.gateway_runtime.handle_message.call_args.kwargs
    assert (
        options["provider_override"] == "fixture-provider"
        and options["model_override"] == "fixture-model"
    )
    assert (
        options["message"].attachments == attachments
        and json.dumps(attachments) == original
    )
    assert options["workspace_task_id"] == (
        "previous-task" if existing_workspace else None
    )
    assert session.workspace_task_id == (
        "previous-task" if existing_workspace else "new-task"
    )
    assert session.pending_task_id == (
        "new-task" if status == "waiting_approval" else None
    )
    queued.stop.assert_called_once()
    queued.progress.finish.assert_called_once()
    queued.outcome.assert_called_once()
    assert queued.outcome.call_args.args[2]["status"] == (status or "fail")


@pytest.mark.parametrize(
    "error", [OSError("fixture transport"), RuntimeError("fixture gateway")]
)
def test_gateway_failure_stops_typing_and_progress_without_fake_outcome(
    bridge, session, queued, error
):
    session.workspace_task_id = "existing-task"
    bridge.gateway_runtime.handle_message = Mock(side_effect=error)
    with pytest.raises(type(error)):
        bridge._forward_user_message_via_task_queue(
            session, "fixture request", attachments=[]
        )
    queued.stop.assert_called_once()
    queued.progress.finish.assert_called_once()
    queued.outcome.assert_not_called()
    assert session.workspace_task_id == "existing-task"


def test_latest_assistant_workspace_is_restored_from_real_history(
    bridge, session, queued
):
    path = bridge._telegram_session_path(session)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(
            json.dumps(row)
            for row in [
                {
                    "role": "assistant",
                    "text": "old answer",
                    "task_id": "old-task",
                    "status": "ok",
                },
                {"role": "user", "text": "recent request"},
                {
                    "role": "assistant",
                    "text": "recent answer",
                    "task_id": "recent-task",
                    "workspace_task_id": "stable-workspace",
                    "status": "ok",
                },
            ]
        )
        + "\n"
    )
    bridge.gateway_runtime.handle_message = Mock(
        return_value=SimpleNamespace(
            task_id="next-task", task={"status": "ok"}, status="ok"
        )
    )
    bridge._forward_user_message_via_task_queue(
        session, "continue fixture", attachments=[]
    )
    options = bridge.gateway_runtime.handle_message.call_args.kwargs
    assert options["workspace_task_id"] == "stable-workspace"
    assert session.workspace_task_id == "stable-workspace"
    assert any(
        row.get("text") == "recent request" for row in options["history_context"]
    )


@pytest.mark.parametrize("set_commands_fails", [False, True])
def test_polling_recovers_bootstrap_and_transport_without_losing_offset(
    bridge, monkeypatch, capsys, set_commands_fails
):
    bridge.telegram.get_me.side_effect = [
        t.TelegramApiUnavailableError("fixture offline"),
        {"id": 7, "username": "fixture"},
    ]
    if set_commands_fails:
        bridge.telegram.set_my_commands.side_effect = RuntimeError(
            "fixture menu unavailable"
        )
    bridge.telegram.get_updates.side_effect = [
        OSError("fixture poll interrupted"),
        [{"update_id": 7}, None, {"update_id": "not-an-int"}, {}],
        KeyboardInterrupt(),
    ]
    bridge._handle_update = Mock()
    monkeypatch.setattr(t.time, "sleep", Mock())
    with pytest.raises(KeyboardInterrupt):
        bridge.run_forever()
    assert bridge.offset == 8
    assert bridge._handle_update.call_count == 3
    assert bridge.telegram.get_updates.call_args.kwargs["offset"] == 8
    assert "polling recovered" in capsys.readouterr().out


def test_typing_keepalive_recovers_send_failure_and_stops_owned_thread(bridge):
    ready = threading.Event()
    count = 0

    def send(*args):
        nonlocal count
        count += 1
        if count == 1:
            raise OSError("fixture typing failure")
        ready.set()

    bridge.telegram.send_chat_action.side_effect = send
    before = set(threading.enumerate())
    stop = bridge._start_typing_keepalive("chat-1", interval_s=0.002)
    try:
        assert ready.wait(1)
        owned = [
            item
            for item in threading.enumerate()
            if item not in before and item.name == "telegram-typing"
        ]
        assert len(owned) == 1
    finally:
        stop()
    owned[0].join(timeout=1)
    assert not owned[0].is_alive() and count >= 2


def test_failed_enable_can_be_retried_on_the_original_pending_request(bridge, session):
    task, request = approval_task(bridge, session)
    with pytest.raises(KeyError):
        bridge._resolve_queued_mcp_approval(
            session, task_id=task["task_id"], action="once"
        )
    assert get_user_input_request(request["request_id"])["status"] == "pending"
    mcp.add_stdio_server("fixture-server", ["fixture-never-executed"], disabled=True)
    bridge._resolve_queued_mcp_approval(session, task_id=task["task_id"], action="once")
    assert get_user_input_request(request["request_id"])["status"] == "submitted"
    assert get_task(task["task_id"])["status"] == "queued"
    assert get_task(task["task_id"])["input_text"] == "Original fixture instruction"
    bridge._update_executor.submit.assert_called_once()
