"""OpenCode bridge lifecycle with owned sockets and never-executed process stubs."""
import io
import json
import socket
import subprocess
import sys
import threading
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from test_opencode_kvm import module


def read_exact(peer,size):
    chunks=[]
    while size:
        chunk=peer.recv(size)
        if not chunk:raise AssertionError('fixture stream ended before expected bytes')
        chunks.append(chunk);size-=len(chunk)
    return b''.join(chunks)


@pytest.fixture(autouse=True)
def initialized_socket(monkeypatch):
    monkeypatch.setattr(module,'socket',socket,raising=False)


@pytest.mark.parametrize('payload',[b'fixture',b'\x00\xff\x80',('Привет 🐙'*100).encode()],ids=['ascii','binary','unicode'])
def test_socket_pump_preserves_binary_both_directions_and_half_close(payload):
    left,client=socket.socketpair();right,server=socket.socketpair()
    for peer in [left,client,right,server]:peer.settimeout(3)
    thread=threading.Thread(target=module.pump,args=(left,right))
    thread.start()
    try:
        client.sendall(payload);client.shutdown(socket.SHUT_WR)
        chunks=[]
        while chunk:=server.recv(65536):chunks.append(chunk)
        assert b''.join(chunks)==payload
        server.sendall(b'ack');server.shutdown(socket.SHUT_WR)
        assert read_exact(client,3)==b'ack' and client.recv(1)==b''
        thread.join(3)
        assert not thread.is_alive()
        assert left.fileno()==right.fileno()==-1
    finally:
        for peer in [client,server,left,right]:peer.close()
        thread.join(3)


@pytest.mark.parametrize('connect_fails',[False,True])
def test_acceptor_handles_only_owned_local_connection_and_stops_on_listener_timeout(monkeypatch,connect_fails):
    listener=socket.socket();listener.bind(('127.0.0.1',0));listener.settimeout(.2)
    target,receiver=socket.socketpair();receiver.settimeout(3)
    created=[]
    def build(**kwargs):
        thread=threading.Thread(**kwargs);created.append(thread);return thread
    monkeypatch.setattr(module,'threading',SimpleNamespace(Thread=build))
    connector=Mock(side_effect=OSError('fixture connect') if connect_fails else None,return_value=target)
    module.bridge(listener,connector)
    client=socket.create_connection(listener.getsockname(),timeout=3)
    try:
        if connect_fails:
            assert client.recv(1)==b''
        else:
            client.sendall(b'ping');assert read_exact(receiver,4)==b'ping'
            receiver.sendall(b'pong');assert read_exact(client,4)==b'pong'
            client.shutdown(socket.SHUT_WR);receiver.shutdown(socket.SHUT_WR)
        connector.assert_called_once_with()
    finally:
        client.close();receiver.close();target.close();listener.close()
        for thread in created:thread.join(3);assert not thread.is_alive()


class StopProbe(Exception):
    pass


@pytest.fixture
def launcher(tmp_path,monkeypatch):
    sockets=[];handlers={};bridges=[];threads=[]
    class FakeSocket:
        def __init__(self,*args):self.kind=args;self.bound=None;self.closed=False;self.timeout=None;sockets.append(self)
        def bind(self,address):self.bound=address
        def getsockname(self):return ('127.0.0.1',20000+len(sockets))
        def close(self):self.closed=True
        def settimeout(self,value):self.timeout=value
        def connect(self,address):self.connected=address
    fake_socket=SimpleNamespace(socket=FakeSocket,AF_UNIX=1,AF_VSOCK=40,SOCK_STREAM=1,create_connection=Mock(return_value='fixture-connection'))
    monkeypatch.setattr(module,'socket',socket,raising=False)
    monkeypatch.setitem(sys.modules,'socket',fake_socket)
    child=Mock();child.wait.return_value=7
    captured_env=[]
    def spawn(argv,**kwargs):captured_env.append(dict(kwargs['env']));return child
    process=Mock(side_effect=spawn)
    monkeypatch.setattr(module,'subprocess',SimpleNamespace(Popen=process,DEVNULL=subprocess.DEVNULL,TimeoutExpired=subprocess.TimeoutExpired))
    env={'PATH':'/fixture','XDG_CONFIG_HOME':str(tmp_path/'config')}
    ops=SimpleNamespace(environ=env,chmod=Mock(),kill=Mock(),getpid=lambda:12345)
    monkeypatch.setattr(module,'os',ops)
    monkeypatch.setattr(module,'bridge',lambda listener,connect:bridges.append((listener,connect)))
    signal=SimpleNamespace(SIGTERM=15,SIGINT=2,SIG_DFL=0,signal=lambda signum,fn:handlers.update({signum:fn}),pause=Mock(side_effect=StopProbe))
    monkeypatch.setattr(module,'signal',signal)
    def thread_factory(**kwargs):threads.append(kwargs);return Mock()
    monkeypatch.setattr(module,'threading',SimpleNamespace(Thread=thread_factory))
    temporary=tmp_path/'temporary';temporary.mkdir()
    monkeypatch.setattr(module,'tempfile',SimpleNamespace(mkdtemp=lambda **kw:str(temporary)))
    domain=Mock();domain.isActive.return_value=True;domain.XMLDesc.return_value='<domain type="kvm"><devices><vsock><cid address="27"/></vsock></devices></domain>'
    connection=Mock();connection.lookupByUUIDString.return_value=domain
    libvirt=SimpleNamespace(open=Mock(return_value=connection));bootstrap=SimpleNamespace(activate=Mock())
    monkeypatch.setitem(sys.modules,'libvirt',libvirt);monkeypatch.setitem(sys.modules,'socket_bootstrap',bootstrap)
    def run(*,mode='bwrap',profiles=False,direct=False):
        config={'mode':mode,'bridge':str(tmp_path/'bridge'),'password':'fixture-password','bundle':str(tmp_path/'bundle'),'vm':'fixture-vm','projects':[]}
        if profiles:config['profiles']={'token':'fixture-grant','profiles':[{'id':'one','name':'fixture','api_format':'openai','model':'fixture-model'}]}
        if direct:config['assignment_direct']=True
        stream=io.StringIO(json.dumps(config)+'\nfixture parent input\n')
        monkeypatch.setattr(module,'sys',SimpleNamespace(stdin=stream,path=[]))
        with pytest.raises((SystemExit,StopProbe)) as error:module.main()
        return config,error.value
    return SimpleNamespace(run=run,sockets=sockets,bridges=bridges,handlers=handlers,threads=threads,child=child,process=process,ops=ops,fake_socket=fake_socket,domain=domain,libvirt=libvirt,bootstrap=bootstrap,temporary=temporary,captured_env=captured_env)


