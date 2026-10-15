"""Color utilities (port of codex-rs/tui/src/color.rs).

Three small RGB helpers used throughout the TUI for theming and
shimmer/gradient rendering.
"""
from __future__ import annotations

from typing import Tuple


RGB = Tuple[int, int, int]


def is_light(bg: RGB) -> bool:
    """Return ``True`` if the background color is perceptually light.

    Port of ``codex::color::is_light``.
    """
    r, g, b = bg
    y = 0.299 * r + 0.587 * g + 0.114 * b
    return y > 128.0


def blend(fg: RGB, bg: RGB, alpha: float) -> RGB:
    """Linearly blend ``fg`` over ``bg`` using ``alpha`` (0..=1).

    Port of ``codex::color::blend``.
    """
    r = int(fg[0] * alpha + bg[0] * (1.0 - alpha))
    g = int(fg[1] * alpha + bg[1] * (1.0 - alpha))
    b = int(fg[2] * alpha + bg[2] * (1.0 - alpha))
    return (r, g, b)


def perceptual_distance(a: RGB, b: RGB) -> float:
    """Perceptual color distance between two RGB colors (CIE76 / Lab).

    Port of ``codex::color::perceptual_distance``.
    """
    def srgb_to_linear(c: int) -> float:
        c = c / 255.0
        if c <= 0.04045:
            return c / 12.92
        return ((c + 0.055) / 1.055) ** 2.4

    def rgb_to_xyz(r: int, g: int, b: int) -> Tuple[float, float, float]:
        r = srgb_to_linear(r)
        g = srgb_to_linear(g)
        b = srgb_to_linear(b)
        x = r * 0.4124 + g * 0.3576 + b * 0.1805
        y = r * 0.2126 + g * 0.7152 + b * 0.0722
        z = r * 0.0193 + g * 0.1192 + b * 0.9505
        return (x, y, z)

    def xyz_to_lab(x: float, y: float, z: float) -> Tuple[float, float, float]:
        xr = x / 0.95047
        yr = y / 1.00000
        zr = z / 1.08883

        def f(t: float) -> float:
            if t > 0.008856:
                return t ** (1.0 / 3.0)
            return 7.787 * t + 16.0 / 116.0

        fx = f(xr)
        fy = f(yr)
        fz = f(zr)

        l = 116.0 * fy - 16.0
        a = 500.0 * (fx - fy)
        b = 200.0 * (fy - fz)
        return (l, a, b)

    x1, y1, z1 = rgb_to_xyz(a[0], a[1], a[2])
    x2, y2, z2 = rgb_to_xyz(b[0], b[1], b[2])
    l1, a1, b1 = xyz_to_lab(x1, y1, z1)
    l2, a2, b2 = xyz_to_lab(x2, y2, z2)
    dl = l1 - l2
    da = a1 - a2
    db = b1 - b2
    return (dl * dl + da * da + db * db) ** 0.5


__all__ = ["is_light", "blend", "perceptual_distance"]
