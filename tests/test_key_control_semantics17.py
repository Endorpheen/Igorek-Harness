"""Tests for keyboard control-character semantics (factory.core.tui.key_hint)."""

import unittest

from factory.core.tui.key_hint import (
    KeyBinding,
    KeyCode,
    KeyEvent,
    KeyModifiers,
    ctrl,
    plain,
)


class TestKeyControlSemantics(unittest.TestCase):
    """Control characters must map to the corresponding Ctrl+<letter> binding."""

    def test_control_char_1_corresponds_to_ctrl_a(self):
        # Raw C0 control character 0x01 is what terminals report for Ctrl+A.
        event = KeyEvent.new(KeyCode.Char("\x01"), KeyModifiers.NONE)
        binding = KeyBinding.from_event(event)

        # Normalized to the Ctrl+A key: Char('a') with CONTROL modifier.
        self.assertEqual(binding.key, KeyCode.Char("a"))
        self.assertEqual(binding.modifiers, KeyModifiers.CONTROL)

        # The normalized event must match a real Ctrl+A key binding.
        self.assertTrue(ctrl(KeyCode.Char("a")).is_press(event))

    def test_control_char_26_corresponds_to_ctrl_z(self):
        # Raw C0 control character 0x1A is what terminals report for Ctrl+Z.
        event = KeyEvent.new(KeyCode.Char("\x1a"), KeyModifiers.NONE)
        binding = KeyBinding.from_event(event)

        # Normalized to the Ctrl+Z key: Char('z') with CONTROL modifier.
        self.assertEqual(binding.key, KeyCode.Char("z"))
        self.assertEqual(binding.modifiers, KeyModifiers.CONTROL)

        # The normalized event must match a real Ctrl+Z key binding.
        self.assertTrue(ctrl(KeyCode.Char("z")).is_press(event))

    def test_control_char_28_corresponds_to_ctrl_4(self):
        # Raw C0 control character 0x1C is what terminals report for Ctrl+4.
        event = KeyEvent.new(KeyCode.Char("\x1c"), KeyModifiers.NONE)
        binding = KeyBinding.from_event(event)

        # Normalized to the Ctrl+4 key: Char('4') with CONTROL modifier.
        self.assertEqual(binding.key, KeyCode.Char("4"))
        self.assertEqual(binding.modifiers, KeyModifiers.CONTROL)

        # The normalized event must match a real Ctrl+4 key binding.
        self.assertTrue(ctrl(KeyCode.Char("4")).is_press(event))

    def test_escape_does_not_convert_to_control(self):
        # Escape is a distinct special key and must not be treated as a control char.
        event = KeyEvent.new(KeyCode.Esc(), KeyModifiers.NONE)
        binding = KeyBinding.from_event(event)

        self.assertEqual(binding.key, KeyCode.Esc())
        self.assertEqual(binding.modifiers, KeyModifiers.NONE)

        # Escape stays a plain Escape binding and matches an Escape event.
        self.assertTrue(plain(KeyCode.Esc()).is_press(event))


if __name__ == "__main__":
    unittest.main()
