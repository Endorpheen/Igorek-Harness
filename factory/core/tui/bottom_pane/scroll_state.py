"""Scroll state — port of Codex bottom_pane/scroll_state.rs."""
from __future__ import annotations

class ScrollState:
    def __init__(self, total_items: int = 0, visible_items: int = 10):
        self.selected = 0
        self.offset = 0
        self.total = total_items
        self.visible = visible_items

    def move(self, direction: int) -> None:
        """Move selection by direction (-1 up, +1 down)."""
        if direction < 0:
            self.move_up()
        elif direction > 0:
            self.move_down()

    def move_up(self):
        self.selected = (self.selected - 1) % self.total if self.total > 0 else 0
        self._ensure_visible()

    def move_down(self):
        self.selected = (self.selected + 1) % self.total if self.total > 0 else 0
        self._ensure_visible()

    def _ensure_visible(self):
        if self.selected < self.offset:
            self.offset = self.selected
        elif self.selected >= self.offset + self.visible:
            self.offset = self.selected - self.visible + 1
