"""Native owned Unix runtime loop with synthetic scheduler/watchdog boundaries."""
import os
import threading
import time
from unittest.mock import Mock

import pytest

from factory.core import cli_daemon as runtime
from factory.core import scheduler, mcp_bridge
from factory import factory_api
from test_cli_daemon_contracts import local as local


@pytest.fixture
def services(local,monkeypatch):
    records=[]
    class Scheduler:
        running=False
        def __init__(self,*args):records.append('scheduler-created')
        def set_task_callback(self,callback):self.callback=callback
        def set_readiness_check(self,callback):self.ready=callback
        def start(self):self.running=True;records.append('scheduler-started')
        def stop(self):self.running=False;records.append('scheduler-stopped')
    class Watchdog:
        def __init__(self,*args):records.append('watchdog-created')
        def start(self):records.append('watchdog-started')
        def stop(self):records.append('watchdog-stopped')
    monkeypatch.setattr(scheduler,'TaskScheduler',Scheduler)
    monkeypatch.setattr(factory_api,'TaskWatchdog',Watchdog)
    shutdown=Mock();monkeypatch.setattr(mcp_bridge,'shutdown_mcp_servers',shutdown)
    from factory.core import cli_run
    monkeypatch.setattr(cli_run,'_bootstrap_stack_env',lambda:None)
    monkeypatch.setattr(runtime.signal,'signal',lambda *args:records.append('signal-installed'))
    return records,shutdown


@pytest.mark.skipif(os.name!='posix',reason='Native POSIX Unix socket shared-runtime lifecycle')
def test_real_service_loop_starts_and_stops_only_its_owned_socket_and_monitor_stubs(local,services):
    records,shutdown=services
    daemon=runtime.Daemon();observed=[];errors=[]
    endpoint=runtime.directory()/'endpoint.json'
    def client():
        try:
            deadline=time.monotonic()+4
            while not endpoint.exists():
                if time.monotonic()>deadline:raise AssertionError('Owned endpoint did not appear')
                time.sleep(0.01)
            observed.append(runtime.request('version'))
            observed.append(runtime.request('stop'))
        except BaseException as exc:errors.append(exc);daemon.stop.set()
    thread=threading.Thread(target=client);thread.start()
    try:daemon.serve()
    finally:
        daemon.stop.set();thread.join(5)
    assert not errors and not thread.is_alive()
    assert observed[0]['running'] and observed[0]['scheduler'] and observed[1]=={'stopping':True}
    assert not endpoint.exists() and not daemon.workers
    assert records.count('scheduler-started')==records.count('scheduler-stopped')==1
    assert records.count('watchdog-started')==records.count('watchdog-stopped')==1
    assert records.count('signal-installed')==2 and shutdown.call_count==1


@pytest.mark.skipif(os.name!='posix',reason='POSIX runtime lock acquisition')
def test_busy_instance_lock_never_starts_or_stops_other_monitors(local,services,monkeypatch):
    records,shutdown=services
    monkeypatch.setattr(runtime,'exclusive_lock',Mock(side_effect=runtime.LockBusyError('fixture busy')))
    daemon=runtime.Daemon();daemon.serve()
    assert records==[] and not shutdown.called and not daemon.stop.is_set()


@pytest.mark.skipif(os.name!='posix',reason='POSIX runtime cleanup after startup failure')
def test_startup_failure_removes_own_endpoint_and_releases_instance_lock(local,services,monkeypatch):
    records,shutdown=services
    original=scheduler.TaskScheduler
    class Broken(original):
        def start(self):raise RuntimeError('fixture scheduler failed')
    monkeypatch.setattr(scheduler,'TaskScheduler',Broken)
    daemon=runtime.Daemon()
    with pytest.raises(RuntimeError,match='scheduler failed'):daemon.serve()
    assert daemon.stop.is_set() and not (runtime.directory()/'endpoint.json').exists()
    assert 'scheduler-stopped' in records and shutdown.call_count==1
    descriptor=runtime.exclusive_lock(runtime.directory()/'instance.lock')
    os.close(descriptor)
