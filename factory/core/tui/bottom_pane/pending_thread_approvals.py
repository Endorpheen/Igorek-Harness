"""Pending thread approvals — port of Codex bottom_pane/pending_thread_approvals.rs."""
from __future__ import annotations

class PendingThreadApprovals:
    def __init__(self):
        self.items: list[dict] = []

    def add(self, thread_id: str, tool: str, target: str):
        self.items.append({"thread_id": thread_id, "tool": tool, "target": target})

    def clear(self):
        self.items = []

    def is_empty(self) -> bool:
        return not self.items

    def render_lines(self) -> list[str]:
        return [f"  {i['thread_id']}: {i['tool']} — {i['target']}"
                for i in self.items]
