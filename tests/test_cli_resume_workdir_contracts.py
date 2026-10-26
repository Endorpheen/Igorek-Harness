"""Actual CLI resume/fork dispatch, owned directories and synthetic chat boundary."""
import importlib
from pathlib import Path
import sys
from types import ModuleType,SimpleNamespace
from unittest.mock import Mock

import pytest
from prompt_toolkit.application import create_app_session
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput

from factory import cli

pytestmark=pytest.mark.timeout(6)


@pytest.fixture
def entry(tmp_path,monkeypatch):
    current=tmp_path/'current';saved=tmp_path/'saved';current.mkdir();saved.mkdir()
    monkeypatch.chdir(current)
    for name in ['FACTORY_DATA_DIR','IGOREK_FACTORY_HOME']:monkeypatch.setenv(name,str(tmp_path/'runtime'))
    chat=importlib.import_module('core.cli_chat');picker=importlib.import_module('core.tui.resume_picker')
    choice={'session_id':'saved-fixture','cwd':str(saved)}
    catalog=Mock(return_value=[choice]);monkeypatch.setattr(chat,'list_sessions',catalog)
    select=Mock(side_effect=lambda sessions,**kwargs:(picker.SessionSelection.RESUME,sessions[0]))
    monkeypatch.setattr(picker,'run_resume_picker',select)
    rich=ModuleType('core.cli_chat_rich');calls=[]
    def run(**kwargs):calls.append({'kwargs':kwargs,'cwd':Path.cwd()});return 17
    rich.run_rich_chat=run;monkeypatch.setitem(sys.modules,'core.cli_chat_rich',rich)
    monkeypatch.setattr(cli,'generate_session_name',lambda:'new-fixture')
    def invoke(*args,keys=''):
        monkeypatch.setattr(sys,'argv',['igorek-factory','chat','--profile','fixture','--data-dir',str(tmp_path/'runtime'),*args])
        with create_pipe_input() as pipe,create_app_session(input=pipe,output=DummyOutput()):
            if keys:pipe.send_text(keys)
            return cli.main()
    return SimpleNamespace(invoke=invoke,current=current,saved=saved,choice=choice,catalog=catalog,select=select,calls=calls,chat=chat,picker=picker)


@pytest.mark.parametrize('keys,selected',[('\r','saved'),('2','current'),('\x1b[B\r','current'),('\x1b','saved')])
def test_resume_picker_with_different_directory_reaches_chat_in_selected_folder(entry,keys,selected):
    assert entry.invoke('--resume',keys=keys)==17
    assert len(entry.calls)==1 and entry.calls[0]['cwd']==getattr(entry,selected)
    assert entry.calls[0]['kwargs']['session']=='saved-fixture'


@pytest.mark.parametrize('keys',['\x03','\x04'])
def test_cancel_workdir_prompt_does_not_start_chat_or_change_directory(entry,keys):
    assert entry.invoke('--resume',keys=keys)==0
    assert entry.calls==[] and Path.cwd()==entry.current


def test_same_saved_directory_resumes_without_a_second_prompt(entry):
    entry.choice['cwd']=str(entry.current)
    assert entry.invoke('--resume')==17
    assert entry.calls[0]['cwd']==entry.current


def test_missing_saved_directory_keeps_current_folder_and_explains_it(entry,capsys):
    entry.choice['cwd']=str(entry.saved/'missing')
    assert entry.invoke('--resume',keys='\r')==17
    assert entry.calls[0]['cwd']==entry.current
    assert 'Session directory not found' in capsys.readouterr().out


def test_explicit_session_id_keeps_current_directory_and_skips_picker(entry):
    assert entry.invoke('--resume','explicit-fixture')==17
    assert entry.calls[0]['kwargs']['session']=='explicit-fixture'
    assert entry.calls[0]['cwd']==entry.current
    entry.select.assert_not_called()


@pytest.mark.parametrize('chosen',[None,{'session_id':'saved-fixture','cwd':'ignored'}])
def test_start_fresh_picker_choice_keeps_current_folder(entry,chosen):
    entry.select.side_effect=None;entry.select.return_value=(entry.picker.SessionSelection.START_FRESH,chosen)
    assert entry.invoke('--resume')==17
    assert entry.calls[0]['kwargs']['session']=='new-fixture' and entry.calls[0]['cwd']==entry.current


def test_empty_resume_catalog_starts_new_chat_without_picker(entry):
    entry.catalog.return_value=[]
    assert entry.invoke('--resume')==17
    entry.select.assert_not_called()
    assert entry.calls[0]['kwargs']['session']=='new-fixture'


def test_exit_from_resume_picker_does_not_start_chat(entry):
    entry.select.side_effect=None;entry.select.return_value=(entry.picker.SessionSelection.EXIT,None)
    assert entry.invoke('--resume')==0 and not entry.calls


def test_fork_keeps_existing_dispatch_and_does_not_switch_workdir(entry,monkeypatch):
    entry.select.side_effect=None;entry.select.return_value=(entry.picker.SessionSelection.FORK,entry.choice)
    source=entry.current/'source.jsonl'
    monkeypatch.setattr(entry.chat,'_session_path',lambda identity:source)
    fork=Mock(return_value='forked-fixture');monkeypatch.setattr(entry.chat,'fork_session',fork)
    assert entry.invoke('--fork')==17
    fork.assert_called_once_with(source)
    assert entry.calls[0]['kwargs']['session']=='forked-fixture' and entry.calls[0]['cwd']==entry.current


def test_fork_without_sessions_is_a_clear_non_success(entry,capsys):
    entry.catalog.return_value=[]
    assert entry.invoke('--fork')==1 and not entry.calls
    assert 'No sessions found to fork' in capsys.readouterr().out
