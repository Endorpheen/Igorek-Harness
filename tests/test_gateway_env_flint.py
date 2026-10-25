import tempfile
import unittest
from pathlib import Path

from factory.core.cli_run import _load_env_file


class TestGatewayEnvFixture(unittest.TestCase):
    def test_missing_file_returns_empty_dict(self):
        """Missing env file should result in an empty dict."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            missing_path = Path(tmp_dir) / "missing.env"
            self.assertEqual(_load_env_file(missing_path), {})

    def test_empty_comments_and_lines_without_equals_are_ignored(self):
        """Empty lines, comments and lines without '=' should be skipped."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            env_path = Path(tmp_dir) / ".env"
            env_path.write_text(
                "# comment\n"
                "   # indented comment\n"
                "\n"
                "no_equals_here\n"
                "API_KEY = my_secret_key\n",
                encoding="utf-8",
            )
            self.assertEqual(_load_env_file(env_path), {"API_KEY": "my_secret_key"})

    def test_strips_whitespace_around_key_and_value(self):
        """Whitespace around key and value should be stripped."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            env_path = Path(tmp_dir) / ".env"
            env_path.write_text("  SPACED_KEY   =    some_value   \n", encoding="utf-8")
            self.assertEqual(_load_env_file(env_path), {"SPACED_KEY": "some_value"})

    def test_value_keeps_extra_equals_and_hash_fragment(self):
        """Value should keep extra '=' and URL fragment with '#'."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            env_path = Path(tmp_dir) / ".env"
            env_path.write_text(
                "DATABASE_URL = postgresql://user:pass@localhost:5432/db?sslmode=require#frag\n",
                encoding="utf-8",
            )
            self.assertEqual(
                _load_env_file(env_path),
                {
                    "DATABASE_URL": (
                        "postgresql://user:pass@localhost:5432/db?sslmode=require#frag"
                    )
                },
            )

    def test_repeated_key_takes_last_value_and_unicode_is_preserved(self):
        """Repeated keys should take the last value; Unicode should be kept."""
        with tempfile.TemporaryDirectory() as tmp_dir:
            env_path = Path(tmp_dir) / ".env"
            env_path.write_text(
                "NAME = first\nNAME = Иван\nGREETING = Привет, мир!\n",
                encoding="utf-8",
            )
            self.assertEqual(
                _load_env_file(env_path),
                {"NAME": "Иван", "GREETING": "Привет, мир!"},
            )


if __name__ == "__main__":
    unittest.main()
