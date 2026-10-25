"""Reviewed Flint response parsing tests against the canonical client."""

import json
import unittest

import importlib

import pytest

from inference_api_support import inference_app as inference_app

ContextCompressionClient = None


@pytest.fixture(autouse=True)
def canonical_compression_client(inference_app, monkeypatch):
    module = importlib.import_module(
        "factory.api_server_core.context_compression_client"
    )
    monkeypatch.setitem(
        globals(), "ContextCompressionClient", module.ContextCompressionClient
    )


class TestContextCompressionClientExtractCompletionText(unittest.TestCase):
    @staticmethod
    def _extract(payload):
        return ContextCompressionClient._extract_completion_text(payload)

    def test_choices_absent_empty_or_not_list(self):
        with self.subTest("choices отсутствует"):
            self.assertEqual(self._extract({}), "")
        with self.subTest("choices пустой список"):
            self.assertEqual(self._extract({"choices": []}), "")
        with self.subTest("choices не список"):
            self.assertEqual(self._extract({"choices": {"message": {}}}), "")
            self.assertEqual(self._extract({"choices": "not-a-list"}), "")

    def test_message_absent_or_not_object(self):
        with self.subTest("message отсутствует"):
            self.assertEqual(self._extract({"choices": [{}]}), "")
        with self.subTest("message не объект"):
            self.assertEqual(self._extract({"choices": [{"message": "text"}]}), "")
            self.assertEqual(self._extract({"choices": [{"message": None}]}), "")

    def test_string_content_with_spaces_and_cyrillic(self):
        with self.subTest("строка с пробелами и кириллицей"):
            payload = {"choices": [{"message": {"content": "  Привет, мир!  "}}]}
            self.assertEqual(self._extract(payload), "Привет, мир!")
        with self.subTest("простая строка"):
            payload = {"choices": [{"message": {"content": "plain"}}]}
            self.assertEqual(self._extract(payload), "plain")

    def test_list_content_with_text_blocks_and_noise(self):
        with self.subTest("текстовые и пустые блоки"):
            payload = {
                "choices": [
                    {
                        "message": {
                            "content": [
                                {"type": "text", "text": "Первый"},
                                {"type": "text", "text": ""},
                                {"type": "text", "text": "Второй"},
                                {"type": "image", "url": "http://example.com/a.png"},
                            ]
                        }
                    }
                ]
            }
            self.assertEqual(self._extract(payload), "Первый\nВторой")
        with self.subTest("одиночный текстовый блок"):
            payload = {
                "choices": [
                    {
                        "message": {
                            "content": [{"type": "text", "text": " Только текст "}]
                        }
                    }
                ]
            }
            self.assertEqual(self._extract(payload), "Только текст")

    def test_number_or_object_content_serialized_to_json(self):
        with self.subTest("число content"):
            payload = {"choices": [{"message": {"content": 42}}]}
            self.assertEqual(self._extract(payload), "42")
        with self.subTest("объект content"):
            content = {"key": "value"}
            payload = {"choices": [{"message": {"content": content}}]}
            self.assertEqual(
                self._extract(payload), json.dumps(content, ensure_ascii=False)
            )

    def test_unserializable_content_returned_as_string(self):
        with self.subTest("не сериализуемый content (set)"):
            content = {1, 2}
            payload = {"choices": [{"message": {"content": content}}]}
            self.assertEqual(self._extract(payload), str(content or "").strip())


if __name__ == "__main__":
    unittest.main()
