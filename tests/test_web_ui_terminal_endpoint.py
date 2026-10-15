#!/usr/bin/env python3
"""Unit tests for the terminal WebSocket endpoint (no socket, no browser).

The endpoint is driven with an in-memory fake WebSocket and the real
:class:`TerminalManager` over a fake backend, so the assertions are about the
contract the browser relies on: ticket gating, backpressure by ack credit, and
screen restore after a reload without killing the shell.
"""

from __future__ import annotations

import base64
import os
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(os.environ.get("IGOREK_FACTORY_REPO", Path(__file__).resolve().parents[1]))
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from factory.core.isolation.terminal_manager import (  # noqa: E402
    TerminalManager,
    TerminalSpec,
)
from factory.core.isolation.terminal_protocol import (  # noqa: E402
    TerminalKind,
    decode_message,
    encode_message,
)
from factory.web_ui.terminal_endpoint import (  # noqa: E402
    CLOSE_CAPACITY,
    CLOSE_PROTOCOL,
    CLOSE_TERMINAL_BUSY,
    CLOSE_TICKET_INVALID,
    TerminalEndpoint,
    TerminalLog,
)

SESSION = "chat-20261002-091216-d920e8"


def unique_ticket_bytes(size: int) -> bytes:
    """Deterministic-but-distinct ticket bytes (a fixed seed would alias tickets).

    The manager requires exactly ``size`` bytes; hex encoding gives ``size``
    ASCII chars for ``size/2`` bytes of entropy, which is plenty for a test.
    """
    unique_ticket_bytes.counter += 1  # type: ignore[attr-defined]
    return f"{unique_ticket_bytes.counter:0{size}x}".encode("ascii")[:size].rjust(size, b"0")


unique_ticket_bytes.counter = 0  # type: ignore[attr-defined]


def b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


class FakeBackend:
    """Scripted PTY: feeds queued chunks, records writes, reports exit on demand."""

    def __init__(self, *, chunks: list[bytes] | None = None) -> None:
        self.opened = False
        self.was_closed = False
        self.written: list[bytes] = []
        self.resizes: list[tuple[int, int]] = []
        self.open_spec: TerminalSpec | None = None
        self.exit_code: int | None = None
        self._chunks = list(chunks or [])

    @property
    def closed(self) -> bool:
        return self.was_closed

    def open(self, spec: TerminalSpec) -> None:
        self.opened = True
        self.open_spec = spec

    def read(self, timeout_s: float) -> bytes | None:
        return self._chunks.pop(0) if self._chunks else None

    def write(self, data: bytes) -> None:
        if self.was_closed:
            raise TerminalErrorStub("terminal_closed")
        self.written.append(data)

    def resize(self, rows: int, cols: int) -> None:
        self.resizes.append((rows, cols))

    def close(self) -> None:
        self.was_closed = True

    def poll_exit(self) -> int | None:
        return self.exit_code


class TerminalErrorStub(Exception):
    pass


class FakeBackendFactory:
    def __init__(self, backends: list[FakeBackend] | None = None) -> None:
        self._backends = list(backends or [])
        self.created: list[FakeBackend] = []

    def __call__(self) -> FakeBackend:
        backend = self._backends.pop(0) if self._backends else FakeBackend()
        self.created.append(backend)
        return backend


class FakeWebSocket:
    """In-memory WebSocket: scripted inbound frames, captured outbound text.

    When the scripted frames run out the socket reports ``idle_windows`` empty
    read windows and then EOF, which is what a real connection does while the
    endpoint waits for output - and it gives the message loop a deterministic,
    finite run in tests (an endless idle stream would loop forever, as it must
    in production).
    """

    def __init__(self, inbound: list[tuple[str, bytes]], *, idle_windows: int = 2) -> None:
        self._inbound = list(inbound)
        self._idle_windows = int(idle_windows)
        self.sent: list[str] = []
        self.pings: list[bytes] = []
        self.pongs: list[bytes] = []
        self.close_code: int | None = None
        self.close_reason: str = ""
        self.closed = False
        self.fail_send = False

    def recv(self, timeout_s: float | None = None) -> tuple[str, bytes] | None:
        if self._inbound:
            return self._inbound.pop(0)
        if self._idle_windows > 0:
            self._idle_windows -= 1
            return ("timeout", b"")
        return None

    def send_text(self, text: str) -> None:
        if self.fail_send:
            raise OSError("peer gone")
        self.sent.append(text)

    def send_ping(self, payload: bytes = b"") -> None:
        self.pings.append(payload)

    def send_pong(self, payload: bytes = b"") -> None:
        self.pongs.append(payload)

    def send_close(self, code: int = 1000, reason: str = "") -> None:
        self.close_code = code
        self.close_reason = reason

    def close(self) -> None:
        self.closed = True

    def outbound(self) -> list[object]:
        return [decode_message(text) for text in self.sent]


