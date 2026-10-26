"""Temporary-file and synthetic WinAPI resource contracts; no native ACL changes."""
import ctypes
from ctypes import wintypes as W
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from factory.core.platform_support import windows_sandbox as source
from test_windows_sandbox_process_contracts import WindowsFailure, assign
from test_windows_sandbox_process_contracts import sandbox as sandbox


@pytest.mark.parametrize('kind',['readable','writable','process_adapter'])
def test_constructor_rejects_non_disposable_paths_before_loading_dlls(sandbox,monkeypatch,tmp_path,kind):
    root=tmp_path/'disposable';root.mkdir();outside=tmp_path/'outside';outside.mkdir()
    dll=Mock(side_effect=AssertionError('must validate paths first'))
    monkeypatch.setattr(source.ctypes,'WinDLL',dll,raising=False)
    kwargs={kind:[outside]} if kind!='process_adapter' else {kind:outside}
    with pytest.raises(ValueError,match='must_be_disposable'):
        source.WindowsSandbox(root,**kwargs)
    dll.assert_not_called()


def test_constructor_requires_an_existing_root_before_loading_dlls(sandbox,monkeypatch,tmp_path):
    dll=Mock(side_effect=AssertionError('no directory exists'))
    monkeypatch.setattr(source.ctypes,'WinDLL',dll,raising=False)
    with pytest.raises(FileNotFoundError):source.WindowsSandbox(tmp_path/'missing')
    dll.assert_not_called()


@pytest.mark.parametrize('manual',[False,True])
def test_constructor_orders_token_grants_job_and_desktop(sandbox,monkeypatch,tmp_path,manual):
    root=tmp_path/'root';root.mkdir();read=root/'read';read.mkdir();write=root/'write';write.mkdir()
    project=tmp_path/'project';project.mkdir();adapter=root/'adapter.dll';adapter.write_bytes(b'not a DLL')
    calls=[]
    monkeypatch.setattr(source.ctypes,'WinDLL',lambda *args,**kwargs:SimpleNamespace(),raising=False)
    for name in ['_bind','_token','_transit_grants','_job','_desktop']:
        monkeypatch.setattr(source.WindowsSandbox,name,lambda self,name=name:calls.append((name,)))
    monkeypatch.setattr(source.WindowsSandbox,'_grant',lambda self,path,*,writable:calls.append(('grant',path,writable)))
    monkeypatch.setattr(source.WindowsSandbox,'_manual_grant',lambda self,path:calls.append(('manual',path)))
    instance=source.WindowsSandbox(root,readable=[read],writable=[write],manual_projects=[project] if manual else [],process_adapter=adapter)
    assert calls[:3]==[('_bind',),('_token',),('_transit_grants',)]
    assert calls[3:6]==[('grant',root,False),('grant',read,False),('grant',write,True)]
    assert calls[-2:]==[('_job',),('_desktop',)]
    assert ('manual',project) in calls if manual else not any(call[0]=='manual' for call in calls)
    assert instance.adapter_path==adapter
    assert instance.capability.startswith('S-1-5-21-')


@pytest.mark.parametrize('stage',['_token','_transit_grants','_grant','_manual_grant','_job','_desktop'])
def test_partial_constructor_failure_invokes_cleanup(sandbox,monkeypatch,tmp_path,stage):
    root=tmp_path/'root';root.mkdir();project=tmp_path/'project';project.mkdir()
    monkeypatch.setattr(source.ctypes,'WinDLL',lambda *args,**kwargs:SimpleNamespace(),raising=False)
    def fail(*args,**kwargs):raise WindowsFailure('synthetic constructor failure')
    for name in ['_bind','_token','_transit_grants','_grant','_manual_grant','_job','_desktop']:
        monkeypatch.setattr(source.WindowsSandbox,name,fail if name==stage else lambda *args,**kwargs:None)
    cleanup=Mock();monkeypatch.setattr(source.WindowsSandbox,'close',cleanup)
    with pytest.raises(WindowsFailure):source.WindowsSandbox(root,manual_projects=[project])
    cleanup.assert_called_once()


def test_binding_declares_process_api_signatures_without_executing_native_calls(sandbox):
    class Library:
        def __init__(self):self.functions={}
        def __getattr__(self,name):return self.functions.setdefault(name,Mock())
    sandbox.kernel=Library();sandbox.security=Library();sandbox.user=Library()
    sandbox._bind()
    assert sandbox.create_process is sandbox.security.functions['CreateProcessAsUserW']
    assert sandbox.create_process.restype is W.BOOL
    assert sandbox.create_process.argtypes[-1] is ctypes.POINTER(source.ProcessInfo)
    assert sandbox.attribute_delete.restype is None
    assert sandbox.close_handle is sandbox.kernel.functions['CloseHandle']
    for library in [sandbox.kernel,sandbox.security,sandbox.user]:
        assert library.functions
        for function in library.functions.values():function.assert_not_called()


@pytest.mark.parametrize('helper,api_name',[('_sid','sid_from_text'),('_descriptor','descriptor_from_text')])
def test_converted_allocations_are_registered_for_later_cleanup(sandbox,helper,api_name):
    def convert(*args):assign(args[-1] if helper=='_sid' else args[2],345);return True
    setattr(sandbox,api_name,Mock(side_effect=convert))
    pointer=getattr(source.WindowsSandbox,helper)(sandbox,'synthetic descriptor')
    assert pointer.value==345 and sandbox.allocations==[pointer]


