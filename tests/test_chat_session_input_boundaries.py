"""Session input routing and cleanup without starting model work."""
import json
import shlex
from unittest.mock import Mock

import pytest

from test_chat_session_lifecycle_contracts import session  # noqa: F401
from factory.core import chat_session as c
from factory.core import cli_chat_idle as idle
from factory.core import cli_chat_active as active
from factory.core import cli_chat_rich as rendering
from factory.core import wally_auth


@pytest.mark.parametrize('result,expected', [('  hello  ', 'hello'), ('', None), ('   ', None), (None, None)])
def test_idle_composer_trims_and_clears_binding(session, monkeypatch, result, expected):
    app = Mock()
    app.run.return_value = result
    session._mouse_controller.consume_viewer_request.return_value = False
    monkeypatch.setattr(idle, '_create_idle_composer_application', lambda *a, **k: (app, None))
    assert session._read_input() == expected
    session._mouse_controller.clear_app.assert_called_once()
    session._mouse_controller.set_capture_allowed.assert_called_once_with(False)


@pytest.mark.parametrize('error', [EOFError, KeyboardInterrupt, idle._IdleReasoningToggle, c._ReasoningToggle])
def test_idle_composer_control_exits_restore_binding(session, monkeypatch, error):
    app = Mock()
    app.run.side_effect = error
    monkeypatch.setattr(idle, '_create_idle_composer_application', lambda *a, **k: (app, None))
    result = session._read_input()
    assert result == (c._REASONING_TOGGLE if error in (idle._IdleReasoningToggle, c._ReasoningToggle) else None)
    session._mouse_controller.clear_app.assert_called_once()


@pytest.mark.parametrize('mouse_signal', [True, False])
def test_viewer_returns_to_same_input_loop(session, monkeypatch, mouse_signal):
    apps = [Mock(), Mock()]
    apps[0].run.return_value = 'draft' if mouse_signal else '__mouse_viewer__'
    apps[1].run.return_value = 'after viewer'
    session._mouse_controller.consume_viewer_request.side_effect = [True, False] if mouse_signal else [False]
    builder = Mock(side_effect=[(app, None) for app in apps])
    monkeypatch.setattr(idle, '_create_idle_composer_application', builder)
    session._open_history_viewer = Mock()
    assert session._read_input() == 'after viewer'
    session._open_history_viewer.assert_called_once()
    assert session._mouse_controller.clear_app.call_count == 2


@pytest.mark.parametrize('result,expected', [('  next  ', 'next'), ('__INTERRUPT__', None), ('', None), (None, None)])
def test_fullscreen_input_preserves_transcript_callbacks(session, monkeypatch, result, expected):
    app = Mock()
    app.run.return_value = result
    builder = Mock(return_value=(app, None, None))
    monkeypatch.setattr(active, '_create_chat_application', builder)
    assert session._read_input_fullscreen() == expected
    args = builder.call_args.kwargs
    assert args['fullscreen'] is True
    assert args['transcript_provider'].__self__ is session
    assert args['on_scroll'].__self__ is session
    assert args['mouse_controller'] is session._mouse_controller
    session._mouse_controller.clear_app.assert_called_once()


@pytest.mark.parametrize('error', [EOFError, KeyboardInterrupt])
def test_fullscreen_input_cancellation_cleans_app(session, monkeypatch, error):
    app = Mock()
    app.run.side_effect = error
    monkeypatch.setattr(active, '_create_chat_application', lambda **k: (app, None, None))
    assert session._read_input_fullscreen() is None
    session._mouse_controller.clear_app.assert_called_once()


def test_fullscreen_application_unavailable_is_clean_eof(session, monkeypatch):
    monkeypatch.setattr(active, '_create_chat_application', lambda **k: (None, None, None))
    assert session._read_input_fullscreen() is None


