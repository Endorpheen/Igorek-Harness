"""Animated shimmer span generator (port of codex-rs/tui/src/shimmer.rs).

Renders a moving highlight band over a fixed text string. The sweep
position is time-based and synchronized to process start so multiple
shimmers move together.

In our ANSI-string world a ``Span`` is a plain ``str`` carrying the
style prefix codes; we approximate the RGB gradient by interpolating
each character between ``base_color`` and ``highlight_color``.
"""
from __future__ import annotations

import math
import time
from typing import List, Tuple

from .color import blend
from .terminal_palette import default_bg, default_fg
from .terminal_hyperlinks import _char_width


_PROCESS_START: float = time.monotonic()


def _elapsed_since_start() -> float:
    return time.monotonic() - _PROCESS_START


def shimmer_spans(text: str) -> List[str]:
    """Render ``text`` as a moving highlight band.

    Returns a list of ANSI-styled string spans that, concatenated, render
    the original text with a sweeping highlight.

    Port of ``codex::shimmer::shimmer_spans``.
    """
    chars: List[str] = list(text)
    if not chars:
        return []
    padding = 10
    period = len(chars) + padding * 2
    sweep_seconds = 2.0
    pos_f = (_elapsed_since_start() % sweep_seconds) / sweep_seconds * period
    pos = int(pos_f)
    has_true_color = True
    band_half_width = 5.0

    base_color = default_fg() or (128, 128, 128)
    highlight_color = default_bg() or (255, 255, 255)
    spans: List[str] = []
    for i, ch in enumerate(chars):
        i_pos = i + padding
        dist = abs(i_pos - pos)
        t = 0.0
        if dist <= band_half_width:
            x = math.pi * (dist / band_half_width)
            t = 0.5 * (1.0 + math.cos(x))
        style = _color_for_level(t, has_true_color, base_color, highlight_color)
        if style:
            spans.append(f"{style}{ch}\033[0m")
        else:
            spans.append(ch)
    return spans


def _color_for_level(
    intensity: float,
    has_true_color: bool,
    base_color: Tuple[int, int, int],
    highlight_color: Tuple[int, int, int],
) -> str:
    """Return ANSI prefix for a given shimmer intensity (0..=1).

    Port of ``codex::shimmer::color_for_level`` + RGB blend branch.
    """
    if has_true_color:
        highlight = max(0.0, min(1.0, intensity))
        r, g, b = blend(highlight_color, base_color, highlight * 0.9)
        bold = "\033[1m"
        return f"\033[38;2;{r};{g};{b}m{bold}"
    if intensity < 0.2:
        return "\033[2m"
    if intensity < 0.6:
        return ""
    return "\033[1m"


__all__ = ["shimmer_spans", "spinner_char"]


# ---------------------------------------------------------------------------
# Spinner character helper (used by exec_cell/render.py legacy path)
# ---------------------------------------------------------------------------
# Codex-rs has a dedicated `Spinner` type in `spinner.rs`; the Python port
# exposes a single `spinner_char` fn returning the current frame for a
# given elapsed time. Legacy callers in `exec_cell/render.py` import it
# but do not yet route through it (they use the inline blink logic in
# `_bullet`). Keeping the symbol here so that import succeeds.

_SPINNER_FRAMES: Tuple[str, ...] = ("•", "◦", "•", "◦", "◌", "○")


def spinner_char(elapsed_seconds: float = 0.0) -> str:
    """Return the current spinner character for the given elapsed time.

    Cycles through ``_SPINNER_FRAMES`` at 10 fps. Mirrors the codex
    ``Spinner::current()`` interface at a 1-frame granularity.
    """
    frame_idx = int(elapsed_seconds * 10) % len(_SPINNER_FRAMES)
    return _SPINNER_FRAMES[frame_idx]
