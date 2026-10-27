import concurrent.futures
import sqlite3
from unittest.mock import patch

import pytest

from factory.core import thread_goals


@pytest.fixture(autouse=True)
def isolated_data(tmp_path, monkeypatch):
    monkeypatch.setenv("IGOREK_FACTORY_HOME", str(tmp_path))
    monkeypatch.setenv("FACTORY_DATA_DIR", str(tmp_path))


def test_goal_persists_with_independent_sessions_and_optional_budget():
    first = thread_goals.create_goal("chat-a", "Finish the migration", token_budget=100)
    other = thread_goals.create_goal("chat-b", "Review the changes")
    assert first["status"] == other["status"] == "active"
    assert first["goal_id"] != other["goal_id"]
    assert thread_goals.get_goal("chat-a")["objective"] == "Finish the migration"
    assert thread_goals.get_goal("chat-a")["token_budget"] == 100
    assert thread_goals.get_goal("chat-b")["token_budget"] is None
    assert thread_goals.get_goal("missing") is None


@pytest.mark.parametrize("session,objective,budget", [
    ("", "valid goal", None),
    ("session", "  ", None),
    ("session", "valid goal", 0),
    ("session", "valid goal", -1),
    ("session", "valid goal", True),
])
def test_invalid_goal_is_rejected_before_persistence(session, objective, budget):
    with pytest.raises(ValueError):
        thread_goals.create_goal(session, objective, token_budget=budget)
    assert thread_goals.get_goal("session") is None


def test_unfinished_goal_cannot_be_replaced_but_completed_goal_can():
    first = thread_goals.create_goal("chat", "First")
    with pytest.raises(thread_goals.GoalConflict):
        thread_goals.create_goal("chat", "Second")
    thread_goals.update_goal("chat", "complete", expected_goal_id=first["goal_id"])
    second = thread_goals.create_goal("chat", "Second")
    assert second["goal_id"] != first["goal_id"]
    assert second["status"] == "active"
    assert second["tokens_used"] == 0


def test_stale_goal_update_cannot_change_replacement():
    first = thread_goals.create_goal("chat", "First")
    thread_goals.update_goal("chat", "complete", expected_goal_id=first["goal_id"])
    second = thread_goals.create_goal("chat", "Second")
    with pytest.raises(thread_goals.GoalConflict):
        thread_goals.update_goal("chat", "blocked", expected_goal_id=first["goal_id"])
    assert thread_goals.get_goal("chat")["goal_id"] == second["goal_id"]
    assert thread_goals.get_goal("chat")["status"] == "active"


@pytest.mark.parametrize("status", ["paused", "blocked", "usage_limited"])
def test_stopped_goal_can_be_resumed_only_by_explicit_user_action(status):
    goal = thread_goals.create_goal("chat", "Work")
    thread_goals.update_goal("chat", status, expected_goal_id=goal["goal_id"], by_user=True)
    with pytest.raises(ValueError):
        thread_goals.update_goal("chat", "active", expected_goal_id=goal["goal_id"])
    assert thread_goals.update_goal("chat", "active", by_user=True)["status"] == "active"


def test_usage_accounting_is_idempotent_and_stops_at_token_budget():
    goal = thread_goals.create_goal("chat", "Work", token_budget=10)
    first = thread_goals.account_usage("chat", goal["goal_id"], "turn-1", tokens=6, seconds=3)
    repeated = thread_goals.account_usage("chat", goal["goal_id"], "turn-1", tokens=6, seconds=3)
    assert first["tokens_used"] == repeated["tokens_used"] == 6
    assert repeated["time_used_seconds"] == 3
    stopped = thread_goals.account_usage("chat", goal["goal_id"], "turn-2", tokens=4, seconds=2)
    assert stopped["status"] == "budget_limited"
    assert stopped["tokens_used"] == 10
    assert stopped["time_used_seconds"] == 5


def test_concurrent_creations_admit_exactly_one_goal():
    def create(index):
        try:
            return thread_goals.create_goal("chat", f"Goal {index}")
        except thread_goals.GoalConflict:
            return None

    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(create, range(4)))
    assert len([item for item in results if item is not None]) == 1
    assert thread_goals.get_goal("chat")["objective"].startswith("Goal ")


