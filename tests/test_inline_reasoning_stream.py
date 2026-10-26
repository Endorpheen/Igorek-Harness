"""Provider-neutral inline reasoning must be routed before visible streaming."""
import copy
import json
from pathlib import Path
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from factory.core import cc_backend_executor as executor


FIXTURE = json.loads((Path(__file__).parent / 'fixtures/tool_salvage/inline_think_content.json').read_text())


@pytest.fixture(scope='module')
def server():
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass
        def do_POST(self):
            payload = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            self.send_response(200);self.send_header('Content-Type', 'text/event-stream');self.end_headers()
            for i, delta in enumerate(payload['fixture_deltas']):
                chunk = {'choices': [{'delta': delta}]}
                self.wfile.write(('data: ' + json.dumps(chunk) + '\n\n').encode());self.wfile.flush()
                if payload.get('fixture_pause') and i == 0:
                    self.server.sent.set();self.server.release.wait(5)
            chunk = {'choices': [{'delta': {}, 'finish_reason': payload.get('fixture_finish', 'stop')}]}
            self.wfile.write(('data: ' + json.dumps(chunk) + '\n\ndata: [DONE]\n\n').encode());self.wfile.flush()
    instance = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    instance.daemon_threads = True
    instance.sent, instance.release = threading.Event(), threading.Event()
    thread = threading.Thread(target=instance.serve_forever, daemon=True);thread.start()
    yield instance
    instance.release.set();instance.shutdown();instance.server_close();thread.join(5)


@pytest.fixture
def stream(server, monkeypatch):
    events = []
    monkeypatch.setattr(executor, 'append_cc_backend_trace', lambda directory, event: events.append(event))
    def run(deltas, finish='stop'):
        payload = {'model': 'fixture', 'messages': [], 'fixture_deltas': deltas, 'fixture_finish': finish}
        original = copy.deepcopy(payload)
        body, _ = executor._http_post_streaming_openai('http://127.0.0.1:' + str(server.server_port) + '/v1/chat/completions', payload, {}, 5, trace_dir='fixture')
        assert payload == original and 'error' not in body
        return body['choices'][0]['message'], events
    return run


def channel(events, kind):
    return ''.join(x.get('text', '') for x in events if x['kind'] == kind)


def test_actual_shape_unclosed_thinking_never_becomes_visible_answer(stream):
    message, events = stream(FIXTURE['deltas'], FIXTURE['finish_reason'])
    assert (message['content'] or '') == FIXTURE['expected_answer']
    assert message['reasoning_content'] == FIXTURE['expected_reasoning']
    assert channel(events, 'model.text_delta') == ''
    assert channel(events, 'model.reasoning_delta') == FIXTURE['expected_reasoning']


@pytest.mark.parametrize('split', range(1, len('<think>private fixture</think>Visible answer.')))
def test_tag_boundaries_do_not_change_reasoning_or_answer(stream, split):
    text = '<think>private fixture</think>Visible answer.'
    message, events = stream([{'content': text[:split]}, {'content': text[split:]}])
    assert message['content'] == 'Visible answer.'
    assert message['reasoning_content'] == 'private fixture'
    assert channel(events, 'model.text_delta') == 'Visible answer.'
    assert channel(events, 'model.reasoning_delta') == 'private fixture'


@pytest.mark.parametrize('text', [
    'Ordinary answer without reasoning.',
    '```xml\n<think>literal example</think>\n```',
    'An inline `<think>literal</think>` example.',
    'A double ``<think>literal</think>`` example.',
    '~~~xml\n<think>literal</think>\n~~~',
    r'An escaped \<think> literal tag.',
    'An incomplete literal <thi',
    'A mathematical x < threshold.',
])
def test_literal_tags_and_normal_answers_are_preserved(stream, text):
    message, events = stream([{'content': char} for char in text])
    assert message['content'] == text
    assert not message.get('reasoning_content')
    assert channel(events, 'model.text_delta') == text


def test_native_reasoning_and_tool_call_shapes_remain_compatible(stream):
    deltas = [{'reasoning_content': 'Native. '}, {'content': '<think>Tagged.</think>Ready.'}, {'tool_calls': [{'index': 0, 'id': 'call-one', 'type': 'function', 'function': {'name': 'Read', 'arguments': '{"path":"fixture"}'}}]}]
    message, events = stream(deltas, 'tool_calls')
    assert message['content'] == 'Ready.' and message['reasoning_content'] == 'Native. Tagged.'
    assert message['tool_calls'][0]['function']['name'] == 'Read'
    assert json.loads(message['tool_calls'][0]['function']['arguments']) == {'path': 'fixture'}


def test_repeated_and_empty_tags_do_not_lose_visible_text(stream):
    text = 'Before.<think/>Between.<THINK data-fixture="x">First.</THINK><think>Second.</think>After.'
    message, events = stream([{'content': text}])
    assert message['content'] == 'Before.Between.After.'
    assert message['reasoning_content'] == 'First.Second.'


def test_nonstream_unclosed_reasoning_is_not_promoted_to_final_text():
    blocks = executor._normalize_tagged_reasoning_blocks([{'type': 'text', 'text': '<think>Fixture reasoning without a closing tag'}])
    assert blocks == [{'type': '__reasoning', 'text': 'Fixture reasoning without a closing tag'}]


def test_live_reasoning_is_routed_before_provider_finishes(server, monkeypatch):
    events, results, failures = [], [], []
    received = threading.Event();server.sent.clear();server.release.clear()
    def trace(directory, event):
        events.append(event);received.set()
    monkeypatch.setattr(executor, 'append_cc_backend_trace', trace)
    def run():
        try:
            results.append(executor._http_post_streaming_openai('http://127.0.0.1:' + str(server.server_port) + '/v1/chat/completions', {'model': 'fixture', 'messages': [], 'fixture_deltas': [{'content': '<think>Fixture live reasoning'}, {'content': '</think>Final.'}], 'fixture_pause': True}, {}, 5, trace_dir='fixture'))
        except BaseException as error: failures.append(error)
    thread = threading.Thread(target=run, daemon=True);thread.start()
    try:
        assert server.sent.wait(3) and received.wait(3)
        assert channel(events, 'model.text_delta') == ''
        assert channel(events, 'model.reasoning_delta') == 'Fixture live reasoning'
    finally:
        server.release.set();thread.join(7)
    assert not thread.is_alive() and not failures and results
    assert results[0][0]['choices'][0]['message']['content'] == 'Final.'


@pytest.mark.parametrize('text', ['A literal </think> without an opening tag.', '<thinking>is not a protocol tag</thinking>', '<think' + ' ' * 300 + 'literal>', 'A tag-shaped substring: <thinker>.'])
def test_nonprotocol_tag_shapes_are_visible(stream, text):
    message, events = stream([{'content': char} for char in text])
    assert message['content'] == text and not message.get('reasoning_content')


def test_normalization_preserves_separate_provider_text_blocks():
    blocks = [{'type': 'text', 'text': 'First paragraph.'}, {'type': 'text', 'text': 'Second paragraph.'}]
    assert executor._normalize_tagged_reasoning_blocks(blocks) == blocks


def test_long_nonstream_thinking_and_answer_are_preserved_without_truncation():
    thought, answer = 'fixture reasoning ' * 10000, 'visible fixture ' * 10000
    blocks = executor._normalize_tagged_reasoning_blocks([{'type': 'text', 'text': '<think>' + thought + '</think>' + answer}])
    assert blocks == [{'type': '__reasoning', 'text': thought.strip()}, {'type': 'text', 'text': answer}]
