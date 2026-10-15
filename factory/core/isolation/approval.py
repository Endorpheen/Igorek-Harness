"""Approval relay across the VM boundary (plan todo 9).

The guest never approves its own blocked tool. The in-guest loop stops with the
executor's own ``approval_required`` result, the runner forwards the executor's
blocked-tool payload as an inert ``approval_request`` envelope
(:data:`~factory.core.isolation.channel.DISPLAY_ENVELOPE_KEY`), and this module
turns it into the host-side decision contract:

* **pause** - :meth:`ApprovalRelay.park` yields the task result the worker
  persists: ``waiting_approval`` only for ``source_platform == "android"`` and
  ``fail`` otherwise. That is the exact rule an off-mode run applies
  (``cc_backend_executor``'s ``approval_wait_status``); the host recomputes it
  from its OWN dispatch payload, so a guest cannot park a task the person will
  never see. The manager parks only the task and keeps its session VM warm for
  approval resume, subject to the long idle TTL;
* **canonicalize** - every guest path (``/workspace/...``) in the payload is
  rewritten to its host path at this boundary, because the mobile approval
  contract and ``approved_tool_calls`` are matched against host-computed
  signatures. :func:`resume_wire_metadata` applies the reverse mapping when the
  host re-dispatches the resumed task, so the signature the guest recomputes
  equals the approved one and the tool runs instead of re-prompting forever;
* **consume once** - a duplicated ``approval_request`` for one ``tool_use_id``
  (a channel retry) appends exactly one blocked entry.

This module is host-only: the guest runner never imports it.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Final, assert_never

from .channel import DISPLAY_ENVELOPE_KEY, ChannelMessage, JsonValue
from .workspace import GUEST_WORKSPACE_PATH

logger = logging.getLogger(__name__)

__all__ = [
    "ApprovalRelay",
    "WorkspacePathMapping",
    "approval_wait_status",
    "canonicalize_blocked_tools",
    "resume_wire_metadata",
]

#: Statuses a blocked call may already carry when it reaches the relay.
PARKED_STATUSES: Final[frozenset[str]] = frozenset({"waiting_approval", "fail"})
#: Nested conversation replay: a new guest mounts the same workspace at the same
#: guest root, so it is replayed verbatim and never rewritten (host-side display
#: does not use it).
VERBATIM_KEYS: Final[frozenset[str]] = frozenset({"approval_context_messages"})


@dataclass(frozen=True, slots=True)
class WorkspacePathMapping:
    """Guest <-> host path mapping for the single workspace export."""

    host_root: str
    guest_root: str = GUEST_WORKSPACE_PATH

    def guest_to_host(self, text: str) -> str:
        """Rewrite every ``/workspace`` path in ``text`` to its host path."""
        return _replace_root(text, self.guest_root, self.host_root)

    def host_to_guest(self, text: str) -> str:
        """Rewrite every host workspace path in ``text`` back to ``/workspace``."""
        return _replace_root(text, self.host_root, self.guest_root)

    def map_guest_value(self, value: JsonValue) -> JsonValue:
        return _map_strings(value, self.guest_to_host)

    def map_host_value(self, value: JsonValue) -> JsonValue:
        return _map_strings(value, self.host_to_guest)


def _replace_root(text: str, root: str, replacement: str) -> str:
    """Boundary-aware literal root replacement (``/workspacefoo`` never matches)."""
    if not root or root not in text:
        return text
    pattern = re.compile(re.escape(root) + r"(?![\w.-])")
    return pattern.sub(lambda _match: replacement, text)


def _map_strings(value: JsonValue, replace: Callable[[str], str]) -> JsonValue:
    match value:
        case str():
            return replace(value)
        case list():
            return [_map_strings(item, replace) for item in value]
        case dict():
            return {
                key: (item if key in VERBATIM_KEYS else _map_strings(item, replace))
                for key, item in value.items()
            }
        case bool() | int() | float() | None:
            return value
        case _ as unreachable:
            assert_never(unreachable)


def canonicalize_blocked_tools(
    blocked: Mapping[str, JsonValue], mapping: WorkspacePathMapping
) -> dict[str, JsonValue]:
    """Rewrite every guest path of one blocked-tool payload to its host path."""
    return {
        key: (value if key in VERBATIM_KEYS else mapping.map_guest_value(value))
        for key, value in blocked.items()
    }


def approval_wait_status(source_platform: str | None) -> str:
    """The paused-task status rule of the cc-backend executor (its lines 1308-1312)."""
    return (
        "waiting_approval"
        if str(source_platform or "").strip().lower() == "android"
        else "fail"
    )


def resume_wire_metadata(metadata: object, mapping: WorkspacePathMapping) -> dict[str, JsonValue]:
    """Host->guest approval resume subset of task metadata (``{}`` when absent)."""
    if not isinstance(metadata, Mapping):
        return {}
    wire: dict[str, JsonValue] = {}
    forced = metadata.get("forced_tool_call")
    if isinstance(forced, dict) and forced:
        wire["forced_tool_call"] = _map_mapping(forced, mapping)
    calls = metadata.get("approved_tool_calls")
    if isinstance(calls, list) and calls:
        wire["approved_tool_calls"] = [mapping.host_to_guest(str(item)) for item in calls]
    rules = metadata.get("approved_session_rules")
    if isinstance(rules, list) and rules:
        wire["approved_session_rules"] = [
            _map_mapping(rule, mapping) for rule in rules if isinstance(rule, dict)
        ]
    return wire


def _map_mapping(
    values: Mapping[str, JsonValue], mapping: WorkspacePathMapping
) -> dict[str, JsonValue]:
    return {
        key: (item if key in VERBATIM_KEYS else mapping.map_host_value(item))
        for key, item in values.items()
    }


class ApprovalRelay:
    """Per-dispatch approval boundary: collect frames, canonicalize, park the task."""

    def __init__(
        self, mapping: WorkspacePathMapping, *, source_platform: str | None = ""
    ) -> None:
        self._mapping = mapping
        self._status = approval_wait_status(source_platform)
        self._collected: list[dict[str, JsonValue]] = []
        self._seen_ids: set[str] = set()

    @property
    def parked(self) -> bool:
        """True once at least one blocked call was collected."""
        return bool(self._collected)

    @property
    def status(self) -> str:
        """The paused status the host will persist for this dispatch."""
        return self._status

    def collect(self, message: ChannelMessage) -> bool:
        """Consume one guest ``approval_request`` frame (a duplicate changes nothing)."""
        blocked = _blocked_record(message.payload)
        if blocked is None:
            return False
        tool_use_id = str(blocked.get("tool_use_id") or "").strip()
        if not tool_use_id:
            logger.warning("isolation approval relay: skipping a request without a tool_use_id")
            return False
        if tool_use_id in self._seen_ids:
            logger.info("isolation approval relay: duplicate request for %s ignored", tool_use_id)
            return False
        self._seen_ids.add(tool_use_id)
        self._collected.append(canonicalize_blocked_tools(blocked, self._mapping))
        return True

    def collect_host_op(self, record: Mapping[str, JsonValue]) -> bool:
        """Consume one parked host-op record (a duplicate request changes nothing).

        Host-op records carry no workspace paths - the broker's typed args cannot
        express one - so they are stored verbatim, exactly as the broker issued
        them, and listed by the existing approval surfaces as ``kind == "host_op"``.
        """
        request_id = str(record.get("request_id") or "").strip()
        if not request_id:
            logger.warning("isolation approval relay: skipping a host-op request without an id")
            return False
        marker = f"host_op:{request_id}"
        if marker in self._seen_ids:
            logger.info("isolation approval relay: duplicate host-op request %s ignored", request_id)
            return False
        self._seen_ids.add(marker)
        self._collected.append(dict(record))
        return True

    def park(self, result: Mapping[str, Any]) -> dict[str, Any] | None:
        """The parked task result, or ``None`` when this dispatch is not an approval park."""
        blocked = list(self._collected)
        if not blocked:
            status = str(result.get("status") or "")
            raw = result.get("approval_blocked_tools")
            if status not in PARKED_STATUSES:
                return None

            def missing_approval_result() -> dict[str, Any]:
                failed = dict(result)
                failed.update(
                    {
                        "status": "fail",
                        "failure_reason": "approval_request_missing",
                        "approval_blocked_tools": [],
                        "failure_details": (
                            "guest reported waiting_approval without a deliverable approval record"
                        ),
                        "output_text": (
                            "Не удалось безопасно передать запрос подтверждения. "
                            "Задача остановлена; повторите её после обновления."
                        ),
                    }
                )
                return failed

            if not isinstance(raw, list) or not raw:
                return missing_approval_result() if status == "waiting_approval" else None
            blocked = [
                canonicalize_blocked_tools(item, self._mapping)
                for item in raw
                if isinstance(item, dict)
            ]
            if not blocked:
                return missing_approval_result() if status == "waiting_approval" else None
        parked = dict(result)
        parked["status"] = self._status
        parked["failure_reason"] = "approval_required"
        parked["approval_blocked_tools"] = blocked
        return parked

    def park_after_timeout(self) -> dict[str, Any] | None:
        """Park shape for a guest that stopped right after the approval request."""
        if not self._collected:
            return None
        return {
            "status": self._status,
            "failure_reason": "approval_required",
            "failure_details": "guest stopped after the approval request",
            "output_text": "Для выполнения этого действия требуется подтверждение.",
            "usage": {},
            "tool_calls_count": 0,
            "approval_blocked_tools": list(self._collected),
        }


def _blocked_record(payload: Mapping[str, JsonValue]) -> dict[str, JsonValue] | None:
    """Narrow one frame payload into the executor's blocked-tool payload."""
    record = payload.get(DISPLAY_ENVELOPE_KEY)
    if not isinstance(record, dict):
        logger.warning(
            "isolation approval relay: skipping a frame without a %r envelope",
            DISPLAY_ENVELOPE_KEY,
        )
        return None
    tool = record.get("tool")
    if not isinstance(tool, str) or not tool.strip():
        logger.warning("isolation approval relay: skipping a request without a tool name")
        return None
    return dict(record)
