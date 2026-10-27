import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest
from factory.core import openai_responses as transport
from factory.core import cc_backend_executor as executor


def message(text):
    return {'type': 'message', 'role': 'assistant', 'content': [{'type': 'output_text', 'text': text}]}


def function(name='Read', args=None, **fields):
    return {'type': 'function_call', 'namespace': 'igorek', 'call_id': 'call-fixture',
            'name': name, 'arguments': json.dumps(args or {}), 'status': 'completed', **fields}


def completed(output):
    return {'type': 'response.completed', 'response': {'status': 'completed', 'id': 'response-fixture',
        'model': 'fixture-model', 'output': output, 'usage': {'input_tokens': 10, 'output_tokens': 5}}}


@pytest.fixture
def server():
    calls, handlers = [], []
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args): pass
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            calls.append((self.path, body, dict(self.headers)))
            events = handlers[0](body, len(calls))
            if events is None:
                self.close_connection = True
                return
            wire = events if isinstance(events, dict) else {'events': events}
            self.send_response(200)
            if wire.get('content_type', 'text/event-stream') is not None:
                self.send_header('Content-Type', wire.get('content_type', 'text/event-stream'))
            self.send_header('x-request-id', 'req_fixture12345678')
            self.end_headers()
            if 'raw' in wire:
                self.wfile.write(wire['raw'])
            for event in wire.get('events', []):
                self.wfile.write(b'data: ' + json.dumps(event).encode() + b'\n\n')
                self.wfile.flush()
    value = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=value.serve_forever, daemon=True)
    thread.start()
    yield f'http://127.0.0.1:{value.server_port}/v1', calls, handlers
    value.shutdown(); value.server_close(); thread.join(2)


def test_payload_preserves_images_tool_ids_history_and_omits_unsupported_fields():
    raw = [{'type': 'reasoning', 'id': 'rs-fixture', 'encrypted_content': 'opaque-fixture', 'summary': []},
           function(args={'file_path': 'fixture.txt'})]
    payload = {'model': 'fixture', 'system': 'trusted system', 'max_tokens': 50, 'temperature': .5,
        'messages': [
            {'role': 'user', 'content': [{'type': 'text', 'text': 'question'}, {'type': 'image',
                'source': {'type': 'base64', 'media_type': 'image/png', 'data': 'fixture'}}]},
            {'role': 'assistant', 'content': [{'type': '__responses_output', 'output': raw},
                {'type': 'tool_use', 'id': 'call-fixture', 'name': 'Read', 'input': {}}]},
            {'role': 'user', 'content': [{'type': 'tool_result', 'tool_use_id': 'call-fixture', 'content': 'real result'}]}],
        'tools': [{'name': 'Read', 'input_schema': {'type': 'object'}}]}
    result = transport.build_request(payload)
    assert result['store'] is False and result['stream'] is True
    assert result['instructions'] == 'trusted system'
    assert 'max_tokens' not in result and 'temperature' not in result and 'messages' not in result
    assert result['input'][0]['content'][1]['image_url'] == 'data:image/png;base64,fixture'
    assert result['input'][1:3] == raw
    assert len([x for x in result['input'] if x.get('type') == 'function_call']) == 1
    assert result['input'][-1] == {'type': 'function_call_output', 'call_id': 'call-fixture', 'output': 'real result'}
    assert result['tools'][0]['type'] == 'namespace'
    assert result['tools'][0]['tools'][0]['name'] == 'Read'


def test_generic_responses_uses_flat_tools_and_keeps_internal_marker_off_wire(server):
    base, calls, handlers = server
    handlers.append(lambda *_: [completed([message('ok')])])
    payload = {'model': 'fixture', 'messages': [{'role': 'user', 'content': 'hello'}],
               'tools': [{'name': 'Read', 'input_schema': {'type': 'object'}}]}
    result, _ = transport.post(base + '/responses', payload,
                               {'__igorek_generic_responses__': True, 'Authorization': 'Bearer fixture'}, 5)
    assert result['content'][0]['text'] == 'ok'
    assert calls[0][1]['tools'][0]['type'] == 'function'
    assert 'include' not in calls[0][1]
    assert '__igorek_generic_responses__' not in str(calls[0][2])
    assert 'OpenCode' in transport._failure({'code': 'invalid_api_key'}, 401, generic=True)['error']
    assert 'OpenAI' in transport._failure({'code': 'invalid_api_key'}, 401)['error']
    assert 'OpenCode' in transport._failure({'code': []}, generic=True)['error']


