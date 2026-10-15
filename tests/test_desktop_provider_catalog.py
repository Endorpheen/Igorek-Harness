import sys
from pathlib import Path
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'desktop'))
import provider_setup as module
from vault_owner import OwnerVault
from factory.core.opencode_quota import OpenCodeUsageError

@pytest.fixture
def owner(tmp_path):
    owner = OwnerVault(tmp_path)
    owner.service.create('test-password')
    yield owner
    owner.close()


def test_live_catalog_filters_incompatible_protocols_and_marks_free(monkeypatch, owner):
    calls = []
    def fetch(url, key=''):
        calls.append((url, key))
        if url.endswith('/models'):
            return {'data': [{'id': m} for m in ['free', 'paid', 'responses', 'unknown']]}
        return {'opencode': {'npm': '@ai-sdk/openai-compatible', 'models': {
            'free': {'name': 'Free', 'cost': {'input': 0, 'output': 0}},
            'paid': {'provider': {'npm': '@ai-sdk/anthropic'}, 'cost': {'input': 1, 'output': 2}},
            'responses': {'provider': {'npm': '@ai-sdk/openai'}, 'cost': {'input': 0, 'output': 0}},
        }}}
    monkeypatch.setattr(module, 'catalog_json', fetch)
    result = module.model_catalog('opencode-zen', 'draft-key', '', owner.lifecycle)
    assert [m['id'] for m in result['models']] == ['free', 'paid']
    assert result['models'][0]['free'] is True
    assert result['models'][1]['api_format'] == 'anthropic'
    assert calls == [('https://opencode.ai/zen/v1/models', 'draft-key'), ('https://models.dev/api.json', '')]
    assert 'draft-key' not in str(result)


def test_saved_key_catalog_binds_provider_and_never_returns_secret(monkeypatch, owner):
    profile = owner.providers.save('router', 'm', module.PRESETS['openrouter'][0], 'openai', 'vault-key')
    calls = []
    monkeypatch.setattr(module, 'catalog_json', lambda url, key='': calls.append((url, key)) or {'data': [
        {'id': 'agent', 'supported_parameters': ['tools'], 'pricing': {'prompt': '0', 'completion': '0'}},
        {'id': 'no-tools', 'supported_parameters': []}]})
    reply = owner.dispatch({'id':'test','action':'model_catalog','preset':'openrouter','api_key':'','credential_id':profile['credential_id']})
    assert reply['result']['models'][0]['id'] == 'agent'
    assert calls[0][1] == 'vault-key'
    assert 'vault-key' not in str(reply)
    with pytest.raises(ValueError):
        module.model_catalog('opencode-go', '', profile['credential_id'], owner.lifecycle)
    owner.service.lock()
    assert owner.dispatch({'id':'test','action':'model_catalog','preset':'openrouter','api_key':'draft','credential_id':''})['error'] == 'vault_locked'


def test_coding_plan_is_documented_not_claimed_authenticated(monkeypatch, owner):
    monkeypatch.setattr(module, 'catalog_json', lambda *_: pytest.fail('Undocumented endpoint requested'))
    result = module.model_catalog('zai-coding-plan', 'key', '', owner.lifecycle)
    assert result['source'] == 'documented'
    assert [m['id'] for m in result['models']] == ['glm-5.3-flash', 'glm-5.3']


def test_quota_uses_verified_desktop_profile_and_refuses_locked_or_other_profile(owner, monkeypatch):
    import factory.core.opencode_quota as quota
    profile = owner.providers.save('go', 'deepseek-v4-flash', module.PRESETS['opencode-go'][0], 'openai', 'desktop-secret')
    owner.service.check_provider_profile(profile['credential_id'], lambda *_: {'status':'verified'})
    calls = []
    monkeypatch.setattr(quota, 'fetch_opencode_usage', lambda key: calls.append(key) or {'usage': {'fixture': True}})
    monkeypatch.setenv('OPENCODE_API_KEY', 'wrong-host-key')
    result = module.desktop_opencode_usage(owner.lifecycle, 'go')
    assert result['usage'] == {'fixture': True}
    assert calls == ['desktop-secret']
    assert 'desktop-secret' not in str(result)
    with pytest.raises(OpenCodeUsageError):
        module.desktop_opencode_usage(owner.lifecycle, 'other')
    owner.service.lock()
    with pytest.raises(OpenCodeUsageError):
        module.desktop_opencode_usage(owner.lifecycle, 'go')
    assert len(calls) == 1


