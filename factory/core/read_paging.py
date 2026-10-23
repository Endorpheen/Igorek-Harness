"""Bounded text pages with lossless line/column continuation."""
from __future__ import annotations

import json


_ENDINGS = ("\n", "\r", "\v", "\f", "\x1c", "\x1d", "\x1e", "\x85", "\u2028", "\u2029")


def read_budget(context_window_tokens=None, maximum=None):
    window = context_window_tokens if type(context_window_tokens) is int and context_window_tokens > 0 else None
    cap = 64_000 if window and window >= 200_000 else 32_000 if window and window >= 100_000 else 16_000
    if window:
        cap = min(cap, max(512, window * 3 // 5))
    if type(maximum) is int and maximum > 0:
        cap = min(cap, max(512, maximum))
    return cap


def _segments(text):
    while chunk := text.read(8192):
        yield from chunk.splitlines(keepends=True)


def _position(offset, cursor, content):
    for segment in content.splitlines(keepends=True):
        if segment.endswith(_ENDINGS):
            offset, cursor = offset + 1, 0
        else:
            cursor += len(segment)
    return offset, cursor


def read_text_page(text, *, offset=0, limit=None, cursor=0, max_chars=16_000):
    if type(cursor) is not int or cursor < 0:
        raise ValueError("Read cursor must be a nonnegative character offset within the requested line")
    if limit is not None and limit <= 0:
        return ""
    if limit is not None:
        limit = min(limit, 2**63 - 1)
    line = column = count = 0
    parts = []
    for segment in _segments(text):
        ends = segment.endswith(_ENDINGS)
        if line < offset:
            if ends:
                line, column = line + 1, 0
            else:
                column += len(segment)
            continue
        if limit is not None and line >= offset + limit:
            break
        skip = max(0, cursor - column) if line == offset else 0
        if ends and skip > len(segment):
            raise ValueError("Read cursor is beyond the requested line")
        selected = segment[min(skip, len(segment)):]
        selected = selected[:max_chars + 1 - count]
        parts.append(selected)
        count += len(selected)
        if count > max_chars:
            break
        if ends:
            line, column = line + 1, 0
        else:
            column += len(segment)
    else:
        if line == offset and cursor > column:
            raise ValueError("Read cursor is beyond the requested line")
    content = "".join(parts)
    if len(content) <= max_chars:
        return content
    content = content[:max_chars - 192]
    next_line, next_cursor = _position(offset, cursor, content)
    continuation = {"offset": next_line, "cursor": next_cursor}
    if limit is not None:
        continuation["limit"] = max(1, offset + limit - next_line)
    return content + "\n\n[Read page truncated; continue on the same file with " + json.dumps(continuation) + "]"
