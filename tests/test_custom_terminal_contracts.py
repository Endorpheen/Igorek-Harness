"""Contract tests for CustomTerminal diff-based viewport rendering."""
from __future__ import annotations

import io
import types

import factory.core.tui.custom_terminal as custom_terminal_module
import pytest

from factory.core.tui.custom_terminal import CustomTerminal, ViewportArea

VIEWPORT_HEIGHT = 6


class _FixedSize:
    """Controlled terminal size so tests are deterministic."""

    def __init__(self, lines: int, columns: int) -> None:
        self.lines = lines
        self.columns = columns


@pytest.fixture
def terminal_size(monkeypatch):
    monkeypatch.setattr(
        custom_terminal_module, "shutil",
        types.SimpleNamespace(get_terminal_size=lambda fallback=None: _FixedSize(10, 40))
    )
    return _FixedSize(10, 40)


@pytest.fixture
def stdout_buffer(monkeypatch):
    buffer = io.StringIO()
    monkeypatch.setattr(
        custom_terminal_module, "sys", types.SimpleNamespace(stdout=buffer)
    )
    return buffer


@pytest.fixture
def terminal(terminal_size, stdout_buffer):
    return CustomTerminal()


def _drain(buffer: io.StringIO) -> str:
    value = buffer.getvalue()
    buffer.seek(0)
    buffer.truncate(0)
    return value


def _render(terminal: CustomTerminal, buffer: io.StringIO, lines: list) -> str:
    def draw_fn(vp, buf):
        for line in lines:
            buf.append(line)

    terminal.draw(draw_fn)
    return _drain(buffer)


def test_first_frame_clears_viewport(terminal_size, stdout_buffer, terminal):
    output = _render(terminal, stdout_buffer, ["hello"])

    for row in range(VIEWPORT_HEIGHT):
        assert f"\x1b[{row};0H\x1b[2K" in output
    assert "\x1b[0;0H\x1b[2Khello" in output


def test_identical_subsequent_frame_does_not_rewrite(terminal_size, stdout_buffer, terminal):
    first = _render(terminal, stdout_buffer, ["hello"])
    assert "\x1b[0;0H\x1b[2Khello" in first

    second = _render(terminal, stdout_buffer, ["hello"])
    assert second == ""


def test_changed_line_is_redrawn(terminal_size, stdout_buffer, terminal):
    _render(terminal, stdout_buffer, ["hello"])
    second = _render(terminal, stdout_buffer, ["world"])
    assert "\x1b[0;0H\x1b[2Kworld" in second


def test_shorter_frame_clears_stale_tail_line(terminal_size, stdout_buffer, terminal):
    first = _render(terminal, stdout_buffer, ["one", "two"])
    second = _render(terminal, stdout_buffer, ["ONE"])

    assert "\x1b[0;0H\x1b[2KONE" in second
    assert "\x1b[1;0H\x1b[2K" in second


def test_empty_frame_clears_previous_lines(terminal_size, stdout_buffer, terminal):
    _render(terminal, stdout_buffer, ["one", "two"])
    second = _render(terminal, stdout_buffer, [])

    assert "\x1b[0;0H\x1b[2K" in second
    assert "\x1b[1;0H\x1b[2K" in second


def test_rendered_lines_limited_to_viewport_height(terminal_size, stdout_buffer, terminal):
    lines = [f"line{index}" for index in range(8)]
    output = _render(terminal, stdout_buffer, lines)

    assert f"\x1b[5;0H\x1b[2Kline5" in output
    assert f"\x1b[6;0H" not in output


def test_invalidate_forces_redraw_on_next_frame(terminal_size, stdout_buffer, terminal):
    first = _render(terminal, stdout_buffer, ["hello"])
    assert "\x1b[0;0H\x1b[2Khello" in first

    terminal.invalidate_viewport()
    second = _render(terminal, stdout_buffer, ["hello"])
    assert "\x1b[0;0H\x1b[2Khello" in second


def test_viewport_area_change_forces_redraw(terminal_size, stdout_buffer, terminal):
    _render(terminal, stdout_buffer, ["hello"])
    terminal.set_viewport_area(ViewportArea(0, 0, 40, 3))
    second = _render(terminal, stdout_buffer, ["hello"])
    assert "\x1b[0;0H\x1b[2Khello" in second


def test_resize_returns_false_when_size_unchanged(terminal_size, terminal):
    assert terminal.resize() is False


def test_resize_returns_true_and_clears_on_change(terminal_size, monkeypatch, stdout_buffer, terminal):
    monkeypatch.setattr(
        custom_terminal_module, "shutil",
        types.SimpleNamespace(get_terminal_size=lambda fallback=None: _FixedSize(12, 40))
    )
    assert terminal.resize() is True
    second = _render(terminal, stdout_buffer, ["hello"])
    assert "\x1b[0;0H\x1b[2Khello" in second
