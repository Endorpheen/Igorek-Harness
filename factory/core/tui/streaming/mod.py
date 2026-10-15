"""Stream state — full port of Codex streaming/mod.rs."""
from collections import deque
import time

class StreamState:
    """Newline-gated markdown collector with timestamps."""
    def __init__(self):
        self._queue: deque[tuple[float, str]] = deque()
        self._committed: list[str] = []
        self._buffer: str = ""

    def feed(self, text: str):
        if text:
            self._queue.append((time.monotonic(), text))

    def commit_newlines(self) -> list[str]:
        result = []
        while self._queue:
            ts, text = self._queue[0]
            full = self._buffer + text
            if "\n" not in full:
                self._buffer = full
                self._queue.popleft()
                continue
            self._queue.popleft()
            parts = full.split("\n")
            self._buffer = parts[-1]
            for line in parts[:-1]:
                self._committed.append(line)
                result.append(line)
        return result

    def drain_all(self) -> list[str]:
        result = self.commit_newlines()
        while self._queue:
            _, text = self._queue.popleft()
            self._buffer += text
        if self._buffer:
            result.append(self._buffer)
            self._committed.append(self._buffer)
            self._buffer = ""
        return result

    def queue_depth(self) -> int:
        return len(self._queue) + (1 if self._buffer else 0)

    def oldest_age(self) -> float:
        if not self._queue:
            return 0.0
        return time.monotonic() - self._queue[0][0]

    def committed_lines(self) -> list[str]:
        return list(self._committed)

    def tail_text(self) -> str:
        return self._buffer + "".join(text for _, text in self._queue)
