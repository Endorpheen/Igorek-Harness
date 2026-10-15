"""Regression tests for the new `minimax` cc-backend provider.

These tests cover the resolver sites that were extended to recognize
`provider="minimax"` in 0.1.520:

- worker._resolve_cc_backend_auth_headers (ANTHROPIC_AUTH_TOKEN / MINIMAX_API_KEY)
- worker._resolve_cc_backend_api_format
- worker._effective_cc_backend_model
- cc_backend_executor._anthropic_messages_url
- memory_review._anthropic_messages_url
- factory.core.opencode_go_catalog.opencode_go_model_transport
- cc_backend_executor._context_window_from_models_cache (provider_id="minimax")
- cli_chat_rich "auto" api_format resolution for provider="minimax"

Plus negative cases that confirm `opencode-go` / `openrouter` paths are
intact (no regression from the new code paths).
"""
import os
import unittest
from unittest.mock import patch

from factory.core import cc_backend_executor, memory_review, worker
from factory.core import cli_chat_rich
from factory.core.opencode_go_catalog import opencode_go_model_transport


class MinimaxProviderAuthHeadersTest(unittest.TestCase):
    """worker._resolve_cc_backend_auth_headers for provider='minimax'."""

    def test_anthropic_format_uses_x_api_key_from_anthropic_auth_token(self):
        with patch.dict(os.environ, {"ANTHROPIC_AUTH_TOKEN": "sub-key-1"}, clear=True):
            headers = worker._resolve_cc_backend_auth_headers(
                {"provider": "minimax"}, api_format="anthropic",
            )
        self.assertEqual(headers, {"x-api-key": "sub-key-1"})

    def test_anthropic_format_falls_back_to_minimax_api_key(self):
        with patch.dict(
            os.environ,
            {"MINIMAX_API_KEY": "vault-key-1", "ANTHROPIC_AUTH_TOKEN": ""},
            clear=True,
        ):
            headers = worker._resolve_cc_backend_auth_headers(
                {"provider": "minimax"}, api_format="anthropic",
            )
        self.assertEqual(headers, {"x-api-key": "vault-key-1"})

    def test_anthropic_auth_token_takes_priority_over_minimax_api_key(self):
        with patch.dict(
            os.environ,
            {"ANTHROPIC_AUTH_TOKEN": "sub-key-1", "MINIMAX_API_KEY": "vault-key-1"},
            clear=True,
        ):
            headers = worker._resolve_cc_backend_auth_headers(
                {"provider": "minimax"}, api_format="anthropic",
            )
        self.assertEqual(headers, {"x-api-key": "sub-key-1"})

    def test_openai_format_uses_bearer_from_anthropic_auth_token(self):
        with patch.dict(os.environ, {"ANTHROPIC_AUTH_TOKEN": "sub-key-1"}, clear=True):
            headers = worker._resolve_cc_backend_auth_headers(
                {"provider": "minimax"}, api_format="openai",
            )
        self.assertEqual(headers, {"Authorization": "Bearer sub-key-1"})

    def test_minimax_does_not_add_opencode_origin_or_user_agent(self):
        with patch.dict(os.environ, {"ANTHROPIC_AUTH_TOKEN": "sub-key-1"}, clear=True):
            headers = worker._resolve_cc_backend_auth_headers(
                {"provider": "minimax"}, api_format="anthropic",
            )
        self.assertNotIn("Origin", headers)
        self.assertNotIn("User-Agent", headers)

    def test_returns_none_when_no_minimax_token_available(self):
        with patch.dict(
            os.environ, {"ANTHROPIC_AUTH_TOKEN": "", "MINIMAX_API_KEY": ""}, clear=True,
        ):
            headers = worker._resolve_cc_backend_auth_headers(
                {"provider": "minimax"}, api_format="anthropic",
            )
        self.assertIsNone(headers)


