from __future__ import annotations

import logging
import re
from typing import Any


_TOKEN_COUNT_FIELDS = frozenset({
    "input_tokens", "cached_input_tokens", "output_tokens", "reasoning_output_tokens",
    "total_tokens", "cache_creation_input_tokens", "cache_read_input_tokens",
    "prompt_tokens", "completion_tokens", "max_tokens", "num_tokens",
})


SECRET_PATTERNS = [
    re.compile(
        r"(?i)(?<![A-Za-z0-9_])(api[_-]?key|token|secret|password)"
        r"(?![A-Za-z0-9_])\s*[:=]\s*([^\s,'\"]+)"
    ),
    re.compile(
        r"\b([A-Z][A-Z0-9_]*(?:API_KEY|TOKEN|SECRET|PASSWORD))"
        r"\s*[:=]\s*([^\s,'\"]+)"
    ),
    re.compile(r"\b(sk-[A-Za-z0-9]{12,})\b"),
    re.compile(r"\b(gh[pousr]_[A-Za-z0-9]{12,})\b"),
    re.compile(r"\bya29\.[A-Za-z0-9._-]+\b"),
    re.compile(r"\b\d{6,}:[A-Za-z0-9_-]{20,}\b"),
]


def redact_text(value: str) -> str:
    redacted = value
    for pattern in SECRET_PATTERNS:
        redacted = pattern.sub(_replace_match, redacted)
    return redacted


def _replace_match(match: re.Match[str]) -> str:
    if match.lastindex and match.lastindex > 1:
        groups = list(match.groups())
        groups[-1] = "[REDACTED]"
        return "=".join(groups[:2]) if len(groups) == 2 else "[REDACTED]"
    return "[REDACTED]"


def redact_value(value: Any) -> Any:
    if isinstance(value, dict):
        return redact_mapping(value)
    if isinstance(value, list):
        return [redact_value(item) for item in value]
    if isinstance(value, tuple):
        return tuple(redact_value(item) for item in value)
    if isinstance(value, str):
        return redact_text(value)
    return value


def redact_mapping(payload: dict[str, Any]) -> dict[str, Any]:
    redacted: dict[str, Any] = {}
    for key, value in payload.items():
        if key.lower() in _TOKEN_COUNT_FIELDS and type(value) is int and value >= 0:
            redacted[key] = value
        elif any(token in key.lower() for token in ("key", "token", "secret", "password")):
            redacted[key] = "[REDACTED]"
        else:
            redacted[key] = redact_value(value)
    return redacted


def safe_exception_message(exc: BaseException) -> str:
    return redact_text(str(exc))


def redact_exception(exc: BaseException) -> str:
    return safe_exception_message(exc)


class RedactingFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        rendered = super().format(record)
        return redact_text(rendered)