@pytest.mark.parametrize('profiles,direct',[(False,False),(True,False),(False,True),(True,True)])
def test_local_sandbox_launch_uses_private_bridges_and_ephemeral_environment(launcher,profiles,direct):
    f=launcher;_,error=f.run(profiles=profiles,direct=direct)
    assert isinstance(error,SystemExit) and error.code==7
    argv=f.process.call_args.args[0];kwargs=f.process.call_args.kwargs
    assert argv==['/opt/opencode/opencode','serve','--hostname','127.0.0.1','--port','4096']
    assert kwargs['stdin']==subprocess.DEVNULL and kwargs['stdout']==subprocess.DEVNULL
    assert kwargs['env']=={},'Ephemeral child environment is cleared after Popen returns'
    assert f.captured_env[0]['OPENCODE_SERVER_PASSWORD']=='fixture-password'
    assert f.captured_env[0]['HTTP_PROXY'].startswith('http://127.0.0.1:')
    if direct:assert json.loads(f.captured_env[0]['OPENCODE_CONFIG_CONTENT'])['snapshots'] is False
    assert len(f.bridges)==(3 if profiles else 2)
    assert 15 in f.handlers and 2 in f.handlers
    assert f.bridges[-1][1]()=='fixture-connection'
    f.fake_socket.create_connection.assert_called_once_with(('127.0.0.1',4096),timeout=10)
    if profiles:
        assert json.loads((f.temporary/'opencode.jsonc').read_text())['providers']['igorek-fixture']['settings']['apiKey']=='fixture-grant'


@pytest.mark.parametrize('wait_code',[0,None,9])
def test_unexpected_child_exit_never_reports_bridge_success(launcher,wait_code):
    launcher.child.wait.return_value=wait_code
    _,error=launcher.run()
    assert isinstance(error,SystemExit) and error.code==(wait_code or 1)


@pytest.mark.parametrize('timeout',[False,True])
def test_signal_handler_closes_only_owned_child_and_listener(launcher,timeout):
    f=launcher;f.run();f.child.wait.reset_mock(side_effect=True)
    if timeout:f.child.wait.side_effect=subprocess.TimeoutExpired('fixture',10)
    with pytest.raises(SystemExit) as error:f.handlers[15]()
    assert error.value.code==0
    f.child.terminate.assert_called_once_with()
    assert f.child.kill.called is timeout
    assert f.sockets[0].closed


def test_parent_eof_requests_only_own_process_stop_via_fake_signal(launcher):
    f=launcher;f.run();target=next(row['target'] for row in f.threads if row['target'].__name__=='parent')
    target()
    f.ops.kill.assert_called_once_with(12345,15)


def test_kvm_connector_revalidates_vm_before_connecting(launcher):
    f=launcher;config,error=f.run(mode='kvm')
    assert isinstance(error,StopProbe)
    f.bootstrap.activate.assert_called_once()
    f.libvirt.open.assert_called_once_with('qemu:///system')
    f.process.assert_not_called()
    connection=f.bridges[-1][1]()
    assert connection.connected==(27,40521) and connection.timeout is None
    f.domain.isActive.return_value=False
    with pytest.raises(OSError,match='changed'):f.bridges[-1][1]()
    assert len(f.sockets)==2


def test_kvm_connector_refuses_changed_vsock_identity(launcher):
    f=launcher;f.run(mode='kvm')
    f.domain.XMLDesc.return_value='<domain type="kvm"><devices><vsock><cid address="28"/></vsock></devices></domain>'
    with pytest.raises(OSError,match='changed'):f.bridges[-1][1]()
    assert len(f.sockets)==1


def test_stopped_vm_is_rejected_before_ready_or_guest_connection(launcher):
    launcher.domain.isActive.return_value=False
    with pytest.raises(ValueError,match='stopped'):launcher.run(mode='kvm')
    assert launcher.bridges==[] and not launcher.process.called


def test_kvm_stop_closes_bridge_without_host_child_process(launcher):
    f=launcher;f.run(mode='kvm')
    with pytest.raises(SystemExit) as error:f.handlers[15]()
    assert error.value.code==0 and f.sockets[0].closed
    f.child.terminate.assert_not_called()
