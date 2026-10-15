"""Skill popup ($) — port of Codex bottom_pane/skill_popup.rs.

Lists skills from skills_manager.  Filters by name/description substring.
Selection inserts $skill-name mention into composer.
"""
from __future__ import annotations
from .scroll_state import ScrollState

_POPUP_HEIGHT = 8


class SkillPopup:
    def __init__(self):
        self.skills: list[tuple[str, str]] = []
        self.filtered: list[tuple[str, str]] = []
        self.scroll = ScrollState(0, _POPUP_HEIGHT)
        self.query = ""

    def set_skills(self, skills: list[tuple[str, str]]):
        self.skills = skills
        self.filtered = list(skills)
        self.scroll.total = len(self.filtered)

    def update_query(self, text: str):
        self.query = text
        q = text.lower()
        if not q:
            self.filtered = list(self.skills)
        else:
            self.filtered = [
                (n, d) for n, d in self.skills
                if q in n.lower() or q in d.lower()
            ]
        self.scroll.total = max(len(self.filtered), 1)
        self.scroll.selected = 0
        self.scroll.offset = 0

    def selected_skill(self) -> tuple[str, str] | None:
        if 0 <= self.scroll.selected < len(self.filtered):
            return self.filtered[self.scroll.selected]
        return None

    def on_composer_text_change(self, text: str):
        """Extract query from first line starting with $."""
        first_line = text.splitlines()[0] if text else ""
        if first_line.startswith("$"):
            token = first_line[1:].strip()
            self.update_query(token)
        else:
            self.update_query("")

    def move_up(self):
        total = len(self.filtered)
        if total == 0:
            return
        self.scroll.selected = (self.scroll.selected - 1) % total
        self.scroll.offset = max(0, self.scroll.selected - self.scroll.visible + 1)

    def move_down(self):
        total = len(self.filtered)
        if total == 0:
            return
        self.scroll.selected = (self.scroll.selected + 1) % total
        self.scroll.offset = max(0, self.scroll.selected - self.scroll.visible + 1)

    def desired_height(self) -> int:
        return min(len(self.filtered), _POPUP_HEIGHT) + 1

    def render_lines(self, width: int = 80) -> list[str]:
        items = self.filtered
        if not self.skills:
            return ["  no skills loaded"]
        if not items:
            return ["  no matches"]
        visible = min(len(items), _POPUP_HEIGHT)
        start = self.scroll.offset
        lines: list[str] = []
        for i in range(start, min(start + visible, len(items))):
            name, desc = items[i]
            selected = i == self.scroll.selected
            marker = "\u25ba" if selected else " "
            # Truncate to width
            label = f"${name}"
            avail = width - 4 - len(label) - 3
            if avail > 5 and desc:
                desc_short = desc[:avail] if len(desc) > avail else desc
                line = f"  {marker} {label}  {desc_short}"
            else:
                line = f"  {marker} {label}"
            lines.append(line)
        return lines
