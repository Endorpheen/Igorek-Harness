"""Installer fallback dialog/control contracts without GUI, privilege or installation."""
import subprocess
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from test_desktop_linux_package import gui


@pytest.fixture(autouse=True)
def no_real_process(monkeypatch):
    monkeypatch.setattr(gui.subprocess,'run',Mock(side_effect=AssertionError('no actual installer/helper')))
    monkeypatch.setattr(gui.subprocess,'Popen',Mock(side_effect=AssertionError('no actual dialog process')))


@pytest.mark.parametrize('code,expected',[(0,'install'),(1,None)])
def test_choose_returns_selected_action_or_cancel_and_tracks_finished(monkeypatch,code,expected):
    command=Mock(return_value=SimpleNamespace(returncode=code,stdout=' install \n'))
    monkeypatch.setattr(gui.subprocess,'run',command)
    dialogs=gui.Dialogs();assert dialogs.choose('Fixture prompt',[('install','Fixture install'),('cancel','Fixture cancel')])==expected
    assert dialogs.finished is (code!=0)
    argv=command.call_args.args[0]
    assert argv[0]=='zenity' and '--no-markup' in argv and '--radiolist' in argv


@pytest.mark.parametrize('code',[2,127])
def test_unavailable_dialog_is_explicit_failure(monkeypatch,code):
    monkeypatch.setattr(gui.subprocess,'run',Mock(return_value=SimpleNamespace(returncode=code,stdout='')))
    with pytest.raises(RuntimeError,match='системное окно'):gui.Dialogs().call('--info')


@pytest.mark.parametrize('code,confirmed',[(0,True),(1,False)])
def test_confirmation_preserves_cancellation(monkeypatch,code,confirmed):
    monkeypatch.setattr(gui.subprocess,'run',Mock(return_value=SimpleNamespace(returncode=code,stdout='')))
    assert gui.Dialogs().confirm('Fixture prompt') is confirmed


@pytest.mark.parametrize('kind',['info','error'])
def test_message_adapter_uses_matching_dialog_type(monkeypatch,kind):
    command=Mock(return_value=SimpleNamespace(returncode=0,stdout=''));monkeypatch.setattr(gui.subprocess,'run',command)
    getattr(gui.Dialogs(),kind)('Fixture message')
    assert '--'+kind in command.call_args.args[0]


@pytest.mark.parametrize('timeout,body_error',[(False,False),(True,False),(False,True)])
def test_progress_always_finishes_or_terminates_owned_dialog(monkeypatch,timeout,body_error):
    peer=Mock()
    if timeout:peer.communicate.side_effect=subprocess.TimeoutExpired('fixture',5)
    launch=Mock(return_value=peer);monkeypatch.setattr(gui.subprocess,'Popen',launch)
    def work():
        with gui.Dialogs().progress('Fixture progress'):
            if body_error:raise ValueError('fixture operation failed')
    if body_error:
        with pytest.raises(ValueError,match='fixture operation'):work()
    else:work()
    peer.communicate.assert_called_once_with('100\n',timeout=5)
    assert peer.terminate.called is timeout
    if timeout:peer.wait.assert_called_once_with(timeout=5)
    assert '--no-cancel' in launch.call_args.args[0]


@pytest.mark.parametrize('exitcode,ready,accepted',[(0,True,True),(0,False,False),(1,False,False)])
def test_sandbox_confirmation_requires_helper_success_and_verified_state(monkeypatch,tmp_path,exitcode,ready,accepted):
    command=Mock(return_value=SimpleNamespace(returncode=exitcode,stdout=''));monkeypatch.setattr(gui.subprocess,'run',command);monkeypatch.setattr(gui,'sandbox_ready',lambda _:ready)
    if accepted:gui.Dialogs().configure_sandbox(tmp_path)
    else:
        with pytest.raises(RuntimeError):gui.Dialogs().configure_sandbox(tmp_path)
    argv=command.call_args.args[0]
    assert argv[0]=='pkexec' and argv[-2:]==['--installation-root',str(tmp_path)]


@pytest.mark.parametrize('rollback,status,code,accepted',[(True,'restored',0,True),(True,'already-restored',0,True),(True,'installed',0,False),(False,'installed',0,True),(False,'already-installed',0,True),(False,'installed',1,False),(False,'unknown',0,False)])
def test_kvm_helper_outcome_is_not_inferred_from_exit_code_alone(monkeypatch,tmp_path,rollback,status,code,accepted):
    command=Mock(return_value=SimpleNamespace(returncode=code,stdout='{"status":"'+status+'"}'));monkeypatch.setattr(gui.subprocess,'run',command)
    if accepted:gui.Dialogs().configure_libvirt(tmp_path,rollback=rollback)
    else:
        with pytest.raises(RuntimeError):gui.Dialogs().configure_libvirt(tmp_path,rollback=rollback)
    argv=command.call_args.args[0]
    assert argv[:3]==['pkexec','/usr/bin/python3','-I'] and argv[4]==('rollback' if rollback else 'install')


@pytest.mark.parametrize('raw',['not-json','[]','null'])
def test_malformed_kvm_helper_reply_is_not_reported_as_configured(monkeypatch,tmp_path,raw):
    monkeypatch.setattr(gui.subprocess,'run',Mock(return_value=SimpleNamespace(returncode=0,stdout=raw)))
    with pytest.raises(RuntimeError):gui.Dialogs().configure_libvirt(tmp_path)


@pytest.mark.parametrize('case',['not-installed','no-management-entry','missing-icon'])
def test_menu_registration_refuses_incomplete_snapshot_without_writing_entry(monkeypatch,tmp_path,case):
    prefix=tmp_path/'prefix';home=tmp_path/'data'
    monkeypatch.setattr(gui.installer,'state',lambda _: {'current':None if case=='not-installed' else 'fixture','previous':None})
    if case=='missing-icon':
        current=prefix/'active/current';current.mkdir(parents=True);(current/'install-gui.sh').write_text('fixture')
    with pytest.raises(ValueError):gui.register_menu(prefix,home)
    assert not (home/'applications/igorek-desktop.desktop').exists()


@pytest.mark.parametrize('action',[None,'unknown'])
def test_cancelled_or_unknown_controller_action_never_installs(monkeypatch,tmp_path,action):
    monkeypatch.setattr(gui.installer._verifier,'verify',lambda _: {'factory_version':'fixture','build_id':'fixture'})
    monkeypatch.setattr(gui.installer,'state',lambda _:{'current':None,'previous':None})
    monkeypatch.setattr(gui.libvirt_fixer,'compatible',lambda _:'missing')
    install=Mock(side_effect=AssertionError('no installation expected'));monkeypatch.setattr(gui.installer,'install',install)
    dialogs=Mock();dialogs.choose.return_value=action
    result=gui.run(tmp_path/'snapshot',tmp_path/'prefix',tmp_path/'data',dialogs)
    assert result==(0 if action is None else 1)
    assert dialogs.error.called is (action=='unknown')
    install.assert_not_called();assert not (tmp_path/'prefix').exists()
