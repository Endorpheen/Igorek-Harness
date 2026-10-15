import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from factory.core import opencode_go_catalog


class OpenCodeGoCatalogTest(unittest.TestCase):
    def test_official_payload_is_cached_and_keeps_transport_metadata(self):
        payload_ids = ["deepseek-v4-pro", "qwen3.7-max", "minimax-m3", "hy3-preview"]
        with tempfile.TemporaryDirectory() as tmp:
            with (
                patch.dict(os.environ, {"FACTORY_DATA_DIR": tmp}, clear=False),
                patch.object(opencode_go_catalog, "_fetch_model_ids", return_value=payload_ids),
            ):
                models = opencode_go_catalog.list_opencode_go_models()
                cached = json.loads((Path(tmp) / "cache" / "opencode_go_models.json").read_text(encoding="utf-8"))

        self.assertEqual([item.id for item in models], payload_ids)
        self.assertEqual(models[0].transport, "openai")
        self.assertEqual(models[1].transport, "anthropic")
        self.assertEqual(models[2].transport, "anthropic")
        self.assertIsNone(models[3].transport)
        self.assertEqual([item["id"] for item in cached["data"]], payload_ids)

    def test_cached_catalog_is_used_when_fresh(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache = Path(tmp) / "cache" / "opencode_go_models.json"
            cache.parent.mkdir(parents=True)
            cache.write_text(json.dumps({"data": [{"id": "qwen3.7-max"}]}), encoding="utf-8")
            with (
                patch.dict(os.environ, {"FACTORY_DATA_DIR": tmp}, clear=False),
                patch.object(opencode_go_catalog, "_fetch_model_ids") as fetch,
            ):
                models = opencode_go_catalog.list_opencode_go_models()

        fetch.assert_not_called()
        self.assertEqual([item.id for item in models], ["qwen3.7-max"])

    def test_fallback_catalog_is_used_when_fetch_fails_without_cache(self):
        with tempfile.TemporaryDirectory() as tmp:
            with (
                patch.dict(os.environ, {"FACTORY_DATA_DIR": tmp}, clear=False),
                patch.object(opencode_go_catalog, "_fetch_model_ids", return_value=[]),
            ):
                models = opencode_go_catalog.list_opencode_go_models()

        self.assertIn("qwen3.7-max", [item.id for item in models])
        self.assertIn("minimax-m3", [item.id for item in models])

    def test_minimax_m3_uses_anthropic_transport(self):
        with patch.object(
            opencode_go_catalog,
            "list_opencode_go_models",
            return_value=[opencode_go_catalog.OpenCodeGoModel("minimax-m3", "anthropic")],
        ):
            self.assertEqual(opencode_go_catalog.opencode_go_model_transport("minimax-m3"), "anthropic")

    def test_recommendation_filters_transport_or_accepts_dual_transport_runtime(self):
        models = [
            opencode_go_catalog.OpenCodeGoModel("qwen3.7-max", "anthropic"),
            opencode_go_catalog.OpenCodeGoModel("deepseek-v4-pro", "openai"),
        ]
        with patch.object(opencode_go_catalog, "list_opencode_go_models", return_value=models):
            self.assertEqual(opencode_go_catalog.recommended_opencode_go_model("openai"), "deepseek-v4-pro")
            self.assertEqual(opencode_go_catalog.recommended_opencode_go_model("anthropic"), "qwen3.7-max")
            self.assertEqual(opencode_go_catalog.recommended_opencode_go_model("auto"), "deepseek-v4-pro")


if __name__ == "__main__":
    unittest.main()
