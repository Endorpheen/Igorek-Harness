"""Short display-semantics tests for factory/core/tui/key_hint.py."""

import unittest
from types import SimpleNamespace
from unittest import mock

import factory.core.tui.key_hint as key_hint


UP = "\u2191"


class KeyDisplaySemantics16Tests(unittest.TestCase):
    def test_ctrl_shift_up_display(self):
        binding = key_hint.KeyBinding.create(
            key=key_hint.KeyCode.Up(),
            modifiers=key_hint.KeyModifiers.CONTROL | key_hint.KeyModifiers.SHIFT,
        )
        self.assertEqual(binding.display_label(), "ctrl + shift + " + UP)

    def test_space_and_pgup_names(self):
        self.assertEqual(
            key_hint.plain(key_hint.KeyCode.Char(" ")).display_label(), "space"
        )
        self.assertEqual(
            key_hint.plain(key_hint.KeyCode.PageUp()).display_label(), "pgup"
        )

    def test_ctrl_shift_order(self):
        self.assertEqual(
            key_hint.modifiers_to_string(
                key_hint.KeyModifiers.CONTROL | key_hint.KeyModifiers.SHIFT
            ),
            "ctrl + shift + ",
        )

    def test_altgr_windows_and_linux(self):
        mods = key_hint.KeyModifiers.CONTROL | key_hint.KeyModifiers.ALT
        with mock.patch.object(key_hint, "sys", SimpleNamespace(platform="win32")):
            self.assertTrue(key_hint.is_altgr(mods))
            self.assertFalse(key_hint.has_ctrl_or_alt(mods))
        with mock.patch.object(key_hint, "sys", SimpleNamespace(platform="linux")):
            self.assertFalse(key_hint.is_altgr(mods))
            self.assertTrue(key_hint.has_ctrl_or_alt(mods))


if __name__ == "__main__":
    unittest.main()
