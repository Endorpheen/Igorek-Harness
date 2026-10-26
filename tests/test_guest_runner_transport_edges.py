"""Guest transport error routing with synthetic sessions, never a VM or executor."""
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from factory.core.isolation import guest_runner as g
from factory.core.isolation.channel import ChannelError,ChannelMessage,MessageKind


@pytest.mark.parametrize('failure',['unavailable','connect','ok'])
def test_vsock_connector_translates_failure_and_closes_owned_socket(monkeypatch,failure):
    peer=Mock()
    if failure=='connect':peer.connect.side_effect=OSError('fixture denied')
    factory=Mock(return_value=peer)
    monkeypatch.setattr(g,'socket',SimpleNamespace(AF_VSOCK=None if failure=='unavailable' else 40,SOCK_STREAM=1,socket=factory))
    if failure=='ok':
        assert g.open_vsock_client(2,5005) is peer
        peer.connect.assert_called_once_with((2,5005));peer.close.assert_not_called()
    else:
        with pytest.raises(ChannelError) as error:g.open_vsock_client(2,5005)
        assert error.value.reason==('vsock_unavailable' if failure=='unavailable' else 'vsock_connect_failed')
        if failure=='connect':peer.close.assert_called_once()
        else:factory.assert_not_called()


@pytest.mark.parametrize('failure',['connect','handshake','serve','ok'])
def test_runner_entrypoint_closes_session_on_every_post_handshake_exit(monkeypatch,failure):
    socket=Mock();session=Mock();runner=Mock();runner.serve_one.return_value=7
    if failure=='serve':runner.serve_one.side_effect=RuntimeError('fixture executor boundary')
    connector=Mock(side_effect=ChannelError('fixture_connect') if failure=='connect' else None,return_value=socket)
    handshake=Mock(side_effect=ChannelError('fixture_handshake') if failure=='handshake' else None,return_value=session)
    monkeypatch.setattr(g,'open_vsock_client',connector);monkeypatch.setattr(g,'guest_connect',handshake);monkeypatch.setattr(g,'GuestRunner',Mock(return_value=runner));monkeypatch.setattr(g,'_stderr_log',Mock())
    if failure=='serve':
        with pytest.raises(RuntimeError,match='fixture executor'):g.run_guest_runner(vsock_cid=2,vsock_port=5005)
    else:
        assert g.run_guest_runner(vsock_cid=2,vsock_port=5005)==(7 if failure=='ok' else 1)
    connector.assert_called_once_with(2,5005)
    if failure=='connect':handshake.assert_not_called()
    elif failure=='handshake':socket.close.assert_called_once()
    else:session.close.assert_called_once()


@pytest.fixture
def runner():
    session=Mock()
    executor=Mock(side_effect=AssertionError('executor must not start'))
    result=g.GuestRunner(session,seams=g.RunnerSeams(executor=executor,log=Mock()))
    return result,session,executor


@pytest.mark.parametrize('kind,relay',[(MessageKind.WEB_REPLY,'web_tools'),(MessageKind.SESSION_SEARCH_REPLY,'session_search'),(MessageKind.VAULT_STORE_REPLY,'vault_store'),(MessageKind.MEMORY_FORGET_REPLY,'memory_forget'),(MessageKind.STEER_REPLY,'steer'),(MessageKind.USER_INPUT_REPLY,'user_input')])
def test_reply_is_delivered_only_to_matching_pending_bridge(runner,kind,relay):
    subject,session,executor=runner
    relays={name:Mock() for name in ['user_input','web_tools','session_search','vault_store','memory_forget','steer']}
    state=g._RunState(**relays)
    message=ChannelMessage(kind=kind,seq=1,payload={'fixture':True})
    state.inbox.put(message)
    subject._handle_inbound(state)
    for name,bridge in relays.items():
        if name==relay:bridge.deliver.assert_called_once_with(message)
        else:bridge.deliver.assert_not_called()
    assert not state.cancel.is_set();session.send.assert_not_called();executor.assert_not_called()


@pytest.mark.parametrize('kind',[None,MessageKind.CANCEL,MessageKind.USER_INPUT_REPLY])
def test_host_closure_and_explicit_cancel_set_cooperative_stop(runner,kind):
    subject,session,executor=runner
    state=g._RunState(**{name:Mock() for name in ['user_input','web_tools','session_search','vault_store','memory_forget','steer']})
    state.user_input.deliver.return_value=g.UserInputReply.CANCELLED
    state.inbox.put(None if kind is None else ChannelMessage(kind=kind,seq=1,payload={}))
    subject._handle_inbound(state)
    assert state.cancel.is_set();executor.assert_not_called()


@pytest.mark.parametrize('kind,reason',[(MessageKind.TASK,'task_already_running'),(MessageKind.HOST_OP,'host_op_unsupported'),(MessageKind.EVENT,'unexpected_frame'),(MessageKind.RESULT,'unexpected_frame'),(MessageKind.ERROR,'unexpected_frame'),(MessageKind.HEARTBEAT,'unexpected_frame')])
def test_unexpected_inbound_frame_gets_explicit_error_without_executing(runner,kind,reason):
    subject,session,executor=runner
    state=g._RunState(**{name:Mock() for name in ['user_input','web_tools','session_search','vault_store','memory_forget','steer']})
    state.inbox.put(ChannelMessage(kind=kind,seq=1,payload={}))
    subject._handle_inbound(state)
    assert session.send.call_args.args[0]==MessageKind.ERROR
    assert reason in session.send.call_args.args[1]['reason']
    assert not state.cancel.is_set();executor.assert_not_called()


@pytest.mark.parametrize('failure',['channel','eof','wrong-kind','invalid-task'])
def test_invalid_initial_task_fails_without_starting_executor(runner,failure):
    subject,session,executor=runner
    if failure=='channel':session.read.side_effect=ChannelError('fixture_read')
    elif failure=='eof':session.read.return_value=None
    else:session.read.return_value=ChannelMessage(kind=MessageKind.TASK if failure=='invalid-task' else MessageKind.EVENT,seq=1,payload={})
    assert subject._read_task() is None
    assert session.send.called is (failure!='eof')
    executor.assert_not_called()
