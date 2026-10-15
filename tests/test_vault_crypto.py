from __future__ import annotations

from pathlib import Path

from factory.core.vault.crypto import resolve_passphrase


def test_resolve_passphrase_uses_secure_default_local_file(monkeypatch, tmp_path):
    monkeypatch.delenv("IGOREK_VAULT_PASSPHRASE", raising=False)
    monkeypatch.delenv("IGOREK_VAULT_PASSPHRASE_FILE", raising=False)
    monkeypatch.delenv("HERMES_VAULT_PASSPHRASE", raising=False)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    passphrase_path = tmp_path / ".igorek-agent-factory" / "vault" / "passphrase.txt"
    passphrase_path.parent.mkdir(parents=True)
    passphrase_path.write_text("local-passphrase\n", encoding="utf-8")
    passphrase_path.chmod(0o600)

    assert resolve_passphrase(prompt=False) == "local-passphrase"


def test_resolve_passphrase_ignores_world_readable_default_local_file(monkeypatch, tmp_path):
    monkeypatch.delenv("IGOREK_VAULT_PASSPHRASE", raising=False)
    monkeypatch.delenv("IGOREK_VAULT_PASSPHRASE_FILE", raising=False)
    monkeypatch.delenv("HERMES_VAULT_PASSPHRASE", raising=False)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    passphrase_path = tmp_path / ".igorek-agent-factory" / "vault" / "passphrase.txt"
    passphrase_path.parent.mkdir(parents=True)
    passphrase_path.write_text("local-passphrase\n", encoding="utf-8")
    passphrase_path.chmod(0o644)

    try:
        resolve_passphrase(prompt=False)
    except Exception as exc:
        assert "No Igorek Vault passphrase available" in str(exc)
    else:
        raise AssertionError("world-readable passphrase file must not be accepted")
