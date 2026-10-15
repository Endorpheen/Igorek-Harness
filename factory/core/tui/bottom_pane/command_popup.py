"""Command popup (/) — port of Codex bottom_pane/command_popup.rs.

Filtering: first non-whitespace token after '/' determines the filter.
Exact matches appear first, then prefix matches.
Aliases hidden by default (quit/approvals), debug hidden always.
"""
from __future__ import annotations
from dataclasses import dataclass
from .scroll_state import ScrollState

MAX_POPUP_ROWS = 8

_COMMANDS: list[tuple[str, str]] = [
    ("/help", "Show all commands and key bindings"),
    ("/exit", "Exit the session"),
    ("/clear", "Clear session history"),
    ("/history", "Last 20 messages"),
    ("/resume", "Resume a previous session"),
    ("/fork", "Fork a previous session"),
    ("/status", "Session status, messages, profile, model"),
    ("/model", "Show or change model"),
    ("/profile", "Show or change profile"),
    ("/provider", "Show or change provider"),
    ("/models", "Recommended models and session picker"),
    ("/statusline", "Show status line settings"),
    ("/title", "Configure terminal title"),
    ("/theme", "Select markdown and diff theme"),
    ("/skills", "Manage local skills"),
    ("/remember", "Explicitly save a durable memory fact"),
    ("/smarthelp", "Quick answer via OpenRouter/Qwen"),
    ("/tui", "Режим TUI: fullscreen / classic"),
    ("/mouse", "Мышь: on / off / status"),
    ("/debug", "Toggle debug logging"),
]

# Commands to hide when filter is empty
_ALIAS_COMMANDS = {"/quit", "/approvals"}


@dataclass
class CommandMatch:
    command: str
    description: str
    highlight_start: int = 0  # char offset where highlight begins


class CommandPopup:
    """Slash command popup with filtering and selection."""

    def __init__(self):
        self.commands: list[tuple[str, str]] = list(_COMMANDS)
        self.filter_text = ""  # token after /
        self.scroll = ScrollState(len(self.commands), MAX_POPUP_ROWS)
        self._filtered_cache: list[CommandMatch] = []

    def on_composer_text_change(self, text: str):
        """Codex line 78: update filter from first line text starting with /."""
        first_line = text.splitlines()[0] if text else ""
        if first_line.startswith("/"):
            stripped = first_line[1:].lstrip()
            token = stripped.split()[0] if stripped else ""
            self.filter_text = token
        else:
            self.filter_text = ""
        self._recompute_filtered()

    def _recompute_filtered(self):
        """Codex line 117: filter commands by exact/prefix matching."""
        query = self.filter_text.strip().lower()
        if not query:
            self._filtered_cache = [
                CommandMatch(cmd, desc)
                for cmd, desc in self.commands
                if cmd not in _ALIAS_COMMANDS
            ]
        else:
            exact: list[CommandMatch] = []
            prefix: list[CommandMatch] = []
            for cmd, desc in self.commands:
                name = cmd[1:]  # strip leading /
                lower_cmd = cmd.lower()
                lower_name = name.lower()
                if lower_name == query:
                    exact.append(CommandMatch(cmd, desc, 0))
                elif lower_name.startswith(query) or lower_cmd.startswith("/" + query):
                    prefix.append(CommandMatch(cmd, desc, 0))
            self._filtered_cache = exact + prefix

        self.scroll.total = max(len(self._filtered_cache), 1)
        self.scroll.selected = 0
        self.scroll.offset = 0

    def filtered_items(self) -> list[CommandMatch]:
        return self._filtered_cache

    def selected_item(self) -> CommandMatch | None:
        if not self._filtered_cache:
            return None
        idx = self.scroll.selected
        if 0 <= idx < len(self._filtered_cache):
            return self._filtered_cache[idx]
        return None

    def move_up(self):
        """Codex line 197: move selection up with wrap."""
        total = len(self._filtered_cache)
        if total == 0:
            return
        self.scroll.selected = (self.scroll.selected - 1) % total
        self.scroll.offset = max(0, self.scroll.selected - self.scroll.visible + 1)

    def move_down(self):
        """Codex line 203: move selection down with wrap."""
        total = len(self._filtered_cache)
        if total == 0:
            return
        self.scroll.selected = (self.scroll.selected + 1) % total
        self.scroll.offset = max(0, self.scroll.selected - self.scroll.visible + 1)

    def desired_height(self) -> int:
        return min(len(self._filtered_cache), MAX_POPUP_ROWS) + 1

    def render_lines(self, width: int = 80) -> list[str]:
        """Render popup lines for display."""
        items = self._filtered_cache
        if not items:
            return ["  no matches"]
        visible = min(len(items), MAX_POPUP_ROWS)
        start = self.scroll.offset
        lines: list[str] = []
        for i in range(start, min(start + visible, len(items))):
            item = items[i]
            selected = i == self.scroll.selected
            marker = "►" if selected else " "
            cmd_part = item.command
            desc_part = item.description
            # Truncate description to fit
            avail = width - 4 - len(cmd_part) - 3
            if avail > 5 and desc_part:
                desc_short = desc_part[:avail] if len(desc_part) > avail else desc_part
                line = f"  {marker} {cmd_part}  {desc_short}"
            else:
                line = f"  {marker} {cmd_part}"
            lines.append(line)
        return lines
