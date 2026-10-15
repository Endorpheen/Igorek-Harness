import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from factory.core import cli_run
from factory.core.runtime import get_env_files_dir


class EnvFilesDirTest(unittest.TestCase):
    def test_default_env_dir_is_outside_repo(self):
        # Secrets must never live inside a repository working tree: the
        # default location is the data home, not the package directory.
        self.assertNotIn(str(Path(__file__).resolve().parents[1]), str(get_env_files_dir()))

    def test_env_dir_override(self):
        with tempfile.TemporaryDirectory() as tmp:
            with mock.patch.dict(os.environ, {"FACTORY_ENV_FILES_DIR": tmp}):
                self.assertEqual(get_env_files_dir(), Path(tmp))

    def test_resolver_picks_up_data_home_env_files(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as cwd:
            env_dir = Path(tmp)
            (env_dir / ".env.gateway").write_text("FACTORY_TEST_MARKER=1\n", encoding="utf-8")
            previous = os.getcwd()
            os.chdir(cwd)
            try:
                with mock.patch.dict(os.environ, {"FACTORY_ENV_FILES_DIR": tmp}):
                    resolved = cli_run._resolve_gateway_env_files()
            finally:
                os.chdir(previous)
            self.assertIn(env_dir / ".env.gateway", resolved)

    def test_explicit_env_file_wins(self):
        with tempfile.TemporaryDirectory() as tmp:
            explicit = Path(tmp) / "custom.env"
            resolved = cli_run._resolve_gateway_env_files(str(explicit))
            self.assertEqual(resolved, [explicit])


if __name__ == "__main__":
    unittest.main()
