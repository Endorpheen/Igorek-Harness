import pytest

from factory.core.mobile_bridge.service import MobileConflict, MobileInvalid
from test_scheduled_delivery import setup as setup


def test_plan_mode_never_steers_an_already_running_task(setup, monkeypatch):
    service, _, _, session, _ = setup
    monkeypatch.setattr(service, "_profile_backend", lambda _: "cc-backend")
    service.send_message(session, "Make changes")
    with pytest.raises(MobileConflict, match="current task"):
        service.send_message(session, "Only make a plan", plan_mode=True)
    assert len(service.get_session(session)["messages"]) == 1


def test_ordinary_message_does_not_steer_a_still_running_plan(setup, monkeypatch):
    service, _, _, session, _ = setup
    monkeypatch.setattr(service, "_profile_backend", lambda _: "cc-backend")
    service.send_message(session, "Inspect before changes", plan_mode=True)
    with pytest.raises(MobileConflict, match="current task"):
        service.send_message(session, "Implement the plan", plan_mode=False)
    assert len(service.get_session(session)["messages"]) == 1


@pytest.mark.parametrize("backend", ["codex", "claude", ""])
def test_plan_mode_rejects_backends_without_enforced_read_only_tools(setup, monkeypatch, backend):
    service, _, _, session, _ = setup
    monkeypatch.setattr(service, "_profile_backend", lambda _: backend)
    with pytest.raises(MobileInvalid, match="not supported"):
        service.send_message(session, "Plan without writes", plan_mode=True)
    assert not service.get_session(session)["messages"]