def test_generic_responses_executor_calls_real_read(server, tmp_path, monkeypatch):
    from factory.core.cc_backend_tools import get_tool_schemas
    base, calls, handlers = server
    path = tmp_path / 'input.txt'
    path.write_text('go-fixture-evidence')
    def reply(payload, index):
        if index == 1:
            call = function(args={'file_path': str(path)})
            call.pop('namespace')
            return [completed([call])]
        results = [item for item in payload['input'] if item.get('type') == 'function_call_output']
        assert len(results) == 1 and 'go-fixture-evidence' in results[0]['output']
        return [completed([message('go-fixture-evidence')])]
    handlers.append(reply)
    monkeypatch.setattr(executor, '_resolve_backend_context_window', lambda **_: (128000, 'fixture'))
    monkeypatch.setattr(executor, 'is_interrupt_requested', lambda: False)
    tool = next(item for item in get_tool_schemas() if item['name'] == 'Read')
    result = executor.execute_cc_backend_task(base_url=base, model='fixture', system_prompt='Fixture',
        user_message='Read input.txt', workdir=str(tmp_path), api_format='opencode-responses',
        enabled_tools=[tool], request_headers={'Authorization': 'Bearer fixture'}, checkpoint_config={'enabled': False})
    assert result['status'] == 'ok', result
    assert result['tool_calls_count'] == 1 and 'go-fixture-evidence' in result['output_text']
    assert len(calls) == 2 and calls[0][1]['tools'][0]['type'] == 'function'
    assert all('__igorek_generic_responses__' not in str(headers) for _, _, headers in calls)


def test_bridge_worker_question_round_trip_reaches_model(server, tmp_path, monkeypatch):
    from test_worker_isolation_seam import WorkerSeamHarness
    from factory.core.cc_backend_tools import get_tool_schemas
    from factory.core.mobile_bridge.service import MobileService
    from factory.core.cli_tasks import create_task
    from factory.core.runtime import get_db_path
    from factory.core.schema import initialize_database
    from factory.core import worker as worker_module
    from unittest.mock import patch
    from concurrent.futures import ThreadPoolExecutor
    monkeypatch.setenv('IGOREK_FACTORY_HOME', str(tmp_path / 'runtime-data'))
    initialize_database(get_db_path())
    base, calls, handlers = server
    questions = [
        {'id': 'color', 'header': 'Цвет', 'question': 'Выбери цвет', 'options': [{'label': 'Зелёный'}, {'label': 'Синий'}]},
        {'id': 'tools', 'header': 'Инструменты', 'question': 'Выбери несколько', 'is_multiselect': True,
         'options': [{'label': 'Браузер'}, {'label': 'Файлы'}, {'label': 'Терминал'}]},
        {'id': 'note', 'header': 'Заметка', 'question': 'Твоя заметка'},
    ]
    answers = {'answers': {'color': ['Зелёный'], 'tools': ['Браузер', 'Файлы'], 'note': ['Точная заметка 847']}}
    def wire(payload, index):
        if index == 1:
            assert any(tool['name'] == 'request_user_input' for namespace in payload['tools'] for tool in namespace.get('tools', []))
            return [completed([function(name='request_user_input', args={'title': 'Проверка формы', 'questions': questions})])]
        outputs = [item for item in payload['input'] if item.get('type') == 'function_call_output']
        assert len(outputs) == 1 and json.loads(outputs[0]['output']) == answers
        return [completed([message('Точная заметка 847 · Зелёный · Браузер, Файлы')])]
    handlers.append(wire)
    monkeypatch.setattr(executor, '_resolve_backend_context_window', lambda **_: (128000, 'fixture'))
    monkeypatch.setattr(executor, 'is_interrupt_requested', lambda: False)
    schema = next(tool for tool in get_tool_schemas() if tool['name'] == 'request_user_input')
    harness = WorkerSeamHarness(); harness.setUp()
    try:
        created = create_task(text='Покажи форму', profile=harness.worker.profile,
            metadata={'source': 'mobile_bridge', 'platform': 'android', 'session': 'fixture-session'})
        harness.task.update(task_id=created['task_id'], limits_timeout_s=10,
            metadata={'source': 'mobile_bridge', 'platform': 'android', 'session': 'fixture-session'})
        harness.worker.config.update(base_url=base, api_format='openai-responses', provider='openai', checkpoints={'enabled': False})
        real_executor = executor.execute_cc_backend_task
        harness.executor.side_effect = lambda **kwargs: real_executor(**kwargs)
        service = MobileService(profile=harness.worker.profile, workdir=str(harness.workdir))
        with harness.seam_env(isolation_env='off'), patch.object(worker_module, 'get_tool_schemas', return_value=[schema]), ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(harness.run_worker)
            deadline = time.monotonic() + 8
            approvals = []
            while time.monotonic() < deadline and not approvals:
                approvals = service.list_approvals(session_id='fixture-session')
                if not approvals: time.sleep(.02)
            assert len(approvals) == 1
            form = approvals[0]
            assert form['kind'] == 'user_input' and form['input']['questions'] == questions
            service.resolve_user_input(created['task_id'], form['request_id'], response=answers)
            result = future.result(timeout=8)
        assert result['status'] == 'ok', result
        assert 'Точная заметка 847' in result['output']['text']
        assert len(calls) == 2 and service.list_approvals(session_id='fixture-session') == []
    finally:
        harness.tearDown()


