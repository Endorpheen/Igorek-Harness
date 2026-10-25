import unittest

from factory.core.skills_tool import _replace_content


class TestSkillTextReplacement(unittest.TestCase):
    def test_single_match_replaces_with_unicode_preserved(self):
        content = "Привет, мир! 🌍 Это тест."
        updated_text, count, error = _replace_content(
            content=content,
            old_string="мир",
            new_string="Земля",
            replace_all=False,
        )
        self.assertEqual(updated_text, "Привет, Земля! 🌍 Это тест.")
        self.assertEqual(count, 1)
        self.assertEqual(error, "")

    def test_multiple_matches_without_replace_all_keeps_text(self):
        content = "abc abc def"
        updated_text, count, error = _replace_content(
            content=content,
            old_string="abc",
            new_string="xyz",
            replace_all=False,
        )
        self.assertEqual(updated_text, "abc abc def")
        self.assertEqual(count, 2)
        self.assertIn("not unique", error)

    def test_multiple_matches_with_replace_all_replaces_all(self):
        content = "one one one"
        updated_text, count, error = _replace_content(
            content=content,
            old_string="one",
            new_string="1",
            replace_all=True,
        )
        self.assertEqual(updated_text, "1 1 1")
        self.assertEqual(count, 3)
        self.assertEqual(error, "")

    def test_missing_old_string_returns_not_found(self):
        content = "hello world"
        updated_text, count, error = _replace_content(
            content=content,
            old_string="missing",
            new_string="replacement",
            replace_all=False,
        )
        self.assertEqual(updated_text, "hello world")
        self.assertEqual(count, 0)
        self.assertIn("not found", error)

    def test_empty_new_string_removes_found_occurrence(self):
        content = "hello brave world"
        updated_text, count, error = _replace_content(
            content=content,
            old_string="hello",
            new_string="",
            replace_all=False,
        )
        self.assertEqual(updated_text, " brave world")
        self.assertEqual(count, 1)
        self.assertEqual(error, "")


if __name__ == "__main__":
    unittest.main()
