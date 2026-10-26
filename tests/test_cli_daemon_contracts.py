"""Shared CLI runtime protocol and lifecycle using only owned data and fake workers."""
import json
import os
from pathlib import Path
import socket
import threading
from unittest.mock import Mock

import pytest

from factory.core import cli_daemon as runtime
from factory.core import cli_tasks
from factory.core.schema import initialize_database


@pytest.fixture
def local(tmp_path,monkeypatch):
    root=tmp_path/'runtime';root.mkdir();profiles=root/'profiles';profiles.mkdir()
    for name in ['one','two']:(profiles/(name+'.json')).write_text('{}')
    db=root/'factory.db';initialize_database(str(db))
    monkeypatch.setattr(runtime,'get_data_root',lambda:root)
    monkeypatch.setattr(runtime,'get_profiles_dir',lambda:str(profiles))
    monkeypatch.setattr(runtime,'get_db_path',lambda:str(db))
    monkeypatch.setattr(runtime,'get_profile_config',lambda profile:{'model':'fixture'})
    return root,db


class Stream:
    def __init__(self,*chunks):self.chunks=list(chunks)
    def recv(self,size):return self.chunks.pop(0) if self.chunks else b''


@pytest.mark.parametrize('chunks,expected',[
    ([b'{"a":',b'1}\n'],{'a':1}),([b'{"a":"', '🐙'.encode(),b'"}\n'],{'a':'🐙'}),
])
def test_receive_handles_fragmented_utf8_json_frames(chunks,expected):
    assert runtime.receive(Stream(*chunks))==expected


@pytest.mark.parametrize('chunks',[[b'[]\n'],[b'{bad}\n'],[b'{"a":1}'],[b'']])
def test_receive_rejects_nonobject_malformed_or_incomplete_frames(chunks):
    with pytest.raises((ValueError,json.JSONDecodeError)):runtime.receive(Stream(*chunks))


def test_receive_enforces_frame_budget_before_decoding(monkeypatch):
    monkeypatch.setattr(runtime,'LIMIT',16)
    with pytest.raises(ValueError,match='Invalid daemon frame'):runtime.receive(Stream(b'x'*17))


@pytest.mark.parametrize('action',['version','ensure','submit','stop'])
def test_wrong_instance_identity_cannot_dispatch_any_command(local,action):
    daemon=runtime.Daemon()
    with pytest.raises(ValueError,match='instance changed'):daemon.dispatch({'id':'wrong','action':action})
    assert not daemon.stop.is_set() and not daemon.served_profiles


def test_submit_validates_payload_and_preserves_profile_and_input_files(local,monkeypatch):
    daemon=runtime.Daemon();daemon.ensure=Mock()
    create=Mock(return_value={'task_id':'fixture-task'});monkeypatch.setattr(cli_tasks,'create_task',create)
    result=daemon.dispatch({'id':daemon.identity,'action':'submit','profile':'one','text':'Fixture','metadata':{'mode':'fixture'},'input_files':['fixture.txt']})
    assert result=={'task_id':'fixture-task'}
    create.assert_called_once_with('Fixture',profile='one',metadata={'mode':'fixture'},input_files=['fixture.txt'])
    for invalid in [{'text':None},{'text':'fixture','metadata':[]}]:
        with pytest.raises(ValueError,match='payload'):daemon.dispatch({'id':daemon.identity,'action':'submit','profile':'one',**invalid})
    assert create.call_count==1


def test_unknown_action_is_explicit_and_version_is_readonly(local):
    daemon=runtime.Daemon();daemon.served_profiles={'two','one'}
    result=daemon.dispatch({'id':daemon.identity,'action':'version'})
    assert result['running'] and result['profiles']==['one','two'] and not result['scheduler']
    with pytest.raises(ValueError,match='Unknown daemon action'):daemon.dispatch({'id':daemon.identity,'action':'unsupported'})
    assert not daemon.stop.is_set()


@pytest.mark.parametrize('busy',['lock','schedule'])
def test_busy_daemon_refuses_stop_and_releases_any_acquired_locks(local,busy):
    daemon=runtime.Daemon();first=threading.Lock();second=threading.Lock()
    daemon.execution_locks={'one':first,'two':second}
    if busy=='lock':second.acquire()
    else:daemon.scheduled=1
    try:
        with pytest.raises(RuntimeError,match='busy'):daemon.dispatch({'id':daemon.identity,'action':'stop'})
        assert not daemon.stop.is_set() and first.acquire(blocking=False)
        first.release()
    finally:
        if busy=='lock':second.release()