@pytest.mark.parametrize('output', [[function(namespace='other')], [function(name='unknown')],
    [function(arguments='[]')], [function(), function()], [function(status='incomplete')]])
def test_invalid_completed_tool_contract_is_never_executable(output):
    with pytest.raises(ValueError):
        transport.to_anthropic(completed(output)['response'], {'Read'})


@pytest.mark.parametrize('events,code', [
    ([{'type': 'response.output_text.delta', 'delta': 'partial'}], 'responses_incomplete_stream'),
    ([{'type': 'response.output_item.done', 'item': function()}], 'responses_incomplete_stream'),
    ([{'type': 'response.failed', 'response': {'error': {'code': 'subscription_sharing_usage_limit_exceeded', 'message': 'fixture-secret'}}}], 'subscription_sharing_usage_limit_exceeded'),
    ([{'type': 'response.incomplete', 'response': {'status': 'incomplete'}}], 'responses_failed')])
def test_stream_failure_cannot_become_success_or_execute_partial_tools(server, events, code):
    base, calls, handlers = server
    handlers.append(lambda *_: events)
    body, _ = transport.post(base + '/responses', {'model': 'fixture', 'messages': [{'role': 'user', 'content': 'test'}]}, {}, 3)
    assert body['error_code'] == code and not body.get('content')
    assert 'fixture-secret' not in str(body)
    assert len(calls) == 1


@pytest.mark.parametrize('content_type', ['text/event-stream', 'application/json'])
@pytest.mark.parametrize('compact', [False, True])
def test_existing_executor_runs_real_read_and_returns_result_in_next_round(server, tmp_path, monkeypatch, content_type, compact):
    base, calls, handlers = server
    path = tmp_path / 'input.txt'
    path.write_text('fixture-evidence-7351')
    reason = {'type': 'reasoning', 'id': 'rs-fixture', 'encrypted_content': 'opaque-fixture', 'summary': []}
    def reply(payload, index):
        if index == 1:
            return [{'type': 'response.output_text.delta', 'delta': 'Читаю файл.'},
                    completed([reason, function(args={'file_path': str(path)})])]
        results = [x for x in payload['input'] if x.get('type') == 'function_call_output']
        assert len(results) == 1 and 'fixture-evidence-7351' in results[0]['output']
        assert reason in payload['input']
        assert len([x for x in payload['input'] if x.get('type') == 'function_call']) == 1
        return [completed([message('В файле: fixture-evidence-7351')])]
    def wire(payload, index):
        events = reply(payload, index)
        if compact:
            output = events[-1]['response'].pop('output')
            events = events[:-1] + [{'type': 'response.output_item.done', 'output_index': i, 'item': item}
                                    for i, item in enumerate(output)] + [events[-1]]
        return {'events': events, 'content_type': content_type}
    handlers.append(wire)
    monkeypatch.setattr(executor, '_resolve_backend_context_window', lambda **_: (128000, 'fixture'))
    monkeypatch.setattr(executor, 'is_interrupt_requested', lambda: False)
    from factory.core.tool_registry import registry
    from factory.core.cc_backend_tools import get_tool_schemas
    schemas = get_tool_schemas()
    tool = next(t for t in schemas if t['name'] == 'Read')
    auth_calls = []
    def headers():
        auth_calls.append(len(auth_calls) + 1)
        return {'Authorization': 'Bearer fixture-token-' + str(len(auth_calls))}
    result = executor.execute_cc_backend_task(base_url=base, model='fixture-model', system_prompt='Test',
        user_message='Прочитай input.txt', workdir=str(tmp_path), api_format='openai-responses',
        enabled_tools=[tool], request_headers_provider=headers, checkpoint_config={'enabled': False})
    assert result['status'] == 'ok', result
    assert result['tool_calls_count'] == 1 and 'fixture-evidence-7351' in result['output_text']
    assert len(calls) == 2
    assert calls[0][0] == '/v1/responses' and calls[1][0] == '/v1/responses'
    assert [x[2]['Authorization'] for x in calls] == ['Bearer fixture-token-1', 'Bearer fixture-token-2']
    trace = (tmp_path / 'cc_backend_trace.jsonl').read_text()
    assert 'model.text_delta' in trace and 'tool.result' in trace


