"""Host-side runtime wiring for the host-op approval class (plan todo 22).

The record contract (:mod:`factory.core.isolation.host_op_approval`), the gate
(:mod:`factory.core.isolation.host_op_gate`) and the typed broker (todos 19/20)
are only useful when a real runtime builds them and consumes their decisions.
This module is that construction and intake path:

* :func:`build_host_op_gate` / :func:`build_host_op_runtime` construct the gate
  the way the worker and the mobile bridge run it - the shipped op catalog, the
  documented host action layer, host-config policy and the host-owned audit and
  park files under the data root. Any construction failure yields ``None``, so
  a caller fails closed with a clear reason instead of half-working;
* :class:`HostOpApprovalRuntime.handle_request` is the host-op intake: an op the
  session was already granted runs immediately, everything else goes to the
  broker and is parked for an explicit decision when approval is due.

Resume consumption of stored decisions lives in
:mod:`factory.core.isolation.host_op_resume`.
"""

from __future__ import annotations

import logging
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final

from ..runtime import get_data_root
from .broker import (
    HostOpBroker,
    HostOpBrokerAuthority,
    HostOpCapabilityGrant,
    HostOpResponse,
    HostOpStatus,
)
from .broker_host import JsonlHostOpAudit
from .broker_ops import default_host_op_registry, safe_operation_label
from .broker_seam import HostOpPolicy
from .channel import ChannelMessage, MessageKind
from .errors import IsolationError
from .host_actions import default_host_action_layer
from .host_op_approval import HostOpApprovalError, HostOpApprovalRequest
from .host_op_gate import HostOpApprovalGate
from .host_op_park import JsonlHostOpParkStore

logger = logging.getLogger(__name__)

__all__ = [
    "HostOpApprovalRuntime",
    "HostOpIntake",
    "build_host_op_gate",
    "build_host_op_runtime",
    "host_op_audit_path",
    "host_op_park_path",
]

_STATE_SUBDIR: Final[str] = "isolation"
_AUDIT_FILE: Final[str] = "host_op_audit.jsonl"
_PARK_FILE: Final[str] = "host_op_parked.json"


def host_op_audit_path() -> Path:
    return get_data_root() / _STATE_SUBDIR / _AUDIT_FILE


def host_op_park_path() -> Path:
    return get_data_root() / _STATE_SUBDIR / _PARK_FILE


def build_host_op_gate(
    *, session_id: str = "", audit_path: Path | None = None, park_path: Path | None = None
) -> HostOpApprovalGate | None:
    """The production gate, or ``None`` when this host cannot run the broker."""
    if sys.platform != 'linux':
        return None
    try:
        registry = default_host_op_registry()
        grant = HostOpCapabilityGrant(
            registry=registry,
            actions=default_host_action_layer(session_id=str(session_id)).actions(),
        )
        authority = HostOpBrokerAuthority(
            policy=HostOpPolicy.from_host_env(),
            trail=JsonlHostOpAudit(audit_path or host_op_audit_path()),
            session_id=str(session_id),
        )
        return HostOpApprovalGate(
            HostOpBroker(grant, authority),
            authority.trail,
            park_store=JsonlHostOpParkStore(park_path or host_op_park_path()),
        )
    except Exception as exc:  # fail closed: no gate, no host action
        logger.warning("host-op runtime unavailable: %s", type(exc).__name__)
        return None


def build_host_op_runtime(
    *, session_id: str = "", audit_path: Path | None = None, park_path: Path | None = None
) -> HostOpApprovalRuntime | None:
    gate = build_host_op_gate(
        session_id=session_id, audit_path=audit_path, park_path=park_path
    )
    return None if gate is None else HostOpApprovalRuntime(gate)


@dataclass(frozen=True, slots=True)
class HostOpIntake:
    """One host-op request the runtime handled: the broker answer, or the park."""

    response: HostOpResponse | None
    parked_request: HostOpApprovalRequest | None

    @property
    def parked(self) -> bool:
        return self.parked_request is not None


class HostOpApprovalRuntime:
    """The host-op intake and decision replay on top of one gate."""

    __slots__ = ("_gate",)

    def __init__(self, gate: HostOpApprovalGate) -> None:
        self._gate = gate

    @property
    def gate(self) -> HostOpApprovalGate:
        return self._gate

    def register_session_grants(self, grants: Sequence[object], *, session_id: str = "") -> tuple[str, ...]:
        """Register persisted ``allow_session`` grants; unusable entries are ignored."""
        registered: list[str] = []
        for raw in grants:
            try:
                registered.append(self._gate.grant_session(str(raw), session_id=session_id))
            except HostOpApprovalError as exc:
                logger.warning(
                    "host-op runtime: ignoring a malformed session grant (%s)", exc.reason
                )
        return tuple(registered)

    def handle_request(
        self,
        message: ChannelMessage,
        *,
        task_id: str = "",
        session_id: str = "",
        reason: str = "",
    ) -> HostOpIntake:
        """Answer one ``host_op`` frame: session-granted ops run, others park."""
        if message.kind is not MessageKind.HOST_OP:
            raise IsolationError("host_op_frame_required", str(message.kind))
        op = safe_operation_label(message.payload.get("op"))
        if self._gate.session_granted(op, session_id=session_id):
            return HostOpIntake(self._gate.broker.handle(message, approval_granted=True), None)
        response = self._gate.broker.handle(message, approval_granted=False)
        if response.status is HostOpStatus.APPROVAL_REQUIRED:
            request = self._gate.park(
                message,
                response,
                task_id=str(task_id),
                session_id=str(session_id),
                reason=str(reason),
            )
            return HostOpIntake(response, request)
        return HostOpIntake(response, None)
