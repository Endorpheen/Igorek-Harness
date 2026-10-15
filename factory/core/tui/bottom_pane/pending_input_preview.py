"""Pending input preview — port of Codex bottom_pane/pending_input_preview.rs."""
from __future__ import annotations

class PendingInputPreview:
    def __init__(self):
        self.queued_messages: list[str] = []
        self.pending_steers: list[str] = []
        self.rejected_steers: list[str] = []

    def add_queued(self, text: str):
        self.queued_messages.append(text[:200])

    def add_pending_steer(self, text: str):
        self.pending_steers.append(text[:200])

    def clear_queued(self):
        self.queued_messages = []

    def clear_pending_steers(self):
        self.pending_steers = []

    def clear_rejected_steers(self):
        self.rejected_steers = []

    @staticmethod
    def _preview_lines(messages: list[str]) -> list[str]:
        lines = []
        for msg in messages[:3]:
            truncated = msg if len(msg) <= 60 else msg[:57] + "..."
            lines.append(f"  ↳ {truncated}")
        if len(messages) > 3:
            lines.append(f"  ↳ ...and {len(messages) - 3} more")
        return lines

    def render_lines(self) -> list[str]:
        lines = []
        if self.pending_steers:
            lines.append(
                "• Messages to be submitted after next tool call "
                "(press esc to interrupt and send immediately)"
            )
            lines.extend(self._preview_lines(self.pending_steers))
        if self.rejected_steers:
            if lines:
                lines.append("")
            lines.append("• Messages to be submitted at end of turn")
            lines.extend(self._preview_lines(self.rejected_steers))
        if self.queued_messages:
            if lines:
                lines.append("")
            lines.append("• Queued follow-up messages")
            lines.extend(self._preview_lines(self.queued_messages))
            lines.append("    alt+up edit last queued message")
        return lines

    def is_empty(self) -> bool:
        return not (self.pending_steers or self.rejected_steers or self.queued_messages)
