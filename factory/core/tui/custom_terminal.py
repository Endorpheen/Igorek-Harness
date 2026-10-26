"""Custom terminal — full port of Codex custom_terminal.rs."""
from __future__ import annotations
import shutil, sys
from dataclasses import dataclass
from typing import Callable

@dataclass
class TerminalSize: lines: int = 24; columns: int = 80

@dataclass
class ViewportArea:
    x: int = 0; y: int = 0; width: int = 0; height: int = 0
    def bottom(self) -> int: return self.y + self.height

class CustomTerminal:
    """Codex CustomTerminal: diff-based viewport rendering.

    Only renders within viewport_area — content above scrolls
    into terminal scrollback.
    """

    def __init__(self):
        sz = shutil.get_terminal_size((120, 24))
        self.size = TerminalSize(sz.lines, sz.columns)
        self.viewport_area = ViewportArea(0, 0, sz.columns, 6)
        self._prev_lines: list[str] = []
        self._needs_clear = True

    def set_viewport_area(self, area: ViewportArea):
        if (area.x != self.viewport_area.x or area.y != self.viewport_area.y or
            area.width != self.viewport_area.width or area.height != self.viewport_area.height):
            self._needs_clear = True
        self.viewport_area = area

    def clear(self):
        vp = self.viewport_area
        for row in range(vp.y, vp.y + vp.height):
            sys.stdout.write(f"\033[{row};{vp.x}H\033[2K")
        sys.stdout.flush()
        self._prev_lines = [""] * vp.height

    def invalidate_viewport(self):
        self._needs_clear = True
        self._prev_lines = []

    def draw(self, draw_fn: Callable):
        """Diff-based render: only write changed lines."""
        full_clear = self._needs_clear
        if full_clear:
            self.clear()
            self._needs_clear = False

        vp = self.viewport_area
        new_lines: list[str] = []

        # Collect rendered lines
        class _Buf:
            def __init__(self): self.lines = []
            def append(self, s): self.lines.append(str(s))
        buf = _Buf()
        draw_fn(vp, buf)
        new_lines = buf.lines

        # Write only changed lines
        for i, line in enumerate(new_lines):
            if i >= vp.height:
                break
            if i >= len(self._prev_lines) or self._prev_lines[i] != line:
                sys.stdout.write(f"\033[{vp.y + i};{vp.x}H\033[2K{line}")

        # Clear leftover rows of a previous, longer frame.
        if not full_clear:
            for i in range(len(new_lines), min(len(self._prev_lines), vp.height)):
                sys.stdout.write(f"\033[{vp.y + i};{vp.x}H\033[2K")
        sys.stdout.flush()
        self._prev_lines = new_lines

    def resize(self) -> bool:
        sz = shutil.get_terminal_size((120, 24))
        if (sz.lines, sz.columns) != (self.size.lines, self.size.columns):
            self.size = TerminalSize(sz.lines, sz.columns)
            self.invalidate_viewport()
            return True
        return False
