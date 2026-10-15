import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from factory.core.telegram_bridge import (
    ChatRuntimeState,
    TelegramApiUnavailableError,
    TelegramBotApi,
    TelegramBridge,
    _extract_named_workdir_files,
)


class _FakeMemoryManager:
    def __init__(self) -> None:
        self.review_calls = []

    def review_turn(self, **kwargs):
        self.review_calls.append(kwargs)
        return []

    def format_memory_update_message(self, writes):
        return ""


class TelegramBridgeTaskOutcomeTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.env_patch = patch.dict(
            os.environ,
            {
                "FACTORY_DATA_DIR": self.tmp.name,
                "IGOREK_FACTORY_HOME": self.tmp.name,
            },
        )
        self.env_patch.start()
        self.addCleanup(self.env_patch.stop)

    def _bridge(self) -> tuple[TelegramBridge, _FakeMemoryManager, list[dict]]:
        bridge = TelegramBridge.__new__(TelegramBridge)
        memory = _FakeMemoryManager()
        sent: list[dict] = []
        bridge.profile = "cc-openrouter"
        bridge.workdir = self.tmp.name
        bridge.memory_manager = memory
        bridge._safe_send = lambda chat_id, text, reply_to=None, reply_markup=None: sent.append(
            {"chat_id": chat_id, "text": text, "reply_to": reply_to, "reply_markup": reply_markup}
        )
        bridge._send_output_files = lambda *args, **kwargs: None
        return bridge, memory, sent

    def _entries(self, session_name: str) -> list[dict]:
        path = Path(self.tmp.name) / "chat_sessions" / f"{session_name}.jsonl"
        return [
            json.loads(line)
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]

    def test_failed_task_does_not_append_assistant_error_to_history(self):
        bridge, memory, sent = self._bridge()
        session = ChatRuntimeState(
            chat_id="42424242",
            user_id="1",
            chat_session="telegram-chat-test",
            last_user_message_id=42,
        )

        bridge._send_task_outcome(
            session,
            "Ты написал два имени через дефис?",
            {
                "task_id": "task-fail",
                "status": "fail",
                "error_message": "HTTP 429: rate-limited upstream",
            },
        )

        entries = self._entries("telegram-chat-test")
        self.assertEqual([entry["role"] for entry in entries], ["user"])
        self.assertEqual(entries[0]["text"], "Ты написал два имени через дефис?")
        self.assertEqual(memory.review_calls, [])
        self.assertEqual(sent[0]["text"], "HTTP 429: rate-limited upstream")
        self.assertEqual(sent[0]["reply_to"], 42)

    def test_successful_task_still_appends_assistant_to_history(self):
        bridge, memory, sent = self._bridge()
        session = ChatRuntimeState(
            chat_id="42424242",
            user_id="1",
            chat_session="telegram-chat-test",
            last_user_message_id=42,
        )

        bridge._send_task_outcome(
            session,
            "Привет",
            {
                "task_id": "task-ok",
                "status": "ok",
                "output_text": "Ответ ассистента",
                "workspace_files": [],
            },
        )

        entries = self._entries("telegram-chat-test")
        self.assertEqual([entry["role"] for entry in entries], ["user", "assistant"])
        self.assertEqual(entries[1]["text"], "Ответ ассистента")
        self.assertEqual(entries[1]["task_id"], "task-ok")
        self.assertEqual(len(memory.review_calls), 1)
        self.assertEqual(sent[0]["text"], "Ответ ассистента")

    def test_waiting_mcp_approval_shows_buttons_without_raw_tool_json(self):
        bridge, memory, sent = self._bridge()
        session = ChatRuntimeState(chat_id="test-chat-id", user_id="test-user-id", chat_session="telegram-chat-test")

        bridge._send_task_outcome(
            session,
            "Сделай видео",
            {
                "task_id": "12345678-1234-1234-1234-123456789012",
                "status": "waiting_approval",
                "approval_blocked_tools": [{"server_name": "FalImage", "reason": "генерация видео"}],
            },
        )

        self.assertEqual(memory.review_calls, [])
        self.assertIn("Игорёк запрашивает доступ к FalImage", sent[0]["text"])
        self.assertNotIn("suggest_enable_mcp", sent[0]["text"])
        labels = [button["text"] for row in sent[0]["reply_markup"]["inline_keyboard"] for button in row]
        self.assertEqual(labels, ["Разрешить один раз", "Разрешить до конца сессии", "Отклонить", "Отменить задачу"])

    def test_bot_commands_advertise_explicit_remember(self):
        commands = {item["command"] for item in TelegramBridge._bot_commands()}

        self.assertIn("remember", commands)

    @patch("factory.core.telegram_bridge.time.sleep")
    def test_bootstrap_retries_transient_get_me_failure(self, sleep_mock):
        bridge = TelegramBridge.__new__(TelegramBridge)

        class FakeTelegram:
            def __init__(self):
                self.get_me_calls = 0

            def get_me(self):
                self.get_me_calls += 1
                if self.get_me_calls == 1:
                    raise TelegramApiUnavailableError("temporary DNS failure")
                return {"username": "igorek_test", "id": 42}

            def set_my_commands(self, _commands):
                return None

            def get_updates(self, **_kwargs):
                raise KeyboardInterrupt

        bridge.telegram = FakeTelegram()
        bridge.profile = "cc-openrouter"
        bridge.auto_approve = False
        bridge.auto_approve_scope = "session"
        bridge.allowed_user_ids = {"1"}
        bridge.workdir = self.tmp.name
        bridge.offset = None
        bridge.poll_timeout_s = 30

        with self.assertRaises(KeyboardInterrupt):
            bridge.run_forever()

        self.assertEqual(bridge.telegram.get_me_calls, 2)
        sleep_mock.assert_called_once_with(3)

    @patch("factory.core.telegram_bridge.time.sleep")
    def test_bootstrap_does_not_retry_non_transport_get_me_failure(self, sleep_mock):
        bridge = TelegramBridge.__new__(TelegramBridge)

        class FakeTelegram:
            def get_me(self):
                raise RuntimeError("Telegram API getMe failed: HTTP 401")

        bridge.telegram = FakeTelegram()

        with self.assertRaisesRegex(RuntimeError, "HTTP 401"):
            bridge.run_forever()

        sleep_mock.assert_not_called()


