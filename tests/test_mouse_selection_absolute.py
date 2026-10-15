"""Tests for absolute (off-screen) mouse selection and drag auto-scroll.

Fullscreen selection stores the anchor/focus in absolute transcript
coordinates so a drag can reach rows scrolled out of the visible window;
`MouseController` scrolls the transcript when the pointer is held against
the top/bottom edge.
"""

import types
import unittest

from factory.core.tui.mouse_controller import MouseController
from factory.core.tui.mouse_frame import (
    FrameRow,
    ScreenFrame,
    extract_selection_absolute,
)


class ExtractAbsoluteTest(unittest.TestCase):
    def setUp(self):
        self.full = ["alpha", "bravo", "charlie", "delta"]

    def test_range(self):
        self.assertEqual(
            extract_selection_absolute(self.full, 0, 0, 1, 3), "alpha\nbra"
        )

    def test_reversed_range(self):
        self.assertEqual(
            extract_selection_absolute(self.full, 1, 3, 0, 0), "alpha\nbra"
        )

    def test_clamped_to_end(self):
        self.assertEqual(
            extract_selection_absolute(self.full, 0, 0, 99, 5),
            "alpha\nbravo\ncharlie\ndelta",
        )

    def test_negative_start_clamped(self):
        self.assertEqual(
            extract_selection_absolute(self.full, -5, 10, 1, 2), "alpha\nbr"
        )

    def test_empty_is_none(self):
        self.assertIsNone(extract_selection_absolute(self.full, 2, 0, 2, 0))

    def test_ansi_is_stripped(self):
        self.assertEqual(
            extract_selection_absolute(["\x1b[31mred\x1b[0m"], 0, 0, 0, 3),
            "red",
        )

    def test_no_lines_is_none(self):
        self.assertIsNone(extract_selection_absolute([], 0, 0, 1, 1))


class ScreenFrameDefaultsTest(unittest.TestCase):
    def test_defaults(self):
        frame = ScreenFrame()
        self.assertEqual(frame.window_start, 0)
        self.assertEqual(frame.full_lines, [])


class _FakeClipboard:
    def __init__(self):
        self.texts = []

    def copy(self, text):
        self.texts.append(text)
        return types.SimpleNamespace(
            success=True, message="", backend="xclip", error=None
        )


class ControllerAbsoluteSelectionTest(unittest.TestCase):
    def _make(self):
        clip = _FakeClipboard()
        controller = MouseController(
            user_enabled=True, clipboard_service=clip, notify=lambda _m: None
        )
        wheels = []
        controller._on_wheel = lambda delta: wheels.append(delta)
        return controller, clip, wheels

    def _frame(self, n_visible=3, window_start=10, total=50):
        full = [f"L{i}" for i in range(total)]
        rows = [
            FrameRow(i, full[window_start + i]) for i in range(n_visible)
        ]
        return ScreenFrame(rows=rows, window_start=window_start, full_lines=full)

    def test_drag_to_bottom_scrolls_and_copies_off_screen_rows(self):
        controller, clip, wheels = self._make()
        frame = self._frame()
        controller._on_left_press(types.SimpleNamespace(x=0, y=0), frame)
        controller._on_left_move(types.SimpleNamespace(x=0, y=2), frame)
        # bottom edge -> scroll towards newer lines (negative delta)
        self.assertTrue(any(delta < 0 for delta in wheels))
        controller._on_left_release(types.SimpleNamespace(x=4, y=2), frame)
        self.assertEqual(clip.texts[-1], "L10\nL11\nL12")

    def test_drag_to_top_scrolls_up(self):
        controller, _clip, wheels = self._make()
        frame = self._frame()
        controller._on_left_press(types.SimpleNamespace(x=0, y=2), frame)
        controller._on_left_move(types.SimpleNamespace(x=0, y=0), frame)
        self.assertTrue(any(delta > 0 for delta in wheels))

    def test_single_line_selection_slice(self):
        controller, clip, _wheels = self._make()
        frame = self._frame()
        controller._on_left_press(types.SimpleNamespace(x=1, y=1), frame)
        controller._on_left_move(types.SimpleNamespace(x=2, y=1), frame)
        controller._on_left_release(types.SimpleNamespace(x=3, y=1), frame)
        self.assertEqual(clip.texts[-1], "11")

    def test_empty_selection_is_not_copied(self):
        controller, clip, _wheels = self._make()
        frame = self._frame()
        controller._on_left_press(types.SimpleNamespace(x=2, y=1), frame)
        controller._on_left_move(types.SimpleNamespace(x=2, y=1), frame)
        controller._on_left_release(types.SimpleNamespace(x=2, y=1), frame)
        self.assertEqual(clip.texts, [])


if __name__ == "__main__":
    unittest.main()
