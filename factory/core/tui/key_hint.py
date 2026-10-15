"""Key binding primitives and input matching (port of key_hint.rs).

``KeyBinding`` is the runtime representation of a single keybinding
(key code + modifier set), along with matching logic that handles
cross-terminal inconsistencies in how shifted letters and raw C0
control characters are reported.

In our Python port, ``KeyCode`` is a small dataclass with a ``name``
string and optional ``char`` payload. ``KeyEvent`` mirrors the
crossterm shape (code, modifiers, kind).
"""
from __future__ import annotations

import sys
from dataclasses import dataclass
from typing import List, Optional, Tuple


# -- Key code dataclass (Python analogue of crossterm::event::KeyCode) --------

@dataclass(frozen=True)
class KeyCode:
    """Port of ``crossterm::event::KeyCode`` (simplified)."""
    name: str  # "char" | "enter" | "esc" | "up" | "down" | "left" | "right" | "page_up" | "page_down" | "backspace" | "tab" | "delete" | "f1".."f12" | ...
    char: Optional[str] = None  # only for KeyCode::Char

    @classmethod
    def Char(cls, ch: str) -> "KeyCode":
        return cls(name="char", char=ch)

    @classmethod
    def Enter(cls) -> "KeyCode":
        return cls(name="enter")

    @classmethod
    def Esc(cls) -> "KeyCode":
        return cls(name="esc")

    @classmethod
    def Up(cls) -> "KeyCode":
        return cls(name="up")

    @classmethod
    def Down(cls) -> "KeyCode":
        return cls(name="down")

    @classmethod
    def Left(cls) -> "KeyCode":
        return cls(name="left")

    @classmethod
    def Right(cls) -> "KeyCode":
        return cls(name="right")

    @classmethod
    def PageUp(cls) -> "KeyCode":
        return cls(name="page_up")

    @classmethod
    def PageDown(cls) -> "KeyCode":
        return cls(name="page_down")

    @classmethod
    def Backspace(cls) -> "KeyCode":
        return cls(name="backspace")

    @classmethod
    def Tab(cls) -> "KeyCode":
        return cls(name="tab")

    @classmethod
    def Delete(cls) -> "KeyCode":
        return cls(name="delete")

    @classmethod
    def F(cls, n: int) -> "KeyCode":
        return cls(name=f"f{n}")

    def __str__(self) -> str:
        if self.name == "char" and self.char is not None:
            return self.char
        return self.name


# -- Modifier bitmask (port of crossterm::event::KeyModifiers) ---------------

class KeyModifiers:
    """Bitmask of modifier keys. Port of ``crossterm::event::KeyModifiers``."""
    NONE = 0
    SHIFT = 1
    CONTROL = 2
    ALT = 4

    @staticmethod
    def contains(modifiers: int, flag: int) -> bool:
        return (modifiers & flag) == flag

    @staticmethod
    def is_empty(modifiers: int) -> bool:
        return modifiers == 0

    @staticmethod
    def union(a: int, b: int) -> int:
        return a | b

    @staticmethod
    def insert(modifiers: int, flag: int) -> int:
        return modifiers | flag


# -- Key event kind (port of crossterm::event::KeyEventKind) ------------------

class KeyEventKind:
    """Port of ``crossterm::event::KeyEventKind``."""
    Press = "press"
    Release = "release"
    Repeat = "repeat"


@dataclass(frozen=True)
class KeyEvent:
    """Port of ``crossterm::event::KeyEvent`` (Python simplified)."""
    code: KeyCode
    modifiers: int
    kind: str = KeyEventKind.Press

    @classmethod
    def new(cls, code: KeyCode, modifiers: int) -> "KeyEvent":
        return cls(code=code, modifiers=modifiers, kind=KeyEventKind.Press)


# -- KeyBinding (port of codex::key_hint::KeyBinding) -------------------------

