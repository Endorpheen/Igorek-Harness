"""File search popup (@) — port of Codex bottom_pane/file_search_popup.rs.

Uses `rg --files` for fast repo file listing, falls back to glob.
"""
from __future__ import annotations
from pathlib import Path
import glob, subprocess, os
from .scroll_state import ScrollState

_MAX_RESULTS = 50
_POPUP_HEIGHT = 8


def _list_repo_files(cwd: Path) -> list[str]:
    """List all tracked files in the repository."""
    try:
        r = subprocess.run(
            ["rg", "--files", "--hidden", "--glob", "!.git"],
            cwd=str(cwd), capture_output=True, text=True, timeout=5,
        )
        if r.returncode == 0 and r.stdout.strip():
            return sorted(r.stdout.strip().splitlines())
    except Exception:
        pass
    # Fallback: simple glob
    try:
        matches = glob.glob("**/*", root_dir=str(cwd), recursive=True)
        return sorted([m for m in matches[:200] if not m.startswith(".git/") and (cwd / m).is_file()])
    except Exception:
        pass
    return []


class FileSearchPopup:
    def __init__(self, cwd: str = "."):
        self.cwd = Path(cwd).resolve()
        self.query = ""
        self.results: list[str] = []
        self.scroll = ScrollState(0, _POPUP_HEIGHT)
        self._all_files: list[str] | None = None

    def on_composer_text_change(self, text: str):
        """Codex: filter files by token after @."""
        first_line = text.splitlines()[0] if text else ""
        if first_line.startswith("@"):
            token = first_line[1:].strip().lower()
            self.query = token
        else:
            self.query = ""
            self.results = []
            self.scroll.total = 0
            return

        # Lazy load all files
        if self._all_files is None:
            self._all_files = _list_repo_files(self.cwd)

        if not self.query:
            self.results = self._all_files[:_MAX_RESULTS]
        else:
            self.results = [
                f for f in self._all_files
                if self.query in f.lower()
            ][:_MAX_RESULTS]

        self.scroll.total = max(len(self.results), 1)
        self.scroll.selected = 0
        self.scroll.offset = 0

    def selected_file(self) -> str | None:
        if 0 <= self.scroll.selected < len(self.results):
            return self.results[self.scroll.selected]
        return None

    def move_up(self):
        total = len(self.results)
        if total:
            self.scroll.selected = (self.scroll.selected - 1) % total
            self.scroll.offset = max(0, self.scroll.selected - _POPUP_HEIGHT + 1)

    def move_down(self):
        total = len(self.results)
        if total:
            self.scroll.selected = (self.scroll.selected + 1) % total
            self.scroll.offset = max(0, self.scroll.selected - _POPUP_HEIGHT + 1)

    def desired_height(self) -> int:
        return min(len(self.results), _POPUP_HEIGHT) + 1

    def render_lines(self, width: int = 80) -> list[str]:
        start = self.scroll.offset
        items = self.results[start:start + _POPUP_HEIGHT]
        if not items:
            if self._all_files is None:
                return ["  scanning files..."]
            return ["  no matches"]
        lines: list[str] = []
        for i, path in enumerate(items, start=start):
            marker = "►" if i == self.scroll.selected else " "
            disp = path if len(path) <= width - 6 else "…" + path[-(width - 7):]
            lines.append(f"  {marker} {disp}")
        return lines
