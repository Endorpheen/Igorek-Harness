import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'desktop'))
from vault_owner import OwnerVault
from factory.core.mcp_tasks.access import McpAccess
from factory.core.mcp_tasks.private import PrivateMcp
from factory.core.vault.consumers import McpValueAccess
from factory.core.vault.lifecycle import VaultAuthenticationError, VaultLockedError, VaultLifecycle
from factory.core.vault.management import VaultManagement, VaultManagementDenied, VaultPrincipal


@pytest.fixture
def setup(tmp_path):
    owner = OwnerVault(tmp_path)
    owner.service.create('fixture-password')
    profile = owner.providers.save('fixture-model', 'model', 'https://example.test/v1', 'openai', 'model-fixture-key')
    owner.service.check_provider_profile(profile['credential_id'], lambda *_: {'status': 'verified'})
    (tmp_path / 'mcp').mkdir()
    entry = {'type': 'stdio', 'command': sys.executable, 'args': ['-I', '-c', 'pass']}
    (tmp_path / 'mcp/servers.json').write_text(json.dumps({'mcpServers': {'search': entry, 'other': entry}}))
    access = McpAccess(tmp_path, owner.lifecycle)
    access.save('search', profile['credential_id'], 'allow', 'fixture-password')
    access.save('other', profile['credential_id'], 'allow', 'fixture-password')
    value = owner.service.save_value('', 'Search token', 'secret', 'fixture-search-token', 'fixture-password')
    values = McpValueAccess(tmp_path, owner.lifecycle)
    yield owner, profile, value, values
    owner.close()


def grant(values, value, profile, **changes):
    fields = {'credential_id': value['credential_id'], 'server': 'search', 'profile_id': profile['credential_id'],
              'env_name': 'SEARCH_TOKEN', 'password': 'fixture-password'}
    return values.grant(**(fields | changes))


def test_default_deny_exact_scope_encryption_and_revoke(setup, tmp_path, monkeypatch):
    owner, profile, value, values = setup
    monkeypatch.setenv('SEARCH_TOKEN', 'wrong-inherited-value')
    assert values.snapshot('search', profile['credential_id'], profile['config'])[0] == {}
    with pytest.raises(VaultAuthenticationError):
        grant(values, value, profile, password='wrong')
    result = grant(values, value, profile)
    assert result['grants'][0]['active'] is True
    assert 'fixture-search-token' not in str(result)
    assert b'fixture-search-token' not in owner.lifecycle.db_path.read_bytes()
    assert not list(tmp_path.rglob('*consumer*.json'))
    env, signature = values.issue('search', profile['credential_id'], profile['config'])
    assert env == {'SEARCH_TOKEN': 'fixture-search-token'}
    assert values.snapshot('other', profile['credential_id'], profile['config'])[0] == {}
    with pytest.raises(PermissionError):
        values.snapshot('search', profile['credential_id'], {**profile['config'], 'model': 'different'})
    audit = owner.lifecycle.home / 'consumer-audit.db'
    assert audit.exists() and b'fixture-search-token' not in audit.read_bytes()
    values.revoke(value['credential_id'], result['grants'][0]['id'], 'fixture-password')
    revoked_env, revoked_signature = values.snapshot('search', profile['credential_id'], profile['config'])
    assert revoked_env == {} and revoked_signature != signature
    owner.service.lock()
    with pytest.raises(PermissionError):
        values.snapshot('search', profile['credential_id'], profile['config'])


@pytest.mark.parametrize('change', ['server', 'profile', 'value', 'delete'])
def test_changes_invalidate_previous_permission(setup, tmp_path, change):
    owner, profile, value, values = setup
    grant(values, value, profile)
    previous = values.snapshot('search', profile['credential_id'], profile['config'])[1]
    if change == 'server':
        path = tmp_path / 'mcp/servers.json'
        registry = json.loads(path.read_text()); registry['mcpServers']['search']['args'] = ['-I', '-c', 'print(1)']
        path.write_text(json.dumps(registry))
        assert not values.status(value['credential_id'])['grants'][0]['active']
        with pytest.raises(PermissionError):
            values.snapshot('search', profile['credential_id'], profile['config'])
    elif change == 'profile':
        owner.providers.update(profile['credential_id'], 'fixture-model', 'changed', 'https://example.test/v1', 'openai', '')
        assert not values.status(value['credential_id'])['grants'][0]['active']
        with pytest.raises(PermissionError):
            values.snapshot('search', profile['credential_id'], profile['config'])
    else:
        if change == 'value':
            owner.service.save_value(value['credential_id'], 'Search token', 'secret', 'new-fixture-value', 'fixture-password')
            assert values.status(value['credential_id'])['grants'] == []
        else:
            owner.service.delete_value(value['credential_id'], 'fixture-password')
        env, current = values.snapshot('search', profile['credential_id'], profile['config'])
        assert env == {} and current != previous


