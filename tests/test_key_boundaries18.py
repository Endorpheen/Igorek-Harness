"""Boundary tests for key binding normalization (factory/core/tui/key_hint.py)."""

import unittest

from factory.core.tui.key_hint import (
    KeyBinding,
    KeyCode,
    KeyEvent,
    KeyEventKind,
    KeyModifiers,
    plain,
)


class TestKeyBoundaries18(unittest.TestCase):
    """Four key-boundary behaviors ported from the Rust reference."""

    def test_raw_symbol_31_normalizes_to_ctrl_plus_7(self):
        event = KeyEvent.new(KeyCode.Char("\x1f"), KeyModifiers.NONE)
        binding = KeyBinding.from_event(event)
        self.assertEqual(binding.key, KeyCode.Char("7"))
        self.assertEqual(binding.modifiers, KeyModifiers.CONTROL)

    def test_raw_symbol_27_is_not_converted_to_ctrl(self):
        event = KeyEvent.new(KeyCode.Char("\x1b"), KeyModifiers.NONE)
        binding = KeyBinding.from_event(event)
        self.assertEqual(binding.key, KeyCode.Char("\x1b"))
        self.assertEqual(binding.modifiers, KeyModifiers.NONE)

    def test_symbol_28_with_shift_does_not_get_implicit_ctrl(self):
        event = KeyEvent.new(KeyCode.Char("\x1c"), KeyModifiers.SHIFT)
        binding = KeyBinding.from_event(event)
        self.assertEqual(binding.modifiers, KeyModifiers.SHIFT)
        self.assertFalse(
            KeyModifiers.contains(binding.modifiers, KeyModifiers.CONTROL)
        )

    def test_repeated_press_of_other_key_does_not_trigger_binding(self):
        binding = plain(KeyCode.Char("a"))
        event = KeyEvent(
            code=KeyCode.Char("b"),
            modifiers=KeyModifiers.NONE,
            kind=KeyEventKind.Repeat,
        )
        self.assertFalse(binding.is_press(event))


if __name__ == "__main__":
    unittest.main()
