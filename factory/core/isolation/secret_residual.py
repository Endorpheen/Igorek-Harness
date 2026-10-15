"""Documented residual risks for credentials that are not fully host-side (todo 18).

A provider or MCP server whose credential cannot stay entirely on the host is
never silently accepted: it produces a :class:`ResidualRisk` entry that names
the subject, the reason, the concrete risk and the planned removal path. The
entries are data, so a test can assert they exist and that they contain no
secret material.

The provider fallback mode string is pinned to ``secret_policy``'s vocabulary by
a regression test (``MCP_RESIDUAL_MODE``); this module deliberately imports
nothing from the policy module so the registry cannot create an import cycle.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final

__all__ = [
    "MCP_RESIDUAL_MODE",
    "ResidualRisk",
    "mcp_credential_residual",
    "provider_residual",
]

MCP_RESIDUAL_MODE: Final[str] = "ephemeral_env"


@dataclass(frozen=True, slots=True)
class ResidualRisk:
    """A credential placement that is not fully host-side, recorded explicitly."""

    subject: str
    mode: str
    reason: str
    risk: str
    mitigation: str
    accepted: bool = True

    def to_dict(self) -> dict[str, object]:
        return {
            "subject": self.subject,
            "mode": self.mode,
            "reason": self.reason,
            "risk": self.risk,
            "mitigation": self.mitigation,
            "accepted": self.accepted,
        }


def provider_residual(provider: str, reason: str, *, mode: str) -> ResidualRisk:
    """The residual for a provider that is not host-proxied."""
    return ResidualRisk(
        subject=provider,
        mode=mode,
        reason=reason,
        risk=(
            f"provider {provider!r} is not host-proxied: its credential path is the "
            "existing host-side ephemeral Vault materialization "
            "(worker._materialize_vault_env, TTL-bounded). In kvm mode the guest "
            "therefore holds no credential for this provider, so the call cannot be "
            "authenticated until the typed vault.fetch host-op (plan todo 19/20) "
            "exists; if a future path passes the ephemeral env into the guest, a "
            "compromised guest could read it for the TTL window."
        ),
        mitigation=(
            "the credential never enters the base image and never crosses the "
            "channel; the guest env scan (secret_scan) refuses any provider token "
            "key or value, and the planned removal path is the host-op broker "
            "vault.fetch (todo 19/20)"
        ),
    )


def mcp_credential_residual(
    servers: Sequence[str], *, mode: str = MCP_RESIDUAL_MODE
) -> tuple[ResidualRisk, ...]:
    """The registered residual for MCP servers whose ephemeral env runs in-guest."""
    entries: list[ResidualRisk] = []
    for name in servers:
        cleaned = str(name).strip()
        if not cleaned:
            continue
        entries.append(
            ResidualRisk(
                subject=f"mcp:{cleaned}",
                mode=mode,
                reason="mcp_ephemeral_env_in_guest",
                risk=(
                    f"MCP server {cleaned!r} starts inside the guest with its "
                    "ephemeral Vault env (plan todo 31 placement), so a compromised "
                    "guest can read that credential while the server process lives."
                ),
                mitigation=(
                    "mark the server host_brokered (MCP_<NAME>_HOST_BROKERED) to keep "
                    "its credential host-side once the todo-19 broker lands; the "
                    "guest holds no long-lived provider key meanwhile"
                ),
            )
        )
    return tuple(entries)
