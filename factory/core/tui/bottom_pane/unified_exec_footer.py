"""Unified exec footer — port of Codex bottom_pane/unified_exec_footer.rs."""
from __future__ import annotations

class UnifiedExecFooter:
    def __init__(self):
        self.active_processes: list[dict] = []

    def add_process(self, cmd: str, pid: int):
        self.active_processes.append({"cmd": cmd, "pid": pid})

    def remove_process(self, pid: int):
        self.active_processes = [p for p in self.active_processes if p["pid"] != pid]

    def is_empty(self) -> bool:
        return not self.active_processes

    def status_text(self) -> str:
        if not self.active_processes:
            return ""
        n = len(self.active_processes)
        return f"{n} background terminal{'s' if n > 1 else ''} running"

    def render_lines(self) -> list[str]:
        lines = []
        for p in self.active_processes[:16]:
            cmd = p["cmd"][:60]
            lines.append(f"  {p['pid']}: {cmd}")
        return lines