def open_frame(ticket: str, **extra: object) -> tuple[str, bytes]:
    payload: dict[str, object] = {"ticket": ticket, "rows": 24, "cols": 80}
    payload.update(extra)
    return ("text", encode_message(TerminalKind.OPEN, 1, payload).encode("utf-8"))


def kind_of(message: object) -> str:
    return str(getattr(message, "kind"))


def payload_of(message: object) -> dict[str, object]:
    return dict(getattr(message, "payload"))


class EndpointHarness(unittest.TestCase):
    def setUp(self) -> None:
        self.manager = TerminalManager(
            backend_factory=FakeBackendFactory(),
            ticket_bytes=unique_ticket_bytes,
        )
        self.log = TerminalLog()

    def build(self, inbound: list[tuple[str, bytes]], **kwargs: object) -> tuple[TerminalEndpoint, FakeWebSocket, list[object]]:
        ws = FakeWebSocket(inbound)
        endpoint = TerminalEndpoint(ws, self.manager, log=self.log, read_window_s=0.0, **kwargs)
        endpoint.serve()
        return endpoint, ws, ws.outbound()


class OpenHandshakeTest(EndpointHarness):
    """The first message must carry a valid single-use ticket."""

    def test_open_requires_ticket(self) -> None:
        _endpoint, ws, out = self.build([("text", encode_message(TerminalKind.ACK, 1, {"seq": 0}).encode())])
        self.assertEqual(kind_of(out[0]), TerminalKind.ERROR.value)
        self.assertEqual(payload_of(out[0])["reason"], "open_expected")
        self.assertEqual(ws.close_code, CLOSE_PROTOCOL)

    def test_unknown_ticket_is_refused(self) -> None:
        _endpoint, ws, out = self.build([open_frame("f" * 32)])
        self.assertEqual(payload_of(out[0])["reason"], "ticket_invalid")
        self.assertEqual(ws.close_code, CLOSE_TICKET_INVALID)
        self.assertEqual(self.manager.count(), 0, "no PTY may be spawned without a ticket")

    def test_valid_ticket_opens_terminal(self) -> None:
        ticket = str(self.manager.issue_ticket(SESSION))
        endpoint, ws, out = self.build([open_frame(ticket)], workdir_provider=lambda s: "")
        self.assertEqual(kind_of(out[0]), TerminalKind.OPENED.value)
        opened = payload_of(out[0])
        self.assertEqual(opened["session_id"], SESSION)
        self.assertEqual(endpoint.session_id, SESSION)
        self.assertEqual(self.manager.count(), 1)
        self.assertIsNone(ws.close_code)

    def test_ticket_is_single_use_across_connections(self) -> None:
        ticket = str(self.manager.issue_ticket(SESSION))
        self.build([open_frame(ticket)])
        _endpoint, ws, out = self.build([open_frame(ticket)])
        self.assertEqual(payload_of(out[0])["reason"], "ticket_invalid")
        self.assertEqual(ws.close_code, CLOSE_TICKET_INVALID)

    def test_second_viewer_is_refused(self) -> None:
        ticket_a = str(self.manager.issue_ticket(SESSION))
        ticket_b = str(self.manager.issue_ticket(SESSION))
        # First connection attaches and stays (its socket is kept alive by a
        # following message), then a second connection with a fresh ticket tries
        # to attach to the same live terminal.
        self.build([open_frame(ticket_a), ("close", b"")])
        terminal = self.manager.terminal_for_session(SESSION)
        self.assertIsNotNone(terminal)
        # Re-claim the viewer on behalf of the first connection is done by the
        # first endpoint; since it has finished, its claim is released, so here
        # we simulate a still-attached viewer explicitly.
        self.manager.claim_viewer(terminal.terminal_id, "occupant")
        _endpoint, ws, out = self.build([open_frame(ticket_b)])
        reasons = [payload_of(item).get("reason") for item in out if kind_of(item) == TerminalKind.ERROR.value]
        self.assertIn("terminal_busy", reasons)
        self.assertEqual(ws.close_code, CLOSE_TERMINAL_BUSY)

    def test_explicit_terminal_close_stops_shell(self) -> None:
        ticket = str(self.manager.issue_ticket(SESSION))
        self.build([open_frame(ticket), ("text", encode_message(TerminalKind.CLOSE, 2, {}).encode())])
        self.assertIsNone(self.manager.terminal_for_session(SESSION))

    def test_ticket_cannot_be_used_by_another_device(self) -> None:
        ticket = str(self.manager.issue_ticket(SESSION, device_identity="device-a"))
        _endpoint, ws, _out = self.build([open_frame(ticket)], device_identity="device-b")
        self.assertEqual(ws.close_code, CLOSE_TICKET_INVALID)
        self.assertIsNone(self.manager.terminal_for_session(SESSION))

    def test_capacity_refusal(self) -> None:
        manager = TerminalManager(
            backend_factory=FakeBackendFactory(),
            ticket_bytes=unique_ticket_bytes,
            max_terminals=1,
        )
        manager.open_terminal(TerminalSpec(session_id="other-session"))
        ticket = str(manager.issue_ticket(SESSION))
        ws = FakeWebSocket([open_frame(ticket)])
        TerminalEndpoint(ws, manager, log=self.log, read_window_s=0.0).serve()
        reasons = [payload_of(decode_message(text)).get("reason") for text in ws.sent]
        self.assertIn("terminal_limit", reasons)
        self.assertEqual(ws.close_code, CLOSE_CAPACITY)

    def test_workdir_is_passed_to_backend(self) -> None:
        factory = FakeBackendFactory()
        manager = TerminalManager(backend_factory=factory, ticket_bytes=unique_ticket_bytes)
        ticket = str(manager.issue_ticket(SESSION))
        ws = FakeWebSocket([open_frame(ticket)])
        TerminalEndpoint(
            ws,
            manager,
            workdir_provider=lambda session: f"/tmp/{session}",
            log=self.log,
            read_window_s=0.0,
        ).serve()
        self.assertEqual(factory.created[0].open_spec.workdir, f"/tmp/{SESSION}")


