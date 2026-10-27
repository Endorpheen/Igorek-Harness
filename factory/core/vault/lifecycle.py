"""Explicit single-owner lifecycle for onboarding vaults; legacy APIs stay unchanged."""
from __future__ import annotations

import base64
import json
import os
import secrets
import sqlite3
import tempfile
import threading
from contextlib import closing, contextmanager
from pathlib import Path
from ..platform_support import LockBusyError, exclusive_lock


@contextmanager
def _connection(*args, **kwargs):
    with closing(sqlite3.connect(*args, **kwargs)) as connection, connection:
        yield connection

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from .crypto import (
    NONCE_SIZE, PBKDF2_ITERATIONS, SALT_SIZE, decrypt_secret, derive_key,
    encrypt_secret, load_or_create_salt,
)
from .models import CredentialSecret
from .vault import Vault

def is_loopback_provider(config):
    import ipaddress
    from urllib.parse import urlsplit
    try:
        url = urlsplit(config.get("base_url", ""))
        if url.scheme not in {"http", "https"} or url.username is not None or url.password is not None:
            return False
        return url.hostname == "localhost" or ipaddress.ip_address(url.hostname).is_loopback
    except (ValueError, TypeError):
        return False


VERIFIER = "igorek-vault-lifecycle-v1"
RECOVERY_FORMAT = "igorek-vault-recovery-v1"
MAX_RECOVERY_BYTES = 16 * 1024 * 1024


class VaultLockedError(RuntimeError):
    pass


class VaultAuthenticationError(RuntimeError):
    pass


class VaultBusyError(RuntimeError):
    pass


class VaultMigrationRequired(RuntimeError):
    pass


class VaultRecoveryError(RuntimeError):
    pass


