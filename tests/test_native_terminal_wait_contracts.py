"""Exercise wait races directly instead of relying on scheduler timing."""

from unittest.mock import Mock

import pytest

from native_terminal_support import wait_for_terminal


def test_process_exit_between_predicate_and_poll_is_success():
    process = Mock()
    process.poll.side_effect = [None, 0, 0]
    pump = Mock()
    wait_for_terminal(
        lambda: process.poll() is not None,
        process=process,
        pump=pump,
        diagnostic=lambda: "fixture",
        clock=lambda: 0,
    )
    pump.assert_called_once()
    assert process.poll.call_count == 3


def test_completed_process_without_expected_transition_fails():
    process = Mock()
    process.poll.return_value = 0
    with pytest.raises(AssertionError, match="fixture marker missing"):
        wait_for_terminal(
            lambda: False,
            process=process,
            pump=Mock(),
            diagnostic=lambda: "fixture marker missing",
            clock=lambda: 0,
        )


def test_ready_predicate_does_not_require_process_exit():
    process = Mock()
    wait_for_terminal(
        lambda: True,
        process=process,
        pump=Mock(),
        diagnostic=lambda: "unused",
        clock=lambda: 0,
    )
    process.poll.assert_not_called()


def test_live_process_remains_bounded_by_deadline():
    process = Mock()
    process.poll.return_value = None
    clock = Mock(side_effect=[0, 1, 8])
    with pytest.raises(AssertionError, match="still running"):
        wait_for_terminal(
            lambda: False,
            process=process,
            pump=Mock(),
            diagnostic=lambda: "still running",
            clock=clock,
            timeout=7,
        )
    process.poll.assert_called_once()


def test_final_marker_arriving_with_exit_is_rechecked():
    process = Mock()
    process.poll.return_value = 0
    predicate = Mock(side_effect=[False, True])
    wait_for_terminal(
        predicate,
        process=process,
        pump=Mock(),
        diagnostic=lambda: "fixture",
        clock=lambda: 0,
    )
    assert predicate.call_count == 2
