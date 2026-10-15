"""Resume/fork session picker — port of Codex resume_picker.rs.

Interactive full-screen picker showing past sessions with search,
sorting, and keyboard navigation. Uses Tui.draw() for rendering,
same pipeline as Codex ratatui.
"""
from __future__ import annotations

import select
import shutil
import sys
import termios
import tty
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from .tui import Tui


class SessionPickerAction(Enum):
    RESUME = "resume"
    FORK = "fork"


class SessionSelection(Enum):
    START_FRESH = "start_fresh"
    RESUME = "resume"
    FORK = "fork"
    EXIT = "exit"


@dataclass
class PickerRow:
    """One session row in the picker."""
    session_id: str
    path: str
    preview: str = ""
    model: str = ""
    profile: str = ""
    cwd: str = ""
    git_branch: str = ""
    message_count: int = 0
    created_at: int = 0
    updated_at: int = 0


class SortKey(Enum):
    UPDATED = "updated"
    CREATED = "created"


@dataclass
class PickerState:
    """Full interactive picker state — mirrors Codex PickerState."""
    action: SessionPickerAction = SessionPickerAction.RESUME
    all_rows: list[PickerRow] = field(default_factory=list)
    filtered_rows: list[PickerRow] = field(default_factory=list)
    selected: int = 0
    scroll_top: int = 0
    query: str = ""
    sort_key: SortKey = SortKey.UPDATED
    inline_error: str = ""
    _width: int = 80
    _height: int = 24

    def set_size(self, width: int, height: int):
        self._width = width
        self._height = height

    def set_rows(self, rows: list[PickerRow]):
        self.all_rows = list(rows)
        self.apply_filter()

    def apply_filter(self):
        q = self.query.lower()
        if not q:
            self.filtered_rows = list(self.all_rows)
        else:
            self.filtered_rows = [
                r for r in self.all_rows
                if q in r.session_id.lower()
                or q in r.preview.lower()
                or q in r.model.lower()
                or q in r.cwd.lower()
                or q in r.git_branch.lower()
            ]
        self._sort_rows()
        # Clamp selection
        if self.filtered_rows:
            self.selected = min(self.selected, len(self.filtered_rows) - 1)
        else:
            self.selected = 0

    def _sort_rows(self):
        if self.sort_key == SortKey.UPDATED:
            self.filtered_rows.sort(key=lambda r: r.updated_at, reverse=True)
        else:
            self.filtered_rows.sort(key=lambda r: r.created_at, reverse=True)

    def set_query(self, new_query: str):
        self.query = new_query
        self.apply_filter()
        self.selected = 0
        self.scroll_top = 0

    def toggle_sort_key(self):
        if self.sort_key == SortKey.UPDATED:
            self.sort_key = SortKey.CREATED
        else:
            self.sort_key = SortKey.UPDATED
        self.apply_filter()

    @property
    def view_height(self) -> int:
        """Available rows for the session list (total - header - search - columns - footer)."""
        return max(1, self._height - 5)

    def ensure_selected_visible(self):
        if self.selected < self.scroll_top:
            self.scroll_top = self.selected
        elif self.selected >= self.scroll_top + self.view_height:
            self.scroll_top = self.selected - self.view_height + 1


def _format_ts(ts: int) -> str:
    """Format unix timestamp to short date-time string."""
    if not ts:
        return ""
    import time
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(ts))


def _truncate(s: str, max_len: int) -> str:
    s = _single_line(s)
    if max_len <= 0:
        return ""
    if len(s) <= max_len:
        return s
    if max_len == 1:
        return "…"
    return s[:max_len - 1] + "…"


def _single_line(s: str) -> str:
    return " ".join(str(s).split())


