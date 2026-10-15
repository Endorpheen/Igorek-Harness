import os
import unittest
from unittest.mock import patch

from factory.core import memory_review
from factory.core.builtin_memory_provider import BuiltinMemoryProvider


class MemoryUpdateMessageTest(unittest.TestCase):
    def test_forget_confirmation_reports_deleted_target(self):
        message = BuiltinMemoryProvider().format_memory_update_message(
            [
                {
                    "changed": True,
                    "rejected": False,
                    "action": "forget",
                    "target": "Вес Игоря — 103.4 кг",
                    "fact": "Вес Игоря — 103.4 кг",
                }
            ]
        )

        self.assertEqual(
            message,
            "Память обновлена:\n- удалено из Saved facts: Вес Игоря — 103.4 кг",
        )


class MemoryReviewTransportTest(unittest.TestCase):
    def test_opencode_profile_uses_openai_transport_and_bearer_auth(self):
        captured = {}

        def fake_post(url, payload, *, timeout_s, headers=None):
            captured.update(url=url, payload=payload, timeout_s=timeout_s, headers=headers)
            return {"choices": [{"message": {"content": '{"items":[]}'}}]}

        config = {
            "backend": "cc-backend",
            "provider": "opencode-go",
            "api_format": "openai",
            "base_url": "https://opencode.ai/zen/go/v1",
            "model": "deepseek-v4-flash",
        }
        with (
            patch.object(memory_review, "get_profile_config", return_value=config),
            patch.object(memory_review, "_http_post_json", side_effect=fake_post),
            patch.dict(os.environ, {"OPENCODE_API_KEY": "test-opencode-key"}, clear=True),
        ):
            items = memory_review._review_with_profile(
                user_text="hello",
                assistant_text="hi",
                profile="cc-opencode-go",
                data_dir=None,
                timeout_s=12,
                log_fn=lambda _message: None,
            )

        self.assertEqual([], items)
        self.assertEqual("https://opencode.ai/zen/go/v1/chat/completions", captured["url"])
        self.assertEqual("Bearer test-opencode-key", captured["headers"]["Authorization"])
        self.assertIn("User-Agent", captured["headers"])
        self.assertEqual("https://opencode.ai", captured["headers"]["Origin"])
        self.assertNotIn("system", captured["payload"])
        self.assertEqual("system", captured["payload"]["messages"][0]["role"])

    def test_anthropic_profile_keeps_messages_transport(self):
        captured = {}

        def fake_post(url, payload, *, timeout_s, headers=None):
            captured.update(url=url, payload=payload, timeout_s=timeout_s, headers=headers)
            return {"content": [{"type": "text", "text": '{"items":[]}'}]}

        config = {
            "backend": "cc-backend",
            "base_url": "http://127.0.0.1:8001",
            "model": "local-model",
        }
        with (
            patch.object(memory_review, "get_profile_config", return_value=config),
            patch.object(memory_review, "_http_post_json", side_effect=fake_post),
        ):
            items = memory_review._review_with_profile(
                user_text="hello",
                assistant_text="hi",
                profile="cc-local",
                data_dir=None,
                timeout_s=12,
                log_fn=lambda _message: None,
            )

        self.assertEqual([], items)
        self.assertEqual("http://127.0.0.1:8001/v1/messages", captured["url"])
        self.assertIsNone(captured["headers"])
        self.assertIn("system", captured["payload"])


if __name__ == "__main__":
    unittest.main()
