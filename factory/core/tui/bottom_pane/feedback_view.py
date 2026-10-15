"""Feedback view — port of Codex bottom_pane/feedback_view.rs."""
from __future__ import annotations

class FeedbackView:
    def __init__(self):
        self.visible = False
        self.options = ["Good response", "Bad response", "Bug", "Feature request"]

    def toggle(self):
        self.visible = not self.visible

    def render_lines(self, width: int = 80) -> list[str]:
        if not self.visible:
            return []
        lines = ["  ╭─ Feedback ─" + "─" * (width - 15) + "╮"]
        for opt in self.options:
            lines.append(f"  │  {opt}" + " " * (width - 6 - len(opt)) + "│")
        lines.append("  ╰" + "─" * (width - 4) + "╯")
        return lines
