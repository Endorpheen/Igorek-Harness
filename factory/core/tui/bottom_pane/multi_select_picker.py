"""Multi-select picker — port of Codex bottom_pane/multi_select_picker.rs."""
from __future__ import annotations
from .scroll_state import ScrollState

class MultiSelectItem:
    def __init__(self, key: str, label: str, selected: bool = False):
        self.key = key; self.label = label; self.selected = selected

class MultiSelectPicker:
    def __init__(self, title: str = ""):
        self.title = title
        self.items: list[MultiSelectItem] = []
        self.scroll = ScrollState(0, 10)

    def set_items(self, items: list[MultiSelectItem]):
        self.items = items
        self.scroll.total = len(items)
        self.scroll.selected = 0
        self.scroll.offset = 0

    def toggle_current(self):
        if 0 <= self.scroll.selected < len(self.items):
            self.items[self.scroll.selected].selected = not self.items[self.scroll.selected].selected

    def selected_keys(self) -> list[str]:
        return [i.key for i in self.items if i.selected]

    def render_lines(self) -> list[str]:
        lines = []
        if self.title:
            lines.append(f"  {self.title}")
        for i, item in enumerate(self.items):
            prefix = "►" if i == self.scroll.selected else " "
            check = "[x]" if item.selected else "[ ]"
            lines.append(f"  {prefix} {check} {item.label}")
        return lines
