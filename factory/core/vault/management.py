"""Management boundary for new vaults, separate from scoped runtime secret access.

Trusted server code binds the principal when constructing this service. Never
derive OWNER from a request field, a device name, platform, or loopback address.
The desktop owner pipe exposes a restricted subset; HTTP clients have no access.
"""
from enum import Enum

from .lifecycle import VaultLifecycle


class VaultPrincipal(Enum):
    OWNER = "owner"
    DEVICE = "device"
    TOOL = "tool"


class VaultManagementDenied(PermissionError):
    pass


class VaultManagement:
    def __init__(self, lifecycle: VaultLifecycle, *, principal: VaultPrincipal):
        self._lifecycle = lifecycle
        self._principal = principal

    def _owner(self):
        if self._principal is not VaultPrincipal.OWNER:
            raise VaultManagementDenied("Управление хранилищем доступно только владельцу.")

    def status(self):
        self._owner()
        return {"state": self._lifecycle.state, "reauthentication": "vault_password", "pin_supported": False}

    def create(self, password: str):
        self._owner()
        self._lifecycle.create(password)

    def unlock(self, password: str):
        self._owner()
        self._lifecycle.unlock(password)

    def lock(self):
        self._owner()
        self.cancel_env_import()
        self._lifecycle.lock()

    def _env_import_controller(self):
        from .env_import import EnvImport
        previous = getattr(self, '_env_import', None)
        if previous is None or previous.lifecycle is not self._lifecycle:
            if previous is not None:
                previous.clear()
            self._env_import = EnvImport(self._lifecycle)
        return self._env_import

    def prepare_env_import(self, source):
        self._owner()
        return self._env_import_controller().prepare(source)

    def commit_env_import(self, draft_id, selection, password):
        self._owner()
        return self._env_import_controller().commit(draft_id, selection, password)

    def cancel_env_import(self, draft_id=None):
        self._owner()
        controller = getattr(self, '_env_import', None)
        if controller is not None:
            controller.clear(draft_id)

    def add_credential(self, *args, **kwargs):
        self._owner()
        return self._lifecycle.add_credential(*args, **kwargs)

    def list_provider_profiles(self):
        self._owner()
        return self._lifecycle.list_provider_profiles()

    def list_values(self):
        self._owner()
        return self._lifecycle.list_values()

    def value_access(self, root, credential_id):
        self._owner()
        from .consumers import McpValueAccess
        return McpValueAccess(root, self._lifecycle).status(credential_id)

    def grant_value_access(self, root, credential_id, server, profile_id, env_name, password):
        self._owner()
        from .consumers import McpValueAccess
        return McpValueAccess(root, self._lifecycle).grant(credential_id, server, profile_id, env_name, password)

    def revoke_value_access(self, root, credential_id, grant_id, password):
        self._owner()
        from .consumers import McpValueAccess
        return McpValueAccess(root, self._lifecycle).revoke(credential_id, grant_id, password)

    def save_value(self, credential_id, name, kind, value, password):
        self._owner()
        return self._lifecycle.save_value(credential_id, name, kind, value, password)

    def reveal_value(self, credential_id, password):
        self._owner()
        return self._lifecycle.reveal_value(credential_id, password)

    def delete_value(self, credential_id, password):
        self._owner()
        self._lifecycle.delete_value(credential_id, password)

    def update_provider_profile(self, credential_id, config, api_key="", *, before_update=None):
        self._owner()
        return self._lifecycle.update_provider_profile(credential_id, config, api_key, before_update=before_update)

    def manage_provider_profile(self, credential_id, password, *, new_name=None, before_update=None):
        self._owner()
        return self._lifecycle.manage_provider_profile(credential_id, password, new_name=new_name, before_update=before_update)

    def check_provider_profile(self, credential_id: str, checker):
        self._owner()
        return self._lifecycle.check_provider_profile(credential_id, checker)

    def chatgpt_access_token(self, credential_id, config=None):
        self._owner()
        return self._lifecycle.chatgpt_access_token(credential_id, config)

    def list_credentials(self):
        self._owner()
        # Do not return encrypted payloads as dashboard metadata.
        return [record.model_dump(exclude={"encrypted_payload"}) for record in self._lifecycle.list_credentials()]

    def authenticate(self, password: str):
        self._owner()
        self._lifecycle.authenticate(password)

    def reveal_secret(self, name: str, password: str):
        self._owner()
        return self._lifecycle.reveal_secret(name, password)

    def change_password(self, current: str, new: str):
        self._owner()
        self._lifecycle.change_passphrase(current, new)
        self.cancel_env_import()

    def export_recovery(self, password: str, recovery_phrase: str):
        self._owner()
        return self._lifecycle.export_authenticated_recovery(password, recovery_phrase)

    def generate_recovery_phrase(self):
        self._owner()
        from .lifecycle import VaultLockedError
        if self._lifecycle.state != "unlocked":
            raise VaultLockedError("Сначала разблокируйте хранилище.")
        return self._lifecycle.generate_recovery_phrase()

    def restore_recovery(self, backup: bytes, recovery_phrase: str, new_password: str):
        self._owner()
        if self._lifecycle.state != "not_initialized":
            raise FileExistsError("Восстановление разрешено только до создания хранилища.")
        restored = VaultLifecycle.restore_recovery(self._lifecycle.home, backup, recovery_phrase, new_password)
        self._lifecycle = restored
