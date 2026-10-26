"""Installed CLI launcher parsing and profile selection without starting a worker."""
import json
from unittest.mock import Mock

import pytest

from factory import cli_launch
from factory.core import cli_bootstrap, cli_chat, cli_chat_rich, cli_run, cli_session_runtime, cli_daemon
from factory.core import runtime as config


@pytest.fixture
def launcher(tmp_path,monkeypatch):
    root=tmp_path/'runtime';root.mkdir()
    sessions=root/'sessions';sessions.mkdir()
    monkeypatch.setenv('IGOREK_FACTORY_HOME',str(root));monkeypatch.setenv('FACTORY_DATA_DIR',str(root))
    names=['one','two'];selections=[];closed=[]
    class SessionRuntime:
        def __enter__(self):return self
        def __exit__(self,*args):closed.append(True)
        def select(self,name):selections.append(name)
        def submit(self,**kwargs):raise AssertionError('Unexpected task creation')
    chat=Mock(return_value=7)
    monkeypatch.setattr(cli_session_runtime,'SessionRuntime',SessionRuntime)
    monkeypatch.setattr(cli_chat_rich,'run_rich_chat',chat)
    monkeypatch.setattr(cli_run,'_bootstrap_stack_env',lambda:None)
    monkeypatch.setattr(config,'list_profile_names',lambda *args:list(names))
    monkeypatch.setattr(config,'get_profiles_dir',lambda:str(root/'profiles'))
    monkeypatch.setattr(config,'get_profile_config',lambda name:{'model':'fixture'})
    monkeypatch.setattr(cli_chat,'generate_session_name',lambda:'fixture-session')
    monkeypatch.setattr(cli_chat,'_session_path',lambda name:sessions/(name+'.jsonl'))
    monkeypatch.setattr(cli_launch.sys.stdin,'isatty',lambda:False)
    monkeypatch.setattr(cli_bootstrap,'_prompt_choice',lambda *args,**kwargs:'two')
    def initialize(**kwargs):
        names[:]=['one']
        return {'profile_names':['one']}
    monkeypatch.setattr(cli_bootstrap,'init_cli_environment',Mock(side_effect=initialize))
    return root,sessions,names,selections,closed,chat


def test_explicit_profile_launch_persists_selection_and_passes_runtime_callbacks(launcher):
    root,_,_,selected,closed,chat=launcher
    assert cli_launch.main(['--profile','two','--no-alt-screen'])==7
    assert selected==['two'] and closed==[True]
    args=chat.call_args.kwargs
    assert args['profile']=='two' and args['session']=='fixture-session' and args['data_dir']==str(root)
    assert args['start_fullscreen'] is False and args['exit_on_interrupt'] and callable(args['submit_task'])
    assert json.loads((root/'run/cli/selection.json').read_text())=={'profile':'two'}


def test_last_profile_is_reused_but_removed_profile_is_not_silently_selected(launcher,capsys):
    root,_,names,selected,_,chat=launcher
    directory=root/'run/cli';directory.mkdir(parents=True,mode=0o700);(directory/'selection.json').write_text('{"profile":"two"}')
    assert cli_launch.main([])==7 and selected==['two']
    names.remove('two');names.append('three');chat.reset_mock()
    assert cli_launch.main([])==2 and not chat.called
    assert '--profile' in capsys.readouterr().err


def test_single_configured_profile_needs_no_interactive_prompt(launcher):
    _,_,names,selected,_,_=launcher;names[:]=['one']
    assert cli_launch.main([])==7 and selected==['one']


def test_multiple_profiles_use_interactive_choice_only_on_a_tty(launcher,monkeypatch):
    _,_,_,selected,_,_=launcher
    monkeypatch.setattr(cli_launch.sys.stdin,'isatty',lambda:True)
    assert cli_launch.main([])==7 and selected==['two']


@pytest.mark.parametrize('tty',[False,True])
def test_empty_configuration_initializes_only_in_a_tty(launcher,monkeypatch,tty,capsys):
    _,_,names,_,_,chat=launcher;names.clear()
    monkeypatch.setattr(cli_launch.sys.stdin,'isatty',lambda:tty)
    result=cli_launch.main([])
    if tty:
        assert result==7 and cli_bootstrap.init_cli_environment.call_count==1
    else:
        assert result==2 and not chat.called and 'init' in capsys.readouterr().err
        assert not cli_bootstrap.init_cli_environment.called


@pytest.mark.parametrize('profile',['missing','../outside','bad profile'])
def test_invalid_profile_never_reaches_chat_and_context_is_closed(launcher,profile,capsys):
    _,_,_,selected,closed,chat=launcher
    assert cli_launch.main(['--profile',profile])==2
    assert selected==[] and closed==[True] and not chat.called
    assert 'Unknown configured profile' in capsys.readouterr().err


def test_profile_change_callback_validates_and_persists_new_selection(launcher):
    root,_,_,selected,_,chat=launcher
    def run(**kwargs):
        kwargs['on_profile_change']('two')
        with pytest.raises(ValueError):kwargs['on_profile_change']('outside')
        return 0
    chat.side_effect=run
    assert cli_launch.main(['--profile','one'])==0
    assert selected==['one','two'] and json.loads((root/'run/cli/selection.json').read_text())['profile']=='two'


def test_resume_existing_session_uses_its_saved_profile(launcher,monkeypatch):
    _,sessions,_,selected,_,chat=launcher
    (sessions/'saved.jsonl').write_text('fixture')
    monkeypatch.setattr(cli_chat,'_read_session_meta',lambda path:{'profile':'two'})
    assert cli_launch.main(['--resume','saved'])==7
    assert selected==['two'] and chat.call_args.kwargs['session']=='saved'


@pytest.mark.parametrize('extra',[['--resume'],['--resume','missing']])
def test_resume_requires_an_existing_id_and_does_not_create_a_session(launcher,extra,capsys):
    _,_,_,selected,_,chat=launcher
    assert cli_launch.main(['--profile','one',*extra])==2
    assert not chat.called and selected==[] and 'existing session ID' in capsys.readouterr().err


@pytest.mark.parametrize('action',['start','stop','restart','version'])
def test_daemon_commands_forward_without_opening_chat(launcher,monkeypatch,action,capsys):
    _,_,_,selected,_,chat=launcher
    call=Mock(return_value={'operation':action});monkeypatch.setattr(cli_daemon,'lifecycle',call)
    assert cli_launch.main(['app-server','daemon',action])==0
    call.assert_called_once_with(action)
    assert json.loads(capsys.readouterr().out)=={'operation':action} and not chat.called and selected==[]


@pytest.mark.parametrize('args',[['app-server','daemon'],['app-server','daemon','invalid']])
def test_bad_daemon_command_returns_usage_without_dispatch(launcher,args,capsys):
    assert cli_launch.main(args)==2 and 'Usage:' in capsys.readouterr().err


@pytest.mark.parametrize('failure,code',[(KeyboardInterrupt(),130),(RuntimeError('fixture unavailable'),2),(OSError('fixture IO'),2)])
def test_chat_failure_returns_conventional_exit_code_and_closes_runtime(launcher,failure,code):
    _,_,_,_,closed,chat=launcher;chat.side_effect=failure
    assert cli_launch.main(['--profile','one'])==code and closed==[True]
