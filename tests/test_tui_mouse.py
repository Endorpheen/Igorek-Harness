"""Tests for TUI mouse support (protocol, output, frame, controller)."""
from __future__ import annotations

import io
import os
import unittest
import threading
from types import SimpleNamespace
from unittest import mock

from prompt_toolkit.data_structures import Size
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.keys import Keys
from prompt_toolkit.output.base import DummyOutput
from prompt_toolkit.output.vt100 import Vt100_Output

from factory.core.tui.clipboard_copy import (
    ClipboardLease,
    CopyEnvironment,
    copy_to_clipboard_with,
)
from factory.core.tui.mouse_clipboard import ClipboardService, format_copy_result
from factory.core.tui.mouse_controller import (
    MouseController,
    env_mouse_forced_off,
    env_mouse_requested,
)
from factory.core.tui.mouse_frame import (
    FrameRegion,
    FrameRow,
    ScreenFrame,
    Selection,
    extract_selection,
    strip_ansi,
)
from factory.core.tui.mouse_output import (
    DISABLE_SEQUENCES,
    ENABLE_SEQUENCES,
    MouseCaptureOutput,
    wrap_output_for_mouse,
)
from factory.core.tui.mouse_protocol import (
    MouseButton,
    MouseEventKind,
    parse_sgr_mouse_sequence,
)


class SgrProtocolTest(unittest.TestCase):
    def test_left_press_absolute_zero_based(self):
        event = parse_sgr_mouse_sequence("\x1b[<0;10;5M")
        self.assertIsNotNone(event)
        self.assertIs(event.kind, MouseEventKind.PRESS)
        self.assertIs(event.button, MouseButton.LEFT)
        self.assertEqual((event.x, event.y), (9, 4))

    def test_release_move_and_wheel(self):
        self.assertIs(
            parse_sgr_mouse_sequence("\x1b[<0;10;5m").kind,
            MouseEventKind.RELEASE,
        )
        self.assertIs(
            parse_sgr_mouse_sequence("\x1b[<32;10;5M").kind,
            MouseEventKind.MOVE,
        )
        self.assertIs(
            parse_sgr_mouse_sequence("\x1b[<64;1;1M").kind,
            MouseEventKind.SCROLL_UP,
        )
        self.assertIs(
            parse_sgr_mouse_sequence("\x1b[<65;1;1M").kind,
            MouseEventKind.SCROLL_DOWN,
        )

    def test_modifiers(self):
        shift = parse_sgr_mouse_sequence("\x1b[<4;1;1M")
        self.assertTrue(shift.shift)
        ctrl = parse_sgr_mouse_sequence("\x1b[<16;1;1M")
        self.assertTrue(ctrl.ctrl)

    def test_malformed_returns_none(self):
        for bad in (
            "",
            "garbage",
            "\x1b[M !!",
            "\x1b[<0;10M",
            "\x1b[<x;1;1M",
            "\x1b[<0;0;1M",
            "\x1b[<0;1;1Z",
        ):
            self.assertIsNone(parse_sgr_mouse_sequence(bad), repr(bad))


class MouseOutputTest(unittest.TestCase):
    def _vt100(self):
        return Vt100_Output(
            io.StringIO(),
            lambda: Size(rows=24, columns=80),
            term="xterm-256color",
        )

    def test_enable_disable_sequences(self):
        out = self._vt100()
        wrapped = wrap_output_for_mouse(out)
        self.assertIsNotNone(wrapped)
        wrapped.enable_mouse_support()
        out.flush()
        written = out.stdout.getvalue()  # type: ignore[attr-defined]
        for seq in ENABLE_SEQUENCES:
            self.assertIn(seq, written)
        self.assertNotIn("\x1b[?1003h", written)
        self.assertNotIn("\x1b[?1015h", written)

    def test_disable_is_idempotent_and_flushes(self):
        out = self._vt100()
        wrapped = wrap_output_for_mouse(out)
        wrapped.enable_mouse_support()
        wrapped.disable_mouse_support()
        wrapped.disable_mouse_support()
        written = out.stdout.getvalue()  # type: ignore[attr-defined]
        for seq in DISABLE_SEQUENCES:
            self.assertIn(seq, written)

    def test_non_vt100_is_unsupported(self):
        self.assertIsNone(wrap_output_for_mouse(DummyOutput()))

    def test_proxy_delegates_attributes(self):
        out = self._vt100()
        wrapped = MouseCaptureOutput(out)
        self.assertTrue(wrapped.supports_capture)
        self.assertEqual(wrapped.get_size(), Size(rows=24, columns=80))
        self.assertTrue(hasattr(wrapped, "responds_to_cpr"))