def test_sid_string_is_freed_after_reading(sandbox):
    buffer=ctypes.create_unicode_buffer('S-1-5-fixture')
    def convert(pointer,out):
        ctypes.cast(out,ctypes.POINTER(W.LPWSTR))[0]=ctypes.cast(buffer,W.LPWSTR)
        return True
    sandbox.sid_to_text=Mock(side_effect=convert)
    assert sandbox._sid_text(ctypes.c_void_p(22))=='S-1-5-fixture'
    sandbox.local_free.assert_called_once()


@pytest.mark.parametrize('label',[False,True])
@pytest.mark.parametrize('present,pointer,expected',[(True,44,True),(False,44,False),(True,0,False)])
def test_descriptor_acl_is_required_for_both_access_and_integrity(sandbox,label,present,pointer,expected):
    def fill(descriptor,out_present,out_acl,out_inherited):
        assign(out_present,present);assign(out_acl,pointer);assign(out_inherited,False);return True
    sandbox.get_dacl=Mock(side_effect=fill);sandbox.get_sacl=Mock(side_effect=fill)
    if expected:
        assert source.WindowsSandbox._acl(sandbox,ctypes.c_void_p(33),label=label).value==44
    else:
        with pytest.raises(ValueError,match='acl_required'):source.WindowsSandbox._acl(sandbox,ctypes.c_void_p(33),label=label)
    assert (sandbox.get_sacl if label else sandbox.get_dacl).call_count==1


@pytest.mark.parametrize('writable',[False,True])
def test_disposable_tree_receives_requested_grants_without_real_acl_operations(sandbox,writable):
    root=sandbox.root/'folder';root.mkdir();file=root/'file';file.write_text('fixture')
    sandbox.set_named_security=Mock(return_value=0)
    sandbox._grant(root,writable=writable)
    calls=sandbox.set_named_security.call_args_list
    assert {call.args[0] for call in calls}=={str(root),str(file)}
    assert len(calls)==2*int(1+writable)
    if writable:assert any(call.args[2]==0x10 for call in calls)
    assert file.read_text()=='fixture'


def test_write_grant_cannot_escape_disposable_root(sandbox,tmp_path):
    outside=tmp_path.parent/'not-owned'
    outside.mkdir(exist_ok=True)
    sandbox.set_named_security=Mock(side_effect=AssertionError('no ACL operation expected'))
    with pytest.raises(ValueError,match='write_root_escape'):sandbox._grant(outside,writable=True)
    sandbox.set_named_security.assert_not_called()


@pytest.mark.parametrize('call_number',[1,2])
def test_grant_api_failure_is_visible(sandbox,call_number):
    values=iter([0]*(call_number-1)+[5])
    sandbox.set_named_security=Mock(side_effect=lambda *args:next(values))
    with pytest.raises(WindowsFailure):sandbox._grant(sandbox.root,writable=True)


def test_tree_walk_preserves_files_and_nested_directories(sandbox):
    sub=sandbox.root/'sub';sub.mkdir();file=sub/'file';file.write_text('fixture')
    assert set(sandbox._paths(sandbox.root))=={sandbox.root,sub,file}


def test_file_symlink_inside_tree_is_resolved_but_outside_link_is_rejected(sandbox,tmp_path):
    inside=sandbox.root/'file';inside.write_text('fixture')
    link=sandbox.root/'link';link.symlink_to(inside)
    entries = list(sandbox._paths(sandbox.root))
    assert inside in entries and link not in entries
    link.unlink()
    outside=tmp_path.parent/'outside-file';outside.write_text('untouched')
    link.symlink_to(outside)
    with pytest.raises(ValueError,match='reparse_point'):list(sandbox._paths(sandbox.root))
    assert link not in list(sandbox._paths(sandbox.root,ignore_reparse=True))
    assert outside.read_text()=='untouched'


def test_directory_symlink_is_not_followed_during_grants(sandbox):
    sub=sandbox.root/'sub';sub.mkdir();link=sandbox.root/'link';link.symlink_to(sub,target_is_directory=True)
    with pytest.raises(ValueError,match='reparse_point'):list(sandbox._paths(sandbox.root))
    assert set(sandbox._paths(sandbox.root,ignore_reparse=True))=={sandbox.root,sub}


def test_transit_grants_are_deduplicated_and_recorded_for_revocation(sandbox):
    sandbox.manual_projects=[sandbox.root/'project']
    attempts={}
    def metadata(path):attempts[path]=attempts.get(path,0)+1;return attempts[path]>1
    sandbox._metadata_access=Mock(side_effect=metadata)
    sandbox._transit_grants()
    expected=list(dict.fromkeys(path for root in [sandbox.root,*sandbox.manual_projects] for path in reversed(root.parents)))
    assert sandbox.transit==expected
    assert [call.args[0] for call in sandbox._path_grant.call_args_list]==expected


def test_failed_metadata_after_transit_grant_is_not_silently_accepted(sandbox):
    sandbox._metadata_access=Mock(return_value=False)
    with pytest.raises(PermissionError,match='ancestor_metadata_denied'):sandbox._transit_grants()
    assert len(sandbox.transit)==1


def test_existing_metadata_access_needs_no_transit_grants(sandbox):
    sandbox._metadata_access=Mock(return_value=True)
    sandbox._transit_grants()
    sandbox._path_grant.assert_not_called();assert sandbox.transit==[]
