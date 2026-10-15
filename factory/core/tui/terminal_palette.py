"""Terminal palette detection and xterm-256 color table.

Python port of codex-rs/tui/src/terminal_palette.rs.

Provides:

- ``StdoutColorLevel`` enum (TrueColor / Ansi256 / Ansi16 / Unknown)
- ``stdout_color_level()`` — detect via ``COLORTERM`` / ``TERM`` env vars
- ``XTERM_COLORS`` — full 256-color xterm palette (constant)
- ``best_color(target)`` — pick the closest supported color
- ``default_fg()`` / ``default_bg()`` — query OSC 10/11 from
  ``COLORFGBG`` env (Python analogue of crossterm's
  ``query_foreground_color`` / ``query_background_color``)
"""
from __future__ import annotations

import os
import re
import threading
from dataclasses import dataclass
from typing import List, Optional, Tuple

from .color import perceptual_distance


RGB = Tuple[int, int, int]


# -- Color level detection (port of StdoutColorLevel / stdout_color_level) ----

class StdoutColorLevel:
    """Port of ``codex::terminal_palette::StdoutColorLevel``."""
    TrueColor = "true_color"
    Ansi256 = "ansi_256"
    Ansi16 = "ansi_16"
    Unknown = "unknown"


def stdout_color_level() -> str:
    """Return the detected stdout color level.

    Port of ``codex::terminal_palette::stdout_color_level``.

    Detection rules (Python analogue of codex + ``supports_color``):
    - ``COLORTERM`` in ``{truecolor, 24bit}`` → ``TrueColor``
    - ``TERM`` contains ``256color`` → ``Ansi256``
    - ``TERM`` is non-empty (not ``dumb``) → ``Ansi16``
    - else → ``Unknown``
    """
    colorterm = os.environ.get("COLORTERM", "").lower()
    if colorterm in ("truecolor", "24bit"):
        return StdoutColorLevel.TrueColor
    term = os.environ.get("TERM", "").lower()
    if "256color" in term:
        return StdoutColorLevel.Ansi256
    if term and term != "dumb":
        return StdoutColorLevel.Ansi16
    return StdoutColorLevel.Unknown


# -- Color constructors (port of rgb_color / indexed_color) -------------------

def rgb_color(color: RGB) -> Tuple[str, RGB]:
    """Tag an RGB triple as a 24-bit color (port of ``rgb_color``).

    Returns a tagged tuple so callers can distinguish from system colors.
    """
    return ("rgb", color)


def indexed_color(index: int) -> Tuple[str, int]:
    """Tag an xterm-256 palette index (port of ``indexed_color``)."""
    return ("indexed", int(index))


def best_color(target: RGB) -> object:
    """Return the closest color the terminal can display.

    Port of ``codex::terminal_palette::best_color``.
    """
    level = stdout_color_level()
    if level == StdoutColorLevel.TrueColor:
        return rgb_color(target)
    if level == StdoutColorLevel.Ansi256:
        best = None
        best_dist = None
        for i, color in enumerate(XTERM_COLORS):
            if i < 16:
                continue
            d = perceptual_distance(color, target)
            if best_dist is None or d < best_dist:
                best = i
                best_dist = d
        if best is not None:
            return indexed_color(best)
    return ("default",)


# -- Default colors cache (port of imp::default_colors) -----------------------

@dataclass
class DefaultColors:
    """Port of ``codex::terminal_palette::DefaultColors``."""
    fg: RGB
    bg: RGB


_cache_lock = threading.RLock()
_cache_attempted: bool = False
_cache_value: Optional[DefaultColors] = None


def requery_default_colors() -> None:
    """Re-query the terminal default colors and update the cache.

    Port of ``codex::terminal_palette::requery_default_colors``.
    """
    global _cache_attempted, _cache_value
    with _cache_lock:
        if _cache_attempted and _cache_value is None:
            return
        colors = _query_default_colors()
        if colors is not None:
            _cache_value = colors
            _cache_attempted = True


def set_default_colors_from_startup_probe(colors: Optional[DefaultColors]) -> None:
    """Set the cached default colors from an external probe.

    Port of ``codex::terminal_palette::set_default_colors_from_startup_probe``.
    """
    global _cache_attempted, _cache_value
    with _cache_lock:
        _cache_value = colors
        _cache_attempted = True