class FrameSelectionTest(unittest.TestCase):
    def _frame(self, texts):
        return ScreenFrame(rows=[FrameRow(i, t) for i, t in enumerate(texts)])

    def test_single_line_range(self):
        frame = self._frame(["abcdef"])
        text = extract_selection(frame, Selection(0, 1, 0, 4, 0))
        self.assertEqual(text, "bcd")

    def test_multiline_forward_and_reverse(self):
        frame = self._frame(["hello", "world", "tail"])
        forward = extract_selection(frame, Selection(0, 2, 2, 2, 0))
        self.assertEqual(forward, "llo\nworld\nta")
        reverse = extract_selection(frame, Selection(2, 2, 0, 2, 0))
        self.assertEqual(reverse, forward)

    def test_ansi_is_stripped(self):
        frame = self._frame(["\x1b[31mred\x1b[0m", "\x1b[2mdim\x1b[0m"])
        text = extract_selection(frame, Selection(0, 0, 1, 3, 0))
        self.assertEqual(text, "red\ndim")
        self.assertEqual(strip_ansi("\x1b[31mred\x1b[0m"), "red")

    def test_wide_characters_not_split(self):
        frame = self._frame(["你好x"])
        # columns 0..4 cover both wide chars exactly
        text = extract_selection(frame, Selection(0, 0, 0, 4, 0))
        self.assertEqual(text, "你好")
        # ending inside the second wide char must not emit half a character
        partial = extract_selection(frame, Selection(0, 0, 0, 3, 0))
        self.assertEqual(partial, "你")

    def test_combining_marks_stay_with_base(self):
        frame = self._frame(["e\u0301xyz"])
        # "é" occupies one column; selecting it must include the combining mark
        text = extract_selection(frame, Selection(0, 0, 0, 1, 0))
        self.assertEqual(text, "e\u0301")

    def test_empty_range_returns_none(self):
        frame = self._frame(["abc"])
        self.assertIsNone(extract_selection(frame, Selection(0, 1, 0, 1, 0)))

    def test_missing_row_cancels(self):
        frame = ScreenFrame(rows=[FrameRow(0, "a"), FrameRow(2, "b")])
        self.assertIsNone(extract_selection(frame, Selection(0, 0, 2, 1, 0)))

    def test_unknown_rows_starting_point(self):
        frame = self._frame(["abc"])
        self.assertIsNone(extract_selection(frame, Selection(5, 0, 5, 1, 0)))

    def test_region_for_row_prefers_specific_zone(self):
        frame = ScreenFrame(
            rows=[FrameRow(0, "x")],
            regions={
                "out": FrameRegion("out", "output", top=0, bottom=5),
                "r": FrameRegion("r", "reasoning", top=0, bottom=0),
            },
        )
        self.assertEqual(frame.region_for_row(0).kind, "reasoning")

    def test_highlight_lines_marks_selection(self):
        from factory.core.tui.mouse_frame import highlight_lines

        out = highlight_lines(["abcdef"], Selection(0, 1, 0, 3, 0))
        self.assertIn("\x1b[7m", out[0])
        self.assertIn("b", out[0])
        self.assertIn("c", out[0])
        # empty selection leaves lines untouched
        self.assertEqual(highlight_lines(["abc"], None), ["abc"])