def test_idle_daemon_stop_changes_only_its_own_event(local):
    daemon=runtime.Daemon()
    assert daemon.dispatch({'id':daemon.identity,'action':'stop'})=={'stopping':True}
    assert daemon.stop.is_set()


@pytest.mark.parametrize('profile',[None,'','../outside','name with spaces','missing'])
def test_ensure_rejects_invalid_or_unconfigured_profile_before_worker_start(local,profile):
    daemon=runtime.Daemon()
    with pytest.raises(ValueError):daemon.ensure(profile)
    assert daemon.workers=={}


def test_existing_consumer_prevents_duplicate_worker_creation(local,monkeypatch):
    monkeypatch.setattr(runtime,'consumers',lambda *args:True)
    daemon=runtime.Daemon();daemon.ensure('one')
    assert daemon.served_profiles=={'one'} and daemon.workers=={}
    daemon.stop.set()
    with pytest.raises(RuntimeError,match='stopping'):daemon.ensure('one')


def test_new_workers_share_execution_lock_and_only_run_synthetic_callbacks(local,monkeypatch):
    from factory.core import worker
    entered=[];consumers={}
    class FakeWorker:
        def __init__(self,profile,**kwargs):self.profile=profile
        def run(self,*,stop_event,execution_lock):
            entered.append((self.profile,execution_lock));consumers[self.profile]=True;stop_event.wait(2)
    monkeypatch.setattr(worker,'Worker',FakeWorker)
    monkeypatch.setattr(runtime,'consumers',lambda profile,config:consumers.get(profile,False))
    daemon=runtime.Daemon()
    try:
        daemon.ensure('one');daemon.ensure('two')
        assert {x[0] for x in entered}=={'one','two'}
        assert len({id(x[1]) for x in entered})==1
        assert daemon.execution_locks['one'] is daemon.execution_locks['two']
    finally:
        daemon.stop.set()
        for thread in daemon.workers.values():thread.join(2)
    assert all(not thread.is_alive() for thread in daemon.workers.values())


@pytest.mark.parametrize('raises',[False,True])
def test_schedule_count_is_restored_even_when_delivery_fails(local,monkeypatch,raises):
    from factory import factory_api
    daemon=runtime.Daemon();daemon.ensure=Mock()
    def execute(info):
        assert daemon.scheduled==1
        if raises:raise RuntimeError('fixture failed')
    monkeypatch.setattr(factory_api,'_execute_schedule_via_gateway',execute)
    if raises:
        with pytest.raises(RuntimeError,match='fixture failed'):daemon.schedule({'agent_id':'one'})
    else:daemon.schedule({'agent_id':'one'})
    assert daemon.scheduled==0


@pytest.mark.parametrize('error',[KeyError('missing'),ValueError('invalid'),OSError('fixture'),RuntimeError('stopping')])
def test_schedule_readiness_returns_false_without_hiding_error_in_a_worker(local,error):
    daemon=runtime.Daemon();daemon.ensure=Mock(side_effect=error)
    assert daemon.schedule_ready({'agent_id':'one'}) is False


@pytest.mark.parametrize('payload',[None,[],{'action':'version','id':'wrong'}])
def test_owned_socket_protocol_returns_errors_instead_of_crashing_thread(local,payload):
    daemon=runtime.Daemon();client,peer=socket.socketpair()
    with client,peer:
        client.settimeout(2)
        thread=threading.Thread(target=daemon.handle,args=(peer,));thread.start()
        client.sendall(json.dumps(payload).encode()+b'\n')
        result=runtime.receive(client);thread.join(2)
    assert not thread.is_alive() and 'error' in result and not daemon.stop.is_set()


def test_owned_socket_version_roundtrip_needs_no_worker_or_scheduler(local):
    daemon=runtime.Daemon();client,peer=socket.socketpair()
    with client,peer:
        client.settimeout(2)
        thread=threading.Thread(target=daemon.handle,args=(peer,));thread.start()
        client.sendall(json.dumps({'id':daemon.identity,'action':'version'}).encode()+b'\n')
        result=runtime.receive(client);thread.join(2)
    assert result['result']['running'] and not thread.is_alive() and daemon.workers=={}


