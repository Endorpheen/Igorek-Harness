"""The host-operation approval class: a distinct decision kind for host ops.

A host op is not a tool call. The blocked-tool payload (``tool``/``input``/
``signature``/``session_rule``) exists so the mobile/TUI surfaces can match an
approval against ``approved_tool_calls`` and re-dispatch the guest with a
``forced_tool_call``. A typed broker operation (plan todo 19/20) has neither a
signature nor a forced call: it is executed host-side, exactly once, from the
approval decision itself (:mod:`factory.core.isolation.host_op_gate`). Overloading
the tool payload would silently corrupt both contracts, so host ops travel as
their own record kind:

* the blocked record carries ``kind == "host_op"`` plus the typed ``op``, its
  validated ``args`` and an :class:`HostOpApprovalClass`; the existing tool
  records stay byte-identical (no ``kind`` key at all);
* classification is host-owned and total: ``workspace.*`` ops are workspace
  actions, ``network.*`` ops are network actions, the ops that control the host
  runtime itself are dangerous ops, and everything else is a plain host op;
* parsing is strict: a malformed or unknown-class record is refused, never
  half-accepted and never mislabelled as a tool approval.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from types import MappingProxyType
from typing import Final

from .broker import BROKER_VERSION
from .broker_ops import ArgValue, safe_operation_label
from .channel import ChannelMessage, JsonValue, MessageKind
from .errors import IsolationError

__all__ = [
    "DANGEROUS_HOST_OPS",
    "HOST_OP_APPROVAL_KIND",
    "NETWORK_OP_PREFIX",
    "REASON_ALREADY_RESOLVED",
    "REASON_INVALID_DECISION",
    "REASON_INVALID_REQUEST",
    "REASON_REJECTED_BY_USER",
    "WORKSPACE_OP_PREFIX",
    "HostOpApprovalClass",
    "HostOpApprovalError",
    "HostOpApprovalRequest",
    "classify_host_op",
    "host_op_request_id",
    "is_host_op_blocked_record",
    "narrow_host_op_args",
    "parse_host_op_blocked_record",
]

#: The distinct mobile/TUI payload kind; tool approvals carry ``"tool"`` and
#: user-input requests carry ``"user_input"``.
HOST_OP_APPROVAL_KIND: Final[str] = "host_op"

REASON_INVALID_REQUEST: Final[str] = "invalid_host_op_request"
REASON_ALREADY_RESOLVED: Final[str] = "host_op_already_resolved"
REASON_INVALID_DECISION: Final[str] = "invalid_host_op_decision"
REASON_REJECTED_BY_USER: Final[str] = "rejected_by_user"

#: Op namespaces that classify the effect domain of a typed host operation.
WORKSPACE_OP_PREFIX: Final[str] = "workspace."
NETWORK_OP_PREFIX: Final[str] = "network."
#: Ops that control the host runtime itself; a host may extend this set, but the
#: shipped catalog is fixed here so classification never depends on guest input.
DANGEROUS_HOST_OPS: Final[frozenset[str]] = frozenset(
    {"runtime.restart", "runtime.update", "vault.fetch"}
)


class HostOpApprovalClass(StrEnum):
    """The plan's four distinguishable classes of host-side operations."""

    WORKSPACE_ACTION = "workspace_action"
    NETWORK_ACTION = "network_action"
    DANGEROUS_OP = "dangerous_op"
    HOST_OP = "host_op"


class HostOpApprovalError(IsolationError):
    """A host-op approval request or decision violates its structural contract."""


def classify_host_op(op: str) -> HostOpApprovalClass:
    """Classify one typed op label. Total by construction: never raises."""
    name = safe_operation_label(op)
    if name.startswith(WORKSPACE_OP_PREFIX):
        return HostOpApprovalClass.WORKSPACE_ACTION
    if name.startswith(NETWORK_OP_PREFIX):
        return HostOpApprovalClass.NETWORK_ACTION
    if name in DANGEROUS_HOST_OPS:
        return HostOpApprovalClass.DANGEROUS_OP
    return HostOpApprovalClass.HOST_OP


