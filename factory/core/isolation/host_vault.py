"""Host-side Vault materialization behind the broker's ``vault.fetch`` op (todo 20).

The guest may ask the host to materialize one named Vault service's ephemeral
environment for its session. The values stay host-side: :class:`VaultEnvProvider`
is the existing host-side Vault broker (``broker.get_ephemeral_env``) asked as a
fixed host identity, and :class:`VaultEnvTarget` is where the materialized
environment is injected - by default the host runtime process environment, where
the existing consumers read it (``wally_auth`` reads ``WALLY_ACCESS_TOKEN`` and
refreshes it). The guest response carries metadata only - service, env **names**
and ttl - so no secret value ever crosses the channel or reaches the audit trail.

Both boundaries are injectable, so tests record deliveries without touching the
real Vault. Every failure is a typed :class:`HostActionError` with the stable
reason ``vault_fetch_denied`` / ``vault_unavailable`` / ``vault_injection_failed``.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final, Protocol

from .broker_ops import ArgValue, HostActionError
from .channel import JsonValue

__all__ = [
    "DEFAULT_VAULT_TTL_S",
    "HOST_OP_VAULT_AGENT_ID",
    "REASON_INVALID_ACTION_ARGUMENTS",
    "REASON_VAULT_FETCH_DENIED",
    "REASON_VAULT_INJECTION_FAILED",
    "REASON_VAULT_UNAVAILABLE",
    "HostProcessEnvTarget",
    "HostVaultEnvProvider",
    "VaultEnvProvider",
    "VaultEnvTarget",
    "VaultFetchContext",
    "VaultInjection",
    "fetch_vault_secret",
]

logger = logging.getLogger(__name__)

DEFAULT_VAULT_TTL_S: Final[int] = 900

#: The fixed host-side identity the broker acts as when it asks the Vault.
HOST_OP_VAULT_AGENT_ID: Final[str] = "cc-backend"

REASON_VAULT_FETCH_DENIED: Final[str] = "vault_fetch_denied"
REASON_VAULT_UNAVAILABLE: Final[str] = "vault_unavailable"
REASON_VAULT_INJECTION_FAILED: Final[str] = "vault_injection_failed"
REASON_INVALID_ACTION_ARGUMENTS: Final[str] = "invalid_action_arguments"


class VaultEnvProvider(Protocol):
    """Host-side source of one service's ephemeral environment."""

    def ephemeral_env(self, service: str, ttl_s: int) -> Mapping[str, str]: ...


@dataclass(frozen=True, slots=True)
class VaultInjection:
    """One host-side secret injection: the values never leave the host."""

    session_id: str
    service: str
    env: Mapping[str, str]
    ttl_s: int


class VaultEnvTarget(Protocol):
    """Host-side sink the materialized environment is injected into."""

    def deliver(self, injection: VaultInjection) -> None: ...


@dataclass(frozen=True, slots=True)
class VaultFetchContext:
    """The host-side boundaries one ``vault.fetch`` action runs against."""

    vault: VaultEnvProvider
    vault_target: VaultEnvTarget
    session_id: str = ""


@dataclass(frozen=True, slots=True)
class HostVaultEnvProvider:
    """Real provider: the existing host-side Vault broker, asked as a fixed identity."""

    agent_id: str = HOST_OP_VAULT_AGENT_ID

    def ephemeral_env(self, service: str, ttl_s: int) -> Mapping[str, str]:
        try:
            from ..vault.runtime import build_services as build_vault_services

            services = build_vault_services(prompt=False)
            decision = services.broker.get_ephemeral_env(
                service=service, agent_id=self.agent_id, ttl=ttl_s
            )
        except Exception as exc:  # vault boundary: any failure is "unavailable"
            raise HostActionError(
                REASON_VAULT_UNAVAILABLE, type(exc).__name__, executed=False
            ) from exc
        if not decision.allowed:
            raise HostActionError(REASON_VAULT_FETCH_DENIED, decision.reason, executed=False)
        return dict(decision.env)


@dataclass(frozen=True, slots=True)
class HostProcessEnvTarget:
    """Default sink: injects the ephemeral env into the host runtime process.

    The existing host-side consumers read these variables from the environment
    (for Wally: ``wally_auth`` reads ``WALLY_ACCESS_TOKEN`` and refreshes it), so
    an approved ``vault.fetch`` makes the credential available to the host
    egress proxy without crossing the channel. Nothing is persisted and nothing
    is added to the guest environment.
    """

    def deliver(self, injection: VaultInjection) -> None:
        logger.info(
            "vault.fetch injected %s for session %s (env names: %s)",
            injection.service,
            injection.session_id or "<host>",
            sorted(injection.env),
        )
        os.environ.update(injection.env)


def fetch_vault_secret(
    args: Mapping[str, ArgValue], context: VaultFetchContext
) -> Mapping[str, JsonValue]:
    """Materialize one service's env host-side; return metadata only to the guest."""
    service = str(args.get("service") or "")
    if not service:
        raise HostActionError(REASON_INVALID_ACTION_ARGUMENTS, "service", executed=False)
    raw_ttl = args.get("ttl_s")
    ttl_s = raw_ttl if isinstance(raw_ttl, int) and not isinstance(raw_ttl, bool) else DEFAULT_VAULT_TTL_S
    env = context.vault.ephemeral_env(service, ttl_s)
    injection = VaultInjection(
        session_id=context.session_id, service=service, env=dict(env), ttl_s=ttl_s
    )
    try:
        context.vault_target.deliver(injection)
    except Exception as exc:  # injection boundary, reported as typed
        raise HostActionError(REASON_VAULT_INJECTION_FAILED, type(exc).__name__) from exc
    return {
        "service": service,
        "ttl_s": ttl_s,
        "env_names": sorted(injection.env),
        "injected": True,
    }