@pytest.mark.parametrize('field,value', [('server', 'gmail'), ('server', 'missing'), ('profile_id', 'missing'),
                                        ('env_name', 'BAD-NAME'), ('env_name', '1BAD'), ('env_name', ''), ('env_name', 'A' * 129)])
def test_invalid_grants_do_not_change_storage(setup, field, value):
    owner, profile, entry, values = setup
    with pytest.raises(ValueError):
        grant(values, entry, profile, **{field: value})
    assert values.status(entry['credential_id'])['grants'] == []


def test_duplicate_env_mapping_and_unrelated_credentials_rejected(setup):
    owner, profile, value, values = setup
    grant(values, value, profile)
    second = owner.service.save_value('', 'Another', 'secret', 'another-value', 'fixture-password')
    with pytest.raises(ValueError):
        grant(values, second, profile)
    with pytest.raises(ValueError):
        grant(values, value, profile, credential_id=profile['credential_id'])
    with pytest.raises(VaultAuthenticationError):
        values.revoke(value['credential_id'], values.status(value['credential_id'])['grants'][0]['id'], 'wrong')


def test_grants_survive_recovery_and_empty_variable_is_materialized(setup, tmp_path):
    owner, profile, value, values = setup
    empty = owner.service.save_value('', 'EMPTY_VALUE', 'variable', '', 'fixture-password')
    saved = grant(values, empty, profile, env_name='EMPTY_VALUE')
    assert values.snapshot('search', profile['credential_id'], profile['config'])[0] == {'EMPTY_VALUE': ''}
    backup = owner.service.export_recovery('fixture-password', 'fixture-recovery-phrase')
    restored = VaultLifecycle.restore_recovery(tmp_path / 'restored', backup, 'fixture-recovery-phrase', 'restored-password')
    try:
        assert McpValueAccess(tmp_path, restored).status(empty['credential_id'])['grants'] == saved['grants']
    finally:
        restored.lock()


@pytest.mark.parametrize('principal', [VaultPrincipal.DEVICE, VaultPrincipal.TOOL, 'owner'])
def test_only_owner_can_manage_consumer_grants(setup, tmp_path, principal):
    owner, profile, value, values = setup
    service = VaultManagement(owner.lifecycle, principal=principal)
    for operation in [lambda: service.value_access(tmp_path, value['credential_id']),
                      lambda: service.grant_value_access(tmp_path, value['credential_id'], 'search', profile['credential_id'], 'SEARCH_TOKEN', 'fixture-password'),
                      lambda: service.revoke_value_access(tmp_path, value['credential_id'], 'grant-id', 'fixture-password')]:
        with pytest.raises(VaultManagementDenied):
            operation()


def test_owner_protocol_exact_fields_and_interface_lock(setup):
    owner, profile, value, values = setup
    fields = {'credential_id': value['credential_id'], 'server': 'search', 'profile_id': profile['credential_id'],
              'env_name': 'SEARCH_TOKEN', 'password': 'fixture-password'}
    def call(action, **kwargs):
        return owner.dispatch({'id': 'consumer', 'action': action, **kwargs})
    assert call('grant_value_access', **fields, role='owner')['error'] == 'invalid_request'
    owner.interface_locked = True
    assert call('grant_value_access', **fields)['error'] == 'interface_locked'
    assert call('value_access', credential_id=value['credential_id'])['error'] == 'interface_locked'
    owner.interface_locked = False
    result = call('grant_value_access', **fields)['result']['value_access']
    assert result['grants'][0]['active']
    assert call('revoke_value_access', credential_id=value['credential_id'], grant_id=result['grants'][0]['id'], password='fixture-password')['result']['value_access']['grants'] == []


