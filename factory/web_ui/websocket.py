"""Minimal RFC 6455 server-side WebSocket for the local web interface.

The interface already runs on :class:`http.server.ThreadingHTTPServer`; pulling a
full WebSocket framework in (``aiohttp``/``starlette``/``wsproto``) would add an
async runtime and a new dependency for one endpoint. This module implements only
the server half of RFC 6455 that the terminal needs - the opening handshake, the
frame codec, masking enforcement, fragmentation, ping/pong, close - in stdlib
code that is fully unit-testable over an in-process ``socketpair``.

Deliberate strictness (fail closed):

* the handshake requires ``Upgrade: websocket``, ``Connection: Upgrade``,
  ``Sec-WebSocket-Version: 13`` and a base64 16-byte ``Sec-WebSocket-Key``;
* **client frames must be masked** (§5.1) - an unmasked frame closes the
  connection with 1002 instead of being guessed at;
* control frames must be unfragmented and carry at most 125 bytes (§5.5);
* one data message is bounded by :data:`DEFAULT_MAX_MESSAGE_BYTES`, and the
  declared payload length is checked *before* any allocation, so a 64-bit
  length cannot be used to exhaust memory (§5.2, §7.4.1 "message too big");
* continuation frames must be part of a started message; an unexpected
  continuation or a new data frame during fragmentation is a protocol error;
* reserved bits (RSV1-3) are rejected because no extension is negotiated.

Text frames are decoded as UTF-8 strictly; a binary frame is accepted for the
``output`` channel only where the caller expects it. Nothing here knows about
terminals, sessions or the isolation layer.
"""

from __future__ import annotations

import base64
import hashlib
import logging
import os
import socket
import struct
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final, Protocol

logger = logging.getLogger(__name__)

WEBSOCKET_GUID: Final[str] = "258EAFA5-E914-47DA-95CA-C5AB0DC85B11"
SUPPORTED_VERSION: Final[str] = "13"
DEFAULT_MAX_MESSAGE_BYTES: Final[int] = 1 * 1024 * 1024
MAX_CONTROL_PAYLOAD: Final[int] = 125

OP_CONTINUATION: Final[int] = 0x0
OP_TEXT: Final[int] = 0x1
OP_BINARY: Final[int] = 0x2
OP_CLOSE: Final[int] = 0x8
OP_PING: Final[int] = 0x9
OP_PONG: Final[int] = 0xA

CLOSE_NORMAL: Final[int] = 1000
CLOSE_PROTOCOL_ERROR: Final[int] = 1002
CLOSE_UNSUPPORTED_DATA: Final[int] = 1003
CLOSE_INVALID_PAYLOAD: Final[int] = 1007
CLOSE_TOO_BIG: Final[int] = 1009
CLOSE_INTERNAL_ERROR: Final[int] = 1011


class WebSocketError(Exception):
    """Typed WebSocket failure; ``close_code`` is the RFC status to send back."""

    def __init__(self, reason: str, detail: str = "", *, close_code: int = CLOSE_PROTOCOL_ERROR) -> None:
        super().__init__(reason)
        self.reason = reason
        self.detail = detail
        self.close_code = close_code

    def __str__(self) -> str:
        return f"{self.reason}: {self.detail}" if self.detail else self.reason


class SocketLike(Protocol):
    """Minimal socket surface the codec needs (tests use ``socketpair``)."""

    def recv(self, size: int) -> bytes: ...
    def sendall(self, data: bytes) -> None: ...
    def settimeout(self, value: float | None) -> None: ...
    def shutdown(self, how: int) -> None: ...
    def close(self) -> None: ...


@dataclass(frozen=True, slots=True)
class HandshakeRequest:
    """The headers a client must present to open a WebSocket."""

    upgrade: str
    connection: str
    version: str
    key: str

    @classmethod
    def from_headers(cls, headers: Mapping[str, str]) -> HandshakeRequest:
        lower = {str(k).lower(): str(v) for k, v in headers.items()}
        return cls(
            upgrade=lower.get("upgrade", ""),
            connection=lower.get("connection", ""),
            version=lower.get("sec-websocket-version", ""),
            key=lower.get("sec-websocket-key", ""),
        )