def default_colors() -> Optional[DefaultColors]:
    """Return cached default terminal colors, lazily probing on first call.

    Port of ``codex::terminal_palette::default_colors``.
    """
    global _cache_attempted, _cache_value
    with _cache_lock:
        if not _cache_attempted:
            _cache_value = _query_default_colors()
            _cache_attempted = True
        return _cache_value


def default_fg() -> Optional[RGB]:
    """Return the cached default foreground color, or ``None`` (port of ``default_fg``)."""
    return default_colors().fg if default_colors() is not None else None


def default_bg() -> Optional[RGB]:
    """Return the cached default background color, or ``None`` (port of ``default_bg``)."""
    return default_colors().bg if default_colors() is not None else None


def _query_default_colors() -> Optional[DefaultColors]:
    """Probe the terminal for default colors via ``COLORFGBG`` env.

    The ``COLORFGBG`` environment variable (set by many terminals) holds
    ``<fg>;<bg>`` as 0-15 xterm palette indices. We map those indices
    through ``XTERM_COLORS`` to RGB tuples.
    """
    colorfgbg = os.environ.get("COLORFGBG")
    if not colorfgbg:
        return None
    parts = re.split(r"[;:]", colorfgbg)
    if len(parts) < 2:
        return None
    try:
        fg_idx = int(parts[0])
        bg_idx = int(parts[1])
    except ValueError:
        return None
    if not (0 <= fg_idx < 256 and 0 <= bg_idx < 256):
        return None
    return DefaultColors(fg=XTERM_COLORS[fg_idx], bg=XTERM_COLORS[bg_idx])


# -- Xterm 256-color palette (port of XTERM_COLORS) --------------------------

