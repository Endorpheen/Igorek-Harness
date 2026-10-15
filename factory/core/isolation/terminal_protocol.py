"""Versioned terminal protocol between the local web client and the terminal owner.

The interactive terminal is a **separate** trust domain from the task channel
(:mod:`factory.core.isolation.channel`): the task channel forbids shell-named
payload keys by design (``_SHELL_OP_NAMES``), while a terminal is, by
definition, raw shell I/O. This module therefore defines its own wire vocabulary
and its own structural payload policy instead of widening the task channel.

Topology
--------

    browser  --WSS-->  local web server (host process)  --local IPC-->  terminal owner
                                                                            |
                                                                            v
                                                                   guest PTY over vsock

The browser never talks to the guest directly. The web server holds the
authenticated WebSocket and forwards terminal frames to the terminal owner over
a host-local channel; the owner binds one PTY inside the session VM. That keeps
the client-facing surface (a paired, same-origin browser) narrower than the
shell surface itself.

Wire protocol (browser <-> web server, and web server <-> owner)
---------------------------------------------------------------

Messages are JSON objects ``{"kind": <str>, "seq": <int>, "payload": {...}}``.
The browser->owner kinds are ``open``/``input``/``resize``/``ack``/``close``;
the owner->browser kinds are ``opened``/``output``/``exit``/``error``. The same
objects are carried as text frames of a WebSocket (browser) or as frames of the
frozen local IPC (owner), so one validator serves both halves.

Policy (structural, never heuristic)
------------------------------------

* ``open`` carries a one-time **ticket** (opaque, single-use) plus terminal
  dimensions; it never carries a command, argv or shell string - the owner
  launches the session's configured shell and nothing else.
* ``input`` carries **base64** keystroke bytes (a PTY consumes raw bytes, not
  text) with a hard decoded-size bound, so a payload can neither smuggle a host
  path nor blow up the owner's memory.
* ``resize`` carries bounded ``rows``/``cols`` integers.
* ``ack`` carries a monotonic sequence number of the last *rendered* output
  chunk: it is the backpressure credit the browser returns after xterm paints a
  chunk (plan terminal item 9). It is a control message, never counted as
  keystrokes.
* ``output`` carries base64 PTY bytes and its ``seq``; the browser acks the
  highest applied seq, so a reconnect can ask the owner to replay from the last
  acked seq without duplicating already-painted output.

Reconnect / replay (items 9 and 10)
-----------------------------------

The owner keeps a bounded ring of recent output chunks with their ``seq``
numbers. After a browser reload it opens a new WebSocket with the same ticket
family (a fresh single-use ticket issued by the same session) and reports the
last acked ``seq``; the owner replays only the chunks after that seq up to a
byte budget. This restores the visible screen without unbounded buffering and
without replaying keystrokes.

Boundaries
----------

* Nothing here is KVM-specific: the module imports no libvirt and no guest code,
  so both the host side and the guest side can share it, and unit tests run on
  any host over in-process pairs.
"""

from __future__ import annotations

import base64
import binascii
import json
import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Final, NewType

TERMINAL_PROTOCOL_VERSION: Final[int] = 1

#: Opaque, single-use ticket id; 32 lowercase hex chars (128 bits).
TICKET_PATTERN: Final = re.compile(r"^[0-9a-f]{32}$")
#: Hard bound on one decoded keystroke batch handed to the PTY.
MAX_INPUT_BYTES: Final[int] = 16 * 1024
#: Hard bound on one decoded output chunk sent to the browser.
MAX_OUTPUT_BYTES: Final[int] = 64 * 1024
#: Largest replay the owner will serve on one reconnect (bounded memory).
MAX_REPLAY_BYTES: Final[int] = 256 * 1024
#: PTY dimensions: 1..1000 is far beyond any real terminal and still bounded.
MIN_DIMENSION: Final[int] = 1
MAX_DIMENSION: Final[int] = 1000
#: The ring the owner keeps for replay; counts chunks, not bytes.
DEFAULT_REPLAY_CHUNKS: Final[int] = 512

TicketId = NewType("TicketId", str)


class TerminalProtocolError(Exception):
    """Typed terminal protocol failure; ``reason`` is a stable machine code."""

    def __init__(self, reason: str, detail: str = "") -> None:
        super().__init__(reason)
        self.reason = reason
        self.detail = detail

    def __str__(self) -> str:
        return f"{self.reason}: {self.detail}" if self.detail else self.reason


