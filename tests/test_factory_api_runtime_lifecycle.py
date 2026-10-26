"""API entrypoint resource cleanup, including real loopback shutdown."""
import http.client
import sys
import threading
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from factory import factory_api as api


class Service:
    def __init__(self):self.started=0;self.stopped=0;self.running=False
    def start(self):self.started+=1;self.running=True
    def stop(self):self.stopped+=1;self.running=False


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    for name in ['IGOREK_FACTORY_HOME','FACTORY_DATA_DIR']:monkeypatch.setenv(name,str(tmp_path))
    db=api.get_db_path();api.initialize_database(db)
    for name,value in [('DB_PATH',db),('FACTORY_DIR',tmp_path),('WORKSPACE_ROOT',str(tmp_path/'workspace')),('PROFILES_DIR',str(tmp_path/'profiles')),('MODE','local'),('API_KEY','fixture-key'),('SCHEDULER_INITIALIZED',False),('WATCHDOG_INSTANCE',None),('BOLTBOOK_HEARTBEAT_INSTANCE',None),('BOLTBOOK_HEARTBEAT_ENABLED',True)]:monkeypatch.setattr(api,name,value)
    scheduler,watchdog,heartbeat=Service(),Service(),Service();callbacks=[]
    monkeypatch.setattr(api,'initialize_scheduler',callbacks.append)
    monkeypatch.setattr(api,'get_scheduler',lambda:scheduler)
    monkeypatch.setattr(api,'TaskWatchdog',lambda **kwargs:watchdog)
    monkeypatch.setattr(api,'BoltbookHeartbeatLoop',lambda **kwargs:heartbeat)
    monkeypatch.setattr(api,'LOGGER',Mock())
    monkeypatch.setattr(sys,'argv',['factory-api','--port','0'])
    return SimpleNamespace(scheduler=scheduler,watchdog=watchdog,heartbeat=heartbeat,callbacks=callbacks,db=db)


@pytest.mark.parametrize('exit_kind',['normal','interrupt','error'])
@pytest.mark.parametrize('heartbeat_enabled',[False,True])
def test_main_closes_server_and_background_services_for_every_serve_exit(runtime,monkeypatch,exit_kind,heartbeat_enabled):
    state={'closed':False}
    class Server:
        def serve_forever(self):
            if exit_kind=='interrupt':raise KeyboardInterrupt()
            if exit_kind=='error':raise OSError('fixture serve failure')
        def server_close(self):state['closed']=True
    monkeypatch.setattr(api,'BOLTBOOK_HEARTBEAT_ENABLED',heartbeat_enabled)
    monkeypatch.setattr(api,'BoundedHTTPServer',lambda *a:Server())
    api.main()
    assert runtime.scheduler.started==runtime.scheduler.stopped==1
    assert runtime.watchdog.started==runtime.watchdog.stopped==1
    assert runtime.heartbeat.started==runtime.heartbeat.stopped==int(heartbeat_enabled)
    assert state['closed'] and runtime.callbacks==[api._execute_schedule_via_gateway]


@pytest.mark.parametrize('mode,argv,host',[('local',[],'127.0.0.1'),('lan',[],'0.0.0.0'),('lan',['--host','127.0.0.2'],'127.0.0.2')])
def test_main_preserves_host_selection_without_binding_public_ports(runtime,monkeypatch,mode,argv,host):
    addresses=[]
    class Server:
        def serve_forever(self):raise KeyboardInterrupt()
        def server_close(self):pass
    def create(address,handler):addresses.append(address);return Server()
    monkeypatch.setattr(api,'MODE',mode);monkeypatch.setattr(sys,'argv',['factory-api','--port','0',*argv]);monkeypatch.setattr(api,'BoundedHTTPServer',create)
    api.main()
    assert addresses==[(host,0)]


def test_invalid_port_does_not_start_background_services(runtime,monkeypatch):
    monkeypatch.setattr(sys,'argv',['factory-api','--port','not-an-int'])
    with pytest.raises(SystemExit) as error:api.main()
    assert error.value.code==2 and not runtime.scheduler.started and not runtime.watchdog.started


def test_bind_failure_stops_already_started_services(runtime,monkeypatch):
    def create(*a):raise OSError('fixture bind failure')
    monkeypatch.setattr(api,'BoundedHTTPServer',create);api.main()
    assert runtime.scheduler.stopped==runtime.watchdog.stopped==runtime.heartbeat.stopped==1


def test_real_server_shutdown_releases_socket_and_background_services(runtime,monkeypatch):
    actual=api.BoundedHTTPServer;bound=threading.Event();instances=[];errors=[]
    def create(address,handler):
        server=actual(('127.0.0.1',0),handler);instances.append(server);bound.set();return server
    monkeypatch.setattr(api,'BoundedHTTPServer',create)
    def run():
        try:api.main()
        except BaseException as error:errors.append(error)
    worker=threading.Thread(target=run,daemon=True);worker.start()
    assert bound.wait(4)
    server=instances[0]
    try:
        conn=http.client.HTTPConnection('127.0.0.1',server.server_port,timeout=3)
        conn.request('GET','/health',headers={'X-API-Key':'fixture-key'});response=conn.getresponse();response.read();conn.close();assert response.status==200
        server.shutdown();worker.join(4)
        assert not worker.is_alive() and not errors
        assert runtime.scheduler.stopped==runtime.watchdog.stopped==runtime.heartbeat.stopped==1
        assert server.socket.fileno()==-1
    finally:
        if worker.is_alive():server.shutdown()
        server.server_close();worker.join(4)
