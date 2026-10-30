"""Local history echoes must not reach visible streams or tool execution."""
import json
from pathlib import Path
from unittest.mock import Mock

import pytest

from factory.core import cc_backend_executor as executor
from factory.core.model_backend_capabilities import ModelBackendCapabilities
from test_cc_stream_wire_contracts import wire, event, chunk  # noqa: F401
from test_executor_tool_loop_edges import loop  # noqa: F401
from test_executor_workflow_guards import response


FIXTURE = Path(__file__).parent / 'fixtures/tool_salvage/historical_tool_evidence_echo.txt'
ECHO = FIXTURE.read_text()


@pytest.fixture(autouse=True)
def confirmed_llama_fixture(monkeypatch):
    from factory.core.llama_image_input import LlamaImageInput
    def probe(policy):
        policy.backend = "llama_cpp"
        return None
    monkeypatch.setattr(LlamaImageInput, "_probe", probe)


def test_original_echo_is_filtered_before_any_visible_sse_frame(wire, monkeypatch):
    wire.url = wire.url.split("?", 1)[0]
    events = []
    monkeypatch.setattr(executor, 'append_cc_backend_trace', lambda directory, record: events.append(record))
    wire.chunks = [event(chunk(ECHO[:17])), event(chunk(ECHO[17:43])), event(chunk(ECHO[43:], finish='stop')), b'data: [DONE]\n\n']
    body, _ = executor._http_post_streaming_openai(wire.url, {'model': 'fixture', 'messages': []}, {}, 3, trace_dir='fixture',
        backend_capabilities=ModelBackendCapabilities('llama_cpp', None, wire.url, 'fixture'))
    assert 'error' not in body
    assert body['choices'][0]['message']['content'] == ECHO
    visible = ''.join(record.get('text', '') for record in events if record['kind'] == 'model.text_delta')
    assert visible == ''


@pytest.mark.parametrize('api', ['openai', 'anthropic'])
def test_original_echo_gets_one_correction_before_normal_answer(loop, monkeypatch, api):
    salvage = Mock(side_effect=AssertionError('Historical evidence must not be salvaged'))
    original_salvage = executor.try_salvage_textual_tool_call
    def check_salvage(text, *args):
        if '[Historical tool evidence' in text:
            return salvage(text, *args)
        return original_salvage(text, *args)
    monkeypatch.setattr(executor, 'try_salvage_textual_tool_call', check_salvage)
    run = loop(api, [response(api, ECHO), response(api, 'Continued with the current task')], names=['desktop_browser'])
    assert run.result['status'] == 'ok' and len(run.requests) == 2 and run.calls == []
    visible = [record.get('text_full', '') for record in run.trace if record['kind'] == 'model.round_response']
    assert visible == ['', 'Continued with the current task']
    assert ECHO not in json.dumps(run.requests[1], ensure_ascii=False)
    assert any(record.get('reason') == 'local_tool_history_echo' for record in run.trace)
    salvage.assert_not_called()


@pytest.mark.parametrize('api', ['openai', 'anthropic'])
def test_repeated_echo_is_bounded_and_never_returns_raw_history(loop, api):
    run = loop(api, [response(api, ECHO), response(api, ECHO)], names=['desktop_browser'])
    assert run.result['status'] == 'fail' and run.result['failure_reason'] == 'local_tool_history_echo_repeated'
    assert len(run.requests) == 2 and run.calls == []
    assert '[Historical' not in run.result['output_text']
    assert 'example.test' not in json.dumps(run.trace)


@pytest.mark.parametrize('api', ['openai', 'anthropic', 'openai-responses'])
def test_remote_model_reply_and_request_are_preserved(loop, api):
    run = loop(api, [response(api, ECHO)], base_url='https://api.example.test/v1')
    assert run.result['status'] == 'ok' and run.result['output_text'] == ECHO
    assert len(run.requests) == 1
    assert not any(record.get('reason') == 'local_tool_history_echo' for record in run.trace)


def test_echo_with_native_calls_is_recovered_without_replaying_old_actions(loop):
    tool = {'id': 'old-call', 'name': 'desktop_browser', 'input': {'action': 'open', 'url': 'https://example.test/old'}}
    run = loop('openai', [response('openai', ECHO, tool), response('openai', 'Current answer')], names=['desktop_browser'])
    assert run.result['status'] == 'ok' and run.calls == [] and len(run.requests) == 2


def test_recovery_can_produce_a_fresh_structured_tool_call(loop):
    tool = {'id': 'new-call', 'name': 'desktop_browser', 'input': {'action': 'read'}}
    run = loop('openai', [response('openai', ECHO), response('openai', tool=tool), response('openai', 'Page checked')], names=['desktop_browser'])
    assert run.result['status'] == 'ok' and run.calls == [('desktop_browser', {'action': 'read'})]
    assert len(run.requests) == 3