@dataclass(frozen=True)
class KeyBinding:
    """One concrete key event that can trigger a TUI action.

    Port of ``codex::key_hint::KeyBinding``.
    """
    key: KeyCode
    modifiers: int

    @classmethod
    def create(cls, key: KeyCode, modifiers: int) -> "KeyBinding":
        return cls(key=key, modifiers=modifiers)

    @classmethod
    def from_event(cls, event: KeyEvent) -> "KeyBinding":
        key, modifiers = normalize_key_parts(event.code, event.modifiers)
        return cls(key=key, modifiers=modifiers)

    def is_press(self, event: KeyEvent) -> bool:
        """Whether ``event`` matches this binding on Press or Repeat.

        Port of ``KeyBinding::is_press``.
        """
        left_key, left_mods = normalize_key_parts(self.key, self.modifiers)
        right_key, right_mods = normalize_key_parts(event.code, event.modifiers)
        if left_key != right_key or left_mods != right_mods:
            return False
        return event.kind in (KeyEventKind.Press, KeyEventKind.Repeat)

    def parts(self) -> Tuple[KeyCode, int]:
        """Return ``(key, modifiers)`` (port of ``parts``)."""
        return (self.key, self.modifiers)

    def display_label(self) -> str:
        """Return a human-readable label like ``ctrl + shift + up``.

        Port of ``KeyBinding::display_label``.
        """
        modifiers = modifiers_to_string(self.modifiers)
        key_str = _key_display_name(self.key)
        return f"{modifiers}{key_str}"


# -- Helpers (port of normalize_key_parts / c0_control_char_to_ctrl_char) ----

def normalize_key_parts(key: KeyCode, modifiers: int) -> Tuple[KeyCode, int]:
    """Normalize a key/modifier pair for cross-terminal matching.

    Port of ``codex::key_hint::normalize_key_parts``.
    """
    if key.name != "char" or key.char is None:
        return (key, modifiers)
    ch = key.char
    if KeyModifiers.is_empty(modifiers):
        ctrl_char = c0_control_char_to_ctrl_char(ch)
        if ctrl_char is not None:
            return (KeyCode.Char(ctrl_char), KeyModifiers.CONTROL | modifiers)
    if ch.isascii() and ch.isupper():
        return (
            KeyCode.Char(ch.lower()),
            KeyModifiers.insert(modifiers, KeyModifiers.SHIFT),
        )
    return (key, modifiers)


def c0_control_char_to_ctrl_char(ch: str) -> Optional[str]:
    """Map a raw C0 control character to its Ctrl-letter equivalent.

    Port of ``codex::key_hint::c0_control_char_to_ctrl_char``.
    """
    code = ord(ch)
    if code == 0x00:
        return " "
    if 0x01 <= code <= 0x1A:
        return chr(code - 0x01 + ord("a"))
    if 0x1C <= code <= 0x1F:
        return chr(code - 0x1C + ord("4"))
    return None


def _key_display_name(key: KeyCode) -> str:
    """Map a ``KeyCode`` to its display string (port of ``display_label`` body)."""
    if key.name == "enter":
        return "enter"
    if key.name == "char" and key.char == " ":
        return "space"
    if key.name == "up":
        return "\u2191"
    if key.name == "down":
        return "\u2193"
    if key.name == "left":
        return "\u2190"
    if key.name == "right":
        return "\u2192"
    if key.name == "page_up":
        return "pgup"
    if key.name == "page_down":
        return "pgdn"
    return str(key).lower()


# -- Constructors (port of plain/alt/shift/ctrl/ctrl_alt) ---------------------

def plain(key: KeyCode) -> KeyBinding:
    """Create a no-modifier binding (port of ``plain``)."""
    return KeyBinding(key=key, modifiers=KeyModifiers.NONE)


def alt(key: KeyCode) -> KeyBinding:
    """Create an Alt binding (port of ``alt``)."""
    return KeyBinding(key=key, modifiers=KeyModifiers.ALT)


def shift(key: KeyCode) -> KeyBinding:
    """Create a Shift binding (port of ``shift``)."""
    return KeyBinding(key=key, modifiers=KeyModifiers.SHIFT)


def ctrl(key: KeyCode) -> KeyBinding:
    """Create a Ctrl binding (port of ``ctrl``)."""
    return KeyBinding(key=key, modifiers=KeyModifiers.CONTROL)


