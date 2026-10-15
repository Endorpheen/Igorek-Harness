"""MessageQueue — thread-safe FIFO queue for user messages.

Pattern adopted from Claude Code (messageQueueManager):
- While the agent is active, user messages are placed in the queue.
- After the move completes, the queue is drained and sent to the agent.
- The queue operates outside React state (in our case, at the module level in cli_chat_rich).

Used in conjunction with TurnGuard.
"""

from __future__ import annotations

import threading
from collections import deque


class MessageQueue:
    """Thread-safe FIFO queue for user messages during agent work."""

    def __init__(self) -> None:
        self._queue: deque[str] = deque()
        self._lock = threading.Lock()

    def enqueue(self, text: str) -> None:
        """Add message to the queue."""
        with self._lock:
            self._queue.append(text)

    def drain_all(self) -> list[str]:
        """Drain all messages from the queue and clear it."""
        with self._lock:
            items = list(self._queue)
            self._queue.clear()
            return items

    @property
    def pending_count(self) -> int:
        """Number of messages in the queue."""
        with self._lock:
            return len(self._queue)

    def peek_all(self) -> list[str]:
        """Return all messages without clearing the queue."""
        with self._lock:
            return list(self._queue)

    @property
    def has_pending(self) -> bool:
        """Are there any messages in the queue?"""
        with self._lock:
            return len(self._queue) > 0
