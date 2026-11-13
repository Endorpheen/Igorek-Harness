"""Internal history stays behind every provider and client boundary."""
import json
from pathlib import Path

import pytest

from factory.core import cc_backend_executor as executor
from factory.core.local_history_output import HistoricalToolEchoFilter
from test_cc_stream_wire_contracts import wire, event, chunk  # noqa: F401
from test_executor_tool_loop_edges import loop  # noqa: F401
from test_executor_workflow_guards import response
from test_openai_responses import completed, message
from test_mobile_bridge_events import EventsTestCase
from test_mobile_bridge_service import ServiceTestCase
from test_worker_execution_contracts import worker_factory  # noqa: F401


ECHO = (Path(__file__).parent / 'fixtures/tool_salvage/opencode_historical_evidence_echo.txt').read_text()


@pytest.mark.parametrize('api', ['openai', 'anthropic', 'openai-responses', 'opencode-responses', 'google'])
def test_cloud_echo_with_calls_is_rejected_before_trace_salvage_or_execution(loop, monkeypatch, api):
    if api == 'google':
        from factory.core import google_generate_content
        monkeypatch.setattr(google_generate_content, 'post_stream',
                            lambda *args, **kwargs: executor._http_post(*args, **kwargs))
    tool = {'id': 'fresh-call', 'name': 'Bash', 'input': {'command': 'printf current'}}
    run = loop(api, [response(api, ECHO, tool), response(api, 'Current answer')],
               names=['Bash'], base_url='https://api.example.test/v1')
    assert run.result['output_text'] == 'Current answer'
    assert run.calls == [] and len(run.requests) == 2
    assert ECHO not in json.dumps(run.requests[1])
    assert '[Historical tool evidence' not in json.dumps(run.trace)


def test_cloud_sse_blocks_the_recorded_first_round_before_final_answer(wire, monkeypatch):
    events = []
    monkeypatch.setattr(executor, 'append_cc_backend_trace', lambda directory, record: events.append(record))
    wire.chunks = [event(chunk(char)) for char in ECHO] + [event(chunk(finish='stop')), b'data: [DONE]\n\n']
    body, _ = executor._http_post_streaming_openai(wire.url, {'model': 'fixture', 'messages': []}, {}, 3, trace_dir='fixture')
    assert body['choices'][0]['message']['content'] == ECHO
    assert ''.join(row.get('text', '') for row in events if row['kind'] == 'model.text_delta') == ''


@pytest.mark.parametrize('separator', [' ', '', '\t', '\r\n', '\u00a0'])
def test_inline_record_cannot_escape_at_any_stream_split(separator):
    text = '[Historical tool evidence; already executed]' + separator + '{"name":"Bash","input":{}}'
    for split in range(len(text) + 1):
        guard = HistoricalToolEchoFilter()
        visible = guard.feed(text[:split]) + guard.feed(text[split:]) + guard.finish()
        assert visible == '' and guard.blocked


@pytest.mark.parametrize('api', ['openai', 'anthropic', 'openai-responses', 'opencode-responses'])
def test_cloud_repeated_echo_fails_closed_with_bounded_requests(loop, api):
    run = loop(api, [response(api, ECHO), response(api, ECHO)], base_url='https://api.example.test/v1')
    assert run.result['status'] == 'fail' and len(run.requests) == 2
    assert run.calls == [] and '[Historical tool evidence' not in json.dumps(run.result)