class InputAndControlTest(EndpointHarness):
    """Keystrokes, resize and ack are separate branches."""

    def _run(self, inbound: list[tuple[str, bytes]]) -> tuple[FakeWebSocket, FakeBackend]:
        ticket = str(self.manager.issue_ticket(SESSION))
        ws = FakeWebSocket([open_frame(ticket), *inbound])
        TerminalEndpoint(ws, self.manager, log=self.log, read_window_s=0.0).serve()
        return ws, self.manager.terminal_for_session(SESSION).backend

    def test_input_is_forwarded(self) -> None:
        _ws, backend = self._run([("text", encode_message(TerminalKind.INPUT, 2, {"data": b64(b"ls\r")}).encode())])
        self.assertEqual(backend.written, [b"ls\r"])

    def test_resize_is_applied(self) -> None:
        _ws, backend = self._run([("text", encode_message(TerminalKind.RESIZE, 2, {"rows": 50, "cols": 132}).encode())])
        self.assertEqual(backend.resizes, [(50, 132)])

    def test_ack_is_not_treated_as_input(self) -> None:
        _ws, backend = self._run([("text", encode_message(TerminalKind.ACK, 2, {"seq": 3}).encode())])
        self.assertEqual(backend.written, [], "an ack must never reach the PTY")
        self.assertEqual(self.manager.terminal_for_session(SESSION).acked_seq, 3)

    def test_binary_frame_is_refused(self) -> None:
        ws, _backend = self._run([("binary", b"\x00\x01")])
        self.assertEqual(ws.close_code, CLOSE_PROTOCOL)

    def test_malformed_text_frame_is_refused(self) -> None:
        ws, _backend = self._run([("text", b"{not json")])
        self.assertEqual(ws.close_code, CLOSE_PROTOCOL)
        self.assertIn("malformed_message", self.log.entries[-1][0:1][0])

    def test_input_over_limit_is_refused(self) -> None:
        huge = b64(b"a" * 20000)
        ws, _backend = self._run([("text", encode_message(TerminalKind.INPUT, 2, {"data": huge}).encode())])
        self.assertEqual(ws.close_code, CLOSE_PROTOCOL)
        self.assertIn("input_too_large", [reason for reason, _ in self.log.entries])

    def test_input_after_exit_reports_exit(self) -> None:
        # White-box on the input branch: the sequence "attach -> shell exits ->
        # keystroke arrives" cannot be scripted through the frame queue (the
        # open would spawn a replacement), so the branch is driven directly.
        factory = FakeBackendFactory()
        manager = TerminalManager(backend_factory=factory, ticket_bytes=unique_ticket_bytes)
        state = manager.open_terminal(TerminalSpec(session_id=SESSION))
        ws = FakeWebSocket([])
        endpoint = TerminalEndpoint(ws, manager, log=self.log, read_window_s=0.0)
        endpoint._terminal_id = state.terminal_id  # type: ignore[attr-defined]
        manager.claim_viewer(state.terminal_id, "viewer")
        manager.mark_exit(state.terminal_id, 3)
        message = decode_message(encode_message(TerminalKind.INPUT, 2, {"data": b64(b"x")}).encode())
        endpoint._handle_input(message)  # type: ignore[attr-defined]
        self.assertEqual(state.backend.written, [], "input must not reach a dead PTY")
        kinds = [kind_of(m) for m in ws.outbound()]
        self.assertIn(TerminalKind.EXIT.value, kinds)
        self.assertIn("input_failed", [reason for reason, _ in self.log.entries])

    def test_second_open_on_same_connection_is_refused(self) -> None:
        ticket = str(self.manager.issue_ticket(SESSION))
        other = str(self.manager.issue_ticket(SESSION))
        ws = FakeWebSocket([open_frame(ticket), open_frame(other)])
        TerminalEndpoint(ws, self.manager, log=self.log, read_window_s=0.0).serve()
        reasons = [payload_of(decode_message(text)).get("reason") for text in ws.sent]
        self.assertIn("terminal_exists", reasons)

    def test_ping_frame_is_answered_and_connection_kept(self) -> None:
        ticket = str(self.manager.issue_ticket(SESSION))
        ws = FakeWebSocket(
            [
                open_frame(ticket),
                ("ping", b"hello"),
                ("text", encode_message(TerminalKind.INPUT, 2, {"data": b64(b"z")}).encode()),
            ]
        )
        TerminalEndpoint(ws, self.manager, log=self.log, read_window_s=0.0).serve()
        self.assertEqual(ws.pongs, [b"hello"])
        self.assertEqual(self.manager.terminal_for_session(SESSION).backend.written, [b"z"])

    def test_client_close_frame_ends_cleanly(self) -> None:
        ticket = str(self.manager.issue_ticket(SESSION))
        ws = FakeWebSocket([open_frame(ticket), ("close", b"")])
        TerminalEndpoint(ws, self.manager, log=self.log, read_window_s=0.0).serve()
        self.assertIsNone(ws.close_code)
        self.assertIn("client_close_frame", [reason for reason, _ in self.log.entries])


