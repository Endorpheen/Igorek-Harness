import concurrent.futures
import sqlite3
from unittest.mock import patch

import pytest

from factory.core import goal_tools
from factory.core.cli_tasks import get_task
from factory.core.mobile_bridge.service import MobileService
from factory.core.runtime import get_db_path
from test_scheduled_delivery import finish, setup as setup


def _goal_tasks(session):
    with sqlite3.connect(get_db_path()) as conn:
        return [row[0] for row in conn.execute(
            "SELECT task_id FROM tasks WHERE json_extract(metadata, '$.session') = ? "
            "AND json_extract(metadata, '$.goal_id') IS NOT NULL ORDER BY rowid", (session,))]


def test_goal_continues_after_turn_and_stops_when_model_marks_complete(setup):
    service, _, _, session, _ = setup
    goal = service.goal_action(session, "create", objective="Produce verified result")["goal"]
    assert service.advance_goals() == 1
    task_id, = _goal_tasks(session)
    task = get_task(task_id)
    assert task["metadata"]["goal_id"] == goal["goal_id"]
    assert "Produce verified result" in task["input_text"]
    assert service.advance_goals() == 0
    finish(task_id, text="First concrete step")
    assert service.advance_goals() == 0
    service.finalize_turns()
    assert service.advance_goals() == 1
    next_id = _goal_tasks(session)[-1]
    next_task = get_task(next_id)
    import json
    result = json.loads(goal_tools.update_goal({"status": "complete"}, current_session=session,
                                               delegate_metadata=next_task["metadata"]))
    assert result["goal"]["status"] == "complete"
    finish(next_id, text="Verified and complete")
    service.finalize_turns()
    assert service.advance_goals() == 0
    assert len(_goal_tasks(session)) == 2


def test_user_work_takes_precedence_over_goal_continuation(setup):
    service, _, _, session, _ = setup
    task_id = service.send_message(session, "User request")["task_id"]
    service.goal_action(session, "create", objective="Long task")
    assert service.advance_goals() == 0
    assert _goal_tasks(session) == []
    finish(task_id, text="User request finished")
    service.finalize_turns()
    assert service.advance_goals() == 1


def test_two_bridge_instances_cannot_enqueue_duplicate_goal_turns(setup):
    service, _, _, session, root = setup
    other = MobileService(profile="demo", workdir=str(root), available_profiles=lambda: ["demo"])
    service.goal_action(session, "create", objective="Long task")
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        result = list(pool.map(lambda instance: instance.advance_goals(), [service, other]))
    assert sorted(result) == [0, 1]
    assert len(_goal_tasks(session)) == 1


def test_failed_goal_turn_blocks_instead_of_restarting_forever(setup):
    service, _, _, session, _ = setup
    service.goal_action(session, "create", objective="Long task")
    assert service.advance_goals() == 1
    task_id, = _goal_tasks(session)
    finish(task_id, status="fail", text="provider unavailable")
    service.finalize_turns()
    assert service.advance_goals() == 0
    assert service.get_session(session)["goal"]["status"] == "blocked"
    assert len(_goal_tasks(session)) == 1


def test_retry_after_event_failure_recovers_without_creating_a_second_task(setup):
    service, _, _, session, _ = setup
    service.goal_action(session, "create", objective="Long task")
    with patch.object(service.store, "append_event", side_effect=OSError("fixture event failure")):
        with pytest.raises(OSError, match="fixture event failure"):
            service.advance_goals()
    task_id, = _goal_tasks(session)
    assert service.advance_goals() == 0
    assert _goal_tasks(session) == [task_id]
    events = service.store.list_events(session_id=session, kinds=("task.submitted",))
    assert len(events) == 1 and events[0]["task_id"] == task_id


def test_goal_budget_counts_terminal_turn_once_and_prevents_another_task(setup):
    service, _, _, session, _ = setup
    service.goal_action(session, "create", objective="Long task", token_budget=10)
    assert service.advance_goals() == 1
    task_id, = _goal_tasks(session)
    finish(task_id, text="Partial work")
    with sqlite3.connect(get_db_path()) as conn:
        conn.execute("UPDATE tasks SET usage_prompt = 6, usage_completion = 4, started_at = 100, completed_at = 105 WHERE task_id = ?", (task_id,))
    service.finalize_turns()
    service.finalize_turns()
    goal = service.get_session(session)["goal"]
    assert goal["tokens_used"] == 10 and goal["time_used_seconds"] == 5
    assert goal["status"] == "budget_limited"
    assert service.advance_goals() == 0 and len(_goal_tasks(session)) == 1


def test_three_empty_turns_block_automatic_continuation(setup):
    service, _, _, session, _ = setup
    service.goal_action(session, "create", objective="Long task")
    for _ in range(3):
        assert service.advance_goals() == 1
        task_id = _goal_tasks(session)[-1]
        finish(task_id, text="")
        service.finalize_turns()
    assert service.advance_goals() == 0
    assert service.get_session(session)["goal"]["status"] == "blocked"
    assert len(_goal_tasks(session)) == 3


def test_user_followup_binds_to_latest_goal_revision_when_requeued(setup):
    service, _, _, session, _ = setup
    goal = service.goal_action(session, "create", objective="Original")["goal"]
    first = service.send_message(session, "User request")
    task = get_task(first["task_id"])
    assert task["metadata"]["goal_id"] == goal["goal_id"]
    service.send_message(session, "User follow-up")
    service.goal_action(session, "edit", objective="Revised", goal_id=goal["goal_id"])
    finish(first["task_id"], text="First answer")
    service.finalize_turns()
    assert service.resume_pending_steers() == 1
    task = get_task(first["task_id"])
    assert task["metadata"]["goal_id"] == goal["goal_id"]
    assert task["metadata"]["goal_revision"] == 2
    assert "User follow-up" in task["input_text"]
