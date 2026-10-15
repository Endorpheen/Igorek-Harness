import pytest

from factory.core.vault.lifecycle import VaultLifecycle, VaultAuthenticationError, VaultLockedError
from factory.core.vault.management import VaultManagement, VaultManagementDenied, VaultPrincipal


@pytest.mark.parametrize('principal', [VaultPrincipal.DEVICE, VaultPrincipal.TOOL, 'owner', None])
def test_untrusted_principals_cannot_manage_vault(tmp_path, principal):
    lifecycle = VaultLifecycle(tmp_path / 'vault')
    service = VaultManagement(lifecycle, principal=principal)
    operations = [service.status, service.lock, service.list_credentials, service.generate_recovery_phrase, service.list_provider_profiles,
                  lambda: service.create('password'), lambda: service.unlock('password'),
                  lambda: service.add_credential('demo', 'secret', 'api_key'),
                  lambda: service.check_provider_profile('id', lambda config, key: {}),
                  lambda: service.update_provider_profile('id', {}, 'key'),
                  lambda: service.reveal_secret('demo', 'password'),
                  lambda: service.authenticate('password'),
                  lambda: service.manage_provider_profile('id', 'password'),
                  lambda: service.change_password('password', 'new'),
                  lambda: service.export_recovery('password', 'phrase'),
                  lambda: service.restore_recovery(b'invalid', 'phrase', 'password')]
    for operation in operations:
        with pytest.raises(VaultManagementDenied):
            operation()
    assert not lifecycle.home.exists()


def test_owner_sensitive_operations_require_current_password(tmp_path):
    lifecycle = VaultLifecycle(tmp_path / 'vault')
    service = VaultManagement(lifecycle, principal=VaultPrincipal.OWNER)
    service.create('old-password')
    service.add_credential('demo', 'fake-secret', 'api_key')
    metadata = service.list_credentials()
    assert 'encrypted_payload' not in metadata[0]
    assert 'fake-secret' not in str(metadata)
    for operation in [lambda: service.reveal_secret('demo', 'wrong'),
                      lambda: service.export_recovery('wrong', 'recovery-phrase'),
                      lambda: service.change_password('wrong', 'new-password')]:
        with pytest.raises(VaultAuthenticationError):
            operation()
    service.change_password('old-password', 'new-password')
    with pytest.raises(VaultAuthenticationError):
        service.reveal_secret('demo', 'old-password')
    assert service.reveal_secret('demo', 'new-password').secret == 'fake-secret'
    backup = service.export_recovery('new-password', 'recovery-phrase')
    restored = VaultManagement(VaultLifecycle(tmp_path / 'restored'), principal=VaultPrincipal.OWNER)
    restored.restore_recovery(backup, 'recovery-phrase', 'restored-password')
    assert restored.reveal_secret('demo', 'restored-password').secret == 'fake-secret'
    restored.lock()
    service.lock()
    with pytest.raises(VaultLockedError):
        service.reveal_secret('demo', 'new-password')
    service.unlock('new-password')
    assert service.status() == {'state': 'unlocked', 'reauthentication': 'vault_password', 'pin_supported': False}
    service.lock()


def test_owner_can_reauthenticate_without_returning_credentials(tmp_path):
    lifecycle = VaultLifecycle(tmp_path / 'vault')
    service = VaultManagement(lifecycle, principal=VaultPrincipal.OWNER)
    with pytest.raises(VaultLockedError):
        service.authenticate('password')
    service.create('password')
    service.add_credential('fixture', 'private-fixture', 'api_key')
    before = service.status()
    with pytest.raises(VaultAuthenticationError):
        service.authenticate('wrong')
    assert service.authenticate('password') is None
    assert service.status() == before
    service.lock()
    with pytest.raises(VaultLockedError):
        service.authenticate('password')
