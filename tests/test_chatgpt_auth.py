import base64
import json
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlsplit

import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from factory.core import chatgpt_auth as auth

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'desktop'))
from vault_owner import OwnerVault
from provider_setup import profile_config


def encoded(value):
    raw = value if isinstance(value, bytes) else json.dumps(value).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip('=')


@pytest.fixture
def signing():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    numbers = key.public_key().public_numbers()
    jwks = {'keys': [{'kty': 'RSA', 'kid': 'fixture', 'alg': 'RS256', 'use': 'sig',
                      'e': encoded(numbers.e.to_bytes(3)), 'n': encoded(numbers.n.to_bytes(256))}]}
    def sign(**overrides):
        claims = {'iss': auth.ISSUER, 'aud': 'oaiapp_fixture', 'sub': 'subject-fixture',
                  'email': 'fixture@example.invalid', 'nonce': 'nonce-fixture', 'exp': time.time() + 300, **overrides}
        body = encoded({'kid': 'fixture', 'alg': 'RS256'}) + '.' + encoded(claims)
        return body + '.' + encoded(key.sign(body.encode(), padding.PKCS1v15(), hashes.SHA256()))
    return sign, jwks


def grant(**overrides):
    return {'access_token': 'fixture-access', 'refresh_token': 'fixture-refresh', 'id_token': 'fixture-id',
            'scope': auth.SCOPES, 'token_type': 'Bearer', 'expires_in': 3600, **overrides}


@pytest.mark.parametrize('override', [{}, {'aud': 'wrong'}, {'iss': 'https://evil.invalid'},
    {'nonce': 'wrong'}, {'exp': 1}, {'exp': float('nan')}, {'sub': ''}, {'aud': ['oaiapp_fixture', 'other']}, {'azp': 'other'}])
def test_oidc_uses_real_signature_and_all_binding_claims(signing, override):
    sign, jwks = signing
    token = sign(**override)
    if not override:
        assert auth.validate_identity(token, 'oaiapp_fixture', 'nonce-fixture', fetch=lambda _: jwks)['sub'] == 'subject-fixture'
        head, body, sig = token.split('.')
        corrupted = head + '.' + encoded({'sub': 'forged'}) + '.' + sig
        with pytest.raises(auth.ChatGPTAuthError):
            auth.validate_identity(corrupted, 'oaiapp_fixture', 'nonce-fixture', fetch=lambda _: jwks)
    else:
        with pytest.raises(auth.ChatGPTAuthError, match='identity_invalid'):
            auth.validate_identity(token, 'oaiapp_fixture', 'nonce-fixture', fetch=lambda _: jwks)


def test_plan_permission_and_rotating_refresh_contract():
    with pytest.raises(auth.ChatGPTAuthError, match='permission_missing'):
        auth.token_set(grant(scope='openid profile email'))
    saved = auth.token_set(grant(), {'client_id': 'oaiapp_fixture'}, now=1)
    calls = []
    def fetch(url, **fields):
        calls.append((url, fields))
        return grant(access_token='next-access', refresh_token='next-refresh')
    assert auth.refresh(saved, fetch=fetch, now=20) is saved
    next_tokens = auth.refresh(saved, fetch=fetch, now=3600)
    assert next_tokens['refresh_token'] == 'next-refresh'
    assert calls == [(auth.TOKEN, {'form': {'grant_type': 'refresh_token', 'client_id': 'oaiapp_fixture',
        'refresh_token': 'fixture-refresh', 'resource': auth.RESOURCE}})]


@pytest.fixture
def owner(tmp_path):
    value = OwnerVault(tmp_path)
    value.service.create('fixture-password')
    yield value
    value.close()