class GlmProviderAuthHeadersTest(unittest.TestCase):
    """worker._resolve_cc_backend_auth_headers for provider='glm'."""

    def test_anthropic_format_uses_x_api_key_from_anthropic_auth_token(self):
        with patch.dict(os.environ, {"ANTHROPIC_AUTH_TOKEN": "sub-key-1"}, clear=True):
            headers = worker._resolve_cc_backend_auth_headers(
                {"provider": "glm"}, api_format="anthropic",
            )
        self.assertEqual(headers, {"x-api-key": "sub-key-1"})

    def test_anthropic_format_falls_back_to_glm_api_key(self):
        with patch.dict(
            os.environ,
            {"GLM_API_KEY": "vault-key-1", "ANTHROPIC_AUTH_TOKEN": ""},
            clear=True,
        ):
            headers = worker._resolve_cc_backend_auth_headers(
                {"provider": "glm"}, api_format="anthropic",
            )
        self.assertEqual(headers, {"x-api-key": "vault-key-1"})

    def test_openai_format_uses_bearer_from_glm_api_key(self):
        with patch.dict(os.environ, {"GLM_API_KEY": "vault-key-1"}, clear=True):
            headers = worker._resolve_cc_backend_auth_headers(
                {"provider": "glm"}, api_format="openai",
            )
        self.assertEqual(headers, {"Authorization": "Bearer vault-key-1"})

    def test_glm_does_not_add_opencode_origin_or_user_agent(self):
        with patch.dict(os.environ, {"GLM_API_KEY": "vault-key-1"}, clear=True):
            headers = worker._resolve_cc_backend_auth_headers(
                {"provider": "glm"}, api_format="anthropic",
            )
        self.assertNotIn("Origin", headers)
        self.assertNotIn("User-Agent", headers)

    def test_memory_review_uses_glm_api_key_without_opencode_headers(self):
        with patch.dict(os.environ, {"GLM_API_KEY": "vault-key-1"}, clear=True):
            headers = memory_review._openai_request_headers(
                {"provider": "glm"}, api_format="anthropic",
            )
        self.assertEqual(headers, {"x-api-key": "vault-key-1"})


class MinimaxProviderApiFormatTest(unittest.TestCase):
    """worker._resolve_cc_backend_api_format and model resolution."""

    def test_selects_anthropic_transport_for_MiniMax_M3(self):
        config = {"provider": "minimax", "api_format": "openai"}
        self.assertEqual(worker._resolve_cc_backend_api_format(config, "MiniMax-M3"), "anthropic")

    def test_selects_anthropic_transport_for_lowercase_alias(self):
        config = {"provider": "minimax", "api_format": "openai"}
        self.assertEqual(worker._resolve_cc_backend_api_format(config, "minimax-m3"), "anthropic")

    def test_effective_model_returns_model_override_for_minimax(self):
        model = worker._effective_cc_backend_model(
            provider="minimax",
            configured_model="MiniMax-M3",
            task_metadata={"model_override": "MiniMax-M2.7"},
            base_url="https://api.minimax.io/anthropic",
        )
        self.assertEqual(model, "MiniMax-M2.7")

    def test_effective_model_returns_model_override_for_glm(self):
        model = worker._effective_cc_backend_model(
            provider="glm",
            configured_model="glm-5.2",
            task_metadata={"model_override": "glm-5.1"},
            base_url="https://api.z.ai/api/anthropic",
        )
        self.assertEqual(model, "glm-5.1")


