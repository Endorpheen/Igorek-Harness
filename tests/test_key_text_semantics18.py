import unittest

from factory.core.tui.key_hint import (
    KeyCode,
    KeyEvent,
    KeyModifiers,
    is_plain_text_key_event,
)


class TestIsPlainTextKeyEvent(unittest.TestCase):
    def test_unicode_char_allowed(self):
        event = KeyEvent(code=KeyCode.Char("é"), modifiers=KeyModifiers.NONE)
        self.assertTrue(is_plain_text_key_event(event))

    def test_shift_letter_allowed(self):
        event = KeyEvent(code=KeyCode.Char("A"), modifiers=KeyModifiers.SHIFT)
        self.assertTrue(is_plain_text_key_event(event))

    def test_raw_control_char_rejected(self):
        event = KeyEvent(code=KeyCode.Char(chr(0x01)), modifiers=KeyModifiers.NONE)
        self.assertFalse(is_plain_text_key_event(event))

    def test_enter_rejected(self):
        event = KeyEvent(code=KeyCode.Enter(), modifiers=KeyModifiers.NONE)
        self.assertFalse(is_plain_text_key_event(event))


if __name__ == "__main__":
    unittest.main()
