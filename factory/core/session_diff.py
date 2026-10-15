"""Structured unified-diff hunks for the mobile changed-file contract.

``build_diff_hunks`` turns two texts into the frozen ``hunks`` payload the
Android app renders, reusing the TUI ``DiffRenderer`` (difflib) so the counting
and rendering paths stay in sync with ``session_changes``.
"""

from __future__ import annotations

import re
from typing import Any

MAX_DIFF_LINES = 2000
MAX_DIFF_FILE_BYTES = 2_000_000
_DIFF_SCAN_LINES = 200_000
_HUNK_HEADER_RE = re.compile(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


def build_diff_hunks(
    old_text: str,
    new_text: str,
    *,
    context_lines: int = 3,
    max_lines: int = MAX_DIFF_LINES,
) -> tuple[list[dict[str, Any]], bool]:
    """Return ``(hunks, truncated)`` for a unified diff of two texts.

    Each hunk is ``{"header": "@@ -a,b +c,d @@", "lines": [{"type", "text"}]}``;
    ``truncated`` is True when the body exceeded ``max_lines`` and was clipped.
    """
    from .tui.diff_render import DiffRenderer

    rendered = DiffRenderer().render_unified_diff(
        old_text or "",
        new_text or "",
        context_lines=context_lines,
        max_lines=_DIFF_SCAN_LINES,
    )
    hunks: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    total = 0
    for line in rendered:
        if current is None and (line.startswith("--- ") or line.startswith("+++ ")):
            continue
        if line.startswith("@@"):
            current = {"header": _format_hunk_header(line), "lines": []}
            hunks.append(current)
            continue
        if current is None:
            continue
        if line.startswith("+"):
            entry = {"type": "+", "text": line[1:]}
        elif line.startswith("-"):
            entry = {"type": "-", "text": line[1:]}
        elif line.startswith(" ") or line == "":
            entry = {"type": " ", "text": line[1:]}
        else:
            continue
        current["lines"].append(entry)
        total += 1

    if total <= max_lines:
        return hunks, False

    remaining = max_lines
    limited: list[dict[str, Any]] = []
    for hunk in hunks:
        if remaining <= 0:
            break
        lines = hunk["lines"]
        if len(lines) <= remaining:
            limited.append(hunk)
            remaining -= len(lines)
        else:
            limited.append({"header": hunk["header"], "lines": lines[:remaining]})
            remaining = 0
    return limited, True


def _format_hunk_header(header: str) -> str:
    match = _HUNK_HEADER_RE.match(header)
    if match is None:
        return header
    old_start, old_len, new_start, new_len = match.groups()
    return f"@@ -{int(old_start)},{int(old_len or 1)} +{int(new_start)},{int(new_len or 1)} @@"