def test_lifecycle_status_and_stop_use_only_the_owned_endpoint(local,monkeypatch):
    root,_=local
    call=Mock(return_value=None);monkeypatch.setattr(runtime,'request',call)
    assert runtime.lifecycle('version')['running'] is False
    assert runtime.lifecycle('stop')=={'running':False}
    assert all(x.args==('version',) for x in call.call_args_list)


def test_lifecycle_reuses_existing_runtime_without_spawning(local,monkeypatch):
    request=Mock(return_value={'running':True,'version':'fixture'})
    spawn=Mock(side_effect=AssertionError('Unexpected process'))
    monkeypatch.setattr(runtime,'request',request);monkeypatch.setattr(runtime.subprocess,'Popen',spawn)
    assert runtime.lifecycle('start')=={'running':True,'version':'fixture'} and not spawn.called


@pytest.mark.parametrize('outcome',['ready','exited','timeout'])
def test_start_lifecycle_reports_actual_start_outcome_without_spawning_a_process(local,monkeypatch,outcome):
    process=Mock();process.poll.return_value=1 if outcome=='exited' else None
    spawn=Mock(return_value=process);monkeypatch.setattr(runtime.subprocess,'Popen',spawn)
    request=Mock(side_effect=[None,{'running':True,'version':'fixture'}] if outcome=='ready' else lambda *args,**kwargs:None)
    monkeypatch.setattr(runtime,'request',request)
    if os.name!='posix':
        with pytest.raises(RuntimeError,match='POSIX'):runtime.lifecycle('start')
        assert not spawn.called
        return
    if outcome=='timeout':
        monkeypatch.setattr(runtime,'time',type('Clock',(),{'monotonic':staticmethod(Mock(side_effect=[0,1,50])),'sleep':staticmethod(lambda seconds:None)}))
    if outcome=='ready':
        assert runtime.lifecycle('start')=={'running':True,'version':'fixture'}
    else:
        with pytest.raises(RuntimeError,match='failed to start|startup timed out'):runtime.lifecycle('start')
    assert spawn.call_count==1 and spawn.call_args.kwargs['start_new_session'] is True
    assert spawn.call_args.args[0][-1]=='factory.core.cli_daemon'


def test_restart_waits_for_owned_endpoint_removal_before_starting_replacement(local,monkeypatch):
    root,_=local;directory=runtime.directory();endpoint=directory/'endpoint.json';endpoint.write_text('{}')
    calls=[]
    def request(action,**kwargs):
        calls.append(action)
        if action=='stop':endpoint.unlink();return {'stopping':True}
        return {'running':True,'version':'fixture'} if len(calls)==1 or len(calls)>2 else None
    monkeypatch.setattr(runtime,'request',request)
    spawn=Mock(return_value=Mock(poll=lambda:None));monkeypatch.setattr(runtime.subprocess,'Popen',spawn)
    if os.name!='posix':
        with pytest.raises(RuntimeError,match='POSIX'):runtime.lifecycle('restart')
        return
    assert runtime.lifecycle('restart')['running']
    assert calls==['version','stop','version'] and spawn.call_count==1


@pytest.mark.skipif(os.name!='posix',reason='POSIX Unix socket and peer-credential runtime protocol')
@pytest.mark.parametrize('action',['version','unsupported'])
def test_real_owned_unix_socket_client_server_roundtrip(local,action):
    import tempfile
    daemon=runtime.Daemon()
    with tempfile.TemporaryDirectory(prefix='igorek-ipc32-') as td:
        address=Path(td)/'control.sock'
        with socket.socket(socket.AF_UNIX,socket.SOCK_STREAM) as listener:
            listener.bind(str(address));listener.listen(1);listener.settimeout(2)
            runtime.write_private(runtime.directory()/'endpoint.json',{'id':daemon.identity,'socket':str(address)})
            def serve_one():
                stream,_=listener.accept();daemon.handle(stream)
            thread=threading.Thread(target=serve_one);thread.start()
            try:
                if action=='version':assert runtime.request(action)['running']
                else:
                    with pytest.raises(RuntimeError,match='Unknown daemon action'):runtime.request(action)
            finally:thread.join(3)
            assert not thread.is_alive() and daemon.workers=={}


def test_optional_request_to_missing_owned_endpoint_returns_none(local):
    assert runtime.request('version',optional=True) is None
    with pytest.raises(RuntimeError,match='not running'):runtime.request('version')
