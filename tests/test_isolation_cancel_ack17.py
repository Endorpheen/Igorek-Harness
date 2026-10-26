"""Cancellation waits accept only a valid guest acknowledgement."""
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from factory.core.isolation import manager as isolation
from factory.core.isolation.channel import ChannelError, ChannelMessage, MessageKind


def frame(kind, payload=None, seq=1):
    return ChannelMessage(kind=kind, seq=seq, payload=payload or {})


def manager():
    obj = object.__new__(isolation.IsolationManager)
    obj._cancel_ack_timeout_s = 5.0
    return obj


def canceled(**extra):
    return frame(MessageKind.RESULT, {'status': 'canceled', 'output_text': '', 'usage': {}, **extra})


def endpoint(*messages):
    return SimpleNamespace(recv_from_guest=Mock(side_effect=list(messages)))


def test_cancel_ack_returns_normalized_failure_with_preserved_output():
    remote = endpoint(canceled(output_text='partial', usage={'prompt': 4, 'completion': 3}))
    result = manager()._await_cancel_result(remote, None)
    assert result['status'] == 'fail'
    assert result['failure_reason'] == 'canceled'
    assert result['output_text'] == 'partial'
    assert result['usage']['prompt'] == 4
    assert result['usage']['completion'] == 3


def test_events_are_relayed_before_the_ack_but_approvals_are_not_executed():
    event = frame(MessageKind.EVENT, {'event': {'kind': 'tool.output'}}, seq=2)
    pending = frame(MessageKind.APPROVAL_REQUEST, {'request': 'fixture'}, seq=3)
    remote = endpoint(event, pending, canceled())
    relay = SimpleNamespace(handle_event=Mock())
    result = manager()._await_cancel_result(remote, relay)
    assert result['failure_reason'] == 'canceled'
    relay.handle_event.assert_called_once_with(event)
    assert remote.recv_from_guest.call_count == 3


def test_events_are_drained_even_without_a_relay():
    remote = endpoint(frame(MessageKind.EVENT), canceled())
    assert manager()._await_cancel_result(remote, None)['status'] == 'fail'
    assert remote.recv_from_guest.call_count == 2


@pytest.mark.parametrize('payload', [
    {'status':'canceled', 'output_text':None, 'usage':{}},
    {'status':'canceled', 'output_text':'', 'usage':None},
    {'status':'canceled', 'output_text':'', 'usage':[]},
])
def test_malformed_cancel_result_does_not_count_as_ack(payload):
    remote = endpoint(frame(MessageKind.RESULT, payload))
    assert manager()._await_cancel_result(remote, None) is None


@pytest.mark.parametrize('status', ['ok', 'fail', 'cancelled', '', None])
def test_other_result_statuses_do_not_count_as_cancel_ack(status):
    remote = endpoint(frame(MessageKind.RESULT, {'status':status,'output_text':'','usage':{}}))
    assert manager()._await_cancel_result(remote, None) is None


def test_disconnected_guest_does_not_fabricate_an_ack():
    remote = endpoint(None)
    assert manager()._await_cancel_result(remote, None) is None


def test_channel_failure_does_not_escape_cancel_cleanup():
    remote = endpoint(ChannelError('fixture_disconnect'))
    assert manager()._await_cancel_result(remote, None) is None


def test_expired_ack_deadline_does_not_read_again(monkeypatch):
    clock = iter([10.0, 16.0])
    monkeypatch.setattr(isolation, 'time', SimpleNamespace(monotonic=lambda: next(clock)))
    remote = endpoint(canceled())
    assert manager()._await_cancel_result(remote, None) is None
    remote.recv_from_guest.assert_not_called()


def test_approval_ack_helper_ignores_other_messages_before_cancellation():
    remote = endpoint(frame(MessageKind.APPROVAL_REQUEST), canceled())
    assert isolation.IsolationManager._await_cancel_ack(remote, 5.0)
    assert remote.recv_from_guest.call_count == 2


def test_approval_ack_helper_rejects_channel_failure():
    assert not isolation.IsolationManager._await_cancel_ack(endpoint(ChannelError('disconnected')), 5.0)
    assert not isolation.IsolationManager._await_cancel_ack(None, 5.0)
