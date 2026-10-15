"""Minimal file-change model (port of codex-rs/tui/src/diff_model.rs)."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Union


@dataclass
class AddChange:
    content: str


@dataclass
class DeleteChange:
    content: str


@dataclass
class UpdateChange:
    unified_diff: str
    move_path: Optional[Path] = None


FileChange = Union[AddChange, DeleteChange, UpdateChange]


__all__ = [
    "AddChange",
    "DeleteChange",
    "FileChange",
    "UpdateChange",
]
