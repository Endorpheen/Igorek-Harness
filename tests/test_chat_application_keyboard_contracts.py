"""Real prompt input for the shared active/idle chat application."""
from unittest.mock import Mock

import pytest
from prompt_toolkit.application import create_app_session
from prompt_toolkit.completion import Completion
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput

from factory.core import cli_chat_active as active
from factory.core import cli_chat_idle as idle
from factory.core import cli_chat_rich_shared as shared
from factory.core.message_queue import MessageQueue
from factory.core.tui.bottom_pane import BottomPane
from factory.core.tui.chatwidget import ChatWidget


@pytest.fixture
def run_app(monkeypatch):
    cancelled = []
    monkeypatch.setattr(active, 'cancel_task', lambda identity: cancelled.append(identity))
    monkeypatch.setattr(shared, '_clipboard_image_path', lambda: None)
    monkeypatch.setattr(shared, '_clipboard_text', lambda: None)

    def run(keys, *, state=None, persistent=False, on_submit=None, **kwargs):
        current = {} if state is None else state
        with create_pipe_input() as pipe, create_app_session(input=pipe, output=DummyOutput()):
            app, buffer, lines = active._create_chat_application(running_state=current, persistent=persistent, on_submit=on_submit, **kwargs)
            app.ttimeoutlen = 0.01
            app.timeoutlen = 0.01
            pipe.send_text(keys)
            result = app.run()
            return result, current, buffer, lines
    run.cancelled = cancelled
    return run


@pytest.mark.parametrize('keys,expected', [
    ('hello\r','hello'), ('  hello  \r','hello'), ('Привет 🐙\r','Привет 🐙'),
    ('one\ntwo\r','one\ntwo'), ('a b\r','a b'), ('abc\x7fd\r','abd'),
    ('message?\r','message?'), ('word @file $skill /path\r','word @file $skill /path'),
])
def test_actual_keyboard_submission_clears_buffer_and_returns_only_user_text(run_app, keys, expected):
    result, state, buffer, _ = run_app(keys)
    assert result == expected == state['submitted_text']
    assert buffer.text == ''
    assert run_app.cancelled == []


def test_empty_enter_does_not_finish_the_chat(run_app):
    result, state, _, _ = run_app('\r\x03')
    assert result == '__INTERRUPT__'
    assert 'submitted_text' not in state


@pytest.mark.parametrize('keys', ['?\x1b\x03', '??\x03'])
def test_shortcut_overlay_can_be_closed_without_submitting_a_message(run_app, keys):
    result, state, _, _ = run_app(keys)
    assert result == '__INTERRUPT__'
    assert not state['show_shortcuts']
    assert 'submitted_text' not in state


def test_transcript_shortcut_is_a_local_command(run_app):
    result, state, _, _ = run_app('\x14')
    assert result == state['submitted_text'] == '__transcript__'


@pytest.mark.parametrize('task_id', ['fixture-task', ''])
@pytest.mark.parametrize('key', ['\x1b', '\x03'])
def test_persistent_cancellation_never_loses_a_cancel_before_task_creation(run_app, task_id, key):
    state = {'task_active':True, 'current_task_id':task_id, 'escape_interrupts':True, 'exit_on_interrupt':True}
    keys = key if key == '\x03' else key + '\x03'
    result, _, _, _ = run_app(keys, state=state, persistent=True)
    assert result == '__INTERRUPT__' and state['interrupted'] and state['exit_requested']
    if task_id:
        assert run_app.cancelled and set(run_app.cancelled) == {task_id}
    else:
        assert state['interrupt_pending'] and run_app.cancelled == []


def test_nonpersistent_escape_interrupts_an_active_turn_without_cancelling_owner_tasks(run_app):
    result, state, _, _ = run_app('\x1b', state={'task_active':True,'escape_interrupts':True})
    assert result == '__INTERRUPT__' and run_app.cancelled == []


def test_cancel_transport_failure_still_closes_the_local_application(run_app, monkeypatch):
    monkeypatch.setattr(active,'cancel_task',Mock(side_effect=RuntimeError('fixture unavailable')))
    result, state, _, _ = run_app('\x03', state={'task_active':True,'current_task_id':'fixture-task'}, persistent=True)
    assert result == '__INTERRUPT__' and state['interrupted']


