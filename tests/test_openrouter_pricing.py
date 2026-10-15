import json
import tempfile
import unittest
from pathlib import Path

from factory.core.openrouter_pricing import (
    estimate_openrouter_cost_usd,
    get_openrouter_context_window,
    load_openrouter_model_pricing,
)


class OpenRouterPricingTest(unittest.TestCase):
    def test_estimates_cost_from_openrouter_per_token_pricing(self):
        cost = estimate_openrouter_cost_usd(
            {
                "input_tokens": 1_000,
                "output_tokens": 200,
                "reasoning_output_tokens": 50,
                "cache_read_input_tokens": 100,
                "cache_creation_input_tokens": 20,
            },
            {
                "prompt": "0.000001",
                "completion": "0.000005",
                "input_cache_read": "0.0000001",
                "input_cache_write": "0.00000125",
            },
        )

        self.assertAlmostEqual(cost, 0.002035)

    def test_loads_model_pricing_from_cache_without_network(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache_path = Path(tmp) / "models.json"
            cache_path.write_text(
                json.dumps(
                    {
                        "fetched_at": 4_102_444_800,
                        "payload": {
                            "data": [
                                {
                                    "id": "qwen/qwen3.6-plus",
                                    "pricing": {
                                        "prompt": "0.0000004",
                                        "completion": "0.0000024",
                                    },
                                }
                            ]
                        },
                    }
                ),
                encoding="utf-8",
            )

            pricing = load_openrouter_model_pricing(
                "qwen/qwen3.6-plus",
                cache_path=cache_path,
            )

        self.assertEqual(pricing["prompt"], "0.0000004")

    def test_get_context_window_from_cache(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache_path = Path(tmp) / "models.json"
            cache_path.write_text(
                json.dumps(
                    {
                        "fetched_at": 4_102_444_800,
                        "payload": {
                            "data": [
                                {
                                    "id": "tencent/hy3-preview",
                                    "context_length": 131072,
                                },
                                {
                                    "id": "qwen/qwen3.6-plus",
                                    "context_length": 131072,
                                },
                            ]
                        },
                    }
                ),
                encoding="utf-8",
            )

            ctx = get_openrouter_context_window(
                "tencent/hy3-preview:free",
                cache_path=cache_path,
            )

        self.assertEqual(ctx, 131072)

    def test_get_context_window_returns_none_for_unknown_model(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache_path = Path(tmp) / "models.json"
            cache_path.write_text(
                json.dumps(
                    {
                        "fetched_at": 4_102_444_800,
                        "payload": {
                            "data": [
                                {"id": "other/model", "context_length": 8192},
                            ]
                        },
                    }
                ),
                encoding="utf-8",
            )

            ctx = get_openrouter_context_window(
                "nonexistent/model",
                cache_path=cache_path,
            )

        self.assertIsNone(ctx)

    def test_get_context_window_uses_top_provider_fallback(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache_path = Path(tmp) / "models.json"
            cache_path.write_text(
                json.dumps(
                    {
                        "fetched_at": 4_102_444_800,
                        "payload": {
                            "data": [
                                {
                                    "id": "test/model",
                                    "context_length": 0,
                                    "top_provider": {
                                        "context_length": 65536,
                                    },
                                },
                            ]
                        },
                    }
                ),
                encoding="utf-8",
            )

            ctx = get_openrouter_context_window(
                "test/model",
                cache_path=cache_path,
            )

        self.assertEqual(ctx, 65536)


if __name__ == "__main__":
    unittest.main()
