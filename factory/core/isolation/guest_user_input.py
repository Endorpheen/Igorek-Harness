"""Guest-side user-input bridge for one isolated turn (plan todo 10).

The in-guest loop has no host store and no terminal. When the executor needs
structured input, :class:`UserInputBridge` publishes one
``user_input_request`` frame - the form payload travels inside the channel's
inert ``record`` envelope, exactly like a trace record or a blocked-tool
payload - and blocks the calling tool until the host answers on the same
channel:

* ``user_input_reply{status:"submitted"}`` wakes the tool with the responses;
* ``user_input_reply{status:"cancelled"}`` wakes it with ``None`` (the host
  cancels the form, or its relay timed out) and the runner aborts the run with
  ``result{status:"canceled"}``;
* no reply within the dispatch timeout raises :class:`TimeoutError`, mirroring
  ``user_input_store.wait_for_user_input_response``, so a lost host can never
  hang the guest forever.

Only the guest runner imports this module for behaviour; the host-side relay
lives in :mod:`factory.core.isolation.user_input` and shares only the timeout
constants defined here.
"""

from __future__ import annotations

import logging
import threading
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum

from .channel import (
    DISPLAY_ENVELOPE_KEY,
    USER_INPUT_CANCELLED,
    USER_INPUT_SUBMITTED,
    ChannelMessage,
    JsonValue,
    MessageKind,
)
from .guest_task import narrow_json_object, narrow_json_value

logger = logging.getLogger(__name__)

__all__ = [
    "DEFAULT_USER_INPUT_TIMEOUT_S",
    "REPLY_TIMEOUT_MARGIN_S",
    "UserInputBridge",
    "UserInputReply",
    "WebToolBridge",
    "SteerBridge",
]

#: The store's own default (``user_input_store.wait_for_user_input_response``).
DEFAULT_USER_INPUT_TIMEOUT_S: float = 1200.0
#: Extra grace over the host relay's timeout so the host's cancel wins the race.
REPLY_TIMEOUT_MARGIN_S: float = 30.0

SendFrame = Callable[[MessageKind, Mapping[str, JsonValue]], None]


class UserInputReply(StrEnum):
    """What one inbound ``user_input_reply`` frame did to a waiting request."""

    SUBMITTED = USER_INPUT_SUBMITTED
    CANCELLED = USER_INPUT_CANCELLED
    UNKNOWN = "unknown"


@dataclass(slots=True)
class _UserInputPending:
    event: threading.Event = field(default_factory=threading.Event)
    response: dict[str, object] | None = None
    cancelled: bool = False


class UserInputBridge:
    """One in-guest round trip per request: frame out, reply in, tool resumes."""

    def __init__(
        self,
        send: SendFrame,
        *,
        timeout_s: float = DEFAULT_USER_INPUT_TIMEOUT_S,
    ) -> None:
        self._send = send
        self._timeout_s = timeout_s
        self._lock = threading.Lock()
        self._pending: dict[str, _UserInputPending] = {}

    def request(self, payload: Mapping[str, object]) -> dict[str, object] | None:
        """Publish one form and block for the answer; ``None`` means cancelled."""
        request_id = str(uuid.uuid4())
        pending = _UserInputPending()
        with self._lock:
            self._pending[request_id] = pending
        try:
            self._send(
                MessageKind.USER_INPUT_REQUEST,
                {"request_id": request_id, DISPLAY_ENVELOPE_KEY: narrow_json_object(payload)},
            )
            if not pending.event.wait(self._timeout_s):
                raise TimeoutError(
                    f"Timeout while waiting for user input reply {request_id}"
                )
        finally:
            with self._lock:
                self._pending.pop(request_id, None)
        if pending.cancelled:
            return None
        return pending.response if pending.response is not None else {}

    def deliver(self, message: ChannelMessage) -> UserInputReply:
        """Route one host reply to its waiting request; never raises into the loop."""
        request_id = message.payload.get("request_id")
        if not isinstance(request_id, str) or not request_id:
            logger.warning("guest user input: reply without a request id ignored")
            return UserInputReply.UNKNOWN
        with self._lock:
            pending = self._pending.get(request_id)
        if pending is None:
            logger.info(
                "guest user input: reply for unknown request %s ignored", request_id
            )
            return UserInputReply.UNKNOWN
        status = message.payload.get("status")
        if status == USER_INPUT_CANCELLED:
            pending.cancelled = True
            pending.event.set()
            return UserInputReply.CANCELLED
        if status == USER_INPUT_SUBMITTED:
            response = message.payload.get("response")
            pending.response = dict(response) if isinstance(response, dict) else {}
            pending.event.set()
            return UserInputReply.SUBMITTED
        logger.warning("guest user input: reply with unknown status %r ignored", status)
        return UserInputReply.UNKNOWN


