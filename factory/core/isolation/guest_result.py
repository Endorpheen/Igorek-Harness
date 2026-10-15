"""Result-frame shaping for the in-guest runner (plan todo 7).

An executor result is untrusted input for the wire: only the statuses the task
table accepts (:data:`RESULT_STATUSES`, the ``worker.update_task`` vocabulary)
are forwarded, and anything else becomes a ``fail`` with
``failure_reason="invalid_executor_result"`` — so a misleading executor status
can never reach the host as ``ok``.

:data:`FALLBACK_KEYS` lists result values the host payload policy may refuse
(e.g. a blocked ``Bash`` tool carries a raw shell string under a denied key).
The runner drops them one by one, keeping the status visible to the host instead
of letting the whole result be rejected and the host time out.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, Final

from .channel import JsonValue
from .guest_task import narrow_int_or_zero, narrow_json_object, narrow_json_value

__all__ = ["FALLBACK_KEYS", "RESULT_STATUSES", "build_result", "failed_result"]

RESULT_STATUSES: Final[frozenset[str]] = frozenset(
    {"ok", "fail", "retry", "waiting_approval"}
)
FALLBACK_KEYS: Final[tuple[str, ...]] = ("approval_blocked_tools", "failure_details")


def build_result(outcome: Mapping[str, Any]) -> dict[str, JsonValue]:
    """Validate one executor outcome into the wire result; never fabricate success."""
    error = outcome.get("error")
    if error is not None:
        return failed_result("executor_error", f"{type(error).__name__}: {error}")
    value = outcome.get("value")
    if not isinstance(value, dict):
        return failed_result("invalid_executor_result", "executor returned no result mapping")
    status = value.get("status")
    output_text = value.get("output_text")
    usage = value.get("usage")
    if (
        not isinstance(status, str)
        or status not in RESULT_STATUSES
        or not isinstance(output_text, str)
        or not isinstance(usage, dict)
    ):
        return failed_result(
            "invalid_executor_result",
            f"status={status!r} output_text={type(output_text).__name__} "
            f"usage={type(usage).__name__}",
        )
    result: dict[str, JsonValue] = {
        "status": status,
        "output_text": output_text,
        "usage": narrow_json_object(usage),
        "tool_calls_count": narrow_int_or_zero(value.get("tool_calls_count")),
    }
    for key in ("failure_reason", "failure_details"):
        text = value.get(key)
        if isinstance(text, str) and text:
            result[key] = text
    blocked = value.get("approval_blocked_tools")
    if isinstance(blocked, list):
        result["approval_blocked_tools"] = narrow_json_value(blocked)
    return result


def failed_result(reason: str, details: str) -> dict[str, JsonValue]:
    """The documented fail shape the host normalizes (``manager._normalize_result``)."""
    return {
        "status": "fail",
        "failure_reason": reason,
        "failure_details": details,
        "output_text": "",
        "usage": {},
        "tool_calls_count": 0,
    }
