"""WebSocket <-> :class:`TerminalManager` endpoint (host-side terminal bridge).

One browser connection = one :class:`TerminalEndpoint`. The endpoint is the only
place where the browser-facing WebSocket and the terminal manager meet, and it
enforces the two properties the terminal design depends on:

* **Backpressure with ack (terminal item 9).** Output is forwarded to the browser
  only while the number of *unacknowledged* bytes is below
  :data:`DEFAULT_ACK_WINDOW_BYTES`. The client sends ``ack`` for the highest
  output ``seq`` it has painted in xterm; the endpoint then frees that much
  credit and pumps again. A slow (or backgrounded) browser therefore throttles
  the PTY reader instead of growing a host-side buffer without bound. Control
  messages (``ack``/``resize``) are handled on their own branch and are never
  treated as keystrokes.
* **Screen restore after reload (terminal item 10).** ``open`` carries the last
  acked ``seq``; the manager replays exactly the chunks after it from a bounded
  ring. Crucially, a reload **attaches** to the terminal that is still alive for
  that session - the shell keeps running and the visible screen is rebuilt - and
  a replayed chunk can never be painted twice because replay starts strictly
  after the acknowledged seq.

Transport-agnostic on purpose: the endpoint talks to any object implementing
:class:`WebSocketLike` (text frames in, text frames out), so unit tests drive it
with an in-memory pair and no real socket.
"""

from __future__ import annotations

import base64
import logging
import secrets
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Final, Protocol

from ..core.isolation.terminal_manager import (
    TerminalError,
    TerminalManager,
    TerminalSpec,
)
from ..core.isolation.terminal_protocol import (
    TerminalKind,
    TerminalMessage,
    TerminalProtocolError,
    decode_bounded,
    decode_message,
    encode_message,
)

logger = logging.getLogger(__name__)

#: Unacknowledged output bytes allowed before the endpoint stops pumping.
DEFAULT_ACK_WINDOW_BYTES: Final[int] = 256 * 1024
#: Read window while idle: short enough for low latency, long enough to be cheap.
DEFAULT_READ_WINDOW_S: Final[float] = 0.05
#: Keepalive ping interval (some proxies drop silent sockets).
DEFAULT_PING_INTERVAL_S: Final[float] = 20.0
#: WebSocket close codes for the application-level failures (RFC 6455 §7.4.2 range).
CLOSE_TICKET_INVALID: Final[int] = 4401
CLOSE_TERMINAL_BUSY: Final[int] = 4409
CLOSE_PROTOCOL: Final[int] = 4400
CLOSE_CAPACITY: Final[int] = 4429


class WebSocketLike(Protocol):
    """The slice of the WebSocket the endpoint uses."""

    def recv(self, timeout_s: float | None = None) -> tuple[str, bytes] | None: ...
    def send_text(self, text: str) -> None: ...
    def send_ping(self, payload: bytes = b"") -> None: ...
    def send_pong(self, payload: bytes = b"") -> None: ...
    def send_close(self, code: int = 1000, reason: str = "") -> None: ...
    def close(self) -> None: ...


@dataclass(frozen=True, slots=True)
class _Idle:
    """Marker result: the read window elapsed with no client message."""


IDLE = _Idle()


class TerminalLog:
    """Thin sink so tests can assert what the client was told (no log noise)."""

    def __init__(self) -> None:
        self.entries: list[tuple[str, str]] = []

    def record(self, reason: str, detail: str = "") -> None:
        self.entries.append((reason, detail))
        logger.info("terminal endpoint: %s%s", reason, f" ({detail})" if detail else "")