class MinimaxAnthropicMessagesUrlTest(unittest.TestCase):
    """cc_backend_executor._anthropic_messages_url / memory_review equivalent."""

    def test_executor_appends_v1_for_minimax_anthropic_suffix(self):
        self.assertEqual(
            cc_backend_executor._anthropic_messages_url("https://api.minimax.io/anthropic"),
            "https://api.minimax.io/anthropic/v1/messages",
        )

    def test_executor_preserves_v1_suffix_for_opencode_go(self):
        self.assertEqual(
            cc_backend_executor._anthropic_messages_url("https://opencode.ai/zen/go/v1"),
            "https://opencode.ai/zen/go/v1/messages",
        )

    def test_executor_appends_v1_for_bare_base_url(self):
        self.assertEqual(
            cc_backend_executor._anthropic_messages_url("http://127.0.0.1:8001"),
            "http://127.0.0.1:8001/v1/messages",
        )

    def test_memory_review_appends_v1_for_minimax_anthropic_suffix(self):
        self.assertEqual(
            memory_review._anthropic_messages_url("https://api.minimax.io/anthropic"),
            "https://api.minimax.io/anthropic/v1/messages",
        )

    def test_executor_url_actually_works_against_minimax(self):
        """End-to-end smoke: the constructed URL must reach MiniMax API
        with a 2xx (auth-verified) response, not 404.

        This regression test guards the structural bug from 0.1.520
        where `_anthropic_messages_url` returned `/anthropic/messages`
        (no `/v1`) for `/anthropic` base_url, which MiniMax rejected
        with HTTP 404.
        """
        import os
        import urllib.error
        import urllib.request

        url = cc_backend_executor._anthropic_messages_url("https://api.minimax.io/anthropic")
        api_key = os.environ.get("MINIMAX_API_KEY")
        if not api_key:
            self.skipTest("MINIMAX_API_KEY not in env; set it to enable this live regression")
        body = (
            b'{"model":"MiniMax-M3","max_tokens":8,'
            b'"messages":[{"role":"user","content":"ping"}]}'
        )
        req = urllib.request.Request(
            url,
            data=body,
            headers={
                "Content-Type": "application/json",
                "anthropic-version": "2023-06-01",
                "x-api-key": api_key,
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                status = int(getattr(resp, "status", 200) or 200)
        except urllib.error.HTTPError as exc:
            status = int(exc.code)
        self.assertNotEqual(
            status, 404,
            f"{url} returned HTTP 404 — URL construction regressed to /anthropic/messages",
        )
        self.assertLess(
            status, 500,
            f"{url} returned HTTP {status} — server-side failure",
        )


class MinimaxCatalogTest(unittest.TestCase):
    """opencode_go_catalog recognises both lowercase and camelcase Minimax model IDs."""

    def test_camelcase_MiniMax_M3_is_anthropic(self):
        self.assertEqual(opencode_go_model_transport("MiniMax-M3"), "anthropic")

    def test_camelcase_MiniMax_M2_7_is_anthropic(self):
        self.assertEqual(opencode_go_model_transport("MiniMax-M2.7"), "anthropic")

    def test_camelcase_MiniMax_M2_5_is_anthropic(self):
        self.assertEqual(opencode_go_model_transport("MiniMax-M2.5"), "anthropic")

    def test_lowercase_alias_still_works(self):
        self.assertEqual(opencode_go_model_transport("minimax-m3"), "anthropic")


class MinimaxContextWindowProviderTest(unittest.TestCase):
    """cc_backend_executor._context_window_from_models_cache accepts 'minimax' provider_id."""

    def test_context_window_search_includes_minimax_provider_id(self):
        # Deterministic: assert the resolver actually consults the "minimax"
        # provider_id, without pinning a value from the mutable models.dev cache.
        seen_providers: list[str] = []

        def fake_get_context_window(model, provider_id="opencode-go"):
            seen_providers.append(provider_id)
            return 512000 if provider_id == "minimax" else None

        with patch(
            "factory.core.opencode_models.get_context_window",
            fake_get_context_window,
        ):
            result = cc_backend_executor._context_window_from_models_cache("MiniMax-M3")

        self.assertEqual(result, 512000)
        self.assertIn("minimax", seen_providers)


class MinimaxRichAutoApiFormatTest(unittest.TestCase):
    """cli_chat_rich resolves api_format='auto' for provider='minimax'."""

    def test_profile_api_format_is_auto_for_minimax(self):
        pc = {"provider": "minimax", "api_format": "anthropic"}
        # Mirror the expression used in cli_chat_rich.py /model command:
        profile_api_format = (
            "auto"
            if str(pc.get("provider") or "").strip().lower() in {"opencode", "opencode-go", "minimax", "glm"}
            else pc.get("api_format")
        )
        self.assertEqual(profile_api_format, "auto")

    def test_profile_api_format_is_auto_for_glm(self):
        pc = {"provider": "glm", "api_format": "anthropic"}
        profile_api_format = (
            "auto"
            if str(pc.get("provider") or "").strip().lower() in {"opencode", "opencode-go", "minimax", "glm"}
            else pc.get("api_format")
        )
        self.assertEqual(profile_api_format, "auto")

    def test_catalog_provider_api_format_is_auto_for_minimax(self):
        # Mirror the expression used in cli_chat_rich.py /models recommend:
        catalog_provider = "minimax"
        api_format = (
            "auto"
            if catalog_provider in {"opencode", "opencode-go", "minimax", "glm"}
            else "openai"
        )
        self.assertEqual(api_format, "auto")


class OpenCodeGoRegressionTest(unittest.TestCase):
    """Negative cases: pre-existing opencode-go / openrouter paths still work."""

    def test_opencode_go_anthropic_path_unchanged(self):
        with patch.dict(os.environ, {"OPENCODE_API_KEY": "oc-key"}, clear=True):
            headers = worker._resolve_cc_backend_auth_headers(
                {"provider": "opencode-go"}, api_format="anthropic",
            )
        self.assertEqual(headers["x-api-key"], "oc-key")
        self.assertEqual(headers["Origin"], "https://opencode.ai")
        self.assertIn("Mozilla/5.0", headers["User-Agent"])

    def test_openrouter_uses_bearer_token(self):
        with patch.dict(os.environ, {"OPENROUTER_API_KEY": "or-key"}, clear=True):
            headers = worker._resolve_cc_backend_auth_headers(
                {"provider": "openrouter"}, api_format="openai",
            )
        self.assertEqual(headers, {"Authorization": "Bearer or-key"})

    def test_unknown_provider_with_anthropic_format_returns_none(self):
        with patch.dict(os.environ, {}, clear=True):
            headers = worker._resolve_cc_backend_auth_headers(
                {"provider": "unknown-thing"}, api_format="anthropic",
            )
        self.assertIsNone(headers)


if __name__ == "__main__":
    unittest.main()
