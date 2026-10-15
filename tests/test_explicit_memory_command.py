import tempfile
import unittest

from factory.core.agent_foundation import extract_explicit_memory_write_request
from factory.core.builtin_memory_provider import BuiltinMemoryProvider


class ExplicitMemoryCommandTest(unittest.TestCase):
    def test_natural_language_remember_request_is_not_a_runtime_command(self):
        self.assertIsNone(
            extract_explicit_memory_write_request(
                "Запомни полезный факт о проекте: локальные skills живут вне Git."
            )
        )

    def test_only_slash_remember_is_parsed_as_explicit_memory_command(self):
        self.assertEqual(
            extract_explicit_memory_write_request(
                "/remember локальные skills живут вне Git."
            ),
            ("long", "локальные skills живут вне Git"),
        )

    def test_slash_remember_persists_without_model_turn(self):
        with tempfile.TemporaryDirectory() as data_dir:
            handled, assistant_text, writes = BuiltinMemoryProvider().handle_explicit_user_memory(
                "/remember локальные skills живут вне Git.",
                data_dir=data_dir,
            )

        self.assertTrue(handled)
        self.assertIn("Запомнил и записал это в память (long)", assistant_text or "")
        self.assertEqual(len(writes), 1)
        self.assertTrue(writes[0]["saved"])

    def test_empty_slash_remember_returns_usage_without_model_turn(self):
        handled, assistant_text, writes = BuiltinMemoryProvider().handle_explicit_user_memory(
            "/remember"
        )

        self.assertTrue(handled)
        self.assertEqual(assistant_text, "Использование: /remember <факт>.")
        self.assertEqual(writes, [])


if __name__ == "__main__":
    unittest.main()
