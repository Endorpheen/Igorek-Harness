#!/usr/bin/env python3
"""Unit tests for the stdlib RFC 6455 server half used by the terminal route.

The transport is an in-process ``socketpair``, so every byte the client would
put on the wire is crafted explicitly. The adversarial cases are the ones that
matter for a local interface: an unmasked client frame, an oversize declared
length (must be refused *before* allocation), a fragmented control frame,
reserved bits, a continuation without a start, and a message that grows past the
limit mid-fragmentation.
"""

from __future__ import annotations

import base64
import os
import socket
import struct
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(os.environ.get("IGOREK_FACTORY_REPO", Path(__file__).resolve().parents[1]))
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from factory.web_ui.websocket import (  # noqa: E402
    CLOSE_NORMAL,
    OP_BINARY,
    OP_CLOSE,
    OP_CONTINUATION,
    OP_PING,
    OP_PONG,
    OP_TEXT,
    HandshakeRequest,
    WebSocket,
    WebSocketError,
    compute_accept,
    decode_frame_header,
    encode_client_frame,
    encode_frame,
    handshake_headers,
    validate_handshake,
)

CLIENT_KEY = base64.b64encode(b"0123456789abcdef").decode("ascii")


def read_server_frames(raw: bytes) -> list[tuple[bool, int, bytes]]:
    """Decode a server->client byte stream into (fin, opcode, payload) triples."""
    frames: list[tuple[bool, int, bytes]] = []
    index = 0
    while index < len(raw):
        first, second = raw[index], raw[index + 1]
        index += 2
        fin = bool(first & 0x80)
        opcode = first & 0x0F
        length = second & 0x7F
        if length == 126:
            (length,) = struct.unpack(">H", raw[index : index + 2])
            index += 2
        elif length == 127:
            (length,) = struct.unpack(">Q", raw[index : index + 8])
            index += 8
        payload = raw[index : index + length]
        index += length
        frames.append((fin, opcode, payload))
    return frames


class HandshakeTest(unittest.TestCase):
    """The opening handshake requires exactly the RFC 6455 headers."""

    def _headers(self, **overrides: str) -> dict[str, str]:
        base = {
            "Upgrade": "websocket",
            "Connection": "Upgrade",
            "Sec-WebSocket-Version": "13",
            "Sec-WebSocket-Key": CLIENT_KEY,
        }
        base.update(overrides)
        return base

    def test_accept_value_matches_rfc_example(self) -> None:
        # RFC 6455 §1.3 worked example.
        self.assertEqual(compute_accept("dGhlIHNhbXBsZSBub25jZQ=="), "s3pPLMBiTxaQ9kYGzzhZRbK+xOo=")

    def test_valid_handshake(self) -> None:
        accept = validate_handshake(HandshakeRequest.from_headers(self._headers()))
        self.assertEqual(accept, compute_accept(CLIENT_KEY))
        headers = dict(handshake_headers(accept))
        self.assertEqual(headers["Upgrade"], "websocket")
        self.assertEqual(headers["Connection"], "Upgrade")
        self.assertEqual(headers["Sec-WebSocket-Accept"], accept)

    def test_header_lookup_is_case_insensitive(self) -> None:
        accept = validate_handshake(
            HandshakeRequest.from_headers(
                {
                    "upgrade": "WebSocket",
                    "connection": "keep-alive, Upgrade",
                    "sec-websocket-version": "13",
                    "sec-websocket-key": CLIENT_KEY,
                }
            )
        )
        self.assertTrue(accept)

    def test_missing_upgrade(self) -> None:
        with self.assertRaises(WebSocketError) as ctx:
            validate_handshake(HandshakeRequest.from_headers(self._headers(Upgrade="h2c")))
        self.assertEqual(ctx.exception.reason, "upgrade_missing")

    def test_missing_connection_upgrade_token(self) -> None:
        with self.assertRaises(WebSocketError) as ctx:
            validate_handshake(HandshakeRequest.from_headers(self._headers(Connection="keep-alive")))
        self.assertEqual(ctx.exception.reason, "connection_upgrade_missing")

    def test_unsupported_version(self) -> None:
        with self.assertRaises(WebSocketError) as ctx:
            validate_handshake(HandshakeRequest.from_headers(self._headers(**{"Sec-WebSocket-Version": "8"})))
        self.assertEqual(ctx.exception.reason, "version_unsupported")

    def test_bad_key(self) -> None:
        for key in ("not base64!", base64.b64encode(b"short").decode(), ""):
            with self.subTest(key=key):
                with self.assertRaises(WebSocketError) as ctx:
                    validate_handshake(HandshakeRequest.from_headers(self._headers(**{"Sec-WebSocket-Key": key})))
                self.assertEqual(ctx.exception.reason, "key_invalid")


