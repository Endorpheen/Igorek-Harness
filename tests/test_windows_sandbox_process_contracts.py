"""Portable Windows process orchestration with explicitly synthetic API boundaries.

These checks do not replace the existing native Windows restricted-token tests.
"""
import ctypes
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from factory.core.platform_support import windows_sandbox as source


class WindowsFailure(OSError):
    pass


def assign(pointer, value):
    pointer._obj.value = value


@pytest.fixture
def sandbox(tmp_path,monkeypatch):
    instance=source.WindowsSandbox.__new__(source.WindowsSandbox)
    instance.root=tmp_path
    instance.readable=[];instance.writable=[];instance.manual_projects=[]
    instance.adapter_path=None;instance.handles=[];instance.allocations=[];instance.transit=[]
    instance.process=instance.thread=instance.desktop=instance.job=None
    instance.token=111;instance.owner='fixture-owner';instance.capability='fixture-capability';instance.logon='fixture-logon';instance.desktop_name='fixture-desktop'
    functions=['close_handle','local_free','terminate_job','wait','terminate_process','assign_job','set_job','attribute_update','attribute_delete','close_desktop','impersonate','revert']
    for name in functions:setattr(instance,name,Mock(return_value=1))
    instance.set_security=Mock(return_value=0)
    instance.resume=Mock(return_value=1)
    instance._descriptor=Mock(side_effect=lambda text:ctypes.c_void_p(303))
    instance._acl=Mock(return_value=ctypes.c_void_p(404))
    instance._sid=Mock(return_value=ctypes.c_void_p(505))
    instance._manual_grant=Mock();instance._path_grant=Mock()
    def initialize(storage,count,flags,size):
        assign(size,128)
        return bool(storage is not None)
    instance.attribute_init=Mock(side_effect=initialize)
    def create(*args):
        process=args[-1]._obj
        process.process=701;process.thread=702;process.pid=8001;process.tid=8002
        return True
    instance.create_process=Mock(side_effect=create)
    inherit=[]
    monkeypatch.setattr(source,'os',SimpleNamespace(name='nt',pathsep=os.pathsep,set_handle_inheritable=lambda handle,value:inherit.append((handle,value))))
    monkeypatch.setattr(source.ctypes,'WinError',lambda code:WindowsFailure(code),raising=False)
    monkeypatch.setattr(source.ctypes,'get_last_error',lambda:123,raising=False)
    monkeypatch.setitem(sys.modules,'msvcrt',SimpleNamespace(get_osfhandle=lambda descriptor:descriptor+1000))
    instance._inherit_events=inherit
    return instance


def streams(*descriptors):
    return [SimpleNamespace(fileno=lambda value=value:value) for value in descriptors]


def start(sandbox,*,env=None,descriptors=(10,11,12)):
    return sandbox.start(['fixture.exe','argument with spaces','plain'],cwd=sandbox.root,
        env={'Z':'last','a':'first'} if env is None else env,stdin=streams(*descriptors)[0],stdout=streams(*descriptors)[1],stderr=streams(*descriptors)[2])


def test_start_assigns_job_before_resuming_and_uses_only_explicit_stdio(sandbox):
    calls=[]
    sandbox.assign_job.side_effect=lambda *args:calls.append('assign') or True
    sandbox.resume.side_effect=lambda *args:calls.append('resume') or 1
    assert start(sandbox,descriptors=(10,11,11))==8001
    assert calls==['assign','resume']
    assert sandbox._inherit_events==[(1010,True),(1011,True),(1010,False),(1011,False)]
    assert sandbox.handles==[701,702]
    args=sandbox.create_process.call_args.args
    assert args[1]=='fixture.exe'
    assert args[2].value==subprocess.list2cmdline(['fixture.exe','argument with spaces','plain'])
    assert args[5] is True and args[8]==str(sandbox.root)
    startup=args[-2]._obj.startup
    assert (startup.stdin,startup.stdout,startup.stderr)==(1010,1011,1011)
    assert startup.desktop=='winsta0\\fixture-desktop'
    assert sandbox.attribute_delete.call_count==1


def test_start_builds_a_sorted_environment_without_mutating_caller(sandbox):
    env={'Z':'last','a':'first'}
    assert start(sandbox,env=env)==8001
    block=sandbox.create_process.call_args.args[7]
    assert ''.join(block).startswith('a=first\0Z=last\0\0')
    assert env=={'Z':'last','a':'first'}