def test_persistent_submit_can_queue_multiple_messages_without_exiting(run_app):
    messages = []
    result, state, buffer, _ = run_app('first\rsecond\r\x03', persistent=True, on_submit=messages.append)
    assert result == '__INTERRUPT__' and messages == ['first','second']
    assert 'submitted_text' not in state and buffer.text == ''


def test_persistent_tui_command_returns_to_the_session_controller(run_app):
    messages = []
    result, _, _, _ = run_app('/tui on\r', persistent=True, on_submit=messages.append)
    assert result == '__tui__:/tui on' and messages == []


def test_inline_tui_command_is_retained_without_a_provider_submission(run_app):
    result, state, _, _ = run_app('/tui on\r\x03')
    assert result == '__INTERRUPT__' and state['local_tui_command'] == '/tui on'
    assert 'submitted_text' not in state


@pytest.mark.parametrize('fullscreen,reasoning,expected', [
    (False, {'visible':False,'lines':['thought'],'cell_overrides':{'x':True}}, True),
    (True, {'visible':False,'lines':[],'cell_overrides':{'x':True}}, True),
    (False, {'visible':False,'lines':[],'cell_overrides':{'x':True}}, False),
    (False, {'visible':True,'expanded_lines':['thought'],'cell_overrides':{'x':False}}, False),
])
def test_reasoning_toggle_changes_visibility_without_touching_message_text(run_app, fullscreen, reasoning, expected):
    state = {'reasoning_state':reasoning}
    result, _, buffer, _ = run_app('\x0f\x03', state=state, fullscreen=fullscreen)
    assert result == '__INTERRUPT__' and reasoning['visible'] is expected and buffer.text == ''
    if reasoning.get('lines') or reasoning.get('expanded_lines') or fullscreen:
        assert reasoning['cell_overrides'] == {}


@pytest.mark.parametrize('trigger', ['/', '@', '$'])
def test_completion_triggers_are_inserted_as_text_and_tab_applies_a_candidate(run_app, monkeypatch, trigger):
    monkeypatch.setattr(idle,'_completion_to_apply',lambda buffer: Completion('fixture',start_position=-len(trigger)))
    result, _, _, _ = run_app(trigger+'\t\r')
    assert result == 'fixture'


@pytest.mark.parametrize('existing,image,text,expected', [
    ('', '/fixture/image.png', None, '📎 /fixture/image.png'),
    ('prefix', '/fixture/image.png', None, 'prefix 📎 /fixture/image.png'),
    ('prefix ', '/fixture/image.png', None, 'prefix 📎 /fixture/image.png'),
    ('', None, 'pasted text', 'pasted text'),
    ('prefix', None, None, 'prefix'),
])
def test_clipboard_adapters_insert_paths_or_text_without_accessing_system_clipboard(run_app, monkeypatch, existing, image, text, expected):
    monkeypatch.setattr(shared,'_clipboard_image_path',lambda:image)
    monkeypatch.setattr(shared,'_clipboard_text',lambda:text)
    result, _, _, _ = run_app(existing+'\x16\r')
    assert result == expected


def test_fullscreen_navigation_calls_scroll_controller_without_changing_input(run_app):
    scroll, edge = [], []
    result, _, buffer, _ = run_app('\x1b[5~\x1b[6~\x1b[1;5H\x1b[1;5F\x03', fullscreen=True, on_scroll=scroll.append, on_scroll_edge=edge.append)
    assert result == '__INTERRUPT__' and scroll == [5,-5] and edge == ['top','bottom'] and buffer.text == ''


def test_follow_up_callbacks_keep_pending_queue_and_preview_in_sync(monkeypatch):
    from factory.core.skills_manager import skills_manager
    monkeypatch.setattr(skills_manager,'is_loaded',False)
    widget = ChatWidget(width=100,model='fixture',branch='fixture',cwd='/fixture')
    widget.set_bottom_pane(BottomPane())
    queue, changes = MessageQueue(), []
    composer = active._attach_running_composer_callbacks(widget,queue,on_change=lambda:changes.append(True))
    composer.text = 'first'
    composer.handle_key_event('enter')
    composer.text = 'second'
    composer.handle_key_event('tab')
    assert queue.peek_all() == ['first','second']
    assert len(changes) == 2
    assert 'first' in '\n'.join(widget.bottom_pane.pending_preview.render_lines())