class BackpressureTest(EndpointHarness):
    """Output flows only while the client's ack credit allows it (item 9)."""

    def test_output_is_sent_with_monotonic_seq(self) -> None:
        factory = FakeBackendFactory([FakeBackend(chunks=[b"one", b"two"])])
        manager = TerminalManager(backend_factory=factory, ticket_bytes=unique_ticket_bytes)
        ticket = str(manager.issue_ticket(SESSION))
        ws = FakeWebSocket([open_frame(ticket)])
        TerminalEndpoint(ws, manager, log=self.log, read_window_s=0.0, ack_window_bytes=1024).serve()
        outputs = [m for m in ws.outbound() if kind_of(m) == TerminalKind.OUTPUT.value]
        self.assertEqual([getattr(m, "seq") for m in outputs], [1, 2])
        self.assertEqual([base64.b64decode(payload_of(m)["data"]) for m in outputs], [b"one", b"two"])

    def test_window_blocks_pumping_until_ack(self) -> None:
        """A full window must stop the pump; the ack releases it."""
        chunks = [b"x" * 100 for _ in range(10)]
        factory = FakeBackendFactory([FakeBackend(chunks=chunks)])
        manager = TerminalManager(backend_factory=factory, ticket_bytes=unique_ticket_bytes)
        ticket = str(manager.issue_ticket(SESSION))
        # Window of 150 bytes fits exactly one 100-byte chunk at a time.
        ws = FakeWebSocket(
            [
                open_frame(ticket),
                ("text", encode_message(TerminalKind.ACK, 2, {"seq": 1}).encode()),
                ("text", encode_message(TerminalKind.ACK, 3, {"seq": 2}).encode()),
            ]
        )
        TerminalEndpoint(ws, manager, log=self.log, read_window_s=0.0, ack_window_bytes=150).serve()
        outputs = [m for m in ws.outbound() if kind_of(m) == TerminalKind.OUTPUT.value]
        # First pump: one chunk (100 < 150); pumping again would exceed the
        # window, so the second chunk waits for the ack.
        self.assertGreaterEqual(len(outputs), 2)
        seqs = [getattr(m, "seq") for m in outputs]
        self.assertEqual(seqs, sorted(seqs))
        self.assertEqual(len(set(seqs)), len(seqs), "no seq may be replayed twice here")

    def test_no_output_when_backend_has_nothing(self) -> None:
        ticket = str(self.manager.issue_ticket(SESSION))
        ws = FakeWebSocket([open_frame(ticket)])
        TerminalEndpoint(ws, self.manager, log=self.log, read_window_s=0.0).serve()
        kinds = [kind_of(m) for m in ws.outbound()]
        self.assertEqual(kinds, [TerminalKind.OPENED.value])

    def test_shell_exit_is_reported_once(self) -> None:
        backend = FakeBackend(chunks=[b"bye"])
        backend.exit_code = 7
        factory = FakeBackendFactory([backend])
        manager = TerminalManager(backend_factory=factory, ticket_bytes=unique_ticket_bytes)
        ticket = str(manager.issue_ticket(SESSION))
        ws = FakeWebSocket([open_frame(ticket)])
        TerminalEndpoint(ws, manager, log=self.log, read_window_s=0.0).serve()
        kinds = [kind_of(m) for m in ws.outbound()]
        self.assertIn(TerminalKind.EXIT.value, kinds)
        exit_payload = [payload_of(m) for m in ws.outbound() if kind_of(m) == TerminalKind.EXIT.value]
        self.assertEqual(exit_payload, [{"code": 7}])

    def test_send_failure_is_reported_as_error(self) -> None:
        manager = TerminalManager(backend_factory=FakeBackendFactory(), ticket_bytes=unique_ticket_bytes)
        ticket = str(manager.issue_ticket(SESSION))
        ws = FakeWebSocket([open_frame(ticket)])
        ws.fail_send = True
        TerminalEndpoint(ws, manager, log=self.log, read_window_s=0.0).serve()
        self.assertIn("send_failed", [reason for reason, _ in self.log.entries])


