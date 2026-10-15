import json
import threading
import sys
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'desktop'))
from provider_setup import profile_config
from provider_probe import check_connection, post_json, ProbeFailure


@pytest.mark.parametrize('api_format', ['openai', 'anthropic', 'openai-responses'])
@pytest.mark.parametrize('answer', ['Привет!', 'Да, я здесь.', '**Связь работает.**'])
def test_one_ordinary_answer_verifies_without_tools_or_exact_text(api_format, answer):
    base = 'https://api.openai.com/v1' if api_format == 'openai-responses' else 'https://provider.invalid/v1'
    calls = []
    def post(url, payload, headers):
        calls.append(payload)
        assert 'tools' not in payload and 'tool_choice' not in payload
        assert len(payload['messages']) == 1
        assert 'test-api-key' not in json.dumps(payload)
        assert headers.get('x-api-key', headers.get('Authorization')) in {'test-api-key', 'Bearer test-api-key'}
        if api_format == 'anthropic':
            assert url.endswith('/v1/messages')
            return {'role': 'assistant', 'content': [{'type': 'text', 'text': answer}]}
        assert url.endswith('/v1/responses' if api_format == 'openai-responses' else '/v1/chat/completions')
        return {'choices': [{'message': {'role': 'assistant', 'content': answer}}]}
    result = check_connection(profile_config('demo', 'model', base, api_format), 'test-api-key', post)
    assert result['status'] == 'verified' and result['reason'] == 'ok'
    assert result['connected'] and len(calls) == 1
    assert 'test-api-key' not in str(result) and answer not in str(result)


@pytest.mark.parametrize('api_format,body', [
    ('openai', {'choices': [{'message': {'role': 'assistant', 'content': ''}}]}),
    ('openai', {'choices': [{'message': {'role': 'assistant', 'content': '  '}}]}),
    ('openai', {'choices': [{'message': {'role': 'user', 'content': 'echoed prompt'}}]}),
    ('openai', {'choices': [{'message': {'role': 'assistant', 'tool_calls': [{}]}}]}),
    ('openai', {'choices': []}),
    ('openai', {'error': {'message': 'private-provider-error'}}),
    ('anthropic', {'content': []}),
    ('anthropic', {'content': [{'type': 'thinking', 'thinking': 'not an answer'}]}),
    ('anthropic', {'role': 'user', 'content': [{'type': 'text', 'text': 'echoed prompt'}]}),
    ('anthropic', {'error': {'message': 'private-provider-error'}}),
])
def test_empty_invalid_or_failed_responses_do_not_verify(api_format, body):
    calls = []
    def post(*args):
        calls.append(args)
        return body
    result = check_connection(profile_config('demo', 'model', 'https://provider.invalid/v1', api_format), 'test-key', post)
    assert result['status'] == 'failed' and len(calls) == 1
    assert 'private-provider-error' not in str(result)


@pytest.mark.parametrize('status,reason', [(401, 'authentication'), (403, 'authentication'), (429, 'rate_limit'), (302, 'http_error'), (500, 'http_error')])
def test_transport_reports_only_structured_status_and_never_follows_redirect(status, reason):
    requests = []
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            requests.append(self.path)
            self.send_response(status)
            self.send_header('Location', '/redirect-target')
            self.end_headers()
            self.wfile.write(b'private-error-body')
        def log_message(self, *args):
            pass
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    try:
        with pytest.raises(ProbeFailure) as caught:
            post_json(f'http://127.0.0.1:{server.server_port}/test', {}, {'Authorization': 'Bearer test-key'})
        assert caught.value.reason == reason
        assert caught.value.http_status == status
        assert requests == ['/test']
        assert 'private-error-body' not in str(caught.value)
    finally:
        server.shutdown(); server.server_close(); thread.join()


@pytest.mark.parametrize('model,base,thinking,session', [
    ('deepseek-v4-flash','https://opencode.ai/zen/go/v1',True,True),
    ('deepseek-v4-pro','https://api.deepseek.com/v1',True,False),
    ('other-model','https://provider.invalid/v1',False,False),
    ('other-model','https://opencode.ai/zen/go/v1',False,True),
])
def test_probe_provider_compatibility_uses_one_text_request(model, base, thinking, session):
    calls=[]
    def post(url,payload,headers):
        calls.append(dict(headers))
        assert headers['User-Agent']=='IgorekDesktop/connection-check'
        assert ('x-opencode-session' in headers) is session
        assert ('thinking' in payload) is thinking
        assert 'tools' not in payload and 'tool_choice' not in payload
        if thinking:
            assert payload['thinking']=={'type':'disabled'}
        return {'choices':[{'message':{'role':'assistant','content':'Привет!'}}]}
    result=check_connection(profile_config('demo',model,base,'openai'),'test-key',post)
    assert result['status']=='verified' and len(calls)==1


def test_openai_realtime_profile_verifies_via_client_secret_mint():
    config = profile_config('openai-realtime', 'gpt-realtime-2.1-mini', 'https://api.openai.com/v1', 'openai')
    calls = []
    def post(url, payload, headers):
        calls.append((url, payload, headers))
        assert headers.get('Authorization') == 'Bearer test-key'
        assert 'test-key' not in json.dumps(payload)
        return {'value': 'ek_test', 'expires_at': 123}
    result = check_connection(config, 'test-key', post)
    assert result['status'] == 'verified'
    assert len(calls) == 1
    assert calls[0][0] == 'https://api.openai.com/v1/realtime/client_secrets'
    assert calls[0][1]['session']['type'] == 'realtime'
    assert 'test-key' not in json.dumps(result)


def test_openai_realtime_mint_without_value_is_failed():
    config = profile_config('openai-realtime', 'gpt-realtime-2.1', 'https://api.openai.com/v1', 'openai')
    result = check_connection(config, 'test-key', lambda *_: {'error': 'noop'})
    assert result['status'] == 'failed'


@pytest.mark.parametrize('api_format',['openai','anthropic'])
def test_local_probe_omits_empty_auth_but_remote_requires_key(api_format):
    calls=[]
    def post(url,payload,headers):
        calls.append(headers)
        assert 'Authorization' not in headers and 'x-api-key' not in headers
        return {}
    config=profile_config('local','model','http://127.0.0.1:1234/v1',api_format)
    check_connection(config,'',post)
    assert len(calls)==1
    calls.clear()
    result=check_connection({**config,'base_url':'https://provider.invalid/v1'},'',post)
    assert result['reason']=='authentication' and not calls
