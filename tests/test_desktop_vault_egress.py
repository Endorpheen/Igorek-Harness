import json
import sys
import threading
import urllib.request
from urllib.parse import urlsplit
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'desktop'))
from vault_owner import OwnerVault
from vault_egress import DesktopVaultEgress
from factory.core.isolation.errors import IsolationError


@pytest.fixture
def setup(tmp_path):
    received = []
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            received.append({'path': self.path, 'headers': dict(self.headers),
                'body': self.rfile.read(int(self.headers['Content-Length']))})
            self.send_response(200); self.end_headers(); self.wfile.write(b'{"answer":"fixture"}')
        def log_message(self, *args): pass
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    owner = OwnerVault(tmp_path); owner.service.create('test-password')
    proxies = []
    def bind(api_format='openai'):
        profile = owner.providers.save('test-' + api_format, 'fixture',
            f'http://127.0.0.1:{server.server_port}/custom/api/v1', api_format, 'test-only-key')
        owner.service.check_provider_profile(profile['credential_id'], lambda *_: {'status':'verified','reason':'ok'})
        proxy = DesktopVaultEgress(owner.lifecycle, profile['credential_id'], profile['config'])
        proxies.append(proxy)
        return profile, proxy
    yield owner, bind, received
    for proxy in proxies: proxy.close()
    owner.close(); server.shutdown(); server.server_close(); thread.join(2)


def request(plan, suffix='/chat/completions'):
    url = f'http://127.0.0.1:{plan.proxy_bind[1]}' + urlsplit(plan.guest_base_url).path.rstrip('/') + suffix
    payload = urllib.request.Request(url, data=b'{"stream":false}', headers={
        'Authorization':'Bearer forged-guest', 'x-api-key':'forged-guest',
        'Content-Type':'application/json', 'anthropic-version':'2023-06-01'}, method='POST')
    try:
        with urllib.request.urlopen(payload, timeout=3) as reply:
            return reply.status, reply.read()
    except urllib.error.HTTPError as error:
        return error.code, error.read()


@pytest.mark.parametrize('api_format', ['openai', 'anthropic'])
def test_custom_verified_profile_uses_host_proxy_and_never_guest_key(setup, api_format):
    owner, bind, received = setup
    profile, proxy = bind(api_format)
    plan = proxy.plan(profile['config']['base_url'])
    assert plan.mode == 'proxy' and plan.guest_env == ()
    assert plan.guest_base_url.startswith('http://10.0.2.2:')
    assert 'test-only-key' not in repr(plan)
    code, body = request(plan, '/messages' if api_format == 'anthropic' else '/chat/completions')
    assert code == 200 and b'fixture' in body
    assert received[0]['path'] == '/custom/api/v1' + ('/messages' if api_format == 'anthropic' else '/chat/completions')
    headers = {key.lower(): value for key, value in received[0]['headers'].items()}
    assert headers.get('x-api-key') == ('test-only-key' if api_format == 'anthropic' else None)
    assert headers.get('authorization') == ('Bearer test-only-key' if api_format == 'openai' else None)
    assert 'forged-guest' not in json.dumps(headers)
    assert received[0]['body'] == b'{"stream":false}'
    assert proxy.plan(profile['config']['base_url']).proxy_bind == plan.proxy_bind


def test_locked_vault_refuses_existing_proxy_calls_and_new_plans(setup):
    owner, bind, received = setup
    profile, proxy = bind()
    plan = proxy.plan(profile['config']['base_url'])
    owner.service.lock()
    code, body = request(plan)
    assert code == 502 and b'provider_auth_unavailable' in body
    assert not received and b'test-only-key' not in body
    with pytest.raises(IsolationError, match='desktop_vault_unavailable'):
        proxy.plan(profile['config']['base_url'])
    owner.service.unlock('test-password')
    assert request(plan)[0] == 200


@pytest.mark.parametrize('change', ['host', 'path', 'format'])
def test_changed_binding_has_no_unknown_route_or_env_fallback(setup, monkeypatch, change):
    owner, bind, received = setup
    profile, proxy = bind()
    monkeypatch.setenv('OPENCODE_API_KEY', 'wrong-production-key')
    if change == 'format':
        owner.providers.update(profile['credential_id'], **{
            'name': profile['config']['name'], 'model':'fixture',
            'base_url':profile['config']['base_url'], 'api_format':'anthropic', 'api_key':''})
        url = profile['config']['base_url']
    else:
        url = 'https://other.invalid/v1' if change == 'host' else profile['config']['base_url'] + '/other'
    with pytest.raises(IsolationError): proxy.plan(url)
    assert not received and proxy._proxy is None


def test_unverified_profile_refuses_proxy_creation(setup):
    owner, bind, received = setup
    profile, proxy = bind()
    owner.providers.update(profile['credential_id'], profile['config']['name'], 'different-model',
        profile['config']['base_url'], 'openai', '')
    with pytest.raises(IsolationError): proxy.plan(profile['config']['base_url'])
    assert proxy._proxy is None and not received



def test_caller_config_mutation_does_not_retarget_proxy(setup):
    owner, bind, received = setup
    profile, proxy = bind()
    original = profile['config']['base_url']
    profile['config']['base_url'] = 'https://other.invalid/v1'
    with pytest.raises(IsolationError): proxy.plan(profile['config']['base_url'])
    assert request(proxy.plan(original))[0] == 200
    assert len(received) == 1