def saved_profile(owner, **overrides):
    saved = auth.token_set(grant(), {'client_id': 'oaiapp_fixture', 'subject': 'subject-fixture',
        'email': 'fixture@example.invalid', 'ext_agent_host_id': 'urn:uuid:00000000-0000-4000-8000-000000000000'})
    saved.update(overrides)
    config = profile_config('chatgpt', 'fixture-model', auth.RESOURCE, 'openai-responses')
    record = owner.lifecycle.add_credential('desktop-profile:chatgpt', json.dumps(saved), 'oauth',
        imported_from='desktop-provider-setup', secret_metadata={'desktop_profile': config})
    return record.id, config


def test_vault_serializes_refresh_persists_rotation_and_never_reveals_tokens(owner, monkeypatch):
    identity, config = saved_profile(owner, expires_at=0)
    calls, results = [], []
    def fetch(url, **fields):
        calls.append(fields)
        time.sleep(.02)
        return grant(access_token='next-access', refresh_token='next-refresh')
    monkeypatch.setattr(auth, 'request', fetch)
    threads = [threading.Thread(target=lambda: results.append(owner.lifecycle.chatgpt_access_token(identity, config))) for _ in range(5)]
    for thread in threads: thread.start()
    for thread in threads: thread.join(2)
    assert len(calls) == 1 and results == ['next-access'] * 5
    listed = owner.dispatch({'id': 'list', 'action': 'list_profiles'})
    assert listed['result']['profiles'][0]['chatgpt']['connected'] is True
    assert 'next-access' not in str(listed) and 'next-refresh' not in str(listed)
    assert owner.dispatch({'id': 'reveal', 'action': 'reveal_profile_key', 'credential_id': identity,
        'password': 'fixture-password'})['error'] == 'invalid_request'
    assert b'next-access' not in owner.lifecycle.db_path.read_bytes()
    owner.lifecycle.lock(); owner.lifecycle.unlock('fixture-password')
    assert owner.lifecycle.chatgpt_access_token(identity, config) == 'next-access'
    owner.lifecycle.lock()
    with pytest.raises(Exception): owner.lifecycle.chatgpt_access_token(identity, config)
    assert len(calls) == 1


def callback(url, **fields):
    params = parse_qs(urlsplit(url).query)
    target = params['redirect_uri'][0] + '?' + urlencode({'state': params['state'][0], 'code': 'fixture-code',
        'client_id': 'oaiapp_fixture', **fields})
    with urllib.request.build_opener(urllib.request.ProxyHandler({})).open(target, timeout=3) as result:
        return result.status


def test_real_loopback_pkce_registration_restart_and_cancel(owner, monkeypatch, signing):
    sign, jwks = signing
    calls = []
    def fetch(url, **fields):
        calls.append((url, fields))
        if url == auth.JWKS: return jwks
        if url == auth.TOKEN:
            return grant(id_token=sign(nonce=owner.chatgpt._pending['nonce']))
        if url.endswith('/models'):
            return {'models': [{'visibility': 'hidden', 'slug': 'hidden'},
                               {'visibility': 'list', 'slug': 'fixture-model', 'display_name': 'Fixture'}]}
        pytest.fail('Unexpected endpoint')
    monkeypatch.setattr(auth, 'request', fetch)
    url = owner.chatgpt.begin('chatgpt')
    params = parse_qs(urlsplit(url).query)
    assert params['client_id'] == ['dynamic_agent_client'] and params['agent_name_hint'] == ['Igorek']
    verifier = owner.chatgpt._pending['verifier']
    import hashlib
    assert params['code_challenge'] == [encoded(hashlib.sha256(verifier.encode()).digest())]
    with pytest.raises(urllib.error.HTTPError): callback(url, state='wrong')
    assert not calls and owner.chatgpt.status()['state'] == 'awaiting'
    assert callback(url) == 200
    status = owner.chatgpt.status()
    assert status['state'] == 'connected'
    identity = status['credential_id']
    assert owner.providers.list_profiles()[0]['config']['model'] == 'fixture-model'
    exchange = next(fields['form'] for endpoint, fields in calls if endpoint == auth.TOKEN)
    assert exchange['client_id'] == 'oaiapp_fixture' and exchange['code_verifier'] == verifier
    assert exchange['redirect_uri'] == params['redirect_uri'][0] and exchange['resource'] == auth.RESOURCE
    host = params['ext_agent_host_id']
    owner.chatgpt.cancel()
    again = owner.chatgpt.begin('chatgpt', identity)
    params2 = parse_qs(urlsplit(again).query)
    assert params2['client_id'] == ['oaiapp_fixture'] and params2['ext_agent_host_id'] == host
    assert 'agent_name_hint' not in params2 and 'id_token_hint' not in params2
    owner.chatgpt.cancel()
    assert owner.chatgpt.status()['state'] == 'idle'


