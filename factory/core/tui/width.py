"""Width guards for transcript rendering (port of codex-rs/tui/src/width.rs).

Several rendering paths reserve a fixed number of columns for bullets,
gutters, or labels before laying out content. When the terminal is
very narrow, those reserved columns can consume the entire width,
leaving zero or negative space for content.

These helpers centralise the subtraction and enforce a strict-positive
contract: they return ``Some(n)`` where ``n > 0``, or ``None`` when no
usable content width remains. Callers treat ``None`` as
"render prefix-only fallback" rather than attempting wrapped
rendering at zero width.
"""
from __future__ import annotations

from typing import Optional


def usable_content_width(total_width: int, reserved_cols: int) -> Optional[int]:
    """Return usable content width after reserving fixed columns.

    Returns ``Some(n)`` where ``n > 0`` or ``None`` when the reserved
    columns consume the full width.

    Port of ``codex::width::usable_content_width``.
    """
    remaining = total_width - reserved_cols
    if remaining <= 0:
        return None
    return remaining


def usable_content_width_u16(total_width: int, reserved_cols: int) -> Optional[int]:
    """``u16`` convenience wrapper around ``usable_content_width``.

    Port of ``codex::width::usable_content_width_u16``.
    """
    return usable_content_width(int(total_width), int(reserved_cols))


__all__ = ["usable_content_width", "usable_content_width_u16"]