@pytest.mark.parametrize('failures', [1, 3])
def test_network_failure_after_tool_retries_same_round_without_reexecuting_tool(server, tmp_path, monkeypatch, failures):
    from factory.core.cc_backend_tools import get_tool_schemas
    base, calls, handlers = server
    path = tmp_path / 'input.txt'
    path.write_text('retry-evidence-7351')

    def reply(payload, index):
        if index == 1:
            return [completed([function(args={'file_path': str(path)})])]
        outputs = [item for item in payload['input'] if item.get('type') == 'function_call_output']
        assert len(outputs) == 1 and 'retry-evidence-7351' in outputs[0]['output']
        if index <= failures + 1:
            return None
        return [completed([message('retry-evidence-7351')])]

    handlers.append(reply)
    monkeypatch.setattr(executor, '_resolve_backend_context_window', lambda **_: (128000, 'fixture'))
    monkeypatch.setattr(executor, 'is_interrupt_requested', lambda: False)
    tool = next(item for item in get_tool_schemas() if item['name'] == 'Read')
    result = executor.execute_cc_backend_task(base_url=base, model='fixture', system_prompt='Fixture',
        user_message='Read input.txt', workdir=str(tmp_path), api_format='openai-responses',
        enabled_tools=[tool], checkpoint_config={'enabled': False}, timeout_s=3)
    events = [json.loads(line) for line in (tmp_path / 'cc_backend_trace.jsonl').read_text().splitlines()]
    retries = [event for event in events if event['kind'] == 'network.retry']
    assert len(retries) == min(failures, 2)
    assert all(event['round'] == 2 for event in retries)
    assert all(event['error_diagnostics'] == {'error_code': 'responses_network', 'transport_phase': 'connect'}
               for event in retries)
    assert len(calls) == 1 + min(failures + 1, 3)
    assert all(call[1] == calls[1][1] for call in calls[2:])
    assert result['tool_calls_count'] == 1
    assert len([event for event in events if event['kind'] == 'tool.result']) == 1
    assert result['status'] == ('ok' if failures == 1 else 'fail')
    if failures == 1:
        assert result['output_text'] == 'retry-evidence-7351'
    else:
        assert events[-1]['reason'] == 'http_error'
        assert events[-1]['error_diagnostics']['error_code'] == 'responses_network'


@pytest.mark.parametrize('code,retry', [
    ('responses_network', True), ('responses_timeout', True),
    ('responses_tls', False), ('authentication', False),
    ('subscription_sharing_usage_limit_exceeded', False),
    ('responses_invalid_response', False), ('responses_incomplete_stream', False),
    ('responses_failed', False),
])
def test_executor_uses_structured_failure_code_before_error_text(tmp_path, monkeypatch, code, retry):
    calls = []

    def post(*args, **kwargs):
        calls.append(1)
        if len(calls) == 1:
            return {'error': 'Connection timeout' if not retry else 'Проверьте соединение.',
                    'error_code': code, 'transport_phase': 'connect'}, 1
        return {'content': [{'type': 'text', 'text': 'Recovered.'}],
                'usage': {'input_tokens': 1, 'output_tokens': 1}}, 1

    monkeypatch.setattr(transport, 'post', post)
    monkeypatch.setattr(executor, '_resolve_backend_context_window', lambda **_: (128000, 'fixture'))
    monkeypatch.setattr(executor, 'is_interrupt_requested', lambda: False)
    result = executor.execute_cc_backend_task(base_url='http://127.0.0.1:1/v1', model='fixture',
        system_prompt='Fixture', user_message='Hello', workdir=str(tmp_path), api_format='openai-responses',
        enabled_tools=[], checkpoint_config={'enabled': False}, timeout_s=3)
    assert len(calls) == (2 if retry else 1)
    assert result['status'] == ('ok' if retry else 'fail')


