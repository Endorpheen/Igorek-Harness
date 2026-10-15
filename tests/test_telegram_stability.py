"""Telegram bridge stability: polling backoff, media limits, edit flood control."""
import os
import unittest
from unittest.mock import patch

import factory.core.telegram_bridge as tb
from factory.core.telegram_bridge import (
    TelegramBotApi,
    TelegramToolProgress,
    _POLL_MAX_BACKOFF_S,
    _max_download_bytes,
    _next_poll_backoff,
)


class _FakeResp:
    def __init__(self, data: bytes, headers: dict | None = None) -> None:
        self._data = data
        self.headers = headers or {}

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self, n: int | None = None) -> bytes:
        return self._data[:n] if n is not None else self._data


class _FloodTelegram:
    def __init__(self) -> None:
        self.edit_calls = 0

    def edit_message_text(self, *args, **kwargs):
        self.edit_calls += 1
        raise RuntimeError("Too Many Requests: retry after 5")

    def send_chat_action(self, *args, **kwargs):
        return None


class _FloodBridge:
    def __init__(self) -> None:
        self.telegram = _FloodTelegram()
        self.sent: list[str] = []

    def _safe_send(self, chat_id, text):
        self.sent.append(text)
        return {"result": {"message_id": 1000 + len(self.sent)}}


class PollBackoffTest(unittest.TestCase):
    def test_backoff_grows_and_caps(self):
        self.assertEqual(_next_poll_backoff(0), 0.0)
        self.assertEqual(_next_poll_backoff(1), 3.0)
        self.assertEqual(_next_poll_backoff(2), 6.0)
        self.assertLessEqual(_next_poll_backoff(50), _POLL_MAX_BACKOFF_S)


class MediaDownloadLimitTest(unittest.TestCase):
    def test_default_download_cap_is_100mb(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("FACTORY_TELEGRAM_MAX_DOWNLOAD_MB", None)
            self.assertEqual(_max_download_bytes(), 100 * 1024 * 1024)

    def test_download_cap_env_override(self):
        with patch.dict(os.environ, {"FACTORY_TELEGRAM_MAX_DOWNLOAD_MB": "250"}):
            self.assertEqual(_max_download_bytes(), 250 * 1024 * 1024)

    def test_download_cap_env_invalid_ignored(self):
        with patch.dict(os.environ, {"FACTORY_TELEGRAM_MAX_DOWNLOAD_MB": "abc"}):
            self.assertEqual(_max_download_bytes(), 100 * 1024 * 1024)

    def test_rejects_declared_too_large(self):
        api = TelegramBotApi("tok")
        with patch.object(
            tb.urllib_request,
            "urlopen",
            return_value=_FakeResp(b"", {"Content-Length": "100"}),
        ):
            with self.assertRaises(RuntimeError):
                api.download_file_bytes("f", max_bytes=10)

    def test_rejects_actual_too_large(self):
        api = TelegramBotApi("tok")
        with patch.object(
            tb.urllib_request,
            "urlopen",
            return_value=_FakeResp(b"x" * 50, {}),
        ):
            with self.assertRaises(RuntimeError):
                api.download_file_bytes("f", max_bytes=10)

    def test_ok_uses_media_read_timeout(self):
        api = TelegramBotApi("tok")
        captured: dict = {}

        def fake_open(req, timeout=None):
            captured["timeout"] = timeout
            return _FakeResp(b"hello", {"Content-Length": "5"})

        with patch.object(tb.urllib_request, "urlopen", side_effect=fake_open):
            data = api.download_file_bytes("f", max_bytes=10, read_timeout_s=7.0)

        self.assertEqual(data, b"hello")
        self.assertEqual(captured["timeout"], 7.0)


class ProgressFloodControlTest(unittest.TestCase):
    def test_first_flush_sends_message(self):
        bridge = _FloodBridge()
        progress = TelegramToolProgress(bridge, "1")

        progress.callback("tool.started", tool_name="Read", args={})

        self.assertEqual(len(bridge.sent), 1)
        self.assertIsNotNone(progress.message_id)

    def test_failing_edits_do_not_spam_new_messages(self):
        bridge = _FloodBridge()
        progress = TelegramToolProgress(bridge, "1")
        progress.lines.append("line")
        progress.message_id = 55
        progress.last_edit_ts = 0.0

        for _ in range(TelegramToolProgress.EDIT_MAX_FAILURES):
            progress.next_edit_allowed_ts = 0.0
            progress._flush_locked(force=False)

        self.assertFalse(progress.can_edit)
        # No new intermediate messages were sent despite repeated edit failures.
        self.assertEqual(bridge.sent, [])

        progress.next_edit_allowed_ts = 0.0
        progress._flush_locked(force=False)
        self.assertEqual(bridge.sent, [])

    def test_finish_still_sends_final_message_after_failures(self):
        bridge = _FloodBridge()
        progress = TelegramToolProgress(bridge, "1")
        progress.lines.append("line")
        progress.message_id = 55
        progress.last_edit_ts = 0.0

        for _ in range(TelegramToolProgress.EDIT_MAX_FAILURES):
            progress.next_edit_allowed_ts = 0.0
            progress._flush_locked(force=False)

        progress.finish()

        self.assertEqual(len(bridge.sent), 1)


if __name__ == "__main__":
    unittest.main()
