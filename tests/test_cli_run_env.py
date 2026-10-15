import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from factory.core import cli_run


class CliRunEnvBootstrapTest(unittest.TestCase):
    def test_local_stack_bootstrap_loads_package_env_local_from_factory_cwd(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            factory_dir = root / "factory"
            factory_dir.mkdir()
            (factory_dir / ".env.local").write_text(
                "\n".join(
                    [
                        "FACTORY_TELEGRAM_BOT_TOKEN=telegram-token-from-file",
                        "FACTORY_TELEGRAM_ALLOWED_USER_IDS=100,200",
                    ]
                ),
                encoding="utf-8",
            )
            previous_cwd = Path.cwd()
            os.chdir(factory_dir)
            self.addCleanup(os.chdir, previous_cwd)

            with patch.object(cli_run, "_resolve_builtin_gateway_preset_path", return_value=factory_dir / "missing.env"):
                with patch.object(cli_run, "_materialize_gateway_vault_env", return_value={}):
                    with patch.dict(os.environ, {}, clear=True):
                        cli_run._bootstrap_stack_env()

                        self.assertEqual(os.environ["FACTORY_TELEGRAM_BOT_TOKEN"], "telegram-token-from-file")
                        self.assertEqual(os.environ["FACTORY_TELEGRAM_ALLOWED_USER_IDS"], "100,200")
                        self.assertEqual(os.environ["FACTORY_STACK_ENV_BOOTSTRAPPED"], "true")

    def test_bootstrap_sentinel_prevents_duplicate_vault_materialization(self):
        with patch.object(cli_run, "_materialize_gateway_vault_env") as materialize:
            with patch.dict(os.environ, {"FACTORY_STACK_ENV_BOOTSTRAPPED": "true"}, clear=True):
                cli_run._bootstrap_stack_env()

        materialize.assert_not_called()

    def test_telegram_bridge_can_start_with_token_without_user_allowlist(self):
        with patch.dict(
            os.environ,
            {
                "FACTORY_TELEGRAM_ENABLED": "true",
                "FACTORY_TELEGRAM_BOT_TOKEN": "token-from-vault",
                "FACTORY_TELEGRAM_ALLOWED_USER_IDS": "",
                "FACTORY_TELEGRAM_ALLOWED_CHAT_IDS": "",
            },
            clear=True,
        ):
            should_start, reason = cli_run._telegram_bridge_start_reason()

        self.assertTrue(should_start)
        self.assertEqual(reason, "ready")

    def test_telegram_bridge_does_not_start_without_token(self):
        with patch.dict(os.environ, {"FACTORY_TELEGRAM_ENABLED": "true"}, clear=True):
            should_start, reason = cli_run._telegram_bridge_start_reason()

        self.assertFalse(should_start)
        self.assertEqual(reason, "missing FACTORY_TELEGRAM_BOT_TOKEN")

    def test_telegram_bridge_profile_cli_flag_beats_preset_env(self):
        # Regression: stale FACTORY_TELEGRAM_PROFILE=cc-glm from env files made
        # `run --profile cc-opencode-go` start the bridge on cc-glm while the
        # worker claimed only cc-opencode-go tasks (stuck queued tasks).
        with patch.dict(os.environ, {"FACTORY_TELEGRAM_PROFILE": "cc-glm"}, clear=True):
            resolved = cli_run._resolve_telegram_bridge_profile("cc-opencode-go")
            self.assertEqual(resolved, "cc-opencode-go")
            self.assertEqual(os.environ["FACTORY_TELEGRAM_PROFILE"], "cc-opencode-go")


if __name__ == "__main__":
    unittest.main()