class ControllerPolicyTest(unittest.TestCase):
    def _frame(self):
        return ScreenFrame(
            rows=[FrameRow(0, "reasoning"), FrameRow(1, "hello"), FrameRow(2, "world")],
            regions={
                "r": FrameRegion(
                    region_id="r", kind="reasoning", top=0, bottom=0, header_row=0
                )
            },
            composer_top=5,
        )

    def _event(self, data):
        return SimpleNamespace(data=data)

    def test_default_off(self):
        controller = MouseController()
        self.assertFalse(controller.should_capture())
        self.assertEqual(controller.dispatch(self._event("\x1b[<0;1;1M")), "delegate")

    def test_enable_requires_support(self):
        controller = MouseController()
        self.assertEqual(
            controller.handle_command("/mouse on"),
            "Мышь недоступна для текущего терминала/вывода.",
        )
        self.assertFalse(controller.should_capture())
        controller.configure_support(True)
        self.assertEqual(controller.handle_command("/mouse on"), "Мышь включена.")
        self.assertTrue(controller.should_capture())

    def test_forced_off_env(self):
        import os

        old = os.environ.get("IGOREK_TUI_MOUSE")
        os.environ["IGOREK_TUI_MOUSE"] = "0"
        try:
            self.assertTrue(env_mouse_forced_off())
            controller = MouseController()
            controller.configure_support(True)
            self.assertFalse(controller.enable())
            self.assertIn("принудительно", controller.handle_command("/mouse on"))
        finally:
            if old is None:
                os.environ.pop("IGOREK_TUI_MOUSE", None)
            else:
                os.environ["IGOREK_TUI_MOUSE"] = old

    def test_command_status_syntax(self):
        controller = MouseController()
        controller.configure_support(True)
        self.assertIsNotNone(controller.handle_command("/mouse off"))
        self.assertIn("Использование", controller.handle_command("/mouse bogus"))
        self.assertIn("Mouse:", controller.handle_command("/mouse status"))

    def test_reasoning_click_calls_toggle(self):
        calls = []
        controller = MouseController(on_toggle_reasoning=lambda: calls.append(1))
        controller.configure_support(True)
        controller.enable()
        controller.set_frame_builder(lambda: self._frame())
        self.assertEqual(controller.dispatch(self._event("\x1b[<0;2;1M")), "handled")
        self.assertEqual(controller.dispatch(self._event("\x1b[<0;2;1m")), "handled")
        self.assertEqual(calls, [1])

    def test_reasoning_click_targets_specific_cell(self):
        from factory.core.tui.mouse_frame import ScreenFrame, FrameRow, FrameRegion

        per_cell = []
        global_calls = []
        controller = MouseController(
            on_toggle_reasoning=lambda: global_calls.append(1),
            on_toggle_reasoning_cell=lambda cid: per_cell.append(cid),
        )
        controller.configure_support(True)
        controller.enable()
        frame = ScreenFrame(
            rows=[FrameRow(0, "reasoning"), FrameRow(1, "hello"), FrameRow(2, "world")],
            regions={
                "r": FrameRegion(
                    region_id="r",
                    kind="reasoning",
                    top=0,
                    bottom=0,
                    cell_id="reasoning-3",
                    expandable=True,
                    header_row=0,
                )
            },
            composer_top=5,
        )
        controller.set_frame_builder(lambda: frame)
        controller.dispatch(self._event("\x1b[<0;2;1M"))  # press row 0
        controller.dispatch(self._event("\x1b[<0;2;1m"))  # release
        self.assertEqual(per_cell, ["reasoning-3"])
        self.assertEqual(global_calls, [])

    def test_selection_copy(self):
        captured = []

        def fake_copy(text):
            captured.append(text)
            return None, None, "xclip"

        service = ClipboardService(copy_fn=fake_copy)
        completed = threading.Event()
        controller = MouseController(clipboard_service=service, notify=lambda _: completed.set())
        controller.configure_support(True)
        controller.enable()
        controller.set_frame_builder(lambda: self._frame())
        controller.dispatch(self._event("\x1b[<0;1;2M"))  # press row 1 col 0
        controller.dispatch(self._event("\x1b[<32;6;3M"))  # drag to row 2 col 5
        controller.dispatch(self._event("\x1b[<0;6;3m"))  # release
        self.assertTrue(completed.wait(2))
        self.assertEqual(captured, ["hello\nworld"])

    def test_composer_release_sets_pending_copy(self):
        controller = MouseController()
        controller.configure_support(True)
        controller.enable()
        controller.set_frame_builder(lambda: self._frame())
        controller.dispatch(self._event("\x1b[<0;2;6M"))  # press row 5 (composer)
        controller.dispatch(self._event("\x1b[<0;2;6m"))  # release
        self.assertTrue(controller._pending_composer_copy)

    def test_composer_selection_is_copied_on_release(self):
        captured = []

        def fake_copy(text):
            captured.append(text)
            return None, None, "xclip"

        service = ClipboardService(copy_fn=fake_copy)
        completed = threading.Event()
        controller = MouseController(clipboard_service=service, notify=lambda _: completed.set())

        class _Doc:
            text = "selected composer text"

        class _Buf:
            selection_state = object()
            def copy_selection(self):
                return _Doc()

        class _App:
            current_buffer = _Buf()
            def invalidate(self):
                pass

        controller._app = _App()
        controller._copy_composer_selection()
        self.assertTrue(completed.wait(2))
        self.assertEqual(captured, ["selected composer text"])

    def test_normalize_no_button_motion_during_delegated_drag(self):
        controller = MouseController()
        controller.gesture.owner = "delegate"
        event = SimpleNamespace(data="\x1b[<35;8;5M")
        normalized = controller._normalize_delegated_drag_event(event)
        self.assertEqual(normalized.data, "\x1b[<32;8;5M")

    def test_normalize_leaves_motion_when_not_dragging(self):
        controller = MouseController()
        event = SimpleNamespace(data="\x1b[<35;8;5M")
        self.assertIs(controller._normalize_delegated_drag_event(event), event)

    def test_composer_without_selection_is_not_copied(self):
        captured = []

        def fake_copy(text):
            captured.append(text)
            return None, None, "xclip"

        service = ClipboardService(copy_fn=fake_copy)
        controller = MouseController(clipboard_service=service)

        class _Buf:
            selection_state = None

        class _App:
            current_buffer = _Buf()

        controller._app = _App()
        controller._copy_composer_selection()
        self.assertEqual(captured, [])

    def test_wheel_delegates_and_does_not_open_viewer(self):
        controller = MouseController()
        controller.configure_support(True)
        controller.enable()
        controller.set_frame_builder(lambda: self._frame())
        self.assertEqual(controller.dispatch(self._event("\x1b[<64;2;1M")), "delegate")
        self.assertFalse(controller.consume_viewer_request())

    def test_wheel_in_composer_delegates(self):
        controller = MouseController()
        controller.configure_support(True)
        controller.enable()
        controller.set_frame_builder(lambda: self._frame())
        self.assertEqual(controller.dispatch(self._event("\x1b[<64;2;6M")), "delegate")

    def test_wheel_handler_scrolls_in_app(self):
        controller = MouseController()
        controller.configure_support(True)
        controller.enable()
        controller.set_frame_builder(lambda: self._frame())
        called = []
        controller.set_wheel_handler(lambda d: called.append(d))
        self.assertEqual(controller.dispatch(self._event("\x1b[<64;2;1M")), "handled")
        self.assertEqual(controller.dispatch(self._event("\x1b[<65;2;1M")), "handled")
        self.assertEqual(called, [1, -1])
        controller.set_wheel_handler(None)
        self.assertEqual(controller.dispatch(self._event("\x1b[<64;2;1M")), "delegate")

    def test_wheel_lines_env_override(self):
        with mock.patch.dict(os.environ, {"IGOREK_TUI_WHEEL_LINES": "4"}):
            controller = MouseController()
        controller.configure_support(True)
        controller.enable()
        controller.set_frame_builder(lambda: self._frame())
        called = []
        controller.set_wheel_handler(lambda d: called.append(d))
        controller.dispatch(self._event("\x1b[<64;2;1M"))
        controller.dispatch(self._event("\x1b[<65;2;1M"))
        self.assertEqual(called, [4, -4])

    def test_reassert_capture_reenables_after_nested_prompt(self):
        controller = MouseController()
        controller.configure_support(True)
        controller.enable()
        calls = []
        controller._capture_output = SimpleNamespace(
            enable_mouse_support=lambda: calls.append("on")
        )
        controller.reassert_capture()
        self.assertEqual(calls, ["on"])

    def test_reassert_capture_noop_when_capture_disallowed(self):
        controller = MouseController()
        controller.configure_support(True)
        calls = []
        controller._capture_output = SimpleNamespace(
            enable_mouse_support=lambda: calls.append("on")
        )
        controller.reassert_capture()
        self.assertEqual(calls, [])

    def test_legacy_protocol_delegates(self):
        controller = MouseController()
        controller.configure_support(True)
        controller.enable()
        controller.set_frame_builder(lambda: self._frame())
        self.assertEqual(controller.dispatch(self._event("\x1b[M !!")), "delegate")

    def test_binding_delegates_once_when_off(self):
        controller = MouseController()
        calls = []
        controller._standard_handler = lambda event: calls.append(event)
        kb = KeyBindings()
        controller.install_bindings(kb)
        bindings = kb.get_bindings_for_keys((Keys.Vt100MouseEvent,))
        self.assertEqual(len(bindings), 1)
        binding = bindings[-1]
        event = SimpleNamespace(data="\x1b[<0;1;1M")
        binding.handler(event)
        self.assertEqual(len(calls), 1)

    def test_binding_handles_without_delegation(self):
        controller = MouseController()
        calls = []
        controller._standard_handler = lambda event: calls.append(event)
        controller.configure_support(True)
        controller.enable()
        controller.set_frame_builder(lambda: self._frame())
        kb = KeyBindings()
        controller.install_bindings(kb)
        binding = kb.get_bindings_for_keys((Keys.Vt100MouseEvent,))[-1]
        binding.handler(SimpleNamespace(data="\x1b[<0;2;1M"))
        self.assertEqual(calls, [])

    def test_real_key_processor_delegates_once(self):
        import asyncio

        from prompt_toolkit.application import Application
        from prompt_toolkit.key_binding.key_processor import KeyPress
        from prompt_toolkit.layout import Layout, Window
        from prompt_toolkit.layout.controls import FormattedTextControl

        calls = []
        controller = MouseController()
        controller._standard_handler = lambda event: calls.append(event)
        kb = KeyBindings()
        controller.install_bindings(kb)
        app = Application(
            layout=Layout(Window(FormattedTextControl("x"))), key_bindings=kb
        )

        async def run(data):
            app.key_processor.feed(KeyPress(Keys.Vt100MouseEvent, data))
            app.key_processor.process_keys()
            await asyncio.sleep(0.01)

        asyncio.run(run("\x1b[<0;1;1M"))
        self.assertEqual(len(calls), 1)

        controller.configure_support(True)
        controller.enable()
        controller.set_frame_builder(lambda: self._frame())
        calls.clear()
        # Plain left press in the managed area is handled by the controller.
        asyncio.run(run("\x1b[<0;2;1M"))
        self.assertEqual(len(calls), 0)

    def test_interceptor_binding_has_priority(self):
        from prompt_toolkit.application import Application
        from prompt_toolkit.layout import Layout, Window
        from prompt_toolkit.layout.controls import FormattedTextControl

        controller = MouseController()
        kb = KeyBindings()
        controller.install_bindings(kb)
        app = Application(
            layout=Layout(Window(FormattedTextControl("x"))),
            key_bindings=kb,
            mouse_support=True,
        )
        merged = app.key_processor._bindings
        matches = merged.get_bindings_for_keys((Keys.Vt100MouseEvent,))
        self.assertGreaterEqual(len(matches), 2)
        self.assertIs(matches[-1].handler, kb.get_bindings_for_keys((Keys.Vt100MouseEvent,))[-1].handler)


