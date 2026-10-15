"""Telegram progress must surface the assistant preamble ("what I'm doing").

Covers:
- TelegramToolProgress.callback('assistant.text', ...) renders a 💬 line.
- execute_cc_backend_task emits 'assistant.text' before running tools when the
  round contains assistant text plus tool calls.
"""
import json
import tempfile
import unittest
from pathlib import Path

from factory.core import cc_backend_executor
from factory.core.telegram_bridge import TelegramToolProgress


class _FakeTelegram:
    def __init__(self) -> None:
        self.edits: list[tuple[object, str]] = []

    def edit_message_text(self, chat_id, message_id, text):
        self.edits.append((message_id, text))
        return {"ok": True}

    def send_chat_action(self, chat_id, action="typing"):
        return None


class _FakeBridge:
    def __init__(self) -> None:
        self.telegram = _FakeTelegram()
        self.sent: list[str] = []

    def _safe_send(self, chat_id, text):
        self.sent.append(text)
        return {"result": {"message_id": 42}}


class TelegramProgressPreambleTest(unittest.TestCase):
    def test_assistant_text_adds_preamble_line(self):
        bridge = _FakeBridge()
        progress = TelegramToolProgress(bridge, "1")

        progress.callback("assistant.text", text="Сначала осмотрюсь, потом создам файл.")

        self.assertTrue(progress.lines)
        self.assertIn("💬", progress.lines[0])
        self.assertIn("осмотрюсь", progress.lines[0])
        self.assertTrue(bridge.sent or bridge.telegram.edits)

    def test_assistant_text_is_truncated(self):
        bridge = _FakeBridge()
        progress = TelegramToolProgress(bridge, "1")

        progress.callback("assistant.text", text="x" * 5000)

        self.assertLessEqual(
            len(progress.lines[0]),
            TelegramToolProgress.PREAMBLE_MAX_CHARS + 4,
        )

    def test_unknown_event_is_ignored(self):
        bridge = _FakeBridge()
        progress = TelegramToolProgress(bridge, "1")

        progress.callback("something.else", text="x")

        self.assertEqual(progress.lines, [])

    def test_executor_emits_assistant_preamble_before_tools(self):
        events: list[tuple[str, dict]] = []
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "note.txt"
            target.write_text("hello", encoding="utf-8")
            old_streaming = cc_backend_executor._http_post_streaming_openai
            calls = {"n": 0}

            def fake_streaming(_url, _payload, *_args, **_kwargs):
                calls["n"] += 1
                if calls["n"] == 1:
                    return (
                        {
                            "choices": [
                                {
                                    "index": 0,
                                    "message": {
                                        "role": "assistant",
                                        "content": "Сначала прочитаю файл.",
                                        "tool_calls": [
                                            {
                                                "id": "call_read",
                                                "type": "function",
                                                "function": {
                                                    "name": "Read",
                                                    "arguments": json.dumps(
                                                        {"file_path": str(target)}
                                                    ),
                                                },
                                            }
                                        ],
                                    },
                                    "finish_reason": "tool_calls",
                                }
                            ],
                            "usage": {},
                        },
                        1,
                    )
                return (
                    {
                        "choices": [
                            {
                                "index": 0,
                                "message": {"role": "assistant", "content": "Готово."},
                                "finish_reason": "stop",
                            }
                        ],
                        "usage": {},
                    },
                    1,
                )

            cc_backend_executor._http_post_streaming_openai = fake_streaming
            try:
                cc_backend_executor.execute_cc_backend_task(
                    base_url="http://127.0.0.1:1",
                    model="test",
                    system_prompt="test",
                    user_message="Прочитай файл.",
                    workdir=tmp,
                    artifact_workdir=tmp,
                    enabled_tools=[
                        {
                            "name": "Read",
                            "input_schema": {
                                "type": "object",
                                "properties": {"file_path": {"type": "string"}},
                                "required": ["file_path"],
                            },
                        }
                    ],
                    api_format="openai",
                    request_headers={"Authorization": "Bearer test"},
                    progress_callback=lambda event_type, **kw: events.append(
                        (event_type, kw)
                    ),
                )
            finally:
                cc_backend_executor._http_post_streaming_openai = old_streaming

        preamble_events = [
            kw for event_type, kw in events if event_type == "assistant.text"
        ]
        self.assertTrue(preamble_events, f"no assistant.text events in {events}")
        self.assertIn("прочитаю", preamble_events[0].get("text", ""))


if __name__ == "__main__":
    unittest.main()