def _render_picker(state: PickerState) -> list[str]:
    """Render the full picker view into lines for Tui.draw()."""
    lines = []
    w = state._width

    # Line 1: Header
    action_title = "Resume session" if state.action == SessionPickerAction.RESUME else "Fork session"
    sort_label = "updated" if state.sort_key == SortKey.UPDATED else "created"
    lines.append(f"\033[1;36m{action_title}\033[0m  \033[2mSort:\033[0m \033[35m{sort_label}\033[0m")

    # Line 2: Search
    if state.inline_error:
        lines.append(f"\033[31m{state.inline_error}\033[0m")
    elif state.query:
        lines.append(f"Search: {state.query}")
    else:
        lines.append("\033[2mType to search\033[0m")

    # Line 3: Column headers
    col_date = "Date".ljust(12)
    col_msgs = "Msgs".ljust(5)
    col_model = "Model".ljust(20)
    col_preview_start = 12 + 1 + 5 + 1 + 20 + 1  # 40
    lines.append(f"\033[2m{col_date} {col_msgs} {col_model} Preview\033[0m")

    # Lines 4..N-1: Session rows
    rows = state.filtered_rows
    area_h = state.view_height

    if not rows:
        lines.append(f"\033[2m  No sessions found{' matching: ' + state.query if state.query else ''}\033[0m")
        # Pad remaining area
        for _ in range(area_h - 1):
            lines.append("")
    else:
        start = state.scroll_top
        end = min(len(rows), start + area_h)
        for idx in range(start, end):
            row = rows[idx]
            is_selected = (idx == state.selected)
            marker = "\033[1;32m›\033[0m" if is_selected else " "

            date_str = _format_ts(row.updated_at)[:11].ljust(11)
            msgs = str(row.message_count).ljust(4)
            model = _truncate(row.model or "?", 19).ljust(19)
            preview = _truncate(row.preview or row.session_id, max(0, w - 42))

            if is_selected:
                lines.append(f"{marker} \033[7m{date_str} {msgs} {model} {preview}\033[0m")
            else:
                lines.append(f"{marker} {date_str} {msgs} \033[2m{model}\033[0m {preview}")

        # Pad if fewer rows than area
        rendered = end - start
        for _ in range(area_h - rendered):
            lines.append("")

    # Last line: Hints
    action_label = "resume" if state.action == SessionPickerAction.RESUME else "fork"
    lines.append(
        f"\033[2m"
        f"Enter:{action_label}  Esc:new  Ctrl+C:quit  Tab:sort  ↑↓:browse  type:search"
        f"\033[0m"
    )

    return lines


def _drain_stdin_raw(fd: int) -> None:
    """Discard any pending stdin characters without affecting terminal settings."""
    try:
        while select.select([sys.stdin], [], [], 0.0)[0]:
            sys.stdin.read(1)
    except Exception:
        pass


def _clear_area(tui: Tui, line_count: int) -> None:
    """Erase all picker lines from the terminal by moving to each line and clearing it."""
    top = max(1, tui.viewport.y + 1)
    for i in range(line_count):
        sys.stdout.write(f"\033[{top + i};1H\033[2K")
    sys.stdout.write(f"\033[{top};1H")  # Return cursor to top of cleared area
    sys.stdout.flush()


def _enter_alt_screen() -> bool:
    """Enter a temporary full-screen buffer if stdout is a real terminal."""
    if not sys.stdout.isatty():
        return False
    sys.stdout.write("\033[?1049h\033[H\033[2J")
    sys.stdout.flush()
    return True


def _leave_alt_screen(active: bool) -> None:
    """Leave the temporary full-screen buffer."""
    if not active:
        return
    sys.stdout.write("\033[?1049l")
    sys.stdout.flush()


def _read_key_raw() -> str:
    """Read a single keypress from stdin in raw mode.

    Returns key names: 'enter', 'escape', 'up', 'down', 'backspace',
    'tab', 'c-c', 'pageup', 'pagedown', or a single character.
    """
    fd = sys.stdin.fileno()
    old_settings = termios.tcgetattr(fd)
    try:
        tty.setraw(fd)
        ch = sys.stdin.read(1)
        if ch == "\r" or ch == "\n":
            return "enter"
        if ch == "\x1b":
            ch2 = sys.stdin.read(1)
            if ch2 == "[":
                ch3 = sys.stdin.read(1)
                if ch3 == "A":
                    return "up"
                elif ch3 == "B":
                    return "down"
                elif ch3 == "5":
                    sys.stdin.read(1)  # ~
                    return "pageup"
                elif ch3 == "6":
                    sys.stdin.read(1)  # ~
                    return "pagedown"
                elif ch3 == "H":
                    return "home"
                elif ch3 == "F":
                    return "end"
                else:
                    return "escape"
            return "escape"
        if ch == "\x7f" or ch == "\x08":
            return "backspace"
        if ch == "\t":
            return "tab"
        if ch == "\x03":
            return "c-c"
        return ch
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)


