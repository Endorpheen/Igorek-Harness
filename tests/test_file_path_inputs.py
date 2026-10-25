import unittest

from factory.api_server_core.file_policy import MAX_LOCAL_PATH_CANDIDATE_LENGTH, sanitize_local_path_candidate


class TestSanitizeLocalPathCandidate(unittest.TestCase):
    """Contract tests for the local file path sanitizer."""

    def test_normal_absolute_path_and_quoted_path_kept(self):
        with self.subTest("absolute path is kept"):
            self.assertEqual(sanitize_local_path_candidate("/tmp/notes/a.txt"), "/tmp/notes/a.txt")
        with self.subTest("single quoted path is kept"):
            self.assertEqual(sanitize_local_path_candidate("'/tmp/notes/a.txt'"), "/tmp/notes/a.txt")
        with self.subTest("double quoted path is kept"):
            self.assertEqual(sanitize_local_path_candidate('"/tmp/notes/a.txt"'), "/tmp/notes/a.txt")

    def test_gt_prefix_stripped_and_unicode_kept(self):
        with self.subTest("greater-than prefix is stripped"):
            self.assertEqual(sanitize_local_path_candidate("> ./notes/мой файл.md"), "./notes/мой файл.md")

    def test_control_characters_rejected(self):
        with self.subTest("newline is rejected"):
            self.assertIsNone(sanitize_local_path_candidate("/tmp/notes/a\nb.txt"))
        with self.subTest("carriage return is rejected"):
            self.assertIsNone(sanitize_local_path_candidate("/tmp/notes/a\rb.txt"))
        with self.subTest("tab is rejected"):
            self.assertIsNone(sanitize_local_path_candidate("/tmp/notes/a\tb.txt"))
        with self.subTest("nul is rejected"):
            self.assertIsNone(sanitize_local_path_candidate("/tmp/notes/a\x00b.txt"))

    def test_shell_metacharacters_rejected(self):
        shell_metachars = ("|", "&", ";", "$", "`", "{", "}", "(", ")")
        for metachar in shell_metachars:
            with self.subTest(f"shell metachar {metachar!r} is rejected"):
                self.assertIsNone(sanitize_local_path_candidate(f"/tmp/notes/a{metachar}b.txt"))

    def test_path_length_limit(self):
        base = "/tmp/"
        accepted = base + "a" * (MAX_LOCAL_PATH_CANDIDATE_LENGTH - len(base))
        self.assertEqual(len(accepted), MAX_LOCAL_PATH_CANDIDATE_LENGTH)
        self.assertEqual(sanitize_local_path_candidate(accepted), accepted)

        rejected = base + "a" * (MAX_LOCAL_PATH_CANDIDATE_LENGTH - len(base) + 1)
        self.assertEqual(len(rejected), MAX_LOCAL_PATH_CANDIDATE_LENGTH + 1)
        self.assertIsNone(sanitize_local_path_candidate(rejected))

    def test_urls_and_windows_like_paths(self):
        self.assertIsNone(sanitize_local_path_candidate("https://example.com/notes/a.txt"))
        self.assertIsNone(sanitize_local_path_candidate("foo:bar"))
        self.assertEqual(sanitize_local_path_candidate("C:/notes/a.txt"), "C:/notes/a.txt")
        self.assertEqual(sanitize_local_path_candidate("C:\\notes\\a.txt"), "C:\\notes\\a.txt")

    def test_invalid_input_types_and_empty_strings_rejected(self):
        self.assertIsNone(sanitize_local_path_candidate(None))
        self.assertIsNone(sanitize_local_path_candidate(123))
        self.assertIsNone(sanitize_local_path_candidate({}))
        self.assertIsNone(sanitize_local_path_candidate(""))
        self.assertIsNone(sanitize_local_path_candidate("   "))
        self.assertIsNone(sanitize_local_path_candidate(">"))

    def test_relative_path_with_special_chars_kept(self):
        self.assertEqual(sanitize_local_path_candidate("./project-v2/notes_a.b.txt"), "./project-v2/notes_a.b.txt")
        self.assertEqual(sanitize_local_path_candidate("./my notes/task.md"), "./my notes/task.md")


if __name__ == "__main__":
    unittest.main()
