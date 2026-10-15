"""Host-operation broker policy seam (plan todo 18; todo 19 implements the wire).

The typed host-op broker (``factory/core/isolation/broker.py``) does not exist
yet, so this module pins the invariants it must consume:

* the policy is computed host-side from host configuration only - no function
  here accepts guest-supplied policy, and a policy whose ``source`` is not
  :data:`HOST_POLICY_SOURCE` is rejected outright;
* only operations in the typed registry can ever be allowed - there is no raw
  command, path or shell argument on this interface at all;
* every allowed operation is approval-gated (the existing mobile/TUI approval
  surfaces) and every request/decision is audited through
  :func:`host_op_audit_record`.

Todo 19 replaces the seam with the real broker and must keep these checks; until
then nothing else in the isolation layer grants a guest any host operation.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final

from ..trace_taxonomy import CcTraceEvent

logger = logging.getLogger(__name__)

__all__ = [
    "DEFAULT_ALLOWED_OPS",
    "ENV_HOST_OPS_ALLOW",
    "ENV_HOST_OPS_DENY",
    "HOST_OP_REGISTRY",
    "HOST_POLICY_SOURCE",
    "HostOpDecision",
    "HostOpPolicy",
    "evaluate_host_op_request",
    "host_op_audit_record",
]

HOST_POLICY_SOURCE: Final[str] = "host_config"
ENV_HOST_OPS_ALLOW: Final[str] = "IGOREK_ISOLATION_HOST_OPS_ALLOW"
ENV_HOST_OPS_DENY: Final[str] = "IGOREK_ISOLATION_HOST_OPS_DENY"

#: Every operation the broker may ever know; the minimum set is todo 19's
#: ``runtime.restart``/``runtime.update`` plus todo 20's ``vault.fetch``.
HOST_OP_REGISTRY: Final[frozenset[str]] = frozenset(
    {"runtime.status", "runtime.restart", "runtime.update", "vault.fetch"}
)
DEFAULT_ALLOWED_OPS: Final[frozenset[str]] = frozenset(
    {"runtime.restart", "runtime.update", "vault.fetch"}
)


@dataclass(frozen=True, slots=True)
class HostOpPolicy:
    """Host-side policy for typed host operations."""

    allowed_ops: frozenset[str]
    denied_ops: frozenset[str] = frozenset()
    source: str = HOST_POLICY_SOURCE
    approval_required: bool = True
    audited: bool = True

    @classmethod
    def from_host_env(cls, env: Mapping[str, str] | None = None) -> HostOpPolicy:
        source = os.environ if env is None else env
        allowed = _names(source.get(ENV_HOST_OPS_ALLOW), DEFAULT_ALLOWED_OPS)
        denied = _names(source.get(ENV_HOST_OPS_DENY), frozenset())
        return cls(allowed_ops=allowed, denied_ops=denied)

    def allows(self, op: str) -> bool:
        return op in self.allowed_ops and op not in self.denied_ops


@dataclass(frozen=True, slots=True)
class HostOpDecision:
    """One evaluated host-op request; ``reason`` is a stable machine code."""

    op: str
    allowed: bool
    requires_approval: bool
    audited: bool
    reason: str

    def to_dict(self) -> dict[str, object]:
        return {
            "op": self.op,
            "allowed": self.allowed,
            "requires_approval": self.requires_approval,
            "audited": self.audited,
            "reason": self.reason,
        }


def evaluate_host_op_request(
    op: str,
    *,
    policy: HostOpPolicy,
    approval_granted: bool = False,
) -> HostOpDecision:
    """Decide one typed host-op request from host policy + an approval bit."""
    name = str(op or "")
    if policy.source != HOST_POLICY_SOURCE:
        return HostOpDecision(name, False, False, policy.audited, "guest_policy_rejected")
    if name not in HOST_OP_REGISTRY:
        return HostOpDecision(name, False, False, policy.audited, "unknown_operation")
    if not policy.allows(name):
        return HostOpDecision(name, False, False, policy.audited, "operation_not_allowed")
    if policy.approval_required and not approval_granted:
        return HostOpDecision(name, False, True, policy.audited, "approval_required")
    return HostOpDecision(name, True, policy.approval_required, policy.audited, "approved")


def host_op_audit_record(decision: HostOpDecision) -> dict[str, object]:
    """The taxonomy-shaped audit record for one host-op decision."""
    return {"kind": CcTraceEvent.ISOLATION_HOST_OP, **decision.to_dict()}


def _names(raw: object, default: frozenset[str]) -> frozenset[str]:
    text = str(raw or "").strip()
    if not text:
        return default
    names: set[str] = set()
    for item in text.split(","):
        name = item.strip()
        if not name:
            continue
        if name not in HOST_OP_REGISTRY:
            logger.warning("Ignoring unknown host-op %r (not in the typed registry)", name)
            continue
        names.add(name)
    return frozenset(names)
