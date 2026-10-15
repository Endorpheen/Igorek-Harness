"""TurnGuard — thread-safe state machine: idle → running → idle.

Pattern borrowed from Claude Code (QueryGuard):
- One thread at a time can hold the 'turn'.
- try_start() returns True if the turn is started, False if already occupied.
- end() releases the turn.

Used in cli_chat_rich.py for coordination:
- Main thread reads input.
- Background thread executes the agent.
- While the agent is working, new messages are queued.
"""

from __future__ import annotations

import threading


class TurnGuard:
    """Thread-safe state machine: idle ↔ running."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._active = False

    @property
    def is_active(self) -> bool:
        """Check if the move (agent is active)."""
        with self._lock:
            return self._active

    def try_start(self) -> bool:
        """Attempt to start the move. Returns `True` if successful, `False` if already occupied."""
        with self._lock:
            if self._active:
                return False
            self._active = True
            return True

    def end(self) -> None:
        """End the move and release the guard."""
        with self._lock:
            self._active = False
