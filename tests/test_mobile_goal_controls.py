import pytest

from factory.core import thread_goals
from factory.core.mobile_bridge.service import MobileNotFound
from test_scheduled_delivery import setup as setup


def test_goal_controls_are_session_scoped_and_visible_in_session_detail(setup):
    service, _, _, session, _ = setup
    other = service.create_session(profile="demo", cwd=service.workdir)["session_id"]
    created = service.goal_action(session, "create", objective="Finish the project")
    assert created["goal"]["status"] == "active"
    assert service.get_session(session)["goal"]["objective"] == "Finish the project"
    assert service.get_session(other)["goal"] is None
    identity = created["goal"]["goal_id"]
    edited = service.goal_action(session, "edit", objective="Finish and verify", goal_id=identity)
    assert edited["goal"]["revision"] == 2
    assert service.goal_action(session, "pause", goal_id=identity)["goal"]["status"] == "paused"
    assert service.goal_action(session, "resume", goal_id=identity)["goal"]["status"] == "active"
    assert service.goal_action(session, "clear", goal_id=identity)["goal"] is None
    events = service.store.list_events(session_id=session, kinds=("goal.updated",))
    assert len(events) == 5
    assert service.get_session(other)["goal"] is None


def test_goal_controls_reject_missing_session_and_stale_identity(setup):
    service, _, _, session, _ = setup
    with pytest.raises(MobileNotFound):
        service.goal_action("missing", "create", objective="Work")
    created = service.goal_action(session, "create", objective="Work")["goal"]
    with pytest.raises(thread_goals.GoalConflict):
        service.goal_action(session, "edit", objective="Other", goal_id="stale")
    with pytest.raises(thread_goals.GoalConflict):
        service.goal_action(session, "clear", goal_id="stale")
    assert service.get_session(session)["goal"]["goal_id"] == created["goal_id"]


def test_unknown_goal_action_does_not_change_goal(setup):
    service, _, _, session, _ = setup
    created = service.goal_action(session, "create", objective="Work")["goal"]
    with pytest.raises(ValueError):
        service.goal_action(session, "invented")
    assert service.get_session(session)["goal"]["goal_id"] == created["goal_id"]


def test_creating_goal_can_activate_its_profile_before_persisting(setup):
    service, _, _, session, _ = setup
    calls = []
    service.profile_control = lambda action, profile: calls.append((action, profile)) or {}
    service.goal_action(session, "create", objective="Work", activate_profile=True)
    assert calls == [("activate", "demo")]
