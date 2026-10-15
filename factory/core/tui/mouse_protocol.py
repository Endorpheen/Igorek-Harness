"""SGR mouse protocol parsing for the TUI chat.

This module is deliberately dependency-free (besides dataclasses) so the
parsing rules can be unit-tested without a terminal. It implements the
xterm SGR 1006 mouse reporting format:

    ESC [ < Cb ; Cx ; Cy (M|m)

* ``Cb`` carries the button, modifier bits and the wheel/motion flags.
* ``Cx``/``Cy`` are 1-based terminal coordinates.
* ``M`` means press / motion, ``m`` means release.

The parser returns absolute zero-based coordinates, matching the TZ
contract: ``\\x1b[<0;10;5M`` must yield a left press at ``(9, 4)``.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class MouseEventKind(str, Enum):
    PRESS = "press"
    RELEASE = "release"
    MOVE = "move"
    SCROLL_UP = "scroll_up"
    SCROLL_DOWN = "scroll_down"
    SCROLL_LEFT = "scroll_left"
    SCROLL_RIGHT = "scroll_right"


class MouseButton(str, Enum):
    LEFT = "left"
    MIDDLE = "middle"
    RIGHT = "right"
    NONE = "none"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class MouseEvent:
    kind: MouseEventKind
    button: MouseButton
    x: int  # zero-based absolute column
    y: int  # zero-based absolute row
    shift: bool = False
    alt: bool = False
    ctrl: bool = False

    @property
    def is_left(self) -> bool:
        return self.button is MouseButton.LEFT

    @property
    def has_modifiers(self) -> bool:
        return self.shift or self.alt or self.ctrl

    def is_plain_left_press(self) -> bool:
        return (
            self.kind is MouseEventKind.PRESS
            and self.button is MouseButton.LEFT
            and not self.has_modifiers
        )


_SGR_PREFIX = "\x1b[<"


def is_sgr_mouse_sequence(data: str) -> bool:
    return isinstance(data, str) and data.startswith(_SGR_PREFIX)


def parse_sgr_mouse_sequence(data: str) -> Optional[MouseEvent]:
    """Parse a single SGR 1006 mouse packet.

    Returns ``None`` for anything malformed so callers can safely ignore
    unknown input (and delegate compatible legacy protocols upstream).
    """
    if not is_sgr_mouse_sequence(data):
        return None
    body = data[len(_SGR_PREFIX):]
    if not body:
        return None
    terminator = body[-1]
    if terminator not in ("M", "m"):
        return None
    payload = body[:-1]
    parts = payload.split(";")
    if len(parts) != 3:
        return None
    try:
        cb = int(parts[0])
        cx = int(parts[1])
        cy = int(parts[2])
    except (TypeError, ValueError):
        return None
    if cx < 1 or cy < 1 or cb < 0:
        return None

    shift = bool(cb & 4)
    alt = bool(cb & 8)
    ctrl = bool(cb & 16)
    motion = bool(cb & 32)
    wheel = bool(cb & 64)
    button_bits = cb & 3

    if wheel:
        if button_bits == 0:
            kind = MouseEventKind.SCROLL_UP
        elif button_bits == 1:
            kind = MouseEventKind.SCROLL_DOWN
        elif button_bits == 2:
            kind = MouseEventKind.SCROLL_LEFT
        else:
            kind = MouseEventKind.SCROLL_RIGHT
        button = MouseButton.NONE
    else:
        button = {
            0: MouseButton.LEFT,
            1: MouseButton.MIDDLE,
            2: MouseButton.RIGHT,
            3: MouseButton.NONE,
        }[button_bits]
        if motion:
            kind = MouseEventKind.MOVE
        elif terminator == "m":
            kind = MouseEventKind.RELEASE
        else:
            kind = MouseEventKind.PRESS

    return MouseEvent(
        kind=kind,
        button=button,
        x=cx - 1,
        y=cy - 1,
        shift=shift,
        alt=alt,
        ctrl=ctrl,
    )


__all__ = [
    "MouseButton",
    "MouseEvent",
    "MouseEventKind",
    "is_sgr_mouse_sequence",
    "parse_sgr_mouse_sequence",
]
