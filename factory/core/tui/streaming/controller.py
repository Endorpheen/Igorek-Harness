"""Two-region stream controller adapted from Codex streaming/controller.rs."""
from collections import deque

from .mod import StreamState

class StreamController:
    """Partitions completed markdown lines from the mutable live tail."""
    def __init__(self):
        self.state = StreamState()
        self.header_emitted = False
        self._stable_lines: deque[str] = deque()
        self._holdback_lines: list[str] = []
        self._in_fence = False
        self._in_pipe_block = False

    def feed(self, chunk: str):
        self.state.feed(chunk)

    def on_commit_tick(self) -> list[str]:
        self._collect_completed_lines()
        if not self._stable_lines:
            return []
        return [self._stable_lines.popleft()]

    def on_commit_tick_batch(self) -> list[str]:
        self._collect_completed_lines()
        lines = list(self._stable_lines)
        self._stable_lines.clear()
        return lines

    def finalize(self) -> list[str]:
        self._collect_completed_lines()
        result = list(self._stable_lines)
        result.extend(self._holdback_lines)
        result.extend(self.state.drain_all())
        self._stable_lines.clear()
        self._holdback_lines.clear()
        self._in_fence = False
        self._in_pipe_block = False
        self.header_emitted = False
        return result

    def is_idle(self) -> bool:
        return (
            self.state.queue_depth() == 0
            and not self._stable_lines
            and not self._holdback_lines
        )

    def tail_text(self) -> str:
        lines = list(self._holdback_lines)
        tail = self.state.tail_text()
        if tail:
            lines.append(tail)
        return "\n".join(lines)

    def _collect_completed_lines(self) -> None:
        for line in self.state.commit_newlines():
            stripped = line.lstrip()
            is_fence = stripped.startswith("```")
            is_pipe_line = stripped.startswith("|") and "|" in stripped[1:]

            if self._in_fence:
                self._holdback_lines.append(line)
                if is_fence:
                    self._in_fence = False
                    self._release_holdback()
                continue

            if is_fence:
                self._in_fence = True
                self._holdback_lines.append(line)
                continue

            if self._in_pipe_block:
                if is_pipe_line:
                    self._holdback_lines.append(line)
                    continue
                self._release_holdback()
                self._in_pipe_block = False

            if is_pipe_line:
                self._in_pipe_block = True
                self._holdback_lines.append(line)
            else:
                self._stable_lines.append(line)

    def _release_holdback(self) -> None:
        self._stable_lines.extend(self._holdback_lines)
        self._holdback_lines.clear()