def test_catalog_redirect_never_forwards_key():
    import threading
    import urllib.error
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    paths = []
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            paths.append(self.path)
            self.send_response(302)
            self.send_header('Location', '/other')
            self.end_headers()
        def log_message(self, *args):
            pass
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with pytest.raises(urllib.error.HTTPError):
            module.catalog_json(f'http://127.0.0.1:{server.server_port}/models', 'private')
        assert paths == ['/models']
    finally:
        server.shutdown(); thread.join(); server.server_close()


def test_voice_key_is_owned_verified_openai_profile_without_host_fallback(owner, monkeypatch):
    from factory.core.realtime_voice.service import VoiceService
    from factory.core.realtime_voice import credentials
    monkeypatch.setattr(credentials, 'openai_api_key', lambda: pytest.fail('Host key must not be read'))
    voice = VoiceService(key_provider=lambda: module.desktop_openai_voice_key(owner.lifecycle))
    router = owner.providers.save('router-voice', 'm', 'https://openrouter.ai/api/v1', 'openai', 'router-key')
    owner.lifecycle.check_provider_profile(router['credential_id'], lambda *_: {'status': 'verified'})
    assert voice.public_config()['reason'] == 'no_key'
    profile = owner.providers.save('openai-voice', 'gpt-4o-mini', 'https://api.openai.com/v1', 'openai', 'test-openai-voice-key')
    assert voice.public_config()['reason'] == 'no_key'
    owner.lifecycle.check_provider_profile(profile['credential_id'], lambda *_: {'status': 'verified'})
    assert voice.public_config()['enabled'] is True
    captured = []
    monkeypatch.setattr(credentials, 'mint_client_secret', lambda **kwargs: captured.append(kwargs['key']) or {'value': 'test-ephemeral', 'expires_at': 9999999999})
    result = voice.start_session('current-agent-session')
    assert captured == ['test-openai-voice-key']
    assert 'test-openai-voice-key' not in str(result)
    call = voice.registry.get(result['call_id'])
    assert call.session_id == 'current-agent-session'
    owner.service.lock()
    assert voice.public_config()['reason'] == 'no_key'


def test_provider_quota_locked_startup_is_typed_and_unlock_recovers(owner, monkeypatch):
    import factory.core.opencode_quota as quota
    profile = owner.providers.save('go', 'deepseek-v4-flash', module.PRESETS['opencode-go'][0], 'openai', 'fixture-key')
    owner.service.check_provider_profile(profile['credential_id'], lambda *_: {'status': 'verified'})
    calls = []
    monkeypatch.setattr(quota, 'fetch_opencode_usage', lambda key: calls.append(key) or {'usage': {'fixture': True}})
    owner.service.lock()
    with pytest.raises(OpenCodeUsageError) as caught:
        module.desktop_provider_usage(owner.lifecycle, 'go')
    assert (caught.value.code, caught.value.status) == ('vault_locked', 423)
    assert calls == []
    owner.service.unlock('test-password')
    assert module.desktop_provider_usage(owner.lifecycle, 'go')['usage'] == {'fixture': True}
    assert calls == ['fixture-key']
    monkeypatch.setattr(owner.lifecycle, 'list_provider_profiles', lambda: (_ for _ in ()).throw(RuntimeError('fixture failure')))
    with pytest.raises(RuntimeError):
        module.desktop_provider_usage(owner.lifecycle, 'go')