@dataclass(slots=True)
class _SessionSearchPending:
    event: threading.Event = field(default_factory=threading.Event)
    result: str | None = None


class SessionSearchBridge:
    """Run session recall through the host-owned session and embedding stores."""

    def __init__(self, send: SendFrame, *, timeout_s: float = 90.0) -> None:
        self._send = send
        self._timeout_s = timeout_s
        self._lock = threading.Lock()
        self._pending: dict[str, _SessionSearchPending] = {}

    request_kind = MessageKind.SESSION_SEARCH_REQUEST
    reply_kind = MessageKind.SESSION_SEARCH_REPLY

    def request(self, args: dict[str, object]) -> str:
        request_id = str(uuid.uuid4())
        pending = _SessionSearchPending()
        with self._lock:
            self._pending[request_id] = pending
        try:
            self._send(self.request_kind, {
                "request_id": request_id, "args": _json_object(args)
            })
            if not pending.event.wait(self._timeout_s):
                raise TimeoutError("Timed out waiting for host session search")
        finally:
            with self._lock:
                self._pending.pop(request_id, None)
        return pending.result if pending.result is not None else "{\"success\":false,\"error\":\"empty host reply\"}"

    def deliver(self, message: ChannelMessage) -> bool:
        if message.kind is not self.reply_kind:
            return False
        request_id, result = message.payload.get("request_id"), message.payload.get("result")
        if not isinstance(request_id, str) or not isinstance(result, str):
            return False
        with self._lock:
            pending = self._pending.get(request_id)
        if pending is None:
            return False
        pending.result = result
        pending.event.set()
        return True


class VaultStoreBridge(SessionSearchBridge):
    """Send file references to the host; secret contents never cross this RPC."""
    request_kind = MessageKind.VAULT_STORE_REQUEST
    reply_kind = MessageKind.VAULT_STORE_REPLY


@dataclass(slots=True)
class _MemoryForgetPending:
    event: threading.Event = field(default_factory=threading.Event)
    result: str | None = None


class MemoryForgetBridge:
    """Send an exact memory deletion request to the host-owned memory store."""

    def __init__(self, send: SendFrame, *, timeout_s: float = DEFAULT_USER_INPUT_TIMEOUT_S + REPLY_TIMEOUT_MARGIN_S) -> None:
        self._send = send
        self._timeout_s = timeout_s
        self._lock = threading.Lock()
        self._pending: dict[str, _MemoryForgetPending] = {}

    def request(self, args: dict[str, object]) -> str:
        target = args.get("target")
        if not isinstance(target, str):
            return '{"changed":false,"rejected":true,"reason":"invalid_target"}'
        request_id = str(uuid.uuid4())
        pending = _MemoryForgetPending()
        with self._lock:
            self._pending[request_id] = pending
        try:
            self._send(MessageKind.MEMORY_FORGET_REQUEST, {"request_id": request_id, "target": target})
            if not pending.event.wait(self._timeout_s):
                raise TimeoutError("Timed out waiting for host memory deletion")
        finally:
            with self._lock:
                self._pending.pop(request_id, None)
        return pending.result or '{"changed":false,"rejected":true,"reason":"empty_host_reply"}'

    def deliver(self, message: ChannelMessage) -> bool:
        if message.kind is not MessageKind.MEMORY_FORGET_REPLY:
            return False
        request_id, result = message.payload.get("request_id"), message.payload.get("result")
        if not isinstance(request_id, str) or not isinstance(result, str):
            return False
        with self._lock:
            pending = self._pending.get(request_id)
        if pending is None:
            return False
        pending.result = result
        pending.event.set()
        return True


