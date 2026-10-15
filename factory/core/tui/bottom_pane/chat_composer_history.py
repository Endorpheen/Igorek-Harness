"""Chat composer history — port of Codex bottom_pane/chat_composer_history.rs.

Shell-style Up/Down navigation: persistent cross-session + local in-session history.
"""
from __future__ import annotations
from typing import Optional

class ChatComposerHistory:
    """Merges persistent cross-session history (text-only) +
    local in-session history (full text + attachments)."""

    def __init__(self):
        self.persistent: list[str] = []
        self.local: list[dict] = []  # full entries with text + elements
        self._index: int = -1
        self._last_recalled: Optional[str] = None

    def add_local(self, entry: dict):
        """Add a full in-session history entry."""
        text = entry.get("text", "")
        # Duplicate suppression: skip if same as previous
        if self.local and self.local[-1].get("text") == text:
            return
        self.local.append(entry)
        self._reset_navigation()

    def add_persistent(self, text: str):
        if self.persistent and self.persistent[-1] == text:
            return
        self.persistent.append(text)

    def recall_previous(self, current_text: str) -> Optional[str]:
        """Recall next older entry. Returns None if no more history."""
        combined = self._combined_texts()
        if not combined:
            return None

        if self._index < 0:
            self._index = len(combined) - 1
            # Save current text as recall point (even if empty)
            self._last_recalled = current_text
        elif self._index > 0:
            self._index -= 1

        if 0 <= self._index < len(combined):
            return combined[self._index]
        return None

    def recall_next(self) -> Optional[str]:
        """Recall next newer entry."""
        combined = self._combined_texts()
        if self._index < 0:
            return None
        self._index += 1
        if self._index >= len(combined):
            self._index = -1
            return self._last_recalled  # restore original text
        if 0 <= self._index < len(combined):
            return combined[self._index]
        return None

    def _combined_texts(self) -> list[str]:
        local_texts = [e.get("text", "") for e in self.local]
        return self.persistent + local_texts

    def _reset_navigation(self):
        self._index = -1
        self._last_recalled = None

    def can_navigate(self, current_text: str, cursor_pos: int) -> bool:
        """Codex cursor gating: only navigate when cursor at boundary."""
        return cursor_pos in (0, len(current_text))
