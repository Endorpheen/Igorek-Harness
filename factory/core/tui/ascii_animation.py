"""Drives ASCII art animations shared across popups (port of ascii_animation.rs).

The Python port uses :class:`time.monotonic` for the start anchor and
exposes a minimal :class:`FrameRequester` protocol that matches the
``tui::FrameRequester`` interface used by the Rust source.

The test module from the source is **not** ported per the project's
"no tests" policy.
"""
from __future__ import annotations

import random
import time
from typing import List, Optional, Protocol


from .frames import ALL_VARIANTS, FRAME_TICK_DEFAULT_MS


class FrameRequester(Protocol):
    """Port of ``tui::FrameRequester`` (Python Protocol)."""

    def schedule_frame(self) -> None: ...
    def schedule_frame_in(self, ms: int) -> None: ...


class AsciiAnimation:
    """Drives ASCII art animations (port one-to-one)."""

    def __init__(self, request_frame: FrameRequester) -> None:
        self.request_frame: FrameRequester = request_frame
        self.variants: List[List[str]] = ALL_VARIANTS
        self.variant_idx: int = 0
        self.frame_tick_ms: int = FRAME_TICK_DEFAULT_MS
        self.start_monotonic: float = time.monotonic()

    @classmethod
    def with_variants(
        cls,
        request_frame: FrameRequester,
        variants: List[List[str]],
        variant_idx: int = 0,
    ) -> "AsciiAnimation":
        if not variants:
            raise ValueError("AsciiAnimation requires at least one animation variant")
        clamped_idx = max(0, min(variant_idx, len(variants) - 1))
        inst = cls(request_frame)
        inst.variants = variants
        inst.variant_idx = clamped_idx
        return inst

    def schedule_next_frame(self) -> None:
        """Schedule the next frame based on the current frame_tick (port one-to-one)."""
        tick_ms = self.frame_tick_ms
        if tick_ms == 0:
            self.request_frame.schedule_frame()
            return
        elapsed_ms = int((time.monotonic() - self.start_monotonic) * 1000)
        rem_ms = elapsed_ms % tick_ms
        delay_ms = tick_ms if rem_ms == 0 else tick_ms - rem_ms
        try:
            self.request_frame.schedule_frame_in(int(delay_ms))
        except Exception:
            self.request_frame.schedule_frame()

    def current_frame(self) -> str:
        """Return the current frame for the current variant (port one-to-one)."""
        frames = self.frames()
        if not frames:
            return ""
        tick_ms = self.frame_tick_ms
        if tick_ms == 0:
            return frames[0]
        elapsed_ms = int((time.monotonic() - self.start_monotonic) * 1000)
        idx = (elapsed_ms // tick_ms) % len(frames)
        return frames[idx]

    def pick_random_variant(self) -> bool:
        """Pick a random variant distinct from the current one (port one-to-one)."""
        if len(self.variants) <= 1:
            return False
        rng = random.SystemRandom()
        next_idx = self.variant_idx
        while next_idx == self.variant_idx:
            next_idx = rng.randrange(len(self.variants))
        self.variant_idx = next_idx
        self.request_frame.schedule_frame()
        return True

    def frames(self) -> List[str]:
        return self.variants[self.variant_idx]


__all__ = ["AsciiAnimation", "FrameRequester"]