@pytest.mark.parametrize('failure',['attributes','create','security','job','limits','resume'])
def test_start_failure_restores_inheritance_and_terminates_created_child(sandbox,failure):
    if failure=='attributes':sandbox.attribute_update.return_value=False
    elif failure=='create':sandbox.create_process.side_effect=None;sandbox.create_process.return_value=False
    elif failure=='security':sandbox.set_security.return_value=5
    elif failure=='job':sandbox.assign_job.return_value=False
    elif failure=='limits':sandbox.set_job.return_value=False
    else:sandbox.resume.return_value=0xFFFFFFFF
    with pytest.raises(WindowsFailure):start(sandbox)
    assert sandbox._inherit_events==[(1010,True),(1011,True),(1012,True),(1010,False),(1011,False),(1012,False)]
    assert sandbox.attribute_delete.call_count==1
    assert sandbox.terminate_process.call_count==int(failure not in {'attributes','create'})
    assert sandbox.resume.call_count==int(failure=='resume')


@pytest.mark.parametrize('failure',['initialize','sizing','inherit'])
def test_prelaunch_failure_restores_every_handle_already_made_inheritable(sandbox,monkeypatch,failure):
    if failure=='initialize':
        def fail(storage,*args):
            if storage is not None:return False
            assign(args[-1],128);return False
        sandbox.attribute_init.side_effect=fail
    elif failure=='sizing':
        sandbox.attribute_init.side_effect=WindowsFailure('sizing failure')
    else:
        def fail(handle,value):
            if handle==1011 and value:raise WindowsFailure('inheritable flag failure')
            sandbox._inherit_events.append((handle,value))
        monkeypatch.setattr(source.os,'set_handle_inheritable',fail)
    with pytest.raises(WindowsFailure):start(sandbox)
    enabled={handle for handle,value in sandbox._inherit_events if value}
    disabled={handle for handle,value in sandbox._inherit_events if not value}
    assert enabled==disabled
    sandbox.create_process.assert_not_called()
    sandbox.terminate_process.assert_not_called()


@pytest.mark.parametrize('value,expected',[(259,None),(0,0),(7,7)])
def test_poll_reports_running_and_actual_exit_status(sandbox,value,expected):
    sandbox.process=701
    def report(handle,pointer):assign(pointer,value);return True
    sandbox.exit_code=Mock(side_effect=report)
    assert sandbox.poll()==expected
    sandbox.exit_code.assert_called_once()


def test_poll_before_start_does_not_query_a_process(sandbox):
    sandbox.exit_code=Mock(side_effect=AssertionError('no process exists'))
    assert sandbox.poll() is None


def test_poll_api_failure_remains_visible(sandbox):
    sandbox.process=701;sandbox.exit_code=Mock(return_value=False)
    with pytest.raises(WindowsFailure):sandbox.poll()


def test_close_reaps_process_reverses_grants_and_frees_owned_resources(sandbox):
    sandbox.job=501;sandbox.process=701;sandbox.thread=702;sandbox.desktop=901
    sandbox.handles=[111,501,701,702];sandbox.allocations=[ctypes.c_void_p(81),ctypes.c_void_p(82)]
    sandbox.transit=[Path('/fixture/ancestor'),Path('/fixture/ancestor/parent')]
    sandbox.manual_projects=[Path('/fixture/manual')]
    sandbox.close()
    sandbox.terminate_job.assert_called_once_with(501,1)
    sandbox.wait.assert_called_once_with(701,5000)
    assert [call.args[0] for call in sandbox.close_handle.call_args_list]==[702,701,501,111]
    assert [call.args[0].value for call in sandbox.local_free.call_args_list]==[82,81]
    assert [call.args[0] for call in sandbox._path_grant.call_args_list]==[Path('/fixture/ancestor/parent'),Path('/fixture/ancestor')]
    assert all(call.kwargs['revoke'] is True for call in sandbox._path_grant.call_args_list)
    sandbox._manual_grant.assert_called_once_with(Path('/fixture/manual'),revoke=True)
    sandbox.close_desktop.assert_called_once_with(901)
    assert sandbox.handles==sandbox.allocations==sandbox.transit==[]
    assert sandbox.job is sandbox.process is sandbox.thread is sandbox.desktop is None
    sandbox.close()
    assert sandbox.close_handle.call_count==4 and sandbox.local_free.call_count==2


