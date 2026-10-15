"""Host-owned broker construction surface: capability grant, authority, audit.

Nothing here reads guest input. The :class:`HostOpCapabilityGrant` pairs the
typed op catalog with exactly one host action per catalog op, so a guest can
never add an op the host did not both declare and grant. The
:class:`HostOpBrokerAuthority` refuses any policy whose source is not host
configuration, so a guest-supplied policy cannot even produce a broker. The
:class:`JsonlHostOpAudit` trail is where the broker writes the decision *before*
it executes anything.
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Final, Protocol

from .broker_ops import HostOpAction, HostOpRegistry
from .broker_seam import HOST_POLICY_SOURCE, HostOpPolicy
from .errors import IsolationError

REASON_INCOMPLETE_GRANT: Final[str] = "incomplete_capability_grant"
REASON_GUEST_POLICY_REJECTED: Final[str] = "guest_policy_rejected"


class HostOpTrail(Protocol):
    """Append-only audit sink for host-op decisions and outcomes."""

    def record(self, entry: Mapping[str, object]) -> None: ...


class JsonlHostOpAudit:
    """Append-only JSONL audit sink; created mode 0600, one object per line."""

    __slots__ = ("_path",)

    def __init__(self, path: Path) -> None:
        self._path = path

    def record(self, entry: Mapping[str, object]) -> None:
        line = json.dumps(dict(entry), ensure_ascii=False, sort_keys=True) + "\n"
        self._path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(self._path, os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o600)
        with os.fdopen(fd, "a", encoding="utf-8") as handle:
            handle.write(line)


@dataclass(frozen=True, slots=True)
class HostOpCapabilityGrant:
    """Host-owned grant: the typed op catalog plus one action per catalog op.

    Both halves must match exactly, so a guest can never add an op that has an
    action but no host-declared schema, or a schema whose action the host never
    granted.
    """

    registry: HostOpRegistry
    actions: Mapping[str, HostOpAction]

    def __post_init__(self) -> None:
        registered = set(self.registry.ops)
        granted = set(self.actions)
        if registered != granted:
            raise IsolationError(
                REASON_INCOMPLETE_GRANT,
                f"registry={sorted(registered)} actions={sorted(granted)}",
            )
        object.__setattr__(self, "actions", MappingProxyType(dict(self.actions)))


@dataclass(frozen=True, slots=True)
class HostOpBrokerAuthority:
    """Host-side authority for one broker: host policy, audit trail, session id."""

    policy: HostOpPolicy
    trail: HostOpTrail
    session_id: str = ""

    def __post_init__(self) -> None:
        if self.policy.source != HOST_POLICY_SOURCE:
            raise IsolationError(
                REASON_GUEST_POLICY_REJECTED, f"policy source {self.policy.source!r}"
            )