@pytest.mark.parametrize('route,text', [('command', '/status'), ('bootstrap', 'my name'), ('memory', 'remember it'), ('turn', 'work')])
def test_only_one_handler_receives_each_input(session, monkeypatch, route, text):
    monkeypatch.setattr(c, 'bootstrap_active', lambda _: route == 'bootstrap')
    session.mm.handle_explicit_user_memory.return_value = (route == 'memory', None, [])
    handlers = {'command': '_handle_command', 'bootstrap': '_handle_bootstrap', 'memory': '_handle_memory_write', 'turn': '_execute_user_turn'}
    for method in handlers.values():
        setattr(session, method, Mock())
    session._handle_input(text)
    for kind, method in handlers.items():
        mock = getattr(session, method)
        if kind == route:
            mock.assert_called_once_with(text)
        else:
            mock.assert_not_called()


@pytest.mark.parametrize('status,should_cancel', [('running', True), ('queued', True), ('ok', False), ('fail', False), ('dead_letter', False), ('cancelled', False), (None, False)])
def test_session_exit_cancels_only_own_unfinished_task(session, monkeypatch, status, should_cancel):
    session.exit_on_interrupt = True
    session._current_task_id = 'owned-fixture-task'
    session._startup = Mock(side_effect=KeyboardInterrupt)
    session._goodbye = Mock()
    monkeypatch.setattr(c, 'get_task', Mock(return_value=None if status is None else {'status': status}))
    cancel = Mock()
    monkeypatch.setattr(c, 'cancel_task', cancel)
    assert session.run() == 0
    if should_cancel:
        cancel.assert_called_once_with('owned-fixture-task')
    else:
        cancel.assert_not_called()
    session._mouse_controller.disable.assert_called_once()
    session._mouse_controller.release_output.assert_called_once()


def test_pending_input_and_control_keys_do_not_duplicate_turns(session):
    session._startup = Mock()
    session._render_widget = Mock()
    session._goodbye = Mock()
    session.pending_inputs = [c._REASONING_TOGGLE, '\x00unknown', '', 'first']
    session._read_input = Mock(side_effect=['second', '/exit'])
    session._handle_input = Mock()
    assert session.run() == 0
    assert [x.args[0] for x in session._handle_input.call_args_list] == ['first', 'second']
    assert session.pending_inputs == []


def test_resume_hint_quotes_paths_without_interpreting_markup(session):
    session.exit_on_interrupt = True
    session.profile = 'profile with space'
    session.session_name = 'thread;echo unsafe'
    session.data_dir = '/tmp/path with [brackets]'
    session._goodbye()
    command = session.output_fixture.getvalue().strip().split('Resume: ', 1)[1]
    assert shlex.split(command) == ['igorek', '--profile', session.profile, '--resume', session.session_name, '--data-dir', session.data_dir]


@pytest.mark.parametrize('error', [wally_auth.WallyAuthError('fixture denied'), ValueError('fixture malformed')])
def test_account_failure_does_not_break_chat(session, monkeypatch, error):
    monkeypatch.setattr(wally_auth, 'wally_account_summary_lines', Mock(side_effect=error))
    session._cmd_wally()
    assert 'Wally:' in session.output_fixture.getvalue()
    assert str(error) in session.output_fixture.getvalue()


def test_account_summary_prints_fake_provider_lines(session, monkeypatch):
    monkeypatch.setattr(wally_auth, 'wally_account_summary_lines', lambda: ['Plan fixture', 'Balance 0'])
    session._cmd_wally()
    assert 'Plan fixture' in session.output_fixture.getvalue()
    assert 'Balance 0' in session.output_fixture.getvalue()


@pytest.mark.parametrize('events', [[], [{'kind': 'model.text_delta', 'text': 'answer'}]])
def test_trace_replay_reads_owned_file_and_keeps_text(session, tmp_path, monkeypatch, events):
    trace = tmp_path / 'owned-trace.jsonl'
    trace.write_text(''.join(json.dumps(row) + '\n' for row in events))
    monkeypatch.setattr(rendering, '_trace_path_for_task', lambda _: trace)
    cells = session._trace_cells_for_task('fixture-trace')
    if not events:
        assert cells == []
    else:
        assert any('answer' in str(getattr(cell, 'text', '')) for cell in cells)


def test_absent_trace_falls_back_to_history(session, monkeypatch, tmp_path):
    monkeypatch.setattr(rendering, '_trace_path_for_task', lambda _: tmp_path / 'absent.jsonl')
    assert session._trace_cells_for_task('fixture-trace') == []
    assert session._trace_cells_for_task('') == []
