import json
from unittest.mock import Mock

import pytest

from factory.core import thread_goals
from factory.core.isolation import channel
from factory.core.isolation.history import PayloadError, RehydratedTaskMetadata
from factory.core.isolation.manager import GoalRelay
from test_isolation_guest_runner import _start_harness, _task_payload


REQUEST_ID = "12345678-1234-1234-1234-123456789abc"


@pytest.fixture(autouse=True)
def isolated_data(tmp_path, monkeypatch):
    monkeypatch.setenv("IGOREK_FACTORY_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("FACTORY_DATA_DIR", str(tmp_path / "data"))


@pytest.mark.parametrize("args", [
    {}, {"operation": "get", "session_id": "other"},
    {"operation": "update", "status": "active"},
    {"operation": "update", "status": []}, {"operation": "update", "status": "complete", "goal_id": "other"},
])
def test_invalid_guest_goal_request_never_writes_to_channel(args):
    socket = Mock()
    peer = channel.PeerSession(socket, channel.ChannelConfig())
    with pytest.raises(channel.ChannelError, match="goal_request_invalid"):
        peer.send(channel.MessageKind.GOAL_REQUEST, {"request_id": REQUEST_ID, "args": args})
    socket.sendall.assert_not_called()


def test_host_relay_binds_goal_to_host_session_and_replays_request_once():
    goal = thread_goals.create_goal("chat", "Work")
    other = thread_goals.create_goal("other", "Private")
    relay = GoalRelay("chat", {"goal_id": goal["goal_id"], "goal_revision": goal["revision"]})
    request = channel.ChannelMessage(channel.MessageKind.GOAL_REQUEST, 1,
        {"request_id": REQUEST_ID, "args": {"operation": "update", "status": "complete"}})
    first = relay.handle_request(request)
    assert json.loads(first["result"])["goal"]["status"] == "complete"
    assert relay.handle_request(request) == first
    assert thread_goals.get_goal("other")["goal_id"] == other["goal_id"]
    forged = channel.ChannelMessage(channel.MessageKind.GOAL_REQUEST, 2,
        {"request_id": "22345678-1234-1234-1234-123456789abc", "args": {"operation": "get", "session_id": "other"}})
    assert "error" in json.loads(relay.handle_request(forged)["result"])
    assert thread_goals.get_goal("other")["status"] == "active"


def test_goal_binding_round_trips_and_incomplete_binding_is_rejected():
    metadata = RehydratedTaskMetadata.from_wire_section({"goal_id": "a" * 32, "goal_revision": 2})
    assert metadata.to_dict() == {"goal_id": "a" * 32, "goal_revision": 2}
    for value in ({"goal_id": "a" * 32}, {"goal_revision": 1},
                  {"goal_id": "bad", "goal_revision": 1}, {"goal_id": "a" * 32, "goal_revision": True}):
        with pytest.raises(PayloadError):
            RehydratedTaskMetadata.from_wire_section(value)


def test_goal_rpc_round_trips_through_guest_runner(tmp_path):
    observed = []

    def executor(**kwargs):
        observed.append(kwargs["goal_control_provider"]({"operation": "update", "status": "complete"}))
        return {"status": "ok", "output_text": observed[-1], "usage": {}, "tool_calls_count": 1}

    harness = _start_harness(executor, poll_interval_s=0.01)
    try:
        harness.host.send(channel.MessageKind.TASK, _task_payload(tmp_path))
        request = harness.host.recv_from_guest(timeout_s=10.0)
        assert request is not None and request.kind is channel.MessageKind.GOAL_REQUEST
        assert request.payload["args"] == {"operation": "update", "status": "complete"}
        harness.host.send(channel.MessageKind.GOAL_REPLY, {"request_id": request.payload["request_id"], "result": '{"ok":true}'})
        messages = harness.collect()
    finally:
        harness.finish()
    assert observed == ['{"ok":true}']
    assert messages[-1].kind is channel.MessageKind.RESULT
