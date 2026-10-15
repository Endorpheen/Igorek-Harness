"""Host-wide admission control for per-session VMs (plan todo 33, Wave 4).

A host may run at most ``IGOREK_ISOLATION_MAX_VMS`` session VMs within an
aggregate RAM/disk budget. This module owns that invariant:

* :class:`CapacityLimits` is the resolved budget. By default it is derived from
  the config exactly (``max_vms`` VMs of guest memory + VMM overhead, and
  ``max_vms`` overlay caps), so it can never silently allow more than ``MAX_VMS``;
  ``IGOREK_ISOLATION_HOST_MEMORY_MB`` / ``IGOREK_ISOLATION_HOST_DISK_GB`` make it
  stricter for a host that cannot afford the derived totals.
* :class:`AdmissionController` is the claim machinery. One *slot* file per
  admitted session is held with a non-blocking ``flock`` under
  ``<overlay_dir>/admission/`` - the same cross-process lease pattern the
  overlay guard uses - and every scan/claim/release runs under one global
  admission flock, so two worker processes sharing one overlay directory share
  one budget and can never both claim the last slot. A crashed process's flock
  dies with it; the next scan reclaims the stale slot file and counts it as
  zero.
* :class:`CapacityWait` is the visible status. A session that cannot fit is
  *queued*, its wait callback receives a taxonomy-shaped record (taxonomy kind
  ``isolation.capacity_wait``, written into the authoritative host trace by
  :func:`emit_capacity_wait`) and it starts as soon as capacity frees. The wait
  is bounded by ``IGOREK_ISOLATION_ADMISSION_WAIT_S``; a timeout, or a stop
  while queued, raises a named :class:`IsolationError` - a queued session is
  never silently failed and never over-commits.

The manager releases the slot on every teardown path (stop, cancel, containment,
failed start), so the accounting follows the VM lifecycle, not the attempt.

# allow: SIZE_OK - one cohesive admission boundary: the budget model, the
# cross-process flock slot store and the visible wait-status contract are the
# same invariant seen from three sides; splitting them would separate halves of
# one critical section (scan -> blocked -> claim under a single global lock).
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import logging
import os
import threading
import time
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

from ..cc_backend_trace import append_cc_backend_trace
from ..trace_taxonomy import CcTraceEvent, trace_event
from .config import IsolationConfig
from .errors import IsolationError
from .limits import memory_hard_limit_mib

logger = logging.getLogger(__name__)

__all__ = [
    "ADMISSION_DIRNAME",
    "CODE_CONFLICT",
    "CODE_STATE_CORRUPT",
    "CODE_WAIT_CANCELED",
    "CODE_WAIT_TIMEOUT",
    "DEFAULT_POLL_S",
    "DEFAULT_WAIT_TIMEOUT_S",
    "GLOBAL_LOCK_NAME",
    "REASON_DISK",
    "REASON_MAX_VMS",
    "REASON_MEMORY",
    "SLOT_SUFFIX",
    "STATE_CANCELED",
    "STATE_TIMEOUT",
    "STATE_WAITING",
    "AdmissionController",
    "CapacityLimits",
    "CapacitySnapshot",
    "CapacityUsage",
    "CapacityWait",
    "emit_capacity_wait",
]

ADMISSION_DIRNAME: Final[str] = "admission"
GLOBAL_LOCK_NAME: Final[str] = ".admission.lock"
SLOT_SUFFIX: Final[str] = ".slot"
DEFAULT_WAIT_TIMEOUT_S: Final[float] = 600.0
DEFAULT_POLL_S: Final[float] = 0.25
_DIGEST_CHARS: Final[int] = 32
_SLOT_MAX_BYTES: Final[int] = 4096

REASON_MAX_VMS: Final[str] = "max_vms"
REASON_MEMORY: Final[str] = "memory"
REASON_DISK: Final[str] = "disk"

STATE_WAITING: Final[str] = "waiting"
STATE_TIMEOUT: Final[str] = "timeout"
STATE_CANCELED: Final[str] = "canceled"

CODE_WAIT_TIMEOUT: Final[str] = "capacity_wait_timeout"
CODE_WAIT_CANCELED: Final[str] = "capacity_wait_canceled"
CODE_CONFLICT: Final[str] = "admission_conflict"
CODE_STATE_CORRUPT: Final[str] = "admission_state_corrupt"

WaitCallback = Callable[["CapacityWait"], None]
AbortCheck = Callable[[], bool]


@dataclass(frozen=True, slots=True)
class CapacityLimits:
    """The resolved host-wide budget for concurrent session VMs."""

    max_vms: int
    memory_mib: int
    disk_gib: int

    @classmethod
    def from_config(cls, config: IsolationConfig) -> CapacityLimits:
        """Derive the budget from the resolved config (overrides win when stricter)."""
        per_vm_memory = memory_hard_limit_mib(config.session_memory_mb)
        memory_mib = (
            config.host_memory_mb
            if config.host_memory_mb > 0
            else config.max_vms * per_vm_memory
        )
        disk_gib = (
            config.host_disk_gb
            if config.host_disk_gb > 0
            else config.max_vms * config.session_disk_gb
        )
        return cls(max_vms=config.max_vms, memory_mib=memory_mib, disk_gib=disk_gib)

    def as_record(self) -> dict[str, int]:
        return {
            "max_vms": self.max_vms,
            "memory_mib": self.memory_mib,
            "disk_gib": self.disk_gib,
        }


@dataclass(frozen=True, slots=True)
class CapacityUsage:
    """What the admitted VMs currently consume."""

    vms: int = 0
    memory_mib: int = 0
    disk_gib: int = 0

    def plus(self, memory_mib: int, disk_gib: int) -> CapacityUsage:
        return CapacityUsage(
            vms=self.vms + 1,
            memory_mib=self.memory_mib + memory_mib,
            disk_gib=self.disk_gib + disk_gib,
        )

    def as_record(self) -> dict[str, int]:
        return {
            "vms": self.vms,
            "memory_mib": self.memory_mib,
            "disk_gib": self.disk_gib,
        }


@dataclass(frozen=True, slots=True)
class CapacitySnapshot:
    """Budget plus current usage; the single read model for status/diagnostics."""

    limits: CapacityLimits
    usage: CapacityUsage

    @property
    def free_vms(self) -> int:
        return max(0, self.limits.max_vms - self.usage.vms)

    @property
    def free_memory_mib(self) -> int:
        return max(0, self.limits.memory_mib - self.usage.memory_mib)

    @property
    def free_disk_gib(self) -> int:
        return max(0, self.limits.disk_gib - self.usage.disk_gib)


@dataclass(frozen=True, slots=True)
class CapacityWait:
    """One visible capacity-wait status for a queued session."""

    session_id: str
    state: str
    reason: str
    position: int
    snapshot: CapacitySnapshot

    def key(self) -> tuple[str, str, int, int, int, int]:
        """Identity of the *status*; a repeat changes nothing for the reader."""
        return (
            self.state,
            self.reason,
            self.position,
            self.snapshot.usage.vms,
            self.snapshot.usage.memory_mib,
            self.snapshot.usage.disk_gib,
        )

    def to_trace_record(self, *, subject: str | None = None) -> dict[str, Any]:
        return trace_event(
            CcTraceEvent.ISOLATION_CAPACITY_WAIT,
            subject=(subject or self.session_id),
            state=self.state,
            reason=self.reason,
            position=self.position,
            usage_vms=self.snapshot.usage.vms,
            limit_vms=self.snapshot.limits.max_vms,
            usage_memory_mb=self.snapshot.usage.memory_mib,
            limit_memory_mb=self.snapshot.limits.memory_mib,
            usage_disk_gb=self.snapshot.usage.disk_gib,
            limit_disk_gb=self.snapshot.limits.disk_gib,
        )


@dataclass(frozen=True, slots=True)
class _Slot:
    session_id: str
    fd: int
    path: Path
    memory_mib: int
    disk_gib: int


class AdmissionController:
    """At most ``limits.max_vms`` live VMs within the RAM/disk budget, host-wide."""

    def __init__(
        self,
        limits: CapacityLimits,
        *,
        directory: str | os.PathLike[str],
        wait_timeout_s: float = DEFAULT_WAIT_TIMEOUT_S,
        poll_interval_s: float = DEFAULT_POLL_S,
    ) -> None:
        self._limits = limits
        self._directory = Path(directory)
        self._wait_timeout_s = max(0.0, float(wait_timeout_s))
        self._poll_s = max(0.01, float(poll_interval_s))
        self._held: dict[str, _Slot] = {}
        self._waiting: dict[str, int] = {}
        self._next_ticket = 1
        self._guard = threading.Lock()
        self._condition = threading.Condition(self._guard)
        self._fs_guard = threading.Lock()

    @property
    def limits(self) -> CapacityLimits:
        return self._limits

    @property
    def held_sessions(self) -> tuple[str, ...]:
        with self._guard:
            return tuple(self._held)

    @staticmethod
    def slot_name(session_id: str) -> str:
        """Filesystem-safe slot file name for a session id (never the raw id)."""
        digest = hashlib.sha256(str(session_id).encode("utf-8")).hexdigest()
        return f"{digest[:_DIGEST_CHARS]}{SLOT_SUFFIX}"

    def acquire(
        self,
        session_id: str,
        *,
        memory_mib: int,
        disk_gib: int,
        on_wait: WaitCallback | None = None,
        should_abort: AbortCheck | None = None,
        timeout_s: float | None = None,
    ) -> CapacitySnapshot:
        """Admit one VM, waiting (visibly) for capacity when the budget is full.

        Returns the post-claim snapshot. Raises :class:`IsolationError` with
        ``capacity_wait_timeout`` when the wait exceeds the TTL, or
        ``capacity_wait_canceled`` when ``should_abort`` reports a stop while
        queued. In both cases the wait callback sees the terminal state first and
        no slot is kept. Re-acquiring a session this controller already holds is
        an idempotent no-op; a slot held for the same session by a *different*
        controller raises ``admission_conflict`` (one session, one VM, host-wide).
        """
        text = _session_text(session_id)
        if memory_mib < 1 or disk_gib < 1:
            raise IsolationError(
                "invalid_admission_request",
                f"memory_mib={memory_mib} disk_gib={disk_gib} must be positive",
            )
        ttl = self._wait_timeout_s if timeout_s is None else max(0.0, float(timeout_s))
        deadline = time.monotonic() + ttl
        last_key: tuple[str, str, int, int, int, int] | None = None
        while True:
            if should_abort is not None and should_abort():
                if last_key is None:
                    self._drop_waiter(text)
                else:
                    self._finish_wait(
                        text, on_wait, last_key, state=STATE_CANCELED, reason=last_key[1]
                    )
                raise IsolationError(
                    CODE_WAIT_CANCELED,
                    f"session {text!r} was stopped while waiting for capacity",
                )
            with self._fs_guard, self._flock():
                usage, owners = self._scan_locked()
                if text in owners:
                    with self._guard:
                        mine = text in self._held
                    if mine:
                        return self._snapshot(usage)
                    raise IsolationError(
                        CODE_CONFLICT,
                        (
                            f"session {text!r} already holds a live VM slot in "
                            "another admission controller"
                        ),
                    )
                blocked = _blocked_dimensions(self._limits, usage, memory_mib, disk_gib)
                if not blocked:
                    self._claim_locked(text, memory_mib=memory_mib, disk_gib=disk_gib)
                    return self._snapshot(usage.plus(memory_mib, disk_gib))
            position = self._register_waiter(text)
            record = self._wait_record(
                text, STATE_WAITING, "+".join(blocked), position, usage
            )
            if last_key is None or record.key() != last_key:
                _invoke_wait(on_wait, record)
                last_key = record.key()
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                self._finish_wait(
                    text, on_wait, last_key, state=STATE_TIMEOUT, reason="+".join(blocked)
                )
                raise IsolationError(
                    CODE_WAIT_TIMEOUT,
                    (
                        f"session {text!r} waited {ttl:.1f}s for capacity; "
                        f"blocked on {'+'.join(blocked)} "
                        f"(usage vms={usage.vms} memory={usage.memory_mib}MiB "
                        f"disk={usage.disk_gib}GiB)"
                    ),
                )
            self._wait_for_change(min(self._poll_s, remaining))

    def release(self, session_id: str) -> bool:
        """Drop the session's slot and wake queued waiters; idempotent."""
        text = str(session_id).strip()
        if not text:
            return False
        with self._fs_guard, self._flock():
            with self._guard:
                slot = self._held.pop(text, None)
            if slot is None:
                return False
            _unlock_close_unlink(slot)
        with self._condition:
            self._condition.notify_all()
        logger.info("isolation admission: session %s released its VM slot", text)
        return True

    def snapshot(self) -> CapacitySnapshot:
        """Current host-wide usage; reclaims stale slots left by dead processes."""
        return self._snapshot(self._usage_now())

    def close(self) -> None:
        """Release every slot this controller holds."""
        for session_id in self.held_sessions:
            self.release(session_id)

    def _snapshot(self, usage: CapacityUsage) -> CapacitySnapshot:
        return CapacitySnapshot(limits=self._limits, usage=usage)

    def _wait_record(
        self,
        session_id: str,
        state: str,
        reason: str,
        position: int,
        usage: CapacityUsage,
    ) -> CapacityWait:
        return CapacityWait(
            session_id=session_id,
            state=state,
            reason=reason,
            position=position,
            snapshot=self._snapshot(usage),
        )

    def _finish_wait(
        self,
        session_id: str,
        on_wait: WaitCallback | None,
        last_key: tuple[str, str, int, int, int, int] | None,
        *,
        state: str,
        reason: str,
    ) -> None:
        with self._guard:
            ticket = self._waiting.pop(session_id, None)
            position = (
                1
                if ticket is None
                else 1 + sum(1 for other in self._waiting.values() if other < ticket)
            )
        record = self._wait_record(
            session_id, state, reason, position, self._usage_now()
        )
        if last_key is None or record.key() != last_key:
            _invoke_wait(on_wait, record)

    def _usage_now(self) -> CapacityUsage:
        with self._fs_guard, self._flock():
            usage, _owners = self._scan_locked()
        return usage

    def _register_waiter(self, session_id: str) -> int:
        with self._guard:
            ticket = self._waiting.get(session_id)
            if ticket is None:
                ticket = self._next_ticket
                self._next_ticket += 1
                self._waiting[session_id] = ticket
            return 1 + sum(1 for other in self._waiting.values() if other < ticket)

    def _drop_waiter(self, session_id: str) -> None:
        with self._guard:
            self._waiting.pop(session_id, None)

    def _wait_for_change(self, seconds: float) -> None:
        with self._condition:
            self._condition.wait(timeout=max(0.0, seconds))

    @contextmanager
    def _flock(self) -> Iterator[None]:
        try:
            self._directory.mkdir(parents=True, exist_ok=True)
            fd = os.open(
                self._directory / GLOBAL_LOCK_NAME,
                os.O_RDWR | os.O_CREAT | os.O_CLOEXEC,
                0o600,
            )
        except OSError as exc:
            raise IsolationError(
                CODE_STATE_CORRUPT, f"{self._directory}: {exc}"
            ) from exc
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield
        finally:
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
            finally:
                os.close(fd)

    def _scan_locked(self) -> tuple[CapacityUsage, set[str]]:
        """Atomic usage scan under the global lock; unlinks dead holders' slots.

        Returns the summed usage of every live slot plus the set of sessions
        those slots belong to, so the caller can tell "already admitted by this
        controller" from "a sibling controller owns this session" (fail-closed).
        """
        usage = CapacityUsage()
        owners: set[str] = set()
        for path in sorted(self._directory.glob(f"*{SLOT_SUFFIX}")):
            try:
                fd = os.open(path, os.O_RDWR | os.O_CLOEXEC)
            except FileNotFoundError:
                continue
            except OSError as exc:
                raise IsolationError(
                    CODE_STATE_CORRUPT, f"{path}: {exc}"
                ) from exc
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except OSError:
                payload = _read_slot_payload(path, fd)
                os.close(fd)
                owners.add(_slot_session(payload, path))
                usage = usage.plus(
                    _slot_int(payload, "memory_mib", path),
                    _slot_int(payload, "disk_gib", path),
                )
                continue
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
            finally:
                os.close(fd)
            try:
                path.unlink()
            except FileNotFoundError:
                pass
            except OSError as exc:
                logger.warning(
                    "isolation admission: cannot remove the stale slot %s: %s", path, exc
                )
        return usage, owners

    def _claim_locked(self, session_id: str, *, memory_mib: int, disk_gib: int) -> None:
        path = self._directory / self.slot_name(session_id)
        try:
            fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_CLOEXEC, 0o600)
        except OSError as exc:
            raise IsolationError(CODE_STATE_CORRUPT, f"{path}: {exc}") from exc
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            os.close(fd)
            raise IsolationError(
                CODE_CONFLICT,
                f"{path} is already held for session {session_id!r} by a live sibling",
            ) from exc
        payload = json.dumps(
            {
                "session_id": session_id,
                "pid": os.getpid(),
                "memory_mib": memory_mib,
                "disk_gib": disk_gib,
            }
        ).encode("utf-8")
        try:
            os.ftruncate(fd, 0)
            os.write(fd, payload)
        except OSError as exc:
            _unlock_close_unlink(
                _Slot(session_id=session_id, fd=fd, path=path, memory_mib=memory_mib,
                      disk_gib=disk_gib)
            )
            raise IsolationError(
                CODE_STATE_CORRUPT, f"{path}: cannot record the slot: {exc}"
            ) from exc
        with self._guard:
            self._held[session_id] = _Slot(
                session_id=session_id,
                fd=fd,
                path=path,
                memory_mib=memory_mib,
                disk_gib=disk_gib,
            )
        logger.info(
            "isolation admission: session %s admitted (vms=%d/%d, memory=%dMiB, disk=%dGiB)",
            session_id,
            len(self._held),
            self._limits.max_vms,
            memory_mib,
            disk_gib,
        )