def host_op_request_id(session_id: str, request_seq: int) -> str:
    """Stable decision key of one host-op request (session + channel sequence)."""
    scope = str(session_id or "").strip() or "session"
    return f"host-op:{scope}:{int(request_seq)}"


def narrow_host_op_args(raw: object) -> dict[str, ArgValue]:
    """Narrow a broker-validated args object to the typed scalar catalog."""
    if not isinstance(raw, Mapping):
        raise HostOpApprovalError(REASON_INVALID_REQUEST, "args_not_a_mapping")
    narrowed: dict[str, ArgValue] = {}
    for key, value in raw.items():
        if not isinstance(key, str) or not key:
            raise HostOpApprovalError(REASON_INVALID_REQUEST, "argument_name")
        if isinstance(value, bool | str | int):
            narrowed[key] = value
            continue
        raise HostOpApprovalError(REASON_INVALID_REQUEST, f"argument_value:{key}")
    return narrowed


@dataclass(frozen=True, slots=True)
class HostOpApprovalRequest:
    """One parked host-op request: the typed op a person is asked to approve."""

    request_id: str
    op: str
    args: Mapping[str, ArgValue] = field(default_factory=dict)
    approval_class: HostOpApprovalClass = HostOpApprovalClass.HOST_OP
    session_id: str = ""
    task_id: str = ""
    request_seq: int = 0
    reason: str = ""
    created_at: int = 0

    def __post_init__(self) -> None:
        if not str(self.request_id).strip():
            raise HostOpApprovalError(REASON_INVALID_REQUEST, "empty_request_id")
        if safe_operation_label(self.op) != self.op:
            raise HostOpApprovalError(REASON_INVALID_REQUEST, f"invalid_op:{self.op}")
        if not isinstance(self.approval_class, HostOpApprovalClass):
            raise HostOpApprovalError(
                REASON_INVALID_REQUEST, f"invalid_class:{self.approval_class!r}"
            )
        object.__setattr__(self, "args", MappingProxyType(narrow_host_op_args(dict(self.args))))

    def to_blocked_record(self) -> dict[str, JsonValue]:
        """The record stored in the task's ``approval_blocked_tools`` list."""
        return {
            "kind": HOST_OP_APPROVAL_KIND,
            "request_id": self.request_id,
            "op": self.op,
            "args": dict(self.args),
            "approval_class": str(self.approval_class),
            "reason": self.reason,
            "request_seq": self.request_seq,
        }

    def channel_message(self) -> ChannelMessage:
        """The original ``host_op`` frame shape the broker answers."""
        return ChannelMessage(
            kind=MessageKind.HOST_OP,
            seq=self.request_seq,
            payload={"version": BROKER_VERSION, "op": self.op, "args": dict(self.args)},
        )


def is_host_op_blocked_record(record: object) -> bool:
    """True only for the explicit host-op record kind; tool records lack ``kind``."""
    return isinstance(record, Mapping) and record.get("kind") == HOST_OP_APPROVAL_KIND


def parse_host_op_blocked_record(record: Mapping[str, object]) -> HostOpApprovalRequest:
    """Parse one stored host-op record; anything malformed is refused."""
    if not is_host_op_blocked_record(record):
        raise HostOpApprovalError(REASON_INVALID_REQUEST, "not_a_host_op_record")
    request_id = str(record.get("request_id") or "").strip()
    if not request_id:
        raise HostOpApprovalError(REASON_INVALID_REQUEST, "empty_request_id")
    op = str(record.get("op") or "")
    try:
        approval_class = HostOpApprovalClass(str(record.get("approval_class") or ""))
    except ValueError as exc:
        raise HostOpApprovalError(
            REASON_INVALID_REQUEST, f"unknown_class:{record.get('approval_class')!r}"
        ) from exc
    seq_raw = record.get("request_seq")
    if isinstance(seq_raw, bool) or not isinstance(seq_raw, int):
        raise HostOpApprovalError(REASON_INVALID_REQUEST, "invalid_request_seq")
    return HostOpApprovalRequest(
        request_id=request_id,
        op=op,
        args=narrow_host_op_args(record.get("args") or {}),
        approval_class=approval_class,
        reason=str(record.get("reason") or ""),
        request_seq=seq_raw,
    )
