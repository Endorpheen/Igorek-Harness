"""Reconnects must not duplicate guest tasks or leak host attachments."""
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from factory.core.isolation.approval import WorkspacePathMapping
from factory.core.isolation.channel import ChannelError, MessageKind
from factory.core.isolation.manager import IsolationError, IsolationManager, _guest_steer_item


def manager(wait=5.0):
    obj = object.__new__(IsolationManager)
    obj._seams = SimpleNamespace(endpoint_wait_s=wait, channel=object())
    obj._await_endpoint = Mock()
    return obj


def vm(*states):
    return SimpleNamespace(domain=SimpleNamespace(is_active=Mock(side_effect=list(states))))


def test_closed_replacement_is_retried_only_on_another_peer():
    owner = manager()
    old = Mock(); failed = Mock(); good = Mock()
    failed.send.side_effect = ChannelError('channel_closed')
    owner._await_endpoint.side_effect = [failed, good]
    task = {'task_id': 'fixture'}
    result = owner._send_task_after_reconnect('session', vm(True, True), old, task)
    assert result is good
    failed.send.assert_called_once_with(MessageKind.TASK, task)
    good.send.assert_called_once_with(MessageKind.TASK, task)
    assert owner._await_endpoint.call_args_list[0].kwargs['previous'] is old
    assert owner._await_endpoint.call_args_list[1].kwargs['previous'] is failed


def test_non_closed_send_error_is_not_retried():
    owner = manager()
    replacement = Mock();replacement.send.side_effect = ChannelError('broken_pipe', 'fixture')
    owner._await_endpoint.return_value = replacement
    with pytest.raises(IsolationError) as failure:
        owner._send_task_after_reconnect('session', vm(True), Mock(), {'task_id':'fixture'})
    assert failure.value.reason == 'channel_send_failed:broken_pipe'
    assert owner._await_endpoint.call_count == 1
    replacement.send.assert_called_once()


def test_stopped_vm_prevents_delivery():
    owner = manager()
    with pytest.raises(IsolationError) as failure:
        owner._send_task_after_reconnect('session', vm(False), Mock(), {'task_id':'fixture'})
    assert failure.value.reason == 'vm_stopped_before_task_delivery'
    owner._await_endpoint.assert_not_called()


def test_disconnected_peer_reports_reconnect_timeout():
    owner = manager()
    owner._await_endpoint.return_value = None
    with pytest.raises(IsolationError) as failure:
        owner._send_task_after_reconnect('session', vm(True), Mock(), {'task_id':'fixture'})
    assert failure.value.reason == 'guest_channel_reconnect_timeout'
    owner._await_endpoint.assert_called_once()


def test_expired_reconnect_budget_does_not_wait_or_send():
    owner = manager(wait=0)
    with pytest.raises(IsolationError) as failure:
        owner._send_task_after_reconnect('session', vm(True), Mock(), {'task_id':'fixture'})
    assert failure.value.reason == 'guest_channel_reconnect_timeout'
    owner._await_endpoint.assert_not_called()


def test_vm_stopping_after_closed_peer_prevents_second_delivery():
    owner = manager()
    replacement = Mock();replacement.send.side_effect = ChannelError('channel_closed')
    owner._await_endpoint.return_value = replacement
    with pytest.raises(IsolationError) as failure:
        owner._send_task_after_reconnect('session', vm(True, False), Mock(), {'task_id':'fixture'})
    assert failure.value.reason == 'vm_stopped_before_task_delivery'
    assert replacement.send.call_count == 1 and owner._await_endpoint.call_count == 1


def test_workspace_attachment_is_mapped_into_guest(tmp_path):
    workspace=tmp_path/'workspace';workspace.mkdir();attachment=workspace/'report.txt';attachment.write_text('fixture')
    mapping=WorkspacePathMapping(host_root=str(workspace), guest_root='/workspace')
    message={'text':'Read '+str(attachment),'message_id':'m1','input_files':[str(attachment)],'selected_skills':['review']}
    result=_guest_steer_item(message,mapping)
    assert result['input_files']==['/workspace/report.txt']
    assert result['text'].startswith('Read /workspace')
    assert result['message_id']=='m1' and result['selected_skills']==['review']


def test_outside_attachment_is_removed_from_guest_message(tmp_path):
    workspace=tmp_path/'workspace';workspace.mkdir();outside=tmp_path/'private.txt';outside.write_text('fixture')
    mapping=WorkspacePathMapping(host_root=str(workspace), guest_root='/workspace')
    result=_guest_steer_item({'text':'Read '+str(outside),'message_id':'m1','input_files':[str(outside)],'selected_skills':[]},mapping)
    assert result['input_files']==[]
    assert str(outside) not in result['text']
    assert 'вложение недоступно' in result['text']


@pytest.mark.parametrize('value', [None, 'text', {}, {'text': 'hi'}, {'text': 4, 'message_id':'m1'}])
def test_invalid_steering_message_is_not_forwarded(value,tmp_path):
    assert _guest_steer_item(value,WorkspacePathMapping(host_root=str(tmp_path))) is None


def test_invalid_attachment_or_skill_types_are_not_forwarded(tmp_path):
    mapping=WorkspacePathMapping(host_root=str(tmp_path))
    result=_guest_steer_item({'text':'hello','message_id':'m1','input_files':['ok',3],'selected_skills':['good',3]},mapping)
    assert result=={'text':'hello','message_id':'m1','input_files':[],'selected_skills':[]}
