import os
import unittest
from unittest.mock import patch

from factory.core.codex_provider import (
    OPENCODE_GO_API_KEY_ENV,
    OPENCODE_GO_BASE_URL,
    normalize_codex_provider,
    resolve_codex_launch_settings,
    suggested_models_for_provider,
    user_facing_codex_provider,
)


class OpenCodeProviderTest(unittest.TestCase):
    def test_normalizes_opencode_alias(self):
        self.assertEqual(normalize_codex_provider("opencode"), "opencode-go")
        self.assertEqual(normalize_codex_provider("opencode-go"), "opencode-go")
        self.assertEqual(normalize_codex_provider("openrouter"), "openrouter")

    def test_user_facing_name(self):
        self.assertEqual(user_facing_codex_provider("opencode"), "OpenCode Go")
        self.assertEqual(user_facing_codex_provider("opencode-go"), "OpenCode Go")

    def test_suggested_models_not_empty(self):
        models = suggested_models_for_provider("opencode")
        self.assertIn("deepseek-v4-pro", models)
        self.assertIn("qwen3.6-plus", models)
        self.assertGreater(len(models), 5)

    def test_base_url_constant(self):
        self.assertEqual(OPENCODE_GO_BASE_URL, "https://opencode.ai/zen/go/v1")

    def test_api_key_env_constant(self):
        self.assertEqual(OPENCODE_GO_API_KEY_ENV, "OPENCODE_API_KEY")

    def test_launch_settings_opencode_go(self):
        settings = resolve_codex_launch_settings(
            {
                "backend": "codex",
                "provider": "opencode",
                "model": "deepseek-v4-pro",
                "env": {"OPENCODE_API_KEY": "sk-test-key"},
            }
        )
        self.assertEqual(settings["provider"], "opencode-go")
        self.assertEqual(settings["provider_display"], "OpenCode Go")
        self.assertEqual(settings["model"], "deepseek-v4-pro")
        self.assertEqual(settings["env"]["OPENCODE_API_KEY"], "sk-test-key")

        overrides = settings["config_overrides"]
        self.assertTrue(any("model_provider=\"opencode-go\"" in o for o in overrides))

    def test_launch_settings_opencode_go_default_model(self):
        settings = resolve_codex_launch_settings(
            {
                "backend": "codex",
                "provider": "opencode",
                "env": {"OPENCODE_API_KEY": "sk-test-key"},
            }
        )
        self.assertEqual(settings["model"], "gpt-5.3-codex")

    def test_launch_settings_opencode_go_api_key_fallback(self):
        with patch.dict(os.environ, {"OPENCODE_API_KEY": "sk-os-key"}):
            settings = resolve_codex_launch_settings(
                {
                    "backend": "codex",
                    "provider": "opencode",
                    "model": "deepseek-v4-pro",
                    "env": {},
                }
            )
        self.assertEqual(settings["env"]["OPENCODE_API_KEY"], "sk-os-key")

    def test_launch_settings_env_expansion(self):
        with patch.dict(os.environ, {"MY_OPENCODE_KEY": "sk-custom-key"}):
            settings = resolve_codex_launch_settings(
                {
                    "backend": "codex",
                    "provider": "opencode",
                    "model": "glm-5",
                    "env": {"OPENCODE_API_KEY": "${MY_OPENCODE_KEY}"},
                }
            )
        self.assertEqual(settings["env"]["OPENCODE_API_KEY"], "sk-custom-key")

    def test_launch_settings_rejects_non_codex_backend(self):
        with self.assertRaises(ValueError):
            resolve_codex_launch_settings(
                {"backend": "cc-backend", "provider": "opencode"}
            )

    def test_openrouter_unchanged_by_opencode_changes(self):
        settings = resolve_codex_launch_settings(
            {
                "backend": "codex",
                "provider": "openrouter",
                "model": "deepseek/deepseek-v4-pro",
                "env": {"OPENROUTER_API_KEY": "sk-or-test"},
            }
        )
        self.assertEqual(settings["provider"], "openrouter")
        self.assertEqual(settings["provider_display"], "openrouter")
        overrides = settings["config_overrides"]
        self.assertTrue(any("model_provider=\"openrouter\"" in o for o in overrides))
        self.assertFalse(any("opencode" in o for o in overrides))


if __name__ == "__main__":
    unittest.main()
