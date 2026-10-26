"""Cancellation state is live, thread-local and cleared across task boundaries."""
import threading

from factory.core.tool_runtime_context import clear_runtime_context, is_interrupt_requested, request_interrupt, set_runtime_context


def test_interrupt_predicate_observes_later_task_cancellation():
    state = {'cancelled': False}
    set_runtime_context(interrupt_check=lambda: state['cancelled'])
    try:
        assert not is_interrupt_requested()
        state['cancelled'] = True
        assert is_interrupt_requested()
    finally: clear_runtime_context()


def test_event_interrupt_keeps_legacy_behavior_with_predicate():
    event = threading.Event()
    set_runtime_context(interrupt_event=event, interrupt_check=lambda: False)
    try:
        assert not is_interrupt_requested()
        request_interrupt()
        assert event.is_set() and is_interrupt_requested()
    finally: clear_runtime_context()


def test_new_task_does_not_inherit_previous_cancellation_predicate():
    set_runtime_context(task_id='old', interrupt_check=lambda: True)
    try:
        assert is_interrupt_requested()
        set_runtime_context(task_id='new')
        assert not is_interrupt_requested()
        set_runtime_context(interrupt_check=lambda: True)
        clear_runtime_context()
        assert not is_interrupt_requested()
    finally: clear_runtime_context()


def test_cancellation_in_one_thread_does_not_interrupt_another():
    values = []
    set_runtime_context(interrupt_check=lambda: True)
    try:
        other = threading.Thread(target=lambda: values.append(is_interrupt_requested()))
        other.start();other.join(3)
        assert not other.is_alive() and values == [False]
        assert is_interrupt_requested()
    finally: clear_runtime_context()
