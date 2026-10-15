"""ASCII animation frame tables (port of codex-rs/tui/src/frames.rs).

The original Rust source embeds the frame_*.txt files at compile time
via the ``include_str!`` macro. The Python port reads them at import
time from the ``frames/`` subdirectory that mirrors the codex layout.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import List


_FRAMES_DIR = Path(__file__).resolve().parent / "frames"


def _load_frames(variant_dir: str, count: int = 36) -> List[str]:
    out: List[str] = []
    variant_path = _FRAMES_DIR / variant_dir
    for i in range(1, count + 1):
        path = variant_path / f"frame_{i}.txt"
        try:
            with open(path, "r", encoding="utf-8") as f:
                out.append(f.read())
        except OSError:
            out.append("")
    return out


FRAMES_DEFAULT: List[str] = _load_frames("default")
FRAMES_CODEX: List[str] = _load_frames("codex")
FRAMES_OPENAI: List[str] = _load_frames("openai")
FRAMES_BLOCKS: List[str] = _load_frames("blocks")
FRAMES_DOTS: List[str] = _load_frames("dots")
FRAMES_HASH: List[str] = _load_frames("hash")
FRAMES_HBARS: List[str] = _load_frames("hbars")
FRAMES_VBARS: List[str] = _load_frames("vbars")
FRAMES_SHAPES: List[str] = _load_frames("shapes")
FRAMES_SLUG: List[str] = _load_frames("slug")


ALL_VARIANTS: List[List[str]] = [
    FRAMES_DEFAULT,
    FRAMES_CODEX,
    FRAMES_OPENAI,
    FRAMES_BLOCKS,
    FRAMES_DOTS,
    FRAMES_HASH,
    FRAMES_HBARS,
    FRAMES_VBARS,
    FRAMES_SHAPES,
    FRAMES_SLUG,
]


# Default frame tick in milliseconds (port of `FRAME_TICK_DEFAULT`).
FRAME_TICK_DEFAULT_MS: int = 80


__all__ = [
    "ALL_VARIANTS",
    "FRAME_TICK_DEFAULT_MS",
    "FRAMES_BLOCKS",
    "FRAMES_CODEX",
    "FRAMES_DEFAULT",
    "FRAMES_DOTS",
    "FRAMES_HASH",
    "FRAMES_HBARS",
    "FRAMES_OPENAI",
    "FRAMES_SHAPES",
    "FRAMES_SLUG",
    "FRAMES_VBARS",
]