XTERM_COLORS: List[RGB] = [
    (0, 0, 0),       (128, 0, 0),     (0, 128, 0),     (128, 128, 0),
    (0, 0, 128),     (128, 0, 128),   (0, 128, 128),   (192, 192, 192),
    (128, 128, 128), (255, 0, 0),     (0, 255, 0),     (255, 255, 0),
    (0, 0, 255),     (255, 0, 255),   (0, 255, 255),   (255, 255, 255),
    (0, 0, 0),       (0, 0, 95),      (0, 0, 135),     (0, 0, 175),
    (0, 0, 215),     (0, 0, 255),     (0, 95, 0),      (0, 95, 95),
    (0, 95, 135),    (0, 95, 175),    (0, 95, 215),    (0, 95, 255),
    (0, 135, 0),     (0, 135, 95),    (0, 135, 135),   (0, 135, 175),
    (0, 135, 215),   (0, 135, 255),   (0, 175, 0),     (0, 175, 95),
    (0, 175, 135),   (0, 175, 175),   (0, 175, 215),   (0, 175, 255),
    (0, 215, 0),     (0, 215, 95),    (0, 215, 135),   (0, 215, 175),
    (0, 215, 215),   (0, 215, 255),   (0, 255, 0),     (0, 255, 95),
    (0, 255, 135),   (0, 255, 175),   (0, 255, 215),   (0, 255, 255),
    (95, 0, 0),      (95, 0, 95),     (95, 0, 135),    (95, 0, 175),
    (95, 0, 215),    (95, 0, 255),    (95, 95, 0),     (95, 95, 95),
    (95, 95, 135),   (95, 95, 175),   (95, 95, 215),   (95, 95, 255),
    (95, 135, 0),    (95, 135, 95),   (95, 135, 135),  (95, 135, 175),
    (95, 135, 215),  (95, 135, 255),  (95, 175, 0),    (95, 175, 95),
    (95, 175, 135),  (95, 175, 175),  (95, 175, 215),  (95, 175, 255),
    (95, 215, 0),    (95, 215, 95),   (95, 215, 135),  (95, 215, 175),
    (95, 215, 215),  (95, 215, 255),  (95, 255, 0),    (95, 255, 95),
    (95, 255, 135),  (95, 255, 175),  (95, 255, 215),  (95, 255, 255),
    (135, 0, 0),     (135, 0, 95),    (135, 0, 135),   (135, 0, 175),
    (135, 0, 215),   (135, 0, 255),   (135, 95, 0),    (135, 95, 95),
    (135, 95, 135),  (135, 95, 175),  (135, 95, 215),  (135, 95, 255),
    (135, 135, 0),   (135, 135, 95),  (135, 135, 135), (135, 135, 175),
    (135, 135, 215), (135, 135, 255), (135, 175, 0),   (135, 175, 95),
    (135, 175, 135), (135, 175, 175), (135, 175, 215), (135, 175, 255),
    (135, 215, 0),   (135, 215, 95),  (135, 215, 135), (135, 215, 175),
    (135, 215, 215), (135, 215, 255), (135, 255, 0),   (135, 255, 95),
    (135, 255, 135), (135, 255, 175), (135, 255, 215), (135, 255, 255),
    (175, 0, 0),     (175, 0, 95),    (175, 0, 135),   (175, 0, 175),
    (175, 0, 215),   (175, 0, 255),   (175, 95, 0),    (175, 95, 95),
    (175, 95, 135),  (175, 95, 175),  (175, 95, 215),  (175, 95, 255),
    (175, 135, 0),   (175, 135, 95),  (175, 135, 135), (175, 135, 175),
    (175, 135, 215), (175, 135, 255), (175, 175, 0),   (175, 175, 95),
    (175, 175, 135), (175, 175, 175), (175, 175, 215), (175, 175, 255),
    (175, 215, 0),   (175, 215, 95),  (175, 215, 135), (175, 215, 175),
    (175, 215, 215), (175, 215, 255), (175, 255, 0),   (175, 255, 95),
    (175, 255, 135), (175, 255, 175), (175, 255, 215), (175, 255, 255),
    (215, 0, 0),     (215, 0, 95),    (215, 0, 135),   (215, 0, 175),
    (215, 0, 215),   (215, 0, 255),   (215, 95, 0),    (215, 95, 95),
    (215, 95, 135),  (215, 95, 175),  (215, 95, 215),  (215, 95, 255),
    (215, 135, 0),   (215, 135, 95),  (215, 135, 135), (215, 135, 175),
    (215, 135, 215), (215, 135, 255), (215, 175, 0),   (215, 175, 95),
    (215, 175, 135), (215, 175, 175), (215, 175, 215), (215, 175, 255),
    (215, 215, 0),   (215, 215, 95),  (215, 215, 135), (215, 215, 175),
    (215, 215, 215), (215, 215, 255), (215, 255, 0),   (215, 255, 95),
    (215, 255, 135), (215, 255, 175), (215, 255, 215), (215, 255, 255),
    (255, 0, 0),     (255, 0, 95),    (255, 0, 135),   (255, 0, 175),
    (255, 0, 215),   (255, 0, 255),   (255, 95, 0),    (255, 95, 95),
    (255, 95, 135),  (255, 95, 175),  (255, 95, 215),  (255, 95, 255),
    (255, 135, 0),   (255, 135, 95),  (255, 135, 135), (255, 135, 175),
    (255, 135, 215), (255, 135, 255), (255, 175, 0),   (255, 175, 95),
    (255, 175, 135), (255, 175, 175), (255, 175, 215), (255, 175, 255),
    (255, 215, 0),   (255, 215, 95),  (255, 215, 135), (255, 215, 175),
    (255, 215, 215), (255, 215, 255), (255, 255, 0),   (255, 255, 95),
    (255, 255, 135), (255, 255, 175), (255, 255, 215), (255, 255, 255),
    (8, 8, 8),       (18, 18, 18),    (28, 28, 28),    (38, 38, 38),
    (48, 48, 48),    (58, 58, 58),    (68, 68, 68),    (78, 78, 78),
    (88, 88, 88),    (98, 98, 98),    (108, 108, 108), (118, 118, 118),
    (128, 128, 128), (138, 138, 138), (148, 148, 148), (158, 158, 158),
    (168, 168, 168), (178, 178, 178), (188, 188, 188), (198, 198, 198),
    (208, 208, 208), (218, 218, 218), (228, 228, 228), (238, 238, 238),
]
"""Port of ``codex::terminal_palette::XTERM_COLORS`` (256 RGB triples)."""


def xterm_fixed_colors() -> List[Tuple[int, RGB]]:
    """Return the ``XTERM_COLORS`` entries beyond the system range (16..256)."""
    return [(i, c) for i, c in enumerate(XTERM_COLORS) if i >= 16]


__all__ = [
    "DefaultColors",
    "StdoutColorLevel",
    "XTERM_COLORS",
    "best_color",
    "default_bg",
    "default_colors",
    "default_fg",
    "indexed_color",
    "requery_default_colors",
    "rgb_color",
    "set_default_colors_from_startup_probe",
    "stdout_color_level",
    "xterm_fixed_colors",
]
