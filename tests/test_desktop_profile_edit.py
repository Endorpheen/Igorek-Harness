import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'desktop'))
from vault_owner import OwnerVault
from agent_runtime import DesktopAgentRuntime
from factory.core.vault.lifecycle import VaultLifecycle


@pytest.fixture
def setup(tmp_path):
    owner = OwnerVault(tmp_path)
    owner.service.create('test-password')
    profile = owner.providers.save('edit-profile', 'old-model', 'https://old.invalid/v1', 'openai', 'old-private-key')
    owner.service.check_provider_profile(profile['credential_id'], lambda *_: {'status': 'verified', 'reason': 'ok'})
    yield owner, profile
    owner.close()


def update(owner, profile, **changes):
    fields = {'credential_id': profile['credential_id'], 'name': profile['config']['name'],
              'model': profile['config']['model'], 'base_url': profile['config']['base_url'],
              'api_format': profile['config']['api_format'], 'api_key': ''}
    fields.update(changes)
    return owner.dispatch({'id': 'edit-test', 'action': 'update_profile', **fields})


def test_edit_preserves_id_and_key_invalidates_check_and_survives_recovery(setup, tmp_path):
    owner, profile = setup
    original = owner.lifecycle.get_secret(profile['credential_id'])
    reply = update(owner, profile, model='new-model', base_url='https://new.invalid/v1', api_format='anthropic')
    assert reply['result']['updated_profile']['changed'] is True
    saved = reply['result']['profiles'][0]
    assert saved['credential_id'] == profile['credential_id']
    assert saved['config']['model'] == 'new-model'
    assert saved['status'] == 'unverified' and 'check' not in saved
    assert 'old-private-key' not in str(reply)
    secret = owner.lifecycle.get_secret(profile['credential_id'])
    assert secret.secret == original.secret
    assert len(owner.lifecycle.list_credentials()) == 1
    backup = owner.service.export_recovery('test-password', 'recovery-phrase')
    restored = VaultLifecycle.restore_recovery(tmp_path / 'restored', backup, 'recovery-phrase', 'new-password')
    try:
        assert restored.list_provider_profiles() == [saved]
        assert restored.get_secret(profile['credential_id']).secret == 'old-private-key'
    finally:
        restored.lock()
    owner.close()
    restarted = OwnerVault(tmp_path)
    try:
        restarted.service.unlock('test-password')
        assert restarted.providers.list_profiles() == [saved]
    finally:
        restarted.close()


def test_key_only_edit_and_noop_have_correct_check_state(setup):
    owner, profile = setup
    assert update(owner, profile)['result']['updated_profile']['changed'] is False
    assert owner.providers.list_profiles()[0]['status'] == 'verified'
    assert update(owner, profile, api_key='old-private-key')['result']['updated_profile']['changed'] is False
    reply = update(owner, profile, api_key='new-private-key')
    assert reply['result']['profiles'][0]['status'] == 'unverified'
    assert owner.lifecycle.get_secret(profile['credential_id']).secret == 'new-private-key'
    assert 'new-private-key' not in str(reply)
    with pytest.raises(ValueError):
        owner.lifecycle.provider_runtime_headers(profile['credential_id'], profile['config'], 'openai')


@pytest.mark.parametrize('changes', [
    {'name': 'renamed'}, {'credential_id': 'missing'}, {'model': ''}, {'base_url': 'http://remote.invalid/v1'},
    {'api_format': 'unknown'}, {'api_key': ' '}, {'api_key': 'key\nvalue'}, {'api_key': None}, {'api_key': 'x'*1025},
])
def test_invalid_edit_is_nonmutating(setup, changes):
    owner, profile = setup
    before = owner.lifecycle.db_path.read_bytes()
    assert update(owner, profile, **changes)['error'] == 'invalid_request'
    assert owner.lifecycle.db_path.read_bytes() == before
    assert owner.providers.list_profiles()[0]['status'] == 'verified'


def test_locked_or_unrelated_credentials_cannot_be_edited(setup):
    owner, profile = setup
    other = owner.service.add_credential('other', 'other-private', 'api_key')
    assert update(owner, profile, credential_id=other.id)['error'] == 'invalid_request'
    owner.service.lock()
    before = owner.lifecycle.db_path.read_bytes()
    assert update(owner, profile, model='new')['error'] == 'vault_locked'
    assert owner.lifecycle.db_path.read_bytes() == before


