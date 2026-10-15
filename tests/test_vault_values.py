import sys
from pathlib import Path

import pytest

from factory.core.vault.lifecycle import VaultLifecycle
from factory.core.vault.management import VaultManagement, VaultManagementDenied, VaultPrincipal


@pytest.fixture
def owner(tmp_path):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'desktop'))
    from vault_owner import OwnerVault
    vault = OwnerVault(tmp_path)
    vault.service.create('fixture-password')
    yield vault
    vault.close()


def call(owner, action, **fields):
    return owner.dispatch({'id': 'values', 'action': action, **fields})


def save(owner, **fields):
    return call(owner, 'save_value', credential_id=fields.pop('credential_id', ''),
                name=fields.pop('name', 'DEMO_TOKEN'), kind=fields.pop('kind', 'secret'),
                value=fields.pop('value', 'fixture-private-token'), password=fields.pop('password', 'fixture-password'), **fields)


def test_values_crud_encryption_reopen_and_recovery(owner, tmp_path):
    saved = save(owner)['result']
    entry = saved['saved_value']
    assert saved['values'] == [entry]
    assert 'fixture-private-token' not in str(saved)
    assert b'fixture-private-token' not in owner.lifecycle.db_path.read_bytes()
    assert call(owner, 'list_profiles')['result']['profiles'] == []
    call(owner, 'lock')
    assert call(owner, 'list_values')['error'] == 'vault_locked'
    call(owner, 'unlock', password='fixture-password')
    assert call(owner, 'list_values')['result']['values'] == [entry]
    assert call(owner, 'reveal_value', credential_id=entry['credential_id'], password='fixture-password')['result'] == {
        'secret': 'fixture-private-token', 'expires_in_seconds': 15}
    updated = save(owner, credential_id=entry['credential_id'], name='DEMO_EMPTY', kind='variable', value='')['result']
    assert updated['saved_value']['credential_id'] == entry['credential_id']
    assert call(owner, 'reveal_value', credential_id=entry['credential_id'], password='fixture-password')['result']['secret'] == ''
    phrase = owner.service.generate_recovery_phrase()
    backup = owner.service.export_recovery('fixture-password', phrase)
    restored = VaultLifecycle.restore_recovery(tmp_path / 'restored', backup, phrase, 'restored-password')
    try:
        assert restored.list_values() == updated['values']
        assert restored.reveal_value(entry['credential_id'], 'restored-password') == ''
    finally:
        restored.lock()
    assert call(owner, 'delete_value', credential_id=entry['credential_id'], password='fixture-password')['result']['values'] == []


@pytest.mark.parametrize('action', ['save_value', 'reveal_value', 'delete_value'])
def test_value_mutations_require_password_and_exact_id(owner, action):
    entry = save(owner)['result']['saved_value']
    fields = {'credential_id': entry['credential_id'], 'password': 'wrong-private-password'}
    if action == 'save_value':
        fields.update(name='CHANGED', kind='secret', value='replacement')
    reply = call(owner, action, **fields)
    assert reply['error'] == 'authentication_failed'
    assert 'wrong-private-password' not in str(reply)
    fields.update(password='fixture-password', credential_id='desktop-value:DEMO_TOKEN')
    assert call(owner, action, **fields)['error'] == 'invalid_request'
    assert call(owner, 'list_values')['result']['values'] == [entry]
    assert owner.lifecycle.reveal_value(entry['credential_id'], 'fixture-password') == 'fixture-private-token'


def test_other_credentials_and_profiles_are_not_values(owner):
    profile = owner.providers.save(name='demo', model='m', base_url='https://example.com/v1', api_format='openai', api_key='fixture-api-key')
    unrelated = owner.service.add_credential('unrelated', 'fixture-other-key', 'api_key')
    for credential_id in [profile['credential_id'], unrelated.id]:
        assert save(owner, credential_id=credential_id)['error'] == 'invalid_request'
        for action in ['reveal_value', 'delete_value']:
            assert call(owner, action, credential_id=credential_id, password='fixture-password')['error'] == 'invalid_request'
    assert call(owner, 'list_values')['result']['values'] == []
    assert owner.lifecycle.get_secret(unrelated.id).secret == 'fixture-other-key'
    assert owner.lifecycle.get_secret(profile['credential_id']).secret == 'fixture-api-key'


@pytest.mark.parametrize('fields', [
    {'name': ' bad'}, {'name': 'BAD\nNAME'}, {'name': 'A' * 129}, {'name': ''},
    {'name': 'bad-name', 'kind': 'variable'}, {'name': '1BAD', 'kind': 'variable'},
    {'kind': 'api_key'}, {'value': ''}, {'value': 'x' * 1025}, {'value': 1},
    {'password': ''}, {'role': 'owner'},
])
def test_invalid_value_fields_never_write(owner, fields):
    assert save(owner, **fields)['error'] == 'invalid_request'
    assert call(owner, 'list_values')['result']['values'] == []


def test_duplicate_names_and_whitespace_preservation(owner):
    entry = save(owner, name='Произвольный секрет', value='  exact value\n')['result']['saved_value']
    assert save(owner, name='Произвольный секрет')['error'] == 'profile_exists'
    assert owner.lifecycle.reveal_value(entry['credential_id'], 'fixture-password') == '  exact value\n'
    second = save(owner, name='SECOND')['result']['saved_value']
    assert save(owner, credential_id=second['credential_id'], name=entry['name'])['error'] == 'profile_exists'
    assert len(owner.service.list_values()) == 2


@pytest.mark.parametrize('action', ['list_values', 'save_value', 'reveal_value', 'delete_value'])
def test_interface_lock_rejects_value_operations(owner, action):
    entry = save(owner)['result']['saved_value']
    owner.interface_locked = True
    fields = {} if action == 'list_values' else {'credential_id': entry['credential_id'], 'password': 'fixture-password'}
    if action == 'save_value':
        fields.update(name='CHANGED', kind='secret', value='replacement')
    assert call(owner, action, **fields)['error'] == 'interface_locked'
    owner.interface_locked = False
    assert owner.lifecycle.reveal_value(entry['credential_id'], 'fixture-password') == 'fixture-private-token'


@pytest.mark.parametrize('principal', [VaultPrincipal.DEVICE, VaultPrincipal.TOOL, 'owner'])
def test_values_require_owner_principal(tmp_path, principal):
    lifecycle = VaultLifecycle(tmp_path)
    service = VaultManagement(lifecycle, principal=principal)
    for operation in [service.list_values,
                      lambda: service.save_value('', 'DEMO', 'secret', 'value', 'password'),
                      lambda: service.reveal_value('id', 'password'), lambda: service.delete_value('id', 'password')]:
        with pytest.raises(VaultManagementDenied):
            operation()
    assert not lifecycle.db_path.exists()
