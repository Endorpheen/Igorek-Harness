"""Private terminal IPC uses owned Unix sockets and a fake VM manager."""
import json
from pathlib import Path
import socket
import stat
import sys
import tempfile
import threading
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from factory.core.isolation import terminal_owner as t
from factory.core.isolation.terminal_manager import TerminalError


pytestmark=pytest.mark.skipif(sys.platform!='linux',reason='Linux Unix sockets and kernel SO_PEERCRED contract')


@pytest.fixture
def owner(monkeypatch):
    with tempfile.TemporaryDirectory(prefix='ig-owner43-') as directory:
        root=Path(directory);sessions=root/'sessions';sessions.mkdir();work=root/'work';work.mkdir()
        monkeypatch.setattr(t,'get_chat_sessions_dir',lambda:str(sessions))
        threads=[]
        def spawn(**kwargs):
            thread=threading.Thread(**kwargs);threads.append(thread);return thread
        monkeypatch.setattr(t,'threading',SimpleNamespace(Event=threading.Event,Thread=spawn))
        manager=Mock();manager.terminal_acquire.return_value={'lease':'owned-lease','cid':42};manager.terminal_renew.return_value=True
        (sessions/'owned-session.jsonl').write_text(json.dumps({'type':'meta','profile':'fixture','cwd':str(work)})+'\n')
        server=t.TerminalOwnerServer('fixture',manager)
        try:yield SimpleNamespace(root=root,sessions=sessions,work=work,manager=manager,server=server)
        finally:
            server.close()
            for thread in threads:thread.join(3);assert not thread.is_alive()


def test_native_owner_open_renew_release_preserve_scope_and_peer_permissions(owner):
    f=owner
    assert stat.S_IMODE(t.owner_path('fixture').stat().st_mode)==0o600
    assert stat.S_IMODE(t.owner_path('fixture').parent.stat().st_mode)==0o700
    assert t.owner_request('fixture',{'op':'open','session_id':'owned-session'})=={'ok':True,'lease':'owned-lease','cid':42}
    spec=f.manager.terminal_acquire.call_args.args[0]
    assert spec.session_id=='owned-session' and str(spec.workspace_cwd)==str(f.work)
    assert t.owner_request('fixture',{'op':'renew','lease':'owned-lease'})=={'ok':True}
    assert t.owner_request('fixture',{'op':'release','lease':'owned-lease'})=={'ok':True}
    f.manager.terminal_renew.assert_called_once_with('owned-lease')
    f.manager.terminal_release.assert_called_once_with('owned-lease')


@pytest.mark.parametrize('payload,reason',[
    ({'op':'open','session_id':'../outside'},'session_id_invalid'),
    ({'op':'open','session_id':''},'session_id_invalid'),
    ({'op':'open','session_id':7},'session_id_invalid'),
    ({'op':'open','session_id':'owned-session','extra':True},'terminal_owner_request_invalid'),
    ({'op':'unknown'},'terminal_owner_request_invalid'),
    ({'op':'renew'},'terminal_owner_request_invalid'),
],ids=['traversal','empty-id','numeric-id','extra-field','unknown-operation','missing-lease'])
def test_invalid_request_never_reaches_vm_manager(owner,payload,reason):
    with pytest.raises(TerminalError,match=reason):t.owner_request('fixture',payload)
    owner.manager.terminal_acquire.assert_not_called()
    owner.manager.terminal_renew.assert_not_called()
    owner.manager.terminal_release.assert_not_called()


@pytest.mark.parametrize('metadata',[{'type':'meta','profile':'different','cwd':'fixture'},{'type':'message','profile':'fixture','cwd':'fixture'}])
def test_session_metadata_cannot_cross_profile_boundary(owner,metadata):
    (owner.sessions/'owned-session.jsonl').write_text(json.dumps(metadata)+'\n')
    with pytest.raises(TerminalError,match='session_profile_mismatch'):t.owner_request('fixture',{'op':'open','session_id':'owned-session'})
    owner.manager.terminal_acquire.assert_not_called()


def test_lost_lease_is_an_explicit_failure(owner):
    owner.manager.terminal_renew.return_value=False
    with pytest.raises(TerminalError,match='terminal_vm_lost'):t.owner_request('fixture',{'op':'renew','lease':'owned-lease'})


def test_manager_refusal_retains_machine_readable_reason(owner):
    owner.manager.terminal_acquire.side_effect=TerminalError('fixture_capacity')
    with pytest.raises(TerminalError,match='fixture_capacity'):t.owner_request('fixture',{'op':'open','session_id':'owned-session'})


def test_missing_owner_is_unavailable_without_creating_a_vm(owner):
    with pytest.raises(TerminalError,match='terminal_owner_unavailable'):t.owner_request('missing-profile',{'op':'open','session_id':'owned-session'})
    owner.manager.terminal_acquire.assert_not_called()


def test_client_rejects_nonprivate_ipc_directory(owner):
    path=t.owner_path('fixture').parent;path.chmod(0o755)
    try:
        with pytest.raises(TerminalError,match='terminal_owner_permissions'):t.owner_request('fixture',{'op':'open','session_id':'owned-session'})
    finally:path.chmod(0o700)
    owner.manager.terminal_acquire.assert_not_called()


def test_duplicate_owner_cannot_replace_live_listener(owner):
    original=t.owner_path('fixture').stat().st_ino
    with pytest.raises(TerminalError,match='terminal_owner_already_running'):t.TerminalOwnerServer('fixture',Mock())
    assert t.owner_path('fixture').stat().st_ino==original
    assert t.owner_request('fixture',{'op':'renew','lease':'owned-lease'})['ok'] is True


def test_close_is_idempotent_and_preserves_replaced_path(owner):
    path=t.owner_path('fixture');path.unlink();path.write_text('owned replacement')
    owner.server.close();owner.server.close()
    assert path.read_text()=='owned replacement'


def test_stale_socket_is_replaced_only_inside_owned_namespace(owner):
    profile='stale-profile';path=t.owner_path(profile)
    stale=socket.socket(socket.AF_UNIX);stale.bind(str(path));stale.close()
    server=t.TerminalOwnerServer(profile,Mock())
    try:assert stat.S_ISSOCK(path.stat().st_mode)
    finally:server.close()
    assert not path.exists()
