"""Animation schedules respect time boundaries and reduced-motion preferences."""

import re
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from factory.core.tui import ascii_animation as art, motion


@pytest.fixture
def animation(monkeypatch):
    clock = SimpleNamespace(value=1.0)
    monkeypatch.setattr(art, 'time', SimpleNamespace(monotonic=lambda: clock.value))
    requests = Mock()
    value = art.AsciiAnimation.with_variants(requests, [['a', 'b', 'c'], ['x', 'y']])
    value.frame_tick_ms = 200
    return value, clock, requests


@pytest.mark.parametrize('index,expected', [(-100, 0), (-1, 0), (0, 0), (1, 1), (100, 1)])
def test_variant_index_is_clamped_to_available_range(monkeypatch, index, expected):
    monkeypatch.setattr(art, 'time', SimpleNamespace(monotonic=lambda: 1.0))
    value = art.AsciiAnimation.with_variants(Mock(), [['first'], ['last']], index)
    assert value.variant_idx == expected and value.current_frame() == ('first' if expected == 0 else 'last')


def test_empty_variant_list_is_rejected_but_an_empty_frame_list_is_quiet():
    with pytest.raises(ValueError, match='at least one'):
        art.AsciiAnimation.with_variants(Mock(), [])
    assert art.AsciiAnimation.with_variants(Mock(), [[]]).current_frame() == ''


def test_frame_and_schedule_follow_exact_tick_boundaries(animation):
    value, clock, requests = animation
    assert value.current_frame() == 'a'
    value.schedule_next_frame()
    requests.schedule_frame_in.assert_called_once_with(200)
    requests.reset_mock()
    clock.value = 1.25
    assert value.current_frame() == 'b'
    value.schedule_next_frame()
    requests.schedule_frame_in.assert_called_once_with(150)
    clock.value = 1.75
    assert value.current_frame() == 'a'


def test_zero_tick_and_failed_delayed_scheduler_use_immediate_frames(animation):
    value, _, requests = animation
    value.frame_tick_ms = 0
    value.schedule_next_frame()
    assert value.current_frame() == 'a'
    requests.schedule_frame.assert_called_once_with()
    value.frame_tick_ms = 200
    requests.reset_mock()
    requests.schedule_frame_in.side_effect = RuntimeError('owned scheduler unavailable')
    value.schedule_next_frame()
    requests.schedule_frame.assert_called_once_with()


def test_random_variant_changes_and_requests_redraw_without_reselecting_current(animation, monkeypatch):
    value, _, requests = animation
    choices = iter([0, 1])
    monkeypatch.setattr(art.random, 'SystemRandom', lambda: SimpleNamespace(randrange=lambda size: next(choices)))
    assert value.pick_random_variant() is True and value.frames() == ['x', 'y']
    requests.schedule_frame.assert_called_once_with()
    value.variants = [['only']]
    requests.reset_mock()
    assert value.pick_random_variant() is False and not requests.schedule_frame.called


@pytest.mark.parametrize('enabled,expected', [(True, motion.MotionMode.Animated), (False, motion.MotionMode.Reduced)])
def test_runtime_animation_flag_selects_motion_mode(enabled, expected):
    assert motion.motion_mode_from_animations_enabled(enabled) == expected


@pytest.mark.parametrize('indicator,expected', [(motion.ReducedMotionIndicator.Hidden, None), (motion.ReducedMotionIndicator.StaticBullet, '\x1b[2m•\x1b[0m'), ('unknown', None)])
def test_reduced_motion_never_requests_shimmer(monkeypatch, indicator, expected):
    render = Mock(side_effect=AssertionError('No animation in reduced mode'))
    monkeypatch.setattr(motion, 'shimmer_spans', render)
    assert motion.activity_indicator(None, motion.MotionMode.Reduced, indicator) == expected
    assert motion.shimmer_text('owned text', motion.MotionMode.Reduced) == ['owned text']
    assert motion.shimmer_text('', motion.MotionMode.Reduced) == []
    render.assert_not_called()


@pytest.mark.parametrize('spans,expected', [(['styled-bullet'], 'styled-bullet'), ([], '•')])
@pytest.mark.parametrize('started', [None, 1.0])
def test_animated_indicator_handles_both_colored_and_empty_shimmer(monkeypatch, spans, expected, started):
    monkeypatch.setattr(motion, 'time', SimpleNamespace(monotonic=lambda: 2.0))
    monkeypatch.setattr(motion, 'shimmer_spans', lambda text: spans)
    assert motion.activity_indicator(started, motion.MotionMode.Animated, motion.ReducedMotionIndicator.Hidden) == expected


def test_real_shimmer_preserves_visible_text():
    output = ''.join(motion.shimmer_text('Привет 🐙', motion.MotionMode.Animated))
    assert re.sub(r'\x1b\[[0-9;]*m', '', output) == 'Привет 🐙'
