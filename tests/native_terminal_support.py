"""Bounded terminal transition waits tolerate process-exit observation races."""

import time


def wait_for_terminal(predicate, *, process, pump, diagnostic, timeout=7, clock=None):
    clock = clock or time.monotonic
    end = clock() + timeout
    while clock() < end:
        pump()
        if predicate():
            return
        if process.poll() is not None:
            if predicate():
                return
            break
    raise AssertionError("Native terminal transition failed: " + diagnostic())
