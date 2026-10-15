from __future__ import annotations

import base64
import getpass
import os
from pathlib import Path

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC


CRYPTO_VERSION = "aesgcm-v1"
NONCE_SIZE = 12
SALT_SIZE = 16
PBKDF2_ITERATIONS = 390_000


class MissingPassphraseError(RuntimeError):
    pass


class MissingKeyMaterialError(RuntimeError):
    pass


class CorruptKeyMaterialError(RuntimeError):
    pass


def _read_passphrase_file(path: Path) -> str | None:
    path = path.expanduser()
    if not path.exists():
        return None
    try:
        mode = path.stat().st_mode & 0o777
    except OSError:
        return None
    if mode & 0o077:
        return None
    file_passphrase = path.read_text(encoding="utf-8").strip()
    return file_passphrase or None


def resolve_passphrase(explicit_passphrase: str | None = None, prompt: bool = False) -> str:
    if explicit_passphrase:
        return explicit_passphrase
    env_passphrase = os.environ.get("IGOREK_VAULT_PASSPHRASE")
    if env_passphrase:
        return env_passphrase
    passphrase_file = os.environ.get("IGOREK_VAULT_PASSPHRASE_FILE")
    if passphrase_file:
        file_passphrase = _read_passphrase_file(Path(passphrase_file))
        if file_passphrase:
            return file_passphrase
    compatibility_passphrase = os.environ.get("HERMES_VAULT_PASSPHRASE")
    if compatibility_passphrase:
        return compatibility_passphrase
    default_passphrase_file = Path.home() / ".igorek-agent-factory" / "vault" / "passphrase.txt"
    file_passphrase = _read_passphrase_file(default_passphrase_file)
    if file_passphrase:
        return file_passphrase
    if prompt:
        secret = getpass.getpass("Igorek Vault passphrase: ")
        if secret:
            return secret
    raise MissingPassphraseError(
        "No Igorek Vault passphrase available. Set IGOREK_VAULT_PASSPHRASE, "
        "set IGOREK_VAULT_PASSPHRASE_FILE, or use an interactive prompt."
    )


def load_or_create_salt(path: Path, create_if_missing: bool = False) -> bytes:
    if path.exists():
        salt = path.read_bytes()
        if len(salt) != SALT_SIZE:
            raise CorruptKeyMaterialError(
                f"Salt file {path} has invalid size {len(salt)}; expected {SALT_SIZE} bytes."
            )
        return salt
    if not create_if_missing:
        raise MissingKeyMaterialError(f"Salt file is missing at {path}. Restore the salt before opening the vault.")
    path.parent.mkdir(parents=True, exist_ok=True)
    salt = os.urandom(SALT_SIZE)
    path.write_bytes(salt)
    path.chmod(0o600)
    return salt


def derive_key(passphrase: str, salt: bytes) -> bytes:
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=32,
        salt=salt,
        iterations=PBKDF2_ITERATIONS,
    )
    return kdf.derive(passphrase.encode("utf-8"))


def encrypt_secret(secret: str, key: bytes) -> str:
    nonce = os.urandom(NONCE_SIZE)
    ciphertext = AESGCM(key).encrypt(nonce, secret.encode("utf-8"), None)
    return base64.b64encode(nonce + ciphertext).decode("ascii")


def decrypt_secret(encoded: str, key: bytes) -> str:
    raw = base64.b64decode(encoded.encode("ascii"))
    nonce = raw[:NONCE_SIZE]
    ciphertext = raw[NONCE_SIZE:]
    return AESGCM(key).decrypt(nonce, ciphertext, None).decode("utf-8")