@pytest.mark.parametrize('protocol', ['responses', 'google'])
def test_other_streaming_protocols_hold_fragmented_echo(wire, monkeypatch, protocol):
    from factory.core import openai_responses, google_generate_content
    events = []
    payload = {'model': 'fixture', 'messages': [{'role': 'user', 'content': 'Current task'}], 'tools': []}
    if protocol == 'responses':
        monkeypatch.setattr(openai_responses, 'append_cc_backend_trace', lambda directory, row: events.append(row))
        wire.chunks = [event({'type': 'response.output_text.delta', 'delta': char}) for char in ECHO]
        wire.chunks.append(event(completed([message(ECHO)])))
        body, _ = openai_responses.post(wire.url, payload, {}, 3, trace_dir='fixture')
    else:
        from factory.core import cc_backend_trace
        monkeypatch.setattr(cc_backend_trace, 'append_cc_backend_trace', lambda directory, row: events.append(row))
        wire.chunks = [event({'candidates': [{'content': {'parts': [{'text': char}]}}]}) for char in ECHO]
        wire.chunks.append(event({'candidates': [{'finishReason': 'STOP'}]}))
        body, _ = google_generate_content.post_stream(wire.url, payload, {}, 3, trace_dir='fixture')
    assert 'error' not in body
    assert ''.join(row.get('text', '') for row in body['content'] if row.get('type') == 'text') == ECHO
    assert ''.join(row.get('text', '') for row in events if row['kind'] == 'model.text_delta') == ''


class TestMobileHistoryBoundaries(EventsTestCase):
    def test_old_round_and_live_fragments_are_hidden_for_parent_and_child(self):
        live = []
        journaler = self._journaler()
        journaler.add_live_hook(live.append)
        for child in (False, True):
            extra = {'subagent_id': 'fixture-child', 'agent_session_id': 'agent-fixture'} if child else {}
            self._write(*[{'kind': 'model.text_delta', 'round': 1, 'text': char, **extra} for char in ECHO],
                        {'kind': 'model.round_response', 'round': 1, 'text_full': ECHO, **extra},
                        {'kind': 'model.round_response', 'round': 2, 'text_full': 'Current answer', **extra})
        journaler.poll()
        assert '[Historical tool evidence' not in json.dumps(live)
        assert '[Historical tool evidence' not in json.dumps(self.store.list_events(limit=500))
        assert 'Current answer' in json.dumps(live)

    def test_legacy_replay_is_safe_even_after_reconnect_inside_marker(self):
        first = self.store.append_event(kind='model.text_delta', task_id=self.task_id,
                                       session_id=self.session_id, payload={'round': 1, 'text': ECHO[:20]})
        self.store.append_event(kind='model.text_delta', task_id=self.task_id,
                                session_id=self.session_id, payload={'round': 1, 'text': ECHO[20:]})
        self.store.append_event(kind='model.round_response', task_id=self.task_id,
                                session_id=self.session_id, payload={'round': 1, 'text': ECHO})
        self.store.append_event(kind='task.finish', task_id=self.task_id,
                                session_id=self.session_id, payload={'output_preview': ECHO})
        replay = self.store.list_events(after_seq=first, limit=500)
        assert replay[0]['payload']['text'] == ''
        assert '[Historical tool evidence' not in json.dumps(replay)
        assert len(replay) == 3

    def test_replay_preserves_explicit_examples_and_structured_tool_results(self):
        text = '```text\n' + ECHO + '```\n'
        for char in text:
            self.store.append_event(kind='model.text_delta', task_id=self.task_id,
                                    session_id=self.session_id, payload={'round': 1, 'text': char})
        rows = self.store.list_events(limit=500)
        after = rows[-1]['seq']
        rows += self.store.list_events(after_seq=after, limit=500)
        assert ''.join(row['payload']['text'] for row in rows) == text
        self.store.append_event(kind='tool.result', payload={'context_output': ECHO})
        assert self.store.list_events(after_seq=rows[-1]['seq'])[0]['payload']['context_output'] == ECHO

    def test_approval_resume_reuses_round_number_without_inheriting_blocked_stream(self):
        for kind, payload in [
            ('model.text_delta', {'round': 1, 'text': ECHO}),
            ('model.round_response', {'round': 1, 'text': ECHO}),
            ('task.finish', {'status': 'waiting_approval'}),
            ('model.text_delta', {'round': 1, 'text': 'Resumed answer'}),
        ]:
            last = self.store.append_event(kind=kind, task_id=self.task_id,
                                           session_id=self.session_id, payload=payload)
        assert self.store.list_events()[-1]['payload']['text'] == 'Resumed answer'
        assert self.store.list_events(after_seq=last - 1)[0]['payload']['text'] == 'Resumed answer'