def _passphrase(value: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("Пароль не должен быть пустым.")


class VaultLifecycle:
    def __init__(self, home: Path):
        self.home = Path(home).resolve()
        self._vault: Vault | None = None
        self._owner = None
        self._mutex = threading.RLock()

    @property
    def db_path(self):
        return self.home / "vault.db"

    @property
    def salt_path(self):
        return self.home / "master_key_salt.bin"

    @property
    def state(self) -> str:
        if self._vault is not None:
            return "unlocked"
        if not self.db_path.exists():
            return "not_initialized"
        return "locked"

    def _acquire(self):
        path = self.home.parent / f".{self.home.name}.lifecycle.lock"
        try:
            fd = exclusive_lock(path)
        except LockBusyError:
            raise VaultBusyError("Хранилище уже открыто другим владельцем.") from None
        self._owner = fd

    def _release(self):
        if self._owner is not None:
            os.close(self._owner)
            self._owner = None

    def _require(self) -> Vault:
        if self._vault is None:
            raise VaultLockedError("Сначала разблокируйте хранилище.")
        return self._vault

    @staticmethod
    def _marker(conn):
        try:
            row = conn.execute("SELECT verifier FROM vault_lifecycle WHERE id=1 AND version=1").fetchone()
        except sqlite3.OperationalError:
            raise VaultMigrationRequired("Для старого Vault нужна отдельная явная миграция.") from None
        if row is None:
            raise VaultMigrationRequired("В хранилище нет проверки пароля.")
        return row[0]

    def create(self, passphrase: str) -> None:
        _passphrase(passphrase)
        with self._mutex:
            if self._vault is not None:
                raise VaultBusyError("Хранилище уже открыто.")
            self._publish(passphrase)

    def _publish(self, passphrase, records=None):
        self.home.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        if os.name == 'nt':
            from ..platform_support.windows_security import protect_directory
            protect_directory(self.home.parent)
        self._acquire()
        try:
            if self.home.exists() and (not self.home.is_dir() or any(self.home.iterdir())):
                raise FileExistsError("Создание и восстановление разрешены только в новом каталоге.")
            with tempfile.TemporaryDirectory(prefix=".vault-create-", dir=self.home.parent) as temporary:
                stage = Path(temporary) / "vault"
                stage.mkdir(mode=0o700)
                vault = Vault(stage / "vault.db", stage / "master_key_salt.bin", passphrase)
                if records is not None:
                    converted = []
                    for record in records:
                        record = dict(record)
                        payload = record.pop("payload")
                        CredentialSecret.model_validate_json(payload)
                        record["encrypted_payload"] = encrypt_secret(payload, vault.key)
                        converted.append(record)
                    vault.import_backup({"version": "hvbackup-v1", "credentials": converted})
                with _connection(vault.db_path) as conn:
                    conn.execute("CREATE TABLE vault_lifecycle (id INTEGER PRIMARY KEY CHECK(id=1), version INTEGER NOT NULL, verifier TEXT NOT NULL)")
                    conn.execute("INSERT INTO vault_lifecycle VALUES (1,1,?)", (encrypt_secret(VERIFIER, vault.key),))
                if self.home.exists():
                    self.home.rmdir()
                stage.rename(self.home)
            self._vault = Vault(self.db_path, self.salt_path, passphrase)
        except Exception:
            self._release()
            raise

    def unlock(self, passphrase: str) -> None:
        _passphrase(passphrase)
        with self._mutex:
            if self._vault is not None:
                raise VaultBusyError("Хранилище уже открыто.")
            if not self.db_path.exists():
                raise FileNotFoundError("Хранилище ещё не создано.")
            self._acquire()
            try:
                salt = load_or_create_salt(self.salt_path, create_if_missing=False)
                with _connection(self.db_path.as_uri() + "?mode=ro", uri=True) as conn:
                    marker = self._marker(conn)
                key = derive_key(passphrase, salt)
                try:
                    valid = decrypt_secret(marker, key) == VERIFIER
                except (InvalidTag, ValueError):
                    valid = False
                if not valid:
                    raise VaultAuthenticationError("Пароль не подошёл или хранилище повреждено.")
                self._vault = Vault(self.db_path, self.salt_path, passphrase)
            except Exception:
                self._release()
                raise

    def lock(self) -> None:
        with self._mutex:
            if self._vault is not None:
                self._vault.key = b""
                self._vault = None
            self._release()

    def add_credential(self, *args, **kwargs):
        with self._mutex:
            return self._require().add_credential(*args, **kwargs)

    def list_credentials(self):
        with self._mutex:
            return self._require().list_credentials()

    def list_values(self):
        with self._mutex:
            return [self._value_summary(record)
                    for record in self._require().list_credentials()
                    if record.imported_from == "desktop-value-setup" and record.service.startswith("desktop-value:")
                    and record.credential_type in {"secret", "variable"}]

    def _value_summary(self, record):
        origin = self._require().get_secret(record.id).metadata.get('origin', {'kind': 'owner'})
        return {'credential_id': record.id, 'name': record.service.removeprefix('desktop-value:'),
                'kind': record.credential_type, 'origin': origin}

    def _append_agent_value(self, name, kind, value, origin, digest):
        with self._mutex:
            vault = self._require()
            entries = self.list_values()
            existing = next((entry for entry in entries if entry['name'] == name), None)
            if existing:
                secret = vault.get_secret(existing['credential_id'])
                if existing['kind'] == kind and existing['origin'] == origin and secret.metadata.get('source_digest') == digest:
                    return existing
                from .vault import DuplicateCredentialError
                raise DuplicateCredentialError()
            if sum(entry['origin'].get('task_id') == origin['task_id'] for entry in entries) >= 32:
                raise ValueError()
            record = vault.add_credential('desktop-value:' + name, value, kind, imported_from='desktop-value-setup',
                                          secret_metadata={'origin': origin, 'source_digest': digest})
            return self._value_summary(record)

    def _value_record(self, credential_id):
        vault = self._require()
        record = vault.get_credential(credential_id)
        if record is None or record.id != credential_id or record.imported_from != "desktop-value-setup" \
                or not record.service.startswith("desktop-value:") or record.credential_type not in {"secret", "variable"}:
            raise ValueError()
        return record

    def save_value(self, credential_id, name, kind, value, password):
        import re
        with self._mutex:
            vault = self._require()
            self._reauthenticate(password)
            if not isinstance(name, str) or not 1 <= len(name) <= 128 or name != name.strip() \
                    or any(ord(char) < 32 or ord(char) == 127 for char in name):
                raise ValueError()
            if kind not in {"secret", "variable"} or not isinstance(value, str) or len(value) > 1024 \
                    or (kind == "secret" and not value):
                raise ValueError()
            if kind == "variable" and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,127}", name) is None:
                raise ValueError()
            if not isinstance(credential_id, str):
                raise ValueError()
            record = self._value_record(credential_id) if credential_id else None
            service = "desktop-value:" + name
            from .vault import DuplicateCredentialError
            if any(item.service == service and (record is None or item.id != record.id)
                   for item in vault.list_credentials()):
                raise DuplicateCredentialError()
            if record is None:
                record = vault.add_credential(service, value, kind, imported_from="desktop-value-setup", secret_metadata={'origin': {'kind': 'owner'}})
            else:
                origin = vault.get_secret(record.id).metadata.get('origin', {'kind': 'owner'})
                encrypted = encrypt_secret(CredentialSecret(secret=value, metadata={'origin': origin}).model_dump_json(), vault.key)
                with _connection(self.db_path) as conn:
                    conn.execute("UPDATE credentials SET service=?, credential_type=?, encrypted_payload=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                                 (service, kind, encrypted, record.id))
            return self._value_summary(vault.get_credential(record.id))

    def reveal_value(self, credential_id, password):
        with self._mutex:
            self._reauthenticate(password)
            record = self._value_record(credential_id)
            return self._require().get_secret(record.id).secret

    def import_owner_values(self, entries, password, filename):
        import re
        from .models import CredentialRecord
        from .vault import DuplicateCredentialError
        with self._mutex:
            vault = self._require()
            self._reauthenticate(password)
            if not isinstance(entries, dict) or not 1 <= len(entries) <= 128:
                raise ValueError()
            records = []
            for name, (kind, value) in entries.items():
                if not isinstance(name, str) or re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]{0,127}', name) is None \
                        or kind not in {'secret', 'variable'} or not isinstance(value, str) or len(value) > 1024 or kind == 'secret' and not value:
                    raise ValueError()
                payload = CredentialSecret(secret=value, metadata={'origin': {'kind': 'import', 'filename': filename}})
                records.append(CredentialRecord(service='desktop-value:' + name, credential_type=kind,
                    encrypted_payload=encrypt_secret(payload.model_dump_json(), vault.key), imported_from='desktop-value-setup'))
            with _connection(self.db_path) as conn:
                conn.execute('BEGIN IMMEDIATE')
                for record in records:
                    if conn.execute('SELECT 1 FROM credentials WHERE service=?', (record.service,)).fetchone():
                        raise DuplicateCredentialError()
                    conn.execute('INSERT INTO credentials (id,service,alias,credential_type,encrypted_payload,status,scopes,created_at,updated_at,imported_from,crypto_version) VALUES (?,?,?,?,?,?,?,?,?,?,?)',
                        (record.id,record.service,record.alias,record.credential_type,record.encrypted_payload,record.status.value,'[]',record.created_at.isoformat(),record.updated_at.isoformat(),record.imported_from,record.crypto_version))

    def delete_value(self, credential_id, password):
        with self._mutex:
            self._reauthenticate(password)
            record = self._value_record(credential_id)
            with _connection(self.db_path) as conn:
                conn.execute("DELETE FROM credentials WHERE id=?", (record.id,))

    def list_provider_profiles(self):
        with self._mutex:
            vault = self._require()
            profiles = []
            for record in vault.list_credentials():
                if record.imported_from != "desktop-provider-setup":
                    continue
                secret = vault.get_secret(record.id)
                config = secret.metadata.get("desktop_profile")
                if isinstance(config, dict):
                    check = secret.metadata.get("connection_check")
                    profile = {"credential_id": record.id, "config": config, "status": check["status"] if isinstance(check, dict) else "unverified"}
                    if isinstance(check, dict):
                        profile["check"] = check
                    if config.get('api_format') == 'openai-responses':
                        from ..chatgpt_auth import ChatGPTAuthError
                        try:
                            saved = json.loads(secret.secret)
                            profile['chatgpt'] = {'email': saved.get('email', ''),
                                'registration': saved.get('client_id', ''),
                                'connected': bool(saved.get('access_token')) and not saved.get('auth_error'),
                                'error': saved.get('auth_error', '')}
                            if saved.get('auth_error'):
                                profile['status'] = 'failed'
                                profile['check'] = {**(check or {}), 'status': 'failed', 'reason': saved['auth_error']}
                        except (ValueError, TypeError, ChatGPTAuthError):
                            profile['chatgpt'] = {'connected': False, 'error': 'chatgpt_login_required'}
                    profiles.append(profile)
            return profiles

    def check_provider_profile(self, credential_id: str, checker):
        with self._mutex:
            vault = self._require()
            record = vault.get_credential(credential_id)
            if record is None or record.id != credential_id or record.imported_from != "desktop-provider-setup":
                raise ValueError("Unknown provider profile")
            secret = vault.get_secret(record.id)
            config = secret.metadata.get("desktop_profile")
            if not isinstance(config, dict):
                raise ValueError("Invalid provider profile")
        check = checker(config, secret.secret)
        with self._mutex:
            current_vault = self._require()
            current = current_vault.get_credential(credential_id)
            if current_vault is not vault or current is None or current.encrypted_payload != record.encrypted_payload:
                raise VaultBusyError("Provider profile changed during connection check")
            secret.metadata["connection_check"] = check
            encrypted = encrypt_secret(secret.model_dump_json(), vault.key)
            with _connection(self.db_path) as conn:
                conn.execute("UPDATE credentials SET encrypted_payload=? WHERE id=?", (encrypted, record.id))
            return check

    def update_provider_profile(self, credential_id: str, config: dict, api_key: str = "", *, before_update=None):
        with self._mutex:
            vault = self._require()
            record = vault.get_credential(credential_id)
            if record is None or record.id != credential_id or record.imported_from != "desktop-provider-setup":
                raise ValueError("Unknown provider profile")
            secret = vault.get_secret(record.id)
            previous = secret.metadata.get("desktop_profile")
            if not isinstance(previous, dict) or previous.get("name") != config.get("name"):
                raise ValueError("Profile identity cannot change")
            if 'openai-responses' in {previous.get('api_format'), config.get('api_format')}:
                if (previous.get('api_format') != config.get('api_format') or api_key
                        or config.get('base_url') != 'https://api.openai.com/v1'):
                    raise ValueError('ChatGPT account binding cannot change through API key setup')
            if not isinstance(api_key, str) or (api_key and (not api_key.strip() or any(ord(c) < 32 for c in api_key))):
                raise ValueError("Invalid API key")
            replacement = api_key.strip() if api_key else secret.secret
            if not replacement and not is_loopback_provider(config):
                raise ValueError("Remote provider requires an API key")
            changed = previous != config or replacement != secret.secret
            if changed:
                secret.secret = replacement
                secret.metadata["desktop_profile"] = config
                secret.metadata.pop("connection_check", None)
                encrypted = encrypt_secret(secret.model_dump_json(), vault.key)
            if before_update is not None:
                before_update(previous, config)
            if changed:
                with _connection(self.db_path) as conn:
                    conn.execute("UPDATE credentials SET encrypted_payload=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                                 (encrypted, record.id))
            return {"credential_id": record.id, "config": config, "changed": changed}

    def manage_provider_profile(self, credential_id, password, *, new_name=None, before_update=None):
        import re
        with self._mutex:
            self._reauthenticate(password)
            vault = self._require()
            record = vault.get_credential(credential_id)
            if record is None or record.id != credential_id or record.imported_from != "desktop-provider-setup":
                raise ValueError("Unknown provider profile")
            secret = vault.get_secret(record.id)
            previous = secret.metadata.get("desktop_profile")
            if not isinstance(previous, dict):
                raise ValueError("Unknown provider profile")
            config = dict(previous)
            if new_name is not None:
                if not isinstance(new_name, str) or not re.fullmatch(r"[a-z][a-z0-9-]{0,39}", new_name):
                    raise ValueError("Invalid profile name")
                if any(p["credential_id"] != credential_id and p["config"]["name"] == new_name for p in self.list_provider_profiles()):
                    raise ValueError("Duplicate profile name")
                config["name"] = new_name
            if before_update:
                before_update(previous, config if new_name is not None else {})
            with _connection(self.db_path) as conn:
                if new_name is None:
                    conn.execute("DELETE FROM credentials WHERE id=?", (record.id,))
                else:
                    secret.metadata["desktop_profile"] = config
                    encrypted = encrypt_secret(secret.model_dump_json(), vault.key)
                    conn.execute("UPDATE credentials SET service=?, encrypted_payload=?, updated_at=CURRENT_TIMESTAMP WHERE id=?",
                                 ("desktop-profile:" + new_name, encrypted, record.id))
            return {"credential_id": record.id, "deleted": new_name is None, "name": config["name"]}

    def provider_runtime_headers(self, credential_id: str, config: dict, api_format: str):
        """Issue auth only to a server-bound profile, never from task metadata."""
        with self._mutex:
            vault = self._require()
            record = vault.get_credential(credential_id)
            if record is None or record.id != credential_id or record.imported_from != "desktop-provider-setup":
                raise ValueError("Unknown provider profile")
            secret = vault.get_secret(record.id)
            go = False
            if config.get('api_format') != api_format:
                from ..opencode_go_catalog import is_opencode_go_url
                go = is_opencode_go_url(str(config.get('base_url') or ''))
            allowed_format = config.get("api_format") == api_format or (
                go and api_format in {'openai', 'anthropic', 'openai-responses', 'opencode-responses', 'google'})
            if secret.metadata.get("desktop_profile") != config or not allowed_format:
                raise ValueError("Provider binding changed")
            if secret.metadata.get("connection_check", {}).get("status") != "verified":
                raise ValueError("Provider is not verified")
            if api_format == 'openai-responses' and not go:
                from ..chatgpt_usage import ResponseHeaders, UsageStore
                return ResponseHeaders({'Authorization': 'Bearer ' + self.chatgpt_access_token(credential_id, config)},
                    UsageStore(self.home, credential_id, config).observe)
            if not secret.secret:
                if not is_loopback_provider(config):
                    raise ValueError("Remote provider requires an API key")
                return {"anthropic-version": "2023-06-01"} if api_format == "anthropic" else {}
            if api_format == 'google':
                return {'x-goog-api-key': secret.secret}
            return {"x-api-key": secret.secret} if api_format == "anthropic" else {"Authorization": "Bearer " + secret.secret}

    def web_search_runtime_headers(self, credential_id: str, config: dict):
        return self.web_search_runtime_connection(credential_id, config)[1]

    def web_search_selection(self):
        with self._mutex:
            vault = self._require()
            record = vault.get_credential("desktop-value:IGOREK_SEARCH_PROVIDER")
            if record is None:
                return "tavily"
            secret = vault.get_secret(record.id)
            if record.imported_from != "desktop-value-setup" or secret.metadata.get("origin", {}).get("kind") not in {"owner", "import"}:
                raise ValueError("Search selection must be configured by the owner")
            if secret.secret not in {"tavily", "exa", "serper", "off"}:
                raise ValueError("Unknown search provider")
            return secret.secret

    def web_search_runtime_connection(self, credential_id: str, config: dict):
        """Authorize one fixed web service for the exact verified worker binding."""
        with self._mutex:
            vault = self._require()
            record = vault.get_credential(credential_id)
            if record is None or record.id != credential_id or record.imported_from != "desktop-provider-setup":
                raise ValueError("Unknown provider profile")
            binding = vault.get_secret(record.id).metadata
            if binding.get("desktop_profile") != config or binding.get("connection_check", {}).get("status") != "verified":
                raise ValueError("Provider binding changed or is not verified")
            provider = self.web_search_selection()
            names = {"tavily": "TAVILY_API_KEY", "exa": "EXA_API_KEY", "serper": "SERPER_API_KEY"}
            if provider not in names:
                raise ValueError("Desktop web search is disabled")
            candidates = [entry for entry in vault.list_credentials()
                if entry.service == "desktop-value:" + names[provider]
                and entry.imported_from == "desktop-value-setup"
                and entry.credential_type == "secret"
                and entry.status not in {"invalid", "expired"}]
            if not candidates:
                raise ValueError("Desktop web search credential is not configured")
            if len(candidates) != 1:
                raise ValueError("Desktop web search credential is ambiguous")
            secret = vault.get_secret(candidates[0].id)
            if secret.metadata.get("origin", {}).get("kind") not in {"owner", "import"}:
                raise ValueError("Web search requires an owner-configured credential")
            key = secret.secret.strip()
            if not key or any(ord(c) < 32 or ord(c) == 127 for c in key):
                raise ValueError("Invalid web search credential")
            headers = {"Authorization": "Bearer " + key} if provider == "tavily" else {"x-api-key": key}
            return provider, headers

    def _chatgpt_record(self, credential_id, config=None):
        vault = self._require()
        record = vault.get_credential(credential_id)
        if record is None or record.imported_from != 'desktop-provider-setup':
            raise ValueError('Unknown ChatGPT profile')
        secret = vault.get_secret(record.id)
        bound = secret.metadata.get('desktop_profile', {})
        if (bound.get('api_format') != 'openai-responses' or bound.get('base_url') != 'https://api.openai.com/v1'
                or config is not None and bound != config):
            raise ValueError('ChatGPT profile binding changed')
        return vault, record, secret

    def _store_provider_secret(self, vault, record, secret):
        encrypted = encrypt_secret(secret.model_dump_json(), vault.key)
        with _connection(self.db_path) as conn:
            conn.execute('UPDATE credentials SET encrypted_payload=?, updated_at=CURRENT_TIMESTAMP WHERE id=?',
                         (encrypted, record.id))

    def chatgpt_access_token(self, credential_id, config=None):
        from ..chatgpt_auth import ChatGPTAuthError, refresh
        # The Vault's exclusive process lease and mutex serialize rotating refreshes.
        with self._mutex:
            vault, record, secret = self._chatgpt_record(credential_id, config)
            saved = json.loads(secret.secret)
            try:
                renewed = refresh(saved)
            except ChatGPTAuthError as error:
                if error.code in {'chatgpt_login_required', 'chatgpt_plan_permission_missing', 'chatgpt_auth_failed'}:
                    saved['auth_error'] = error.code
                    secret.secret = json.dumps(saved)
                    self._store_provider_secret(vault, record, secret)
                raise
            if renewed != saved:
                secret.secret = json.dumps(renewed)
                self._store_provider_secret(vault, record, secret)
            return renewed['access_token']

    def disconnect_chatgpt(self, credential_id):
        from ..chatgpt_auth import revoke
        with self._mutex:
            vault, record, secret = self._chatgpt_record(credential_id)
            saved = json.loads(secret.secret)
            confirmed = False
            try:
                confirmed = revoke(saved)
            except Exception:
                pass
            for field in ('access_token', 'refresh_token', 'id_token'):
                saved.pop(field, None)
            saved['auth_error'] = 'chatgpt_login_required'
            secret.secret = json.dumps(saved)
            secret.metadata.pop('connection_check', None)
            self._store_provider_secret(vault, record, secret)
            return {'revoked': confirmed}

    def get_secret(self, name):
        with self._mutex:
            return self._require().get_secret(name)

    def _reauthenticate(self, passphrase: str) -> None:
        self._require()
        _passphrase(passphrase)
        salt = load_or_create_salt(self.salt_path, create_if_missing=False)
        with _connection(self.db_path.as_uri() + "?mode=ro", uri=True) as conn:
            marker = self._marker(conn)
        try:
            valid = decrypt_secret(marker, derive_key(passphrase, salt)) == VERIFIER
        except (InvalidTag, ValueError):
            valid = False
        if not valid:
            raise VaultAuthenticationError("Текущий пароль не подошёл или хранилище повреждено.")

    def authenticate(self, passphrase: str) -> None:
        with self._mutex:
            self._reauthenticate(passphrase)

    def reveal_secret(self, name: str, passphrase: str):
        with self._mutex:
            self._reauthenticate(passphrase)
            return self._require().get_secret(name)

    def export_authenticated_recovery(self, passphrase: str, recovery_phrase: str) -> bytes:
        with self._mutex:
            self._reauthenticate(passphrase)
            return self.export_recovery(recovery_phrase)

    def change_passphrase(self, current: str, new: str) -> None:
        _passphrase(current)
        _passphrase(new)
        with self._mutex:
            vault = self._require()
            salt = load_or_create_salt(self.salt_path)
            old_key, new_key = derive_key(current, salt), derive_key(new, salt)
            with _connection(self.db_path) as conn:
                conn.execute("BEGIN IMMEDIATE")
                try:
                    valid = decrypt_secret(self._marker(conn), old_key) == VERIFIER
                except (InvalidTag, ValueError):
                    valid = False
                if not valid:
                    raise VaultAuthenticationError("Текущий пароль не подошёл или хранилище повреждено.")
                rows = conn.execute("SELECT id, encrypted_payload FROM credentials").fetchall()
                for record_id, encrypted in rows:
                    clear = decrypt_secret(encrypted, old_key)
                    conn.execute("UPDATE credentials SET encrypted_payload=? WHERE id=?", (encrypt_secret(clear, new_key), record_id))
                conn.execute("UPDATE vault_lifecycle SET verifier=? WHERE id=1", (encrypt_secret(VERIFIER, new_key),))
            # Salt is immutable: verifier and all payloads change in one SQLite transaction.
            vault.key = new_key

    @staticmethod
    def generate_recovery_phrase() -> str:
        return secrets.token_urlsafe(32)

    def export_recovery(self, recovery_phrase: str) -> bytes:
        _passphrase(recovery_phrase)
        with self._mutex:
            vault = self._require()
            records = vault.export_backup()["credentials"]
            for record in records:
                record["payload"] = decrypt_secret(record.pop("encrypted_payload"), vault.key)
            clear = json.dumps({"records": records}, ensure_ascii=False).encode()
            if len(clear) > MAX_RECOVERY_BYTES // 2:
                raise VaultRecoveryError("Хранилище слишком большое для этого формата восстановления.")
            salt, nonce = os.urandom(SALT_SIZE), os.urandom(NONCE_SIZE)
            ciphertext = AESGCM(derive_key(recovery_phrase, salt)).encrypt(nonce, clear, RECOVERY_FORMAT.encode())
            return json.dumps({"format": RECOVERY_FORMAT, "kdf": "pbkdf2-sha256", "iterations": PBKDF2_ITERATIONS,
                               "salt": base64.b64encode(salt).decode(), "nonce": base64.b64encode(nonce).decode(),
                               "ciphertext": base64.b64encode(ciphertext).decode()}).encode()

    @classmethod
    def restore_recovery(cls, home: Path, backup: bytes, recovery_phrase: str, new_passphrase: str):
        _passphrase(recovery_phrase)
        _passphrase(new_passphrase)
        try:
            if not isinstance(backup, bytes) or len(backup) > MAX_RECOVERY_BYTES:
                raise ValueError()
            data = json.loads(backup)
            if data["format"] != RECOVERY_FORMAT or data["kdf"] != "pbkdf2-sha256" or data["iterations"] != PBKDF2_ITERATIONS:
                raise ValueError()
            salt, nonce, ciphertext = (base64.b64decode(data[field], validate=True) for field in ("salt", "nonce", "ciphertext"))
            if len(salt) != SALT_SIZE or len(nonce) != NONCE_SIZE:
                raise ValueError()
            clear = AESGCM(derive_key(recovery_phrase, salt)).decrypt(nonce, ciphertext, RECOVERY_FORMAT.encode())
            records = json.loads(clear)["records"]
            if not isinstance(records, list):
                raise ValueError()
            ids, aliases = set(), set()
            for record in records:
                CredentialSecret.model_validate_json(record["payload"])
                identity = (record["service"], record["alias"])
                if record["id"] in ids or identity in aliases:
                    raise ValueError()
                ids.add(record["id"])
                aliases.add(identity)
        except (ValueError, KeyError, TypeError, InvalidTag):
            raise VaultRecoveryError("Копия повреждена, формат не поддерживается или фраза восстановления неверна.") from None
        result = cls(home)
        try:
            with result._mutex:
                result._publish(new_passphrase, records)
        except (ValueError, KeyError, TypeError):
            raise VaultRecoveryError("Копия содержит некорректные записи.") from None
        return result
