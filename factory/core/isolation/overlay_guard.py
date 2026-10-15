"""One live-VM lease per overlay path (todo 23, parallel sessions).

The workspace export guard (todo 32) answers *which host tree* is attached; this
module answers *which process is still running the VM that owns this overlay*.
Parallel profiles run one worker process each and share the same overlay
directory, but a process can only see its own session table - without a lease,
``IsolationManager.reap_orphans`` in one manager would destroy a live sibling
manager's domain and delete its overlay.

The lease is a non-blocking ``flock`` on ``<overlay>.lease``:

* cross-process by construction - every manager on the host serializes on the
  same kernel lock, so at most one live VM owns a given overlay path;
* self-healing - the kernel releases a dead holder's lock, so a crashed manager
  leaves a reapable overlay and never a permanent block;
* idempotent for one session, because ``ensure_vm`` is called repeatedly for the
  same session;
* probeable - ``is_leased`` answers the reaper's question ("is a live sibling
  still running this?") by try-locking, which is how a true orphan is told apart
  from a running VM.

The lease file is never unlinked: removing it would let a second process lock a
different inode and break the mutual exclusion (the same rule as the workspace
export guard). It carries the holder session id/pid for diagnostics only; the
flock *is* the claim.
"""

from __future__ import annotations

import fcntl
import json
import os
from pathlib import Path

from .errors import IsolationError

__all__ = ["LEASE_SUFFIX", "OverlayLeaseGuard", "lease_path"]

LEASE_SUFFIX = ".lease"
_MAX_HOLDER_BYTES = 4096


def lease_path(overlay: str | os.PathLike[str]) -> Path:
    """Lease file guarding one overlay path (a sibling, never the overlay)."""
    return Path(f"{os.path.realpath(str(overlay))}{LEASE_SUFFIX}")


def _holder_session_id(lock_path: Path) -> str:
    """Best-effort holder session id; only meaningful while the flock is held."""
    try:
        raw = lock_path.read_bytes()[:_MAX_HOLDER_BYTES]
    except OSError:
        return ""
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return ""
    if not isinstance(payload, dict):
        return ""
    session = payload.get("session_id")
    return session if isinstance(session, str) else ""


class OverlayLeaseGuard:
    """At most one live VM per overlay path, across processes and managers."""

    def __init__(self) -> None:
        self._held: dict[str, str] = {}
        self._fds: dict[str, int] = {}

    @property
    def held(self) -> dict[str, str]:
        """``session_id -> overlay realpath`` for the leases of this guard."""
        return dict(self._held)

    def acquire(self, overlay: str | os.PathLike[str], session_id: str) -> None:
        """Lease ``overlay`` for ``session_id`` or refuse ``overlay_busy``.

        Re-leasing the same overlay for the same session is a no-op. A lease
        held by another session - in this process or another one - raises
        :class:`IsolationError` with code ``overlay_busy`` and the holder in
        ``detail``, before the caller creates or starts any VM state.
        """
        real = os.path.realpath(str(overlay))
        text = str(session_id).strip()
        if not text:
            raise IsolationError(
                "invalid_session_id", "session_id must be a non-empty string"
            )
        held_path = self._held.get(text)
        if held_path is not None:
            if held_path == real:
                return
            raise IsolationError(
                "overlay_busy",
                f"session {text!r} already leases {held_path}, not {real}",
            )
        path = lease_path(real)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_CLOEXEC, 0o600)
        except OSError as exc:
            raise IsolationError(
                "overlay_lease_unavailable", f"{path}: {exc}"
            ) from exc
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            holder = _holder_session_id(path)
            os.close(fd)
            who = f"session {holder!r}" if holder else "another live session"
            raise IsolationError(
                "overlay_busy", f"{real} is already leased by {who}"
            ) from exc
        try:
            os.ftruncate(fd, 0)
            os.write(
                fd,
                json.dumps(
                    {"session_id": text, "pid": os.getpid(), "overlay": real}
                ).encode("utf-8"),
            )
        except OSError:
            pass  # diagnostics only; the flock above is the claim
        self._held[text] = real
        self._fds[text] = fd

    def is_leased(self, overlay: str | os.PathLike[str]) -> bool:
        """True when a live holder owns this overlay; probes with a try-lock.

        The probe never creates state: a missing lease file means no holder
        (``acquire`` always creates it before flocking), so an off-mode or
        never-leased overlay is left exactly as it was.
        """
        path = lease_path(overlay)
        try:
            fd = os.open(path, os.O_RDWR | os.O_CLOEXEC)
        except FileNotFoundError:
            return False
        except OSError as exc:
            raise IsolationError(
                "overlay_lease_unavailable", f"{path}: {exc}"
            ) from exc
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            os.close(fd)
            return True
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)
        return False

    def holder(self, overlay: str | os.PathLike[str]) -> str:
        """Best-effort session id of the live holder, or ``""`` when free."""
        return _holder_session_id(lease_path(overlay))

    def release(self, session_id: str) -> None:
        """Drop the session's lease (idempotent; unknown sessions are a no-op)."""
        text = str(session_id).strip()
        fd = self._fds.pop(text, None)
        self._held.pop(text, None)
        if fd is None:
            return
        try:
            os.ftruncate(fd, 0)
        except OSError:
            pass
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)

    def close(self) -> None:
        """Release every lease this guard holds."""
        for session_id in list(self._fds):
            self.release(session_id)
