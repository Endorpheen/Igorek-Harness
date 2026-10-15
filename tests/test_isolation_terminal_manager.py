#!/usr/bin/env python3
"""Unit tests for the host-side terminal manager (no KVM, no real PTY needed).

The backend seam is faked, so every assertion is about the manager's own
contract: tickets are single-use, bound to their session and expiring; one
terminal per session; the global limit is enforced *before* a backend is
spawned; the replay ring is bounded; and teardown is idempotent.
"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(os.environ.get("IGOREK_FACTORY_REPO", Path(__file__).resolve().parents[1]))
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from factory.core.isolation.terminal_manager import (  # noqa: E402
    TICKET_TTL_S,
    PtyTerminalBackend,
    TerminalError,
    TerminalManager,
    TerminalSpec,
    _clamp_dimension,
)
from factory.core.isolation.terminal_protocol import (  # noqa: E402
    MAX_DIMENSION,
    MAX_REPLAY_BYTES,
)

SESSION = "chat-20261002-091216-d920e8"


class FakeClock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


class FakeBackend:
    """Deterministic stand-in for a PTY (records calls, scripted output)."""

    instances: list["FakeBackend"] = []

    def __init__(self, *, chunks: list[bytes | None] | None = None) -> None:
        self.opened = False
        self.was_closed = False
        self.written: list[bytes] = []
        self.resizes: list[tuple[int, int]] = []
        self.open_spec: TerminalSpec | None = None
        self._chunks = list(chunks or [])
        FakeBackend.instances.append(self)

    @property
    def closed(self) -> bool:
        return self.was_closed

    def open(self, spec: TerminalSpec) -> None:
        if self.opened:
            raise TerminalError("backend_already_open")
        self.opened = True
        self.open_spec = spec

    def read(self, timeout_s: float) -> bytes | None:
        return self._chunks.pop(0) if self._chunks else None

    def write(self, data: bytes) -> None:
        if self.was_closed:
            raise TerminalError("terminal_closed")
        self.written.append(data)

    def resize(self, rows: int, cols: int) -> None:
        self.resizes.append((rows, cols))

    def close(self) -> None:
        self.was_closed = True


class BackendFactory:
    """Factory handing out pre-built fake backends, one per open()."""

    def __init__(self, backends: list[FakeBackend] | None = None) -> None:
        self._backends = list(backends or [])
        self.created: list[FakeBackend] = []

    def __call__(self) -> FakeBackend:
        backend = self._backends.pop(0) if self._backends else FakeBackend()
        self.created.append(backend)
        return backend


def build_manager(**kwargs: object) -> tuple[TerminalManager, BackendFactory, FakeClock]:
    clock = FakeClock()
    factory = BackendFactory()
    manager = TerminalManager(
        backend_factory=factory,
        clock=clock,
        ticket_bytes=lambda n: b"\xab" * n,
        **kwargs,
    )
    return manager, factory, clock


class TicketTest(unittest.TestCase):
    """Tickets: single-use, session-bound, expiring, 128-bit."""

    def test_ticket_shape(self) -> None:
        manager, _factory, _clock = build_manager()
        ticket = str(manager.issue_ticket(SESSION))
        self.assertEqual(len(ticket), 32)
        self.assertTrue(all(ch in "0123456789abcdef" for ch in ticket))

    def test_single_use(self) -> None:
        manager, _factory, _clock = build_manager()
        ticket = manager.issue_ticket(SESSION)
        record = manager.consume_ticket(str(ticket), SESSION)
        self.assertEqual(record.session_id, SESSION)
        with self.assertRaises(TerminalError) as ctx:
            manager.consume_ticket(str(ticket), SESSION)
        self.assertEqual(ctx.exception.reason, "ticket_invalid")

    def test_session_binding(self) -> None:
        manager, _factory, _clock = build_manager()
        ticket = manager.issue_ticket(SESSION)
        with self.assertRaises(TerminalError) as ctx:
            manager.consume_ticket(str(ticket), "another-session")
        self.assertEqual(ctx.exception.reason, "ticket_session_mismatch")
        # A failed consumption must not burn the ticket's binding silently:
        # the ticket was popped, so it is already spent (fail-closed).
        with self.assertRaises(TerminalError):
            manager.consume_ticket(str(ticket), SESSION)

    def test_expiry(self) -> None:
        manager, _factory, clock = build_manager()
        ticket = manager.issue_ticket(SESSION)
        clock.advance(TICKET_TTL_S + 1)
        with self.assertRaises(TerminalError) as ctx:
            manager.consume_ticket(str(ticket), SESSION)
        self.assertEqual(ctx.exception.reason, "ticket_invalid")
        self.assertEqual(manager.pending_tickets(), 0)

    def test_unknown_ticket(self) -> None:
        manager, _factory, _clock = build_manager()
        with self.assertRaises(TerminalError) as ctx:
            manager.consume_ticket("f" * 32, SESSION)
        self.assertEqual(ctx.exception.reason, "ticket_invalid")

    def test_non_string_ticket(self) -> None:
        manager, _factory, _clock = build_manager()
        with self.assertRaises(TerminalError) as ctx:
            manager.consume_ticket(None, SESSION)  # type: ignore[arg-type]
        self.assertEqual(ctx.exception.reason, "ticket_invalid")

    def test_issues_are_unique(self) -> None:
        manager = TerminalManager(ticket_bytes=lambda n: os.urandom(n))
        tickets = {str(manager.issue_ticket(SESSION)) for _ in range(50)}
        self.assertEqual(len(tickets), 50)

    def test_invalid_session_id(self) -> None:
        manager, _factory, _clock = build_manager()
        for value in ("", "   ", None, 5):
            with self.subTest(value=value):
                with self.assertRaises(TerminalError) as ctx:
                    manager.issue_ticket(value)  # type: ignore[arg-type]
                self.assertEqual(ctx.exception.reason, "session_id_invalid")

    def test_ticket_source_must_be_16_bytes(self) -> None:
        manager = TerminalManager(ticket_bytes=lambda n: b"\x00" * (n - 1))
        with self.assertRaises(TerminalError) as ctx:
            manager.issue_ticket(SESSION)
        self.assertEqual(ctx.exception.reason, "ticket_source_invalid")


class TerminalLifecycleTest(unittest.TestCase):
    """One terminal per session, bounded globally, idempotent teardown."""

    def test_open_uses_spec(self) -> None:
        manager, factory, _clock = build_manager()
        state = manager.open_terminal(TerminalSpec(session_id=SESSION, rows=24, cols=80))
        backend = factory.created[0]
        self.assertTrue(backend.opened)
        self.assertEqual(backend.open_spec, TerminalSpec(session_id=SESSION, rows=24, cols=80))
        self.assertEqual((state.rows, state.cols), (24, 80))
        self.assertEqual(state.terminal_id, manager.terminal_for_session(SESSION).terminal_id)

    def test_second_open_same_session_is_refused(self) -> None:
        manager, factory, _clock = build_manager()
        manager.open_terminal(TerminalSpec(session_id=SESSION))
        with self.assertRaises(TerminalError) as ctx:
            manager.open_terminal(TerminalSpec(session_id=SESSION))
        self.assertEqual(ctx.exception.reason, "terminal_exists")
        self.assertEqual(len(factory.created), 1)

    def test_global_limit_refused_before_spawn(self) -> None:
        manager, factory, _clock = build_manager(max_terminals=2)
        manager.open_terminal(TerminalSpec(session_id="a"))
        manager.open_terminal(TerminalSpec(session_id="b"))
        with self.assertRaises(TerminalError) as ctx:
            manager.open_terminal(TerminalSpec(session_id="c"))
        self.assertEqual(ctx.exception.reason, "terminal_limit")
        self.assertEqual(len(factory.created), 2, "no backend may be spawned past the limit")

    def test_finished_terminal_can_be_replaced(self) -> None:
        manager, factory, _clock = build_manager()
        first = manager.open_terminal(TerminalSpec(session_id=SESSION))
        manager.mark_exit(first.terminal_id, 0)
        second = manager.open_terminal(TerminalSpec(session_id=SESSION))
        self.assertNotEqual(second.terminal_id, first.terminal_id)
        self.assertTrue(factory.created[0].closed)

    def test_input_is_forwarded_verbatim(self) -> None:
        manager, factory, _clock = build_manager()
        state = manager.open_terminal(TerminalSpec(session_id=SESSION))
        manager.input(state.terminal_id, b"\x03")
        manager.input(state.terminal_id, "ls\r".encode())
        self.assertEqual(factory.created[0].written, [b"\x03", b"ls\r"])

    def test_input_to_exited_terminal_is_refused(self) -> None:
        manager, _factory, _clock = build_manager()
        state = manager.open_terminal(TerminalSpec(session_id=SESSION))
        manager.mark_exit(state.terminal_id, 1)
        with self.assertRaises(TerminalError) as ctx:
            manager.input(state.terminal_id, b"x")
        self.assertEqual(ctx.exception.reason, "terminal_exited")

    def test_unknown_terminal(self) -> None:
        manager, _factory, _clock = build_manager()
        for call in (
            lambda: manager.input("nope", b"x"),
            lambda: manager.resize("nope", 10, 10),
            lambda: manager.ack("nope", 1),
            lambda: manager.replay("nope", 0),
            lambda: manager.pump("nope"),
        ):
            with self.assertRaises(TerminalError) as ctx:
                call()
            self.assertEqual(ctx.exception.reason, "terminal_unknown")

    def test_resize_is_clamped_and_applied(self) -> None:
        manager, factory, _clock = build_manager()
        state = manager.open_terminal(TerminalSpec(session_id=SESSION, rows=24, cols=80))
        manager.resize(state.terminal_id, 100, 200)
        self.assertEqual(factory.created[0].resizes, [(100, 200)])
        self.assertEqual((state.rows, state.cols), (100, 200))
        # Out-of-range values fall back to the previous size, never to an
        # unchecked value, and the backend is still told about it.
        manager.resize(state.terminal_id, 0, MAX_DIMENSION + 5)
        self.assertEqual((state.rows, state.cols), (100, 200))
        self.assertEqual(factory.created[0].resizes[-1], (100, 200))

    def test_close_is_idempotent(self) -> None:
        manager, factory, _clock = build_manager()
        state = manager.open_terminal(TerminalSpec(session_id=SESSION))
        self.assertTrue(manager.close_terminal(state.terminal_id))
        self.assertFalse(manager.close_terminal(state.terminal_id))
        self.assertTrue(factory.created[0].closed)
        self.assertIsNone(manager.terminal_for_session(SESSION))

    def test_close_session(self) -> None:
        manager, _factory, _clock = build_manager()
        manager.open_terminal(TerminalSpec(session_id=SESSION))
        self.assertEqual(manager.close_session(SESSION), 1)
        self.assertEqual(manager.close_session(SESSION), 0)

    def test_close_all(self) -> None:
        manager, factory, _clock = build_manager()
        for session in ("a", "b", "c"):
            manager.open_terminal(TerminalSpec(session_id=session))
        manager.close_all()
        self.assertEqual(manager.count(), 0)
        self.assertTrue(all(backend.closed for backend in factory.created))

    def test_clamp_dimension(self) -> None:
        self.assertEqual(_clamp_dimension(10, 30), 10)
        self.assertEqual(_clamp_dimension(0, 30), 30)
        self.assertEqual(_clamp_dimension(True, 30), 30)
        self.assertEqual(_clamp_dimension("20", 30), 30)  # type: ignore[arg-type]


class OutputAndReplayTest(unittest.TestCase):
    """Sequenced output, ack cursor and bounded reconnect replay."""

    def _open_with_output(self, chunks: list[bytes | None]) -> tuple[TerminalManager, FakeBackend, str]:
        factory = BackendFactory([FakeBackend(chunks=chunks)])
        manager = TerminalManager(
            backend_factory=factory,
            clock=FakeClock(),
            ticket_bytes=lambda n: b"\xab" * n,
        )
        state = manager.open_terminal(TerminalSpec(session_id=SESSION))
        return manager, factory.created[0], state.terminal_id

    def test_sequence_is_monotonic_and_dense(self) -> None:
        manager, _backend, terminal_id = self._open_with_output([b"a", b"bb", b"ccc"])
        out = manager.pump(terminal_id)
        self.assertEqual([item.seq for item in out], [1, 2, 3])
        self.assertEqual([item.data for item in out], [b"a", b"bb", b"ccc"])
        self.assertEqual(manager.pump(terminal_id), [])
    def test_ack_never_moves_backwards(self) -> None:
        manager, _backend, terminal_id = self._open_with_output([b"a", b"b"])
        manager.pump(terminal_id)
        self.assertEqual(manager.ack(terminal_id, 2), 2)
        self.assertEqual(manager.ack(terminal_id, 1), 2)

    def test_ack_validation(self) -> None:
        manager, _backend, terminal_id = self._open_with_output([])
        for value in (-1, True, "3", None):
            with self.subTest(value=value):
                with self.assertRaises(TerminalError) as ctx:
                    manager.ack(terminal_id, value)  # type: ignore[arg-type]
                self.assertEqual(ctx.exception.reason, "ack_invalid")

    def test_replay_after_reload(self) -> None:
        manager, _backend, terminal_id = self._open_with_output([b"one", b"two", b"three"])
        manager.pump(terminal_id)
        # Browser painted chunks 1 and 2, then reloaded.
        manager.ack(terminal_id, 2)
        replay = manager.replay(terminal_id, manager.terminal(terminal_id).acked_seq)
        self.assertEqual([item.seq for item in replay], [3])
        self.assertEqual([item.data for item in replay], [b"three"])

    def test_replay_is_empty_when_caught_up(self) -> None:
        manager, _backend, terminal_id = self._open_with_output([b"one"])
        manager.pump(terminal_id)
        manager.ack(terminal_id, 1)
        self.assertEqual(manager.replay(terminal_id, 1), [])

    def test_replay_rejects_bad_seq(self) -> None:
        manager, _backend, terminal_id = self._open_with_output([])
        with self.assertRaises(TerminalError) as ctx:
            manager.replay(terminal_id, -1)
        self.assertEqual(ctx.exception.reason, "replay_seq_invalid")

    def test_ring_is_bounded_by_chunks(self) -> None:
        manager, _backend, terminal_id = self._open_with_output([b"x"] * 40)
        manager._replay_chunks = 10  # type: ignore[attr-defined]
        manager.pump(terminal_id)
        state = manager.terminal(terminal_id)
        self.assertEqual(len(state.replay), 10)
        self.assertEqual(state.replay[0].seq, 31)

    def test_ring_is_bounded_by_bytes(self) -> None:
        big = b"z" * (MAX_REPLAY_BYTES // 2)
        manager, _backend, terminal_id = self._open_with_output([big, big, big, big])
        manager.pump(terminal_id)
        state = manager.terminal(terminal_id)
        self.assertLessEqual(state.replay_bytes, MAX_REPLAY_BYTES)
        self.assertEqual(len(state.replay), 2)

    def test_mark_exit_clamps_code(self) -> None:
        manager, _backend, terminal_id = self._open_with_output([])
        manager.mark_exit(terminal_id, 300)
        self.assertEqual(manager.terminal(terminal_id).exit_code, 255)
        manager.mark_exit(terminal_id, -5)
        self.assertEqual(manager.terminal(terminal_id).exit_code, 255, "first exit wins")

    def test_backend_read_error_is_propagated(self) -> None:
        class BrokenBackend(FakeBackend):
            def read(self, timeout_s: float) -> bytes | None:
                raise TerminalError("pty_read_failed", "boom")

        factory = BackendFactory([BrokenBackend()])
        manager = TerminalManager(backend_factory=factory, clock=FakeClock())
        state = manager.open_terminal(TerminalSpec(session_id=SESSION))
        with self.assertRaises(TerminalError) as ctx:
            manager.pump(state.terminal_id)
        self.assertEqual(ctx.exception.reason, "pty_read_failed")

    def test_oserror_from_backend_is_wrapped(self) -> None:
        class OsErrorBackend(FakeBackend):
            def read(self, timeout_s: float) -> bytes | None:
                raise OSError("no such device")

        factory = BackendFactory([OsErrorBackend()])
        manager = TerminalManager(backend_factory=factory, clock=FakeClock())
        state = manager.open_terminal(TerminalSpec(session_id=SESSION))
        with self.assertRaises(TerminalError) as ctx:
            manager.pump(state.terminal_id)
        self.assertEqual(ctx.exception.reason, "terminal_read_failed")

    def test_failed_open_releases_the_slot(self) -> None:
        class FailingBackend(FakeBackend):
            def open(self, spec: TerminalSpec) -> None:
                raise TerminalError("spawn_failed", "no pty")

        failing = BackendFactory([FailingBackend()])
        manager = TerminalManager(backend_factory=failing, clock=FakeClock(), max_terminals=1)
        with self.assertRaises(TerminalError):
            manager.open_terminal(TerminalSpec(session_id=SESSION))
        # The reservation must be released: another session can still open.
        manager._backend_factory = BackendFactory()  # type: ignore[attr-defined]
        state = manager.open_terminal(TerminalSpec(session_id="other"))
        self.assertEqual(manager.count(), 1)
        self.assertEqual(state.session_id, "other")


class PtyBackendValidationTest(unittest.TestCase):
    """The real backend refuses configurations it cannot honour (no spawn)."""

    def test_shell_must_be_absolute(self) -> None:
        backend = PtyTerminalBackend(shell="bash")
        with self.assertRaises(TerminalError) as ctx:
            backend.open(TerminalSpec(session_id=SESSION))
        self.assertEqual(ctx.exception.reason, "shell_not_absolute")
        self.assertFalse(backend.closed)

    def test_missing_workdir_is_refused(self) -> None:
        backend = PtyTerminalBackend(shell="/bin/bash")
        with self.assertRaises(TerminalError) as ctx:
            backend.open(TerminalSpec(session_id=SESSION, workdir="/nonexistent/igorek-dir"))
        self.assertEqual(ctx.exception.reason, "workdir_missing")

    def test_double_open_is_refused(self) -> None:
        backend = PtyTerminalBackend(shell="/bin/bash")
        # White-box on purpose: the guard under test is "a PTY already exists",
        # and spawning a real interactive shell from a unit test would leave a
        # stray process behind. The fd is therefore injected directly.
        backend._master_fd = 0  # type: ignore[attr-defined]
        with self.assertRaises(TerminalError) as ctx:
            backend.open(TerminalSpec(session_id=SESSION, workdir=""))
        self.assertEqual(ctx.exception.reason, "backend_already_open")


if __name__ == "__main__":
    unittest.main()
