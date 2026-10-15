"""The host-rendered per-session egress decision (plan todo 40a).

:class:`SessionEgress` is the value the host renders once per session and hands
to the guest with the task: the nftables ruleset (the session's allowlist, the
one local-inference route when enabled, the pinned DNS accepts and the logged
denies) plus the pinned-address map the blocked-host diagnostic needs.

Rendering stays host-side: the allowlist inputs, the DNS pinning and the
local-inference gate live in :mod:`factory.core.isolation.network`, and the
guest only applies the text it receives. The wire section is additive and
optional (like the todo-31 capability sections): an absent section leaves the
guest on the boot-time default-deny baseline, never on an unrestricted path.

The enforcement half (applying and verifying the ruleset in the guest) is
:mod:`factory.core.isolation.egress_enforce`.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Final

from .channel import JsonValue
from .network import build_network_policy
from .network_sources import HostResolver

__all__ = [
    "EgressWireError",
    "SessionEgress",
    "build_session_egress",
]

_UNSUPPORTED_WIRE: Final[str] = "egress section is not an object"


class EgressWireError(ValueError):
    """A malformed ``egress`` section on the wire; never defaulted."""


@dataclass(frozen=True, slots=True)
class SessionEgress:
    """The host-rendered per-session egress decision carried to the guest."""

    profile: str
    ruleset: str
    pinned_hosts: tuple[tuple[str, str], ...] = ()
    allow_hosts: tuple[str, ...] = ()
    local_inference_target: str = ""
    host_local_ports: tuple[int, ...] = ()

    def pinned_map(self) -> dict[str, str]:
        """Pinned address -> allow host, for the blocked-host diagnostic."""
        return {address: host for address, host in self.pinned_hosts}

    def to_wire(self) -> dict[str, JsonValue]:
        wire: dict[str, JsonValue] = {
            "profile": self.profile,
            "ruleset": self.ruleset,
        }
        if self.pinned_hosts:
            wire["pinned_hosts"] = self.pinned_map()
        if self.allow_hosts:
            wire["allow_hosts"] = list(self.allow_hosts)
        if self.local_inference_target:
            wire["local_inference"] = self.local_inference_target
        if self.host_local_ports:
            wire["host_local_ports"] = [int(port) for port in self.host_local_ports]
        return wire

    @classmethod
    def from_wire(cls, value: JsonValue) -> "SessionEgress":
        """Parse the additive ``egress`` section; a bad shape is refused."""
        if not isinstance(value, dict):
            raise EgressWireError(_UNSUPPORTED_WIRE)
        ruleset = value.get("ruleset")
        if not isinstance(ruleset, str) or not ruleset.strip():
            raise EgressWireError("egress ruleset is missing")
        profile = value.get("profile")
        return cls(
            profile=profile if isinstance(profile, str) else "",
            ruleset=ruleset,
            pinned_hosts=_wire_pairs(value.get("pinned_hosts")),
            allow_hosts=_wire_texts(value.get("allow_hosts")),
            local_inference_target=_wire_text(value.get("local_inference")),
            host_local_ports=_wire_ports(value.get("host_local_ports")),
        )


def build_session_egress(
    profile: str,
    *,
    base_url: str = "",
    env: Mapping[str, str] | None = None,
    resolver: HostResolver | None = None,
    host_local_ports: Iterable[int] = (),
    extra_mcp_servers: Iterable[str] = (),
    host_addresses: Iterable[str] | None = None,
) -> SessionEgress:
    """Render the per-session policy once; the guest applies exactly this text."""
    policy = build_network_policy(
        profile,
        base_url=base_url,
        env=env,
        resolver=resolver,
        host_local_ports=host_local_ports,
        extra_mcp_servers=extra_mcp_servers,
        host_addresses=host_addresses,
    )
    route = policy.local_inference
    return SessionEgress(
        profile=policy.profile,
        ruleset=policy.ruleset(),
        pinned_hosts=tuple(sorted(policy.address_hosts().items())),
        allow_hosts=policy.allow_hosts(),
        local_inference_target=(
            route.target if route is not None and route.enabled else ""
        ),
        host_local_ports=policy.host_local_ports,
    )


def _wire_text(value: JsonValue) -> str:
    return value if isinstance(value, str) else ""


def _wire_texts(value: JsonValue) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise EgressWireError("egress allow_hosts is not a list of strings")
    return tuple(str(item) for item in value)


def _wire_pairs(value: JsonValue) -> tuple[tuple[str, str], ...]:
    if value is None:
        return ()
    if not isinstance(value, dict):
        raise EgressWireError("egress pinned_hosts is not an object")
    return tuple(sorted((str(key), str(item)) for key, item in value.items()))


def _wire_ports(value: JsonValue) -> tuple[int, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise EgressWireError("egress host_local_ports is not a list")
    ports: set[int] = set()
    for item in value:
        if isinstance(item, bool) or not isinstance(item, int) or not 1 <= item <= 65535:
            raise EgressWireError(f"egress host_local_ports entry is not a port: {item!r}")
        ports.add(item)
    return tuple(sorted(ports))