@pytest.mark.parametrize('text', [
    'Ordinary answer with JSON {"name":"example"}.',
    '`[Historical tool evidence; already executed]` is a marker.',
    '> [Historical tool evidence; already executed]\n> quoted evidence',
    '```json\n' + ECHO + '```\nExplanation.',
    '~~~~text\n' + ECHO + '~~~~\nExplanation.',
    '    ' + ECHO.replace('\n', '\n    '),
    '[Historical tool evidence; already executed] means historical data.',
    '[Historical examples](https://example.test) are documentation.',
])
def test_local_ordinary_answers_and_quoted_examples_are_preserved(loop, text):
    run = loop('openai', [response('openai', text)])
    assert run.result['status'] == 'ok' and run.result['output_text'] == text
    assert len(run.requests) == 1


def test_filter_all_split_boundaries_and_safe_preamble():
    from factory.core.local_history_output import HistoricalToolEchoFilter
    text = 'Checking the current task.\n' + ECHO
    for split in range(len(text) + 1):
        guard = HistoricalToolEchoFilter()
        output = guard.feed(text[:split]) + guard.feed(text[split:]) + guard.finish()
        assert output == 'Checking the current task.\n'
        assert guard.blocked


def test_streaming_normal_text_is_not_buffered_until_completion():
    from factory.core.local_history_output import HistoricalToolEchoFilter
    guard = HistoricalToolEchoFilter()
    assert guard.feed('Visible first sentence.') == 'Visible first sentence.'
    assert guard.feed('\nNext sentence.') == '\nNext sentence.'
    assert guard.finish() == '' and not guard.blocked


def test_protocol_prefix_is_held_during_an_interrupted_stream():
    from factory.core.local_history_output import HistoricalToolEchoFilter
    guard = HistoricalToolEchoFilter()
    assert guard.feed('[Historical tool evi') == ''


def test_canonical_history_producer_still_matches_the_local_guard():
    from factory.core.session_history import build_working_history
    from factory.core.local_history_output import contains_historical_tool_echo
    entries = [{'role': 'user', 'task_id': 'fixture-task', 'text': 'Earlier task'}]
    events = [{'kind': 'tool.result', 'task_id': 'fixture-task', 'seq': 2, 'payload': {
        'tool_use_id': 'fixture-tool', 'tool_name': 'desktop_browser', 'tool_input': {'action': 'read'},
        'context_output': 'Fixture page', 'status': 'done'}}]
    records = build_working_history(entries, events)
    assert records[1]['role'] == 'assistant'
    assert contains_historical_tool_echo(records[1]['content'])


@pytest.mark.parametrize('text', [
    '[Historical tool evidence]\n{"name":"fixture"}',
    '[Historical reasoning evidence]\n{"text":"fixture"}',
    '[Historical tool evidence; already executed]',
])
def test_other_emitted_history_markers_are_detected(text):
    from factory.core.local_history_output import contains_historical_tool_echo
    assert contains_historical_tool_echo(text)


def test_remote_sse_stream_is_preserved_byte_for_byte(wire, monkeypatch):
    import http.client
    from urllib.parse import urlsplit
    port = urlsplit(wire.url).port
    connection = http.client.HTTPConnection
    monkeypatch.setattr(executor.http.client, 'HTTPSConnection', lambda *args, **kwargs: connection('127.0.0.1', port, timeout=3))
    events = []
    monkeypatch.setattr(executor, 'append_cc_backend_trace', lambda directory, record: events.append(record))
    wire.chunks = [event(chunk(ECHO[:10])), event(chunk(ECHO[10:], finish='stop')), b'data: [DONE]\n\n']
    body, _ = executor._http_post_streaming_openai('https://api.example.test/v1/chat/completions',
        {'model': 'fixture', 'messages': []}, {}, 3, trace_dir='fixture')
    assert body['choices'][0]['message']['content'] == ECHO
    assert ''.join(record['text'] for record in events if record['kind'] == 'model.text_delta') == ECHO


@pytest.mark.parametrize('text', [
    '```json\n' + ECHO + '```\nExplanation.',
    '~~~\n' + ECHO + '~~~\nExplanation.',
    '`[Historical tool evidence; already executed]` is a marker.',
    '> [Historical tool evidence; already executed]\n> quoted evidence',
])
def test_quoted_markers_survive_character_by_character_streaming(text):
    from factory.core.local_history_output import HistoricalToolEchoFilter
    guard = HistoricalToolEchoFilter()
    output = ''.join(guard.feed(char) for char in text) + guard.finish()
    assert output == text and not guard.blocked


@pytest.mark.parametrize('url,enabled', [
    ('http://127.0.0.1:8081/v1', True),
    ('http://127.0.0.1:8081/v1/chat/completions?fixture=1', False),
    ('http://[::1]:8081/v1', True),
    ('http://localhost:8081/v1', True),
    ('https://api.example.test/v1', False),
    ('http://192.168.0.171/v1', False),
    ('http://user:secret@localhost/v1', False),
])
def test_guard_is_scoped_to_local_transports(url, enabled):
    from factory.core.local_history_output import local_history_guard_enabled
    from factory.core.model_backend_capabilities import ModelBackendCapabilities
    capabilities = ModelBackendCapabilities('llama_cpp', None, url, 'fixture')
    assert not local_history_guard_enabled(url)
    assert local_history_guard_enabled(url, capabilities=capabilities, model='fixture') is enabled
    assert not local_history_guard_enabled(url, 'openai-responses', capabilities=capabilities, model='fixture')
