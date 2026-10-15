"""Bridge from a host-op approval decision to the typed broker (plan todo 22).

The gate is the only component that may turn an approval decision into a host
action. It holds the pending host-op requests the host parked (the same records
listed in the mobile approval center as ``kind == "host_op"``) and resolves one
decision per request:

* **allow** - ``HostOpBroker.handle(..., approval_granted=True)`` executes the
  typed op; the broker's own decision/outcome audit entries are the record;
* **reject** - a rejection entry is written to the same audit trail and no host
  action is touched;
* **consume once** - the in-memory pending entry and the atomic claim on the
  host-owned park index are both removed before anything runs; when an index is
  configured it is the cross-process authority, so a request already claimed by
  another gate instance (or by :meth:`HostOpApprovalGate.resolve_stored` in a
  resumed runtime) is refused as already resolved instead of running again, and
  a duplicated decision can never execute the op twice;
* **audit first** - the resolution entry is written *before* execution, and an
  unavailable trail fails the decision closed instead of running it unaudited.
"""

# allow: SIZE_OK - one cohesive approval boundary (park -> claim -> audit ->
# execute); splitting the claim from the execution would let them drift.

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Mapping

from .broker import (
    HostOpBroker,
    HostOpResponse,
    HostOpStatus,
)
from .broker_host import HostOpTrail
from .broker_ops import safe_operation_label
from .channel import ChannelMessage, MessageKind
from .host_op_approval import (
    REASON_ALREADY_RESOLVED,
    REASON_INVALID_DECISION,
    REASON_INVALID_REQUEST,
    HostOpApprovalError,
    HostOpApprovalRequest,
    classify_host_op,
    host_op_request_id,
    narrow_host_op_args,
    parse_host_op_blocked_record,
)
from .host_op_audit import audit_resolution
from .host_op_park import REASON_PARK_UNAVAILABLE, HostOpParkStore
from .host_op_resolution import (
    DECISIONS,
    PENDING_BROKER_STATUS,
    HostOpApprovalOutcome,
    rejected_outcome,
)

logger = logging.getLogger(__name__)

__all__ = [
    "DECISIONS",
    "PENDING_BROKER_STATUS",
    "HostOpApprovalGate",
    "HostOpApprovalOutcome",
]