def compute_accept(key: str) -> str:
    """RFC 6455 §4.2.2 accept value for a client key."""
    digest = hashlib.sha1((key + WEBSOCKET_GUID).encode("ascii")).digest()
    return base64.b64encode(digest).decode("ascii")


def validate_handshake(request: HandshakeRequest) -> str:
    """Return the ``Sec-WebSocket-Accept`` value, or raise :class:`WebSocketError`."""
    if request.upgrade.strip().lower() != "websocket":
        raise WebSocketError("upgrade_missing", "Upgrade: websocket required")
    tokens = {token.strip().lower() for token in request.connection.split(",")}
    if "upgrade" not in tokens:
        raise WebSocketError("connection_upgrade_missing", "Connection: Upgrade required")
    if request.version.strip() != SUPPORTED_VERSION:
        raise WebSocketError("version_unsupported", request.version or "<missing>")
    try:
        raw = base64.b64decode(request.key, validate=True)
    except Exception as exc:
        raise WebSocketError("key_invalid", "not base64") from exc
    if len(raw) != 16:
        raise WebSocketError("key_invalid", f"{len(raw)} bytes")
    return compute_accept(request.key)


def handshake_headers(accept: str) -> list[tuple[str, str]]:
    """Response headers that complete the upgrade (order preserved)."""
    return [
        ("Upgrade", "websocket"),
        ("Connection", "Upgrade"),
        ("Sec-WebSocket-Accept", accept),
    ]


def _recv_exact(sock: SocketLike, size: int) -> bytes | None:
    """Read exactly ``size`` bytes; ``None`` on clean EOF before the first byte."""
    chunks: list[bytes] = []
    remaining = size
    while remaining:
        try:
            chunk = sock.recv(remaining)
        except (TimeoutError, socket.timeout):
            if remaining == size:
                raise
            raise WebSocketError("truncated_frame", f"{size - remaining} of {size} bytes")
        except OSError as exc:
            raise WebSocketError("peer_disconnected", str(exc)) from exc
        if not chunk:
            if remaining == size:
                return None
            raise WebSocketError("truncated_frame", f"{size - remaining} of {size} bytes")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def encode_frame(opcode: int, payload: bytes, *, fin: bool = True) -> bytes:
    """Encode one server->client frame (never masked, §5.1)."""
    if opcode >= OP_CLOSE and (len(payload) > MAX_CONTROL_PAYLOAD or not fin):
        raise WebSocketError("control_frame_invalid", "control frames must be final and <=125 bytes")
    header = bytearray()
    header.append((0x80 if fin else 0x00) | (opcode & 0x0F))
    length = len(payload)
    if length < 126:
        header.append(length)
    elif length <= 0xFFFF:
        header.append(126)
        header += struct.pack(">H", length)
    else:
        header.append(127)
        header += struct.pack(">Q", length)
    return bytes(header) + payload


@dataclass(frozen=True, slots=True)
class Frame:
    """One decoded inbound frame."""

    fin: bool
    opcode: int
    payload: bytes


def decode_frame_header(header: bytes) -> tuple[bool, int, bool, int]:
    """Parse a 2-byte frame header into (fin, opcode, masked, payload_length_code)."""
    if len(header) != 2:
        raise WebSocketError("frame_header_invalid", f"{len(header)} bytes")
    first, second = header[0], header[1]
    if first & 0x70:
        raise WebSocketError("reserved_bits_set", "no extension is negotiated")
    fin = bool(first & 0x80)
    opcode = first & 0x0F
    masked = bool(second & 0x80)
    length = second & 0x7F
    return fin, opcode, masked, length


