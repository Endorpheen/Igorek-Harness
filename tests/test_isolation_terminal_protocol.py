#!/usr/bin/env python3
"""Unit tests for the versioned terminal protocol (no KVM, no browser required).

The adversarial cases mirror the task-channel suite: an unexpected field, an
oversize base64 blob, a smuggled host path in a free-form string and a
malformed frame must all be refused with a stable machine-readable reason, while
a well-formed message of every kind must round-trip byte-for-byte.
"""

from __future__ import annotations

import base64
import json
import os
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(os.environ.get("IGOREK_FACTORY_REPO", Path(__file__).resolve().parents[1]))
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from factory.core.isolation.terminal_protocol import (  # noqa: E402
    BROWSER_KINDS,
    MAX_DIMENSION,
    MAX_INPUT_BYTES,
    MAX_OUTPUT_BYTES,
    OWNER_KINDS,
    TERMINAL_PROTOCOL_VERSION,
    TerminalKind,
    TerminalProtocolError,
    decode_bounded,
    decode_message,
    decode_output,
    encode_message,
    validate_payload,
)

TICKET = "0123456789abcdef0123456789abcdef"
SESSION = "chat-20261002-091216-d920e8"


def b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


class RoundTripTest(unittest.TestCase):
    """Every message kind survives encode -> decode unchanged."""

    def test_browser_kinds_round_trip(self) -> None:
        cases = [
            (TerminalKind.OPEN, {"ticket": TICKET, "rows": 40, "cols": 120, "since_seq": 7}),
            (TerminalKind.OPEN, {"ticket": TICKET}),
            (TerminalKind.INPUT, {"data": b64(b"ls -la\r")}),
            (TerminalKind.RESIZE, {"rows": 24, "cols": 80}),
            (TerminalKind.ACK, {"seq": 12}),
            (TerminalKind.CLOSE, {}),
        ]
        for kind, payload in cases:
            with self.subTest(kind=kind, payload=payload):
                message = decode_message(encode_message(kind, 3, payload))
                self.assertEqual(message.kind, kind)
                self.assertEqual(message.seq, 3)
                self.assertEqual(message.payload, payload)

    def test_owner_kinds_round_trip(self) -> None:
        cases = [
            (TerminalKind.OPENED, {"terminal_id": "t-1", "session_id": SESSION, "rows": 40, "cols": 120}),
            (TerminalKind.OUTPUT, {"data": b64("\x1b[32mok\x1b[0m\r\n".encode())}),
            (TerminalKind.EXIT, {"code": 0}),
            (TerminalKind.ERROR, {"reason": "ticket_invalid"}),
        ]
        for kind, payload in cases:
            with self.subTest(kind=kind):
                message = decode_message(encode_message(kind, 1, payload))
                self.assertEqual(message.kind, kind)
                self.assertEqual(message.payload, payload)

    def test_kind_partitions_are_disjoint(self) -> None:
        self.assertEqual(BROWSER_KINDS & OWNER_KINDS, frozenset())
        self.assertEqual(BROWSER_KINDS | OWNER_KINDS, frozenset(TerminalKind))

    def test_version_is_positive_int(self) -> None:
        self.assertIsInstance(TERMINAL_PROTOCOL_VERSION, int)
        self.assertGreaterEqual(TERMINAL_PROTOCOL_VERSION, 1)


