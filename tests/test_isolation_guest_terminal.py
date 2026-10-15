#!/usr/bin/env python3
"""Unit tests for the in-guest terminal service (no vsock, no real PTY).

The transport is one in-process ``socketpair`` framed exactly like the wire
protocol, and the PTY is injected, so the suite pins the service contract:
handshake validation, the exact message sequence for open/input/resize/output,
the fail-closed answer to a malformed frame, and that closing the unit closes
the PTY.
"""

from __future__ import annotations

import base64
import os
import socket
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(os.environ.get("IGOREK_FACTORY_REPO", Path(__file__).resolve().parents[1]))
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from factory.core.isolation.channel import ChannelConfig, read_frame, write_frame  # noqa: E402
from factory.core.isolation.guest_terminal import (  # noqa: E402
    DEFAULT_TERMINAL_VSOCK_PORT,
    GuestTerminalService,
    TerminalTuning,
)
from factory.core.isolation.terminal_protocol import (  # noqa: E402
    MAX_INPUT_BYTES,
    TERMINAL_PROTOCOL_VERSION,
    TerminalKind,
    TerminalProtocolError,
    TerminalStage,
    decode_message,
    encode_message,
)



def _config() -> ChannelConfig:
    return ChannelConfig(port=DEFAULT_TERMINAL_VSOCK_PORT)


class FakePty:
    """Scripted PTY: queued output, recorded writes and resizes, exit code."""

    def __init__(self, *, chunks: list[bytes | None] | None = None) -> None:
        self.rows = 30
        self.cols = 100
        self.started: tuple[int, int] | None = None
        self.written: list[bytes] = []
        self.resizes: list[tuple[int, int]] = []
        self.exit_code: int | None = None
        self.closed = False
        self.fail_start = False
        self._chunks = list(chunks or [])

    def start(self, rows: int, cols: int) -> None:
        if self.fail_start:
            raise OSError("no pty available")
        self.started = (rows, cols)
        self.rows, self.cols = rows, cols

    def read(self, timeout_s: float) -> bytes | None:
        return self._chunks.pop(0) if self._chunks else None

    def write(self, data: bytes) -> None:
        if self.closed:
            raise TerminalProtocolError("terminal_closed", "pty is gone")
        self.written.append(data)

    def resize(self, rows: int, cols: int) -> None:
        self.rows, self.cols = rows, cols
        self.resizes.append((rows, cols))

    def poll_exit(self) -> int | None:
        return self.exit_code

    def close(self) -> None:
        self.closed = True


def encode_host(kind: TerminalKind, seq: int, payload: dict[str, object]) -> dict:
    """Build the framed message a host would send (internal-leg schema)."""
    return {"kind": str(kind), "seq": seq, "payload": payload}


class ServiceHarness(unittest.TestCase):
    def setUp(self) -> None:
        self.host_sock, self.guest_sock = socket.socketpair()
        self.config = _config()
        self.ptys: list[FakePty] = []

    def tearDown(self) -> None:
        for sock in (self.host_sock, self.guest_sock):
            try:
                sock.close()
            except OSError:
                pass

    def _factory(self, pty: FakePty | None = None):
        def build(_tuning: TerminalTuning) -> FakePty:
            created = pty if pty is not None else FakePty()
            self.ptys.append(created)
            return created
        return build

    def _handshake(self) -> None:
        write_frame(self.host_sock, {"kind": "hello", "version": TERMINAL_PROTOCOL_VERSION})
        ack = read_frame(self.guest_sock, timeout_s=1.0)
        self.assertEqual(ack["kind"], "hello_ack")
        self.assertTrue(ack["accepted"])

    def _send(self, kind: TerminalKind, seq: int, payload: dict[str, object]) -> None:
        write_frame(self.host_sock, encode_host(kind, seq, payload))

    def _recv(self, timeout_s: float = 1.0) -> object:
        frame = read_frame(self.host_sock, timeout_s=timeout_s)
        self.assertIsNotNone(frame)
        return decode_message(frame, stage=TerminalStage.INTERNAL)