def test_edited_public_config_republished_at_next_verified_start(setup, tmp_path):
    owner, profile = setup
    runtime = DesktopAgentRuntime(tmp_path, owner.lifecycle, SimpleNamespace(profile=''))
    owner.agent = runtime
    target = tmp_path / 'profiles/edit-profile.json'
    target.parent.mkdir()
    target.write_text(json.dumps(profile['config']))
    reply = update(owner, profile, model='new-model')
    assert not target.exists()
    assert reply['result']['profiles'][0]['config']['model'] == 'new-model'
    owner.service.check_provider_profile(profile['credential_id'], lambda *_: {'status': 'verified'})
    from test_desktop_agent_runtime import FakeWorker
    runtime.worker_factory = FakeWorker
    assert runtime.start(profile['credential_id'])['running'] is True
    assert json.loads(target.read_text())['model'] == 'new-model'
    assert 'old-private-key' not in target.read_text()
    runtime.stop()


def test_running_profile_edit_is_rejected_without_mutating_key_or_publication(setup, tmp_path):
    owner, profile = setup
    from test_desktop_agent_runtime import FakeWorker
    runtime = DesktopAgentRuntime(tmp_path, owner.lifecycle, SimpleNamespace(profile=''), worker_factory=FakeWorker)
    owner.agent = runtime
    runtime.start(profile['credential_id'])
    target = tmp_path / 'profiles/edit-profile.json'
    before = target.read_bytes(), owner.lifecycle.db_path.read_bytes()
    assert update(owner, profile, model='new-model', api_key='new-key')['error'] == 'stop_agent_first'
    assert (target.read_bytes(), owner.lifecycle.db_path.read_bytes()) == before
    assert runtime.status()['running'] is True
    runtime.stop()
    assert update(owner, profile, model='new-model')['result']['updated_profile']['changed']


@pytest.mark.parametrize('symlink', [False, True])
def test_external_publication_changes_are_never_deleted_or_overwritten(setup, tmp_path, symlink):
    owner, profile = setup
    owner.agent = DesktopAgentRuntime(tmp_path, owner.lifecycle, SimpleNamespace(profile=''))
    target = tmp_path / 'profiles/edit-profile.json'
    target.parent.mkdir()
    external = tmp_path / 'external.json'
    external.write_text(json.dumps(profile['config']) if symlink else '{"external": true}')
    if symlink:
        target.symlink_to(external)
    else:
        target.write_bytes(external.read_bytes())
    before = target.read_bytes(), owner.lifecycle.db_path.read_bytes()
    assert update(owner, profile, model='new-model')['error'] == 'invalid_request'
    assert (target.read_bytes(), owner.lifecycle.db_path.read_bytes()) == before


def test_failed_encryption_retains_original_vault_profile(setup, tmp_path, monkeypatch):
    owner, profile = setup
    owner.agent = DesktopAgentRuntime(tmp_path, owner.lifecycle, SimpleNamespace(profile=''))
    target = tmp_path / 'profiles/edit-profile.json'
    target.parent.mkdir()
    target.write_text(json.dumps(profile['config']))
    import factory.core.vault.lifecycle as module
    original_encrypt = module.encrypt_secret
    def fail_encrypt(*args):
        raise RuntimeError('private-error')
    monkeypatch.setattr(module, 'encrypt_secret', fail_encrypt)
    assert update(owner, profile, model='new-model')['error'] == 'operation_failed'
    assert target.exists()
    monkeypatch.setattr(module, 'encrypt_secret', original_encrypt)
    assert owner.providers.list_profiles()[0]['config'] == profile['config']


def test_failed_database_update_can_regenerate_original_public_config(setup, tmp_path, monkeypatch):
    import sqlite3
    owner, profile = setup
    owner.agent = DesktopAgentRuntime(tmp_path, owner.lifecycle, SimpleNamespace(profile=''))
    target = tmp_path / 'profiles/edit-profile.json'
    target.parent.mkdir()
    target.write_text(json.dumps(profile['config']))
    original_connect = sqlite3.connect
    class FailingConnection(sqlite3.Connection):
        def execute(self, sql, *args, **kwargs):
            if sql.startswith('UPDATE credentials'):
                raise sqlite3.OperationalError('fixture write failed')
            return super().execute(sql, *args, **kwargs)
    def connect(*args, **kwargs):
        return original_connect(*args, **{**kwargs, 'factory': FailingConnection})
    monkeypatch.setattr(sqlite3, 'connect', connect)
    assert update(owner, profile, model='new-model')['error'] == 'operation_failed'
    assert owner.providers.list_profiles()[0]['config'] == profile['config']
    assert not target.exists()
    monkeypatch.setattr(sqlite3, 'connect', original_connect)
    from test_desktop_agent_runtime import FakeWorker
    owner.agent.worker_factory = FakeWorker
    owner.agent.start(profile['credential_id'])
    assert json.loads(target.read_text()) == profile['config']
    owner.agent.stop()
