"""Host-side relay of guest ``user_input_request`` frames (plan todo 10).

The guest has no user-input store; the host has exactly one
(``factory/core/user_input_store.py``). This relay is the only writer of that
store for isolated sessions: one ``user_input_request`` frame becomes one
``create_user_input_request`` row with the frame's envelope as the payload, so
the mobile ``pending_user_input`` payload and the
``POST /tasks/{id}/user-input/{request_id}`` route keep their shape unchanged.

While the person decides, the host holds the guest run open - the guest is
blocked in its tool call, not parked - and then answers on the same channel:

* an answer becomes ``user_input_reply{status:"submitted", response:...}`` and
  the same guest run continues (no VM respawn, no re-dispatch);
* a cancel - the phone's ``cancelled=true``, or the relay timeout - becomes
  ``user_input_reply{status:"cancelled"}`` and the guest ends with
  ``result{status:"canceled"}``, which the manager normalizes into a clean
  terminal ``fail``.

Frame identity is the guest's ``request_id``: a duplicated request frame (a
channel retry) is answered from the remembered reply instead of creating a
second store row, and a form is published exactly once.

Paths are deliberately NOT canonicalized here (unlike the approval relay): no
signature is matched, the question text is display data, and the answer is free
text the person typed - rewriting either would corrupt a value the guest or the
person actually means.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

from ..user_input_store import (
    create_user_input_request,
    get_user_input_request,
    resolve_user_input_request,
    wait_for_user_input_response,
)
from .channel import (
    DISPLAY_ENVELOPE_KEY,
    USER_INPUT_CANCELLED,
    USER_INPUT_SUBMITTED,
    ChannelMessage,
    JsonValue,
)
from .errors import IsolationError
from .guest_user_input import DEFAULT_USER_INPUT_TIMEOUT_S

logger = logging.getLogger(__name__)

__all__ = ["UserInputRelay"]


class UserInputRelay:
    """Per-dispatch user-input boundary: one store row, one reply per request."""

    def __init__(
        self,
        *,
        task_id: str,
        timeout_s: float = DEFAULT_USER_INPUT_TIMEOUT_S,
    ) -> None:
        self._task_id = str(task_id)
        self._timeout_s = timeout_s
        self._replies: dict[str, dict[str, JsonValue]] = {}

    def handle_request(self, message: ChannelMessage) -> dict[str, JsonValue] | None:
        """Publish one request in the existing store and return the reply to send."""
        request = _request_record(message.payload)
        if request is None:
            return None
        guest_request_id, payload = request
        remembered = self._replies.get(guest_request_id)
        if remembered is not None:
            logger.info(
                "isolation user input relay: duplicate request %s re-answered",
                guest_request_id,
            )
            return dict(remembered)
        created = create_user_input_request(task_id=self._task_id, payload=payload)
        request_id = str(created.get("request_id") or "")
        if not request_id:
            raise IsolationError(
                "user_input_store_failed", "created request carries no request_id"
            )
        try:
            response = wait_for_user_input_response(request_id, timeout_s=self._timeout_s)
        except TimeoutError:
            logger.warning(
                "isolation user input relay: request %s timed out; cancelling the form",
                request_id,
            )
            response = _cancel_after_timeout(request_id)
        reply = _reply_payload(guest_request_id, response)
        self._replies[guest_request_id] = reply
        return dict(reply)


def _cancel_after_timeout(request_id: str) -> dict[str, Any] | None:
    """Cancel the form after the wait expired; keep an answer that raced the timeout.

    ``resolve_user_input_request`` wins only while the row is pending, so a
    ``None`` means the person answered in the same instant the wait expired and
    the answer must not be thrown away.
    """
    if resolve_user_input_request(request_id, response=None, cancelled=True) is not None:
        return None
    current = get_user_input_request(request_id)
    answered = current.get("response") if isinstance(current, dict) else None
    return answered if isinstance(answered, dict) else None


def _request_record(
    payload: Mapping[str, JsonValue],
) -> tuple[str, dict[str, Any]] | None:
    """Narrow one request frame into (guest request id, form payload)."""
    request_id = payload.get("request_id")
    if not isinstance(request_id, str) or not request_id.strip():
        logger.warning("isolation user input relay: skipping a request without a request id")
        return None
    record = payload.get(DISPLAY_ENVELOPE_KEY)
    if not isinstance(record, dict):
        logger.warning(
            "isolation user input relay: skipping a request without a %r envelope",
            DISPLAY_ENVELOPE_KEY,
        )
        return None
    return request_id, dict(record)


def _reply_payload(
    guest_request_id: str, response: dict[str, Any] | None
) -> dict[str, JsonValue]:
    if response is None:
        return {
            "request_id": guest_request_id,
            "status": USER_INPUT_CANCELLED,
            "response": None,
        }
    return {
        "request_id": guest_request_id,
        "status": USER_INPUT_SUBMITTED,
        "response": _json_value(response),
    }


def _json_value(value: Any) -> JsonValue:  # json.loads product, narrowed here
    match value:
        case str() | bool() | int() | float() | None:
            return value
        case list():
            return [_json_value(item) for item in value]
        case dict():
            return {str(key): _json_value(item) for key, item in value.items()}
        case _ as unreachable:
            raise IsolationError(
                "user_input_not_serializable", type(unreachable).__name__
            )
