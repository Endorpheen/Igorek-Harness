"""Terminal-title output helpers (port of codex-rs/tui/src/terminal_title.rs).

The Python port writes OSC 0 directly via :func:`sys.stdout.write`. The
test module from the source is **not** ported per the project's
"no tests" policy.
"""
from __future__ import annotations

import sys
from enum import Enum
from typing import Optional


MAX_TERMINAL_TITLE_CHARS: int = 240


class SetTerminalTitleResult(Enum):
    Applied = "applied"
    NoVisibleContent = "no_visible_content"


def _stdout_is_tty() -> bool:
    try:
        return sys.stdout.isatty()
    except Exception:
        return False


def set_terminal_title(title: str) -> SetTerminalTitleResult:
    """Write a sanitized OSC window-title sequence (port one-to-one)."""
    if not _stdout_is_tty():
        return SetTerminalTitleResult.Applied
    sanitized = sanitize_terminal_title(title)
    if not sanitized:
        return SetTerminalTitleResult.NoVisibleContent
    sys.stdout.write(f"\x1b]0;{sanitized}\x07")
    sys.stdout.flush()
    return SetTerminalTitleResult.Applied


def clear_terminal_title() -> None:
    """Clear the current terminal title (port one-to-one)."""
    if not _stdout_is_tty():
        return
    sys.stdout.write("\x1b]0;\x07")
    sys.stdout.flush()


def write_set_window_title_ansi(out, title: str) -> None:
    """Write the OSC 0 sequence with BEL terminator (port of ``SetWindowTitle``).

    Mirrors the ``crossterm::Command`` impl; matches the source line:
    ``\x1b]0;{title}\x07``.
    """
    out.write(f"\x1b]0;{title}\x07")


def sanitize_terminal_title(title: str) -> str:
    """Normalize untrusted title text into a single bounded display line.

    Port of ``codex::terminal_title::sanitize_terminal_title``.

    * drops control characters and a curated set of invisible/bidi
      formatting codepoints (Trojan-Source family),
    * collapses any whitespace run into a single ASCII space,
    * truncates after ``MAX_TERMINAL_TITLE_CHARS`` characters.
    """
    sanitized: list = []
    chars_written = 0
    pending_space = False
    for ch in title:
        if ch.isspace():
            pending_space = bool(sanitized)
            continue
        if is_disallowed_terminal_title_char(ch):
            continue
        if pending_space:
            remaining = MAX_TERMINAL_TITLE_CHARS - chars_written
            if remaining > 1:
                sanitized.append(" ")
                chars_written += 1
                pending_space = False
        if chars_written >= MAX_TERMINAL_TITLE_CHARS:
            break
        sanitized.append(ch)
        chars_written += 1
    return "".join(sanitized)


def is_disallowed_terminal_title_char(ch: str) -> bool:
    """Return whether ``ch`` should be dropped from terminal-title output."""
    if not ch:
        return False
    if ch.isprintable() is False and not ch.isspace():
        return True
    cp = ord(ch)
    if cp == 0x00AD or cp == 0x034F or cp == 0x061C or cp == 0x180E:
        return True
    if 0x200B <= cp <= 0x200F:
        return True
    if 0x202A <= cp <= 0x202E:
        return True
    if 0x2060 <= cp <= 0x206F:
        return True
    if 0xFE00 <= cp <= 0xFE0F:
        return True
    if cp == 0xFEFF:
        return True
    if 0xFFF9 <= cp <= 0xFFFB:
        return True
    if 0x1BCA0 <= cp <= 0x1BCA3:
        return True
    if 0xE0100 <= cp <= 0xE01EF:
        return True
    return False


__all__ = [
    "MAX_TERMINAL_TITLE_CHARS",
    "SetTerminalTitleResult",
    "clear_terminal_title",
    "is_disallowed_terminal_title_char",
    "sanitize_terminal_title",
    "set_terminal_title",
    "write_set_window_title_ansi",
]
