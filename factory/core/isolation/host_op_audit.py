"""Resolution audit for host-op decisions (plan todo 22).

One recorded decision per resolved request, written *before* the broker executes
anything: an unavailable trail raises instead of letting an op run unaudited.
Kept apart from the gate so the record shape and the decision machinery never
drift together by accident.
"""

from __future__ import annotations

import logging

from ..trace_taxonomy import CcTraceEvent
from .broker import REASON_AUDIT_UNAVAILABLE
from .broker_host import HostOpTrail
from .host_op_approval import (
    REASON_REJECTED_BY_USER,
    HostOpApprovalError,
    HostOpApprovalRequest,
)

__all__ = ["audit_resolution"]

logger = logging.getLogger(__name__)


def audit_resolution(
    trail: HostOpTrail,
    request: HostOpApprovalRequest,
    *,
    approved: bool,
    resolved_by: str,
) -> None:
    """Write the resolution entry; a trail failure is a typed fail-closed error."""
    entry: dict[str, object] = {
        "kind": CcTraceEvent.ISOLATION_HOST_OP,
        "phase": "resolution",
        "op": request.op,
        "approval_class": str(request.approval_class),
        "allowed": approved,
        "status": "approved" if approved else "denied",
        "reason": "approved" if approved else REASON_REJECTED_BY_USER,
        "session": request.session_id,
        "request_seq": request.request_seq,
        "resolved_by": str(resolved_by),
    }
    try:
        trail.record(entry)
    except Exception as exc:  # audit boundary; no trail, no decision
        logger.warning("host-op resolution audit unavailable for %s", request.op)
        raise HostOpApprovalError(REASON_AUDIT_UNAVAILABLE, request.request_id) from exc
