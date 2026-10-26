"""Active-turn integration with owned polling threads and controlled UI boundaries."""
import asyncio
from collections import deque
import io
import threading
import time
from types import SimpleNamespace

import pytest
from rich.console import Console

from factory.core import cli_chat_active as active
from factory.core import cli_chat_user_input, user_input_store
from factory.core.message_queue import MessageQueue
from factory.core.trace_taxonomy import CcTraceEvent


@pytest.fixture
def harness(monkeypatch, tmp_path):
    for name in ('HOME', 'FACTORY_DATA_DIR', 'IGOREK_FACTORY_HOME'):
        monkeypatch.setenv(name, str(tmp_path / name.lower()))
    state = SimpleNamespace(
        release=threading.Event(), notice=threading.Event(), threads=[], apps=[],
        created=[], cancelled=[], resolved=[], prints=[], frames=[], offsets=[0.0],
        events=deque(), status='ok', error=None, missing=False, timeout=False,
        frontend='app', submit='', form=None, inline=None, interrupt=False,
        keyboard=False, queued_notice=False, reads=0, widgets=[],
        start_error=None, keep_processing=False, stop_poll=False,
        continuous_trace=False, poll_started=threading.Event(),
    )
    output = io.StringIO()
    state.console = Console(file=output, force_terminal=False, width=90, markup=False)
    state.output = output
    state.queued = MessageQueue()
    state.status_line = SimpleNamespace(refresh=lambda: state.prints.append('refresh'),
        toolbar_text=lambda: 'Owned footer', note_task=lambda task: state.prints.append(('note', task)))
    class PollThread(threading.Thread):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            state.threads.append(self)
    monkeypatch.setattr(active, 'threading', SimpleNamespace(Thread=PollThread, Event=threading.Event))
    monkeypatch.setattr(active, 'time', SimpleNamespace(
        time=time.time, monotonic=lambda: time.monotonic() + state.offsets[0], sleep=time.sleep))
    monkeypatch.setattr(active, 'shutil', SimpleNamespace(get_terminal_size=lambda fallback: SimpleNamespace(columns=90, lines=25)))
    monkeypatch.setattr(active, '_branch', lambda: 'owned-test-branch')
    monkeypatch.setattr(active, 'get_profile_config', lambda profile: {'model': 'owned-test-model'})
    monkeypatch.setattr(active, 'terminal_render_log', lambda *args, **kwargs: None)
    def create_task(**kwargs):
        state.created.append(kwargs)
        return {'task_id': 'owned-live-turn'}
    monkeypatch.setattr(active, 'create_task', create_task)
    monkeypatch.setattr(active, 'cancel_task', state.cancelled.append)
    monkeypatch.setattr(active, '_trace_path_for_task', lambda task: tmp_path / 'owned-trace.jsonl')
    def read_events(path, seen):
        assert path == tmp_path / 'owned-trace.jsonl'
        state.reads += 1
        state.poll_started.set()
        if state.continuous_trace and not state.stop_poll:
            time.sleep(0.002)
            return [{'kind': CcTraceEvent.MODEL_TEXT_DELTA, 'text': 'owned late chunk'}], seen + 1
        batch = state.events.popleft() if state.events else []
        return batch, seen + len(batch)
    monkeypatch.setattr(active, '_read_new_trace_events', read_events)
    def get_task(task_id):
        assert task_id == 'owned-live-turn'
        assert state.release.wait(3), 'Controlled UI boundary did not release its own poller'
        if state.stop_poll:
            return None
        if state.error:
            raise state.error
        if state.missing:
            return None
        if state.timeout or state.keep_processing:
            return {'task_id': task_id, 'status': 'processing'}
        if state.queued_notice and not state.notice.is_set():
            state.offsets[0] = 10.0
            return {'task_id': task_id, 'status': 'queued'}
        return {'task_id': task_id, 'status': state.status, 'result_text': 'owned result'}
    monkeypatch.setattr(active, 'get_task', get_task)
    def pending(**kwargs):
        assert kwargs == {'task_id': 'owned-live-turn'}
        return [] if state.form is None else [state.form]
    monkeypatch.setattr(user_input_store, 'list_pending_user_input_requests', pending)
    monkeypatch.setattr(user_input_store, 'resolve_user_input_request',
        lambda request_id, **kwargs: state.resolved.append((request_id, kwargs)))
    state.answer = None
    monkeypatch.setattr(cli_chat_user_input, '_ask_user_input', lambda console, request: state.answer)
    original_widget = active.ChatWidget
    class ObservedWidget(original_widget):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            state.widgets.append(self)
        def add_status_cell(self, text):
            if text == active._QUEUED_TASK_NOTICE:
                state.notice.set()
            return super().add_status_cell(text)
    monkeypatch.setattr(active, 'ChatWidget', ObservedWidget)
    async def run_in_terminal(callback):
        callback()
    monkeypatch.setattr(active, 'run_in_terminal', run_in_terminal)
    class Buffer:
        def __init__(self):
            self.text = 'owned unsent input'
            self.resets = 0
        def reset(self):
            self.text = ''
            self.resets += 1
    class Application:
        def __init__(self, provider):
            self.provider = provider
            self.stop = asyncio.Event()
            self.cancelled = False
            self.invalidations = 0
        async def run_async(self):
            state.frames.append(list(self.provider()))
            state.release.set()
            try:
                await self.stop.wait()
            except asyncio.CancelledError:
                self.cancelled = True
                raise
        def exit(self, *, result):
            assert result == '__form__'
            self.stop.set()
        def invalidate(self):
            self.invalidations += 1
            state.frames.append(list(self.provider()))
    def create_application(status_line, *, running_state, output_provider, composer):
        if state.start_error is not None:
            assert state.poll_started.wait(3)
            state.release.set()
            raise state.start_error
        if state.keyboard:
            state.release.set()
            raise KeyboardInterrupt()
        if state.interrupt:
            running_state['interrupted'] = True
        was_submitted = bool(state.submit)
        if state.submit:
            running_state['submitted_text'] = state.submit
            state.submit = ''
        if state.inline is not None:
            running_state['user_input_form_result'] = state.inline
        if state.frontend == 'noapp':
            state.release.set()
            return None, None, []
        app = Application(output_provider)
        buffer = Buffer()
        if was_submitted:
            buffer.reset()
        state.apps.append((app, buffer))
        return app, buffer, []
    monkeypatch.setattr(active, '_create_running_application', create_application)
    class Live:
        def __init__(self, renderable, **kwargs):
            self.renderable = renderable
        def __enter__(self):
            state.release.set()
            return self
        def __exit__(self, *args):
            return False
        def update(self, text):
            self.renderable = text
    monkeypatch.setattr(active, 'Live', Live)
    def invoke():
        values = {'console': state.console, 'text': 'owned task', 'profile': 'owned profile',
            'interval_s': 0.001, 'timeout_s': -1 if state.timeout else 10,
            'metadata': {'owned': True}, 'status_line': state.status_line,
            'input_files': ['owned-input.txt']}
        if state.frontend != 'rich':
            values.update(queued_input=state.queued, queued_prompt_session=SimpleNamespace(prompt_async=lambda: None))
        return active._ask_task_with_live_trace(**values)
    state.invoke = invoke
    yield state
    state.stop_poll = True
    state.release.set()
    state.notice.set()
    for thread in state.threads:
        thread.join(timeout=3)
        assert not thread.is_alive(), 'Owned poller leaked after the integration scenario'


