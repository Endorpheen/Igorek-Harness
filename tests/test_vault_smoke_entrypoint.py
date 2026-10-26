"""Developer vault smoke acceptance uses real owned crypto/database fixtures."""

from pathlib import Path
import os

import pytest

from factory.core import smoke_vault as smoke


def test_vault_smoke_cli_checks_real_owned_storage_and_redaction(
    tmp_path, monkeypatch, capsys
):
    for key in ["FACTORY_DATA_DIR", "IGOREK_FACTORY_HOME", "HOME"]:
        path = tmp_path / key
        path.mkdir()
        monkeypatch.setenv(key, str(path))
    for key in [
        "IGOREK_VAULT_HOME",
        "IGOREK_VAULT_PASSPHRASE",
        "FACTORY_VAULT_ENABLED",
        "FACTORY_VAULT_SERVICES",
        "FACTORY_VAULT_AGENT",
        "FACTORY_VAULT_TTL_SECONDS",
        "TAVILY_API_KEY",
    ]:
        monkeypatch.delenv(key, raising=False)
    assert smoke.main() == 0
    text = capsys.readouterr().out
    assert "PASS" in text
    assert (
        "fixture-tavily-key" not in text
        and "sk-secretvalue12345678901234567890" not in text
    )
    assert Path(os.environ["IGOREK_VAULT_HOME"]).name == "worker-vault"


def test_smoke_assertion_preserves_diagnostic_reason():
    smoke._assert(True, "not an error")
    with pytest.raises(AssertionError, match="fixture invariant failed"):
        smoke._assert(False, "fixture invariant failed")
