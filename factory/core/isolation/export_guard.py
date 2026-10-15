"""One live virtiofs export per workspace realpath (todo 32).

The workspace resolver (:mod:`factory.core.isolation.workspace`) answers *which*
host directory may be exported; this module answers *whether anyone else already
exports it*. Two sessions that resolve to the same realpath would otherwise
mount the same host tree twice and interleave writes - the failure mode is
silent workspace corruption - so the second claim is refused with the stable
``workspace_busy`` reason instead.

The claim is a non-blocking ``flock`` on a per-realpath lock file under
``<data_root>/isolation/workspace_exports/``:

* cross-process by construction - the gateway, a worker and a manual run all
  serialize on the same kernel lock;
* self-healing - the kernel releases a dead holder's lock, so a stale lock file
  can never block a new session;
* idempotent for one session, because the manager calls ``ensure_vm`` repeatedly
  for the same session.

The lock file carries the holder session id/pid for diagnostics only; the flock
*is* the claim. The file is never unlinked: removing it would let a second
process lock a different inode and break the mutual exclusion.
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import os
from pathlib import Path

from .errors import IsolationError
from .workspace import WorkspacePolicy

__all__ = ["WorkspaceExportGuard"]

LOCK_DIR_NAME = "workspace_exports"
_MAX_HOLDER_BYTES = 4096


def _lock_path(realpath: str, policy: WorkspacePolicy) -> Path:
    digest = hashlib.sha256(realpath.encode("utf-8")).hexdigest()[:32]
    return Path(policy.data_root) / "isolation" / LOCK_DIR_NAME / f"{digest}.lock"


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


class WorkspaceExportGuard:
    """At most one live export of a given workspace realpath, across processes."""

    def __init__(self, policy: WorkspacePolicy) -> None:
        self._policy = policy
        self._held: dict[str, str] = {}
        self._fds: dict[str, int] = {}

    @property
    def held(self) -> dict[str, str]:
        """``session_id -> workspace realpath`` for the claims of this guard."""
        return dict(self._held)

    def acquire(self, host_path: str | os.PathLike[str], session_id: str) -> None:
        """Claim ``host_path``'s realpath for ``session_id`` or refuse ``workspace_busy``.

        Re-claiming the same realpath for the same session is a no-op. A claim
        held by another session - in this process or another one - raises
        :class:`IsolationError` with code ``workspace_busy`` and the holder in
        ``detail``, before any VM side effect.
        """
        real = os.path.realpath(str(host_path))
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
                "workspace_busy",
                f"session {text!r} already exports {held_path}, not {real}",
            )
        path = _lock_path(real, self._policy)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_CLOEXEC, 0o600)
        except OSError as exc:
            raise IsolationError(
                "workspace_lock_unavailable", f"{path}: {exc}"
            ) from exc
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            holder = _holder_session_id(path)
            os.close(fd)
            who = f"session {holder!r}" if holder else "another session"
            raise IsolationError(
                "workspace_busy", f"{real} is already exported by {who}"
            ) from exc
        try:
            os.ftruncate(fd, 0)
            os.write(
                fd,
                json.dumps(
                    {"session_id": text, "pid": os.getpid(), "host_path": real}
                ).encode("utf-8"),
            )
        except OSError:
            pass  # diagnostics only; the flock above is the claim
        self._held[text] = real
        self._fds[text] = fd

    def release(self, session_id: str) -> None:
        """Drop the session's claim (idempotent; unknown sessions are a no-op)."""
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
        """Release every claim this guard holds."""
        for session_id in list(self._fds):
            self.release(session_id)