class MalformedFrameTest(unittest.TestCase):
    """Framing-level violations are refused, never guessed at."""

    def test_invalid_json(self) -> None:
        with self.assertRaises(TerminalProtocolError) as ctx:
            decode_message("{not json")
        self.assertEqual(ctx.exception.reason, "malformed_message")

    def test_not_an_object(self) -> None:
        with self.assertRaises(TerminalProtocolError) as ctx:
            decode_message("[1,2,3]")
        self.assertEqual(ctx.exception.reason, "malformed_message")

    def test_unknown_kind(self) -> None:
        with self.assertRaises(TerminalProtocolError) as ctx:
            decode_message(json.dumps({"kind": "shell", "seq": 1, "payload": {}}))
        self.assertEqual(ctx.exception.reason, "unknown_kind")

    def test_missing_kind(self) -> None:
        with self.assertRaises(TerminalProtocolError) as ctx:
            decode_message(json.dumps({"seq": 1, "payload": {}}))
        self.assertEqual(ctx.exception.reason, "missing_kind")

    def test_sequence_must_be_non_negative_int(self) -> None:
        for seq in (-1, True, "1", None, 1.5):
            with self.subTest(seq=seq):
                with self.assertRaises(TerminalProtocolError) as ctx:
                    decode_message(json.dumps({"kind": "ack", "seq": seq, "payload": {"seq": 1}}))
                self.assertEqual(ctx.exception.reason, "sequence_invalid")

    def test_payload_must_be_object(self) -> None:
        with self.assertRaises(TerminalProtocolError) as ctx:
            decode_message(json.dumps({"kind": "ack", "seq": 1, "payload": [1]}))
        self.assertEqual(ctx.exception.reason, "payload_invalid")

    def test_non_utf8_bytes(self) -> None:
        with self.assertRaises(TerminalProtocolError) as ctx:
            decode_message(b"\xff\xfe")
        self.assertEqual(ctx.exception.reason, "malformed_message")


class PayloadPolicyTest(unittest.TestCase):
    """The terminal payload policy is an exact-key structural allow-list."""

    def _reason(self, kind: TerminalKind, payload: dict[str, object]) -> str:
        with self.assertRaises(TerminalProtocolError) as ctx:
            validate_payload(kind, payload)
        return ctx.exception.reason

    def test_open_rejects_command_keys(self) -> None:
        for key in ("command", "argv", "shell_command", "cwd", "path"):
            with self.subTest(key=key):
                reason = self._reason(
                    TerminalKind.OPEN, {"ticket": TICKET, key: "/bin/sh -c whoami"}
                )
                self.assertEqual(reason, "payload_fields_invalid")

    def test_open_rejects_host_path_smuggling_as_ticket(self) -> None:
        reason = self._reason(TerminalKind.OPEN, {"ticket": "/home/example/.ssh/id_ed25519"})
        self.assertEqual(reason, "ticket_invalid")

    def test_open_rejects_bad_ticket_shapes(self) -> None:
        for ticket in ("", "A" * 32, "g" * 32, "0" * 31, "0" * 33, TICKET.upper()):
            with self.subTest(ticket=ticket):
                self.assertEqual(
                    self._reason(TerminalKind.OPEN, {"ticket": ticket}), "ticket_invalid"
                )

    def test_open_dimension_bounds(self) -> None:
        self.assertEqual(
            self._reason(TerminalKind.OPEN, {"ticket": TICKET, "rows": 0}), "payload_field_range"
        )
        self.assertEqual(
            self._reason(TerminalKind.OPEN, {"ticket": TICKET, "cols": MAX_DIMENSION + 1}),
            "payload_field_range",
        )
        self.assertEqual(
            self._reason(TerminalKind.OPEN, {"ticket": TICKET, "rows": True}),
            "payload_field_type",
        )

    def test_ack_carries_only_seq(self) -> None:
        self.assertEqual(self._reason(TerminalKind.ACK, {"seq": 1, "data": "x"}), "payload_fields_invalid")
        self.assertEqual(self._reason(TerminalKind.ACK, {}), "payload_fields_invalid")
        self.assertEqual(self._reason(TerminalKind.ACK, {"seq": -1}), "payload_field_type")

    def test_resize_is_exact(self) -> None:
        self.assertEqual(self._reason(TerminalKind.RESIZE, {"rows": 1}), "payload_fields_invalid")
        self.assertEqual(
            self._reason(TerminalKind.RESIZE, {"rows": 1, "cols": 1, "extra": 1}),
            "payload_fields_invalid",
        )

    def test_close_takes_no_fields(self) -> None:
        self.assertEqual(self._reason(TerminalKind.CLOSE, {"reason": "x"}), "payload_fields_invalid")
        validate_payload(TerminalKind.CLOSE, {})

    def test_input_requires_base64(self) -> None:
        self.assertEqual(self._reason(TerminalKind.INPUT, {"data": "not base64!*"}), "payload_field_invalid")
        self.assertEqual(self._reason(TerminalKind.INPUT, {"data": b"bytes"}), "payload_field_type")

    def test_output_requires_ascii_base64(self) -> None:
        self.assertEqual(self._reason(TerminalKind.OUTPUT, {"data": "привет"}), "payload_field_type")
        self.assertEqual(self._reason(TerminalKind.OUTPUT, {"data": "***"}), "payload_field_invalid")

    def test_exit_code_range(self) -> None:
        self.assertEqual(self._reason(TerminalKind.EXIT, {"code": 256}), "payload_field_range")
        self.assertEqual(self._reason(TerminalKind.EXIT, {"code": -2}), "payload_field_range")

    def test_error_reason_is_bounded_string(self) -> None:
        self.assertEqual(self._reason(TerminalKind.ERROR, {"reason": "x" * 200}), "payload_field_invalid")
        self.assertEqual(self._reason(TerminalKind.ERROR, {"reason": ""}), "payload_field_invalid")


