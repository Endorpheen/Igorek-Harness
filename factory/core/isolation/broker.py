"""Typed, versioned host-operation broker over the isolation channel.

One request frame of the existing ``host_op`` kind gets exactly one response
frame of the same kind::

    request : {"version": 1, "op": "runtime.status", "args": {}}
    response: {"version": 1, "op": ..., "status": "ok"|"denied"|"approval_required"|"failed",
               "reason": <stable code>, "detail": <sanitized>, "result": {...},
               "request_seq": <int>}

Every layer is host-owned: the op catalog with its typed argument schemas
(:mod:`factory.core.isolation.broker_ops`), the allowlist/policy
(:mod:`factory.core.isolation.broker_seam`), and the capability grant, policy
authority and audit trail (:mod:`factory.core.isolation.broker_host`). There is
no ``exec``/``shell`` op and no free-form string argument kind, so the wire
cannot express a raw command, argv or host path; a policy whose source is not
host configuration cannot even construct a broker.

Every request is audited: the decision is written to the trail *before* any
action runs, and an unavailable trail blocks execution. Stable reasons:
``unknown_operation``, ``invalid_arguments``, ``operation_not_allowed``,
``approval_required``, ``unsupported_version``, ``malformed_request``,
``guest_policy_rejected``, ``audit_unavailable``, ``host_action_failed``,
``invalid_action_result``, ``privileged_user_denied``.

A typed action may refuse or fail with
:class:`~factory.core.isolation.broker_ops.HostActionError`; the broker then
reports that error's stable ``code`` (and its ``executed`` audit bit) instead of
the generic ``host_action_failed``. A plain exception still maps to it.
"""

# allow: SIZE_OK - one cohesive trust boundary (envelope -> schema -> policy ->
# audit -> execute -> response); splitting its stages would let them drift.

from __future__ import annotations

import logging
import os
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from functools import cache
from typing import Final

from ..trace_taxonomy import CcTraceEvent
from .broker_host import (
    REASON_GUEST_POLICY_REJECTED, REASON_INCOMPLETE_GRANT, HostOpBrokerAuthority,
    HostOpCapabilityGrant, HostOpTrail, JsonlHostOpAudit,
)
from .broker_ops import (
    BROKER_VERSION,
    ArgValue,
    HostActionError,
    HostOpArgumentError,
    HostOpResultError,
    narrow_json_result,
    safe_operation_label,
    validate_host_op_args,
)
from .broker_seam import HostOpDecision, evaluate_host_op_request, host_op_audit_record
from .channel import (
    ChannelConfig,
    ChannelMessage,
    JsonValue,
    MessageKind,
    PeerSession,
    payload_policy_violation,
)
from .errors import IsolationError

__all__ = [
    "BROKER_VERSION",
    "REASON_AUDIT_UNAVAILABLE",
    "REASON_GUEST_POLICY_REJECTED",
    "REASON_HOST_ACTION_FAILED",
    "REASON_INCOMPLETE_GRANT",
    "REASON_INVALID_ACTION_RESULT",
    "REASON_INVALID_ARGUMENTS",
    "REASON_MALFORMED_REQUEST",
    "REASON_OPERATION_NOT_ALLOWED",
    "REASON_PRIVILEGED_USER",
    "REASON_UNEXPECTED_FRAME",
    "REASON_UNKNOWN_OPERATION",
    "REASON_UNSUPPORTED_VERSION",
    "HostOpBroker",
    "HostOpBrokerAuthority",
    "HostOpCapabilityGrant",
    "HostOpResponse",
    "HostOpStatus",
    "HostOpTrail",
    "JsonlHostOpAudit",
]

logger = logging.getLogger(__name__)

REASON_UNKNOWN_OPERATION: Final[str] = "unknown_operation"
REASON_INVALID_ARGUMENTS: Final[str] = "invalid_arguments"
REASON_OPERATION_NOT_ALLOWED: Final[str] = "operation_not_allowed"
REASON_UNSUPPORTED_VERSION: Final[str] = "unsupported_version"
REASON_MALFORMED_REQUEST: Final[str] = "malformed_request"
REASON_AUDIT_UNAVAILABLE: Final[str] = "audit_unavailable"
REASON_HOST_ACTION_FAILED: Final[str] = "host_action_failed"
REASON_INVALID_ACTION_RESULT: Final[str] = "invalid_action_result"
REASON_PRIVILEGED_USER: Final[str] = "privileged_user_denied"
REASON_UNEXPECTED_FRAME: Final[str] = "unexpected_frame_kind"

