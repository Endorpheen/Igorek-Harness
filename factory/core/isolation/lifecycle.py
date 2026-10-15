"""VM lifecycle states and the idle-teardown policy (plan todo 26).

This module owns *when* a session VM is torn down; the persistence classes it
must not destroy live in :mod:`factory.core.isolation.persistence`.

Lifecycle: ``cold -> starting -> warm -> idle -> stopped -> reaped``.

* ``cold`` - no VM (unknown session ids resolve here);
* ``starting`` - the manager is defining/starting the domain;
* ``warm`` - the domain runs; ``in_flight`` counts dispatches waiting for the guest;
* ``idle`` - the domain runs with no dispatch in flight: the idle TTL applies here;
* ``stopped`` - the domain was destroyed and its overlay removed (idle TTL or session stop);
* ``reaped`` - every host handle is released (routing entry, overlay lease,
  workspace export, admission slot); a restart begins a new generation.

An *idle-frozen* variant (pause the domain, keep the RAM) was rejected: a frozen
VM still holds the guest memory and the overlay, so it is not torn down.

Two structural rules keep a teardown from cutting work in half:

* a VM with an in-flight dispatch is never an idle candidate, so a checkpoint
  write (which only happens inside a dispatch) cannot be interrupted by the
  sweeper - the teardown is deferred until the turn ends;
* the session-stop trigger (mobile Stop) is explicit and unconditional, exactly
  as the todo-11 cancel path already behaves.

The tracker is pure Python: the clock is injected, no host side effect happens
here, and the manager remains the only owner of VM/domain state.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from enum import StrEnum

__all__ = [
    "LifecycleState",
    "LifecycleTracker",
    "SessionLifecycle",
    "TeardownDecision",
    "TeardownTrigger",
    "teardown_decision",
]


class LifecycleState(StrEnum):
    """One VM generation's state; unknown sessions are :attr:`COLD`."""

    COLD = "cold"
    STARTING = "starting"
    WARM = "warm"
    IDLE = "idle"
    STOPPED = "stopped"
    REAPED = "reaped"


class TeardownTrigger(StrEnum):
    """Why a VM left the running states (recorded on the session record)."""

    IDLE_TTL = "idle_ttl"
    SESSION_STOP = "session_stop"
    VM_LOST = "vm_lost"
    LIMIT_BREACH = "limit_breach"
    START_FAILED = "start_failed"
    HOST_SHUTDOWN = "host_shutdown"


class TeardownDecision(StrEnum):
    KEEP = "keep"
    STOP_AND_REAP = "stop_and_reap"


@dataclass(frozen=True, slots=True)
class SessionLifecycle:
    """One session's lifecycle record (immutable; the tracker replaces it)."""

    session_id: str
    state: LifecycleState
    created_at: float
    last_active_at: float
    generation: int = 1
    dispatches: int = 0
    in_flight: int = 0
    last_trigger: str = ""

    def idle_age_s(self, now: float) -> float:
        """Seconds since this record last became active (never negative)."""
        return max(0.0, float(now) - self.last_active_at)

    @property
    def running(self) -> bool:
        """True while the session is expected to own a live VM."""
        return self.state in (
            LifecycleState.STARTING,
            LifecycleState.WARM,
            LifecycleState.IDLE,
        )


def teardown_decision(
    record: SessionLifecycle, *, now: float, ttl_s: float
) -> TeardownDecision:
    """The idle-TTL verdict for one record; the session-stop trigger bypasses it.

    Only a running, quiescent VM whose idle age reached the TTL is selected. A
    VM with an in-flight dispatch is always kept - that is what serializes a
    teardown against an in-progress checkpoint write.
    """
    if record.state not in (LifecycleState.WARM, LifecycleState.IDLE):
        return TeardownDecision.KEEP
    if record.in_flight > 0:
        return TeardownDecision.KEEP
    if record.idle_age_s(now) >= max(0.0, float(ttl_s)):
        return TeardownDecision.STOP_AND_REAP
    return TeardownDecision.KEEP


