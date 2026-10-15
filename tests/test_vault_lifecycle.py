import json
import os
import sqlite3

import pytest
from factory.core.vault.lifecycle import (
    VaultLifecycle, VaultAuthenticationError, VaultBusyError, VaultLockedError,
    VaultMigrationRequired, VaultRecoveryError,
)
from factory.core.vault.vault import Vault


def test_empty_vault_checks_password_and_lock(tmp_path):
    manager = VaultLifecycle(tmp_path / 'vault')
    assert manager.state == 'not_initialized'
    manager.create('correct password')
    assert manager.state == 'unlocked'
    manager.lock()
    assert manager.state == 'locked'
    with pytest.raises(VaultAuthenticationError):
        manager.unlock('wrong password')
    assert manager.state == 'locked'
    manager.unlock('correct password')
    assert manager.list_credentials() == []
    manager.lock()


def test_locked_operations_are_rejected(tmp_path):
    manager = VaultLifecycle(tmp_path / 'vault')
    manager.create('password')
    manager.add_credential('example', 'fake-api-secret', 'api_key')
    manager.lock()
    for operation in (
        lambda: manager.get_secret('example'), manager.list_credentials,
        lambda: manager.add_credential('x', 'secret', 'api_key'),
        lambda: manager.change_passphrase('password', 'new'),
        lambda: manager.export_recovery('recovery phrase'),
    ):
        with pytest.raises(VaultLockedError):
            operation()


def test_single_owner_released_after_lock_and_failed_unlock(tmp_path):
    a = VaultLifecycle(tmp_path / 'vault')
    a.create('password')
    b = VaultLifecycle(a.home)
    with pytest.raises(VaultBusyError):
        b.unlock('password')
    a.lock()
    with pytest.raises(VaultAuthenticationError):
        b.unlock('wrong')
    a.unlock('password')
    a.lock()
    b.unlock('password')
    b.lock()


def test_change_password_is_atomic_and_old_password_stops_working(tmp_path):
    manager = VaultLifecycle(tmp_path / 'vault')
    manager.create('old password')
    manager.add_credential('example', 'secret-one', 'api_key')
    manager.add_credential('other', 'secret-two', 'api_key')
    salt = manager.salt_path.read_bytes()
    with pytest.raises(VaultAuthenticationError):
        manager.change_passphrase('wrong', 'new password')
    assert manager.get_secret('example').secret == 'secret-one'
    manager.change_passphrase('old password', 'new password')
    assert manager.salt_path.read_bytes() == salt
    manager.lock()
    with pytest.raises(VaultAuthenticationError):
        manager.unlock('old password')
    manager.unlock('new password')
    assert manager.get_secret('other').secret == 'secret-two'
    manager.lock()


def test_corrupt_record_rolls_back_password_change(tmp_path):
    manager = VaultLifecycle(tmp_path / 'vault')
    manager.create('old')
    manager.add_credential('a', 'secret-one', 'api_key')
    manager.add_credential('b', 'secret-two', 'api_key')
    with sqlite3.connect(manager.db_path) as conn:
        conn.execute("UPDATE credentials SET encrypted_payload='broken' WHERE service='b'")
    with pytest.raises(Exception):
        manager.change_passphrase('old', 'new')
    manager.lock()
    manager.unlock('old')
    assert manager.get_secret('a').secret == 'secret-one'
    manager.lock()


def test_recovery_is_independent_of_original_password_and_salt(tmp_path):
    manager = VaultLifecycle(tmp_path / 'original')
    manager.create('forgotten password')
    record = manager.add_credential('example', 'secret-api-value', 'api_key', scopes=['read'])
    phrase = manager.generate_recovery_phrase()
    backup = manager.export_recovery(phrase)
    assert b'secret-api-value' not in backup
    assert b'example' not in backup
    assert phrase.encode() not in backup
    original_salt = manager.salt_path.read_bytes()
    manager.lock()
    manager.home.rename(tmp_path / 'offline-original')
    restored = VaultLifecycle.restore_recovery(tmp_path / 'restored', backup, phrase, 'new password')
    assert restored.get_secret('example').secret == 'secret-api-value'
    assert restored.list_credentials()[0].id == record.id
    assert restored.list_credentials()[0].scopes == ['read']
    assert restored.salt_path.read_bytes() != original_salt
    restored.lock()
    restored.unlock('new password')
    restored.lock()