def answer_events(status='ok', text='Owned final answer 🐙'):
    return [[{'kind': CcTraceEvent.MODEL_TEXT_DELTA, 'text': text + '\n'}],
            [{'kind': CcTraceEvent.TASK_FINISH, 'status': status, 'output_preview': text}]]


@pytest.mark.parametrize('frontend', ['rich', 'noapp', 'app'])
def test_turn_returns_result_and_preserves_final_events(harness, frontend):
    h = harness
    h.frontend = frontend
    h.events.extend(answer_events())
    result = h.invoke()
    assert result['task_id'] == 'owned-live-turn' and result['status'] == 'ok'
    assert result['_worked_for_label'].startswith('Worked for ')
    assert h.prints == ['refresh', ('note', 'owned-live-turn')] and h.cancelled == []
    assert h.created == [{'text': 'owned task', 'profile': 'owned profile', 'metadata': {'owned': True}, 'input_files': ['owned-input.txt']}]
    if frontend == 'app':
        assert any('Owned final answer' in line for line in result['_deferred_scrollback_lines'])
        assert result['_had_streaming_output']
        assert h.apps[-1][0].cancelled
    else:
        assert h.output.getvalue().count('Owned final answer') == 1
        assert '_had_streaming_output' not in result
    assert h.reads >= 2


@pytest.mark.parametrize('status', ['fail', 'dead_letter'])
def test_terminal_task_failure_is_returned_without_invented_cancellation(harness, status):
    h = harness
    h.status = status
    h.events.extend(answer_events(status=status))
    result = h.invoke()
    assert result['status'] == status and h.cancelled == []
    assert h.apps[-1][0].cancelled


