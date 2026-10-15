import importlib.util
import json
from pathlib import Path
import threading
from types import SimpleNamespace
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from factory.core.host_services import UnixHTTPConnection
import sys
sys.path.insert(0, str(Path(__file__).parents[1] / 'desktop'))


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, Path(__file__).parents[1] / 'desktop' / filename)
    value = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(value)
    return value


module = load('opencode_profiles', 'opencode_profiles.py')
sandbox = load('opencode_sandbox_profiles', 'opencode-sandbox.py')


@pytest.fixture
def endpoint(tmp_path):
    received = []
    class Upstream(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(json.dumps({'default_generation_settings': {'n_ctx': 131072}}).encode())
        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            received.append((self.path, dict(self.headers), body))
            if body.get('redirect'):
                self.send_response(307)
                self.send_header('Location', 'http://127.0.0.1:1/secret')
                self.end_headers()
                return
            self.send_response(200)
            self.send_header('Content-Type', 'text/event-stream')
            self.end_headers()
            self.wfile.write(b'data: {"text":"first"}\n\n')
            self.wfile.flush()
            self.wfile.write(b'data: [DONE]\n\n')
    upstream = ThreadingHTTPServer(('127.0.0.1', 0), Upstream)
    worker = threading.Thread(target=upstream.serve_forever, daemon=True)
    worker.start()
    config = {'name': 'local-test', 'model': 'lfm2.5-24b-a2b', 'api_format': 'openai',
              'base_url': f'http://127.0.0.1:{upstream.server_port}/v1'}
    profiles = [{'credential_id': 'fixture-id', 'config': config, 'status': 'verified'},
                {'credential_id': 'draft-id', 'config': {**config, 'name': 'draft'}, 'status': 'unverified'}]
    state = {'unlocked': True}
    auth_calls = []
    def headers(identity, binding, api):
        auth_calls.append((identity, binding, api))
        return {'Authorization': 'Bearer fixture-provider-secret'} if api != 'anthropic' else {'x-api-key': 'fixture-provider-secret'}
    lifecycle = SimpleNamespace(list_provider_profiles=lambda: profiles, provider_runtime_headers=headers)
    server = module.OpenCodeProfiles(tmp_path / 'private', lifecycle, lambda: state['unlocked'])
    grant = server.configure('grant', {})
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield server, grant, config, profiles, state, received, auth_calls
    server.shutdown()
    server.server_close()
    upstream.shutdown()
    upstream.server_close()


def request(endpoint, path=None, body=None, token=None, extra=None):
    server, grant, config, *_ = endpoint
    conn = UnixHTTPConnection(str(server.path))
    headers = {'Authorization': 'Bearer ' + (grant['token'] if token is None else token), **(extra or {})}
    try:
        try:
            conn.request('POST', path or '/profiles/fixture-id/v1/chat/completions', json.dumps(body or {'model': config['model'], 'messages': []}), headers)
        except BrokenPipeError:
            # Early header rejection can close the upload before its body is sent.
            # The response must still contain the expected HTTP denial.
            pass
        response = conn.getresponse()
        return response.status, response.read()
    finally:
        conn.close()


def test_verified_profiles_and_native_context_without_exported_provider_keys(endpoint):
    grant = endpoint[1]
    assert len(grant['profiles']) == 1
    assert grant['profiles'][0]['context'] == 131072
    assert 'fixture-provider-secret' not in json.dumps(grant)
    assert not endpoint[-1]
    config = sandbox.profile_providers(grant, 12345)
    provider = config['igorek-local-test']
    assert provider['settings']['apiKey'] == grant['token']
    assert provider['settings']['baseURL'] == 'http://127.0.0.1:12345/profiles/fixture-id/v1'
    assert provider['models']['lfm2.5-24b-a2b']['limit']['context'] == 131072
    assert 'fixture-provider-secret' not in json.dumps(config)


def test_streaming_keeps_provider_auth_host_side_and_ignores_injected_headers(endpoint):
    status, body = request(endpoint, extra={'x-api-key': 'attacker', 'Cookie': 'attacker'})
    assert status == 200 and b'first' in body and b'[DONE]' in body
    path, headers, payload = endpoint[-2][0]
    assert path == '/v1/chat/completions'
    assert headers['Authorization'] == 'Bearer fixture-provider-secret'
    assert 'X-Api-Key' not in headers and 'Cookie' not in headers
    assert endpoint[-1][0][0] == 'fixture-id'


@pytest.mark.parametrize('path', ['/profiles/other/v1/chat/completions', '/profiles/fixture-id/v1/models',
                                '/profiles/fixture-id/v1/chat/completions?url=http://localhost/',
                                '/profiles/fixture-id/v1/../messages', 'http://localhost/private'])
def test_request_cannot_change_provider_endpoint_or_target(endpoint, path):
    assert request(endpoint, path=path)[0] == 403
    assert not endpoint[-2] and not endpoint[-1]


def test_wrong_model_capability_lock_and_revocation_are_rejected(endpoint):
    assert request(endpoint, token='wrong')[0] == 403
    assert request(endpoint, body={'model': 'other'})[0] == 403
    endpoint[4]['unlocked'] = False
    assert request(endpoint)[0] == 403
    with pytest.raises(ValueError):
        endpoint[0].configure('grant', {})
    endpoint[0].configure('revoke', {'token': endpoint[1]['token']})
    endpoint[4]['unlocked'] = True
    assert request(endpoint)[0] == 403
    assert not endpoint[-2] and not endpoint[-1]


def test_profile_edit_or_unverification_invalidates_existing_binding(endpoint):
    endpoint[3][0]['config'] = {**endpoint[2], 'base_url': 'http://127.0.0.1:1/v1'}
    assert request(endpoint)[0] == 403
    endpoint[3][0]['config'] = endpoint[2]
    endpoint[3][0]['status'] = 'failed'
    assert request(endpoint)[0] == 403
    assert not endpoint[-2] and not endpoint[-1]


def test_redirect_is_returned_without_following_or_exporting_auth(endpoint):
    assert request(endpoint, body={'model': endpoint[2]['model'], 'redirect': True})[0] == 307
    assert len(endpoint[-2]) == 1


@pytest.mark.parametrize('api,endpoint_name,npm', [('openai', 'chat/completions', '@ai-sdk/openai-compatible'),
    ('anthropic', 'messages', '@ai-sdk/anthropic'), ('openai-responses', 'responses', '@ai-sdk/openai')])
def test_provider_wire_formats_preserve_original_profile_contract(endpoint, api, endpoint_name, npm):
    endpoint[3][0]['config'] = {**endpoint[2], 'api_format': api}
    grant = endpoint[0].configure('grant', {})
    endpoint[1].update(grant)
    assert request(endpoint, path='/profiles/fixture-id/v1/' + endpoint_name)[0] == 200
    assert endpoint[-2][0][0] == '/v1/' + endpoint_name
    config = sandbox.profile_providers(grant, 12345)
    assert config['igorek-local-test']['package'] == {'openai':'@opencode/ai/providers/openai-compatible','anthropic':'@opencode/ai/providers/anthropic','openai-responses':'@opencode/ai/providers/openai/responses'}[api]


def test_closed_server_removes_only_its_own_private_socket(tmp_path):
    lifecycle = SimpleNamespace(list_provider_profiles=lambda: [])
    server = module.OpenCodeProfiles(tmp_path / 'private', lifecycle, lambda: True)
    path = server.path
    assert path.exists()
    server.server_close()
    assert not path.exists()


def test_anthropic_base_without_v1_preserves_profile_probe_route(endpoint):
    endpoint[3][0]['config'] = {**endpoint[2], 'api_format': 'anthropic',
                              'base_url': endpoint[2]['base_url'].removesuffix('/v1')}
    grant = endpoint[0].configure('grant', {})
    endpoint[1].update(grant)
    assert request(endpoint, path='/profiles/fixture-id/v1/messages')[0] == 200
    assert endpoint[-2][0][0] == '/v1/messages'


def test_owner_delegation_uses_only_unlocked_private_protocol(endpoint, tmp_path):
    from vault_owner import OwnerVault
    owner = OwnerVault(tmp_path / 'owner')
    owner.opencode_profiles = endpoint[0]
    endpoint[0].allowed = lambda: not owner.interface_locked and owner.service.status()['state'] == 'unlocked'
    value = {'id': 'profiles', 'action': 'opencode_profiles', 'operation': 'grant', 'config': '{}'}
    try:
        assert 'error' in owner.dispatch(value)
        owner.service.create('fixture-password')
        grant = owner.dispatch(value)['result']
        assert grant['profiles'][0]['name'] == 'local-test'
        assert 'fixture-provider-secret' not in json.dumps(grant)
        owner.interface_locked = True
        assert owner.dispatch(value)['error'] == 'interface_locked'
        assert owner.dispatch({'id': 'revoke', 'action': 'opencode_profiles', 'operation': 'revoke',
                               'config': json.dumps({'token': grant['token']})})['result'] == {'revoked': True}
    finally:
        owner.close()


def test_response_usage_observer_receives_headers_with_its_existing_keyword_contract(endpoint):
    from factory.core.chatgpt_usage import ResponseHeaders
    observed=[]
    def observe(*, headers=None, event=None):
        observed.append(headers.get('Content-Type'))
    endpoint[0].lifecycle.provider_runtime_headers=lambda *args: ResponseHeaders({'Authorization':'Bearer fixture-provider-secret'},observe)
    assert request(endpoint)[0]==200
    assert observed==['text/event-stream']


@pytest.mark.parametrize('original_config', [
    '{"providers":{"existing":{"name":"Keep native"}}}',
    '{"provider":{"existing":{"npm":"@ai-sdk/openai-compatible","models":{"old":{}}}}}',
    '// comment\n{"providers":{"existing":{"name":"URL http://example.test"}},}',
])
def test_native_configuration_overlay_preserves_existing_profiles_and_original_files(endpoint,tmp_path,original_config):
    original=tmp_path/'config';original.mkdir()
    (original/'opencode.jsonc').write_text(original_config)
    (original/'opencode.json').write_text('{"model":"existing/old"}')
    (original/'plugins').mkdir()
    runtime=tmp_path/'runtime'
    sandbox.profile_config_directory(endpoint[1],12345,original,runtime,{})
    assert (original/'opencode.jsonc').read_text()==original_config
    result=json.loads((runtime/'opencode.jsonc').read_text())
    providers=result.get('providers',result.get('provider'))
    assert set(providers)=={'existing','igorek-local-test'}
    assert (runtime/'opencode.json').resolve()==(original/'opencode.json').resolve()
    assert (runtime/'plugins').resolve()==(original/'plugins').resolve()


def test_native_mcp_configuration_is_preserved_beside_profiles(endpoint,tmp_path):
    original=tmp_path/'config';original.mkdir()
    (original/'opencode.jsonc').write_text('{"mcp":{"servers":{"existing":{"type":"local","command":["old"]}}}}')
    runtime=tmp_path/'runtime'
    sandbox.profile_config_directory(endpoint[1],12345,original,runtime,{'mcp':{'servers':{'igorek':{'type':'local','command':['bridge']}}}})
    assert set(json.loads((runtime/'opencode.jsonc').read_text())['mcp']['servers'])=={'existing','igorek'}


def test_jsonc_trailing_comments_escaped_quotes_and_url_strings_are_preserved():
    value=sandbox.parse_jsonc('{"url":"http://localhost/v1", "quote":"a\\\"b", // trailing\n}')
    assert value=={'url':'http://localhost/v1','quote':'a"b'}


def test_invalid_optional_config_does_not_block_enrolled_profile_overlay(endpoint,tmp_path):
    original=tmp_path/'config';original.mkdir()
    (original/'opencode.jsonc').write_text('{broken')
    runtime=tmp_path/'runtime'
    sandbox.profile_config_directory(endpoint[1],12345,original,runtime,{})
    assert 'igorek-local-test' in json.loads((runtime/'opencode.jsonc').read_text())['providers']
    assert (original/'opencode.jsonc').read_text()=='{broken'