class ClipboardFallbackTest(unittest.TestCase):
    def _env(self, **kwargs):
        return CopyEnvironment(
            ssh_session=kwargs.get("ssh", False),
            wsl_session=kwargs.get("wsl", False),
            tmux_session=kwargs.get("tmux", False),
        )

    def test_linux_fallback_used_after_arboard(self):
        order = []

        def arboard(text):
            order.append("arboard")
            return None, "no arboard"

        def linux(text):
            order.append("linux")
            return ClipboardLease.test(), None, "wl-copy"

        lease, err = copy_to_clipboard_with(
            "hello",
            self._env(),
            lambda t: None,
            lambda t: None,
            arboard,
            lambda t: None,
            linux,
        )
        self.assertIsNone(err)
        self.assertEqual(order, ["arboard", "linux"])

    def test_linux_error_falls_back_to_terminal(self):
        order = []

        def arboard(text):
            order.append("arboard")
            return None, "no arboard"

        def linux(text):
            order.append("linux")
            return None, "no wl-copy", None

        def osc(text):
            order.append("osc")
            return None

        lease, err = copy_to_clipboard_with(
            "hello",
            self._env(),
            lambda t: None,
            osc,
            arboard,
            lambda t: None,
            linux,
        )
        self.assertIsNone(err)
        self.assertEqual(order, ["arboard", "linux", "osc"])

    def test_ssh_uses_terminal_only(self):
        order = []

        def arboard(text):
            order.append("arboard")
            return None, "unused"

        copy_to_clipboard_with(
            "hello",
            self._env(ssh=True),
            lambda t: None,
            lambda t: order.append("osc") or None,
            arboard,
            lambda t: None,
            lambda t: order.append("linux"),  # not a triple: should not be called
        )
        self.assertEqual(order, ["osc"])

    def test_notification_text_is_honest(self):
        success, message = format_copy_result("a\nb", "arboard", None)
        self.assertTrue(success)
        self.assertEqual(message, "Скопировано: 2 строк")
        success, message = format_copy_result("a", "osc52", None)
        self.assertTrue(success)
        self.assertIn("буфер терминала", message)
        success, message = format_copy_result("a", None, "boom")
        self.assertFalse(success)
        self.assertIn("boom", message)


class ViewerTest(unittest.TestCase):
    def _controller(self):
        controller = MouseController()
        controller.configure_support(True)
        controller.enable()
        return controller

    def test_scroll_is_bounded(self):
        from factory.core.tui.mouse_viewer import MouseHistoryViewer

        viewer = MouseHistoryViewer(["l%d" % i for i in range(50)], controller=self._controller())
        viewer.offset = 0
        viewer.scroll(-10)
        self.assertEqual(viewer.offset, 0)
        viewer.scroll(10_000)
        self.assertGreaterEqual(viewer.offset, 0)
        viewer.to_top()
        self.assertEqual(viewer.offset, 0)
        viewer.to_bottom()
        self.assertEqual(viewer.offset, viewer._max_offset())

    def test_item_toggle_collapsed_expanded(self):
        from factory.core.tui.mouse_viewer import MouseHistoryViewer, ViewerItem

        controller = self._controller()
        item = ViewerItem(
            cell_id="t1",
            lines_collapsed=["Ran Bash", "… +3 lines"],
            lines_expanded=["Ran Bash", "a", "b", "c"],
            expandable=True,
        )
        viewer = MouseHistoryViewer(items=[item], controller=controller)
        viewer._visible_height = 20
        self.assertEqual(len(viewer._build_content()[0]), 2)
        viewer._handle_click(0, 0)
        self.assertTrue(controller.is_cell_expanded("t1"))
        self.assertEqual(len(viewer._build_content()[0]), 4)
        viewer._handle_click(0, 0)
        self.assertFalse(controller.is_cell_expanded("t1"))

    def test_plain_lines_are_not_zones(self):
        from factory.core.tui.mouse_viewer import MouseHistoryViewer

        viewer = MouseHistoryViewer(
            ["ordinary text", "… +5 lines"], controller=self._controller()
        )
        viewer._visible_height = 10
        frame = viewer._build_frame()
        self.assertEqual(frame.regions, {})
        self.assertIsNone(frame.region_for_row(1))

    def test_click_without_items_does_nothing(self):
        from factory.core.tui.mouse_viewer import MouseHistoryViewer

        viewer = MouseHistoryViewer(["hello"], controller=self._controller())
        viewer._visible_height = 5
        viewer._handle_click(0, 0)  # must not raise


