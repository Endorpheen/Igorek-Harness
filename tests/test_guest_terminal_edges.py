"""Terminal transport/exit boundaries with framed buffers and fake VSOCK only."""
import errno
import io
import json
import struct
import sys
from types import SimpleNamespace
from unittest.mock import MagicMock,Mock

import pytest

from factory.core.isolation import guest_terminal as g,channel
from factory.core.isolation.channel import ChannelError,ChannelConfig
from factory.core.isolation.terminal_protocol import TERMINAL_PROTOCOL_VERSION,TerminalProtocolError,TerminalStage,decode_message


class Framed:
    def __init__(self,value):
        raw=json.dumps(value).encode() if value is not None else None
        self.input=io.BytesIO(struct.pack('!I',len(raw))+raw if raw is not None else b'')
        self.output=io.BytesIO()
    def recv(self,size):return self.input.read(size)
    def sendall(self,data):self.output.write(data)
    def settimeout(self,value):self.timeout=value


@pytest.mark.parametrize('ack,accepted',[
    ({'kind':'hello_ack','accepted':True},True),
    ({'kind':'hello_ack','accepted':False,'reason':'fixture denied'},False),
    ({'kind':'wrong','accepted':True},False),(None,False),([],False),
])
def test_handshake_uses_validated_frames_and_never_accepts_bad_ack(ack,accepted):
    wire=Framed(ack)
    assert g.guest_terminal_handshake(wire,ChannelConfig()) is accepted
    sent=json.loads(wire.output.getvalue()[4:])
    assert sent=={'kind':'hello','version':TERMINAL_PROTOCOL_VERSION}


@pytest.mark.parametrize('failure',['missing-family','connect','ok'])
def test_vsock_client_boundary_closes_failed_socket_without_network(monkeypatch,failure):
    peer=Mock()
    if failure=='connect':peer.connect.side_effect=OSError('fixture unavailable')
    factory=Mock(return_value=peer);module=SimpleNamespace(AF_VSOCK=None if failure=='missing-family' else 40,SOCK_STREAM=1,socket=factory)
    monkeypatch.setitem(sys.modules,'socket',module)
    if failure=='ok':assert g.open_vsock_client(2,5006) is peer
    else:
        with pytest.raises(ChannelError,match='vsock_unavailable' if failure=='missing-family' else 'vsock_connect_failed'):g.open_vsock_client(2,5006)
    if failure=='missing-family':factory.assert_not_called()
    else:peer.connect.assert_called_once_with((2,5006))
    assert peer.close.called is (failure=='connect')


def test_listener_rejects_foreign_peer_and_failed_handshake_before_service(monkeypatch,tmp_path):
    class StopProbe(Exception):pass
    peers=[MagicMock() for _ in range(3)]
    for peer in peers:peer.__enter__.return_value=peer
    listener=MagicMock();listener.__enter__.return_value=listener
    listener.accept.side_effect=[(peers[0],(99,0)),(peers[1],(2,0)),(peers[2],(2,0)),StopProbe()]
    open_listener=Mock(return_value=listener);monkeypatch.setattr(channel,'open_vsock_listener',open_listener)
    handshake=Mock(side_effect=[False,True]);monkeypatch.setattr(g,'guest_terminal_handshake',handshake)
    service=Mock();factory=Mock(return_value=service);monkeypatch.setattr(g,'GuestTerminalService',factory)
    monkeypatch.setenv('IGOREK_TERMINAL_WORKDIR',str(tmp_path))
    with pytest.raises(StopProbe):g.run_guest_terminal(vsock_cid=2,vsock_port=5006)
    open_listener.assert_called_once_with(5006);assert handshake.call_count==2
    assert factory.call_args.args[0] is peers[2] and factory.call_args.kwargs['tuning'].workdir==str(tmp_path)
    service.serve.assert_called_once()
    for peer in peers:peer.__exit__.assert_called_once()


@pytest.mark.parametrize('outcome',['eof','close','idle','channel-error','unexpected'])
def test_service_always_closes_owned_pty_on_transport_exit(monkeypatch,outcome):
    wire=Framed(None);service=g.GuestTerminalService(wire,on_log=Mock());pty=Mock();service._pty=pty
    value=None if outcome=='eof' else g._CLOSE if outcome=='close' else g._IDLE
    read=Mock(return_value=value)
    if outcome=='channel-error':read.side_effect=ChannelError('fixture closed')
    if outcome=='unexpected':read.side_effect=RuntimeError('fixture read failed')
    monkeypatch.setattr(service,'_read_host_message',read);monkeypatch.setattr(service,'_serve_pty',lambda:True)
    assert service.serve()==(1 if outcome=='unexpected' else 0)
    pty.close.assert_called_once();assert service._pty is None


def test_valid_input_backend_failure_emits_reason_and_exit_then_closes(monkeypatch):
    wire=Framed(None);service=g.GuestTerminalService(wire,on_log=Mock());pty=Mock();pty.write.side_effect=TerminalProtocolError('pty_write_failed');pty.poll_exit.return_value=7;service._pty=pty
    message=decode_message({'kind':'input','seq':1,'payload':{'data':'dHlwZWQ='}},stage=TerminalStage.INTERNAL)
    monkeypatch.setattr(service,'_read_host_message',Mock(return_value=message))
    assert service.serve()==0
    raw=wire.output.getvalue();frames=[]
    while raw:
        size=struct.unpack('!I',raw[:4])[0];frames.append(json.loads(raw[4:4+size]));raw=raw[4+size:]
    assert [(row['kind'],row['payload']) for row in frames]==[('error',{'reason':'pty_write_failed'}),('exit',{'code':7})]
    pty.write.assert_called_once_with(b'typed');pty.close.assert_called_once()


@pytest.mark.parametrize('error,reaped',[(ChildProcessError(),True),(OSError(errno.ECHILD,'fixture gone'),True),(OSError(errno.EINTR,'fixture interrupted'),False),(OSError(errno.EIO,'fixture wait error'),False)])
def test_wait_errors_preserve_ownership_until_reaping_is_confirmed(monkeypatch,error,reaped):
    pty=g.GuestPty(g.TerminalTuning());pty._pid=123456789
    monkeypatch.setattr(g.os,'waitpid',Mock(side_effect=error));kill=Mock();monkeypatch.setattr(g.os,'kill',kill)
    assert pty.poll_exit()==(0 if reaped else None)
    assert (pty.pid is None) is reaped
    pty.close();assert kill.called is (not reaped)


def test_cli_options_do_not_start_service_until_dispatch(monkeypatch):
    runner=Mock(return_value=7);monkeypatch.setattr(g,'run_guest_terminal',runner)
    assert g.main(['--vsock-cid','2','--vsock-port','5006'])==7
    runner.assert_called_once_with(vsock_cid=2,vsock_port=5006)
