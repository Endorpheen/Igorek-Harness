"""Commit scheduling uses real controllers without dropping or exposing held blocks."""

from types import SimpleNamespace

import pytest

from factory.core.tui.streaming import mod
from factory.core.tui.streaming.chunking import ChunkingMode
from factory.core.tui.streaming.commit_tick import CommitTickRunner
from factory.core.tui.streaming.controller import StreamController
from factory.core.tui.bottom_pane.feedback_view import FeedbackView


def test_empty_controller_group_has_no_output():
    runner = CommitTickRunner([])
    assert runner.tick() == []


def test_smooth_mode_emits_at_most_one_ready_line_without_losing_order():
    first, second = StreamController(), StreamController()
    first.feed('a\nb\n')
    second.feed('z\n')
    runner = CommitTickRunner([first, second])
    assert runner.tick() == ['a']
    assert runner.tick() == ['b']
    assert runner.tick() == ['z']
    assert runner.tick() == [] and first.is_idle() and second.is_idle()


def test_smooth_mode_skips_empty_controller_and_retains_partial_tail():
    first, second = StreamController(), StreamController()
    second.feed('complete\npartial')
    runner = CommitTickRunner([first, second])
    assert runner.tick() == ['complete']
    assert runner.tick() == []
    assert second.tail_text() == 'partial' and second.finalize() == ['partial']


def test_backlog_catch_up_drains_complete_lines_from_every_controller():
    first, second = StreamController(), StreamController()
    expected = [f'line-{i}' for i in range(8)]
    for line in expected:
        first.feed(line + '\n')
    second.feed('other\n')
    runner = CommitTickRunner([first, second])
    assert runner.tick() == expected + ['other']
    assert runner.policy.mode is ChunkingMode.CATCH_UP
    assert first.is_idle() and second.is_idle() and runner.tick() == []


def test_old_pending_chunks_trigger_catch_up_below_depth_threshold(monkeypatch):
    clock = SimpleNamespace(value=1.0)
    monkeypatch.setattr(mod, 'time', SimpleNamespace(monotonic=lambda: clock.value))
    controller = StreamController()
    controller.feed('first\n')
    controller.feed('second\n')
    clock.value = 1.25
    runner = CommitTickRunner([controller])
    assert runner.tick() == ['first', 'second']
    assert controller.is_idle() and runner.policy.mode is ChunkingMode.CATCH_UP


def test_catch_up_returns_to_smooth_when_new_backlog_is_small():
    controller = StreamController()
    for i in range(8):
        controller.feed(str(i) + '\n')
    runner = CommitTickRunner([controller])
    assert runner.tick() == [str(i) for i in range(8)]
    controller.feed('next\nlast\n')
    assert runner.tick() == ['next'] and runner.policy.mode is ChunkingMode.SMOOTH
    assert runner.tick() == ['last'] and runner.tick() == []


def test_catch_up_does_not_expose_an_unclosed_fence():
    controller = StreamController()
    lines = ['```python', *[f'line-{i}' for i in range(7)]]
    for line in lines:
        controller.feed(line + '\n')
    runner = CommitTickRunner([controller])
    assert runner.tick() == [] and not controller.is_idle()
    assert 'line-6' in controller.tail_text()
    controller.feed('```\n')
    emitted = []
    for _ in range(12):
        emitted.extend(runner.tick())
        if controller.is_idle():
            break
    assert emitted == lines + ['```'] and controller.is_idle()


def test_catch_up_preserves_table_and_blank_separator_lines():
    controller = StreamController()
    lines = ['| A | B |', '| --- | --- |', *[f'| {i} | x |' for i in range(6)], '', 'after']
    for line in lines:
        controller.feed(line + '\n')
    runner = CommitTickRunner([controller])
    assert runner.tick() == lines and controller.is_idle()


@pytest.mark.parametrize('width', [40, 80, 120])
def test_feedback_visibility_and_frame_width_are_stable(width):
    view = FeedbackView()
    original = tuple(view.options)
    assert view.render_lines(width) == []
    view.toggle()
    lines = view.render_lines(width)
    assert lines and all(len(line) == width for line in lines)
    assert all(any(option in line for line in lines) for option in original)
    assert tuple(view.options) == original and view.render_lines(width) == lines
    view.toggle()
    assert view.render_lines(width) == []