class ToolRenderExpansionTest(unittest.TestCase):
    def _output(self, n):
        return "\n".join(f"line {i}" for i in range(n))

    def test_collapsed_render_is_unchanged(self):
        from factory.core.tui.exec_cell.render import (
            EXEC_OUTPUT_MAX_LINES,
            render_tool_call,
        )

        collapsed = render_tool_call("Bash", {"command": "x"}, "done", 0.1, self._output(9), False, width=80)
        self.assertEqual(
            len([l for l in collapsed if "line " in l]),
            EXEC_OUTPUT_MAX_LINES,
        )
        self.assertTrue(any(f"+{9 - EXEC_OUTPUT_MAX_LINES} lines" in l for l in collapsed))
        # expanded=False must equal the default call
        self.assertEqual(
            collapsed,
            render_tool_call("Bash", {"command": "x"}, "done", 0.1, self._output(9), False, width=80),
        )

    def test_expanded_render_shows_full_output(self):
        from factory.core.tui.exec_cell.render import render_tool_call

        expanded = render_tool_call(
            "Bash", {"command": "x"}, "done", 0.1, self._output(9), False, width=80, expanded=True
        )
        self.assertEqual(len([l for l in expanded if "line " in l]), 9)
        self.assertFalse(any("lines" in l and "…" in l for l in expanded))

    def test_two_cells_expand_independently(self):
        controller = MouseController()
        controller.configure_support(True)
        controller.enable()
        controller.toggle_cell("a")
        self.assertTrue(controller.is_cell_expanded("a"))
        self.assertFalse(controller.is_cell_expanded("b"))


class SupportDetectionTest(unittest.TestCase):
    def test_term_dumb_disables_support(self):
        import os

        old = os.environ.get("TERM")
        os.environ["TERM"] = "dumb"
        try:
            self.assertFalse(MouseController._terminal_supported(object()))
        finally:
            if old is None:
                os.environ.pop("TERM", None)
            else:
                os.environ["TERM"] = old

    def test_none_output_unsupported(self):
        self.assertFalse(MouseController._terminal_supported(None))


class AppIntegrationTest(unittest.TestCase):
    def test_idle_app_installs_mouse_binding(self):
        from factory.core.cli_chat_idle import _create_idle_composer_application

        controller = MouseController()
        app, _buffer = _create_idle_composer_application(mouse_controller=controller)
        if app is None:
            self.skipTest("prompt_toolkit unavailable")
        # The idle app still builds with the default-off controller.
        self.assertFalse(controller.should_capture())
        self.assertIsNotNone(app)

    def test_active_app_installs_mouse_binding(self):
        from factory.core.cli_chat_active import _create_chat_application

        controller = MouseController()
        app, _buffer, _lines = _create_chat_application(
            running_state={"task_active": True, "t0": 0.0},
            mouse_controller=controller,
        )
        if app is None:
            self.skipTest("prompt_toolkit unavailable")
        self.assertFalse(controller.should_capture())


class CopyPipeTest(unittest.TestCase):
    def test_slash_pool_has_tui_and_mouse(self):
        from factory.core.tui.bottom_pane.command_popup import _COMMANDS

        names = {cmd for cmd, _desc in _COMMANDS}
        self.assertIn("/tui", names)
        self.assertIn("/mouse", names)

    def test_run_pipe_command_wait_true_no_value_error(self):
        from factory.core.tui.clipboard_copy import _run_pipe_command

        _proc, err = _run_pipe_command(["cat"], "hello", timeout=2.0, wait=True)
        self.assertIsNone(err)

    def test_run_pipe_command_wait_false_returns_running_process(self):
        from factory.core.tui.clipboard_copy import _run_pipe_command

        proc, err = _run_pipe_command(["cat"], "hi", timeout=2.0, wait=False)
        self.assertIsNone(err)
        self.assertIsNotNone(proc)
        try:
            proc.kill()
        except Exception:
            pass
        try:
            proc.wait(timeout=2)
        except Exception:
            pass

    def test_resolve_image_path_long_text_returns_none(self):
        from factory.core.cli_chat_rich_shared import _resolve_image_path_from_text

        self.assertIsNone(_resolve_image_path_from_text("x" * 5000))
        self.assertIsNone(_resolve_image_path_from_text("" ))
        self.assertIsNone(_resolve_image_path_from_text("/no/such/file.png"))


