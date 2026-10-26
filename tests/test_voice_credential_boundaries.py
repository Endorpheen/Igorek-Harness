"""Voice credential precedence, expiry and malformed upstream data; no network."""
import io
import json
import urllib.error
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from factory.core.realtime_voice import credentials as c
from factory.core.vault import runtime as vault


@pytest.fixture
def keys(monkeypatch):
    monkeypatch.delenv('OPENAI_API_KEY', raising=False)
    monkeypatch.setenv('FACTORY_VAULT_AGENT', 'fixture-fallback')
    monkeypatch.setattr(c, '_KEY_CACHE', {'at': 0, 'key': ''})
    monkeypatch.setattr(c, 'time', SimpleNamespace(monotonic=lambda: 1000))
    monkeypatch.setattr(c.rt_config, 'agent_id', lambda: 'fixture-owner')
    monkeypatch.setattr(c.rt_config, 'ttl_seconds', lambda: 60)
    broker = Mock()
    broker.get_ephemeral_env.return_value = SimpleNamespace(allowed=False)
    services = Mock(return_value=SimpleNamespace(broker=broker))
    monkeypatch.setattr(vault, 'build_services', services)
    return SimpleNamespace(broker=broker, services=services)


def test_environment_key_has_precedence_without_opening_vault(keys, monkeypatch):
    monkeypatch.setenv('OPENAI_API_KEY', '  fixture-environment  ')
    c._KEY_CACHE.update(at=999, key='fixture-cached')
    assert c.openai_api_key() == 'fixture-environment'
    keys.services.assert_not_called()


@pytest.mark.parametrize('age,uses_cache', [(599, True), (600, False), (601, False)])
def test_credential_cache_expiry_is_bounded(keys, age, uses_cache):
    c._KEY_CACHE.update(at=1000-age, key='fixture-cached')
    assert c.openai_api_key() == ('fixture-cached' if uses_cache else '')
    assert keys.services.called is (not uses_cache)


def test_vault_candidates_fall_back_after_denial_and_failure(keys):
    keys.broker.get_ephemeral_env.side_effect = [RuntimeError('fixture unavailable'), SimpleNamespace(allowed=False), SimpleNamespace(allowed=True, env={'OPENAI_API_KEY': ' fixture-granted '})]
    assert c.openai_api_key() == 'fixture-granted'
    assert [row.kwargs['agent_id'] for row in keys.broker.get_ephemeral_env.call_args_list] == ['fixture-owner', 'fixture-fallback', 'codex']
    assert c._KEY_CACHE == {'at': 1000, 'key': 'fixture-granted'}
    assert c.openai_api_key() == 'fixture-granted'
    assert keys.broker.get_ephemeral_env.call_count == 3


def test_duplicate_candidate_is_not_retried(keys, monkeypatch):
    monkeypatch.setattr(c.rt_config, 'agent_id', lambda: 'codex')
    monkeypatch.setenv('FACTORY_VAULT_AGENT', 'codex')
    assert c.openai_api_key() == ''
    assert keys.broker.get_ephemeral_env.call_count == 1


def test_unavailable_vault_returns_no_secret(keys):
    keys.services.side_effect = RuntimeError('fixture locked')
    assert c.openai_api_key() == ''


class Reply(io.BytesIO):
    def __init__(self, raw, headers=None):
        super().__init__(raw)
        self.headers = headers or {}


@pytest.mark.parametrize('raw', [b'\xff', b'{', b'[]', b'null', b'4'])
def test_json_transport_rejects_nonobject_responses(monkeypatch, raw):
    monkeypatch.setattr(c.urllib.request, 'urlopen', Mock(return_value=Reply(raw)))
    with pytest.raises(c.RealtimeError) as error:
        c._post_json('http://fixture', payload={}, bearer='fixture-bearer')
    assert error.value.code == 'invalid_response' and error.value.status == 502
    assert 'fixture-bearer' not in str(error.value)