_REQUEST_KEYS: Final[frozenset[str]] = frozenset({"version", "op", "args"})


class HostOpStatus(StrEnum):
    """Terminal state of one host-op request."""

    OK = "ok"
    DENIED = "denied"
    APPROVAL_REQUIRED = "approval_required"
    FAILED = "failed"


@dataclass(frozen=True, slots=True)
class HostOpResponse:
    """Exactly one typed answer to exactly one request."""

    version: int
    op: str
    status: HostOpStatus
    reason: str
    detail: str
    result: Mapping[str, JsonValue]
    request_seq: int

    def to_payload(self) -> dict[str, JsonValue]:
        return {
            "version": self.version,
            "op": self.op,
            "status": str(self.status),
            "reason": self.reason,
            "detail": self.detail,
            "result": dict(self.result),
            "request_seq": self.request_seq,
        }


class HostOpBroker:
    """Validate, decide, audit, execute: one typed response per ``host_op`` request."""

    __slots__ = ("_authority", "_grant")

    def __init__(
        self,
        grant: HostOpCapabilityGrant,
        authority: HostOpBrokerAuthority,
        *,
        host_uid: int | None = None,
    ) -> None:
        effective_uid = os.geteuid() if host_uid is None else host_uid
        if effective_uid == 0:
            raise IsolationError(
                REASON_PRIVILEGED_USER, "the host-op broker must run as an unprivileged host user"
            )
        self._grant = grant
        self._authority = authority

    def handle(self, message: ChannelMessage, *, approval_granted: bool = False) -> HostOpResponse:
        """Answer exactly one ``host_op`` message; other kinds are a caller bug."""
        if message.kind is not MessageKind.HOST_OP:
            raise IsolationError(REASON_UNEXPECTED_FRAME, str(message.kind))
        return self._handle_payload(
            message.payload, request_seq=message.seq, approval_granted=approval_granted
        )

    def respond(
        self, session: PeerSession, message: ChannelMessage, *, approval_granted: bool = False
    ) -> HostOpResponse:
        """Handle one message and send the single response frame back over the channel."""
        response = self.handle(message, approval_granted=approval_granted)
        session.send(MessageKind.HOST_OP, response.to_payload())
        return response

    def _handle_payload(
        self, payload: Mapping[str, JsonValue], *, request_seq: int, approval_granted: bool
    ) -> HostOpResponse:
        label = safe_operation_label(payload.get("op"))
        if any(key not in _REQUEST_KEYS for key in payload):
            return self._deny(label, REASON_MALFORMED_REQUEST, request_seq=request_seq)
        version = payload.get("version")
        if not isinstance(version, int) or isinstance(version, bool) or version != BROKER_VERSION:
            return self._deny(label, REASON_UNSUPPORTED_VERSION, request_seq=request_seq)
        spec = self._grant.registry.spec(label) if isinstance(payload.get("op"), str) else None
        if spec is None:
            return self._deny(label, REASON_UNKNOWN_OPERATION, request_seq=request_seq)
        raw_args = payload.get("args", {})
        if not isinstance(raw_args, dict):
            return self._deny(
                spec.op, REASON_INVALID_ARGUMENTS, request_seq=request_seq, detail="args_not_object"
            )
        try:
            args = validate_host_op_args(spec, raw_args)
        except HostOpArgumentError as exc:
            return self._deny(
                spec.op, REASON_INVALID_ARGUMENTS, request_seq=request_seq, detail=exc.detail
            )
        decision = evaluate_host_op_request(
            spec.op, policy=self._authority.policy, approval_granted=approval_granted
        )
        if not decision.allowed:
            status = (
                HostOpStatus.APPROVAL_REQUIRED
                if decision.reason == "approval_required"
                else HostOpStatus.DENIED
            )
            self._audit_decision(decision, request_seq=request_seq, status=status)
            return HostOpResponse(BROKER_VERSION, spec.op, status, decision.reason, "", {}, request_seq)
        if not self._audit_decision(decision, request_seq=request_seq, status=HostOpStatus.OK):
            return HostOpResponse(
                BROKER_VERSION, spec.op, HostOpStatus.FAILED, REASON_AUDIT_UNAVAILABLE, "", {}, request_seq
            )
        return self._execute(spec.op, args, request_seq=request_seq)

    def _execute(self, op: str, args: Mapping[str, ArgValue], *, request_seq: int) -> HostOpResponse:
        action = self._grant.actions[op]
        try:
            raw_result = action(args)
        except HostActionError as exc:
            # Typed action failure: a stable reason crosses the wire, the detail stays host-side.
            logger.info("host op %s failed with %s", op, exc.code)
            self._audit_outcome(op, request_seq, status=HostOpStatus.FAILED, reason=exc.code, executed=exc.executed)
            return HostOpResponse(BROKER_VERSION, op, HostOpStatus.FAILED, exc.code, "", {}, request_seq)
        except Exception as exc:  # host-action boundary, reported as `failed`
            logger.warning("host op %s failed with %s", op, type(exc).__name__)
            self._audit_outcome(op, request_seq, status=HostOpStatus.FAILED, reason=REASON_HOST_ACTION_FAILED)
            return HostOpResponse(
                BROKER_VERSION, op, HostOpStatus.FAILED, REASON_HOST_ACTION_FAILED, type(exc).__name__, {}, request_seq
            )
        try:
            result = narrow_json_result(raw_result)
        except HostOpResultError as exc:
            logger.warning("host op %s returned a value that cannot cross the channel: %s", op, exc)
            self._audit_outcome(op, request_seq, status=HostOpStatus.FAILED, reason=REASON_INVALID_ACTION_RESULT)
            return HostOpResponse(
                BROKER_VERSION, op, HostOpStatus.FAILED, REASON_INVALID_ACTION_RESULT, str(exc), {}, request_seq
            )
        response = HostOpResponse(BROKER_VERSION, op, HostOpStatus.OK, "approved", "", result, request_seq)
        violation = payload_policy_violation(MessageKind.HOST_OP, response.to_payload(), _wire_policy())
        if violation is not None:
            logger.warning("host op %s result rejected by the channel payload policy: %s", op, violation)
            self._audit_outcome(op, request_seq, status=HostOpStatus.FAILED, reason=REASON_INVALID_ACTION_RESULT)
            return HostOpResponse(
                BROKER_VERSION, op, HostOpStatus.FAILED, REASON_INVALID_ACTION_RESULT, violation, {}, request_seq
            )
        self._audit_outcome(op, request_seq, status=HostOpStatus.OK, reason="approved")
        return response

    def _deny(self, op: str, reason: str, *, request_seq: int, detail: str = "") -> HostOpResponse:
        decision = HostOpDecision(op=op, allowed=False, requires_approval=False, audited=True, reason=reason)
        self._audit_decision(decision, request_seq=request_seq, status=HostOpStatus.DENIED)
        return HostOpResponse(BROKER_VERSION, op, HostOpStatus.DENIED, reason, detail, {}, request_seq)

    def _audit_decision(self, decision: HostOpDecision, *, request_seq: int, status: HostOpStatus) -> bool:
        entry: dict[str, object] = {
            **host_op_audit_record(decision),
            "phase": "decision",
            "session": self._authority.session_id,
            "request_seq": request_seq,
            "version": BROKER_VERSION,
            "status": str(status),
        }
        return self._record(entry)

    def _audit_outcome(
        self, op: str, request_seq: int, *, status: HostOpStatus, reason: str, executed: bool = True
    ) -> None:
        entry: dict[str, object] = {
            "kind": CcTraceEvent.ISOLATION_HOST_OP,
            "phase": "outcome",
            "session": self._authority.session_id,
            "request_seq": request_seq,
            "version": BROKER_VERSION,
            "op": op,
            "allowed": status is HostOpStatus.OK,
            "executed": executed,
            "status": str(status),
            "reason": reason,
        }
        self._record(entry)

    def _record(self, entry: dict[str, object]) -> bool:
        try:
            self._authority.trail.record(entry)
        except Exception:  # audit boundary; a missing audit blocks execution
            logger.warning("host-op audit trail unavailable for %s", entry.get("op"))
            return False
        return True


@cache
def _wire_policy() -> ChannelConfig:
    return ChannelConfig()
