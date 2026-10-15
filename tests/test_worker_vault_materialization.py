from __future__ import annotations

from factory.core.worker import (
    _existing_env_for_vault_service,
    _vault_reason_is_missing_credential,
)


def test_existing_env_for_vault_service_finds_tavily_key_without_secret_leak():
    env = {"TAVILY_API_KEY": "tvly-test-secret", "OTHER": "ignored"}

    assert _existing_env_for_vault_service("tavily", env) == {
        "TAVILY_API_KEY": "tvly-test-secret"
    }


def test_existing_env_for_vault_service_ignores_unmapped_service():
    assert _existing_env_for_vault_service(
        "unknown-service",
        {"TAVILY_API_KEY": "tvly-test-secret"},
    ) == {}


def test_vault_reason_is_missing_credential_only_for_missing_records():
    assert _vault_reason_is_missing_credential("credential not found in vault")
    assert not _vault_reason_is_missing_credential(
        "service 'tavily' is not allowed for agent 'cc-backend'"
    )
