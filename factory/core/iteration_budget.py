"""Thread-safe iteration counter for agent (parent or subagent).

Each agent turn consumes one iteration.  Subagents get their own budget so
total iterations across parent + children can exceed the parent's cap.
"""
from __future__ import annotations

import threading


class IterationBudget:
    """Thread-safe iteration counter for an agent.

    Consumed by every LLM turn.  Refunds are supported for turns that
    should not count against the budget (e.g. programmatic retries).
    """

    def __init__(self, max_total: int) -> None:
        self.max_total = max_total
        self._used = 0
        self._lock = threading.Lock()

    def consume(self) -> bool:
        """Try to consume one iteration. Returns True if allowed."""
        with self._lock:
            if self._used >= self.max_total:
                return False
            self._used += 1
            return True

    def refund(self) -> None:
        """Give back one iteration."""
        with self._lock:
            if self._used > 0:
                self._used -= 1

    @property
    def used(self) -> int:
        return self._used

    @property
    def remaining(self) -> int:
        with self._lock:
            return max(0, self.max_total - self._used)