@pytest.mark.parametrize('status,expected', [(401, 'key_rejected'), (429, 'rate_limited'), (503, 'upstream')])
@pytest.mark.parametrize('operation', ['secret', 'call'])
def test_http_error_does_not_leak_upstream_body(monkeypatch, status, expected, operation):
    error = urllib.error.HTTPError('http://fixture', status, 'fixture private body', {}, io.BytesIO(b'fixture-upstream-secret'))
    monkeypatch.setattr(c.urllib.request, 'urlopen', Mock(side_effect=error))
    with pytest.raises(c.RealtimeError) as result:
        if operation == 'secret':
            c.mint_client_secret(session_payload={}, ttl_s=60, key='fixture-long-key', base_url='http://fixture')
        else:
            c.create_call(sdp='v=0\nm=audio', ephemeral='fixture-ephemeral', base_url='http://fixture')
    assert result.value.code == expected
    assert 'fixture-upstream-secret' not in str(result.value)
    assert 'fixture private body' not in str(result.value)


@pytest.mark.parametrize('operation', ['secret', 'call'])
@pytest.mark.parametrize('error', [urllib.error.URLError('fixture-private-host'), TimeoutError('fixture timeout'), OSError('fixture socket')])
def test_network_failure_has_safe_typed_error(monkeypatch, operation, error):
    monkeypatch.setattr(c.urllib.request, 'urlopen', Mock(side_effect=error))
    with pytest.raises(c.RealtimeError) as result:
        if operation == 'secret':
            c.mint_client_secret(session_payload={}, ttl_s=60, key='fixture', base_url='http://fixture')
        else:
            c.create_call(sdp='v=0\nm=audio', ephemeral='fixture', base_url='http://fixture')
    assert result.value.code == 'network'
    assert 'fixture-private-host' not in str(result.value)


@pytest.mark.parametrize('sdp', ['', 'v=0', 'm=audio', 'v=0\nm=video'])
def test_invalid_sdp_is_rejected_before_network(monkeypatch, sdp):
    network = Mock(side_effect=AssertionError('unexpected network'))
    monkeypatch.setattr(c.urllib.request, 'urlopen', network)
    with pytest.raises(c.RealtimeError) as error:
        c.create_call(sdp=sdp, ephemeral='fixture', base_url='http://fixture')
    assert error.value.code == 'invalid_sdp'
    network.assert_not_called()


@pytest.mark.parametrize('answer', [b'', b'v=0', b'm=audio'])
def test_invalid_provider_answer_is_not_exposed_as_success(monkeypatch, answer):
    monkeypatch.setattr(c.urllib.request, 'urlopen', Mock(return_value=Reply(answer)))
    with pytest.raises(c.RealtimeError) as error:
        c.create_call(sdp='v=0\nm=audio', ephemeral='fixture', base_url='http://fixture')
    assert error.value.code == 'invalid_response'


def test_secret_response_is_reduced_to_contract(monkeypatch):
    network = Mock(return_value=Reply(json.dumps({'value':'fixture-secret','expires_at':100,'debug':'private'}).encode()))
    monkeypatch.setattr(c.urllib.request, 'urlopen', network)
    result = c.mint_client_secret(session_payload={'type':'realtime'}, ttl_s=60, key='fixture-long-key', base_url='http://fixture')
    assert result == {'value':'fixture-secret','expires_at':100}
    request = network.call_args.args[0]
    assert json.loads(request.data)['expires_after']['seconds'] == 60
    assert request.get_header('Authorization') == 'Bearer fixture-long-key'


def test_missing_secret_is_an_error(monkeypatch):
    monkeypatch.setattr(c.urllib.request, 'urlopen', Mock(return_value=Reply(b'{}')))
    with pytest.raises(c.RealtimeError) as error:
        c.mint_client_secret(session_payload={}, ttl_s=60, key='fixture', base_url='http://fixture')
    assert error.value.code == 'invalid_response'


@pytest.mark.parametrize('call_id,key', [('', 'fixture'), ('fixture-call', '')])
def test_incomplete_hangup_does_not_contact_provider(monkeypatch, call_id, key):
    network = Mock(side_effect=AssertionError('unexpected network'))
    monkeypatch.setattr(c.urllib.request, 'urlopen', network)
    c.hangup(provider_call_id=call_id, key=key, base_url='http://fixture')
    network.assert_not_called()


def test_failed_hangup_is_best_effort(monkeypatch):
    network = Mock(side_effect=OSError('fixture offline'))
    monkeypatch.setattr(c.urllib.request, 'urlopen', network)
    assert c.hangup(provider_call_id='fixture-call', key='fixture', base_url='http://fixture') is None
    assert network.call_args.args[0].full_url.endswith('/fixture-call/hangup')