class FrameCodecTest(unittest.TestCase):
    """Encoding and header parsing, including the length encodings."""

    def test_short_payload_encoding(self) -> None:
        frame = encode_frame(OP_TEXT, b"hi")
        self.assertEqual(frame, b"\x81\x02hi")

    def test_medium_payload_uses_16bit_length(self) -> None:
        payload = b"a" * 300
        frame = encode_frame(OP_BINARY, payload)
        self.assertEqual(frame[0], 0x82)
        self.assertEqual(frame[1], 126)
        self.assertEqual(struct.unpack(">H", frame[2:4])[0], 300)
        self.assertEqual(frame[4:], payload)

    def test_large_payload_uses_64bit_length(self) -> None:
        payload = b"a" * 70000
        frame = encode_frame(OP_BINARY, payload)
        self.assertEqual(frame[1], 127)
        self.assertEqual(struct.unpack(">Q", frame[2:10])[0], 70000)

    def test_server_frames_are_unmasked(self) -> None:
        frame = encode_frame(OP_TEXT, b"x")
        self.assertEqual(frame[1] & 0x80, 0, "server frames must not be masked")

    def test_control_frame_must_be_final_and_small(self) -> None:
        with self.assertRaises(WebSocketError) as ctx:
            encode_frame(OP_PING, b"x", fin=False)
        self.assertEqual(ctx.exception.reason, "control_frame_invalid")
        with self.assertRaises(WebSocketError) as ctx:
            encode_frame(OP_CLOSE, b"x" * 200)
        self.assertEqual(ctx.exception.reason, "control_frame_invalid")

    def test_decode_header_rejects_reserved_bits(self) -> None:
        with self.assertRaises(WebSocketError) as ctx:
            decode_frame_header(bytes([0x81 | 0x40, 0x80]))
        self.assertEqual(ctx.exception.reason, "reserved_bits_set")

    def test_decode_header_fields(self) -> None:
        fin, opcode, masked, length = decode_frame_header(bytes([0x89, 0x80 | 5]))
        self.assertTrue(fin)
        self.assertEqual(opcode, OP_PING)
        self.assertTrue(masked)
        self.assertEqual(length, 5)

    def test_client_frame_helper_masks(self) -> None:
        frame = encode_client_frame(OP_TEXT, b"hello", mask=b"\x01\x02\x03\x04")
        self.assertEqual(frame[1] & 0x80, 0x80)
        self.assertEqual(frame[2:6], b"\x01\x02\x03\x04")
        masked = frame[6:]
        unmasked = bytes(byte ^ b"\x01\x02\x03\x04"[i & 3] for i, byte in enumerate(masked))
        self.assertEqual(unmasked, b"hello")