def _session_text(session_id: str) -> str:
    text = str(session_id).strip()
    if not text:
        raise IsolationError("invalid_session_id", "session id must be non-empty")
    return text


def _blocked_dimensions(
    limits: CapacityLimits, usage: CapacityUsage, memory_mib: int, disk_gib: int
) -> tuple[str, ...]:
    """Which budget dimension(s) refuse one more VM, in a stable order."""
    blocked: list[str] = []
    if usage.vms + 1 > limits.max_vms:
        blocked.append(REASON_MAX_VMS)
    if usage.memory_mib + memory_mib > limits.memory_mib:
        blocked.append(REASON_MEMORY)
    if usage.disk_gib + disk_gib > limits.disk_gib:
        blocked.append(REASON_DISK)
    return tuple(blocked)


def _invoke_wait(on_wait: WaitCallback | None, record: CapacityWait) -> None:
    if on_wait is None:
        return
    try:
        on_wait(record)
    except Exception:  # a status sink must never change admission semantics
        logger.warning(
            "isolation admission: capacity wait callback raised", exc_info=True
        )


def _read_slot_payload(path: Path, fd: int) -> Mapping[str, Any]:
    try:
        os.lseek(fd, 0, os.SEEK_SET)
        raw = os.read(fd, _SLOT_MAX_BYTES)
        payload = json.loads(raw.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise IsolationError(
            CODE_STATE_CORRUPT, f"{path}: unreadable live slot ({exc})"
        ) from exc
    if not isinstance(payload, dict):
        raise IsolationError(
            CODE_STATE_CORRUPT, f"{path}: slot payload is {type(payload).__name__}"
        )
    return payload


def _slot_session(payload: Mapping[str, Any], path: Path) -> str:
    session = payload.get("session_id")
    if not isinstance(session, str) or not session.strip():
        raise IsolationError(
            CODE_STATE_CORRUPT, f"{path}: slot carries no session id"
        )
    return session


def _slot_int(payload: Mapping[str, Any], key: str, path: Path) -> int:
    value = payload.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise IsolationError(
            CODE_STATE_CORRUPT, f"{path}: {key} is {value!r}"
        )
    return value


def _unlock_close_unlink(slot: _Slot) -> None:
    try:
        os.ftruncate(slot.fd, 0)
    except OSError:
        pass
    try:
        fcntl.flock(slot.fd, fcntl.LOCK_UN)
    finally:
        os.close(slot.fd)
    try:
        slot.path.unlink()
    except FileNotFoundError:
        pass
    except OSError as exc:
        logger.warning(
            "isolation admission: cannot remove the slot %s: %s", slot.path, exc
        )


def emit_capacity_wait(
    workdir: str | os.PathLike[str] | None,
    wait: CapacityWait,
    *,
    subject: str | None = None,
    writer: Callable[[str, dict[str, Any]], None] | None = None,
) -> bool:
    """Append the wait status to the authoritative host trace; never raises.

    This is what makes the queue visible to the existing mobile event pipeline:
    the record carries the taxonomy kind ``isolation.capacity_wait`` and the
    structural fields the UI needs. A missing workdir is logged and reported,
    never silently dropped.
    """
    if workdir is None or not str(workdir).strip():
        logger.warning(
            "isolation admission: capacity wait for session %s dropped "
            "(no artifact_workdir)",
            wait.session_id,
        )
        return False
    write = writer if writer is not None else append_cc_backend_trace
    try:
        write(str(workdir), wait.to_trace_record(subject=subject))
    except OSError as exc:
        logger.warning(
            "isolation admission: cannot append the capacity wait record: %s", exc
        )
        return False
    return True
