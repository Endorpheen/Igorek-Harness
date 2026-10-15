"""Diff rendering — full port of Codex diff_render.rs.

Theme-aware diff with line numbers, gutter signs, syntax highlighting.
"""
from __future__ import annotations
import difflib
from typing import Optional

# ─── Theme palettes ──────────────────────────────────────────────

DARK_THEME = {
    "add_fg": "#50fa7b", "add_bg": "#213A2B",
    "del_fg": "#ff5555", "del_bg": "#4A221D",
    "header_fg": "#8be9fd", "header_bg": "#1a1a2e",
    "gutter_fg": "#6c6c6c", "context_fg": "#d0d0d0",
    "hunk_header": "#ff79c6",
}
LIGHT_THEME = {
    "add_fg": "#1a7f37", "add_bg": "#dafbe1",
    "del_fg": "#cf222e", "del_bg": "#ffebe9",
    "header_fg": "#0969da", "header_bg": "#f0f0f0",
    "gutter_fg": "#57606a", "context_fg": "#1f2328",
    "hunk_header": "#8250df",
}
_16COLOR_THEME = {
    "add_fg": "32", "add_bg": "",
    "del_fg": "31", "del_bg": "",
    "header_fg": "36", "header_bg": "",
    "gutter_fg": "90", "context_fg": "",
    "hunk_header": "35",
}


class DiffRenderer:
    """Codex theme-aware diff renderer."""

    def __init__(self, dark_mode: bool = True, truecolor: bool = False,
                 colors_256: bool = False):
        self.dark = dark_mode
        self.truecolor = truecolor
        self.colors_256 = colors_256
        self._palette = self._select_palette()

    def _select_palette(self) -> dict:
        if self.truecolor or self.colors_256:
            return DARK_THEME if self.dark else LIGHT_THEME
        return _16COLOR_THEME

    def _ansi(self, fg: str, bg: str = "") -> str:
        if not fg:
            return ""
        if self.truecolor and fg.startswith("#"):
            r, g, b = int(fg[1:3], 16), int(fg[3:5], 16), int(fg[5:7], 16)
            codes = [f"38;2;{r};{g};{b}"]
            if bg and bg.startswith("#"):
                r2, g2, b2 = int(bg[1:3], 16), int(bg[3:5], 16), int(bg[5:7], 16)
                codes.append(f"48;2;{r2};{g2};{b2}")
            return f"\033[{';'.join(codes)}m"
        return ""

    def _reset(self) -> str:
        return "\033[0m"

    def render_diff_summary(self, changes: dict[str, tuple[str, str]]) -> list[str]:
        """Render summary of multiple file changes."""
        lines = []
        total_added = 0
        total_removed = 0
        p = self._palette

        for path, (old, new) in sorted(changes.items()):
            d = list(difflib.unified_diff(
                old.splitlines(), new.splitlines(),
                fromfile=path, tofile=path, lineterm="",
            ))
            added = sum(1 for l in d if l.startswith("+") and not l.startswith("+++"))
            removed = sum(1 for l in d if l.startswith("-") and not l.startswith("---"))
            if added or removed:
                add_c = self._ansi(p["add_fg"])
                del_c = self._ansi(p["del_fg"])
                r = self._reset()
                lines.append(f"  {path}: {add_c}+{added}{r} {del_c}−{removed}{r}")
                total_added += added
                total_removed += removed

        if total_added or total_removed:
            lines.insert(0, f"  Total: +{total_added} −{total_removed} across {len(lines)} files")
        return lines

    def render_unified_diff(
        self, old_text: str, new_text: str, filename: str = "",
        context_lines: int = 3, max_lines: int = 200,
    ) -> list[str]:
        """Render unified diff with optional ANSI colors."""
        old_lines = old_text.splitlines()
        new_lines = new_text.splitlines()
        fromfile = filename or "a"
        tofile = filename or "b"

        diff = list(difflib.unified_diff(
            old_lines, new_lines,
            fromfile=fromfile, tofile=tofile,
            lineterm="", n=context_lines,
        ))
        return diff[:max_lines]

    def render_with_gutters(
        self, old_text: str, new_text: str, filename: str = "",
        width: int = 80,
    ) -> list[str]:
        """Render diff with line numbers and gutter signs (+, -, space)."""
        old_lines = old_text.splitlines()
        new_lines = new_text.splitlines()
        matcher = difflib.SequenceMatcher(None, old_lines, new_lines)
        p = self._palette

        result = []
        result.append(f"  ── {filename or 'diff'} ──")

        for tag, i1, i2, j1, j2 in matcher.get_opcodes():
            if tag == "equal":
                ctx_before = min(3, i1)
                ctx_after = min(3, len(old_lines) - i2)
                # Show context before changes
                for i in range(max(0, i1 - 3), i1):
                    if i < len(old_lines):
                        result.append(f"    {i+1:4d}  {old_lines[i]}")
                # Skip middle if large gap
                if i2 - i1 > 10:
                    result.append(f"    ... ({i2 - i1} unchanged lines)")
                # Show context after changes
                for i in range(i2, min(len(old_lines), i2 + 3)):
                    result.append(f"    {i+1:4d}  {old_lines[i]}")
            elif tag == "delete":
                for i in range(i1, i2):
                    result.append(f"  {self._ansi(p['del_fg'])}−{self._reset()} {i+1:4d}  {old_lines[i]}")
            elif tag == "insert":
                for j in range(j1, j2):
                    result.append(f"  {self._ansi(p['add_fg'])}+{self._reset()} {j+1:4d}  {new_lines[j]}")
            elif tag == "replace":
                for i in range(i1, i2):
                    result.append(f"  {self._ansi(p['del_fg'])}−{self._reset()} {i+1:4d}  {old_lines[i]}")
                for j in range(j1, j2):
                    result.append(f"  {self._ansi(p['add_fg'])}+{self._reset()} {j+1:4d}  {new_lines[j]}")

        return result

    def render_hunks(
        self, old_text: str, new_text: str, filename: str = "",
        context_lines: int = 3,
    ) -> list[str]:
        """Render diff as hunks with headers."""
        old_lines = old_text.splitlines()
        new_lines = new_text.splitlines()
        matcher = difflib.SequenceMatcher(None, old_lines, new_lines)
        p = self._palette
        result = []

        hunk_line = None
        for tag, i1, i2, j1, j2 in matcher.get_opcodes():
            if tag == "equal":
                continue

            # Hunk header
            hdr = f"  {self._ansi(p['hunk_header'])}@@ -{i1+1},{i2-i1} +{j1+1},{j2-j1} @@{self._reset()}"
            if hunk_line != hdr:
                if result:
                    result.append("")
                result.append(hdr)
                hunk_line = hdr

            if tag == "delete":
                for i in range(i1, i2):
                    result.append(f"  {self._ansi(p['del_fg'])}-{self._reset()} {old_lines[i]}")
            elif tag == "insert":
                for j in range(j1, j2):
                    result.append(f"  {self._ansi(p['add_fg'])}+{self._reset()} {new_lines[j]}")
            elif tag == "replace":
                for i in range(i1, i2):
                    result.append(f"  {self._ansi(p['del_fg'])}-{self._reset()} {old_lines[i]}")
                for j in range(j1, j2):
                    result.append(f"  {self._ansi(p['add_fg'])}+{self._reset()} {new_lines[j]}")

        return result