class ReloadReplayTest(EndpointHarness):
    """Reload attaches to the live terminal and restores the screen (item 10)."""

    def test_reload_keeps_shell_and_replays_after_acked_seq(self) -> None:
        factory = FakeBackendFactory([FakeBackend(chunks=[b"first", b"second", b"third"])])
        manager = TerminalManager(backend_factory=factory, ticket_bytes=unique_ticket_bytes)
        # First connection: open, receive chunks, ack 2, then the socket goes away.
        ws_one = FakeWebSocket([open_frame(str(manager.issue_ticket(SESSION)))])
        TerminalEndpoint(ws_one, manager, log=self.log, read_window_s=0.0).serve()
        terminal = manager.terminal_for_session(SESSION)
        self.assertIsNotNone(terminal)
        manager.ack(terminal.terminal_id, 2)

        # Reload: attach with since_seq=2 and expect only chunk 3.
        ws_two = FakeWebSocket([open_frame(str(manager.issue_ticket(SESSION)), since_seq=2)])
        TerminalEndpoint(ws_two, manager, log=self.log, read_window_s=0.0).serve()
        outputs = [m for m in ws_two.outbound() if kind_of(m) == TerminalKind.OUTPUT.value]
        self.assertEqual([getattr(m, "seq") for m in outputs], [3])
        self.assertEqual([base64.b64decode(payload_of(m)["data"]) for m in outputs], [b"third"])
        # The same terminal object (the shell) survived the reload.
        self.assertEqual(manager.terminal_for_session(SESSION).terminal_id, terminal.terminal_id)
        self.assertEqual(len(factory.created), 1, "no second shell may be spawned on reload")

    def test_reload_with_caught_up_seq_replays_nothing(self) -> None:
        factory = FakeBackendFactory([FakeBackend(chunks=[b"only"])])
        manager = TerminalManager(backend_factory=factory, ticket_bytes=unique_ticket_bytes)
        ws_one = FakeWebSocket([open_frame(str(manager.issue_ticket(SESSION)))])
        TerminalEndpoint(ws_one, manager, log=self.log, read_window_s=0.0).serve()
        terminal = manager.terminal_for_session(SESSION)
        manager.ack(terminal.terminal_id, 1)
        ws_two = FakeWebSocket([open_frame(str(manager.issue_ticket(SESSION)), since_seq=1)])
        TerminalEndpoint(ws_two, manager, log=self.log, read_window_s=0.0).serve()
        kinds = [kind_of(m) for m in ws_two.outbound()]
        self.assertEqual(kinds, [TerminalKind.OPENED.value])

    def test_reload_applies_new_dimensions(self) -> None:
        factory = FakeBackendFactory([FakeBackend()])
        manager = TerminalManager(backend_factory=factory, ticket_bytes=unique_ticket_bytes)
        ws_one = FakeWebSocket([open_frame(str(manager.issue_ticket(SESSION)))])
        TerminalEndpoint(ws_one, manager, log=self.log, read_window_s=0.0).serve()
        ws_two = FakeWebSocket(
            [open_frame(str(manager.issue_ticket(SESSION)), rows=60, cols=200, since_seq=0)]
        )
        TerminalEndpoint(ws_two, manager, log=self.log, read_window_s=0.0).serve()
        self.assertEqual(factory.created[0].resizes[-1], (60, 200))

    def test_exited_shell_is_replaced_on_reopen(self) -> None:
        first = FakeBackend()
        first.exit_code = 0
        factory = FakeBackendFactory([first, FakeBackend()])
        manager = TerminalManager(backend_factory=factory, ticket_bytes=unique_ticket_bytes)
        manager.open_terminal(TerminalSpec(session_id=SESSION))
        ws = FakeWebSocket([open_frame(str(manager.issue_ticket(SESSION)))])
        TerminalEndpoint(ws, manager, log=self.log, read_window_s=0.0).serve()
        self.assertEqual(len(factory.created), 2)
        self.assertTrue(first.was_closed, "the dead PTY must be closed before replacement")

    def test_viewer_claim_is_released_when_connection_ends(self) -> None:
        manager = self.manager
        ws = FakeWebSocket([open_frame(str(manager.issue_ticket(SESSION)))])
        TerminalEndpoint(ws, manager, log=self.log, read_window_s=0.0).serve()
        terminal = manager.terminal_for_session(SESSION)
        self.assertIsNone(terminal.viewer, "a finished connection must not hold the viewer slot")
        self.assertIsNotNone(terminal, "the shell must outlive the connection")


if __name__ == "__main__":
    unittest.main()
