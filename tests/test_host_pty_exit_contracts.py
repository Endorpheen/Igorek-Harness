"""Owned Linux PTYs clear reaped child identities before close can send a signal."""

import errno
import os
import signal
import time
from unittest.mock import Mock

import pytest

from factory.core.isolation import terminal_manager as tm


@pytest.mark.skipif(os.name == 'nt', reason='Requires a real POSIX PTY; Windows terminal acceptance runs in Windows CI')
@pytest.mark.parametrize('exit_code', [0, 7])
def test_real_shell_geometry_io_and_reaped_identity(tmp_path, monkeypatch, exit_code):
    home = tmp_path / 'home'
    home.mkdir()
    backend = tm.PtyTerminalBackend(shell='/bin/bash', env={'HOME': str(home), 'PATH': '/usr/bin:/bin', 'TERM': 'xterm-256color', 'PS1': ''})
    child = None
    reaped = False
    output = bytearray()
    try:
        backend.open(tm.TerminalSpec('owned-fixture', 24, 80, str(tmp_path)))
        child = backend.pid
        assert child and child != os.getpid()
        backend.write(b"stty size; printf 'PTY_%s\\n' INITIAL\n")
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and b'PTY_INITIAL\r\n' not in output:
            output.extend(backend.read(0.05) or b'')
        assert b'24 80\r\n' in output and b'PTY_INITIAL\r\n' in output
        backend.resize(33, 91)
        backend.write(f"stty size; printf 'PTY_%s\\n' RESIZED; exit {exit_code}\n".encode())
        deadline = time.monotonic() + 5
        code = None
        while time.monotonic() < deadline:
            output.extend(backend.read(0.05) or b'')
            code = backend.poll_exit()
            if code is not None:
                reaped = True
                break
        assert code == exit_code
        assert b'33 91\r\n' in output and b'PTY_RESIZED\r\n' in output
        with pytest.raises(ChildProcessError):
            os.waitpid(child, os.WNOHANG)
        assert backend.poll_exit() == exit_code
        kill = Mock(side_effect=AssertionError('Must not signal a reaped child'))
        with monkeypatch.context() as scope:
            scope.setattr(tm.os, 'kill', kill)
            backend.close()
            backend.close()
        assert not kill.called and backend.pid is None and backend.closed
        assert backend.read(0) is None
        with pytest.raises(tm.TerminalError, match='terminal_closed'):
            backend.write(b'ignored')
    finally:
        if reaped:
            backend._pid = None
        backend.close()
        if child and not reaped:
            try:
                os.kill(child, signal.SIGKILL)
            except ProcessLookupError:
                pass
            try:
                os.waitpid(child, 0)
            except ChildProcessError:
                pass


@pytest.mark.parametrize('failure', [ChildProcessError(), OSError(errno.ECHILD, 'already reaped')])
def test_child_reaped_elsewhere_clears_identity_and_preserves_known_exit(monkeypatch, failure):
    backend = tm.PtyTerminalBackend()
    backend._pid = 424242
    monkeypatch.setattr(tm.os, 'waitpid', Mock(side_effect=failure))
    assert backend.poll_exit() == 0
    assert backend.pid is None and backend.poll_exit() == 0


def test_transient_wait_error_keeps_running_child_identity(monkeypatch):
    backend = tm.PtyTerminalBackend()
    backend._pid = 424242
    wait = Mock(side_effect=[OSError(errno.EINTR, 'interrupted'), (0, 0), (424242, signal.SIGTERM)])
    monkeypatch.setattr(tm.os, 'waitpid', wait)
    assert backend.poll_exit() is None and backend.pid == 424242
    assert backend.poll_exit() is None and backend.pid == 424242
    assert backend.poll_exit() == 128 + signal.SIGTERM
    assert backend.pid is None and backend.poll_exit() == 143


def test_close_waits_for_inflight_reap_before_deciding_whether_to_signal(monkeypatch):
    import threading

    backend = tm.PtyTerminalBackend()
    backend._pid = 424242
    entered = threading.Event()
    release = threading.Event()
    closing = threading.Event()
    closed = threading.Event()
    outcomes = []

    def wait(pid, flags):
        entered.set()
        assert release.wait(2)
        return pid, 0

    def close():
        closing.set()
        backend.close()
        closed.set()

    kill = Mock()
    monkeypatch.setattr(tm.os, 'waitpid', wait)
    monkeypatch.setattr(tm.os, 'kill', kill)
    poller = threading.Thread(target=lambda: outcomes.append(backend.poll_exit()))
    closer = threading.Thread(target=close)
    poller.start()
    try:
        assert entered.wait(2)
        closer.start()
        assert closing.wait(2)
        assert not closed.wait(0.05)
    finally:
        release.set()
        poller.join(2)
        if closer.ident is not None:
            closer.join(2)
    assert not poller.is_alive() and not closer.is_alive()
    assert outcomes == [0] and backend.closed and backend.pid is None
    assert not kill.called