@dataclass(slots=True)
class _SteerPending:
    event: threading.Event = field(default_factory=threading.Event)
    items: list[dict[str, object]] = field(default_factory=list)


class SteerBridge:
    def __init__(self, send: SendFrame, *, timeout_s: float = 90.0) -> None:
        self._send = send
        self._timeout_s = timeout_s
        self._lock = threading.Lock()
        self._pending: dict[str, _SteerPending] = {}

    def request(self, *, prioritized_only: bool = False) -> list[dict[str, object]]:
        request_id = str(uuid.uuid4())
        pending = _SteerPending()
        with self._lock:
            self._pending[request_id] = pending
        try:
            request: dict[str, JsonValue] = {"request_id": request_id}
            if prioritized_only:
                request["prioritized_only"] = True
            self._send(MessageKind.STEER_REQUEST, request)
            if not pending.event.wait(self._timeout_s):
                raise TimeoutError("Timed out waiting for pending session messages")
            return pending.items
        finally:
            with self._lock:
                self._pending.pop(request_id, None)

    def deliver(self, message: ChannelMessage) -> bool:
        if message.kind is not MessageKind.STEER_REPLY:
            return False
        request_id, items = message.payload.get("request_id"), message.payload.get("items")
        if not isinstance(request_id, str) or not isinstance(items, list):
            return False
        with self._lock:
            pending = self._pending.get(request_id)
        if pending is None:
            return False
        narrowed = narrow_json_value(items)
        if not isinstance(narrowed, list) or not all(isinstance(item, dict) for item in narrowed):
            return False
        pending.items = narrowed
        pending.event.set()
        return True


@dataclass(slots=True)
class _WebToolPending:
    event: threading.Event = field(default_factory=threading.Event)
    result: str | None = None


class WebToolBridge:
    """Expose fixed search and extraction operations through the host."""

    def __init__(self, send: SendFrame, *, timeout_s: float = 90.0) -> None:
        self._send = send
        self._timeout_s = timeout_s
        self._lock = threading.Lock()
        self._pending: dict[str, _WebToolPending] = {}

    def request(self, operation: str, args: dict[str, object]) -> str:
        if operation not in {"search", "extract"}:
            raise ValueError("unsupported host web operation")
        request_id = str(uuid.uuid4())
        pending = _WebToolPending()
        with self._lock:
            self._pending[request_id] = pending
        try:
            self._send(MessageKind.WEB_REQUEST, {
                "request_id": request_id, "operation": operation, "args": _json_object(args)
            })
            if not pending.event.wait(self._timeout_s):
                raise TimeoutError("Timed out waiting for host web tool")
        finally:
            with self._lock:
                self._pending.pop(request_id, None)
        return pending.result if pending.result is not None else "{\"success\":false,\"error\":\"empty host reply\"}"

    def deliver(self, message: ChannelMessage) -> bool:
        if message.kind is not MessageKind.WEB_REPLY:
            return False
        request_id, result = message.payload.get("request_id"), message.payload.get("result")
        if not isinstance(request_id, str) or not isinstance(result, str):
            return False
        with self._lock:
            pending = self._pending.get(request_id)
        if pending is None:
            return False
        pending.result = result
        pending.event.set()
        return True


def _json_object(value: Mapping[str, object]) -> dict[str, JsonValue]:
    result: dict[str, JsonValue] = {}
    for key, item in value.items():
        if isinstance(item, (str, bool, int, float)) or item is None:
            result[str(key)] = item
        elif isinstance(item, list) and all(isinstance(part, str) for part in item):
            result[str(key)] = list(item)
        else:
            raise ValueError("web tool arguments have an unsupported shape")
    return result
