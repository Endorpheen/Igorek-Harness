"""Tests for key binding semantics (factory/core/tui/key_hint.py)."""

import unittest

from factory.core.tui.key_hint import (
    KeyBinding,
    KeyCode,
    KeyEvent,
    KeyEventKind,
    KeyModifiers,
    c0_control_char_to_ctrl_char,
    is_plain_text_key_event,
    is_pressed,
    plain,
)


class TestKeyBindingSemantics14(unittest.TestCase):
    def test_press_and_repeat_trigger(self) -> None:
        binding = plain(KeyCode.Char("a"))
        press = KeyEvent.new(KeyCode.Char("a"), KeyModifiers.NONE)
        repeat = KeyEvent(KeyCode.Char("a"), KeyModifiers.NONE, KeyEventKind.Repeat)

        self.assertTrue(binding.is_press(press))
        self.assertTrue(binding.is_press(repeat))
        self.assertTrue(is_pressed([binding], press))
        self.assertTrue(is_pressed([binding], repeat))

    def test_release_does_not_trigger(self) -> None:
        binding = plain(KeyCode.Char("a"))
        release = KeyEvent(KeyCode.Char("a"), KeyModifiers.NONE, KeyEventKind.Release)

        self.assertFalse(binding.is_press(release))
        self.assertFalse(is_pressed([binding], release))

    def test_uppercase_latin_equals_lowercase_with_shift(self) -> None:
        upper = KeyBinding.from_event(KeyEvent.new(KeyCode.Char("A"), KeyModifiers.NONE))
        lower_shift = KeyBinding.from_event(
            KeyEvent.new(KeyCode.Char("a"), KeyModifiers.SHIFT)
        )

        self.assertEqual(upper.key, KeyCode.Char("a"))
        self.assertEqual(upper.modifiers, KeyModifiers.SHIFT)
        self.assertEqual(lower_shift.key, KeyCode.Char("a"))
        self.assertEqual(lower_shift.modifiers, KeyModifiers.SHIFT)
        self.assertTrue(upper.is_press(KeyEvent.new(KeyCode.Char("a"), KeyModifiers.SHIFT)))

    def test_nul_becomes_ctrl_plus_space(self) -> None:
        self.assertEqual(c0_control_char_to_ctrl_char("\x00"), " ")

        binding = KeyBinding.from_event(KeyEvent.new(KeyCode.Char("\x00"), KeyModifiers.NONE))
        self.assertEqual(binding.key, KeyCode.Char(" "))
        self.assertEqual(binding.modifiers, KeyModifiers.CONTROL)

    def test_unicode_symbols_not_transformed(self) -> None:
        binding = KeyBinding.from_event(KeyEvent.new(KeyCode.Char("а"), KeyModifiers.NONE))

        self.assertEqual(binding.key, KeyCode.Char("а"))
        self.assertEqual(binding.modifiers, KeyModifiers.NONE)
        upper = KeyBinding.from_event(KeyEvent.new(KeyCode.Char("Ж"), KeyModifiers.NONE))
        self.assertEqual(upper.parts(), (KeyCode.Char("Ж"), KeyModifiers.NONE))

    def test_ctrl_and_alt_not_plain_text(self) -> None:
        ctrl_event = KeyEvent.new(KeyCode.Char("c"), KeyModifiers.CONTROL)
        alt_event = KeyEvent.new(KeyCode.Char("c"), KeyModifiers.ALT)
        plain_event = KeyEvent.new(KeyCode.Char("c"), KeyModifiers.NONE)

        self.assertFalse(is_plain_text_key_event(ctrl_event))
        self.assertFalse(is_plain_text_key_event(alt_event))
        self.assertTrue(is_plain_text_key_event(plain_event))


    def test_different_key_or_modifier_does_not_trigger(self) -> None:
        binding = plain(KeyCode.Char("a"))
        self.assertFalse(binding.is_press(KeyEvent.new(KeyCode.Char("b"), KeyModifiers.NONE)))
        self.assertFalse(binding.is_press(KeyEvent.new(KeyCode.Char("a"), KeyModifiers.CONTROL)))

    def test_empty_binding_list_does_not_trigger(self) -> None:
        self.assertFalse(is_pressed([], KeyEvent.new(KeyCode.Char("a"), KeyModifiers.NONE)))


if __name__ == "__main__":
    unittest.main()
