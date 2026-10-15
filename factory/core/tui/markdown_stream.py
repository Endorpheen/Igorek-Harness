"""Newline-gated accumulator that buffers raw markdown source deltas.

Python port of codex-rs/tui/src/markdown_stream.rs (production code only;
Rust test-only helpers are intentionally not ported as part of this module).

The collector buffers streaming token deltas and exposes a commit
boundary at each newline. Stream controllers call
``commit_complete_source()`` after each newline-bearing delta to obtain
the completed prefix for re-rendering, leaving the trailing incomplete
line in the buffer for the next delta.

On finalization, ``finalize_and_drain_source()`` flushes whatever
remains (the last line, which may lack a trailing newline).
"""
from __future__ import annotations

from typing import Optional


class MarkdownStreamCollector:
    """Port of ``codex::markdown_stream::MarkdownStreamCollector``.

    Production-only: ``width`` / ``cwd`` and the
    ``commit_complete_lines`` / ``finalize_and_drain`` test-only
    rendering helpers from the Rust source are not ported (they are
    gated behind ``#[cfg(test)]`` in codex and not part of the runtime
    API surface).
    """

    def __init__(self) -> None:
        self.buffer: str = ""
        self.committed_source_len: int = 0

    def clear(self) -> None:
        """Reset all buffered source and commit bookkeeping.

        Port of ``MarkdownStreamCollector::clear``.
        """
        self.buffer = ""
        self.committed_source_len = 0

    def push_delta(self, delta: str) -> None:
        """Append a raw streaming delta to the internal source buffer.

        Port of ``MarkdownStreamCollector::push_delta``.
        """
        self.buffer += delta

    def commit_complete_source(self) -> Optional[str]:
        """Commit newly completed raw markdown source up to the last newline.

        Returns only source that has not been returned by a previous
        commit. Returns ``None`` if there is no new content past the
        last commit boundary (i.e. no newline has been added or the
        only new newline has already been committed).

        Port of ``MarkdownStreamCollector::commit_complete_source``.
        """
        nl = self.buffer.rfind("\n")
        if nl < 0:
            return None
        commit_end = nl + 1
        if commit_end <= self.committed_source_len:
            return None
        out = self.buffer[self.committed_source_len:commit_end]
        self.committed_source_len = commit_end
        return out

    def finalize_and_drain_source(self) -> str:
        """Finalize the stream and return any remaining raw source.

        Ensures the returned source chunk is newline-terminated when
        non-empty so callers can safely run markdown block parsing on
        the final chunk. This method clears the collector; callers
        should not invoke it until the stream is truly complete or
        interrupted output is being intentionally consolidated.

        Port of ``MarkdownStreamCollector::finalize_and_drain_source``.
        """
        if self.committed_source_len >= len(self.buffer):
            self.clear()
            return ""
        out = self.buffer[self.committed_source_len:]
        if not out.endswith("\n"):
            out += "\n"
        self.clear()
        return out


__all__ = ["MarkdownStreamCollector"]
