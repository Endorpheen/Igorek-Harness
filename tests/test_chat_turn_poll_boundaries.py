"""Task polling/cancellation uses owned history, a real thread and fake worker/UI."""

import itertools
import json
import time
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from factory.core import chat_session as c, cli_chat_active as active
from test_chat_session_lifecycle_contracts import session as session


@pytest.fixture
def turn(session, monkeypatch, tmp_path):
    trace = tmp_path / "trace.jsonl"
    trace.write_text("")
    monkeypatch.setattr(c, "_trace_path_for_task", lambda _: str(trace))
    monkeypatch.setattr(
        active, "_create_chat_application", lambda **_: (None, None, None)
    )
    ticks = itertools.count(step=2)
    monkeypatch.setattr(
        c,
        "time",
        SimpleNamespace(
            monotonic=lambda: next(ticks), sleep=lambda _: time.sleep(0.001)
        ),
    )
    session.submit_task = Mock(return_value={"task_id": "owned-task"})
    session.before_turn = Mock()
    session.sl.refresh = Mock()
    monkeypatch.setattr(session, "_pending_user_input_request", lambda _: None)
    cancelled = Mock()
    monkeypatch.setattr(c, "cancel_task", cancelled)

    def task(_):
        time.sleep(0.015)
        return {"task_id": "owned-task", "status": "cancelled", "output_text": ""}

    monkeypatch.setattr(c, "get_task", task)
    return SimpleNamespace(session=session, trace=trace, cancelled=cancelled)


def test_cancelled_task_clears_pending_ui_interrupt_without_new_cancel(turn):
    s = turn.session
    s._fs_running_state = {"interrupting": True, "task_finished": False}
    result = s._execute_one_turn("fixture", {}, ["owned.png"])
    assert result["status"] == "cancelled"
    assert (
        s._fs_running_state["task_finished"] and not s._fs_running_state["interrupting"]
    )
    assert s._fs_running_state["current_task_id"] == "owned-task"
    assert s.submit_task.call_args.kwargs["input_files"] == ["owned.png"]
    turn.cancelled.assert_not_called()


@pytest.mark.parametrize("cancel_error", [False, True])
def test_escape_before_task_creation_is_delivered_to_that_task_only(turn, cancel_error):
    s = turn.session
    s._fs_running_state = {"interrupt_pending": True}
    if cancel_error:
        turn.cancelled.side_effect = RuntimeError("fixture cancellation race")
    result = s._execute_one_turn("fixture", {})
    turn.cancelled.assert_called_once_with("owned-task")
    assert (
        "interrupt_pending" not in s._fs_running_state
        and result["status"] == "cancelled"
    )


def test_profile_preparation_failure_never_submits_task(turn):
    turn.session.before_turn.side_effect = RuntimeError("fixture profile not ready")
    with pytest.raises(RuntimeError, match="profile not ready"):
        turn.session._execute_one_turn("fixture", {})
    turn.session.submit_task.assert_not_called()
    turn.cancelled.assert_not_called()


def test_worker_poll_error_is_propagated_after_terminal_finalization(turn, monkeypatch):
    monkeypatch.setattr(
        c, "get_task", Mock(side_effect=OSError("fixture database unavailable"))
    )
    with pytest.raises(OSError, match="database unavailable"):
        turn.session._execute_one_turn("fixture", {})
    turn.cancelled.assert_not_called()


@pytest.mark.parametrize("invalidate_fails", [False, True])
def test_persistent_ui_drains_trailing_trace_without_second_application(
    turn, monkeypatch, invalidate_fails
):
    s = turn.session
    ui = Mock()
    if invalidate_fails:
        ui.invalidate.side_effect = RuntimeError("fixture repaint unavailable")
    s._fs_external_app = ui
    application = Mock(side_effect=AssertionError("no second application"))
    monkeypatch.setattr(active, "_create_chat_application", application)
    events = [
        {"kind": "model.text_delta", "round": 1, "text": "OWNED_FINAL_DELTA"},
        {"kind": "task.finish", "status": "ok", "output_preview": "OWNED_FINAL_DELTA"},
    ]
    turn.trace.write_text("".join(json.dumps(row) + "\n" for row in events))

    def completed(_):
        time.sleep(0.015)
        return {
            "task_id": "owned-task",
            "status": "ok",
            "output_text": "OWNED_FINAL_DELTA",
        }

    monkeypatch.setattr(c, "get_task", completed)
    result = s._execute_one_turn("fixture", {})
    assert result["status"] == "ok"
    assert "OWNED_FINAL_DELTA" in "\n".join(s._scrollback_widget_lines())
    application.assert_not_called()
    assert ui.invalidate.called


def test_fullscreen_exit_cancels_only_current_owned_task(turn):
    s = turn.session
    s._fs_running_state = {"exit_requested": True}
    result = s._execute_one_turn("fixture", {})
    turn.cancelled.assert_called_once_with("owned-task")
    assert s._active is False
    assert result is None or result["status"] == "cancelled"
