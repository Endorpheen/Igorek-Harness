"""Mouse copy completion must not overwrite newer user interaction."""
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from factory.core.tui.mouse_controller import MouseController
from factory.core.tui.mouse_frame import FrameRegion, FrameRow, ScreenFrame, Selection


class DeferredClipboard:
    def __init__(self, accepted=True):
        self.accepted = accepted
        self.calls = []

    def submit(self, text, callback):
        self.calls.append((text, callback))
        return self.accepted


def frame():
    return ScreenFrame(rows=[FrameRow(0, "abcdef")], composer_top=2)


def controller(clipboard=None, **kwargs):
    instance = MouseController(clipboard_service=clipboard or DeferredClipboard(), **kwargs)
    instance.configure_support(True)
    instance.enable()
    instance.set_frame(frame())
    return instance


def packet(value):
    return SimpleNamespace(data=value)


def drag(instance, start, end):
    assert instance.dispatch(packet(f"\x1b[<0;{start};1M")) == "handled"
    assert instance.dispatch(packet(f"\x1b[<32;{end};1M")) == "handled"
    assert instance.dispatch(packet(f"\x1b[<0;{end};1m")) == "handled"


def test_late_clipboard_confirmation_does_not_clear_newer_selection():
    clipboard = DeferredClipboard()
    instance = controller(clipboard)
    drag(instance, 1, 4)
    assert clipboard.calls[0][0] == "abc"
    drag(instance, 3, 6)
    new_selection = instance.selection
    assert clipboard.calls[1][0] == "cde"
    clipboard.calls[0][1](SimpleNamespace(confirmed=True, message="copied"))
    assert instance.selection is new_selection
    clipboard.calls[1][1](SimpleNamespace(confirmed=True, message="copied"))
    assert instance.selection is None


def test_rejected_copy_keeps_selection_and_reports_busy_clipboard():
    clipboard = DeferredClipboard(accepted=False)
    notifications = []
    instance = controller(clipboard, notify=notifications.append)
    drag(instance, 1, 4)
    assert instance.selection is not None
    assert notifications == ["Буфер обмена занят; повторите копирование"]


def test_unconfirmed_copy_does_not_clear_selection():
    clipboard = DeferredClipboard()
    instance = controller(clipboard)
    drag(instance, 1, 4)
    selected = instance.selection
    clipboard.calls[0][1](SimpleNamespace(confirmed=False, message="copy failed"))
    assert instance.selection is selected


def test_late_confirmation_after_frame_change_cannot_clear_selection_from_new_frame():
    clipboard = DeferredClipboard()
    instance = controller(clipboard)
    drag(instance, 1, 4)
    new_frame = frame()
    instance.set_frame(new_frame)
    drag(instance, 2, 5)
    selected = instance.selection
    clipboard.calls[0][1](SimpleNamespace(confirmed=True, message=""))
    assert instance.selection is selected
    assert selected.frame_revision == new_frame.revision


def test_frame_builder_failure_falls_back_to_last_complete_frame():
    instance = controller()
    previous = instance.current_frame()
    instance.set_frame_builder(Mock(side_effect=RuntimeError("fixture")))
    assert instance.current_frame() is previous
    assert instance.dispatch(packet("\x1b[<0;1;1M")) == "handled"


@pytest.mark.parametrize("data", ["\x1b[<1;2;1M", "\x1b[<4;2;1M", "\x1b[<66;2;1M", "not-sgr"])
def test_unhandled_mouse_events_delegate_without_destroying_selection(data):
    instance = controller()
    instance.selection = Selection(0, 0, 0, 2, instance.current_frame().revision)
    previous = instance.selection
    assert instance.dispatch(packet(data)) == "delegate"
    assert instance.selection is previous


def test_tool_cell_toggles_only_the_clicked_cell():
    instance = controller()
    displayed = ScreenFrame(
        rows=[FrameRow(0, "tool A"), FrameRow(1, "tool B")],
        regions={
            "a": FrameRegion("a", "tool", cell_id="a", top=0, bottom=0, expandable=True),
            "b": FrameRegion("b", "tool", cell_id="b", top=1, bottom=1, expandable=True),
        }, composer_top=3,
    )
    instance.set_frame(displayed)
    instance.dispatch(packet("\x1b[<0;1;2M"))
    instance.dispatch(packet("\x1b[<0;1;2m"))
    assert instance.is_cell_expanded("b")
    assert not instance.is_cell_expanded("a")


def test_broken_reasoning_callback_does_not_break_subsequent_clicks():
    toggle = Mock(side_effect=RuntimeError("fixture"))
    instance = controller(on_toggle_reasoning=toggle)
    displayed = ScreenFrame(rows=[FrameRow(0, "reasoning")],
                            regions={"r": FrameRegion("r", "reasoning", top=0, bottom=0)},
                            composer_top=2)
    instance.set_frame(displayed)
    for _ in range(2):
        assert instance.dispatch(packet("\x1b[<0;1;1M")) == "handled"
        assert instance.dispatch(packet("\x1b[<0;1;1m")) == "handled"
    assert toggle.call_count == 2