class WebSocket:
    """One server-side WebSocket connection: read/write frames, enforce policy."""

    def __init__(
        self,
        sock: SocketLike,
        *,
        max_message_bytes: int = DEFAULT_MAX_MESSAGE_BYTES,
        read_timeout_s: float | None = None,
    ) -> None:
        self._sock = sock
        self._max_message_bytes = max(1024, int(max_message_bytes))
        self._read_timeout_s = read_timeout_s
        self._closed = False

    @property
    def closed(self) -> bool:
        return self._closed

    def recv(self, timeout_s: float | None = None) -> tuple[str, bytes] | None:
        """Read one *message* (fragmenting as needed); ``None`` on clean EOF.

        Returns ``("text"|"binary"|"close"|"ping"|"pong"|"timeout", payload)``.
        A ``timeout`` entry means the read window elapsed with no data at all:
        normal for a long-lived connection and never a protocol violation.
        """
        try:
            return self._recv_message(timeout_s)
        except _ReadTimeout:
            return ("timeout", b"")

    def _recv_message(self, timeout_s: float | None) -> tuple[str, bytes] | None:
        effective = self._read_timeout_s if timeout_s is None else timeout_s
        self._set_timeout(effective)
        while True:
            header = self._read_header()
            if header is None:
                return None
            fin, opcode, masked, length_code = header
            payload = self._read_payload(opcode, fin, masked, length_code)
            if payload is None:
                return None
            if opcode == OP_CLOSE:
                return ("close", payload)
            if opcode == OP_PING:
                return ("ping", payload)
            if opcode == OP_PONG:
                return ("pong", payload)
            if opcode == OP_CONTINUATION:
                raise WebSocketError("unexpected_continuation", "no message in progress")
            if opcode not in (OP_TEXT, OP_BINARY):
                raise WebSocketError("opcode_unknown", str(opcode))
            if fin:
                return ("text" if opcode == OP_TEXT else "binary", payload)
            return self._read_fragments(opcode, payload)

    def _set_timeout(self, value: float | None) -> None:
        try:
            self._sock.settimeout(value)
        except OSError as exc:  # pragma: no cover - socket closed under us
            raise WebSocketError("peer_disconnected", str(exc)) from exc

    def _read_header(self) -> tuple[bool, int, bool, int] | None:
        try:
            header = _recv_exact(self._sock, 2)
        except (TimeoutError, socket.timeout) as exc:
            raise _ReadTimeout from exc
        if header is None:
            return None
        return decode_frame_header(header)

    def _read_payload(
        self, opcode: int, fin: bool, masked: bool, length_code: int
    ) -> bytes | None:
        if not masked:
            # §5.1: every client frame is masked. Accepting unmasked input would
            # let a local process forge frames as if they came from the page.
            raise WebSocketError("client_frame_unmasked", "client frames must be masked")
        if opcode >= OP_CLOSE:
            if not fin:
                raise WebSocketError("control_frame_fragmented", str(opcode))
            if length_code > MAX_CONTROL_PAYLOAD:
                raise WebSocketError("control_frame_too_large", str(length_code))
        length = self._resolve_length(length_code)
        if opcode < OP_CLOSE and length > self._max_message_bytes:
            raise WebSocketError(
                "message_too_big", f"{length} > {self._max_message_bytes}", close_code=CLOSE_TOO_BIG
            )
        mask = _recv_exact(self._sock, 4)
        if mask is None:
            return None
        body = _recv_exact(self._sock, length) if length else b""
        if body is None:
            return None
        return _apply_mask(body, mask)

    def _resolve_length(self, length_code: int) -> int:
        if length_code < 126:
            return length_code
        if length_code == 126:
            raw = _recv_exact(self._sock, 2)
            if raw is None:
                raise WebSocketError("truncated_frame", "extended length missing")
            return struct.unpack(">H", raw)[0]
        raw = _recv_exact(self._sock, 8)
        if raw is None:
            raise WebSocketError("truncated_frame", "extended length missing")
        (value,) = struct.unpack(">Q", raw)
        if value & (1 << 63):
            # §5.2: the most significant bit must be 0.
            raise WebSocketError("length_invalid", "msb set")
        return value

    def _read_fragments(self, opcode: int, first: bytes) -> tuple[str, bytes]:
        """Assemble a fragmented message, bounded by the message limit."""
        total = bytearray(first)
        while True:
            header = self._read_header()
            if header is None:
                raise WebSocketError("truncated_frame", "stream ended mid-message")
            fin, next_opcode, masked, length_code = header
            payload = self._read_payload(next_opcode, fin, masked, length_code)
            if payload is None:
                raise WebSocketError("truncated_frame", "stream ended mid-message")
            if next_opcode == OP_CLOSE:
                raise WebSocketError("interleaved_close", "close during fragmentation")
            if next_opcode in (OP_PING, OP_PONG):
                # Control frames may interrupt; the caller sees the message only
                # once it is complete, so the ping is answered here.
                if next_opcode == OP_PING:
                    self.send_pong(payload)
                continue
            if next_opcode != OP_CONTINUATION:
                raise WebSocketError("fragment_out_of_order", str(next_opcode))
            total += payload
            if len(total) > self._max_message_bytes:
                raise WebSocketError(
                    "message_too_big",
                    f">{self._max_message_bytes}",
                    close_code=CLOSE_TOO_BIG,
                )
            if fin:
                return ("text" if opcode == OP_TEXT else "binary", bytes(total))

    def send_text(self, text: str) -> None:
        self.send_frame(encode_frame(OP_TEXT, text.encode("utf-8")))

    def send_binary(self, data: bytes) -> None:
        self.send_frame(encode_frame(OP_BINARY, data))

    def send_pong(self, payload: bytes = b"") -> None:
        self.send_frame(encode_frame(OP_PONG, payload[:MAX_CONTROL_PAYLOAD]))

    def send_ping(self, payload: bytes = b"") -> None:
        self.send_frame(encode_frame(OP_PING, payload[:MAX_CONTROL_PAYLOAD]))

    def send_close(self, code: int = CLOSE_NORMAL, reason: str = "") -> None:
        """Send a close frame; never raises (the peer may already be gone)."""
        body = struct.pack(">H", max(0, min(65535, int(code)))) + reason.encode("utf-8")[:123]
        try:
            self.send_frame(encode_frame(OP_CLOSE, body))
        except (WebSocketError, OSError):
            pass
        self._closed = True

    def send_frame(self, frame: bytes) -> None:
        if self._closed:
            raise WebSocketError("connection_closed", "closed locally")
        try:
            self._sock.sendall(frame)
        except OSError as exc:
            raise WebSocketError("peer_unreachable", str(exc)) from exc

    def close(self) -> None:
        self._closed = True
        try:
            self._sock.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass
        try:
            self._sock.close()
        except OSError:
            pass


class _ReadTimeout(Exception):
    """Internal signal for an empty read window (never escapes to callers)."""


def _apply_mask(payload: bytes, mask: bytes) -> bytes:
    return bytes(byte ^ mask[index & 3] for index, byte in enumerate(payload))


def is_timeout(exc: BaseException) -> bool:
    """True when an exception is the internal empty-read-window signal."""
    return isinstance(exc, _ReadTimeout)


def encode_client_frame(opcode: int, payload: bytes, *, fin: bool = True, mask: bytes | None = None) -> bytes:
    """Encode one client->server frame (masked, §5.1) - used by tests."""
    key = mask if mask is not None else os.urandom(4)
    if len(key) != 4:
        raise WebSocketError("mask_invalid", f"{len(key)} bytes")
    header = bytearray()
    header.append((0x80 if fin else 0x00) | (opcode & 0x0F))
    length = len(payload)
    if length < 126:
        header.append(0x80 | length)
    elif length <= 0xFFFF:
        header.append(0x80 | 126)
        header += struct.pack(">H", length)
    else:
        header.append(0x80 | 127)
        header += struct.pack(">Q", length)
    header += key
    return bytes(header) + _apply_mask(payload, key)
