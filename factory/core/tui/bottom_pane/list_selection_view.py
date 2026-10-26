"""List selection view — port of Codex bottom_pane/list_selection_view.rs."""
from __future__ import annotations
from .scroll_state import ScrollState

class SelectionItem:
    def __init__(self, name: str, description: str = "", selected: bool = False):
        self.name = name; self.description = description; self.selected = selected

class ListSelectionView:
    def __init__(self, title: str = ""):
        self.title = title
        self.items: list[SelectionItem] = []
        self.scroll = ScrollState(0, 8)
        self.width = 80

    def set_items(self, items: list[SelectionItem]):
        self.items = items
        self.scroll.total = len(items)
        self.scroll.selected = 0
        self.scroll.offset = 0

    def selected_item(self) -> SelectionItem | None:
        if 0 <= self.scroll.selected < len(self.items):
            return self.items[self.scroll.selected]
        return None

    def render_lines(self) -> list[str]:
        lines = []
        if self.title:
            lines.append(f"  {self.title}")
        for i, item in enumerate(self.items):
            prefix = "►" if i == self.scroll.selected else " "
            desc = f" — {item.description}" if item.description else ""
            mark = "●" if item.selected else " "
            lines.append(f"  {prefix} {mark} {item.name}{desc}")
        return lines