class BoundedDecodeTest(unittest.TestCase):
    """Decoded blobs are hard-bounded before they reach the PTY or the socket."""

    def test_input_round_trip(self) -> None:
        self.assertEqual(decode_bounded(b64(b"\x00\x1b[12;3R").encode("ascii")), b"\x00\x1b[12;3R")

    def test_input_oversize_is_refused(self) -> None:
        blob = base64.b64encode(b"a" * (MAX_INPUT_BYTES + 1))
        with self.assertRaises(TerminalProtocolError) as ctx:
            decode_bounded(blob)
        self.assertEqual(ctx.exception.reason, "input_too_large")

    def test_oversize_is_rejected_before_decoding(self) -> None:
        # A huge base64 string must not be decoded at all: the length gate is
        # checked first, so a 1 GiB payload never allocates.
        with self.assertRaises(TerminalProtocolError) as ctx:
            decode_output(b"A" * (4 * 1024 * 1024))
        self.assertEqual(ctx.exception.reason, "output_too_large")

    def test_invalid_base64(self) -> None:
        with self.assertRaises(TerminalProtocolError) as ctx:
            decode_bounded(b"!!!!")
        self.assertEqual(ctx.exception.reason, "payload_field_invalid")

    def test_non_bytes_input(self) -> None:
        with self.assertRaises(TerminalProtocolError) as ctx:
            decode_bounded("str")  # type: ignore[arg-type]
        self.assertEqual(ctx.exception.reason, "payload_field_type")

    def test_input_message_rejects_oversize_at_validation(self) -> None:
        blob = base64.b64encode(b"a" * (MAX_INPUT_BYTES + 1)).decode("ascii")
        with self.assertRaises(TerminalProtocolError) as ctx:
            decode_message(encode_message(TerminalKind.INPUT, 1, {"data": blob}))
        self.assertEqual(ctx.exception.reason, "input_too_large")


class ReplaySemanticsTest(unittest.TestCase):
    """The replay contract used by the owner is expressible in the protocol."""

    def test_open_can_request_replay_from_seq(self) -> None:
        message = decode_message(
            encode_message(TerminalKind.OPEN, 1, {"ticket": TICKET, "since_seq": 42, "rows": 30, "cols": 100})
        )
        self.assertEqual(message.payload["since_seq"], 42)

    def test_ack_is_a_control_message_not_keystrokes(self) -> None:
        message = decode_message(encode_message(TerminalKind.ACK, 9, {"seq": 42}))
        self.assertEqual(message.kind, TerminalKind.ACK)
        self.assertNotIn("data", message.payload)


if __name__ == "__main__":
    unittest.main()