class TerminalKind(StrEnum):
    """Every terminal message kind (browser<->web, web<->owner)."""

    # browser -> owner
    OPEN = "open"
    INPUT = "input"
    RESIZE = "resize"
    ACK = "ack"
    CLOSE = "close"
    # owner -> browser
    OPENED = "opened"
    OUTPUT = "output"
    EXIT = "exit"
    ERROR = "error"


class TerminalStage(StrEnum):
    """Which leg of the terminal path a payload is validated for.

    ``BROWSER`` is the untrusted edge: ``open`` must present a ticket, and every
    other field is bounded (that is what the local page sends). ``INTERNAL`` is
    the host<->owner leg (the guest terminal service over a separate vsock port):
    the peer is authenticated by its kernel CID and has already been reached
    through a single-use ticket, so ``open`` carries only dimensions and the
    session id stays a host-side concept. Keeping two stages means the edge
    schema can never be widened to accommodate the internal leg (and vice versa).
    """

    BROWSER = "browser"
    INTERNAL = "internal"


BROWSER_KINDS: Final[frozenset[TerminalKind]] = frozenset(
    {TerminalKind.OPEN, TerminalKind.INPUT, TerminalKind.RESIZE, TerminalKind.ACK, TerminalKind.CLOSE}
)
OWNER_KINDS: Final[frozenset[TerminalKind]] = frozenset(
    {TerminalKind.OPENED, TerminalKind.OUTPUT, TerminalKind.EXIT, TerminalKind.ERROR}
)


@dataclass(frozen=True, slots=True)
class TerminalMessage:
    """One validated terminal message."""

    kind: TerminalKind
    seq: int
    payload: dict[str, object]


def encode_message(kind: TerminalKind, seq: int, payload: dict[str, object]) -> str:
    """Serialize one terminal message to the text form used by both transports."""
    if not isinstance(seq, int) or isinstance(seq, bool) or seq < 0:
        raise TerminalProtocolError("sequence_invalid", repr(seq))
    return json.dumps(
        {"kind": str(kind), "seq": seq, "payload": payload},
        separators=(",", ":"),
        ensure_ascii=False,
    )


def decode_message(
    raw: str | bytes | dict[str, object],
    *,
    stage: TerminalStage = TerminalStage.BROWSER,
) -> TerminalMessage:
    """Parse and structurally validate one terminal message.

    ``stage`` selects the payload schema for the leg the message arrived on
    (see :class:`TerminalStage`). Raises :class:`TerminalProtocolError` for
    anything malformed; the caller answers with an ``error`` frame.

    ``raw`` may also be an already-decoded mapping: the guest-facing internal leg
    receives frames from the shared framed-JSON reader
    (:func:`factory.core.isolation.channel.read_frame`), and re-encoding them just
    to re-parse the same JSON would be wasted work. The mapping path runs exactly
    the same validation as the text path - sharing a decoder must never be a way
    to skip the policy.
    """
    if isinstance(raw, dict):
        obj = raw
    else:
        if isinstance(raw, (bytes, bytearray)):
            try:
                text = raw.decode("utf-8")
            except UnicodeDecodeError as exc:
                raise TerminalProtocolError("malformed_message", "not utf-8") from exc
        else:
            text = raw
        try:
            obj = json.loads(text)
        except (json.JSONDecodeError, TypeError) as exc:
            raise TerminalProtocolError("malformed_message", "invalid json") from exc
    if not isinstance(obj, dict):
        raise TerminalProtocolError("malformed_message", "not an object")
    kind_raw = obj.get("kind")
    if not isinstance(kind_raw, str):
        raise TerminalProtocolError("missing_kind")
    try:
        kind = TerminalKind(kind_raw)
    except ValueError as exc:
        raise TerminalProtocolError("unknown_kind", kind_raw) from exc
    seq = obj.get("seq")
    if not isinstance(seq, int) or isinstance(seq, bool) or seq < 0:
        raise TerminalProtocolError("sequence_invalid", repr(seq))
    payload = obj.get("payload")
    if not isinstance(payload, dict):
        raise TerminalProtocolError("payload_invalid", "not an object")
    validate_payload(kind, payload, stage=stage)
    return TerminalMessage(kind=kind, seq=seq, payload=payload)


def decode_bounded(data: bytes) -> bytes:
    """Base64-decode *data* with a hard size bound (input direction)."""
    return _b64_decode(data, MAX_INPUT_BYTES, "input_too_large")