class HostOpApprovalGate:
    """The single bridge from a host-op decision to the typed broker.

    The gate never interprets a decision: it either passes ``approval_granted``
    to the broker (allow) or records the refusal on the audit trail (reject).
    """

    __slots__ = ("_broker", "_lock", "_park_store", "_pending", "_session_grants", "_trail")

    def __init__(
        self,
        broker: HostOpBroker,
        trail: HostOpTrail,
        *,
        park_store: HostOpParkStore | None = None,
    ) -> None:
        self._broker = broker
        self._trail = trail
        self._park_store = park_store
        self._pending: dict[str, HostOpApprovalRequest] = {}
        self._session_grants: set[tuple[str, str]] = set()
        self._lock = threading.Lock()

    @property
    def broker(self) -> HostOpBroker:
        return self._broker

    @property
    def pending_ids(self) -> tuple[str, ...]:
        with self._lock:
            return tuple(self._pending)

    def pending(self, request_id: str) -> HostOpApprovalRequest | None:
        with self._lock:
            return self._pending.get(str(request_id or "").strip())

    def session_granted(self, op: str, *, session_id: str = "") -> bool:
        with self._lock:
            return bool(session_id) and (str(session_id), str(op or "")) in self._session_grants

    def grant_session(self, op: str, *, session_id: str = "") -> str:
        """Record one op as session-approved (the persisted grant is replayed here)."""
        name = str(op or "")
        if not session_id or not name or safe_operation_label(name) != name:
            raise HostOpApprovalError(REASON_INVALID_REQUEST, f"invalid_op:{op!r}")
        with self._lock:
            self._session_grants.add((str(session_id), name))
        return name

    def park(
        self,
        message: ChannelMessage,
        response: HostOpResponse,
        *,
        task_id: str,
        session_id: str,
        reason: str = "",
    ) -> HostOpApprovalRequest:
        """Register one broker ``approval_required`` answer; a replay is idempotent."""
        if message.kind is not MessageKind.HOST_OP:
            raise HostOpApprovalError(REASON_INVALID_REQUEST, f"frame_kind:{message.kind}")
        if response.status is not HostOpStatus.APPROVAL_REQUIRED:
            raise HostOpApprovalError(
                REASON_INVALID_REQUEST, f"response_status:{response.status}"
            )
        op = str(response.op)
        if message.payload.get("op") != op:
            raise HostOpApprovalError(REASON_INVALID_REQUEST, "op_mismatch")
        request = HostOpApprovalRequest(
            request_id=host_op_request_id(session_id, message.seq),
            op=op,
            args=narrow_host_op_args(message.payload.get("args", {})),
            approval_class=classify_host_op(op),
            session_id=str(session_id),
            task_id=str(task_id),
            request_seq=int(message.seq),
            reason=str(reason or ""),
            created_at=int(time.time()),
        )
        with self._lock:
            existing = self._pending.get(request.request_id)
            if existing is not None:
                return existing
            self._pending[request.request_id] = request
            self._index_parked(request)
        return request

    def resolve(
        self, request_id: str, *, decision: str, resolved_by: str
    ) -> HostOpApprovalOutcome:
        """Consume one parked request; the broker runs the op only for an allow."""
        choice = str(decision or "").strip().lower()
        if choice not in DECISIONS:
            raise HostOpApprovalError(REASON_INVALID_DECISION, choice)
        key = str(request_id or "").strip()
        with self._lock:
            request = self._pending.get(key)
            if request is None:
                raise HostOpApprovalError(REASON_ALREADY_RESOLVED, key)
            if choice == "allow_session" and not request.session_id:
                raise HostOpApprovalError(REASON_INVALID_REQUEST, "session_id_required")
            approved = choice != "reject"
            self._consume_claim(request)
            self._audit_resolution(request, approved=approved, resolved_by=resolved_by)
            if choice == "allow_session":
                self._session_grants.add((request.session_id, request.op))
        if not approved:
            return rejected_outcome(request, choice=choice, resolved_by=resolved_by)
        return self._execute_request(request, choice=choice, resolved_by=resolved_by)

    def resolve_stored(
        self,
        record: Mapping[str, object],
        *,
        resolved_by: str,
        session_id: str = "",
        task_id: str = "",
    ) -> HostOpApprovalOutcome:
        """Execute one persisted outcome handoff (``pending_broker``) exactly once.

        This is the cross-process form of :meth:`resolve`: the decision may have
        been made by a runtime that never parked the request (TUI -> worker), so
        the atomic claim on the host-owned park index is the once-only authority
        whenever one is configured; only without an index does the in-memory
        pending entry decide. Anything else is refused before any host action.
        """
        stored = HostOpApprovalOutcome.from_record(record)
        if stored.status != PENDING_BROKER_STATUS or stored.executed:
            raise HostOpApprovalError(
                REASON_INVALID_REQUEST, f"not_pending:{stored.status}"
            )
        if stored.decision not in DECISIONS or stored.decision == "reject":
            raise HostOpApprovalError(REASON_INVALID_DECISION, stored.decision)
        return self._claim_and_execute(
            self._rebuild(stored, session_id=session_id, task_id=task_id),
            choice=stored.decision,
            resolved_by=resolved_by,
        )

    def resolve_request(
        self,
        record: Mapping[str, object],
        *,
        decision: str,
        resolved_by: str,
        session_id: str = "",
        task_id: str = "",
    ) -> HostOpApprovalOutcome:
        """Execute one parked request record (the mobile blocked record) once."""
        choice = str(decision or "").strip().lower()
        if choice not in DECISIONS:
            raise HostOpApprovalError(REASON_INVALID_DECISION, choice)
        stored = parse_host_op_blocked_record(record)
        request = self._rebuild(stored, session_id=session_id, task_id=task_id)
        if choice == "reject":
            with self._lock:
                self._consume_claim(request)
                self._audit_resolution(request, approved=False, resolved_by=resolved_by)
            return rejected_outcome(request, choice=choice, resolved_by=resolved_by)
        return self._claim_and_execute(request, choice=choice, resolved_by=resolved_by)

    @staticmethod
    def _rebuild(
        source: HostOpApprovalRequest | HostOpApprovalOutcome,
        *,
        session_id: str,
        task_id: str,
    ) -> HostOpApprovalRequest:
        return HostOpApprovalRequest(
            request_id=source.request_id,
            op=source.op,
            args=dict(source.args),
            approval_class=source.approval_class,
            session_id=str(session_id or source.session_id),
            task_id=str(task_id or source.task_id),
            request_seq=source.request_seq,
            reason=source.reason,
        )

    def _consume_claim(self, request: HostOpApprovalRequest) -> None:
        """Consume the once-only claim; a lost cross-process claim refuses the request.

        When a host-owned park index is configured it is the authority, not this
        instance's memory: a request still in the pending table but already
        claimed elsewhere must never execute here a second time. Without an
        index the pending table itself is the only once-only authority.
        """
        parked_here = self._pending.pop(request.request_id, None) is not None
        if parked_here and self._park_store is None:
            return
        if not self._claim_parked(request.request_id):
            raise HostOpApprovalError(REASON_ALREADY_RESOLVED, request.request_id)

    def _claim_and_execute(
        self, request: HostOpApprovalRequest, *, choice: str, resolved_by: str
    ) -> HostOpApprovalOutcome:
        with self._lock:
            if choice == "allow_session" and not request.session_id:
                raise HostOpApprovalError(REASON_INVALID_REQUEST, "session_id_required")
            self._consume_claim(request)
            self._audit_resolution(request, approved=True, resolved_by=resolved_by)
            if choice == "allow_session":
                self._session_grants.add((request.session_id, request.op))
        return self._execute_request(request, choice=choice, resolved_by=resolved_by)

    def _execute_request(
        self, request: HostOpApprovalRequest, *, choice: str, resolved_by: str
    ) -> HostOpApprovalOutcome:
        response = self._broker.handle(request.channel_message(), approval_granted=True)
        return HostOpApprovalOutcome(
            request_id=request.request_id,
            op=request.op,
            approval_class=request.approval_class,
            decision=choice,
            status=str(response.status),
            reason=response.reason,
            executed=response.status is HostOpStatus.OK,
            resolved_by=str(resolved_by),
            result=dict(response.result),
            args=dict(request.args),
            request_seq=request.request_seq,
            session_id=request.session_id,
            task_id=request.task_id,
        )


    def _index_parked(self, request: HostOpApprovalRequest) -> None:
        if self._park_store is None:
            return
        try:
            self._park_store.put(request.to_blocked_record())
        except OSError as exc:
            logger.warning("host-op park index unavailable; request kept in memory only: %s", exc)

    def _claim_parked(self, request_id: str) -> bool:
        if self._park_store is None:
            return False
        try:
            return self._park_store.claim(request_id) is not None
        except OSError as exc:
            logger.warning("host-op park index unavailable while claiming: %s", exc)
            raise HostOpApprovalError(REASON_PARK_UNAVAILABLE, request_id) from exc

    def _audit_resolution(
        self, request: HostOpApprovalRequest, *, approved: bool, resolved_by: str
    ) -> None:
        audit_resolution(
            self._trail, request, approved=approved, resolved_by=str(resolved_by)
        )
