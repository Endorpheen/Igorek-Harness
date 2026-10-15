"""Centralized motion primitives for the TUI (port of motion.rs).

Callers choose an explicit reduced-motion fallback here instead of
reaching directly for time-varying spinner or shimmer helpers.
"""
from __future__ import annotations

import time
from typing import List, Optional, Tuple

from .shimmer import shimmer_spans


# -- MotionMode / ReducedMotionIndicator enums (port one-to-one) -------------------

class MotionMode:
    """Port of ``codex::motion::MotionMode``."""
    Animated = "animated"
    Reduced = "reduced"


class ReducedMotionIndicator:
    """Port of ``codex::motion::ReducedMotionIndicator``."""
    Hidden = "hidden"
    StaticBullet = "static_bullet"


def motion_mode_from_animations_enabled(animations_enabled: bool) -> str:
    """Map a runtime flag to a ``MotionMode`` (port of ``from_animations_enabled``)."""
    return MotionMode.Animated if animations_enabled else MotionMode.Reduced


# -- Spinner / shimmer selectors (port of activity_indicator / shimmer_text) ---

def activity_indicator(
    start_time: Optional[float],
    motion_mode: str,
    reduced_motion_indicator: str,
) -> Optional[str]:
    """Return a spinner span for the given motion mode, or ``None``.

    Port of ``codex::motion::activity_indicator``.
    """
    if motion_mode == MotionMode.Animated:
        return _animated_activity_indicator(start_time)
    if reduced_motion_indicator == ReducedMotionIndicator.Hidden:
        return None
    if reduced_motion_indicator == ReducedMotionIndicator.StaticBullet:
        return "\033[2m\u2022\033[0m"
    return None


def shimmer_text(text: str, motion_mode: str) -> List[str]:
    """Return shimmer-styled spans for ``text`` (or plain text in Reduced mode).

    Port of ``codex::motion::shimmer_text``.
    """
    if motion_mode == MotionMode.Animated:
        return shimmer_spans(text)
    if not text:
        return []
    return [text]


# -- Internal: animated activity indicator (port of animated_activity_indicator)

def _animated_activity_indicator(start_time: Optional[float]) -> str:
    """Animated activity indicator (port one-to-one)."""
    if start_time is None:
        elapsed_ms = 0
    else:
        elapsed_ms = int((time.monotonic() - start_time) * 1000)
    has_true_color = True
    if has_true_color:
        spans = shimmer_spans("\u2022")
        if spans:
            return spans[0]
        return "\u2022"
    blink_on = (elapsed_ms // 600) % 2 == 0
    if blink_on:
        return "\u2022"
    return "\033[2m\u25e6\033[0m"


__all__ = [
    "MotionMode",
    "ReducedMotionIndicator",
    "activity_indicator",
    "motion_mode_from_animations_enabled",
    "shimmer_text",
]
