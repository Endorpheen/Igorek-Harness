"""Ready forms and active-turn controls through real owned prompt-toolkit input."""
import threading
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from prompt_toolkit.application import create_app_session
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput

from test_chat_session_lifecycle_contracts import session  # noqa: F401
from factory.core import chat_session as c,cli_chat_active as active,cli_chat_idle as idle
from factory.core.tui.mouse_controller import MouseController


@pytest.fixture
def turn(session,monkeypatch,tmp_path):
    finished=threading.Event();threads=[];states=[];real_thread=threading.Thread
    def spawn(**kwargs):
        thread=real_thread(**kwargs);threads.append(thread);return thread
    monkeypatch.setattr(c,'threading',SimpleNamespace(Event=threading.Event,Thread=spawn))
    session._mouse_controller=MouseController(user_enabled=False,clipboard_service=SimpleNamespace(copy=Mock()))
    monkeypatch.setattr(active,'create_output',lambda:DummyOutput())
    monkeypatch.setattr(idle,'_make_idle_completer',lambda:None)
    session.submit_task=Mock(return_value={'task_id':'owned-form-task'})
    trace=tmp_path/'trace.jsonl';trace.write_text('');monkeypatch.setattr(c,'_trace_path_for_task',lambda _:trace)
    session._pending_user_input_request=lambda _:None
    cancel=Mock(side_effect=lambda _:finished.set());monkeypatch.setattr(c,'cancel_task',cancel)
    builder=active._create_chat_application
    def run(*,keys='',requests=(),after_render=None,complete_if=None):
        pending=list(requests);resolved=[]
        session._pending_user_input_request=lambda _:pending[0] if pending else None
        def resolve(value):
            assert value is pending[0];resolved.append(pending.pop(0))
            if not pending:finished.set()
        session._resolve_user_input_request=resolve
        def create(**kwargs):
            states.append(kwargs['running_state']);app,buffer,lines=builder(**kwargs);app.ttimeoutlen=.01;app.timeoutlen=.01
            if after_render is not None:
                called=[]
                def callback(_):
                    if not called:called.append(True);after_render(app,finished)
                app.after_render+=callback
            return app,buffer,lines
        monkeypatch.setattr(active,'_create_chat_application',create)
        def get_task(_):
            if complete_if is not None and complete_if(states):finished.set()
            return {'task_id':'owned-form-task','status':('cancelled' if cancel.called else 'ok') if finished.is_set() else 'waiting_approval' if pending else 'running','output_text':'fixture final answer'}
        monkeypatch.setattr(c,'get_task',get_task)
        try:
            with create_pipe_input() as pipe,create_app_session(input=pipe,output=DummyOutput()):
                if keys:pipe.send_text(keys)
                result=session._execute_one_turn('fixture prompt',{})
            return SimpleNamespace(result=result,resolved=resolved,states=states,cancel=cancel,session=session)
        finally:
            finished.set()
            for thread in threads:thread.join(3);assert not thread.is_alive()
    return run,session,finished


@pytest.mark.parametrize('count',[1,2])
def test_already_pending_form_waits_for_real_application_start(turn,count):
    run,_,_=turn;requests=[{'request_id':'owned-input-'+str(i),'payload':{'questions':[]}} for i in range(count)]
    outcome=run(requests=requests)
    assert outcome.result['status']=='ok' and outcome.result['output_text']=='fixture final answer'
    assert outcome.resolved==requests
    outcome.cancel.assert_not_called()


def test_active_prompt_enqueues_followup_once_without_resubmitting_task(turn):
    run,session,_=turn
    outcome=run(keys='fixture followup\r',complete_if=lambda _:session.mq.pending_count==1)
    assert outcome.result['status']=='ok' and session.mq.peek_all()==['fixture followup']
    session.submit_task.assert_called_once()


@pytest.mark.parametrize('keys,exit_session',[('\x03',True),('\x1b',False)])
def test_keyboard_interrupt_cancels_only_owned_task_and_preserves_session_policy(turn,keys,exit_session):
    run,session,_=turn;session.exit_on_interrupt=exit_session
    outcome=run(keys=keys)
    outcome.cancel.assert_called_once_with('owned-form-task')
    assert session._active is (not exit_session)
    assert outcome.result is None or outcome.result['status']=='cancelled'
    assert 'Interrupted by user' in session.output_fixture.getvalue()


def test_viewer_transition_restores_active_application_without_queuing_marker(turn,monkeypatch):
    run,session,finished=turn
    viewer=Mock(side_effect=finished.set);monkeypatch.setattr(session,'_open_history_viewer',viewer)
    outcome=run(after_render=lambda app,_:app.exit(result='__mouse_viewer__'))
    assert outcome.result['status']=='ok' and session.mq.pending_count==0
    viewer.assert_called_once()


def test_local_tui_command_waits_for_turn_end_and_never_becomes_model_input(turn,monkeypatch):
    run,session,_=turn;command=Mock();monkeypatch.setattr(session,'_cmd_tui',command)
    outcome=run(keys='/tui off\r',complete_if=lambda states:any(state.get('local_tui_command')=='/tui off' for state in states))
    command.assert_called_once_with('/tui off')
    assert outcome.result['status']=='ok' and session.mq.pending_count==0


def test_pending_interrupt_before_task_creation_is_applied_to_exact_new_task(turn):
    run,session,_=turn;session._fs_running_state={'interrupt_pending':True}
    outcome=run()
    outcome.cancel.assert_called_once_with('owned-form-task')
    assert session._fs_running_state['current_task_id']=='owned-form-task'
    assert 'interrupt_pending' not in session._fs_running_state and session._fs_running_state['task_finished'] is True


@pytest.mark.parametrize('command',['/exit','/quit','/tui off'])
def test_persistent_session_local_controls_never_start_model_task(session,monkeypatch,command):
    session._tui_fullscreen=True
    session._mouse_controller=MouseController(user_enabled=False,clipboard_service=SimpleNamespace(copy=Mock()))
    monkeypatch.setattr(active,'create_output',lambda:DummyOutput());monkeypatch.setattr(idle,'_make_idle_completer',lambda:None)
    execute=Mock(side_effect=AssertionError('no model task expected'));monkeypatch.setattr(session,'_execute_user_turn',execute)
    with create_pipe_input() as pipe,create_app_session(input=pipe,output=DummyOutput()):
        pipe.send_text(command+'\r');assert session.run_fullscreen()==0
    execute.assert_not_called()
    assert session._fs_external_app is None and session._fs_loop is None
    if command=='/tui off':assert session._tui_fullscreen is False
    else:assert session._active is False


def test_persistent_session_runs_submitted_turn_once_and_cleans_busy_state(session,monkeypatch):
    session._tui_fullscreen=True
    session._mouse_controller=MouseController(user_enabled=False,clipboard_service=SimpleNamespace(copy=Mock()))
    monkeypatch.setattr(active,'create_output',lambda:DummyOutput());monkeypatch.setattr(idle,'_make_idle_completer',lambda:None)
    def complete(text):assert text=='fixture prompt';session._active=False
    execute=Mock(side_effect=complete);monkeypatch.setattr(session,'_execute_user_turn',execute)
    with create_pipe_input() as pipe,create_app_session(input=pipe,output=DummyOutput()):
        pipe.send_text('fixture prompt\r');assert session.run_fullscreen()==0
    execute.assert_called_once_with('fixture prompt')
    assert session._fs_external_app is None and not session._fs_in_turn
    assert session._fs_running_state['task_active'] is False
