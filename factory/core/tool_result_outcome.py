"""Execution outcomes independent of natural-language output and content size."""
from __future__ import annotations

import json


class ToolResultText(str):
    """Keep the existing text wire contract with an in-process execution receipt."""

    def __new__(cls, text: str, succeeded: bool):
        value = super().__new__(cls, text)
        value.succeeded = succeeded
        return value

    def __getnewargs__(self):
        return str(self), self.succeeded


def tool_result_succeeded(text: str, *, tool_name: str = "") -> bool | None:
    """Read explicit outcome fields; opaque content has no inferred outcome.

    File/search/shell contents are data, not result envelopes. Their producers
    can attach a ToolResultText receipt when an execution status is available.
    Error fields take precedence over positive fields in structured envelopes.
    """
    if isinstance(text, ToolResultText):
        return text.succeeded
    if tool_name in {"Read", "Grep", "Glob", "Bash"}:
        return None
    try:
        payload = json.loads(text)
    except (ValueError, TypeError):
        return None
    if not isinstance(payload, dict):
        return None
    detail = payload.get("detail")
    if payload.get("error") or (isinstance(detail, dict) and detail.get("error")):
        return False
    if any(payload.get(key) is True for key in ("is_error", "isError", "rejected", "cancelled")):
        return False
    flags = [payload[key] for key in ("success", "ok", "performed") if type(payload.get(key)) is bool]
    codes = [payload[key] for key in ("exit_code", "returncode") if type(payload.get(key)) is int]
    status = payload.get("status")
    if False in flags or any(code != 0 for code in codes) or status in ("error", "failed", "failure", "cancelled", "denied", "blocked"):
        return False
    if status in ("pending", "queued", "running", "in_progress", "waiting_approval"):
        return None
    if True in flags or codes or status in ("ok", "success", "completed", "succeeded", "created"):
        return True
    return None