class FullscreenModeTest(unittest.TestCase):
    def _cs(self, committed=()):
        from factory.core.chat_session import ChatSession
        from factory.core.tui.chatwidget import ChatWidget
        from types import SimpleNamespace

        cs = object.__new__(ChatSession)
        cs.widget = ChatWidget(model="t", branch="b", cwd="/")
        cs.reasoning_state = {"visible": False, "lines": [], "expanded_lines": []}
        cs._tui_fullscreen = False
        cs._fs_scroll = {"offset": 0, "follow": True}
        cs.console = SimpleNamespace(print=lambda *a, **k: None)
        cs._mouse_controller = SimpleNamespace(
            set_notification=lambda *a, **k: None,
            is_cell_expanded=lambda *a, **k: False,
        )
        for cell in committed:
            cs.widget.committed_cells.append(cell)
        return cs

    def test_cmd_tui_toggles_and_reports(self):
        cs = self._cs()
        cs._cmd_tui("/tui fullscreen")
        self.assertTrue(cs._tui_fullscreen)
        cs._cmd_tui("/tui default")
        self.assertFalse(cs._tui_fullscreen)

    def test_fullscreen_view_follows_bottom(self):
        from factory.core.tui.cells.history_cell import TextCell

        cs = self._cs([TextCell("\n".join(f"line{i}" for i in range(200)))])
        lines, height, total, offset = cs._fullscreen_view()
        self.assertEqual(total, 200)
        self.assertGreater(height, 0)
        self.assertEqual(offset, 0)
        self.assertEqual(lines[-1], "line199")
        self.assertEqual(len(lines), height)

    def test_fullscreen_scroll_by_and_edges(self):
        from factory.core.tui.cells.history_cell import TextCell

        cs = self._cs([TextCell("\n".join(f"line{i}" for i in range(200)))])
        cs._fs_scroll_by(5)
        _lines, _h, _t, offset = cs._fullscreen_view()
        self.assertEqual(offset, 5)
        cs._fs_scroll_edge("bottom")
        self.assertEqual(cs._fs_scroll["offset"], 0)
        self.assertTrue(cs._fs_scroll["follow"])
        cs._fs_scroll_edge("top")
        self.assertFalse(cs._fs_scroll["follow"])
        self.assertGreater(cs._fs_scroll["offset"], 0)

    def test_cmd_tui_accepts_slash_form(self):
        cs = self._cs()
        cs._cmd_tui("/tui/fullscreen")
        self.assertTrue(cs._tui_fullscreen)
        cs._cmd_tui("/tui/default")
        self.assertFalse(cs._tui_fullscreen)

    def test_fullscreen_regions_expose_reasoning_span(self):
        from factory.core.tui.cells.history_cell import ReasoningCell, MarkdownCell

        cs = self._cs(
            [ReasoningCell("a\nb"), MarkdownCell("hello")]
        )
        cs.reasoning_state = {
            "visible": False,
            "lines": ["x"],
            "expanded_lines": ["x"],
        }
        # _cs resets reasoning_state; assign before building regions
        regions = cs._fullscreen_regions()
        kinds = [r["kind"] for r in regions]
        self.assertIn("reasoning", kinds)
        for r in regions:
            self.assertGreaterEqual(r["start"], 0)
            self.assertGreaterEqual(r["end"], r["start"])

    def test_fullscreen_transcript_inserts_spacing(self):
        from factory.core.tui.cells.history_cell import MarkdownCell, ToolCallCell

        cs = self._cs(
            [
                MarkdownCell("hello"),
                ToolCallCell(
                    "Read", {"file_path": "/x"}, status="done", elapsed=0.1, output="out"
                ),
            ]
        )
        lines, spans = cs._fullscreen_transcript_data()
        self.assertIn("", lines)
        self.assertTrue(any(s["kind"] == "tool" for s in spans))

    def test_reverse_ansi_maps_to_style(self):
        from factory.core.cli_chat_rich_shared import _ansi_to_formatted_text

        frags = _ansi_to_formatted_text("\x1b[7mX\x1b[0m")
        styles = " ".join(style for style, _text in frags)
        self.assertIn("reverse", styles)

    def test_all_ansi_mapped_styles_are_valid(self):
        from prompt_toolkit.styles.style import _parse_style_str

        from factory.core.cli_chat_rich_shared import (
            _ANSI_STYLE_MAP,
            _STANDARD_16_BG,
            _STANDARD_16_FG,
        )

        styles = list(_ANSI_STYLE_MAP.values()) + list(_STANDARD_16_FG) + list(_STANDARD_16_BG)
        for style in styles:
            if not style:
                continue
            for token in style.split():
                _parse_style_str(token)  # must not raise

    def test_fullscreen_transcript_includes_user_cell(self):
        from factory.core.tui.cells.history_cell import TextCell

        cs = self._cs([TextCell("hello user", role="user")])
        lines, _spans = cs._fullscreen_transcript_data()
        self.assertTrue(any("hello user" in line for line in lines))

    def test_fullscreen_transcript_wraps_long_assistant_markdown(self):
        import re

        from factory.core.chat_session import _terminal_width
        from factory.core.tui.cells.history_cell import MarkdownCell

        cs = self._cs([MarkdownCell(" ".join(["слово"] * 60))])
        lines, _spans = cs._fullscreen_transcript_data()
        width = _terminal_width()
        ansi = re.compile(r"\033\[[0-9;]*[A-Za-z]")
        plain = [ansi.sub("", line) for line in lines]
        self.assertTrue(any(line.strip() for line in plain))
        self.assertTrue(
            all(len(line) <= width for line in plain),
            (width, max(len(line) for line in plain)),
        )

    def test_user_message_has_marker_and_background(self):
        from factory.core.tui.cells.history_cell import TextCell

        cs = self._cs([TextCell("hello user", role="user")])
        lines, _spans = cs._fullscreen_transcript_data()
        joined = "\n".join(lines)
        self.assertIn("›", joined)
        self.assertIn("48;2;", joined)

    def _session_for_switch(self):
        import json
        from types import SimpleNamespace

        from factory.core.cli_chat import _session_path, generate_session_name

        target = generate_session_name("chat-test-resume")
        path = _session_path(target)
        self.addCleanup(lambda: path.unlink(missing_ok=True))
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(json.dumps({"type": "meta", "session_id": target,
                                 "profile": "cc-glm", "model": "m", "provider": "p",
                                 "cwd": "/", "git_branch": "b"}) + "\n")
            fh.write(json.dumps({"role": "user", "text": "старый вопрос", "ts": 1}) + "\n")
            fh.write(json.dumps({"role": "assistant",
                                 "text": "старый ответ из выбранной сессии",
                                 "ts": 2, "status": "ok"}) + "\n")

        cs = self._cs([])
        cs._tui_fullscreen = True
        cs.model = "m"
        cs.branch = "b"
        cs.cwd = "/"
        cs.session_name = "chat-current-test"
        cs.session_path = _session_path("chat-current-test")
        cs.sl = SimpleNamespace(
            session_name="", session_path=None, task_ids=[], refresh=lambda: ""
        )
        cs._emitted_line_count = 0
        cs.shown_edit_diff_signatures = set()
        cs.last_worked_for = ""
        cs.pending_inputs = []
        cs.approved_session_rules = []
        cs._fs_submitted = []
        cs._fs_history_cells = []
        return cs, target

    def test_resume_switches_session_in_process(self):
        cs, target = self._session_for_switch()

        cs._cmd_resume(f"/resume {target}")

        self.assertEqual(cs.session_name, target)
        self.assertTrue(cs.session_path.name.startswith(target))
        lines, _spans = cs._fullscreen_transcript_data()
        joined = "\n".join(lines)
        self.assertIn("старый ответ из выбранной сессии", joined)

    def test_switch_session_rejects_missing_and_current(self):
        cs, target = self._session_for_switch()

        self.assertFalse(cs._switch_session("chat-does-not-exist-xyz"))
        self.assertEqual(cs.session_name, "chat-current-test")
        self.assertFalse(cs._switch_session("chat-current-test"))

    def _write_session_with_turns(self):
        import json
        from types import SimpleNamespace

        from factory.core.cli_chat import _session_path, generate_session_name

        target = generate_session_name("chat-test-reasoning")
        path = _session_path(target)
        self.addCleanup(lambda: path.unlink(missing_ok=True))
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(json.dumps({"type": "meta", "session_id": target,
                                 "profile": "cc-glm", "model": "m", "provider": "p",
                                 "cwd": "/", "git_branch": "b"}) + "\n")
            fh.write(json.dumps({"role": "user", "text": "вопрос 1", "ts": 1}) + "\n")
            fh.write(json.dumps({"role": "assistant", "text": "ответ 1", "ts": 2,
                                 "task_id": "t1", "status": "ok"}) + "\n")
            fh.write(json.dumps({"role": "user", "text": "вопрос 2", "ts": 3}) + "\n")
            fh.write(json.dumps({"role": "assistant", "text": "ответ 2", "ts": 4,
                                 "task_id": "t2", "status": "ok"}) + "\n")

        cs = self._cs([])
        cs._tui_fullscreen = True
        cs.session_path = path
        cs.sl = SimpleNamespace(
            session_name="", session_path=None, task_ids=[], refresh=lambda: ""
        )
        return cs

    def test_resume_interleaves_reasoning_in_original_positions(self):
        from factory.core.chat_session import ChatSession
        from factory.core.tui.cells.history_cell import MarkdownCell, ReasoningCell

        cs = self._write_session_with_turns()

        def fake_cells(task_id):
            if not task_id:
                return []
            return [
                ReasoningCell(f"reasoning {task_id}"),
                MarkdownCell(f"answer {task_id}"),
            ]

        with mock.patch.object(
            ChatSession, "_trace_cells_for_task", lambda self, tid: fake_cells(tid)
        ):
            cs._seed_fullscreen_history_from_session()

        names = [type(c).__name__ for c in cs._fs_history_cells]
        self.assertEqual(
            names,
            [
                "TextCell",
                "ReasoningCell",
                "MarkdownCell",
                "TextCell",
                "ReasoningCell",
                "MarkdownCell",
            ],
        )

    def test_resume_renders_tool_calls_from_trace(self):
        from factory.core.chat_session import ChatSession
        from factory.core.tui.cells.history_cell import (
            MarkdownCell,
            ReasoningCell,
            ToolCallCell,
        )

        cs = self._write_session_with_turns()

        def fake_cells(task_id):
            if task_id == "t1":
                return [
                    ReasoningCell("думаю раз"),
                    ToolCallCell(
                        "Bash",
                        {"command": "git status"},
                        status="done",
                        elapsed=0.1,
                        output="ok",
                    ),
                    ReasoningCell("думаю два"),
                    MarkdownCell("ответ 1"),
                ]
            return [ReasoningCell("думаю три"), MarkdownCell("ответ 2")]

        with mock.patch.object(
            ChatSession,
            "_trace_cells_for_task",
            lambda self, tid: fake_cells(tid) if tid else [],
        ):
            cs._seed_fullscreen_history_from_session()

        names = [type(c).__name__ for c in cs._fs_history_cells]
        self.assertEqual(
            names,
            [
                "TextCell",
                "ReasoningCell",
                "ToolCallCell",
                "ReasoningCell",
                "MarkdownCell",
                "TextCell",
                "ReasoningCell",
                "MarkdownCell",
            ],
        )
        _lines, spans = cs._fullscreen_transcript_data()
        self.assertTrue(any(s["kind"] == "tool" for s in spans))

    def test_reasoning_click_expands_only_selected_block(self):
        from factory.core.chat_session import ChatSession
        from factory.core.tui.cells.history_cell import MarkdownCell, ReasoningCell

        cs = self._write_session_with_turns()

        def fake_cells(task_id):
            if not task_id:
                return []
            return [
                ReasoningCell(f"reason {task_id}"),
                MarkdownCell(f"answer {task_id}"),
            ]

        with mock.patch.object(
            ChatSession, "_trace_cells_for_task", lambda self, tid: fake_cells(tid)
        ):
            cs._seed_fullscreen_history_from_session()

        # All blocks collapsed by default (placeholder only).
        lines, spans = cs._fullscreen_transcript_data()
        joined = "\n".join(lines)
        self.assertNotIn("reason t1", joined)
        self.assertNotIn("reason t2", joined)
        reasoning_spans = [s for s in spans if s["kind"] == "reasoning"]
        self.assertEqual(
            [s["cell_id"] for s in reasoning_spans],
            ["reasoning-1", "reasoning-4"],
        )
        self.assertTrue(all(s["expandable"] for s in reasoning_spans))

        # Expanding one block must not expand the other.
        cs._mouse_toggle_reasoning_cell("reasoning-1")
        joined = "\n".join(cs._fullscreen_transcript_data()[0])
        self.assertIn("reason t1", joined)
        self.assertNotIn("reason t2", joined)

    def test_global_reasoning_toggle_clears_overrides(self):
        from factory.core.chat_session import ChatSession
        from factory.core.tui.cells.history_cell import MarkdownCell, ReasoningCell

        cs = self._write_session_with_turns()
        with mock.patch.object(
            ChatSession,
            "_trace_cells_for_task",
            lambda self, tid: [ReasoningCell(f"reason {tid}"), MarkdownCell(f"answer {tid}")]
            if tid
            else [],
        ):
            cs._seed_fullscreen_history_from_session()

        cs._mouse_toggle_reasoning_cell("reasoning-1")
        self.assertIn("reason t1", "\n".join(cs._fullscreen_transcript_data()[0]))

        cs._mouse_toggle_reasoning()
        joined = "\n".join(cs._fullscreen_transcript_data()[0])
        self.assertIn("reason t1", joined)
        self.assertIn("reason t2", joined)

    def test_fullscreen_transcript_keeps_previous_turns(self):
        from factory.core.tui.cells.history_cell import TextCell

        cs = self._cs([TextCell("new turn")])
        cs._fs_history_cells = [TextCell("old turn")]
        lines, _spans = cs._fullscreen_transcript_data()
        joined = "\n".join(lines)
        self.assertIn("old turn", joined)
        self.assertIn("new turn", joined)

    def test_fullscreen_composer_defines_visible_selected_style(self):
        from factory.core.cli_chat_active import _CHAT_COMPOSER_STYLE

        self.assertIn("selected", _CHAT_COMPOSER_STYLE)
        self.assertTrue(_CHAT_COMPOSER_STYLE["selected"].strip())

    def test_fullscreen_app_persistent_flag_builds(self):
        from factory.core.cli_chat_active import _create_chat_application

        submitted = []
        app, _buffer, _lines = _create_chat_application(
            running_state={"task_active": False},
            fullscreen=True,
            transcript_provider=lambda: ["a"],
            persistent=True,
            on_submit=submitted.append,
        )
        if app is None:
            self.skipTest("prompt_toolkit unavailable")
        self.assertTrue(app.full_screen)

    def test_fs_run_on_terminal_fallback_without_app(self):
        from factory.core.chat_session import ChatSession

        cs = object.__new__(ChatSession)
        self.assertEqual(cs._fs_run_on_terminal(lambda: 42), 42)

    def test_fullscreen_shows_restored_reasoning_cell(self):
        from factory.core.tui.cells.history_cell import RestoredReasoningCell

        cs = self._cs([RestoredReasoningCell(["collapsed-r"], ["expanded-r"])])
        cs._fs_history_cells = list(cs.widget.committed_cells)
        cs.widget.committed_cells.clear()
        lines, _spans = cs._fullscreen_transcript_data()
        self.assertIn("collapsed-r", "\n".join(lines))
        regions = cs._fullscreen_regions()
        self.assertTrue(any(r["kind"] == "reasoning" for r in regions))
        cs.reasoning_state["visible"] = True
        lines, _spans = cs._fullscreen_transcript_data()
        self.assertIn("expanded-r", "\n".join(lines))

    def test_fullscreen_app_is_full_screen(self):
        from factory.core.cli_chat_active import _create_chat_application

        app, _buffer, _lines = _create_chat_application(
            running_state={"task_active": False},
            fullscreen=True,
            transcript_provider=lambda: ["a", "b"],
            on_scroll=lambda d: None,
            on_scroll_edge=lambda w: None,
        )
        if app is None:
            self.skipTest("prompt_toolkit unavailable")
        self.assertTrue(app.full_screen)


