"""Incremental separation of tagged reasoning from visible provider text."""
from __future__ import annotations

import re


_OPEN = re.compile(r'<think\b[^>]*>', re.IGNORECASE)
_CLOSE = re.compile(r'</think\s*>', re.IGNORECASE)


class TaggedReasoningSplitter:
    """Keep partial tags buffered and Markdown code examples visible."""

    def __init__(self):
        self._pending = ''
        self._reasoning = False
        self._code: tuple[str, int] | None = None
        self._escaped = False

    def feed(self, text: str, *, final: bool = False) -> list[tuple[str, str]]:
        buffer = self._pending + text
        output: list[tuple[str, list[str]]] = []
        cursor = 0

        def emit(value: str):
            kind = '__reasoning' if self._reasoning else 'text'
            if output and output[-1][0] == kind:
                output[-1][1].append(value)
            else:
                output.append((kind, [value]))

        while cursor < len(buffer):
            char = buffer[cursor]
            if not self._reasoning and self._escaped:
                emit(char);self._escaped = False;cursor += 1;continue
            if not self._reasoning and not self._code and char == '\\':
                emit(char);self._escaped = True;cursor += 1;continue
            if not self._reasoning and char in '`~':
                end = cursor + 1
                while end < len(buffer) and buffer[end] == char:
                    end += 1
                if end == len(buffer) and not final:
                    break
                length = end - cursor
                if self._code is None:
                    if char == '`' or length >= 3:
                        self._code = (char, length)
                elif self._code[0] == char:
                    required = self._code[1]
                    if length == required or (required >= 3 and length >= required):
                        self._code = None
                emit(buffer[cursor:end]);cursor = end;continue
            if char == '<' and self._code is None:
                remaining = buffer[cursor:cursor + 257]
                close = _CLOSE.match(remaining)
                opening = _OPEN.match(remaining)
                if close:
                    if self._reasoning:
                        self._reasoning = False
                    else:
                        emit(close.group())
                    cursor += close.end();continue
                if opening:
                    tag = opening.group()
                    if tag.rstrip().endswith('/>'):
                        cursor += opening.end();continue
                    if not self._reasoning:
                        self._reasoning = True
                    else:
                        emit(tag)
                    cursor += opening.end();continue
                lower = remaining.lower()
                possible = any(tag.startswith(lower) for tag in ('<think', '</think'))
                if lower.startswith('<think') and len(lower) > 6:
                    possible = lower[6].isspace() or lower[6] == '/'
                elif lower.startswith('</think') and len(lower) > 7:
                    possible = lower[7].isspace()
                if possible and not final and len(remaining) <= 256:
                    break
            emit(char);cursor += 1
        self._pending = buffer[cursor:]
        return [(kind, "".join(parts)) for kind, parts in output]

    def finish(self) -> list[tuple[str, str]]:
        return self.feed('', final=True)