@pytest.mark.parametrize('content_type', ['text/event-stream', 'application/json', None,
                                       'Text/Event-Stream; charset=UTF-8', 'application/octet-stream'])
def test_real_single_request_probe_accepts_completed_text(server, monkeypatch, content_type):
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'desktop'))
    from provider_probe import check_connection
    from provider_setup import profile_config
    base, calls, handlers = server
    def reply(payload, index):
        assert index == 1
        assert 'tools' not in payload and 'tool_choice' not in payload
        assert len(payload['input']) == 1
        return {'events': [completed([message('Привет! Модель на связи.')])], 'content_type': content_type}
    handlers.append(reply)
    original = transport.post
    monkeypatch.setattr(transport, 'post', lambda url, *a, **k: original(base + '/responses', *a, **k))
    config = profile_config('chatgpt', 'fixture-model', 'https://api.openai.com/v1', 'openai-responses')
    result = check_connection(config, 'fixture-access')
    assert result['status'] == 'verified', result
    assert len(calls) == 1 and 'fixture-access' not in str(result)


def test_responses_cancel_closes_a_stalled_socket():
    ready, release, cancel = threading.Event(), threading.Event(), threading.Event()
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args): pass
        def do_POST(self):
            self.rfile.read(int(self.headers['Content-Length']))
            self.send_response(200); self.send_header('Content-Type', 'text/event-stream'); self.end_headers()
            self.wfile.flush(); ready.set(); release.wait(3)
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    result = []
    worker = threading.Thread(target=lambda: result.append(transport.post(
        f'http://127.0.0.1:{server.server_port}/responses', {'model': 'm', 'messages': []}, {}, 10, cancel_check=cancel.is_set)))
    try:
        worker.start(); assert ready.wait(2); cancel.set(); worker.join(1.5)
        assert not worker.is_alive() and result[0][0]['__interrupted__'] is True
    finally:
        release.set(); worker.join(2); server.shutdown(); server.server_close(); thread.join(2)



def test_forced_function_is_the_only_available_tool_and_uses_documented_required_choice():
    result = transport.build_request({'model':'fixture','messages':[],
        'tools':[{'name':'Read','input_schema':{'type':'object'}}, {'name':'Write','input_schema':{'type':'object'}}],
        'tool_choice':{'type':'function','function':{'name':'Read'}}})
    assert result['tool_choice'] == 'required'
    assert [tool['name'] for tool in result['tools'][0]['tools']] == ['Read']
    with pytest.raises(ValueError, match='invalid_tool_choice'):
        transport.build_request({'model':'fixture','messages':[], 'tools':[{'name':'Read'}],
            'tool_choice':{'type':'function','function':{'name':'NotAvailable'}}})


def test_http_error_diagnostics_reach_probe_without_provider_text_or_credentials(monkeypatch):
    import sys
    sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'desktop'))
    from provider_probe import check_connection
    from provider_setup import profile_config
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args): pass
        def do_POST(self):
            self.rfile.read(int(self.headers['Content-Length']))
            self.send_response(400)
            self.send_header('Content-Type','application/json')
            self.send_header('x-request-id','req_fixture12345678')
            self.end_headers()
            self.wfile.write(json.dumps({'error':{'type':'invalid_request_error','code':'unknown_parameter',
                'param':'tool_choice.namespace','message':'fixture-secret-must-not-be-shown'}}).encode())
    value=ThreadingHTTPServer(('127.0.0.1',0),Handler)
    thread=threading.Thread(target=value.serve_forever,daemon=True);thread.start()
    original=transport.post
    monkeypatch.setattr(transport,'post',lambda url,*a,**k: original(f'http://127.0.0.1:{value.server_port}/responses',*a,**k))
    try:
        result=check_connection(profile_config('codexpro','fixture','https://api.openai.com/v1','openai-responses'),'fixture-access')
        assert result['status']=='failed' and result['reason']=='responses_failed'
        assert result['http_status']==400 and result['provider_error_code']=='unknown_parameter'
        assert result['error_param']=='tool_choice.namespace'
        assert result['request_phase']=='text_response'
        assert result['request_id']=='req_fixture12345678'
        assert 'fixture-secret' not in str(result) and 'fixture-access' not in str(result)
    finally:
        value.shutdown();value.server_close();thread.join(2)


