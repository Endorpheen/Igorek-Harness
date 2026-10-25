"""Contract tests for Anthropic-compatible block helpers."""

import json
import unittest
from types import SimpleNamespace

from factory.api_server_core.anthropic_messages import (
    flatten_block_content,
    is_tool_result_only_user_turn,
)


def text_block(text: str) -> dict:
    return {"type": "text", "text": text}


def tool_result_block(tool_use_id: str = "toolu_123", content=None, is_error: bool = False) -> dict:
    return {
        "type": "tool_result",
        "tool_use_id": tool_use_id,
        "content": content if content is not None else [{"type": "text", "text": "ok"}],
        "is_error": is_error,
    }


class TestFlattenBlockContent(unittest.TestCase):
    def test_visible_text_is_kept_and_thinking_blocks_do_not_leak(self):
        content = [
            {"type": "thinking", "thinking": "private reasoning"},
            {"type": "text", "text": "Вот что я знаю."},
            {"type": "redacted_thinking", "data": "redacted"},
            {"type": "signature", "signature": "sig"},
        ]
        self.assertEqual(flatten_block_content(content, role="assistant"), "Вот что я знаю.")

    def test_nested_tool_result_keeps_id_error_and_unicode_result(self):
        content = [
            {
                "type": "tool_result",
                "tool_use_id": "toolu_abc",
                "content": [{"type": "text", "text": "Результат: погода в Москве +21° ☀️"}],
                "is_error": True,
            }
        ]
        result = flatten_block_content(content, role="user")
        self.assertIn("[tool_result id=toolu_abc error=true]", result)
        self.assertIn("Результат: погода в Москве +21° ☀️", result)

    def test_tool_use_keeps_id_name_and_unicode_json_input(self):
        content = [
            {
                "type": "tool_use",
                "id": "toolu_xyz",
                "name": "get_weather",
                "input": {"city": "Москва", "temp": 21},
            }
        ]
        result = flatten_block_content(content, role="user")
        self.assertIn("[tool_use id=toolu_xyz name=get_weather input=", result)
        payload = json.loads(result.split("input=", 1)[1].rstrip("]"))
        self.assertEqual(payload, {"city": "Москва", "temp": 21})

    def test_unknown_block_keeps_text_and_empty_text_is_skipped(self):
        result = flatten_block_content(
            [
                {"type": "future_block", "content": "kept"},
                {"type": "text", "text": ""},
            ],
            role="user",
        )
        self.assertEqual(result, "kept")

    def test_image_and_document_omit_media_type_without_payload(self):
        image_result = flatten_block_content(
            [{"type": "image", "source": {"media_type": "image/png", "data": "aGVsbG8="}}],
            role="user",
        )
        self.assertIn("[image omitted media_type=image/png]", image_result)
        self.assertNotIn("aGVsbG8", image_result)

        doc_result = flatten_block_content(
            [{"type": "document", "source": {"media_type": "application/pdf"}}],
            role="user",
        )
        self.assertIn("[document omitted media_type=application/pdf]", doc_result)


class TestIsToolResultOnlyUserTurn(unittest.TestCase):
    def test_tool_result_only_user_turn_detection(self):
        tool_result_msg = SimpleNamespace(
            content=[
                tool_result_block("toolu_42"),
                text_block(""),
                text_block("<system-reminder>reminder</system-reminder>"),
            ]
        )
        self.assertTrue(is_tool_result_only_user_turn(tool_result_msg))

        plain_user_msg = SimpleNamespace(
            content=[
                tool_result_block("toolu_42"),
                text_block("Какая сейчас погода?"),
            ]
        )
        self.assertFalse(is_tool_result_only_user_turn(plain_user_msg))


if __name__ == "__main__":
    unittest.main()