def decode_output(data: bytes) -> bytes:
    """Base64-decode *data* received from the owner (output direction)."""
    return _b64_decode(data, MAX_OUTPUT_BYTES, "output_too_large")


def _b64_decode(data: bytes, limit: int, reason: str) -> bytes:
    if not isinstance(data, (bytes, bytearray)):
        raise TerminalProtocolError("payload_field_type", type(data).__name__)
    # Reject oversize before decoding: 4 base64 chars carry 3 bytes.
    if len(data) > ((limit + 2) // 3) * 4:
        raise TerminalProtocolError(reason, f">{limit} bytes")
    try:
        decoded = base64.b64decode(bytes(data), validate=True)
    except (binascii.Error, ValueError) as exc:
        raise TerminalProtocolError("payload_field_invalid", "invalid base64") from exc
    if len(decoded) > limit:
        raise TerminalProtocolError(reason, f">{limit} bytes")
    return decoded


def _require_int(payload: dict[str, object], key: str, *, minimum: int, maximum: int) -> int:
    value = payload.get(key)
    if not isinstance(value, int) or isinstance(value, bool):
        raise TerminalProtocolError("payload_field_type", f"{key} is not an int")
    if not minimum <= value <= maximum:
        raise TerminalProtocolError("payload_field_range", f"{key}={value}")
    return value


def validate_payload(
    kind: TerminalKind,
    payload: dict[str, object],
    *,
    stage: TerminalStage = TerminalStage.BROWSER,
) -> None:
    """Structural payload policy for one terminal message kind and leg.

    Every branch pins an **exact** key set: an unexpected key is a violation, so
    no future field can quietly become an attack surface. Fields hold either
    bounded integers or base64 blobs - never a free-form string that could
    smuggle a command or a host path.

    ``TerminalStage.INTERNAL`` (host<->guest owner leg) keeps the same field
    shapes but drops the ticket from ``open`` and the session id from ``opened``:
    the peer there is authenticated by its kernel vsock CID and already reached
    through a single-use ticket at the browser edge.
    """
    if stage is TerminalStage.INTERNAL:
        _validate_internal(kind, payload)
        return
    match kind:
        case TerminalKind.OPEN:
            _validate_open(payload)
        case TerminalKind.INPUT:
            _validate_input(payload)
        case TerminalKind.RESIZE:
            _validate_resize(payload)
        case TerminalKind.ACK:
            _validate_ack(payload)
        case TerminalKind.CLOSE:
            if payload:
                raise TerminalProtocolError("payload_fields_invalid", "close takes no fields")
        case TerminalKind.OPENED:
            _validate_opened(payload)
        case TerminalKind.OUTPUT:
            _validate_output(payload)
        case TerminalKind.EXIT:
            _validate_exit(payload)
        case TerminalKind.ERROR:
            _validate_error(payload)


def _validate_internal(kind: TerminalKind, payload: dict[str, object]) -> None:
    """The host<->owner leg schema: no ticket, no session id, same shapes."""
    match kind:
        case TerminalKind.OPEN:
            allowed = {"rows", "cols", "since_seq"}
            if set(payload) - allowed:
                raise TerminalProtocolError(
                    "payload_fields_invalid", f"unexpected {sorted(set(payload) - allowed)}"
                )
            for key in ("rows", "cols"):
                if key in payload:
                    _require_int(payload, key, minimum=MIN_DIMENSION, maximum=MAX_DIMENSION)
            if "since_seq" in payload:
                value = payload["since_seq"]
                if not isinstance(value, int) or isinstance(value, bool) or value < 0:
                    raise TerminalProtocolError("payload_field_type", "since_seq")
        case TerminalKind.INPUT:
            _validate_input(payload)
        case TerminalKind.RESIZE:
            _validate_resize(payload)
        case TerminalKind.ACK:
            _validate_ack(payload)
        case TerminalKind.CLOSE:
            if payload:
                raise TerminalProtocolError("payload_fields_invalid", "close takes no fields")
        case TerminalKind.OPENED:
            allowed = {"terminal_id", "rows", "cols"}
            if set(payload) != allowed:
                raise TerminalProtocolError(
                    "payload_fields_invalid", f"opened requires exactly {sorted(allowed)}"
                )
            terminal_id = payload.get("terminal_id")
            if not isinstance(terminal_id, str) or not terminal_id or len(terminal_id) > 128:
                raise TerminalProtocolError("payload_field_invalid", "terminal_id")
            _require_int(payload, "rows", minimum=MIN_DIMENSION, maximum=MAX_DIMENSION)
            _require_int(payload, "cols", minimum=MIN_DIMENSION, maximum=MAX_DIMENSION)
        case TerminalKind.OUTPUT:
            _validate_output(payload)
        case TerminalKind.EXIT:
            _validate_exit(payload)
        case TerminalKind.ERROR:
            _validate_error(payload)


def _validate_open(payload: dict[str, object]) -> None:
    allowed = {"ticket", "rows", "cols", "since_seq", "shell"}
    if set(payload) - allowed:
        raise TerminalProtocolError("payload_fields_invalid", f"unexpected {sorted(set(payload) - allowed)}")
    ticket = payload.get("ticket")
    if not isinstance(ticket, str) or not TICKET_PATTERN.match(ticket):
        raise TerminalProtocolError("ticket_invalid", "ticket must be 32 lowercase hex chars")
    for key in ("rows", "cols"):
        if key in payload:
            _require_int(payload, key, minimum=MIN_DIMENSION, maximum=MAX_DIMENSION)
    if "since_seq" in payload:
        value = payload["since_seq"]
        if not isinstance(value, int) or isinstance(value, bool) or value < 0:
            raise TerminalProtocolError("payload_field_type", "since_seq")
    if "shell" in payload:
        shell = payload["shell"]
        if not isinstance(shell, int) or isinstance(shell, bool) or shell not in (0, 1):
            raise TerminalProtocolError("payload_field_type", "shell must be 0 or 1")


def _validate_input(payload: dict[str, object]) -> None:
    if set(payload) != {"data"}:
        raise TerminalProtocolError("payload_fields_invalid", "input requires exactly {data}")
    data = payload.get("data")
    if not isinstance(data, str) or not data.isascii():
        raise TerminalProtocolError("payload_field_type", "data must be ascii base64")
    _b64_decode(data.encode("ascii"), MAX_INPUT_BYTES, "input_too_large")


def _validate_resize(payload: dict[str, object]) -> None:
    if set(payload) != {"rows", "cols"}:
        raise TerminalProtocolError("payload_fields_invalid", "resize requires exactly {rows,cols}")
    _require_int(payload, "rows", minimum=MIN_DIMENSION, maximum=MAX_DIMENSION)
    _require_int(payload, "cols", minimum=MIN_DIMENSION, maximum=MAX_DIMENSION)


def _validate_ack(payload: dict[str, object]) -> None:
    if set(payload) != {"seq"}:
        raise TerminalProtocolError("payload_fields_invalid", "ack requires exactly {seq}")
    value = payload.get("seq")
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise TerminalProtocolError("payload_field_type", "ack seq")


def _validate_opened(payload: dict[str, object]) -> None:
    allowed = {"terminal_id", "session_id", "rows", "cols"}
    if set(payload) != allowed:
        raise TerminalProtocolError("payload_fields_invalid", f"opened requires exactly {sorted(allowed)}")
    for key in ("terminal_id", "session_id"):
        value = payload.get(key)
        if not isinstance(value, str) or not value or len(value) > 128:
            raise TerminalProtocolError("payload_field_invalid", key)
    _require_int(payload, "rows", minimum=MIN_DIMENSION, maximum=MAX_DIMENSION)
    _require_int(payload, "cols", minimum=MIN_DIMENSION, maximum=MAX_DIMENSION)


def _validate_output(payload: dict[str, object]) -> None:
    if set(payload) != {"data"}:
        raise TerminalProtocolError("payload_fields_invalid", "output requires exactly {data}")
    data = payload.get("data")
    if not isinstance(data, str) or not data.isascii():
        raise TerminalProtocolError("payload_field_type", "output data")
    decode_output(data.encode("ascii"))


def _validate_exit(payload: dict[str, object]) -> None:
    if set(payload) != {"code"}:
        raise TerminalProtocolError("payload_fields_invalid", "exit requires exactly {code}")
    value = payload.get("code")
    if not isinstance(value, int) or isinstance(value, bool) or not -1 <= value <= 255:
        raise TerminalProtocolError("payload_field_range", "exit code")


def _validate_error(payload: dict[str, object]) -> None:
    if set(payload) != {"reason"}:
        raise TerminalProtocolError("payload_fields_invalid", "error requires exactly {reason}")
    value = payload.get("reason")
    if not isinstance(value, str) or not value or len(value) > 128:
        raise TerminalProtocolError("payload_field_invalid", "error reason")
