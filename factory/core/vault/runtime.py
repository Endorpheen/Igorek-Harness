from __future__ import annotations

from dataclasses import dataclass

from factory.core.vault.audit import AuditLogger
from factory.core.vault.broker import Broker
from factory.core.vault.config import AppSettings, get_settings
from factory.core.vault.crypto import resolve_passphrase
from factory.core.vault.policy import PolicyEngine
from factory.core.vault.verifier import Verifier
from factory.core.vault.vault import Vault


@dataclass(frozen=True)
class VaultServices:
    settings: AppSettings
    policy: PolicyEngine
    vault: Vault
    audit: AuditLogger
    verifier: Verifier
    broker: Broker


def build_services(
    *,
    prompt: bool = False,
    passphrase: str | None = None,
    verifier_timeout_seconds: int = 10,
) -> VaultServices:
    settings = get_settings()
    policy = PolicyEngine.from_yaml(settings.effective_policy_path)
    policy.write_default(settings.effective_policy_path)
    resolved_passphrase = resolve_passphrase(passphrase, prompt=prompt)
    vault = Vault(settings.db_path, settings.salt_path, resolved_passphrase)
    audit = AuditLogger(settings.db_path)
    verifier = Verifier(timeout_seconds=verifier_timeout_seconds)
    broker = Broker(vault=vault, policy=policy, verifier=verifier, audit=audit)
    return VaultServices(
        settings=settings,
        policy=policy,
        vault=vault,
        audit=audit,
        verifier=verifier,
        broker=broker,
    )
