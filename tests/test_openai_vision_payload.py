"""Regression tests: images must survive conversion to the OpenAI transport.

Context
-------
`build_anthropic_user_content()` builds Anthropic `image` blocks for attached
files, but `_anthropic_messages_to_openai()` used to drop them, so profiles with
`api_format: "openai"` (opencode-go / OpenRouter / any chat.completions backend)
saw text only. These tests pin the fixed behavior:

* `image` blocks become OpenAI `image_url` parts (base64 and url sources),
* `content` becomes a multi-part list whenever images are present,
* messages without images keep a plain string content (no behavior change).
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from factory.core import cc_backend_executor
from factory.core.multimodal_payload import build_anthropic_user_content

BASE64_PIXEL = "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8DwHwAFAAH/q842iQAAAABJRU5ErkJggg=="

JPEG_STUB = b"\xff\xd8\xff\xe0" + b"\x00" * 32 + b"\xff\xd9"


def _image_block(media_type: str = "image/png", data: str = BASE64_PIXEL) -> dict:
    return {
        "type": "image",
        "source": {"type": "base64", "media_type": media_type, "data": data},
    }


class AnthropicImageToOpenAITest(unittest.TestCase):
    def _convert_user(self, content):
        return cc_backend_executor._anthropic_messages_to_openai(
            [{"role": "user", "content": content}]
        )

    def test_base64_image_becomes_image_url_part(self):
        messages = self._convert_user([_image_block("image/jpeg", "QUJD")])
        self.assertEqual(len(messages), 1)
        content = messages[0]["content"]
        self.assertIsInstance(content, list)
        self.assertEqual(len(content), 1)
        self.assertEqual(content[0]["type"], "image_url")
        self.assertEqual(
            content[0]["image_url"]["url"], "data:image/jpeg;base64,QUJD"
        )

    def test_text_and_image_keep_order(self):
        messages = self._convert_user(
            [{"type": "text", "text": "посмотри скриншот"}, _image_block()]
        )
        content = messages[0]["content"]
        self.assertIsInstance(content, list)
        self.assertEqual([part["type"] for part in content], ["text", "image_url"])
        self.assertEqual(content[0]["text"], "посмотри скриншот")
        self.assertTrue(content[1]["image_url"]["url"].startswith("data:image/png;base64,"))

    def test_image_only_message_has_no_empty_text_part(self):
        messages = self._convert_user([_image_block()])
        content = messages[0]["content"]
        self.assertEqual([part["type"] for part in content], ["image_url"])

    def test_url_source_image_passes_through(self):
        block = {"type": "image", "source": {"type": "url", "url": "https://x/y.png"}}
        messages = self._convert_user([block])
        self.assertEqual(
            messages[0]["content"][0]["image_url"]["url"], "https://x/y.png"
        )

    def test_unknown_source_is_ignored_without_crash(self):
        block = {"type": "image", "source": {"type": "file", "file_id": "abc"}}
        messages = self._convert_user([block, {"type": "text", "text": "текст"}])
        self.assertEqual(messages[0]["content"], "текст")

    def test_plain_text_message_stays_string(self):
        messages = self._convert_user([{"type": "text", "text": "просто текст"}])
        self.assertEqual(messages[0]["content"], "просто текст")
        self.assertIsInstance(messages[0]["content"], str)

    def test_tool_result_message_still_works(self):
        messages = cc_backend_executor._anthropic_messages_to_openai(
            [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "call_1",
                            "content": "ok",
                        }
                    ],
                }
            ]
        )
        self.assertEqual(
            messages[0], {"role": "tool", "tool_call_id": "call_1", "content": "ok"}
        )

    def test_assistant_tool_calls_with_image_does_not_crash(self):
        messages = cc_backend_executor._anthropic_messages_to_openai(
            [
                {
                    "role": "assistant",
                    "content": [
                        {"type": "text", "text": "делаю"},
                        _image_block(),
                        {"type": "tool_use", "id": "call_9", "name": "bash", "input": {"cmd": "ls"}},
                    ],
                }
            ]
        )
        self.assertEqual(messages[0]["tool_calls"][0]["function"]["name"], "bash")
        self.assertIsInstance(messages[0]["content"], list)

    def test_system_prefixed_payload_from_call_site(self):
        """Mimics the real call site: [system] + messages passed to the converter."""
        messages = cc_backend_executor._anthropic_messages_to_openai(
            [{"role": "system", "content": "sys"}] + [
                {"role": "user", "content": [{"type": "text", "text": "hi"}, _image_block()]}
            ]
        )
        self.assertEqual(messages[0], {"role": "system", "content": "sys"})
        self.assertEqual([p["type"] for p in messages[1]["content"]], ["text", "image_url"])

    def test_roundtrip_from_real_file_via_build_anthropic_user_content(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "screenshot.jpg"
            path.write_bytes(JPEG_STUB)
            content = build_anthropic_user_content("моя активность", input_files=[str(path)])
            self.assertIsInstance(content, list)
            self.assertEqual(content[0]["type"], "text")
            self.assertEqual(content[1]["type"], "image")

            messages = self._convert_user(content)
            parts = messages[0]["content"]
            self.assertEqual([p["type"] for p in parts], ["text", "image_url"])
            self.assertEqual(parts[0]["text"], "моя активность")
            self.assertTrue(parts[1]["image_url"]["url"].startswith("data:image/jpeg;base64,"))


if __name__ == "__main__":
    unittest.main()
