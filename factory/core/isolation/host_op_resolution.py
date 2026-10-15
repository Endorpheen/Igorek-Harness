"""The host-op decision record: what one approval decision did (plan todo 22).

A decision record travels further than the process that made it: the TUI stores
it in resume metadata, the mobile bridge stores it on the task, and the host
runtime replays it later. This module is the only codec for that record, so a
producer and a consumer can never drift on the shape:

* :meth:`HostOpApprovalOutcome.pending_broker` - the TUI handoff: allowed on the
  terminal, still to be executed by the host broker;
* :meth:`HostOpApprovalOutcome.from_record` - the strict parser: an unknown
  class, an unusable op label, a non-boolean ``executed`` or a missing sequence
  is refused, never half-accepted.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Final

from .broker_ops import ArgValue, safe_operation_label
from .channel import JsonValue
from .host_op_approval import (
    REASON_INVALID_DECISION,
    REASON_INVALID_REQUEST,
    REASON_REJECTED_BY_USER,
    HostOpApprovalClass,
    HostOpApprovalError,
    HostOpApprovalRequest,
    narrow_host_op_args,
)

__all__ = [
    "DECISIONS",
    "PENDING_BROKER_STATUS",
    "HostOpApprovalOutcome",
    "rejected_outcome",
]

#: Decisions the existing mobile approval surface can send.
DECISIONS: Final[frozenset[str]] = frozenset({"allow_once", "allow_session", "reject"})

#: The TUI handoff status: approved on the terminal, still to run by the host broker.
PENDING_BROKER_STATUS: Final[str] = "pending_broker"


@dataclass(frozen=True, slots=True)
class HostOpApprovalOutcome:
    """What one host-op decision did: the broker result or the recorded rejection."""

    request_id: str
    op: str
    approval_class: HostOpApprovalClass
    decision: str
    status: str
    reason: str
    executed: bool
    resolved_by: str = ""
    result: Mapping[str, JsonValue] = field(default_factory=dict)
    args: Mapping[str, ArgValue] = field(default_factory=dict)
    request_seq: int = 0
    session_id: str = ""
    task_id: str = ""

    def to_record(self) -> dict[str, JsonValue]:
        return {
            "request_id": self.request_id,
            "op": self.op,
            "approval_class": str(self.approval_class),
            "decision": self.decision,
            "status": self.status,
            "reason": self.reason,
            "executed": self.executed,
            "resolved_by": self.resolved_by,
            "result": dict(self.result),
            "args": dict(self.args),
            "request_seq": self.request_seq,
            "session_id": self.session_id,
            "task_id": self.task_id,
        }

    @classmethod
    def from_record(cls, record: Mapping[str, object]) -> HostOpApprovalOutcome:
        """Parse one stored outcome (a TUI handoff or an executed record), or refuse it."""
        if not isinstance(record, Mapping):
            raise HostOpApprovalError(REASON_INVALID_REQUEST, "not_a_record")
        request_id = str(record.get("request_id") or "").strip()
        if not request_id:
            raise HostOpApprovalError(REASON_INVALID_REQUEST, "empty_request_id")
        op = str(record.get("op") or "")
        if safe_operation_label(op) != op:
            raise HostOpApprovalError(REASON_INVALID_REQUEST, f"invalid_op:{op}")
        try:
            approval_class = HostOpApprovalClass(str(record.get("approval_class") or ""))
        except ValueError as exc:
            raise HostOpApprovalError(
                REASON_INVALID_REQUEST, f"unknown_class:{record.get('approval_class')!r}"
            ) from exc
        decision = str(record.get("decision") or "")
        if decision not in DECISIONS:
            raise HostOpApprovalError(REASON_INVALID_DECISION, decision or "<missing>")
        executed = record.get("executed")
        if not isinstance(executed, bool):
            raise HostOpApprovalError(REASON_INVALID_REQUEST, "invalid_executed")
        request_seq = record.get("request_seq")
        if isinstance(request_seq, bool) or not isinstance(request_seq, int):
            raise HostOpApprovalError(REASON_INVALID_REQUEST, "invalid_request_seq")
        raw_result = record.get("result")
        if raw_result is not None and not isinstance(raw_result, Mapping):
            raise HostOpApprovalError(REASON_INVALID_REQUEST, "invalid_result")
        return cls(
            request_id=request_id,
            op=op,
            approval_class=approval_class,
            decision=decision,
            status=str(record.get("status") or ""),
            reason=str(record.get("reason") or ""),
            executed=executed,
            resolved_by=str(record.get("resolved_by") or ""),
            result=dict(raw_result or {}),
            args=narrow_host_op_args(record.get("args") or {}),
            request_seq=request_seq,
            session_id=str(record.get("session_id") or ""),
            task_id=str(record.get("task_id") or ""),
        )

    @classmethod
    def pending_broker(
        cls, request: HostOpApprovalRequest, *, decision: str, resolved_by: str
    ) -> HostOpApprovalOutcome:
        """A TUI handoff: approved on the terminal, still to be run by the host broker."""
        if decision not in DECISIONS or decision == "reject":
            raise HostOpApprovalError(REASON_INVALID_DECISION, decision)
        return cls(
            request_id=request.request_id,
            op=request.op,
            approval_class=request.approval_class,
            decision=decision,
            status=PENDING_BROKER_STATUS,
            reason="",
            executed=False,
            resolved_by=resolved_by,
            args=dict(request.args),
            request_seq=request.request_seq,
            session_id=request.session_id,
            task_id=request.task_id,
        )


def rejected_outcome(
    request: HostOpApprovalRequest, *, choice: str, resolved_by: str
) -> HostOpApprovalOutcome:
    """The recorded rejection of one request: no host action, no broker call."""
    return HostOpApprovalOutcome(
        request_id=request.request_id,
        op=request.op,
        approval_class=request.approval_class,
        decision=choice,
        status="denied",
        reason=REASON_REJECTED_BY_USER,
        executed=False,
        resolved_by=str(resolved_by),
        args=dict(request.args),
        request_seq=request.request_seq,
        session_id=request.session_id,
        task_id=request.task_id,
    )