@pytest.mark.parametrize('damage', ['wrong_phrase', 'ciphertext', 'iterations', 'format'])
def test_invalid_recovery_does_not_create_target(tmp_path, damage):
    manager = VaultLifecycle(tmp_path / 'source')
    manager.create('password')
    backup = manager.export_recovery('recovery phrase')
    manager.lock()
    phrase = 'wrong' if damage == 'wrong_phrase' else 'recovery phrase'
    data = json.loads(backup)
    if damage == 'ciphertext':
        data['ciphertext'] = 'AAAA'
    if damage == 'iterations':
        data['iterations'] = 1
    if damage == 'format':
        data['format'] = 'unknown'
    with pytest.raises(VaultRecoveryError):
        VaultLifecycle.restore_recovery(tmp_path / 'target', json.dumps(data).encode(), phrase, 'new')
    assert not (tmp_path / 'target').exists()


def test_recovery_never_overwrites_existing_vault(tmp_path):
    manager = VaultLifecycle(tmp_path / 'source')
    manager.create('password')
    backup = manager.export_recovery('recovery phrase')
    manager.lock()
    target = tmp_path / 'existing'
    target.mkdir()
    (target / 'canary').write_text('keep')
    with pytest.raises(FileExistsError):
        VaultLifecycle.restore_recovery(target, backup, 'recovery phrase', 'new')
    assert (target / 'canary').read_text() == 'keep'


def test_legacy_api_still_works_and_is_not_silently_migrated(tmp_path):
    home = tmp_path / 'legacy'
    old = Vault(home / 'vault.db', home / 'master_key_salt.bin', 'password')
    old.add_credential('example', 'legacy-secret', 'api_key')
    assert old.export_backup()['version'] == 'hvbackup-v1'
    manager = VaultLifecycle(home)
    with pytest.raises(VaultMigrationRequired):
        manager.unlock('password')
    assert old.get_secret('example').secret == 'legacy-secret'
    with sqlite3.connect(old.db_path) as conn:
        assert not conn.execute("SELECT name FROM sqlite_master WHERE name='vault_lifecycle'").fetchall()


def test_storage_permissions_and_no_password_file(tmp_path):
    manager = VaultLifecycle(tmp_path / 'vault')
    manager.create('do-not-save-this-password')
    if os.name == 'nt':
        from factory.core.platform_support.windows_security import read_dacl
        dacl = read_dacl(manager.home.parent, directory=True)
        assert dacl.startswith('D:P')
        assert dacl.count('(A;OICI;FA;;;') == 2
        assert ';;;SY)' in dacl
        assert all(value not in dacl for value in (';;;WD)', ';;;BU)', ';;;AU)'))
    else:
        assert manager.home.stat().st_mode & 0o777 == 0o700
        assert manager.db_path.stat().st_mode & 0o777 == 0o600
        assert manager.salt_path.stat().st_mode & 0o777 == 0o600
    assert not (manager.home / 'passphrase.txt').exists()
    assert b'do-not-save-this-password' not in manager.db_path.read_bytes()
    manager.lock()


def test_directory_alias_cannot_bypass_owner_lock(tmp_path):
    owner = VaultLifecycle(tmp_path / 'vault')
    owner.create('password')
    alias = tmp_path / 'alias'
    if os.name == 'nt':
        import _winapi
        _winapi.CreateJunction(str(owner.home), str(alias))
    else:
        alias.symlink_to(owner.home, target_is_directory=True)
    other = VaultLifecycle(alias)
    try:
        with pytest.raises(VaultBusyError):
            other.unlock('password')
    finally:
        owner.lock()
