import os
import tempfile
import unittest
from unittest.mock import patch

from factory.core import cc_backend_executor, memory_review, worker


class OpenCodeDualTransportTest(unittest.TestCase):
    def test_worker_uses_session_model_override_for_remote_opencode(self):
        model = worker._effective_cc_backend_model(
            provider="opencode-go",
            configured_model="deepseek-v4-flash",
            task_metadata={"model_override": "qwen3.7-max"},
            base_url="https://opencode.ai/zen/go/v1",
        )
        self.assertEqual(model, "qwen3.7-max")

    def test_worker_selects_transport_per_model(self):
        with patch("factory.core.opencode_go_catalog.opencode_go_model_transport", side_effect=lambda model: {"deepseek-v4-flash": "openai", "qwen3.7-max": "anthropic"}.get(model)):
            config = {"provider": "opencode-go", "api_format": "openai"}
            self.assertEqual(worker._resolve_cc_backend_api_format(config, "deepseek-v4-flash"), "openai")
            self.assertEqual(worker._resolve_cc_backend_api_format(config, "qwen3.7-max"), "anthropic")

    def test_worker_selects_anthropic_transport_for_minimax_m3(self):
        config = {"provider": "opencode-go", "api_format": "openai"}

        self.assertEqual(worker._resolve_cc_backend_api_format(config, "minimax-m3"), "anthropic")

    def test_worker_uses_x_api_key_auth_for_anthropic_opencode(self):
        with patch.dict(os.environ, {"OPENCODE_API_KEY": "test-key"}, clear=True):
            headers = worker._resolve_cc_backend_auth_headers({"provider": "opencode-go"}, api_format="anthropic")
        self.assertNotIn("Authorization", headers)
        self.assertEqual(headers["x-api-key"], "test-key")
        self.assertEqual(headers["Origin"], "https://opencode.ai")
        self.assertIn("Mozilla/5.0", headers["User-Agent"])

    def test_executor_avoids_duplicate_v1_for_anthropic_opencode(self):
        self.assertEqual(
            cc_backend_executor._anthropic_messages_url("https://opencode.ai/zen/go/v1"),
            "https://opencode.ai/zen/go/v1/messages",
        )
        self.assertEqual(
            cc_backend_executor._anthropic_messages_url("http://127.0.0.1:8001"),
            "http://127.0.0.1:8001/v1/messages",
        )

    def test_executor_posts_anthropic_opencode_payload_to_single_v1_messages_path(self):
        captured = {}

        def fake_post(url, payload, headers, timeout_s):
            captured.update(url=url, payload=payload, headers=headers, timeout_s=timeout_s)
            return {"content": [{"type": "text", "text": "ok"}], "usage": {}}, 1

        with tempfile.TemporaryDirectory() as tmp, patch.object(cc_backend_executor, "_http_post", side_effect=fake_post):
            result = cc_backend_executor.execute_cc_backend_task(
                base_url="https://opencode.ai/zen/go/v1",
                model="qwen3.7-max",
                system_prompt="system",
                user_message="ping",
                workdir=tmp,
                artifact_workdir=tmp,
                api_format="anthropic",
                request_headers={"x-api-key": "test-key"},
                enabled_tools=[],
            )

        self.assertEqual(result["status"], "ok")
        self.assertEqual(captured["url"], "https://opencode.ai/zen/go/v1/messages")
        self.assertEqual(captured["headers"]["x-api-key"], "test-key")
        self.assertNotIn("Authorization", captured["headers"])
        self.assertEqual(captured["payload"]["model"], "qwen3.7-max")
        self.assertEqual(captured["payload"]["system"], "system")

    def test_memory_review_posts_qwen37_to_anthropic_opencode_endpoint(self):
        captured = {}

        def fake_post(url, payload, *, timeout_s, headers=None):
            captured.update(url=url, payload=payload, timeout_s=timeout_s, headers=headers)
            return {"content": [{"type": "text", "text": '{"items":[]}'}]}

        config = {
            "backend": "cc-backend",
            "provider": "opencode-go",
            "api_format": "openai",
            "base_url": "https://opencode.ai/zen/go/v1",
            "model": "qwen3.7-max",
        }
        with (
            patch.object(memory_review, "get_profile_config", return_value=config),
            patch.object(memory_review, "_http_post_json", side_effect=fake_post),
            patch("factory.core.opencode_go_catalog.opencode_go_model_transport", return_value="anthropic"),
            patch.dict(os.environ, {"OPENCODE_API_KEY": "test-key"}, clear=True),
        ):
            items = memory_review._review_with_profile(
                user_text="hello", assistant_text="hi", profile="cc-opencode-go",
                data_dir=None, timeout_s=12, log_fn=lambda _message: None,
            )

        self.assertEqual(items, [])
        self.assertEqual(captured["url"], "https://opencode.ai/zen/go/v1/messages")
        self.assertNotIn("Authorization", captured["headers"])
        self.assertEqual(captured["headers"]["x-api-key"], "test-key")
        self.assertEqual(captured["payload"]["model"], "qwen3.7-max")
        self.assertIn("system", captured["payload"])

    def test_memory_review_selects_anthropic_transport_for_qwen37(self):
        config = {"provider": "opencode-go", "api_format": "openai"}
        with patch("factory.core.opencode_go_catalog.opencode_go_model_transport", return_value="anthropic"):
            self.assertEqual(memory_review._review_api_format(config, "qwen3.7-max"), "anthropic")
        self.assertEqual(
            memory_review._anthropic_messages_url("https://opencode.ai/zen/go/v1"),
            "https://opencode.ai/zen/go/v1/messages",
        )


if __name__ == "__main__":
    unittest.main()
