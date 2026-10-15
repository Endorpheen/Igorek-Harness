"""Status indicator widget — port of Codex status_indicator_widget.rs."""
from __future__ import annotations
import time
from dataclasses import dataclass
from typing import Optional

_STATUS_DETAILS_DEFAULT_MAX_LINES = 3
_DETAILS_PREFIX = "  └ "
_DIM = "\033[2m"
_YELLOW = "\033[33m"
_GREEN = "\033[32m"
_RESET = "\033[0m"

STATUS_DETAILS_DEFAULT_MAX_LINES = _STATUS_DETAILS_DEFAULT_MAX_LINES


def fmt_elapsed_compact(elapsed_secs: int) -> str:
    """Format: 0s, 59s, 1m 05s, 1h 02m 03s."""
    if elapsed_secs < 60: return f"{elapsed_secs}s"
    if elapsed_secs < 3600:
        m = elapsed_secs // 60; s = elapsed_secs % 60
        return f"{m}m {s:02d}s"
    h = elapsed_secs // 3600
    m = (elapsed_secs % 3600) // 60
    s = elapsed_secs % 60
    return f"{h}h {m:02d}m {s:02d}s"


class StatusIndicatorWidget:
    """Codex status indicator: ● Working (15s · esc to interrupt)."""
    def __init__(self, header: str = "Working"):
        self.header = header
        self.details: Optional[str] = None
        self.details_max_lines = _STATUS_DETAILS_DEFAULT_MAX_LINES
        self.inline_message: Optional[str] = None
        self.show_interrupt_hint = True
        self.t0 = time.monotonic()

    def status_line(self) -> str:
        """Render status line: ● Working (15s · esc to interrupt) [· inline_msg]."""
        elapsed = int(time.monotonic() - self.t0)
        ch = "•"
        ts = fmt_elapsed_compact(elapsed)
        hint = f" {_DIM}· esc to interrupt{_RESET}" if self.show_interrupt_hint else ""
        inline = f" {_DIM}· {self.inline_message}{_RESET}" if self.inline_message else ""
        return f"  {_YELLOW}{ch}{_RESET}  {self.header} ({_DIM}{ts}{hint}{_RESET}){inline}"

    def detail_lines(self) -> list[str]:
        """Render detail lines below status:   └ detail1,     detail2."""
        if not self.details: return []
        lines = self.details.strip().splitlines()
        result = []
        for i, line in enumerate(lines):
            if i == 0:
                result.append(f"{_DETAILS_PREFIX}{_DIM}{line}{_RESET}")
            else:
                result.append(f"    {_DIM}{line}{_RESET}")
        return result[:self.details_max_lines]

    def render(self) -> str:
        """Full status + details as text block."""
        parts = [self.status_line()]
        parts.extend(self.detail_lines())
        return "\n".join(parts)

    def update_header(self, header: str):
        self.header = header

    def update_details(self, details: Optional[str], max_lines: int = _STATUS_DETAILS_DEFAULT_MAX_LINES):
        self.details = details.strip() if details else None
        self.details_max_lines = max(max_lines, 1)

    def update_inline_message(self, message: Optional[str]):
        self.inline_message = message.strip() if message else None