def run_resume_picker(
    sessions: list[dict],
    *,
    action: SessionPickerAction = SessionPickerAction.RESUME,
) -> tuple[SessionSelection, dict | None]:
    """Run the interactive session picker.

    Args:
        sessions: Output of list_sessions() — list of session metadata dicts.
        action: Whether this is a resume or fork picker.

    Returns:
        Tuple of (SessionSelection, session_dict_or_None).
        session_dict is the chosen session from the input list, or None
        for START_FRESH/EXIT.
    """
    if not sessions:
        return (SessionSelection.START_FRESH, None)

    tui = Tui()
    state = PickerState(action=action)
    rows = []
    for s in sessions:
        rows.append(PickerRow(
            session_id=s["session_id"],
            path=s.get("path", ""),
            preview=s.get("first_user_message", ""),
            model=s.get("model", ""),
            profile=s.get("profile", ""),
            cwd=s.get("cwd", ""),
            git_branch=s.get("git_branch", ""),
            message_count=s.get("message_count", 0),
            created_at=s.get("created_at", 0),
            updated_at=s.get("updated_at", 0),
        ))
    state.set_rows(rows)

    # Save terminal state
    fd = sys.stdin.fileno()
    old_settings = termios.tcgetattr(fd)
    rendered_count = 0
    alt_screen = False

    # Drain any buffered stdin characters (e.g. from prompt_toolkit)
    _drain_stdin_raw(fd)

    try:
        alt_screen = _enter_alt_screen()
        size = shutil.get_terminal_size((80, 24))
        state.set_size(size.columns, size.lines)

        # First render — clear the area we'll use
        rendered = _render_picker(state)
        rendered_count = len(rendered)
        if not alt_screen:
            _clear_area(tui, rendered_count)
        tui.draw(rendered_count, lambda vp, buf: buf.extend(rendered), force=True)
        sys.stdout.flush()

        while True:
            # Read key
            key = _read_key_raw()

            # Drain any extra buffered characters to prevent rapid re-renders
            _drain_stdin_raw(fd)

            # Re-check terminal size
            size = shutil.get_terminal_size((80, 24))
            state.set_size(size.columns, size.lines)

            if key == "escape":
                return (SessionSelection.START_FRESH, None)
            elif key == "c-c":
                return (SessionSelection.EXIT, None)
            elif key == "enter":
                if state.filtered_rows:
                    row = state.filtered_rows[state.selected]
                    # Find matching session dict from input
                    for s in sessions:
                        if s["session_id"] == row.session_id:
                            sel = (SessionSelection.RESUME if action == SessionPickerAction.RESUME
                                   else SessionSelection.FORK)
                            return (sel, s)
                state.inline_error = "No session selected"
            elif key == "up":
                if state.selected > 0:
                    state.selected -= 1
                    state.ensure_selected_visible()
            elif key == "down":
                if state.selected + 1 < len(state.filtered_rows):
                    state.selected += 1
                    state.ensure_selected_visible()
            elif key == "pageup":
                step = max(1, state.view_height)
                state.selected = max(0, state.selected - step)
                state.ensure_selected_visible()
            elif key == "pagedown":
                step = max(1, state.view_height)
                max_idx = max(0, len(state.filtered_rows) - 1)
                state.selected = min(max_idx, state.selected + step)
                state.ensure_selected_visible()
            elif key == "tab":
                state.toggle_sort_key()
            elif key == "backspace":
                if state.query:
                    state.set_query(state.query[:-1])
            elif len(key) == 1 and key.isprintable():
                state.set_query(state.query + key)

            # Render after processing key
            rendered = _render_picker(state)
            rendered_count = len(rendered)
            tui.draw(rendered_count, lambda vp, buf: buf.extend(rendered), force=True)
            sys.stdout.flush()
    finally:
        # Restore terminal
        termios.tcsetattr(fd, termios.TCSADRAIN, old_settings)
        if alt_screen:
            _leave_alt_screen(alt_screen)
        elif rendered_count:
            # Clear picker lines from terminal: move to top of picker area, clear each line
            _clear_area(tui, rendered_count)
        sys.stdout.flush()
