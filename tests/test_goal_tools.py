import json

import pytest

from factory.core import goal_tools, thread_goals


@pytest.fixture(autouse=True)
def isolated_data(tmp_path, monkeypatch):
    monkeypatch.setenv("IGOREK_FACTORY_HOME", str(tmp_path))
    monkeypatch.setenv("FACTORY_DATA_DIR", str(tmp_path))


def test_model_reads_and_completes_only_its_bound_goal():
    goal = thread_goals.create_goal("chat", "Implement the change")
    context = {"current_session": "chat", "delegate_metadata": {"goal_id": goal["goal_id"], "goal_revision": goal["revision"]}}
    result = json.loads(goal_tools.get_goal({}, **context))
    assert result["goal"]["objective"] == "Implement the change"
    result = json.loads(goal_tools.update_goal({"status": "complete"}, **context))
    assert result["goal"]["status"] == "complete"
    assert thread_goals.get_goal("chat")["status"] == "complete"


def test_model_cannot_change_revised_goal_or_choose_another_session():
    first = thread_goals.create_goal("first", "Original")
    second = thread_goals.create_goal("second", "Private goal")
    context = {"current_session": "first", "delegate_metadata": {"goal_id": first["goal_id"], "goal_revision": first["revision"]}}
    thread_goals.edit_goal("first", "Revised", expected_goal_id=first["goal_id"])
    assert "error" in json.loads(goal_tools.update_goal({"status": "complete"}, **context))
    forged = {"current_session": "second", "delegate_metadata": context["delegate_metadata"]}
    assert "error" in json.loads(goal_tools.get_goal({}, **forged))
    assert thread_goals.get_goal("first")["status"] == "active"
    assert thread_goals.get_goal("second")["goal_id"] == second["goal_id"]


@pytest.mark.parametrize("status", ["active", "budget_limited", "usage_limited", "invalid", [], {}])
def test_model_cannot_resume_or_set_runtime_controlled_status(status):
    goal = thread_goals.create_goal("chat", "Work")
    context = {"current_session": "chat", "delegate_metadata": {"goal_id": goal["goal_id"], "goal_revision": goal["revision"]}}
    assert "error" in json.loads(goal_tools.update_goal({"status": status}, **context))
    assert thread_goals.get_goal("chat")["status"] == "active"


def test_unbound_tool_calls_fail_without_creating_session_data():
    assert "error" in json.loads(goal_tools.get_goal({}, current_session="chat", delegate_metadata={}))
    assert "error" in json.loads(goal_tools.update_goal({"status": "complete"}, delegate_metadata={}))
    assert thread_goals.get_goal("chat") is None


def test_get_goal_rejects_arguments_and_reports_remaining_budget():
    goal = thread_goals.create_goal("chat", "Work", token_budget=10)
    context = {"current_session": "chat", "delegate_metadata": {"goal_id": goal["goal_id"], "goal_revision": goal["revision"]}}
    thread_goals.account_usage("chat", goal["goal_id"], "turn", tokens=3, seconds=1)
    assert json.loads(goal_tools.get_goal({}, **context))["remaining_tokens"] == 7
    assert "error" in json.loads(goal_tools.get_goal({"session": "other"}, **context))


def test_registered_goal_tools_are_hidden_from_ordinary_tasks_and_dispatch_when_bound(tmp_path):
    from factory.core.cc_backend_tools import execute_tool, get_tool_schemas

    assert {"get_goal", "update_goal"}.isdisjoint({item["name"] for item in get_tool_schemas()})
    goal = thread_goals.create_goal("chat", "Work")
    result = execute_tool("update_goal", {"status": "complete"}, str(tmp_path), current_session="chat",
                          delegate_metadata={"goal_id": goal["goal_id"], "goal_revision": goal["revision"]})
    assert json.loads(result)["goal"]["status"] == "complete"


def test_goal_tools_are_only_exposed_for_a_matching_active_session_task():
    goal = thread_goals.create_goal("chat", "Work")
    metadata = {"source": "mobile_bridge", "session": "chat", "goal_id": goal["goal_id"], "goal_revision": goal["revision"]}
    assert {item["name"] for item in goal_tools.schemas_for_task(metadata)} == {"get_goal", "update_goal"}
    assert goal_tools.schemas_for_task({}) == []
    assert goal_tools.schemas_for_task({**metadata, "session": ""}) == []
    assert goal_tools.schemas_for_task({**metadata, "source": "subagent"}) == []
    assert goal_tools.schemas_for_task({**metadata, "session": "other"}) == []
    assert goal_tools.schemas_for_task({**metadata, "goal_revision": 999}) == []
    thread_goals.update_goal("chat", "paused", by_user=True)
    assert goal_tools.schemas_for_task(metadata) == []


def test_guest_tools_use_host_relay_and_fail_closed_without_it(monkeypatch):
    monkeypatch.setenv("IGOREK_ISOLATION_GUEST", "1")
    calls = []
    relay = lambda args: calls.append(args) or '{"goal":{"status":"complete"}}'
    assert json.loads(goal_tools.get_goal({}, goal_control_provider=relay))["goal"]["status"] == "complete"
    assert json.loads(goal_tools.update_goal({"status": "complete"}, goal_control_provider=relay))["goal"]["status"] == "complete"
    assert calls == [{"operation": "get"}, {"operation": "update", "status": "complete"}]
    assert "error" in json.loads(goal_tools.get_goal({}))
    assert "error" in json.loads(goal_tools.update_goal({"status": "complete"}))
