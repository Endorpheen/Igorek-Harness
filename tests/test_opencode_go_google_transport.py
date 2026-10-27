import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
from factory.core import cc_backend_executor as executor
from factory.core.cc_backend_executor import _http_post
from factory.core.google_generate_content import build_request, post_stream, to_anthropic


def test_google_tool_round_trip_preserves_thought_signature_and_parallel_calls():
    parts = [{'functionCall': {'name': 'Read', 'args': {'path': 'a'}}, 'thoughtSignature': 'signature'},
             {'functionCall': {'name': 'Read', 'args': {'path': 'b'}}}]
    response = to_anthropic({'candidates': [{'content': {'parts': parts}, 'finishReason': 'STOP'}]}, {'Read'})
    calls = [block for block in response['content'] if block['type'] == 'tool_use']
    messages = [{'role': 'user', 'content': 'Read both'}, {'role': 'assistant', 'content': response['content']},
                {'role': 'user', 'content': [{'type': 'tool_result', 'tool_use_id': call['id'], 'content': 'ok'} for call in calls]}]
    request = build_request({'model': 'fixture', 'messages': messages, 'tools': [
        {'name': 'Read', 'input_schema': {'type': 'object', 'properties': {}}}]})
    assert request['contents'][1]['parts'] == parts
    assert [p['functionResponse']['name'] for p in request['contents'][2]['parts']] == ['Read', 'Read']
    assert request['tools'][0]['functionDeclarations'][0]['name'] == 'Read'


def test_google_rejects_tool_calls_from_truncated_response():
    response = {'candidates': [{'content': {'parts': [{'functionCall': {'name': 'Read', 'args': {}}}]},
                                'finishReason': 'MAX_TOKENS'}]}
    with pytest.raises(ValueError, match='google_incomplete_tool_call'):
        to_anthropic(response, {'Read'})


def test_google_http_transport_sends_and_receives_tool_calls():
    received = []
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            received.append((self.path, json.loads(self.rfile.read(int(self.headers['Content-Length'])))))
            data = json.dumps({'candidates': [{'content': {'parts': [{'functionCall': {'name': 'Read', 'args': {'path': 'a'}},
                'thoughtSignature': 'fixture'}]}, 'finishReason': 'STOP'}], 'usageMetadata': {'promptTokenCount': 3, 'candidatesTokenCount': 2}}).encode()
            self.send_response(200)
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        def log_message(self, *args):
            pass
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        url = f'http://127.0.0.1:{server.server_port}/models/fixture:generateContent'
        result, _ = _http_post(url, {'model': 'fixture', 'system': 'System', 'messages': [{'role': 'user', 'content': 'Read'}],
            'tools': [{'name': 'Read', 'input_schema': {'type': 'object'}}]}, {'__igorek_google_mode__': True}, 5)
        assert received[0][1]['systemInstruction']['parts'][0]['text'] == 'System'
        assert result['content'][0]['name'] == 'Read'
        assert result['usage'] == {'input_tokens': 3, 'output_tokens': 2}
    finally:
        server.shutdown()
        server.server_close()
        thread.join(3)


def test_google_executor_round_trip_calls_real_read_and_replays_signature(tmp_path, monkeypatch):
    from factory.core.cc_backend_tools import get_tool_schemas
    path = tmp_path / 'input.txt'
    path.write_text('fixture-evidence', encoding='utf-8')
    requests = []
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            requests.append(json.loads(self.rfile.read(int(self.headers['Content-Length']))))
            if len(requests) == 1:
                parts = [{'functionCall': {'name': 'Read', 'args': {'file_path': str(path)}}, 'thoughtSignature': 'fixture-signature'}]
            else:
                parts = [{'text': 'fixture-evidence'}]
            data = b'data: ' + json.dumps({'candidates': [{'content': {'parts': parts}, 'finishReason': 'STOP'}]}).encode() + b'\n\ndata: [DONE]\n\n'
            self.send_response(200)
            self.send_header('Content-Type', 'text/event-stream')
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        def log_message(self, *args):
            pass
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    monkeypatch.setattr(executor, '_resolve_backend_context_window', lambda **_: (128000, 'fixture'))
    monkeypatch.setattr(executor, 'is_interrupt_requested', lambda: False)
    tool = next(item for item in get_tool_schemas() if item['name'] == 'Read')
    try:
        result = executor.execute_cc_backend_task(base_url=f'http://127.0.0.1:{server.server_port}/v1',
            model='fixture', system_prompt='Fixture', user_message='Read input.txt', workdir=str(tmp_path),
            api_format='google', enabled_tools=[tool], checkpoint_config={'enabled': False}, timeout_s=5)
        assert result['status'] == 'ok', result
        assert result['tool_calls_count'] == 1 and 'fixture-evidence' in result['output_text']
        assert requests[1]['contents'][1]['parts'][0]['thoughtSignature'] == 'fixture-signature'
        assert 'fixture-evidence' in json.dumps(requests[1]['contents'][2])
    finally:
        server.shutdown()
        server.server_close()
        thread.join(3)


def test_google_incomplete_stream_never_delivers_a_tool_call():
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers['Content-Length']))
            data = b'data: ' + json.dumps({'candidates': [{'content': {'parts': [
                {'functionCall': {'name': 'Read', 'args': {}}}]}}]}).encode() + b'\n\n'
            self.send_response(200)
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        def log_message(self, *args):
            pass
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        result, _ = post_stream(f'http://127.0.0.1:{server.server_port}/models/fixture:generateContent',
            {'messages': [{'role': 'user', 'content': 'hello'}], 'tools': [{'name': 'Read'}]}, {}, 5)
        assert 'error' in result and 'content' not in result
    finally:
        server.shutdown()
        server.server_close()
        thread.join(3)


def test_google_stream_cancellation_interrupts_a_stalled_connection():
    ready, release, cancel = threading.Event(), threading.Event(), threading.Event()
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers['Content-Length']))
            self.send_response(200)
            self.send_header('Content-Type', 'text/event-stream')
            self.end_headers()
            self.wfile.write(b': ready\n\n')
            self.wfile.flush()
            ready.set()
            release.wait(5)
        def log_message(self, *args):
            pass
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    results = []
    worker = threading.Thread(target=lambda: results.append(post_stream(
        f'http://127.0.0.1:{server.server_port}/models/fixture:generateContent',
        {'messages': [{'role': 'user', 'content': 'hello'}]}, {}, 20, cancel_check=cancel.is_set)))
    worker.start()
    try:
        assert ready.wait(3)
        cancel.set()
        worker.join(3)
        assert not worker.is_alive()
        assert results[0][0].get('__interrupted__') is True
    finally:
        release.set()
        worker.join(5)
        server.shutdown()
        server.server_close()
        server_thread.join(3)
