"""Host-side relay of guest trace frames into the existing mobile pipeline (todo 8).

Frame contract (produced by :mod:`factory.core.isolation.guest_runner`): an
``event`` frame carries ``{"kind": "trace", "record": <trace record>}``, whose
record is exactly what the in-guest executor appended to its own
``cc_backend_trace.jsonl``. There is no second event pipeline: the relay appends
each accepted record to the HOST trace file with the existing
:func:`factory.core.cc_backend_trace.append_cc_backend_trace`, so
:mod:`factory.core.mobile_bridge.events` keeps producing the same normalized
mobile events (``task.start``, ``tool.request_batch``, ``tool.result``,
``approval.required``, ``task.finish``, ``task.status``, ...) with the same
``seq`` ordering an off-mode run produces for the same records.

The host is the sole authoritative writer of that file: the guest only sends
frames and never holds the host path. The channel's payload policy treats the
record as an inert display envelope (see :mod:`factory.core.isolation.channel`),
which is what lets a faithful trace record carry tool input with ``command`` /
``argv`` keys.

A frame the relay cannot interpret - an unknown payload kind, a non-object
record, or a record kind that is not a taxonomy-shaped ``domain.action`` - is
skipped with a warning: a bad frame must never raise into the awaiting host
loop, and the frames after it are still relayed. Valid-but-unknown taxonomy
kinds are forwarded verbatim; the mobile layer already normalizes those through
its generic fallback.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from pathlib import Path

from ..cc_backend_trace import append_cc_backend_trace
from ..trace_taxonomy import EVENT_KIND_RE
from .channel import DISPLAY_ENVELOPE_KEY, TRACE_EVENT_KIND, ChannelMessage, JsonValue

logger = logging.getLogger(__name__)

__all__ = ["TraceRelay"]

TraceWriter = Callable[[str, dict[str, JsonValue]], None]


class TraceRelay:
    """Append-only host writer for the guest trace records of one dispatch."""

    def __init__(
        self,
        artifact_workdir: str | Path,
        *,
        writer: TraceWriter = append_cc_backend_trace,
    ) -> None:
        self._workdir = str(artifact_workdir)
        self._writer = writer
        self._written = 0
        try:
            Path(self._workdir).mkdir(parents=True, exist_ok=True)
        except OSError as exc:  # a trace concern must never fail the task itself
            logger.warning("isolation relay: artifact dir unavailable (%s): %s", self._workdir, exc)

    @property
    def written(self) -> int:
        """Records appended so far (0 when the guest sent none the relay accepted)."""
        return self._written

    def handle_event(self, message: ChannelMessage) -> bool:
        """Append one guest ``event`` record; returns ``False`` when it was skipped."""
        record = _trace_record(message.payload)
        if record is None:
            return False
        try:
            self._writer(self._workdir, record)
        except OSError as exc:  # disk full/permission: drop the record, keep the turn
            logger.warning("isolation relay: cannot append a trace record: %s", exc)
            return False
        self._written += 1
        return True


def _trace_record(payload: Mapping[str, JsonValue]) -> dict[str, JsonValue] | None:
    """Narrow one event payload into a trace record, or ``None`` with a warning."""
    if payload.get("kind") != TRACE_EVENT_KIND:
        logger.warning("isolation relay: skipping an event frame with unknown kind %r", payload.get("kind"))
        return None
    record = payload.get(DISPLAY_ENVELOPE_KEY)
    if not isinstance(record, dict):
        logger.warning("isolation relay: skipping an event frame without a display record object")
        return None
    kind = record.get("kind")
    if not isinstance(kind, str) or not EVENT_KIND_RE.match(kind):
        logger.warning("isolation relay: skipping a trace record with an invalid kind %r", kind)
        return None
    return dict(record)
