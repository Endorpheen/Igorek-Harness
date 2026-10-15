"""Tests for cli_chat_idle key bindings (Ctrl+V image paste, etc.)."""
from __future__ import annotations

import unittest
from unittest.mock import MagicMock, patch

from factory.core import cli_chat_idle


def _ensure_real_prompt_toolkit_symbols() -> None:
    """Restore real prompt_toolkit classes on cli_chat_idle in case a
    previous test patched them with fakes.

    Imports inside the function body (not at module top) so we always
    bind to the *current* real classes from prompt_toolkit, regardless
    of what some prior test patched onto cli_chat_idle.
    """
    from prompt_toolkit import PromptSession
    from prompt_toolkit.key_binding import KeyBindings
    from prompt_toolkit.styles import Style
    from prompt_toolkit.validation import Validator
    from prompt_toolkit.formatted_text import ANSI

    cli_chat_idle.PromptSession = PromptSession
    cli_chat_idle.KeyBindings = KeyBindings
    cli_chat_idle.Style = Style
    cli_chat_idle.Validator = Validator
    cli_chat_idle.ANSI = ANSI


class _FakeBuffer:
    def __init__(self, text: str = ""):
        self.text = text
        self.inserted: list[str] = []

    def insert_text(self, s: str) -> None:
        self.inserted.append(s)
        self.text += s


class _FakeKey:
    def __init__(self, arg=None):
        self.arg = arg


class _FakeEvent:
    def __init__(self, text: str = "", key_arg=None):
        self.current_buffer = _FakeBuffer(text)
        self.app = MagicMock()
        self.key_sequence = [_FakeKey(arg=key_arg)]


def _find_ctrl_v_handler(session):
    """Return the c-v handler from the PromptSession's KeyBindings."""
    kb = session.key_bindings
    for b in kb.bindings:
        if any(getattr(k, "value", str(k)) == "c-v" for k in b.keys):
            return b.handler
        if any(str(k) == "c-v" or str(k) == "ControlV" for k in b.keys):
            return b.handler
    return None


class IsFirstPressTest(unittest.TestCase):
    """The filter prevents kitty/Wayland auto-repeat from doubling the
    paste. A first press has ``arg=None``; a repeat has ``arg >= 2``.
    """

    def test_arg_none_is_first_press(self):
        event = _FakeEvent(key_arg=None)
        self.assertTrue(cli_chat_idle._is_first_press(event))

    def test_arg_one_is_first_press(self):
        event = _FakeEvent(key_arg=1)
        self.assertTrue(cli_chat_idle._is_first_press(event))

    def test_arg_two_is_repeat(self):
        event = _FakeEvent(key_arg=2)
        self.assertFalse(cli_chat_idle._is_first_press(event))

    def test_arg_high_is_repeat(self):
        event = _FakeEvent(key_arg=42)
        self.assertFalse(cli_chat_idle._is_first_press(event))

    def test_empty_key_sequence_is_first_press(self):
        # Defensive fallback: if prompt_toolkit didn't populate the
        # sequence for some reason, treat as first press.
        e = _FakeEvent()
        e.key_sequence = []
        self.assertTrue(cli_chat_idle._is_first_press(e))

    def test_no_key_sequence_attr_is_first_press(self):
        e = MagicMock(spec=[])  # no key_sequence attribute
        self.assertTrue(cli_chat_idle._is_first_press(e))


class CtrlVImagePasteTest(unittest.TestCase):
    """Mirrors Codex TUI's fixed.paste_image key binding:
    _make_prompt_session adds a c-v handler that reads the clipboard
    via _clipboard_image_path and inserts the resolved path into the
    buffer. If no image is on the clipboard, the buffer is left
    alone so the user can still paste text with Shift+Insert.
    """

    def setUp(self):
        _ensure_real_prompt_toolkit_symbols()
        self.session = cli_chat_idle._make_prompt_session()
        self.assertIsNotNone(self.session, "prompt_toolkit must be available")
        self.handler = _find_ctrl_v_handler(self.session)
        self.assertIsNotNone(self.handler, "c-v key binding must be installed")

    def _trigger(self, text: str = "", key_arg=None) -> str:
        event = _FakeEvent(text=text, key_arg=key_arg)
        self.handler(event)
        return event.current_buffer.text

    def test_ctrl_v_inserts_clipboard_path_when_image_present(self):
        with patch.object(
            cli_chat_idle, "_clipboard_image_path",
            return_value="/tmp/clip_20260604_xyz.png",
        ):
            result = self._trigger(text="hello ", key_arg=None)
        self.assertIn("📎 /tmp/clip_20260604_xyz.png", result)
        self.assertTrue(result.startswith("hello "))

    def test_ctrl_v_adds_space_separator_when_buffer_nonempty(self):
        with patch.object(
            cli_chat_idle, "_clipboard_image_path",
            return_value="/tmp/clip_x.png",
        ):
            result = self._trigger(text="hello", key_arg=None)
        self.assertTrue(result.startswith("hello 📎"))
        self.assertTrue(result.endswith(" "))

    def test_ctrl_v_does_not_prefix_space_when_buffer_empty(self):
        with patch.object(
            cli_chat_idle, "_clipboard_image_path",
            return_value="/tmp/clip_x.png",
        ):
            result = self._trigger(text="", key_arg=None)
        self.assertTrue(result.startswith("📎"))
        self.assertFalse(result.startswith(" 📎"))

    def test_ctrl_v_no_op_when_clipboard_has_no_image(self):
        with patch.object(
            cli_chat_idle, "_clipboard_image_path",
            return_value=None,
        ):
            result = self._trigger(text="existing text", key_arg=None)
        # Buffer unchanged: user keeps their text and can use
        # Shift+Insert / middle-click for plain-text paste.
        self.assertEqual(result, "existing text")

    def test_ctrl_v_does_not_fire_on_auto_repeat_event(self):
        # Regression: without the filter, kitty dispatched Press +
        # Repeat and both fired, producing two attachments with
        # different temp file names. With the filter, only the first
        # press hits the handler; repeat events are ignored.
        with patch.object(
            cli_chat_idle, "_clipboard_image_path",
            return_value="/tmp/clip_xyz.png",
        ) as mocked:
            result = self._trigger(text="", key_arg=2)
        self.assertEqual(result, "")
        mocked.assert_not_called()

    def test_two_independent_presses_each_attach_once(self):
        # Two independent Press events (separate physical key presses)
        # should each attach exactly once. The repeat between them
        # must be suppressed.
        with patch.object(
            cli_chat_idle, "_clipboard_image_path",
            return_value="/tmp/clip_a.png",
        ) as mocked:
            self._trigger(text="", key_arg=None)
            self._trigger(text="", key_arg=2)        # repeat, ignored
            self._trigger(text="", key_arg=None)     # next press
        self.assertEqual(mocked.call_count, 2)


if __name__ == "__main__":
    unittest.main()