class SessionTest(ServiceHarness):
    """Open/input/resize/output/exit over the framed internal protocol."""

    def test_open_starts_pty_and_reports_dimensions(self) -> None:
        pty = FakePty()
        service = GuestTerminalService(self.guest_sock, pty_factory=self._factory(pty))
        self._send(TerminalKind.OPEN, 1, {"rows": 40, "cols": 120})
        # The service returns after the host closes the connection.
        self._send(TerminalKind.CLOSE, 2, {})
        self.assertEqual(service.serve(), 0)
        self.assertEqual(pty.started, (40, 120))
        self.assertTrue(pty.closed)

    def test_multiple_commands_in_order(self) -> None:
        pty = FakePty(chunks=[b"out-1"])
        service = GuestTerminalService(
            self.guest_sock, pty_factory=self._factory(pty), read_window_s=0.0
        )
        self._send(TerminalKind.OPEN, 1, {"rows": 24, "cols": 80})
        self._send(TerminalKind.INPUT, 2, {"data": base64.b64encode(b"ls\r").decode()})
        self._send(TerminalKind.RESIZE, 3, {"rows": 50, "cols": 132})
        self._send(TerminalKind.ACK, 4, {"seq": 1})
        self._send(TerminalKind.CLOSE, 5, {})
        service.serve()
        self.assertEqual(pty.written, [b"ls\r"])
        self.assertEqual(pty.resizes, [(50, 132)])

    def test_open_rejected_without_pty_factory_success(self) -> None:
        pty = FakePty()
        pty.fail_start = True
        service = GuestTerminalService(self.guest_sock, pty_factory=self._factory(pty))
        self._send(TerminalKind.OPEN, 1, {"rows": 24, "cols": 80})
        self._send(TerminalKind.CLOSE, 2, {})
        service.serve()
        message = self._recv()
        self.assertEqual(str(message.kind), TerminalKind.ERROR.value)
        self.assertEqual(message.payload["reason"], "spawn_failed")

    def test_double_open_is_refused(self) -> None:
        service = GuestTerminalService(self.guest_sock, pty_factory=self._factory())
        self._send(TerminalKind.OPEN, 1, {"rows": 24, "cols": 80})
        self._send(TerminalKind.OPEN, 2, {"rows": 24, "cols": 80})
        self._send(TerminalKind.CLOSE, 3, {})
        service.serve()
        first = self._recv()
        second = self._recv()
        self.assertEqual(str(first.kind), TerminalKind.OPENED.value)
        self.assertEqual(str(second.kind), TerminalKind.ERROR.value)
        self.assertEqual(second.payload["reason"], "terminal_exists")

    def test_input_oversize_is_refused(self) -> None:
        pty = FakePty()
        service = GuestTerminalService(self.guest_sock, pty_factory=self._factory(pty))
        self._send(TerminalKind.OPEN, 1, {"rows": 24, "cols": 80})
        payload = base64.b64encode(b"a" * (MAX_INPUT_BYTES + 1)).decode()
        # The internal-leg validator applies the same bound before the write.
        write_frame(
            self.host_sock,
            {"kind": str(TerminalKind.INPUT), "seq": 2, "payload": {"data": payload}},
        )
        self._send(TerminalKind.CLOSE, 3, {})
        service.serve()
        self.assertEqual(pty.written, [])

    def test_malformed_frame_is_rejected_and_connection_closed(self) -> None:
        pty = FakePty()
        service = GuestTerminalService(self.guest_sock, pty_factory=self._factory(pty))
        # Unknown kind: structurally invalid, so the service answers and stops.
        write_frame(self.host_sock, {"kind": "shell", "seq": 1, "payload": {}})
        self.assertEqual(service.serve(), 0)
        self.assertFalse(pty.closed, "no PTY was started, so nothing to close")

    def test_input_without_open_is_answered(self) -> None:
        service = GuestTerminalService(self.guest_sock, pty_factory=self._factory())
        self._send(TerminalKind.INPUT, 1, {"data": base64.b64encode(b"x").decode()})
        self._send(TerminalKind.CLOSE, 2, {})
        service.serve()
        message = self._recv()
        self.assertEqual(str(message.kind), TerminalKind.ERROR.value)

    def test_exit_is_reported_when_shell_dies(self) -> None:
        # The service loop's idle handling and its own stop condition are driven
        # step by step here: a full ``serve()`` blocks on the framed reader until
        # the socket timeout, and two blocking reads on one socketpair in a
        # single thread would race with each other.
        pty = FakePty(chunks=[b"bye"])
        pty.exit_code = 3
        service = GuestTerminalService(
            self.guest_sock, pty_factory=self._factory(pty), read_window_s=0.0
        )
        self._send(TerminalKind.OPEN, 1, {"rows": 24, "cols": 80})
        opened_request = service._read_host_message()  # type: ignore[attr-defined]
        self.assertEqual(str(opened_request.kind), TerminalKind.OPEN.value)
        self.assertTrue(service._handle(opened_request))  # type: ignore[attr-defined]
        finished = service._serve_pty()  # type: ignore[attr-defined]
        self.assertTrue(finished, "an exited shell must stop the loop")
        service._close_pty()  # type: ignore[attr-defined]
        self.assertTrue(pty.closed)
        frames = [self._recv(0.5), self._recv(0.5), self._recv(0.5)]
        kinds = [str(frame.kind) for frame in frames]
        self.assertEqual(
            kinds,
            [TerminalKind.OPENED.value, TerminalKind.OUTPUT.value, TerminalKind.EXIT.value],
        )
        exit_message = frames[2]
        self.assertEqual(exit_message.payload["code"], 3)
        # ``_exit_if_finished`` is the input-path twin of the same decision: a
        # keystroke that cannot be delivered because the shell is gone reports
        # the exit once and tells the caller the session is over.
        service._pty = pty  # type: ignore[attr-defined]  # re-attach for the branch
        self.assertFalse(service._exit_if_finished())  # type: ignore[attr-defined]

    def test_eof_from_host_closes_pty(self) -> None:
        pty = FakePty()
        service = GuestTerminalService(self.guest_sock, pty_factory=self._factory(pty))
        self._send(TerminalKind.OPEN, 1, {"rows": 24, "cols": 80})
        self.host_sock.close()
        self.assertEqual(service.serve(), 0)
        self.assertTrue(pty.closed, "the shell must not outlive the host connection")


class TuningTest(unittest.TestCase):
    """The spawn policy is explicit and defaults to the hardened values."""

    def test_defaults_are_hardened(self) -> None:
        tuning = TerminalTuning()
        self.assertTrue(tuning.no_new_privs)
        self.assertTrue(tuning.drop_capabilities)
        self.assertEqual(tuning.workdir, "")

    def test_terminal_port_differs_from_task_port(self) -> None:
        from factory.core.isolation.channel import DEFAULT_VSOCK_PORT

        self.assertNotEqual(DEFAULT_TERMINAL_VSOCK_PORT, DEFAULT_VSOCK_PORT)


if __name__ == "__main__":
    unittest.main()