@pytest.mark.parametrize('result,expected',[(40,True),(ctypes.c_void_p(-1).value,False)])
def test_metadata_check_always_reverts_impersonation(sandbox,monkeypatch,result,expected):
    monkeypatch.setattr(source.ctypes,'get_last_error',lambda:5)
    sandbox.open_file=Mock(return_value=result)
    assert sandbox._metadata_access(sandbox.root) is expected
    sandbox.impersonate.assert_called_once_with(111)
    sandbox.revert.assert_called_once()
    assert sandbox.close_handle.call_count==int(expected)


def test_metadata_unexpected_open_failure_reverts_before_propagation(sandbox,monkeypatch):
    sandbox.open_file=Mock(return_value=ctypes.c_void_p(-1).value)
    monkeypatch.setattr(source.ctypes,'get_last_error',lambda:32)
    with pytest.raises(WindowsFailure):sandbox._metadata_access(sandbox.root)
    sandbox.revert.assert_called_once()


def test_metadata_access_denied_reverts_without_closing_invalid_handle(sandbox,monkeypatch):
    sandbox.open_file=Mock(return_value=ctypes.c_void_p(-1).value)
    monkeypatch.setattr(source.ctypes,'get_last_error',lambda:5)
    assert sandbox._metadata_access(sandbox.root) is False
    sandbox.revert.assert_called_once();sandbox.close_handle.assert_not_called()


def test_job_sets_kill_on_close_and_keeps_handle_for_cleanup(sandbox):
    sandbox.create_job=Mock(return_value=501)
    sandbox._job()
    assert sandbox.job==501 and sandbox.handles==[501]
    assert sandbox.set_job.call_args.args[2]._obj.basic.flags & 0x2000


@pytest.mark.parametrize('failed',['create','configure'])
def test_job_api_failure_is_not_accepted(sandbox,failed):
    sandbox.create_job=Mock(return_value=0 if failed=='create' else 501)
    if failed=='configure':sandbox.set_job.return_value=False
    with pytest.raises(WindowsFailure):sandbox._job()
    if failed=='configure':assert sandbox.handles==[501]


def test_private_desktop_uses_own_descriptor_and_is_labeled(sandbox):
    sandbox.create_desktop=Mock(return_value=901)
    sandbox._desktop()
    assert sandbox.desktop==901 and sandbox.desktop_name.startswith('igorek-')
    args=sandbox.create_desktop.call_args.args
    assert args[-1]._obj.inherit is False or args[-1]._obj.inherit==0
    assert sandbox.set_security.call_args.args[0]==901


def test_desktop_label_failure_is_not_accepted(sandbox):
    sandbox.create_desktop=Mock(return_value=901);sandbox.set_security.return_value=5
    with pytest.raises(WindowsFailure):sandbox._desktop()


@pytest.mark.parametrize('mode',['success','embed-failure','load-failure'])
def test_optional_process_adapter_is_scoped_and_unloaded(sandbox,monkeypatch,mode):
    import _ctypes
    sandbox.adapter_path=sandbox.root/'adapter.dll'
    adapter=SimpleNamespace(_handle=990,IgorekEmbed=Mock(return_value=mode=='success'))
    def load(path,**kwargs):
        assert path==str(sandbox.adapter_path)
        if mode=='load-failure':raise WindowsFailure('synthetic DLL load failure')
        return adapter
    monkeypatch.setattr(source.ctypes,'WinDLL',load,raising=False)
    free=Mock();monkeypatch.setattr(_ctypes,'FreeLibrary',free,raising=False)
    env={'PATH':'original-path','Z':'last'}
    if mode=='success':assert start(sandbox,env=env)==8001
    else:
        with pytest.raises(WindowsFailure):start(sandbox,env=env)
        sandbox.terminate_process.assert_called_once_with(701,1)
    assert env=={'PATH':'original-path','Z':'last'}
    assert free.call_count==int(mode!='load-failure')
    assert sandbox._inherit_events[-3:]==[(1010,False),(1011,False),(1012,False)]
    if mode!='load-failure':adapter.IgorekEmbed.assert_called_once_with(701)
    if mode=='success':
        block=''.join(sandbox.create_process.call_args.args[7])
        assert 'PATH='+str(sandbox.adapter_path.parent)+os.pathsep+'original-path\0' in block