@pytest.mark.parametrize('local', [False, True])
def test_actual_stdio_receives_only_granted_value_and_model_never_gets_env(setup, tmp_path, monkeypatch, local):
    owner, profile, value, values = setup
    if local:
        profile = owner.providers.update(profile['credential_id'], 'fixture-model', 'model', 'http://127.0.0.1:1234/v1', 'openai', '')
        owner.service.check_provider_profile(profile['credential_id'], lambda *_: {'status': 'verified'})
        McpAccess(tmp_path, owner.lifecycle).save('search', profile['credential_id'], 'local_only', 'fixture-password')
    script = tmp_path / 'mcp-fixture.py'
    script.write_text('''import os,json,sys
for line in sys.stdin:
 request=json.loads(line)
 if 'id' not in request: continue
 method=request['method']
 result={'tools':[{'name':'check_connection','inputSchema':{'type':'object','properties':{}}}]} if method=='tools/list' else {'content':[{'type':'text','text':'connection-ok' if os.environ.get('SEARCH_TOKEN')=='fixture-search-token' and 'UNRELATED_SECRET' not in os.environ and 'MODEL_KEY' not in os.environ else 'wrong-env'}]} if method=='tools/call' else {}
 print(json.dumps({'jsonrpc':'2.0','id':request['id'],'result':result}),flush=True)
''')
    path = tmp_path / 'mcp/servers.json'
    path.write_text(json.dumps({'mcpServers': {'search': {'type': 'stdio', 'command': sys.executable, 'args': ['-I', str(script)]}}}))
    monkeypatch.setenv('UNRELATED_SECRET', 'unrelated-fixture-key')
    monkeypatch.setenv('MODEL_KEY', 'model-fixture-key')
    grant(values, value, profile)
    payloads = []
    def model(url, payload, headers):
        payloads.append(json.loads(json.dumps(payload)))
        assert 'fixture-search-token' not in json.dumps(payload)
        if payload['messages'][-1]['role'] == 'tool':
            return {'choices': [{'message': {'content': payload['messages'][-1]['content']}}]}
        return {'choices': [{'message': {'content': None, 'tool_calls': [{'id': 'call', 'type': 'function', 'function': {'name': 'check_connection', 'arguments': '{}'}}]}}]}
    monkeypatch.setattr('factory.core.mcp_tasks.private.post_json', model)
    runner = PrivateMcp(tmp_path, owner.lifecycle)
    runner.start(profile['credential_id'], 'search', 'Check connection')
    runner.thread.join(5)
    assert not runner.thread.is_alive()
    assert runner.status()['result'] == 'connection-ok'
    assert len(payloads) == 2
    grant_id = values.status(value['credential_id'])['grants'][0]['id']
    values.revoke(value['credential_id'], grant_id, 'fixture-password')
    assert runner.status()['error'] == 'mcp_access_denied'
    assert runner.status()['result'] == ''


def test_revocation_during_model_request_blocks_tools_and_discards_answer(setup, tmp_path, monkeypatch):
    owner, profile, value, values = setup
    result = grant(values, value, profile)
    calls = []
    class Client:
        def __init__(self, *args, **kwargs): self.tools = []
        def start(self): calls.append('start')
        def stop(self): calls.append('stop')
    monkeypatch.setattr('factory.core.mcp_tasks.private.MCPClient', Client)
    def model(*args):
        values.revoke(value['credential_id'], result['grants'][0]['id'], 'fixture-password')
        return {'choices': [{'message': {'content': 'must-be-discarded'}}]}
    monkeypatch.setattr('factory.core.mcp_tasks.private.post_json', model)
    runner = PrivateMcp(tmp_path, owner.lifecycle)
    runner.start(profile['credential_id'], 'search', 'Check connection')
    runner.thread.join(5)
    assert calls == ['start', 'stop']
    assert runner.status()['result'] == '' and runner.history == []


def test_grant_does_not_follow_another_profile(setup):
    owner, profile, value, values = setup
    grant(values, value, profile)
    second = owner.providers.save('second-model', 'model', 'https://example.test/v1', 'openai', 'second-model-key')
    values.access.save('search', second['credential_id'], 'allow', 'fixture-password')
    assert values.snapshot('search', second['credential_id'], second['config'])[0] == {}


def test_regrant_discards_previous_private_context(setup, tmp_path, monkeypatch):
    owner, profile, value, values = setup
    grant(values, value, profile)
    class Client:
        def __init__(self, *args, **kwargs): self.tools = []
        def start(self): pass
        def stop(self): pass
    monkeypatch.setattr('factory.core.mcp_tasks.private.MCPClient', Client)
    lengths = []
    def model(url, payload, headers):
        lengths.append(len(payload['messages']))
        return {'choices': [{'message': {'content': 'fixture summary'}}]}
    monkeypatch.setattr('factory.core.mcp_tasks.private.post_json', model)
    runner = PrivateMcp(tmp_path, owner.lifecycle)
    runner.start(profile['credential_id'], 'search', 'first')
    runner.thread.join(5)
    runner.start(profile['credential_id'], 'search', 'continue', continuation=True)
    runner.thread.join(5)
    grant(values, value, profile)
    runner.start(profile['credential_id'], 'search', 'new permission', continuation=True)
    runner.thread.join(5)
    assert lengths == [2, 4, 2]
