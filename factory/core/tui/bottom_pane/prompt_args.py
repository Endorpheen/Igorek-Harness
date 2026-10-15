"""Prompt args — port of Codex bottom_pane/prompt_args.rs."""
from __future__ import annotations
from typing import Optional

def parse_slash_name(text: str) -> Optional[str]:
    if text.startswith("/"):
        parts = text[1:].split(maxsplit=1)
        return parts[0] if parts else None
    return None
