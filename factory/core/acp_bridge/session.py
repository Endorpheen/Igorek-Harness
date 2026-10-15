"""ACP session manager — maps ACP sessions to factory cc-backend agents.

Sessions are held in-memory for fast access. Persistence can be added
via SessionDB later.
"""

from __future__ import annotations

import copy
import json
import logging
import uuid
from dataclasses import dataclass, field
from threading import Lock
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class SessionState:
    """Tracks per-session state for an ACP-managed factory agent."""

    session_id: str
    cwd: str = "."
    model: str = ""
    history: list[dict[str, Any]] = field(default_factory=list)
    cancel_event: Any = None  # threading.Event


class SessionManager:
    """Thread-safe manager for ACP sessions backed by factory cc-backend.

    Sessions are held in-memory. For production, persistence via SQLite
    (like Hermes SessionDB) can be added.
    """

    def __init__(self):
        self._sessions: dict[str, SessionState] = {}
        self._lock = Lock()

    def create_session(self, cwd: str = ".") -> SessionState:
        """Create a new session with a unique ID."""
        import threading

        session_id = str(uuid.uuid4())
        state = SessionState(
            session_id=session_id,
            cwd=cwd,
            model="",
            history=[],
            cancel_event=threading.Event(),
        )
        with self._lock:
            self._sessions[session_id] = state
        logger.info("Created ACP session %s (cwd=%s)", session_id, cwd)
        return state

    def get_session(self, session_id: str) -> SessionState | None:
        """Return the session for session_id, or None."""
        with self._lock:
            return self._sessions.get(session_id)

    def remove_session(self, session_id: str) -> bool:
        """Remove a session. Returns True if it existed."""
        with self._lock:
            existed = self._sessions.pop(session_id, None) is not None
        return existed

    def fork_session(self, session_id: str, cwd: str = ".") -> SessionState | None:
        """Deep-copy a session's history into a new session."""
        import threading

        original = self.get_session(session_id)
        if original is None:
            return None

        new_id = str(uuid.uuid4())
        state = SessionState(
            session_id=new_id,
            cwd=cwd,
            model=original.model,
            history=copy.deepcopy(original.history),
            cancel_event=threading.Event(),
        )
        with self._lock:
            self._sessions[new_id] = state
        logger.info("Forked ACP session %s -> %s", session_id, new_id)
        return state

    def update_cwd(self, session_id: str, cwd: str) -> SessionState | None:
        """Update the working directory for a session."""
        state = self.get_session(session_id)
        if state is None:
            return None
        state.cwd = cwd
        return state

    def save_session(self, session_id: str) -> None:
        """No-op for in-memory sessions. Hook for future DB persistence."""
        pass

    def list_sessions(self) -> list[dict[str, Any]]:
        """Return lightweight info dicts for all sessions."""
        with self._lock:
            return [
                {
                    "session_id": s.session_id,
                    "cwd": s.cwd,
                    "model": s.model,
                    "history_len": len(s.history),
                }
                for s in self._sessions.values()
            ]
