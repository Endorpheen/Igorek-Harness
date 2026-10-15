"""Outbound event streaming for the in-guest runner (plan todo 7).

The executor owns its trace file (``cc_backend_trace.append_cc_backend_trace``),
so streaming needs no executor seam: :class:`TraceTailer` follows the append-only
JSONL while the loop runs and yields every record this run appended exactly once.
``trace_start_offset`` pins the boundary: records written before the run started
(a reused artifact dir in a long-lived VM) are never replayed to the host.

The runner wraps each record into an ``event`` frame; the host's payload policy
stays authoritative about what may cross, and a rejected record is dropped, never
allowed to break the stream.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, BinaryIO

from .channel import JsonValue
from .guest_task import narrow_json_value

logger = logging.getLogger(__name__)

__all__ = ["TraceTailer", "trace_start_offset"]


def trace_start_offset(path: Path) -> int:
    """Current size of the trace file: everything before it belongs to earlier runs."""
    try:
        return path.stat().st_size
    except FileNotFoundError:
        return 0  # the executor has not written its first trace line yet


class TraceTailer:
    """Incremental reader for records the current run appends to the trace JSONL."""

    def __init__(self, path: Path, *, start_offset: int = 0) -> None:
        self._path = path
        self._start_offset = start_offset
        self._handle: BinaryIO | None = None
        self._buffer = b""

    def read_new(self, *, final: bool = False) -> list[JsonValue]:
        """Complete records appended since the last call (plus the tail when final)."""
        chunk = self._read_chunk()
        if chunk:
            self._buffer += chunk
        records = self._parse_lines()
        if final and self._buffer.strip():
            record = _parse_trace_line(self._buffer.decode("utf-8", errors="replace"))
            self._buffer = b""
            if record is not None:
                records.append(record)
        return records

    def _read_chunk(self) -> bytes:
        if self._handle is None:
            try:
                self._handle = self._path.open("rb")
            except FileNotFoundError:
                return b""
            self._handle.seek(self._start_offset)
        try:
            return self._handle.read()
        except OSError as exc:  # a broken tail must not kill the run
            logger.warning("guest runner: trace tail failed: %s", exc)
            return b""

    def _parse_lines(self) -> list[JsonValue]:
        lines = self._buffer.split(b"\n")
        self._buffer = lines.pop()
        records: list[JsonValue] = []
        for line in lines:
            record = _parse_trace_line(line.decode("utf-8", errors="replace"))
            if record is not None:
                records.append(record)
        return records


def _parse_trace_line(line: str) -> JsonValue | None:
    text = line.strip()
    if not text:
        return None
    try:
        decoded: Any = json.loads(text)
    except json.JSONDecodeError:
        logger.warning("guest runner: skipping a malformed trace line")
        return None
    return narrow_json_value(decoded)