def test_unknown_error_metadata_and_message_are_not_forwarded():
    body=transport._failure({'code':'sk-fixture-private','param':'Authorization','message':'fixture-secret'},400,'fixture-secret')
    assert body['http_status']==400
    assert 'provider_error_code' not in body and 'error_param' not in body and 'request_id' not in body
    assert 'fixture-private' not in str(body) and 'fixture-secret' not in str(body)


def test_completed_response_validation_reports_only_a_static_code(server):
    base,calls,handlers=server
    handlers.append(lambda *_:[completed([function(arguments='[]')])])
    body,_=transport.post(base+'/responses',{'model':'fixture','messages':[], 'tools':[{'name':'Read'}]}, {}, 3)
    assert body['error_code']=='responses_invalid_response'
    assert body['validation_code']=='responses_invalid_tool_call'
    assert body['transport_phase']=='response_validation'
    assert not body.get('content')


@pytest.mark.parametrize('content_type', ['application/json', None, 'text/event-stream'])
def test_http_200_json_error_reaches_probe_without_secrets(server, monkeypatch, content_type):
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'desktop'))
    from provider_probe import check_connection
    from provider_setup import profile_config
    base, calls, handlers = server
    handlers.append(lambda *_: {'content_type': content_type, 'raw': json.dumps({'error': {
        'code': 'subscription_sharing_usage_unavailable', 'message': 'fixture-secret'}}).encode()})
    original = transport.post
    monkeypatch.setattr(transport, 'post', lambda url, *a, **k: original(base + '/responses', *a, **k))
    result = check_connection(profile_config('codexpro', 'fixture', 'https://api.openai.com/v1',
                                              'openai-responses'), 'fixture-access')
    assert result['status'] == 'failed' and result['reason'] == 'subscription_sharing_usage_unavailable'
    assert result['http_status'] == 200 and result['request_phase'] == 'text_response'
    assert result['response_content_type'] == (content_type or 'missing')
    assert result['request_id'] == 'req_fixture12345678'
    assert len(calls) == 1 and not result['connected']
    assert 'fixture-secret' not in str(result) and 'fixture-access' not in str(result)


@pytest.mark.parametrize('wire', [
    {'content_type': 'text/html', 'raw': b'<html>fixture-secret</html>'},
    {'content_type': 'sk-fixture-secret', 'raw': b'fixture-secret'},
    {'content_type': 'application/json', 'raw': json.dumps(completed([function()])['response']).encode()},
    {'content_type': 'application/json', 'events': [{'type': 'response.output_item.done', 'item': function()}]},
    {'content_type': None, 'raw': b'data: [DONE]\n\n'},
    {'content_type': 'application/json', 'raw': b'data: []\n\n'},
])
def test_wrong_header_never_makes_html_json_or_partial_tools_executable(server, wire):
    base, calls, handlers = server
    handlers.append(lambda *_: wire)
    result, _ = transport.post(base + '/responses', {'model': 'fixture', 'messages': [],
        'tools': [{'name': 'Read'}]}, {}, 3)
    assert result.get('error') and not result.get('content')
    assert result['response_content_type'] in {'text/html', 'other', 'application/json', 'missing'}
    assert result['http_status'] == 200
    assert 'fixture-secret' not in str(result) and len(calls) == 1


@pytest.mark.parametrize('with_status', [False, True])
def test_codex_reference_items_survive_empty_completed_output(server, monkeypatch, with_status):
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'desktop'))
    from provider_probe import check_connection
    from provider_setup import profile_config
    base, calls, handlers = server
    raw = (Path(__file__).parent / 'fixtures/responses/codex_cli.sse').read_bytes()
    if with_status:
        raw = raw.replace(b'"id":"resp1","output":[]', b'"id":"resp1","status":"completed","output":[]')
    handlers.append(lambda *_: {'raw': raw, 'content_type': 'text/event-stream'})
    original = transport.post
    monkeypatch.setattr(transport, 'post', lambda url, *a, **k: original(base + '/responses', *a, **k))
    result = check_connection(profile_config('codexpro', 'fixture', 'https://api.openai.com/v1',
                                              'openai-responses'), 'fixture-token')
    assert result['status'] == 'verified', result
    assert len(calls) == 1 and 'tools' not in calls[0][1]


