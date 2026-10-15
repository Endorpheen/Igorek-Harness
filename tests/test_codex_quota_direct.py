"""Direct quota requests never rotate, clear or copy authorization state."""
import json
import subprocess
from types import SimpleNamespace
import urllib.error

import pytest

from test_codex_quota import _quota_fixture
from factory.core import chatgpt_auth
from factory.core.chatgpt_usage import UsageError, fetch_account_usage, USAGE_URL


@pytest.fixture
def connected(tmp_path, monkeypatch):
    connection, vault, local, auth = _quota_fixture(tmp_path, monkeypatch)
    pending = connection.begin('codexpro')
    connection.confirm('codexpro', pending['link_id'])
    monkeypatch.setattr(subprocess, 'Popen', lambda *a, **k: pytest.fail('Quota launched a process'))
    for name in ('request', 'refresh', 'revoke'):
        monkeypatch.setattr(chatgpt_auth, name, lambda *a, **k: pytest.fail('Quota mutated authorization'))
    monkeypatch.setattr(vault, 'add_credential', lambda *a, **k: pytest.fail('Quota read wrote to Vault'))
    return connection, vault, local, auth


@pytest.mark.parametrize('status', [200, 401, 403, 429, 500])
def test_success_and_refusal_preserve_all_credentials_without_retry(connected, status):
    connection, vault, local, auth = connected
    before = local.read_bytes(), vault.model.secret, vault.links['desktop-codex-quota:p'].secret
    calls = []
    def fetch(token, **kwargs):
        calls.append((token, kwargs))
        if status != 200:
            raise UsageError('quota_access_denied', status)
        return {'usage': {'weekly': {'percent': 44}}, 'fetched_at': '2026-10-15T00:00:00+00:00'}
    connection.fetcher = fetch
    if status == 200:
        assert connection.usage('codexpro')['usage']['weekly']['percent'] == 44
    else:
        with pytest.raises(UsageError) as error: connection.usage('codexpro')
        assert error.value.http_status == status
    assert len(calls) == 1 and calls[0][0] == 'fixture-token'
    assert 'refresh' not in str(calls)
    assert connection.status('codexpro')['state'] == 'connected'
    assert before == (local.read_bytes(), vault.model.secret, vault.links['desktop-codex-quota:p'].secret)


def test_legacy_separate_quota_login_survives_upgrade_without_reauthorization(connected):
    connection, vault, local, auth = connected
    item, _, _, saved = connection.record('codexpro')
    old = json.dumps({'source': 'oauth', 'binding': connection.binding(item, saved),
                      'identity': connection.identity(auth), 'auth': auth})
    vault.links['desktop-codex-quota:p'] = SimpleNamespace(secret=old)
    local.unlink()
    assert connection.usage('codexpro')['usage']['weekly']['percent'] == 44
    assert vault.links['desktop-codex-quota:p'].secret == old


def test_account_change_during_request_cannot_publish_stale_quota(connected):
    connection, vault, local, auth = connected
    original = connection.fetcher
    def race(*args, **kwargs):
        result = original(*args, **kwargs)
        auth['tokens']['account_id'] = 'account-b'
        local.write_text(json.dumps(auth))
        return result
    connection.fetcher = race
    with pytest.raises(UsageError, match='quota_account_mismatch'): connection.usage('codexpro')


def test_profile_token_refusal_does_not_start_any_login_or_create_a_link(tmp_path, monkeypatch):
    connection, vault, local, auth = _quota_fixture(tmp_path, monkeypatch)
    local.unlink()
    saved = json.loads(vault.model.secret)
    saved['access_token'] = 'profile-access'
    vault.model.secret = json.dumps(saved)
    original = vault.model.secret
    calls = []
    def deny(token, **kwargs):
        calls.append(token)
        raise UsageError('quota_access_denied', 403)
    connection.fetcher = deny
    with pytest.raises(UsageError): connection.begin('codexpro')
    assert calls == ['profile-access'] and not connection.pending and not vault.links
    assert vault.model.secret == original


def test_expired_confirmation_can_restart_without_authentication(tmp_path, monkeypatch):
    connection, vault, local, auth = _quota_fixture(tmp_path, monkeypatch)
    original = local.read_bytes(), vault.model.secret
    first = connection.begin('codexpro')
    connection.pending['p']['expires'] = 0
    second = connection.begin('codexpro')
    assert second['link_id'] != first['link_id']
    assert not vault.links and original == (local.read_bytes(), vault.model.secret)


@pytest.mark.parametrize('wrong', [None, 'account', 'email'])
def test_http_get_binds_actual_response_to_confirmed_account(wrong):
    calls = []
    class Response:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def read(self, limit):
            return json.dumps({'account_id': 'other-account' if wrong == 'account' else 'account-a',
                'email': 'other@example.invalid' if wrong == 'email' else 'fixture@example.invalid',
                'rate_limit': {'primary_window': {'used_percent': 50, 'limit_window_seconds': 604800}}}).encode()
    class Opener:
        def open(self, request, timeout):
            assert request.full_url == USAGE_URL and request.get_method() == 'GET'
            assert request.get_header('Authorization') == 'Bearer fixture-access'
            assert request.get_header('Chatgpt-account-id') == 'account-a'
            assert request.data is None
            calls.append(request)
            return Response()
    if wrong:
        with pytest.raises(UsageError, match='quota_account_mismatch'):
            fetch_account_usage('fixture-access', account_id='account-a', expected_email='fixture@example.invalid', opener=Opener())
    else:
        value = fetch_account_usage('fixture-access', account_id='account-a', expected_email='fixture@example.invalid', opener=Opener())
        assert value['usage']['weekly']['percent'] == 50
        assert 'fixture-access' not in str(value) and 'account-a' not in str(value)
    assert len(calls) == 1