@pytest.mark.parametrize('failure', ['missing', 'exception', 'timeout'])
def test_poll_failures_reach_caller_and_cancel_only_owned_view(harness, failure):
    h = harness
    if failure == 'missing':
        h.missing = True
        expected, match = RuntimeError, 'Task not found: owned-live-turn'
    elif failure == 'exception':
        h.error = ValueError('owned read failure')
        expected, match = ValueError, 'owned read failure'
    else:
        h.timeout = True
        expected, match = TimeoutError, 'Timeout waiting for task owned-live-turn'
    with pytest.raises(expected, match=match):
        h.invoke()
    assert h.cancelled == []
    assert h.apps[-1][0].cancelled
    assert h.prints == ['refresh']


@pytest.mark.parametrize('keyboard', [False, True])
def test_interrupt_cancels_exactly_the_created_task(harness, keyboard):
    h = harness
    h.keyboard, h.interrupt = keyboard, not keyboard
    with pytest.raises(KeyboardInterrupt):
        h.invoke()
    assert h.cancelled == ['owned-live-turn']
    assert 'Interrupted by user.' in h.output.getvalue()
    assert h.prints == ['refresh']


def test_queued_worker_notice_is_visible_before_completion(harness):
    h = harness
    h.queued_notice = True
    result = h.invoke()
    assert result['status'] == 'ok' and h.notice.is_set()
    assert h.cancelled == []


def test_followup_submission_is_queued_once_and_composer_is_cleared(harness):
    h = harness
    h.queued.enqueue('existing followup')
    h.submit = '  new followup  '
    result = h.invoke()
    assert result['status'] == 'ok'
    assert h.queued.drain_all() == ['existing followup', 'new followup']
    assert h.widgets[-1].bottom_pane.composer.text == ''


def test_last_trace_chunks_during_grace_are_not_lost_or_repeated(harness):
    h = harness
    h.frontend = 'rich'
    h.events.extend([[{'kind': CcTraceEvent.MODEL_TEXT_DELTA, 'text': 'First unique line\n'}],
                     [{'kind': CcTraceEvent.MODEL_TEXT_DELTA, 'text': 'Last unique line\n'}],
                     [{'kind': CcTraceEvent.TASK_FINISH, 'status': 'ok', 'output_preview': ''}]])
    assert h.invoke()['status'] == 'ok'
    text = h.output.getvalue()
    assert text.count('First unique line') == text.count('Last unique line') == 1


@pytest.mark.parametrize('mode', ['cancel', 'answer', 'inline'])
def test_requested_input_is_resolved_and_live_view_resumes(harness, mode):
    h = harness
    h.form = {'request_id': 'owned-request', 'payload': {'questions': [{'id': 'q1', 'header': 'Owned question', 'question': 'Owned question text'}]}}
    if mode == 'answer':
        h.answer = SimpleNamespace(answers={'q1': SimpleNamespace(question_id='q1', selected_options=[], text_value='owned answer', notes='')})
    elif mode == 'inline':
        h.inline = {'response': {'cancelled': False, 'answers': {'q1': {'text_value': 'inline answer'}}}, 'cancelled': False}
    result = h.invoke()
    assert result['status'] == 'ok' and len(h.resolved) == 1
    identifier, values = h.resolved[0]
    assert identifier == 'owned-request'
    if mode == 'cancel':
        assert values == {'response': None, 'cancelled': True}
    else:
        assert not values['cancelled']
        assert values['response']['answers']['q1']['text_value'] == ('owned answer' if mode == 'answer' else 'inline answer')
    assert len(h.apps) >= 2 and h.apps[0][1].resets >= 1


def test_view_start_failure_stops_owned_poller_before_propagating(harness):
    h = harness
    h.start_error = ValueError('owned view startup failure')
    h.keep_processing = True
    with pytest.raises(ValueError, match='owned view startup failure'):
        h.invoke()
    assert h.cancelled == []
    assert h.threads and all(not thread.is_alive() for thread in h.threads)


def test_view_start_failure_interrupts_continuous_final_trace_drain(harness):
    h = harness
    h.start_error = ValueError('owned view startup failure')
    h.continuous_trace = True
    with pytest.raises(ValueError, match='owned view startup failure'):
        h.invoke()
    assert h.cancelled == []
    assert h.threads and all(not thread.is_alive() for thread in h.threads)