def test_clear_removes_only_selected_session():
    thread_goals.create_goal("one", "First")
    thread_goals.create_goal("two", "Second")
    assert thread_goals.clear_goal("one") is True
    assert thread_goals.clear_goal("one") is False
    assert thread_goals.get_goal("one") is None
    assert thread_goals.get_goal("two")["objective"] == "Second"


def test_missing_and_invalid_status_are_rejected():
    assert thread_goals.get_goal("") is None
    with pytest.raises(ValueError, match="unknown"):
        thread_goals.update_goal("chat", "imaginary")
    with pytest.raises(ValueError, match="no goal"):
        thread_goals.update_goal("chat", "complete")
    thread_goals.create_goal("chat", "Work")
    with pytest.raises(thread_goals.GoalConflict, match="cannot be resumed"):
        thread_goals.update_goal("chat", "active", by_user=True)
    thread_goals.update_goal("chat", "complete")
    with pytest.raises(thread_goals.GoalConflict, match="not active"):
        thread_goals.update_goal("chat", "blocked")


@pytest.mark.parametrize("turn,tokens,seconds", [("", 1, 1), ("turn", -1, 1), ("turn", 1, -1), ("turn", True, 1)])
def test_invalid_usage_is_rejected(turn, tokens, seconds):
    with pytest.raises(ValueError):
        thread_goals.account_usage("chat", "goal", turn, tokens=tokens, seconds=seconds)


def test_stale_usage_is_rejected_and_a_finished_turn_is_accounted_after_pause():
    first = thread_goals.create_goal("chat", "First")
    with pytest.raises(thread_goals.GoalConflict):
        thread_goals.account_usage("chat", "stale", "turn", tokens=1, seconds=1)
    thread_goals.update_goal("chat", "paused")
    result = thread_goals.account_usage("chat", first["goal_id"], "turn", tokens=9, seconds=8)
    assert result["status"] == "paused" and result["tokens_used"] == 9
    assert result["time_used_seconds"] == 8


def test_initialization_retries_transient_database_lock_without_hiding_other_errors():
    original = thread_goals.initialize_database
    with patch.object(thread_goals, "initialize_database", side_effect=[sqlite3.OperationalError("database is locked"), original(thread_goals.get_db_path())]):
        with patch.object(thread_goals.time, "sleep") as sleep:
            assert thread_goals.create_goal("chat", "Work")["status"] == "active"
            sleep.assert_called_once()
    with patch.object(thread_goals, "initialize_database", side_effect=sqlite3.OperationalError("disk error")):
        with pytest.raises(sqlite3.OperationalError, match="disk error"):
            thread_goals.get_goal("chat")


def test_persistent_database_lock_is_reported_after_bounded_retries():
    with patch.object(thread_goals, "initialize_database", side_effect=sqlite3.OperationalError("database busy")) as initialize:
        with patch.object(thread_goals.time, "sleep"):
            with pytest.raises(sqlite3.OperationalError, match="busy"):
                thread_goals.get_goal("chat")
    assert initialize.call_count == 20


def test_edit_preserves_usage_and_invalidates_stale_completion():
    goal = thread_goals.create_goal("chat", "Original objective")
    thread_goals.account_usage("chat", goal["goal_id"], "turn", tokens=7, seconds=2)
    updated = thread_goals.edit_goal("chat", "Revised objective", expected_goal_id=goal["goal_id"])
    assert updated["goal_id"] == goal["goal_id"]
    assert updated["revision"] == goal["revision"] + 1
    assert updated["tokens_used"] == 7 and updated["created_at"] == goal["created_at"]
    with pytest.raises(thread_goals.GoalConflict):
        thread_goals.update_goal("chat", "complete", expected_goal_id=goal["goal_id"], expected_revision=goal["revision"])
    assert thread_goals.get_goal("chat")["status"] == "active"