class ConnectionTest(unittest.TestCase):
    """End-to-end frame exchange over a socketpair, plus every refusal."""

    def setUp(self) -> None:
        self.server_sock, self.client_sock = socket.socketpair()
        self.ws = WebSocket(self.server_sock, max_message_bytes=4096, read_timeout_s=0.2)

    def tearDown(self) -> None:
        for sock in (self.server_sock, self.client_sock):
            try:
                sock.close()
            except OSError:
                pass

    def _client_raw(self, data: bytes) -> None:
        self.client_sock.sendall(data)

    def _read_client(self, size: int = 65536) -> bytes:
        self.client_sock.settimeout(0.5)
        return self.client_sock.recv(size)

    def test_text_message(self) -> None:
        self._client_raw(encode_client_frame(OP_TEXT, b"hello"))
        self.assertEqual(self.ws.recv(), ("text", b"hello"))

    def test_binary_message(self) -> None:
        self._client_raw(encode_client_frame(OP_BINARY, b"\x00\xff"))
        self.assertEqual(self.ws.recv(), ("binary", b"\x00\xff"))

    def test_close_message(self) -> None:
        self._client_raw(encode_client_frame(OP_CLOSE, struct.pack(">H", CLOSE_NORMAL)))
        kind, payload = self.ws.recv()
        self.assertEqual(kind, "close")
        self.assertEqual(struct.unpack(">H", payload)[0], CLOSE_NORMAL)

    def test_ping_is_surfaced(self) -> None:
        self._client_raw(encode_client_frame(OP_PING, b"ping"))
        self.assertEqual(self.ws.recv(), ("ping", b"ping"))

    def test_clean_eof_returns_none(self) -> None:
        self.client_sock.close()
        self.assertIsNone(self.ws.recv())

    def test_timeout_window_is_not_an_error(self) -> None:
        self.assertEqual(self.ws.recv(), ("timeout", b""))

    def test_unmasked_client_frame_is_refused(self) -> None:
        # Hand-crafted unmasked text frame: exactly what a local impostor would
        # send if the masking rule were not enforced.
        self._client_raw(b"\x81\x02hi")
        with self.assertRaises(WebSocketError) as ctx:
            self.ws.recv()
        self.assertEqual(ctx.exception.reason, "client_frame_unmasked")

    def test_fragmented_message_is_assembled(self) -> None:
        self._client_raw(encode_client_frame(OP_TEXT, b"he", fin=False))
        self._client_raw(encode_client_frame(OP_CONTINUATION, b"llo", fin=True))
        self.assertEqual(self.ws.recv(), ("text", b"hello"))

    def test_ping_inside_fragmentation_is_answered(self) -> None:
        self._client_raw(encode_client_frame(OP_TEXT, b"he", fin=False))
        self._client_raw(encode_client_frame(OP_PING, b"p"))
        self._client_raw(encode_client_frame(OP_CONTINUATION, b"llo", fin=True))
        self.assertEqual(self.ws.recv(), ("text", b"hello"))
        frames = read_server_frames(self._read_client())
        self.assertEqual([(opcode, payload) for _fin, opcode, payload in frames], [(OP_PONG, b"p")])

    def test_continuation_without_start_is_refused(self) -> None:
        self._client_raw(encode_client_frame(OP_CONTINUATION, b"oops"))
        with self.assertRaises(WebSocketError) as ctx:
            self.ws.recv()
        self.assertEqual(ctx.exception.reason, "unexpected_continuation")

    def test_new_data_frame_during_fragmentation_is_refused(self) -> None:
        self._client_raw(encode_client_frame(OP_TEXT, b"he", fin=False))
        self._client_raw(encode_client_frame(OP_TEXT, b"llo"))
        with self.assertRaises(WebSocketError) as ctx:
            self.ws.recv()
        self.assertEqual(ctx.exception.reason, "fragment_out_of_order")

    def test_close_during_fragmentation_is_refused(self) -> None:
        self._client_raw(encode_client_frame(OP_TEXT, b"he", fin=False))
        self._client_raw(encode_client_frame(OP_CLOSE, struct.pack(">H", CLOSE_NORMAL)))
        with self.assertRaises(WebSocketError) as ctx:
            self.ws.recv()
        self.assertEqual(ctx.exception.reason, "interleaved_close")

    def test_fragmented_control_frame_is_refused(self) -> None:
        self._client_raw(encode_client_frame(OP_PING, b"x", fin=False))
        with self.assertRaises(WebSocketError) as ctx:
            self.ws.recv()
        self.assertEqual(ctx.exception.reason, "control_frame_fragmented")

    def test_control_frame_too_large_is_refused(self) -> None:
        self._client_raw(encode_client_frame(OP_PING, b"x" * 200))
        with self.assertRaises(WebSocketError) as ctx:
            self.ws.recv()
        self.assertEqual(ctx.exception.reason, "control_frame_too_large")

    def test_declared_length_over_limit_is_refused_without_reading_body(self) -> None:
        # 64-bit length of 8 MiB while the limit is 4096: the refusal must come
        # from the header alone, so the body is never allocated or waited for.
        header = bytes([0x81, 0x80 | 127]) + struct.pack(">Q", 8 * 1024 * 1024) + b"\x00\x00\x00\x00"
        self._client_raw(header)
        with self.assertRaises(WebSocketError) as ctx:
            self.ws.recv()
        self.assertEqual(ctx.exception.reason, "message_too_big")
        self.assertEqual(ctx.exception.close_code, 1009)

    def test_message_too_big_via_fragments(self) -> None:
        self._client_raw(encode_client_frame(OP_BINARY, b"a" * 3000, fin=False))
        self._client_raw(encode_client_frame(OP_CONTINUATION, b"b" * 3000, fin=True))
        with self.assertRaises(WebSocketError) as ctx:
            self.ws.recv()
        self.assertEqual(ctx.exception.reason, "message_too_big")

    def test_most_significant_length_bit_is_refused(self) -> None:
        header = bytes([0x82, 0x80 | 127]) + struct.pack(">Q", 1 << 63) + b"\x00\x00\x00\x00"
        self._client_raw(header)
        with self.assertRaises(WebSocketError) as ctx:
            self.ws.recv()
        self.assertEqual(ctx.exception.reason, "length_invalid")

    def test_truncated_frame(self) -> None:
        self._client_raw(b"\x81\x85\x00\x00\x00\x00ab")  # declares 5, sends 2
        self.client_sock.close()
        with self.assertRaises(WebSocketError) as ctx:
            self.ws.recv()
        self.assertEqual(ctx.exception.reason, "truncated_frame")

    def test_unknown_opcode(self) -> None:
        self._client_raw(encode_client_frame(0x3, b"x"))
        with self.assertRaises(WebSocketError) as ctx:
            self.ws.recv()
        self.assertEqual(ctx.exception.reason, "opcode_unknown")

    def test_send_text_is_unmasked_and_readable(self) -> None:
        self.ws.send_text("привет")
        frames = read_server_frames(self._read_client())
        self.assertEqual(len(frames), 1)
        fin, opcode, payload = frames[0]
        self.assertTrue(fin)
        self.assertEqual(opcode, OP_TEXT)
        self.assertEqual(payload.decode("utf-8"), "привет")

    def test_send_close_then_send_frame_is_refused(self) -> None:
        self.ws.send_close(CLOSE_NORMAL, "bye")
        frames = read_server_frames(self._read_client())
        self.assertEqual(frames[0][1], OP_CLOSE)
        self.assertEqual(struct.unpack(">H", frames[0][2][:2])[0], CLOSE_NORMAL)
        with self.assertRaises(WebSocketError) as ctx:
            self.ws.send_text("nope")
        self.assertEqual(ctx.exception.reason, "connection_closed")

    def test_send_after_peer_gone_is_typed(self) -> None:
        self.client_sock.close()
        with self.assertRaises(WebSocketError) as ctx:
            self.ws.send_text("x")
        self.assertEqual(ctx.exception.reason, "peer_unreachable")

    def test_close_is_safe_twice(self) -> None:
        self.ws.close()
        self.ws.close()
        self.assertTrue(self.ws.closed)

    def test_extended_16bit_length_round_trip(self) -> None:
        payload = b"z" * 200
        self._client_raw(encode_client_frame(OP_BINARY, payload))
        self.assertEqual(self.ws.recv(), ("binary", payload))

    def test_interleaved_two_messages(self) -> None:
        self._client_raw(encode_client_frame(OP_TEXT, b"one"))
        self._client_raw(encode_client_frame(OP_TEXT, b"two"))
        self.assertEqual(self.ws.recv(), ("text", b"one"))
        self.assertEqual(self.ws.recv(), ("text", b"two"))


if __name__ == "__main__":
    unittest.main()