def ctrl_alt(key: KeyCode) -> KeyBinding:
    """Create a Ctrl+Alt binding (port of ``ctrl_alt``)."""
    return KeyBinding(
        key=key,
        modifiers=KeyModifiers.union(KeyModifiers.CONTROL, KeyModifiers.ALT),
    )


# -- List extension (port of KeyBindingListExt) ------------------------------

def is_pressed(bindings: List[KeyBinding], event: KeyEvent) -> bool:
    """True when any binding in ``bindings`` matches ``event`` (port of ``is_pressed``)."""
    return any(b.is_press(event) for b in bindings)


# -- Plain-text detection (port of is_plain_text_key_event) ------------------

def is_plain_text_key_event(event: KeyEvent) -> bool:
    """Whether ``event`` should be treated as literal text input.

    Port of ``codex::key_hint::is_plain_text_key_event``.
    """
    if event.code.name != "char" or event.code.char is None:
        return False
    ch = event.code.char
    if ord(ch) < 0x20:
        return False
    if KeyModifiers.contains(event.modifiers, KeyModifiers.CONTROL):
        return False
    if KeyModifiers.contains(event.modifiers, KeyModifiers.ALT):
        return False
    return True


# -- Modifier display (port of modifiers_to_string) ---------------------------

ALT_PREFIX = "\u2325 + " if sys.platform == "darwin" else "alt + "
CTRL_PREFIX = "ctrl + "
SHIFT_PREFIX = "shift + "


def modifiers_to_string(modifiers: int) -> str:
    """Format a modifier bitmask as a prefix string (port of ``modifiers_to_string``)."""
    out: List[str] = []
    if KeyModifiers.contains(modifiers, KeyModifiers.CONTROL):
        out.append(CTRL_PREFIX)
    if KeyModifiers.contains(modifiers, KeyModifiers.SHIFT):
        out.append(SHIFT_PREFIX)
    if KeyModifiers.contains(modifiers, KeyModifiers.ALT):
        out.append(ALT_PREFIX)
    return "".join(out)


# -- Span conversion (port of From<KeyBinding> for Span) ---------------------

def keybinding_to_styled_span(binding: KeyBinding) -> str:
    """Return an ANSI-dim ``Span``-equivalent string for the binding.

    Port of ``From<&KeyBinding> for ratatui::text::Span``.
    """
    return f"\033[2m{binding.display_label()}\033[0m"


# -- Modifier queries (port of has_ctrl_or_alt / is_altgr) -------------------

def has_ctrl_or_alt(mods: int) -> bool:
    """True if ``mods`` contain Ctrl or Alt (and not an AltGr on Windows).

    Port of ``codex::key_hint::has_ctrl_or_alt``.
    """
    has_mod = (
        KeyModifiers.contains(mods, KeyModifiers.CONTROL)
        or KeyModifiers.contains(mods, KeyModifiers.ALT)
    )
    if not has_mod:
        return False
    return not is_altgr(mods)


def is_altgr(mods: int) -> bool:
    """Whether ``mods`` represent AltGr (Ctrl+Alt on Windows, false elsewhere).

    Port of ``codex::key_hint::is_altgr``.
    """
    if sys.platform.startswith("win"):
        return (
            KeyModifiers.contains(mods, KeyModifiers.ALT)
            and KeyModifiers.contains(mods, KeyModifiers.CONTROL)
        )
    return False


__all__ = [
    "ALT_PREFIX",
    "CTRL_PREFIX",
    "KeyBinding",
    "KeyCode",
    "KeyEvent",
    "KeyEventKind",
    "KeyModifiers",
    "SHIFT_PREFIX",
    "alt",
    "c0_control_char_to_ctrl_char",
    "ctrl",
    "ctrl_alt",
    "has_ctrl_or_alt",
    "is_altgr",
    "is_plain_text_key_event",
    "is_pressed",
    "keybinding_to_styled_span",
    "modifiers_to_string",
    "normalize_key_parts",
    "plain",
    "shift",
]
