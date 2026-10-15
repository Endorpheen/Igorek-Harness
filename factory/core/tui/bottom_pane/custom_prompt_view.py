"""Custom prompt view — port of Codex bottom_pane/custom_prompt_view.rs."""
from __future__ import annotations

class CustomPromptView:
    def __init__(self):
        self.visible = False
        self.prompt_text = ""
        self.default_value = ""

    def show(self, prompt: str, default: str = ""):
        self.visible = True
        self.prompt_text = prompt
        self.default_value = default

    def hide(self):
        self.visible = False

    def render_lines(self, width: int = 80) -> list[str]:
        if not self.visible:
            return []
        return [
            "  " + "─" * (width - 4),
            f"  {self.prompt_text}",
            f"  > {self.default_value}",
            "  " + "─" * (width - 4),
        ]