class TelegramBotApiRichMessageTest(unittest.TestCase):
    def test_rich_message_mode_uses_send_rich_message_payload(self):
        calls: list[tuple[str, dict]] = []
        bot = TelegramBotApi("token")
        bot._request_json = lambda method, payload: calls.append((method, payload)) or {"ok": True, "result": {}}

        with patch.dict(os.environ, {"FACTORY_TELEGRAM_RICH_MESSAGES": "true"}):
            bot.send_message("42", "# Заголовок\n\n| A | B |\n|---|---|\n| 1 | 2 |", reply_to_message_id=7)

        self.assertEqual(calls[0][0], "sendRichMessage")
        self.assertEqual(calls[0][1]["rich_message"]["markdown"].splitlines()[0], "# Заголовок")
        self.assertEqual(calls[0][1]["reply_parameters"], {"message_id": 7})
        self.assertNotIn("parse_mode", calls[0][1])

    def test_rich_message_failure_falls_back_to_existing_html_send_message(self):
        calls: list[tuple[str, dict]] = []
        bot = TelegramBotApi("token")

        def fake_request(method, payload):
            calls.append((method, payload))
            if method == "sendRichMessage":
                raise RuntimeError("Telegram API sendRichMessage failed: HTTP 400")
            return {"ok": True, "result": {}}

        bot._request_json = fake_request

        with patch.dict(os.environ, {"FACTORY_TELEGRAM_RICH_MESSAGES": "true"}):
            bot.send_message("42", "**Жирный**")

        self.assertEqual([method for method, _payload in calls], ["sendRichMessage", "sendMessage"])
        self.assertEqual(calls[1][1]["parse_mode"], "HTML")
        self.assertIn("<b>Жирный</b>", calls[1][1]["text"])

    def test_rich_message_failure_is_logged_before_html_fallback(self):
        calls: list[tuple[str, dict]] = []
        bot = TelegramBotApi("token")

        def fake_request(method, payload):
            calls.append((method, payload))
            if method == "sendRichMessage":
                raise RuntimeError("Telegram API sendRichMessage failed: HTTP 400")
            return {"ok": True, "result": {}}

        bot._request_json = fake_request
        captured = io.StringIO()

        with patch.dict(os.environ, {"FACTORY_TELEGRAM_RICH_MESSAGES": "true"}):
            with redirect_stdout(captured):
                bot.send_message("42", "**Жирный**")

        log_line = captured.getvalue()
        self.assertIn("rich message failed", log_line)
        self.assertIn("RuntimeError", log_line)
        self.assertEqual([method for method, _payload in calls], ["sendRichMessage", "sendMessage"])


class ExtractNamedWorkdirFilesTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base_dir = Path(self.tmp.name)

    def test_xlsx_file_mentioned_in_text_is_extracted(self):
        (self.base_dir / "report.xlsx").write_bytes(b"xlsx-bytes")
        text = "Готово, создал файл report.xlsx с данными."

        resolved = _extract_named_workdir_files(self.base_dir, text)

        self.assertEqual(resolved, [str(self.base_dir / "report.xlsx")])

    def test_xlsx_file_under_subdirectory_is_extracted(self):
        subdir = self.base_dir / "fitness"
        subdir.mkdir()
        backup = subdir / "weight_calories_backup_2026-09-06.xlsx"
        backup.write_bytes(b"xlsx-bytes")
        (self.base_dir / "fitness" / "weight_calories.xlsx").write_bytes(b"original")
        text = (
            "Готово, Игорь. Копия создана:\n\n"
            "**`fitness/weight_calories_backup_2026-09-06.xlsx`** "
            "(9781 байт — размер совпадает с оригиналом `weight_calories.xlsx`)\n\n"
            "Оригинал на месте, не тронут."
        )

        resolved = _extract_named_workdir_files(self.base_dir, text)

        self.assertEqual(resolved, [str(backup)])

    def test_missing_xlsx_file_is_not_extracted(self):
        text = "Готово, создал файл report.xlsx с данными."

        resolved = _extract_named_workdir_files(self.base_dir, text)

        self.assertEqual(resolved, [])


if __name__ == "__main__":
    unittest.main()