@pytest.mark.parametrize('case', ['denied', 'missing_client', 'wrong_identity', 'missing_permission', 'locked'])
def test_failed_signin_never_replaces_account_or_stores_unvalidated_grant(owner, monkeypatch, signing, case):
    sign, jwks = signing
    identity = saved_profile(owner)[0] if case == 'wrong_identity' else ''
    before = owner.lifecycle.get_secret(identity).secret if identity else None
    def fetch(url, **fields):
        if url == auth.JWKS: return jwks
        if url == auth.TOKEN:
            return grant(id_token=sign(nonce=owner.chatgpt._pending['nonce'], sub='another' if identity else 'subject-fixture'),
                         scope='openid profile email' if case == 'missing_permission' else auth.SCOPES)
        return {'models': []}
    monkeypatch.setattr(auth, 'request', fetch)
    url = owner.chatgpt.begin('chatgpt', identity)
    fields = {'error': 'access_denied'} if case == 'denied' else {'client_id': ''} if case == 'missing_client' else {}
    if case == 'locked':
        owner.dispatch({'id': 'lock', 'action': 'lock'})
        owner.lifecycle.unlock('fixture-password')
        assert owner.chatgpt.status()['state'] == 'idle'
    else:
        callback(url, **fields)
        assert owner.chatgpt.status()['state'] == 'failed'
    if identity:
        assert owner.lifecycle.get_secret(identity).secret == before
    else:
        assert not owner.providers.list_profiles()


def test_disconnect_revokes_and_retains_mapping_but_clears_all_tokens(owner, monkeypatch):
    identity, config = saved_profile(owner)
    calls = []
    def fetch(url, **fields):
        calls.append((url, fields))
        return {'issuer': auth.ISSUER, 'revocation_endpoint': auth.ISSUER + '/revoke'} if not fields else {}
    monkeypatch.setattr(auth, 'request', fetch)
    result = owner.dispatch({'id': 'logout', 'action': 'chatgpt_disconnect', 'credential_id': identity, 'password': 'fixture-password'})
    assert result['result']['revoked'] is True
    saved = json.loads(owner.lifecycle.get_secret(identity).secret)
    assert saved['client_id'] == 'oaiapp_fixture'
    assert not any(k in saved for k in ('access_token', 'refresh_token', 'id_token'))
    assert calls[1][1]['form']['token'] == 'fixture-refresh'
    assert result['result']['profiles'][0]['status'] == 'failed'
    with pytest.raises(auth.ChatGPTAuthError): owner.lifecycle.chatgpt_access_token(identity, config)


def test_plan_profile_cannot_be_retargeted_or_replaced_by_an_api_key(owner):
    identity, config = saved_profile(owner)
    for change in ({'api_format': 'openai'}, {'api_key': 'forged-key'}, {'base_url': 'https://evil.invalid/v1'}):
        with pytest.raises(ValueError):
            owner.providers.update(identity, **{k: config[k] for k in ('name', 'model', 'base_url', 'api_format')} | {'api_key': ''} | change)
    assert owner.providers.update(identity, **{k: config[k] for k in ('name', 'base_url', 'api_format')}, model='other-model', api_key='')['changed']