@pytest.mark.parametrize('response', [
    {'id': 'fixture', 'output': None}, {'id': 'fixture'},
    {'id': 'fixture', 'status': 'completed', 'output': []},
    {'id': 'fixture', 'status': 'completed', 'output': [message('obsolete terminal copy')]},
])
def test_finalized_items_are_preserved_without_duplicate_terminal_text(server, response):
    base, calls, handlers = server
    item = message('finalized text')
    done = {'type': 'response.output_item.done', 'output_index': 0, 'item': item}
    handlers.append(lambda *_: [done, done, {'type': 'response.completed', 'response': response}])
    result, _ = transport.post(base + '/responses', {'model': 'fixture', 'messages': []}, {}, 3)
    assert [block['text'] for block in result['content'] if block['type'] == 'text'] == ['finalized text']
    assert result['content'][-1]['output'] == [item]


@pytest.mark.parametrize('terminal', [None, {'type': 'response.failed', 'response': {'error': {'code': 'invalid_token'}}},
    {'type': 'response.incomplete', 'response': {'status': 'incomplete'}},
    {'type': 'response.completed', 'response': {'status': 'incomplete'}}])
def test_finished_item_still_requires_successful_terminal_event(server, terminal):
    base, calls, handlers = server
    events = [{'type': 'response.output_item.done', 'output_index': 0, 'item': function()}]
    if terminal: events.append(terminal)
    handlers.append(lambda *_: events)
    result, _ = transport.post(base + '/responses', {'model': 'fixture', 'messages': [],
        'tools': [{'name': 'Read'}]}, {}, 3)
    assert result.get('error') and not result.get('content')


@pytest.mark.parametrize('item', [function(status='incomplete'), function(arguments='[]'), function(name='unknown')])
def test_compact_stream_does_not_bypass_tool_validation(server, item):
    base, calls, handlers = server
    handlers.append(lambda *_: [{'type': 'response.output_item.done', 'output_index': 0, 'item': item},
                               {'type': 'response.completed', 'response': {'id': 'fixture'}}])
    result, _ = transport.post(base + '/responses', {'model': 'fixture', 'messages': [],
        'tools': [{'name': 'Read'}]}, {}, 3)
    assert result['validation_code'] == 'responses_invalid_tool_call' and not result.get('content')


def test_done_items_use_output_order_and_preserve_encrypted_reasoning(server):
    base, calls, handlers = server
    reason = {'type': 'reasoning', 'id': 'rs-fixture', 'encrypted_content': 'opaque-fixture', 'summary': []}
    call = function(status=None)
    handlers.append(lambda *_: [{'type': 'response.output_item.done', 'output_index': 1, 'item': call},
        {'type': 'response.output_item.done', 'output_index': 0, 'item': reason},
        {'type': 'response.completed', 'response': {'id': 'fixture', 'output': None}}])
    result, _ = transport.post(base + '/responses', {'model': 'fixture', 'messages': [],
        'tools': [{'name': 'Read'}]}, {}, 3)
    assert result['stop_reason'] == 'tool_use'
    assert result['content'][-1]['output'] == [reason, call]


@pytest.mark.parametrize('index,item', [(-1, message('bad')), (True, message('bad')), (0, None)])
def test_invalid_done_item_envelope_fails_before_tools(server, index, item):
    base, calls, handlers = server
    handlers.append(lambda *_: [{'type': 'response.output_item.done', 'output_index': index, 'item': item},
        {'type': 'response.completed', 'response': {'id': 'fixture'}}])
    result, _ = transport.post(base + '/responses', {'model': 'fixture', 'messages': []}, {}, 3)
    assert result['validation_code'] == 'responses_invalid_output' and not result.get('content')


def test_responses_quota_event_is_observed_without_changing_model_answer(server, tmp_path):
    from factory.core.chatgpt_usage import ResponseHeaders, UsageStore
    base, calls, handlers = server
    store = UsageStore(tmp_path, 'account', {'name': 'codexpro'})
    handlers.append(lambda *_: [{'type': 'codex.rate_limits', 'rate_limits': {'primary': {
        'used_percent': 36, 'window_minutes': 10080, 'reset_at': 1792000000}}, 'private': 'fixture-secret'},
        completed([message('Привет')])])
    headers = transport.RenewingHeaders({}, lambda: ResponseHeaders({'Authorization': 'Bearer fixture-token'}, store.observe))
    result, _ = transport.post(base + '/responses', {'model': 'fixture', 'messages': []}, headers, 3)
    assert result['content'][0]['text'] == 'Привет'
    assert store.load()['usage']['weekly']['percent'] == 36
    assert 'fixture-secret' not in store.path.read_text() and 'fixture-token' not in store.path.read_text()


