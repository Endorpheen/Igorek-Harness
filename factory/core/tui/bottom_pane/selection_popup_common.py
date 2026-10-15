"""Selection popup common — port of Codex bottom_pane/selection_popup_common.rs."""
from __future__ import annotations

def render_popup_row(item: str, description: str = "", selected: bool = False,
                     width: int = 80, match_highlight: str = "") -> str:
    """Render one row of a selection popup."""
    prefix = "► " if selected else "  "
    desc = f"  {description}" if description else ""
    row = f"{prefix}{item}{desc}"
    if len(row) > width:
        row = row[:width - 1] + "…"
    return row
