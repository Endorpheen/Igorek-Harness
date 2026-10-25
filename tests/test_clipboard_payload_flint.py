"""Reviewed pure clipboard payload tests authored by Flint."""

import base64
import unittest

from factory.core.tui.clipboard_copy import (
    OSC52_MAX_RAW_BYTES,
    osc52_sequence,
    tmux_clipboard_copy_ready,
    write_osc52_to_writer,
)

TMUX_PREFIX = "\x1bPtmux;\x1b\x1b]52;c;"
TMUX_SUFFIX = "\x07\x1b\\"
PLAIN_PREFIX = "\x1b]52;c;"
PLAIN_SEP = "\x07"


class FakeWriter:
    def __init__(self, write_exc=None, flush_exc=None):
        self.data = bytearray()
        self.flushed = False
        self.write_exc = write_exc
        self.flush_exc = flush_exc

    def write(self, b):
        if self.write_exc is not None:
            raise self.write_exc
        if not isinstance(b, bytes):
            raise AssertionError("Clipboard writer requires bytes")
        self.data.extend(b)
        return len(b)

    def flush(self):
        if self.flush_exc is not None:
            raise self.flush_exc
        self.flushed = True


class TestOsc52SequenceNormal(unittest.TestCase):
    def test_normal_osc52_unicode_roundtrip(self):
        text = "Привет, Игорь! 🎉"
        sequence, err = osc52_sequence(text, tmux=False)
        self.assertIsNone(err)
        self.assertIsNotNone(sequence)
        start = sequence.index(PLAIN_PREFIX) + len(PLAIN_PREFIX)
        end = sequence.index(PLAIN_SEP, start)
        encoded = sequence[start:end]
        decoded = base64.b64decode(encoded).decode("utf-8")
        self.assertEqual(decoded, text)


class TestOsc52SequenceTmux(unittest.TestCase):
    def test_tmux_wrapper_no_double_base64(self):
        text = "clipboard"
        sequence, err = osc52_sequence(text, tmux=True)
        self.assertIsNone(err)
        self.assertIsNotNone(sequence)
        self.assertTrue(sequence.startswith(TMUX_PREFIX))
        self.assertTrue(sequence.endswith(TMUX_SUFFIX))
        encoded = sequence[len(TMUX_PREFIX) : -len(TMUX_SUFFIX)]
        decoded_bytes = base64.b64decode(encoded)
        self.assertEqual(decoded_bytes.decode("utf-8"), text)
        # single base64 layer: one decode must yield the utf-8 text back
        self.assertEqual(base64.b64decode(encoded).decode("utf-8"), text)


class TestOsc52SequenceLimit(unittest.TestCase):
    def test_limit_100000_bytes(self):
        # equal to the limit is allowed (multi-byte Cyrillic: 2 bytes per char)
        text_ok = "а" * (OSC52_MAX_RAW_BYTES // 2)
        sequence, err = osc52_sequence(text_ok, tmux=False)
        self.assertIsNone(err)
        self.assertIsNotNone(sequence)

        # a value over the byte limit is forbidden, including Cyrillic
        text_bad = "а" * (OSC52_MAX_RAW_BYTES // 2 + 1)
        sequence, err = osc52_sequence(text_bad, tmux=False)
        self.assertIsNotNone(err)
        self.assertIsNone(sequence)


class TestWriteOsc52ToWriter(unittest.TestCase):
    def test_write_osc52_to_writer_writes_and_flushes(self):
        sequence, err = osc52_sequence("hello", tmux=False)
        self.assertIsNone(err)
        writer = FakeWriter()
        result = write_osc52_to_writer(sequence, writer)
        self.assertIsNone(result)
        self.assertEqual(bytes(writer.data), sequence.encode("utf-8"))
        self.assertTrue(writer.flushed)


class TestWriteOsc52ToWriterErrors(unittest.TestCase):
    def test_write_and_flush_errors_return_message(self):
        sequence, err = osc52_sequence("hello", tmux=False)
        self.assertIsNone(err)

        with self.subTest(case="write raises OSError"):
            writer = FakeWriter(write_exc=OSError("boom"))
            result = write_osc52_to_writer(sequence, writer)
            self.assertIsNotNone(result)
            self.assertIn("failed to write OSC 52", result)

        with self.subTest(case="flush raises ValueError"):
            writer = FakeWriter(flush_exc=ValueError("bad"))
            result = write_osc52_to_writer(sequence, writer)
            self.assertIsNotNone(result)
            self.assertIn("failed to write OSC 52", result)


class TestTmuxClipboardCopyReady(unittest.TestCase):
    def test_tmux_clipboard_copy_ready(self):
        # off is forbidden
        err = tmux_clipboard_copy_ready(lambda: "off", lambda: None)
        self.assertEqual(err, "tmux clipboard forwarding is disabled")

        # missing Ms capability is forbidden
        err = tmux_clipboard_copy_ready(
            lambda: "on", lambda: "clipboard\nMs: [missing]\n"
        )
        self.assertEqual(
            err, "tmux clipboard forwarding is unavailable: missing Ms capability"
        )

        # normal configuration or None is allowed
        self.assertIsNone(tmux_clipboard_copy_ready(lambda: "on", lambda: "Ms: [mux]"))
        self.assertIsNone(tmux_clipboard_copy_ready(lambda: None, lambda: None))


if __name__ == "__main__":
    unittest.main()