class LifecycleTracker:
    """Per-session lifecycle records over an injected clock.

    The tracker is lock-protected but side-effect free: it never touches a
    domain, an overlay or a lease, so a policy test can drive it directly and
    the manager stays the only owner of host state.
    """

    def __init__(
        self, *, ttl_s: float, clock: Callable[[], float] = time.monotonic
    ) -> None:
        self._ttl_s = max(0.0, float(ttl_s))
        self._clock = clock
        self._lock = threading.Lock()
        self._records: dict[str, SessionLifecycle] = {}

    @property
    def ttl_s(self) -> float:
        return self._ttl_s

    def now(self) -> float:
        return float(self._clock())

    def state_of(self, session_id: str) -> LifecycleState:
        """The current state; an unknown session is :attr:`LifecycleState.COLD`."""
        record = self.record_of(session_id)
        return LifecycleState.COLD if record is None else record.state

    def record_of(self, session_id: str) -> SessionLifecycle | None:
        """The session's record, or ``None`` when the tracker never saw it."""
        text = _session_text(session_id)
        with self._lock:
            return self._records.get(text)

    def snapshot(self) -> tuple[SessionLifecycle, ...]:
        """Every record this process holds, ordered by session id."""
        with self._lock:
            return tuple(self._records[key] for key in sorted(self._records))

    def begin_start(self, session_id: str) -> SessionLifecycle:
        """Enter ``starting``; a terminal record begins a new generation."""
        text = _session_text(session_id)
        with self._lock:
            moment = self.now()
            previous = self._records.get(text)
            if previous is None:
                record = SessionLifecycle(
                    session_id=text,
                    state=LifecycleState.STARTING,
                    created_at=moment,
                    last_active_at=moment,
                )
            elif previous.running:
                record = previous
            else:
                record = replace(
                    previous,
                    state=LifecycleState.STARTING,
                    created_at=moment,
                    last_active_at=moment,
                    generation=previous.generation + 1,
                    in_flight=0,
                )
            self._records[text] = record
            return record

    def mark_warm(self, session_id: str) -> SessionLifecycle:
        """The VM is running and ready for a dispatch."""
        return self._update(session_id, state=LifecycleState.WARM, in_flight=None)

    def begin_dispatch(self, session_id: str) -> SessionLifecycle:
        """One dispatch entered the wait for the guest (``in_flight`` + 1)."""
        text = _session_text(session_id)
        with self._lock:
            moment = self.now()
            previous = self._records.get(text)
            if previous is None:
                previous = SessionLifecycle(
                    session_id=text,
                    state=LifecycleState.WARM,
                    created_at=moment,
                    last_active_at=moment,
                )
            record = replace(
                previous,
                state=LifecycleState.WARM,
                last_active_at=moment,
                dispatches=previous.dispatches + 1,
                in_flight=previous.in_flight + 1,
            )
            self._records[text] = record
            return record

    def finish_dispatch(self, session_id: str) -> SessionLifecycle | None:
        """One dispatch ended; a still-warm VM becomes idle, a stopped one stays.

        The ``in_flight`` counter is always decremented (a contained dispatch
        must not leave the counter stuck); only a ``warm`` record moves to
        ``idle``.
        """
        text = _session_text(session_id)
        with self._lock:
            previous = self._records.get(text)
            if previous is None:
                return None
            in_flight = max(0, previous.in_flight - 1)
            state = previous.state
            if state is LifecycleState.WARM and in_flight == 0:
                state = LifecycleState.IDLE
            record = replace(
                previous,
                state=state,
                last_active_at=self.now(),
                in_flight=in_flight,
            )
            self._records[text] = record
            return record

    def mark_stopped(
        self, session_id: str, trigger: TeardownTrigger
    ) -> SessionLifecycle | None:
        """The VM was destroyed; unknown sessions are left unknown."""
        return self._update(
            session_id,
            state=LifecycleState.STOPPED,
            in_flight=0,
            last_trigger=str(trigger),
        )

    def mark_reaped(self, session_id: str) -> SessionLifecycle | None:
        """Every host handle was released; unknown sessions are left unknown."""
        return self._update(session_id, state=LifecycleState.REAPED)

    def idle_expired(self, now: float | None = None) -> tuple[str, ...]:
        """Session ids the idle policy would stop and reap, ordered by id."""
        moment = self.now() if now is None else float(now)
        with self._lock:
            records = [self._records[key] for key in sorted(self._records)]
        return tuple(
            record.session_id
            for record in records
            if teardown_decision(record, now=moment, ttl_s=self._ttl_s)
            is TeardownDecision.STOP_AND_REAP
        )

    def _update(
        self,
        session_id: str,
        *,
        state: LifecycleState,
        in_flight: int | None = None,
        last_trigger: str | None = None,
    ) -> SessionLifecycle | None:
        text = _session_text(session_id)
        with self._lock:
            previous = self._records.get(text)
            if previous is None:
                return None
            record = replace(
                previous,
                state=state,
                last_active_at=self.now(),
                in_flight=previous.in_flight if in_flight is None else in_flight,
                last_trigger=(
                    previous.last_trigger if last_trigger is None else last_trigger
                ),
            )
            self._records[text] = record
            return record


def _session_text(session_id: str) -> str:
    text = str(session_id).strip()
    if not text:
        raise ValueError("session_id must be a non-empty string")
    return text
