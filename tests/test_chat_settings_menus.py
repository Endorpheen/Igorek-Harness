"""Interactive menu selections persist only to owned settings/skill state."""
import json
from unittest.mock import Mock

import pytest

from test_chat_session_lifecycle_contracts import session  # noqa: F401
from test_cli_skills_contracts import runtime  # noqa: F401
from factory.core import cli_chat_rich as rich,cli_skills,chat_session
from factory.core.status_line import load_status_line_config
from factory.core.cli_chat_terminal import load_terminal_title_config
from factory.core.tui.theme import load_theme_name


@pytest.fixture
def settings(session):
    path=session.sl.settings_path;path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps({'unrelated':{'fixture':'keep'}}))
    return session.console,session.sl,path


@pytest.mark.parametrize('kind',['status','title'])
def test_item_menu_preserves_order_deduplicates_and_keeps_other_settings(settings,kind):
    console,line,path=settings
    setup,catalog,load=(rich._statusline_setup,rich.status_line_item_catalog,load_status_line_config) if kind=='status' else (rich._terminal_title_setup,rich.terminal_title_item_catalog,load_terminal_title_config)
    assert setup(console,line,input_fn=lambda:'2,1,2') is True
    assert load(path).items==(catalog()[1]['id'],catalog()[0]['id'])
    assert json.loads(path.read_text())['unrelated']=={'fixture':'keep'}


@pytest.mark.parametrize('kind',['status','title'])
@pytest.mark.parametrize('choice',['','bad','0','999',', ,'])
def test_invalid_or_cancelled_item_menu_never_rewrites_settings(settings,kind,choice):
    console,line,path=settings;before=path.read_bytes()
    setup=rich._statusline_setup if kind=='status' else rich._terminal_title_setup
    assert setup(console,line,input_fn=lambda:choice) is False
    assert path.read_bytes()==before


@pytest.mark.parametrize('kind',['status','title'])
@pytest.mark.parametrize('error',[EOFError,KeyboardInterrupt])
def test_item_menu_interruption_preserves_settings(settings,kind,error):
    console,line,path=settings;before=path.read_bytes()
    setup=rich._statusline_setup if kind=='status' else rich._terminal_title_setup
    assert setup(console,line,input_fn=Mock(side_effect=error)) is False
    assert path.read_bytes()==before


@pytest.mark.parametrize('kind',['status','title'])
def test_item_menu_reset_restores_default_without_erasing_other_preferences(settings,kind):
    console,line,path=settings
    setup,load=(rich._statusline_setup,load_status_line_config) if kind=='status' else (rich._terminal_title_setup,load_terminal_title_config)
    assert setup(console,line,input_fn=lambda:'2 1')
    assert setup(console,line,input_fn=lambda:'reset')
    assert load(path).items==() and json.loads(path.read_text())['unrelated']=={'fixture':'keep'}


@pytest.mark.parametrize('choice',['','bad','0','999'])
def test_theme_menu_cancellation_or_invalid_choice_preserves_config(settings,choice):
    console,line,path=settings;before=path.read_bytes()
    assert rich._theme_setup(console,line,input_fn=lambda:choice) is False
    assert path.read_bytes()==before


@pytest.mark.parametrize('error',[EOFError,KeyboardInterrupt])
def test_theme_menu_interruption_is_safe(settings,error):
    console,line,path=settings;before=path.read_bytes()
    assert rich._theme_setup(console,line,input_fn=Mock(side_effect=error)) is False
    assert path.read_bytes()==before


def test_theme_menu_updates_then_resets_only_theme(settings):
    console,line,path=settings
    assert rich._theme_setup(console,line,input_fn=lambda:'2') is True
    assert load_theme_name(path)==rich.list_available_themes()[1].name
    assert rich._theme_setup(console,line,input_fn=lambda:'default') is True
    assert load_theme_name(path)=='dark' and json.loads(path.read_text())['unrelated']=={'fixture':'keep'}


def test_skill_menu_toggles_only_selected_package_and_persists_to_owned_store(runtime):
    import io
    from rich.console import Console
    root,_,_=runtime
    for name in ['fixture-one','fixture-two']:cli_skills.init_skill(name,data_dir=str(root),description=name)
    assert rich._skills_manage(Console(file=io.StringIO()),data_dir=str(root),input_fn=Mock(side_effect=['1',''])) is True
    listed=cli_skills.list_skills(data_dir=str(root))['skills']
    assert listed[0]['enabled'] is False and listed[1]['enabled'] is True
    summary=rich._skills_summary(data_dir=str(root))
    assert 'fixture-one' in summary and 'fixture-two' in summary


@pytest.mark.parametrize('choice',['bad','0','999'])
def test_invalid_skill_choice_does_not_change_owned_skill_state(runtime,choice):
    import io
    from rich.console import Console
    root,_,_=runtime;cli_skills.init_skill('fixture',data_dir=str(root))
    assert rich._skills_manage(Console(file=io.StringIO()),data_dir=str(root),input_fn=lambda:choice) is False
    assert cli_skills.list_skills(data_dir=str(root))['active_count']==1


@pytest.mark.parametrize('error',[EOFError,KeyboardInterrupt])
def test_skill_menu_interruption_does_not_disable_package(runtime,error):
    import io
    from rich.console import Console
    root,_,_=runtime;cli_skills.init_skill('fixture',data_dir=str(root))
    assert rich._skills_manage(Console(file=io.StringIO()),data_dir=str(root),input_fn=Mock(side_effect=error)) is False
    assert cli_skills.list_skills(data_dir=str(root))['active_count']==1


def test_rich_entrypoint_sanitizes_session_and_passes_hooks_without_starting_worker(session,monkeypatch):
    instance=Mock();instance.run.return_value=7;constructor=Mock(return_value=instance)
    monkeypatch.setattr(chat_session,'ChatSession',constructor)
    monkeypatch.setattr(rich,'get_profile_config',lambda _:{'backend':'cc-backend','model':'fixture'})
    monkeypatch.setattr(rich,'_branch',lambda:'fixture-branch')
    monkeypatch.setattr(rich,'_make_console',lambda:session.console)
    hook=Mock();submit=Mock()
    assert rich.run_rich_chat(profile='fixture',session='name with spaces',data_dir=session.data_dir,on_profile_change=hook,before_turn=hook,submit_task=submit,exit_on_interrupt=True)==7
    kwargs=constructor.call_args.kwargs
    assert kwargs['session_name']=='name_with_spaces' and kwargs['profile']=='fixture'
    assert kwargs['on_profile_change'] is hook and kwargs['before_turn'] is hook and kwargs['submit_task'] is submit
    instance.run.assert_called_once_with();submit.assert_not_called()
