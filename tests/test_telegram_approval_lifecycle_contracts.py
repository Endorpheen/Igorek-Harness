"""Approval selection and resumed-task completion stay scoped to an allowed chat."""

import json
from types import SimpleNamespace
from unittest.mock import Mock, call

import pytest

from factory.core import telegram_bridge as t


@pytest.fixture
def bridge():
    result = t.TelegramBridge.__new__(t.TelegramBridge)
    result.allowed_user_ids = {"owner"}
    result.allowed_chat_ids = {"chat"}
    result.telegram = Mock()
    result._safe_send = Mock()
    result._get_or_create_state = Mock(
        return_value=t.ChatRuntimeState(
            chat_id="chat", user_id="owner", username="fixture", chat_session="fixture"
        )
    )
    result._resolve_queued_mcp_approval = Mock()
    result._queued_mcp_approvals = Mock(return_value=[])
    result._gateway_app_state = Mock(return_value="initial")
    result._apply_gateway_app_state = Mock()
    result._send_turn_outcome = Mock()
    result._send_task_outcome = Mock()
    result.gateway_runtime = Mock()
    result.gateway_runtime.resolve_app_server_approval.side_effect = [
        "resolved-one",
        "resolved-two",
    ]
    result.gateway_runtime.continue_app_server_turn.return_value = "outcome"
    result.auto_approve = False
    result.auto_approve_scope = "once"
    return result


def callback(**overrides):
    return dict(
        {
            "from": {"id": "owner", "username": "fixture"},
            "message": {"chat": {"id": "chat"}},
            "id": "callback",
            "data": "mcp:task:once",
        },
        **overrides,
    )


@pytest.mark.parametrize(
    "value",
    [
        {},
        {"from": []},
        callback(message=[]),
        callback(message={"chat": []}),
        callback(message={"chat": {}}),
        callback(**{"from": {}}),
        callback(**{"from": {"id": "intruder"}}),
        callback(message={"chat": {"id": "other"}}),
    ],
)
def test_invalid_or_unauthorized_callback_cannot_resolve_approval(bridge, value):
    bridge._handle_callback_query(value)
    bridge._resolve_queued_mcp_approval.assert_not_called()
    bridge.telegram.answer_callback_query.assert_not_called()
    bridge._get_or_create_state.assert_not_called()


@pytest.mark.parametrize("data", ["", "other:task:once", "mcp", "mcp:task"])
def test_non_approval_callback_is_acknowledged_without_resolution(bridge, data):
    bridge._handle_callback_query(callback(data=data))
    bridge.telegram.answer_callback_query.assert_called_once_with("callback")
    bridge._resolve_queued_mcp_approval.assert_not_called()


@pytest.mark.parametrize("action", ["once", "session", "reject"])
def test_allowed_callback_routes_exact_task_and_action(bridge, action):
    bridge._handle_callback_query(callback(data=f"mcp:exact-task:{action}"))
    session = bridge._get_or_create_state.return_value
    bridge._resolve_queued_mcp_approval.assert_called_once_with(
        session, task_id="exact-task", action=action
    )


def test_callback_ack_failure_does_not_discard_allowed_decision(bridge):
    bridge.telegram.answer_callback_query.side_effect = OSError(
        "synthetic transport failure"
    )
    bridge._handle_callback_query(callback())
    bridge._resolve_queued_mcp_approval.assert_called_once()


def test_resolution_failure_returns_feedback_without_retrying_decision(bridge):
    bridge._resolve_queued_mcp_approval.side_effect = ValueError(
        "synthetic decision failure"
    )
    bridge._handle_callback_query(callback())
    bridge._resolve_queued_mcp_approval.assert_called_once()
    assert "approval" in bridge._safe_send.call_args.args[1]


@pytest.mark.parametrize(
    ("argument", "expected"),
    [
        ("", [0, 1]),
        (" all ", [0, 1]),
        ("1", [0]),
        ("2", [1]),
        ("req-two", [1]),
        ("0", []),
        ("3", []),
        ("missing", []),
    ],
)
def test_runtime_approval_selection_is_explicit_and_does_not_mutate(
    bridge, argument, expected
):
    approvals = [
        SimpleNamespace(request_id="req-one"),
        SimpleNamespace(request_id="req-two"),
    ]
    selected = bridge._select_approvals(approvals, argument)
    assert selected == [approvals[index] for index in expected]
    assert len(approvals) == 2