class TerminalEndpoint:
    """Serve one browser terminal connection until it closes or the shell exits."""

    def __init__(
        self,
        ws: WebSocketLike,
        manager: TerminalManager,
        *,
        workdir_provider: Callable[[str], str] | None = None,
        access_validator: Callable[[str], str] | None = None,
        device_identity: str = "",
        ack_window_bytes: int = DEFAULT_ACK_WINDOW_BYTES,
        read_window_s: float = DEFAULT_READ_WINDOW_S,
        ping_interval_s: float = DEFAULT_PING_INTERVAL_S,
        clock: Callable[[], float] | None = None,
        log: TerminalLog | None = None,
    ) -> None:
        self._ws = ws
        self._manager = manager
        self._workdir_provider = workdir_provider or (lambda _session: "")
        self._access_validator = access_validator
        self._device_identity = device_identity
        self._last_access_check = 0.0
        self._window = max(1, int(ack_window_bytes))
        self._read_window_s = max(0.0, float(read_window_s))
        self._ping_interval_s = max(1.0, float(ping_interval_s))
        self._clock = clock or time.monotonic
        self._log = log or TerminalLog()
        self._viewer_token = secrets.token_hex(8)
        self._terminal_id: str | None = None
        self._session_id: str | None = None
        self._next_server_seq = 1
        self._last_ping = self._clock()

    @property
    def log(self) -> TerminalLog:
        return self._log

    @property
    def terminal_id(self) -> str | None:
        return self._terminal_id

    @property
    def session_id(self) -> str | None:
        return self._session_id

    def reasons(self) -> list[str]:
        """Reasons recorded so far (test/diagnostic helper)."""
        return [reason for reason, _detail in self._log.entries]

    # ------------------------------------------------------------------ serve

    def serve(self) -> None:
        """Run the message loop; every failure is reported before closing."""
        try:
            if not self._await_open():
                return
            self._run_loop()
        except (TerminalError, TerminalProtocolError) as exc:
            reason = getattr(exc, "reason", "terminal_error")
            self._fail(reason, str(exc), CLOSE_PROTOCOL)
        except Exception as exc:  # the socket must always close
            logger.warning("terminal endpoint: unexpected failure: %s", exc)
            self._fail("terminal_internal_error", str(exc), CLOSE_PROTOCOL)
        finally:
            self._teardown()

    def _teardown(self) -> None:
        """Release the viewer claim and close the socket (idempotent).

        The **PTY is not closed here**: a browser reload must not kill the shell
        (item 10). The terminal survives until the session is stopped, the shell
        exits, or the manager's capacity policy evicts it.
        """
        if self._terminal_id is not None:
            self._manager.release_viewer(self._terminal_id, self._viewer_token)
        try:
            self._ws.close()
        except Exception:  # teardown must not raise
            pass

    def _await_open(self) -> bool:
        """First client message must be ``open`` carrying a valid ticket.

        The endpoint refuses (with 4400 + an ``open_expected`` error) anything
        else: ``open`` is the only unauthenticated message, and every other kind
        would act on a terminal that does not exist yet.
        """
        while True:
            result = self._read_message()
            if result is None:
                return False
            if isinstance(result, _Idle):
                continue
            if result.kind is TerminalKind.OPEN:
                return self._handle_open(result)
            self._fail("open_expected", f"first message was {result.kind}", CLOSE_PROTOCOL)
            return False

    def _handle_open(self, message: TerminalMessage) -> bool:
        ticket = str(message.payload.get("ticket") or "")
        try:
            record = self._manager.consume_ticket(ticket)
            if record.device_identity and not secrets.compare_digest(record.device_identity, self._device_identity):
                raise TerminalError("ticket_device_mismatch")
        except TerminalError as exc:
            self._fail(exc.reason, exc.detail, CLOSE_TICKET_INVALID)
            return False
        session_id = record.session_id
        workdir = self._workdir_provider(session_id)
        rows = int(message.payload.get("rows", 0) or 0)
        cols = int(message.payload.get("cols", 0) or 0)
        since_seq = int(message.payload.get("since_seq", 0) or 0)

        state = self._manager.terminal_for_session(session_id)
        # "Still alive" is decided by the backend, not by our bookkeeping: a
        # shell may have died while nobody was attached, and reopening must
        # start a new one instead of attaching to a corpse.
        reused = state is not None and self._manager.poll_exit(state.terminal_id) is None
        if reused:
            try:
                state = self._manager.claim_viewer(state.terminal_id, self._viewer_token)
            except TerminalError as exc:
                self._fail(exc.reason, exc.detail, CLOSE_TERMINAL_BUSY)
                return False
        else:
            if state is not None:
                # The previous shell already exited: it is replaced, and the
                # manager closes its PTY before the new one is spawned.
                self._manager.close_terminal(state.terminal_id)
            try:
                state = self._manager.open_terminal(
                    TerminalSpec(
                        session_id=session_id,
                        rows=rows or 30,
                        cols=cols or 100,
                        workdir=workdir,
                    )
                )
            except TerminalError as exc:
                code = CLOSE_CAPACITY if exc.reason == "terminal_limit" else CLOSE_PROTOCOL
                self._fail(exc.reason, exc.detail, code)
                return False
            try:
                self._manager.claim_viewer(state.terminal_id, self._viewer_token)
            except TerminalError as exc:  # pragma: no cover - fresh terminal cannot be claimed
                self._fail(exc.reason, exc.detail, CLOSE_PROTOCOL)
                return False

        self._terminal_id = state.terminal_id
        self._session_id = session_id
        if rows and cols and reused:
            # A reload may arrive with different dimensions (rotated phone):
            # apply them to the live PTY so the shell sees SIGWINCH.
            try:
                self._manager.resize(state.terminal_id, rows, cols)
            except TerminalError as exc:
                self._log.record("resize_failed", exc.reason)
        self._send(
            TerminalKind.OPENED,
            {
                "terminal_id": state.terminal_id,
                "session_id": session_id,
                "rows": state.rows,
                "cols": state.cols,
            },
        )
        for chunk in self._manager.replay(state.terminal_id, since_seq):
            self._send_output(chunk.seq, chunk.data)
        self._log.record(
            "attached" if reused else "opened",
            f"terminal {state.terminal_id} from seq {since_seq}",
        )
        return True

    # -------------------------------------------------------------------- loop

    def _run_loop(self) -> None:
        inflight = 0
        while True:
            if self._access_validator and self._clock() - self._last_access_check >= 5:
                try:
                    self._access_validator(self._session_id)
                except TerminalError:
                    self._manager.close_terminal(self._terminal_id)
                    raise
                self._last_access_check = self._clock()
            result = self._read_message()
            if result is None:
                return
            if isinstance(result, _Idle):
                if inflight < self._window:
                    inflight = self._pump(inflight)
                self._maybe_ping()
                if self._shell_exited():
                    return
                continue
            if result.kind is TerminalKind.INPUT:
                self._handle_input(result)
                continue
            if result.kind is TerminalKind.RESIZE:
                self._handle_resize(result)
                continue
            if result.kind is TerminalKind.ACK:
                try:
                    acked = self._manager.ack(self._terminal_id, int(result.payload["seq"]))
                except TerminalError as exc:
                    self._log.record("ack_failed", exc.reason)
                    continue
                inflight = self._unacked_bytes(acked)
                continue
            if result.kind is TerminalKind.CLOSE:
                self._manager.close_terminal(self._terminal_id)
                self._log.record("client_closed")
                return
            if result.kind is TerminalKind.OPEN:
                # A second open on one connection is a duplicate: refusing is
                # safer than silently re-binding a second viewer token.
                self._fail("terminal_exists", "already attached", CLOSE_PROTOCOL)
                return
            self._log.record("ignored_message", str(result.kind))

    def _handle_input(self, message: TerminalMessage) -> None:
        raw = message.payload.get("data")
        if not isinstance(raw, str):
            self._fail("payload_field_type", "input data", CLOSE_PROTOCOL)
            return
        try:
            data = decode_bounded(raw.encode("ascii"))
        except (UnicodeEncodeError, TerminalProtocolError) as exc:
            self._fail(getattr(exc, "reason", "payload_field_invalid"), "input", CLOSE_PROTOCOL)
            return
        try:
            self._manager.input(self._terminal_id, data)
        except TerminalError as exc:
            # A keystroke into an exited shell is not fatal: the client is told
            # and the loop keeps serving so the exit frame still arrives.
            self._log.record("input_failed", exc.reason)
            if exc.reason == "terminal_exited":
                self._shell_exited()

    def _handle_resize(self, message: TerminalMessage) -> None:
        try:
            self._manager.resize(
                self._terminal_id,
                int(message.payload["rows"]),
                int(message.payload["cols"]),
            )
        except TerminalError as exc:
            self._log.record("resize_failed", exc.reason)

    def _read_message(self) -> TerminalMessage | _Idle | None:
        """Read one client message; ``IDLE`` on an empty window, ``None`` on EOF.

        Control frames the browser may send (``ping``/``pong``) are answered or
        ignored here, so the frame loop above only ever sees application
        messages, idle windows, or end-of-connection.
        """
        while True:
            try:
                raw = self._ws.recv(self._read_window_s)
            except Exception as exc:  # transport, reported as EOF
                self._log.record("transport_error", type(exc).__name__)
                return None
            if raw is None:
                self._log.record("client_eof")
                return None
            kind, payload = raw
            if kind == "timeout":
                return IDLE
            if kind == "close":
                self._log.record("client_close_frame")
                return None
            if kind == "ping":
                try:
                    self._ws.send_pong(payload)
                except Exception:  # best effort
                    return None
                continue
            if kind == "pong":
                continue
            if kind != "text":
                self._fail("binary_not_supported", kind, CLOSE_PROTOCOL)
                return None
            try:
                return decode_message(payload)
            except TerminalProtocolError as exc:
                self._fail(exc.reason, exc.detail, CLOSE_PROTOCOL)
                return None

    # ------------------------------------------------------------------- pump

    def _pump(self, inflight: int) -> int:
        """Read at most ``window - inflight`` bytes worth of output.

        The read budget *is* the remaining ack credit: once the client owes us an
        ack, the PTY reader stops instead of buffering without bound. A single
        chunk may overshoot the remaining credit (a PTY read is atomic and can be
        larger than the window), which is bounded by one chunk and is why the
        window is checked *before* each pump as well.
        """
        budget = max(1, self._window - inflight)
        try:
            chunks = self._manager.pump(self._terminal_id, budget=budget)
        except TerminalError as exc:
            self._fail(exc.reason, exc.detail, CLOSE_PROTOCOL)
            return inflight
        for chunk in chunks:
            self._send_output(chunk.seq, chunk.data)
            inflight += len(chunk.data)
        return inflight

    def _unacked_bytes(self, acked_seq: int) -> int:
        state = self._manager.terminal(self._terminal_id)
        if state is None:
            return 0
        return sum(len(item.data) for item in state.replay if item.seq > acked_seq)

    def _send_output(self, seq: int, data: bytes) -> None:
        self._send(TerminalKind.OUTPUT, {"data": base64.b64encode(data).decode("ascii")}, seq=seq)

    def _shell_exited(self) -> bool:
        """Send the final output and one ``exit`` frame when the shell is gone."""
        try:
            code = self._manager.poll_exit(self._terminal_id)
        except TerminalError:
            return True
        if code is None:
            return False
        self._pump(self._window)
        self._send(TerminalKind.EXIT, {"code": int(code)})
        self._log.record("shell_exited", str(code))
        return True

    def _maybe_ping(self) -> None:
        now = self._clock()
        if now - self._last_ping < self._ping_interval_s:
            return
        self._last_ping = now
        try:
            self._ws.send_ping(b"")
        except Exception:  # keepalive is best-effort
            self._log.record("ping_failed")

    # ------------------------------------------------------------------ output

    def _send(self, kind: TerminalKind, payload: dict[str, object], *, seq: int | None = None) -> None:
        message_seq = self._next_server_seq if seq is None else seq
        if seq is None:
            self._next_server_seq += 1
        try:
            self._ws.send_text(encode_message(kind, message_seq, payload))
        except Exception as exc:  # the peer is gone
            raise TerminalError("send_failed", type(exc).__name__) from exc

    def _send_error(self, reason: str) -> None:
        try:
            self._send(TerminalKind.ERROR, {"reason": reason[:128]})
        except TerminalError:
            pass

    def _fail(self, reason: str, detail: str, close_code: int) -> None:
        self._log.record(reason, detail)
        self._send_error(reason)
        try:
            self._ws.send_close(close_code, reason[:123])
        except Exception:  # closing is best-effort
            pass