def test_renewal_failure_requires_login_and_never_falls_back_to_host_api_key(owner, monkeypatch):
    identity, config = saved_profile(owner, expires_at=0)
    calls = []
    def fail(*args, **kwargs):
        calls.append(1)
        raise auth.ChatGPTAuthError('chatgpt_login_required')
    monkeypatch.setattr(auth, 'request', fail)
    monkeypatch.setenv('OPENAI_API_KEY', 'wrong-host-key')
    for _ in range(2):
        with pytest.raises(auth.ChatGPTAuthError, match='login_required'):
            owner.lifecycle.chatgpt_access_token(identity, config)
    assert calls == [1]
    listed = owner.providers.list_profiles()[0]
    assert listed['chatgpt']['connected'] is False
    assert listed['chatgpt']['error'] == 'chatgpt_login_required'


def test_revocation_network_failure_still_signs_out_locally_and_reports_it(owner, monkeypatch):
    identity, config = saved_profile(owner)
    monkeypatch.setattr(auth, 'request', lambda *a, **k: (_ for _ in ()).throw(auth.ChatGPTAuthError('chatgpt_network')))
    result = owner.dispatch({'id': 'logout', 'action': 'chatgpt_disconnect', 'credential_id': identity, 'password': 'fixture-password'})
    assert result['result']['revoked'] is False
    assert 'fixture-refresh' not in owner.lifecycle.get_secret(identity).secret


def test_existing_host_proxy_injects_chatgpt_token_without_exposing_it_to_guest(owner, monkeypatch):
    import io
    from factory.core.isolation import egress_auth
    from factory.core.openai_responses import post
    from vault_egress import DesktopVaultEgress
    identity, config = saved_profile(owner)
    owner.lifecycle.check_provider_profile(identity, lambda *_: {'status': 'verified'})
    captured = []
    class Response(io.BytesIO):
        status = 200
        headers = {'Content-Type': 'text/event-stream'}
    class Opener:
        def open(self, request, **kwargs):
            captured.append(request)
            data = {'type': 'response.completed', 'response': {'status': 'completed', 'output': [
                {'type': 'message', 'role': 'assistant', 'content': [{'type': 'output_text', 'text': 'fixture'}]}]}}
            return Response(('data: ' + json.dumps(data) + '\n\n').encode())
    monkeypatch.setattr(egress_auth, '_OPENER', Opener())
    proxy = DesktopVaultEgress(owner.lifecycle, identity, config)
    try:
        plan = proxy.plan(config['base_url'])
        assert plan.guest_env == () and 'fixture-access' not in repr(plan)
        body, _ = post(plan.guest_base_url.replace('10.0.2.2', '127.0.0.1').rstrip('/') + '/responses',
            {'model': 'fixture', 'messages': [{'role': 'user', 'content': 'test'}]}, {'Authorization': 'Bearer forged-guest'}, 3)
        assert body['stop_reason'] == 'end_turn'
        assert captured[0].full_url == auth.RESOURCE + '/responses'
        assert captured[0].get_header('Authorization') == 'Bearer fixture-access'
        assert 'forged-guest' not in str(captured[0].headers)
    finally:
        proxy.close()


def test_owner_authorization_starts_for_the_canonical_ui_name_before_any_account_is_saved(owner):
    from urllib.parse import urlsplit, parse_qs
    invalid = owner.dispatch({'id':'uppercase','action':'chatgpt_begin','name':'CodexPro','credential_id':''})
    assert invalid['error'] == 'invalid_request'
    result = owner.dispatch({'id':'canonical','action':'chatgpt_begin','name':'codexpro','credential_id':''})
    url = urlsplit(result['result']['authorization_url'])
    assert url.scheme == 'https' and url.netloc == 'auth.openai.com'
    assert url.path == '/api/accounts/authorize'
    params = parse_qs(url.query)
    assert params['client_id'] == ['dynamic_agent_client']
    assert 'chatgpt.tokens.use.direct' in params['scope'][0].split()
    assert not owner.providers.list_profiles()
    assert owner.chatgpt.status()['state'] == 'awaiting'
    owner.chatgpt.cancel()