@pytest.mark.parametrize(
    ("command", "approved", "remember"),
    [
        ("/approve", True, False),
        ("/approve_session", True, True),
        ("/reject", False, False),
    ],
)
def test_runtime_decisions_chain_state_and_continue_once(
    bridge, command, approved, remember
):
    session = bridge._get_or_create_state.return_value
    session.runtime_session_id = "runtime"
    session.pending_approvals = [
        SimpleNamespace(request_id="one"),
        SimpleNamespace(request_id="two"),
    ]
    bridge._handle_approval_command(session, command=command, argument="all")
    assert bridge.gateway_runtime.resolve_app_server_approval.call_args_list == [
        call(
            state="initial",
            request_id="one",
            approved=approved,
            remember_for_session=remember,
        ),
        call(
            state="resolved-one",
            request_id="two",
            approved=approved,
            remember_for_session=remember,
        ),
    ]
    bridge.gateway_runtime.continue_app_server_turn.assert_called_once_with(
        state="resolved-two",
        turn_timeout_s=600,
        auto_approve=False,
        auto_approve_scope="once",
    )
    bridge._send_turn_outcome.assert_called_once_with("chat", "outcome")


@pytest.mark.parametrize("case", ["empty", "inactive", "missing-selection"])
def test_unavailable_runtime_approval_never_continues_task(bridge, case):
    session = bridge._get_or_create_state.return_value
    session.pending_approvals = (
        [] if case == "empty" else [SimpleNamespace(request_id="one")]
    )
    session.runtime_session_id = "" if case == "inactive" else "runtime"
    bridge._handle_approval_command(session, command="/approve", argument="missing")
    bridge.gateway_runtime.resolve_app_server_approval.assert_not_called()
    bridge.gateway_runtime.continue_app_server_turn.assert_not_called()
    bridge._safe_send.assert_called_once()


@pytest.mark.parametrize(
    ("command", "action"),
    [("/approve", "once"), ("/approve_session", "session"), ("/reject", "reject")],
)
def test_queued_approval_uses_selected_request_without_runtime_path(
    bridge, command, action
):
    session = bridge._get_or_create_state.return_value
    bridge._queued_mcp_approvals.return_value = [
        dict(request_id="first", task_id="first-task"),
        dict(request_id="second", task_id="second-task"),
    ]
    bridge._handle_approval_command(session, command=command, argument="2")
    bridge._resolve_queued_mcp_approval.assert_called_once_with(
        session, task_id="second-task", action=action
    )
    bridge.gateway_runtime.continue_app_server_turn.assert_not_called()


@pytest.mark.parametrize(
    "status", ["ok", "fail", "dead_letter", "cancelled", "waiting_approval"]
)
def test_resumed_task_stops_on_every_terminal_status_and_finishes_progress(
    bridge, tmp_path, monkeypatch, status
):
    session = bridge._get_or_create_state.return_value
    task = {"status": status, "metadata": {"original_message_text": "fixture request"}}
    progress = Mock()
    monkeypatch.setattr(t, "get_data_root", lambda: tmp_path)
    monkeypatch.setattr(t, "TelegramToolProgress", lambda *args: progress)
    monkeypatch.setattr(t.time, "monotonic", Mock(side_effect=[0, 1]))
    monkeypatch.setattr(t, "get_task", Mock(return_value=task))
    bridge._watch_resumed_task(session, "task-id")
    progress.finish.assert_called_once()
    bridge._send_task_outcome.assert_called_once_with(session, "fixture request", task)


def test_resumed_task_reads_new_tool_events_and_ignores_approval_button_tool(
    bridge, tmp_path, monkeypatch
):
    session = bridge._get_or_create_state.return_value
    trace = tmp_path / "workspace/temp_execution_task-id/cc_backend_trace.jsonl"
    trace.parent.mkdir(parents=True)
    trace.write_text(
        "not json\n"
        + json.dumps(
            {
                "kind": "tool.request_batch",
                "tools": [
                    {"name": "Read", "input": {"file_path": "fixture.txt"}},
                    {"name": "suggest_enable_mcp"},
                ],
            }
        )
        + "\n"
    )
    progress = Mock()
    monkeypatch.setattr(t, "get_data_root", lambda: tmp_path)
    monkeypatch.setattr(t, "TelegramToolProgress", lambda *args: progress)
    monkeypatch.setattr(t.time, "monotonic", Mock(side_effect=[0, 1, 2]))
    monkeypatch.setattr(t.time, "sleep", Mock())
    monkeypatch.setattr(
        t, "get_task", Mock(side_effect=[{"status": "processing"}, {"status": "ok"}])
    )
    bridge._watch_resumed_task(session, "task-id")
    progress.callback.assert_called_once_with(
        "tool.started", tool_name="Read", args={"file_path": "fixture.txt"}
    )
    progress.finish.assert_called_once()
    bridge._send_task_outcome.assert_called_once()