@pytest.mark.parametrize("objective", ["", "   ", "x" * 4001])
def test_edit_rejects_empty_or_excessive_objective_without_changing_state(objective):
    goal = thread_goals.create_goal("chat", "Original")
    with pytest.raises(ValueError):
        thread_goals.edit_goal("chat", objective, expected_goal_id=goal["goal_id"])
    assert thread_goals.get_goal("chat")["objective"] == "Original"


def test_edit_missing_or_replaced_goal_is_rejected():
    with pytest.raises(ValueError):
        thread_goals.edit_goal("missing", "New")
    thread_goals.create_goal("chat", "Original")
    with pytest.raises(thread_goals.GoalConflict):
        thread_goals.edit_goal("chat", "New", expected_goal_id="stale")


def test_edit_completed_goal_reactivates_it():
    goal = thread_goals.create_goal("chat", "Original")
    thread_goals.update_goal("chat", "complete", expected_goal_id=goal["goal_id"])
    updated = thread_goals.edit_goal("chat", "Continue", expected_goal_id=goal["goal_id"])
    assert updated["status"] == "active" and updated["revision"] == 2


def test_pause_and_resume_invalidate_a_running_turn():
    goal = thread_goals.create_goal("chat", "Work")
    thread_goals.update_goal("chat", "paused", by_user=True)
    resumed = thread_goals.update_goal("chat", "active", by_user=True)
    assert resumed["revision"] == 3
    with pytest.raises(thread_goals.GoalConflict):
        thread_goals.update_goal("chat", "complete", expected_goal_id=goal["goal_id"], expected_revision=1)


def test_task_creation_can_join_caller_transaction_and_roll_back():
    from factory.core.cli_tasks import create_task, get_task
    from factory.core.runtime import get_db_path
    from factory.core.schema import initialize_database, open_db

    initialize_database(get_db_path())
    with open_db(get_db_path()) as connection:
        connection.execute("BEGIN IMMEDIATE")
        task = create_task("work", profile="demo", _connection=connection)
        connection.rollback()
    assert get_task(task["task_id"]) is None


def test_active_goals_are_filtered_by_owning_surface():
    thread_goals.create_goal("mobile", "First", surface="mobile_bridge")
    thread_goals.create_goal("terminal", "Second", surface="cli_chat_rich")
    thread_goals.create_goal("paused", "Third", surface="mobile_bridge")
    thread_goals.update_goal("paused", "paused", by_user=True)
    assert [goal["session_id"] for goal in thread_goals.list_active_goals("mobile_bridge")] == ["mobile"]
    assert [goal["session_id"] for goal in thread_goals.list_active_goals("cli_chat_rich")] == ["terminal"]
    with pytest.raises(ValueError):
        thread_goals.create_goal("invalid", "Work", surface="unknown")


def test_repeated_empty_turns_block_but_a_real_response_resets_counter():
    goal = thread_goals.create_goal("chat", "Work")
    for turn in ("one", "two"):
        result = thread_goals.account_usage("chat", goal["goal_id"], turn, tokens=0, seconds=1, empty=True, revision=1)
    assert result["empty_turns"] == 2 and result["status"] == "active"
    result = thread_goals.account_usage("chat", goal["goal_id"], "three", tokens=1, seconds=1, revision=1)
    assert result["empty_turns"] == 0
    for turn in ("four", "five", "six"):
        result = thread_goals.account_usage("chat", goal["goal_id"], turn, tokens=0, seconds=1, empty=True, revision=1)
    assert result["empty_turns"] == 3 and result["status"] == "blocked"


def test_stale_or_failed_turn_cannot_silently_continue_the_wrong_goal():
    goal = thread_goals.create_goal("chat", "Original")
    changed = thread_goals.edit_goal("chat", "Revised")
    stale = thread_goals.account_usage("chat", goal["goal_id"], "old", tokens=2, seconds=1, failed=True, revision=1)
    assert stale["status"] == "active" and stale["tokens_used"] == 2
    current = thread_goals.account_usage("chat", goal["goal_id"], "new", tokens=1, seconds=1, failed=True, revision=changed["revision"])
    assert current["status"] == "blocked" and current["tokens_used"] == 3