@pytest.mark.parametrize('api_format',['openai-responses','anthropic'])
def test_failed_request_trace_preserves_diagnostics_without_changing_tool_execution(server,tmp_path,monkeypatch,api_format):
    base,calls,handlers=server
    if api_format=='openai-responses':
        handlers.append(lambda *_:[{'type':'response.failed','response':{'error':{'code':'server_error','message':'private-provider-body'}}}])
    else:
        handlers.append(lambda *_:{'raw':json.dumps({'error':'local fixture failure','http_status':503,'request_id':'req_local12345678',
            'error_code':'private-local-code','authorization':'Bearer private-local-token'}).encode(),'content_type':'application/json'})
    monkeypatch.setattr(executor,'_resolve_backend_context_window',lambda **_:(128000,'fixture'))
    monkeypatch.setattr(executor,'is_interrupt_requested',lambda:False)
    result=executor.execute_cc_backend_task(base_url=base,model='fixture',system_prompt='Fixture',user_message='Read a page',
        workdir=str(tmp_path),api_format=api_format,enabled_tools=[],checkpoint_config={'enabled':False},timeout_s=3)
    assert result['status']=='fail' and result['tool_calls_count']==0 and len(calls)==1
    events=[json.loads(raw) for raw in (tmp_path/'cc_backend_trace.jsonl').read_text().splitlines()]
    failure=next(event for event in events if event['kind']=='task.finish')
    details=failure['error_diagnostics']
    assert details['http_status']==(200 if api_format=='openai-responses' else 503)
    if api_format=='openai-responses':
        assert details['provider_error_code']=='server_error' and details['terminal_event']=='response.failed'
        assert details['transport_phase']=='response_terminal' and details['request_id']=='req_fixture12345678'
    else:assert 'error_code' not in details and details['request_id']=='req_local12345678'
    assert 'private-' not in json.dumps(failure)


@pytest.mark.parametrize('event',[
    {'type':'response.failed','response':{'error':{'code':'server_error','message':'private-fixture-body'}}},
    {'type':'response.incomplete','response':{'status':'incomplete','incomplete_details':{'reason':'max_output_tokens'}}},
    {'type':'error','error':{'code':'model_error','message':'private-fixture-body'}},
])
def test_terminal_failure_diagnostics_keep_event_and_request_identity(server,event):
    base,calls,handlers=server;handlers.append(lambda *_:[event])
    body,_=transport.post(base+'/responses',{'model':'fixture','messages':[{'role':'user','content':'fixture'}]}, {},3)
    assert body['error_code']=='responses_failed' and body['terminal_event']==event['type']
    assert body['http_status']==200 and body['request_id']=='req_fixture12345678'
    if event['type']=='response.incomplete':assert body['incomplete_reason']=='max_output_tokens'
    assert 'private-fixture-body' not in str(body) and len(calls)==1


def test_browser_error_has_correlated_structured_tool_diagnostics(server,tmp_path,monkeypatch):
    base,calls,handlers=server
    handlers.append(lambda payload,index:[completed([function(name='desktop_browser',args={'action':'open','url':'https://example.com/'})])] if index==1
        else [completed([message('Не удалось открыть страницу; ошибка перехода подтверждена.')])])
    monkeypatch.setattr(executor,'_resolve_backend_context_window',lambda **_:(128000,'fixture'))
    monkeypatch.setattr(executor,'is_interrupt_requested',lambda:False)
    monkeypatch.setattr(executor,'execute_tool',lambda *args,**kwargs:json.dumps({'error':'navigation_failed',
        'navigation':{'phase':'failed','error_code':'ERR_NAME_NOT_RESOLVED','committed':False,'document_ready':False,'recovered':False}}))
    tool={'name':'desktop_browser','description':'Fixture browser','input_schema':{'type':'object','properties':{'action':{'type':'string'},'url':{'type':'string'}}}}
    result=executor.execute_cc_backend_task(base_url=base,model='fixture',system_prompt='Fixture',user_message='Open example.com',workdir=str(tmp_path),
        api_format='openai-responses',enabled_tools=[tool],checkpoint_config={'enabled':False},timeout_s=3)
    assert result['status']=='ok',result
    events=[json.loads(raw) for raw in (tmp_path/'cc_backend_trace.jsonl').read_text().splitlines()]
    item=next(event for event in events if event['kind']=='tool.result')
    assert item['tool_use_id']=='call-fixture' and item['browser_diagnostics']['action']=='open'
    assert item['browser_diagnostics']['error']=='navigation_failed'
    assert item['browser_diagnostics']['navigation']['error_code']=='ERR_NAME_NOT_RESOLVED'
