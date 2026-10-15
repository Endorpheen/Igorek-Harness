"""BottomPaneView trait — port of Codex bottom_pane/bottom_pane_view.rs."""
from __future__ import annotations
from abc import ABC, abstractmethod

class BottomPaneView(ABC):
    @abstractmethod
    def render(self, area, buf): ...
    def handle_key_event(self, key: str): pass
    def is_complete(self) -> bool: return False
