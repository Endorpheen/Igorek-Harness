import unittest

from factory.core import cc_backend_executor
from factory.core.cc_backend_executor import (
    _extract_context_window_from_payload,
    _resolve_backend_context_window,
    _is_prompt_too_long_error,
)


class CcBackendContextWindowTest(unittest.TestCase):
    def test_context_error_formats_preserve_cloud_and_local_recovery(self):
        for error in (
            'HTTP 400: {"error":{"type":"exceed_context_size_error"}}',
            'request (35512 tokens) exceeds the available context size (32768 tokens)',
            'prompt too long', 'maximum context length exceeded',
        ):
            with self.subTest(error=error):
                self.assertTrue(_is_prompt_too_long_error(error))
        for error in ('HTTP 401: unauthorized', 'HTTP 429: rate limit', 'connection refused', ''):
            with self.subTest(error=error):
                self.assertFalse(_is_prompt_too_long_error(error))

    def test_extracts_llama_props_n_ctx(self):
        payload = {"default_generation_settings": {"n_ctx": 65536}}

        self.assertEqual(65536, _extract_context_window_from_payload(payload))

    def test_extracts_matching_model_context_length(self):
        payload = {
            "data": [
                {"id": "other", "context_length": 8192},
                {"id": "local-model", "context_length": 32768},
            ]
        }

        self.assertEqual(
            32768,
            _extract_context_window_from_payload(payload, model="local-model"),
        )

    def test_resolver_does_not_duplicate_v1_for_openai_base_url(self):
        old_cache = cc_backend_executor._context_window_from_models_cache
        old_get = cc_backend_executor._http_get_curl
        urls = []

        def fake_get(url, timeout_s):
            self.assertEqual(timeout_s, 2)
            urls.append(url)
            if url.endswith("/v1/models"):
                return {
                    "data": [
                        {
                            "id": "ornith-1.5-35b",
                            "meta": {"n_ctx": 65536},
                        }
                    ]
                }, 1
            return {"error": "not found"}, 1

        cc_backend_executor._context_window_from_models_cache = lambda _model: None
        cc_backend_executor._http_get_curl = fake_get
        try:
            result = _resolve_backend_context_window(
                base_url="http://127.0.0.1:8081/v1",
                model="ornith-1.5-35b",
                fallback=32000,
                use_curl=True,
            )
        finally:
            cc_backend_executor._context_window_from_models_cache = old_cache
            cc_backend_executor._http_get_curl = old_get

        self.assertEqual(result, (65536, "/v1/models"))
        self.assertEqual(
            urls,
            [
                "http://127.0.0.1:8081/props",
                "http://127.0.0.1:8081/v1/models",
            ],
        )

    def test_resolver_keeps_bare_base_url_compatibility(self):
        old_cache = cc_backend_executor._context_window_from_models_cache
        old_get = cc_backend_executor._http_get_curl
        urls = []

        def fake_get(url, timeout_s):
            urls.append(url)
            if url.endswith("/v1/models"):
                return {"data": [{"id": "local", "n_ctx": 32768}]}, 1
            return {"error": "not found"}, 1

        cc_backend_executor._context_window_from_models_cache = lambda _model: None
        cc_backend_executor._http_get_curl = fake_get
        try:
            result = _resolve_backend_context_window(
                base_url="http://127.0.0.1:8081",
                model="local",
                fallback=32000,
                use_curl=True,
            )
        finally:
            cc_backend_executor._context_window_from_models_cache = old_cache
            cc_backend_executor._http_get_curl = old_get

        self.assertEqual(result, (32768, "/v1/models"))
        self.assertEqual(
            urls,
            [
                "http://127.0.0.1:8081/props",
                "http://127.0.0.1:8081/v1/models",
            ],
        )


class GuestModeCatalogTest(unittest.TestCase):
    def test_guest_mode_skips_the_external_models_catalog(self):
        import os
        from unittest.mock import patch

        from factory.core import opencode_models, openrouter_pricing

        with patch.dict(os.environ, {"IGOREK_ISOLATION_GUEST": "1"}, clear=False):
            self.assertIsNone(opencode_models._fetch_models())
            self.assertIsNone(openrouter_pricing._fetch_models(1.0))


class HostResolvedContextWindowTest(unittest.TestCase):
    def test_resolve_host_context_window_returns_the_resolved_value(self):
        old = cc_backend_executor._resolve_backend_context_window

        def fake_resolve(*, base_url, model, fallback, use_curl=False):
            return 128_000, "models_cache"

        cc_backend_executor._resolve_backend_context_window = fake_resolve
        try:
            value = cc_backend_executor.resolve_host_context_window(
                "https://example.test/v1", "glm-5.3-flash", api_format="openai"
            )
        finally:
            cc_backend_executor._resolve_backend_context_window = old
        self.assertEqual(128_000, value)

    def test_resolve_host_context_window_uses_the_emergency_fallback(self):
        old = cc_backend_executor._resolve_backend_context_window

        def fake_resolve(*, base_url, model, fallback, use_curl=False):
            return fallback, "metadata_or_default"

        cc_backend_executor._resolve_backend_context_window = fake_resolve
        try:
            value = cc_backend_executor.resolve_host_context_window(
                "https://example.test/v1", "unknown-model"
            )
        finally:
            cc_backend_executor._resolve_backend_context_window = old
        self.assertEqual(cc_backend_executor.DEFAULT_CONTEXT_WINDOW_TOKENS, value)


if __name__ == "__main__":
    unittest.main()