if __name__ == "__main__":
    unittest.main()


class AsyncClipboardDeliveryTest(unittest.TestCase):
    def test_pending_copy_does_not_block_and_busy_copy_is_refused(self):
        entered, release, finished = threading.Event(), threading.Event(), threading.Event()
        results = []
        def copy(text):
            entered.set()
            self.assertTrue(release.wait(2))
            return None, None, 'xclip'
        service = ClipboardService(copy_fn=copy)
        try:
            self.assertTrue(service.submit('first', lambda result: (results.append(result), finished.set())))
            self.assertTrue(entered.wait(1))
            self.assertFalse(service.submit('second', lambda _: self.fail('Busy copy was queued')))
        finally:
            release.set()
        self.assertTrue(finished.wait(2))
        self.assertTrue(results[0].confirmed)

    def test_selection_clears_only_after_confirmed_delivery(self):
        from factory.core.tui.mouse_frame import Selection
        for backend, error, retained in [('xclip', None, False), ('osc52', None, True), ('xclip', 'failed', True)]:
            with self.subTest(backend=backend, error=error):
                completed = threading.Event()
                service = ClipboardService(copy_fn=lambda _: (None, error, backend))
                controller = MouseController(clipboard_service=service, notify=lambda _: completed.set())
                selection = Selection(0, 0, 0, 4, 0)
                controller.selection = selection
                controller._deliver_copy('text', selection)
                self.assertTrue(completed.wait(2))
                self.assertEqual(controller.selection is selection, retained)

    def test_old_completion_does_not_clear_new_selection(self):
        from factory.core.tui.mouse_frame import Selection
        entered, release, finished = threading.Event(), threading.Event(), threading.Event()
        def copy(_):
            entered.set()
            release.wait(2)
            return None, None, 'xclip'
        controller = MouseController(clipboard_service=ClipboardService(copy_fn=copy), notify=lambda _: finished.set())
        old, new = Selection(0,0,0,3,0), Selection(1,0,1,3,1)
        controller.selection = old
        controller._deliver_copy('old', old)
        self.assertTrue(entered.wait(1))
        controller.selection = new
        release.set()
        self.assertTrue(finished.wait(2))
        self.assertIs(controller.selection, new)
