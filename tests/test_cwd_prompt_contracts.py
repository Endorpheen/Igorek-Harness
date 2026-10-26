"""Working-directory selection through real prompt-toolkit keyboard events."""
from unittest.mock import Mock

import pytest
from prompt_toolkit.application import create_app_session
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput

from factory.core.tui import cwd_prompt as cwd
from factory.core.tui.key_hint import KeyCode,KeyEvent,KeyEventKind,KeyModifiers

pytestmark=pytest.mark.timeout(5)


@pytest.mark.parametrize('action',[cwd.CwdPromptAction.Resume,cwd.CwdPromptAction.Fork])
def test_screen_starts_with_the_saved_directory_and_no_confirmation(action):
    screen=cwd.CwdPromptScreen.new(Mock(),action,'/current','/saved')
    assert screen.highlighted is cwd.CwdSelection.Session
    assert screen.get_selection() is None and not screen.is_done()
    assert action.verb() in {'resume','fork'}
    assert action.past_participle() in {'resumed','forked'}


@pytest.mark.parametrize('code,expected',[
    (KeyCode.Down(),cwd.CwdSelection.Current),
    (KeyCode.Up(),cwd.CwdSelection.Current),
    (KeyCode.Char('j'),cwd.CwdSelection.Current),
    (KeyCode.Char('k'),cwd.CwdSelection.Current),
])
def test_key_state_uses_actual_key_hint_events(code,expected):
    frames=Mock();screen=cwd.CwdPromptScreen.new(frames,cwd.CwdPromptAction.Resume,'/current','/saved')
    screen.handle_key(KeyEvent(code,KeyModifiers.NONE))
    assert screen.highlighted is expected and not screen.is_done()
    screen.handle_key(KeyEvent(KeyCode.Enter(),KeyModifiers.NONE))
    assert screen.get_selection() is expected and screen.is_done()
    assert frames.schedule_frame.called


def test_key_release_and_unrelated_keys_do_not_change_selection():
    screen=cwd.CwdPromptScreen.new(Mock(),cwd.CwdPromptAction.Resume,'/current','/saved')
    screen.handle_key(KeyEvent(KeyCode.Down(),KeyModifiers.NONE,KeyEventKind.Release))
    screen.handle_key(KeyEvent(KeyCode.Char('x'),KeyModifiers.NONE))
    assert screen.highlighted is cwd.CwdSelection.Session and not screen.is_done()


@pytest.mark.parametrize('code',[KeyCode.Char('c'),KeyCode.Char('d')])
def test_control_cancel_is_explicit_and_does_not_select_a_directory(code):
    screen=cwd.CwdPromptScreen.new(Mock(),cwd.CwdPromptAction.Resume,'/current','/saved')
    screen.handle_key(KeyEvent(code,KeyModifiers.CONTROL))
    assert screen.should_exit and screen.is_done() and screen.get_selection() is None


@pytest.mark.parametrize('keys,kind,selection',[
    ('\r',cwd.CwdPromptOutcomeKind.Selection,cwd.CwdSelection.Session),
    ('\x1b[B\r',cwd.CwdPromptOutcomeKind.Selection,cwd.CwdSelection.Current),
    ('\x1b[A\r',cwd.CwdPromptOutcomeKind.Selection,cwd.CwdSelection.Current),
    ('j\r',cwd.CwdPromptOutcomeKind.Selection,cwd.CwdSelection.Current),
    ('k\r',cwd.CwdPromptOutcomeKind.Selection,cwd.CwdSelection.Current),
    ('1',cwd.CwdPromptOutcomeKind.Selection,cwd.CwdSelection.Session),
    ('2',cwd.CwdPromptOutcomeKind.Selection,cwd.CwdSelection.Current),
    ('\x1b',cwd.CwdPromptOutcomeKind.Selection,cwd.CwdSelection.Session),
    ('\x03',cwd.CwdPromptOutcomeKind.Exit,None),
    ('\x04',cwd.CwdPromptOutcomeKind.Exit,None),
])
def test_real_prompt_input_returns_typed_selection_or_cancel(keys,kind,selection):
    with create_pipe_input() as pipe,create_app_session(input=pipe,output=DummyOutput()):
        pipe.send_text(keys)
        result=cwd.run_cwd_prompt(action=cwd.CwdPromptAction.Resume,current_cwd='/current',session_cwd='/saved')
    assert result.kind is kind and result.selection is selection


def test_closed_input_cancels_instead_of_silently_switching_workdir():
    with create_pipe_input() as pipe,create_app_session(input=pipe,output=DummyOutput()):
        pipe.close()
        result=cwd.run_cwd_prompt(action=cwd.CwdPromptAction.Resume,current_cwd='/current',session_cwd='/saved')
    assert result.kind is cwd.CwdPromptOutcomeKind.Exit and result.selection is None
