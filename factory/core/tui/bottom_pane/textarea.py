"""Custom text area widget — port of Codex bottom_pane/textarea.rs."""
from __future__ import annotations

class TextArea:
    def __init__(self):
        self.text = ""
        self.cursor = 0
        self.width = 80

    def insert(self, ch: str):
        self.text = self.text[:self.cursor] + ch + self.text[self.cursor:]
        self.cursor += 1

    def delete_before(self):
        if self.cursor > 0:
            self.text = self.text[:self.cursor - 1] + self.text[self.cursor:]
            self.cursor -= 1

    def move_left(self):
        if self.cursor > 0: self.cursor -= 1

    def move_right(self):
        if self.cursor < len(self.text): self.cursor += 1

    def clear(self):
        self.text = ""; self.cursor = 0
