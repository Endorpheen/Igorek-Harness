import json
import tempfile
import unittest
from pathlib import Path

from factory.core.openrouter_model_selector import (
    select_free_openrouter_models,
    update_openrouter_profile_model,
)


class OpenRouterModelSelectorTest(unittest.TestCase):
    def test_selects_recent_free_tool_model_before_older_coding_hint(self):
        reference_ts = 1_778_439_000
        payload = {
            "data": [
                {
                    "id": "old/coder:free",
                    "name": "Old Coder",
                    "created": reference_ts - 40 * 24 * 60 * 60,
                    "context_length": 262144,
                    "pricing": {"prompt": "0", "completion": "0"},
                    "supported_parameters": ["tools", "max_tokens"],
                    "architecture": {"output_modalities": ["text"]},
                    "top_provider": {"max_completion_tokens": 65536},
                },
                {
                    "id": "fresh/chat:free",
                    "name": "Fresh Chat",
                    "created": reference_ts - 2 * 24 * 60 * 60,
                    "context_length": 262144,
                    "pricing": {"prompt": "0", "completion": "0"},
                    "supported_parameters": ["tools", "max_tokens"],
                    "architecture": {"output_modalities": ["text"]},
                    "top_provider": {"max_completion_tokens": 65536},
                },
                {
                    "id": "fresh/coder:free",
                    "name": "Fresh Coder",
                    "created": reference_ts - 3 * 24 * 60 * 60,
                    "context_length": 131072,
                    "pricing": {"prompt": "0", "completion": "0"},
                    "supported_parameters": ["tools", "max_tokens"],
                    "architecture": {"output_modalities": ["text"]},
                    "top_provider": {"max_completion_tokens": 65536},
                },
                {
                    "id": "fresh/coder-paid",
                    "name": "Fresh Coder Paid",
                    "created": reference_ts - 1 * 24 * 60 * 60,
                    "context_length": 262144,
                    "pricing": {"prompt": "0.000001", "completion": "0"},
                    "supported_parameters": ["tools", "max_tokens"],
                    "architecture": {"output_modalities": ["text"]},
                    "top_provider": {"max_completion_tokens": 65536},
                },
            ]
        }

        candidates = select_free_openrouter_models(
            payload,
            max_age_days=31,
            reference_ts=reference_ts,
        )

        self.assertEqual(candidates[0].model_id, "fresh/chat:free")
        self.assertNotIn("old/coder:free", [c.model_id for c in candidates])
        self.assertNotIn("fresh/coder-paid", [c.model_id for c in candidates])

    def test_update_profile_writes_backup_and_selected_model_from_cache(self):
        reference_ts = 1_778_439_000
        payload = {
            "data": [
                {
                    "id": "fresh/coder:free",
                    "name": "Fresh Coder",
                    "created": reference_ts,
                    "context_length": 131072,
                    "pricing": {"prompt": "0", "completion": "0"},
                    "supported_parameters": ["tools", "max_tokens"],
                    "architecture": {"output_modalities": ["text"]},
                    "top_provider": {"max_completion_tokens": 65536},
                }
            ]
        }
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            profiles_dir = root / "profiles"
            profiles_dir.mkdir()
            profile_path = profiles_dir / "cc-openrouter.json"
            profile_path.write_text(
                json.dumps(
                    {
                        "backend": "cc-backend",
                        "provider": "openrouter",
                        "model": "gone/model:free",
                    }
                ),
                encoding="utf-8",
            )
            cache_path = root / "openrouter_models.json"
            cache_path.write_text(
                json.dumps({"fetched_at": reference_ts, "payload": payload}),
                encoding="utf-8",
            )

            from factory.core import openrouter_model_selector

            old_cache_path = openrouter_model_selector._pricing_cache_path
            old_time = openrouter_model_selector.time.time
            openrouter_model_selector._pricing_cache_path = lambda: cache_path
            openrouter_model_selector.time.time = lambda: reference_ts
            try:
                result = update_openrouter_profile_model(
                    profiles_dir=profiles_dir,
                    refresh=False,
                )
            finally:
                openrouter_model_selector._pricing_cache_path = old_cache_path
                openrouter_model_selector.time.time = old_time

            updated = json.loads(profile_path.read_text(encoding="utf-8"))
            backups = list(profiles_dir.glob("cc-openrouter.json.*.bak"))

        self.assertEqual(result["status"], "ok")
        self.assertTrue(result["changed"])
        self.assertEqual(updated["model"], "fresh/coder:free")
        self.assertEqual(len(backups), 1)

    def test_exclude_model_skips_top_candidate(self):
        reference_ts = 1_778_439_000
        payload = {
            "data": [
                {
                    "id": "fresh/top:free",
                    "canonical_slug": "fresh/top",
                    "name": "Fresh Top",
                    "created": reference_ts,
                    "context_length": 262144,
                    "pricing": {"prompt": "0", "completion": "0"},
                    "supported_parameters": ["tools", "max_tokens"],
                    "architecture": {"output_modalities": ["text"]},
                    "top_provider": {"max_completion_tokens": 65536},
                },
                {
                    "id": "fresh/next:free",
                    "canonical_slug": "fresh/next",
                    "name": "Fresh Next",
                    "created": reference_ts - 1,
                    "context_length": 131072,
                    "pricing": {"prompt": "0", "completion": "0"},
                    "supported_parameters": ["tools", "max_tokens"],
                    "architecture": {"output_modalities": ["text"]},
                    "top_provider": {"max_completion_tokens": 65536},
                },
            ]
        }

        candidates = select_free_openrouter_models(
            payload,
            max_age_days=31,
            reference_ts=reference_ts,
            exclude_models={"fresh/top:free"},
        )

        self.assertEqual(candidates[0].model_id, "fresh/next:free")


if __name__ == "__main__":
    unittest.main()