class TestSavedMessages(ServiceTestCase):
    def test_saved_assistant_echo_is_hidden_without_rewriting_user_evidence(self):
        from factory.core.cli_chat import _append_entry
        path = self._write_session(self.session_id)
        _append_entry(path, role='user', text=ECHO, task_id='fixture-user')
        _append_entry(path, role='assistant', text=ECHO, task_id='fixture-assistant')
        messages = self.service.get_session(self.session_id)['messages']
        assert messages[0]['text'] == ECHO
        assert '[Historical tool evidence' not in messages[1]['text']
        saved = [json.loads(line) for line in path.read_text().splitlines()]
        assert saved[-1]['text'] == ECHO


def test_worker_final_result_is_protected_for_cli_backends(worker_factory):
    from factory.core import worker as worker_module
    from factory.core.schema import open_db
    worker = worker_factory()
    with open_db(worker_module.DB_PATH) as conn:
        conn.execute('INSERT INTO tasks(task_id,status,profile,metadata) VALUES(?,?,?,?)',
                     ('fixture-result', 'processing', 'fixture', '{}'))
        conn.commit()
        worker.update_task(conn, 'fixture-result', {'status': 'ok', 'output': {'text': ECHO}})
        assert '[Historical tool evidence' not in worker.get_task(conn, 'fixture-result')['output_text']


def test_cli_replay_and_task_queries_hide_legacy_echo(tmp_path, monkeypatch):
    from factory.core.cli_chat import _load_entries
    from factory.core.cli_tasks import get_task, list_tasks
    from factory.core.runtime import get_db_path
    from factory.core.schema import initialize_database, open_db
    monkeypatch.setenv('IGOREK_FACTORY_HOME', str(tmp_path))
    monkeypatch.setenv('FACTORY_DATA_DIR', str(tmp_path))
    path = tmp_path / 'history.jsonl'
    path.write_text(json.dumps({'role': 'assistant', 'text': ECHO}) + '\n')
    assert '[Historical tool evidence' not in _load_entries(path)[0]['text']
    initialize_database(get_db_path())
    with open_db(get_db_path()) as conn:
        conn.execute('INSERT INTO tasks(task_id,status,profile,output_text,metadata) VALUES(?,?,?,?,?)',
                     ('legacy', 'ok', 'fixture', ECHO, '{}'))
        conn.commit()
    assert '[Historical tool evidence' not in get_task('legacy')['output_text']
    assert '[Historical tool evidence' not in json.dumps(list_tasks())


def test_tui_replay_does_not_commit_old_echo_stream():
    from factory.core.tui.trace_reducer import TraceEventReducer
    reducer = TraceEventReducer()
    for char in ECHO:
        reducer.apply({'kind': 'model.text_delta', 'round': 1, 'text': char})
    reducer.apply({'kind': 'model.round_response', 'round': 1, 'text_full': ECHO})
    reducer.apply({'kind': 'task.finish', 'status': 'ok', 'output_preview': ECHO})
    text = '\n'.join(line for cell in reducer.widget.committed_cells for line in cell.display_lines(120))
    assert '[Historical tool evidence' not in text


def test_poisoned_assistant_history_is_not_sent_back_as_an_answer_example():
    from factory.core.session_history import build_working_history
    records = build_working_history([
        {'role': 'user', 'task_id': 'earlier', 'text': 'Earlier question'},
        {'role': 'assistant', 'task_id': 'earlier', 'text': ECHO},
    ], [{'kind': 'model.round_response', 'task_id': 'earlier', 'seq': 1,
         'payload': {'round': 1, 'text': ECHO}}])
    assert '[Historical tool evidence' not in json.dumps(records)
