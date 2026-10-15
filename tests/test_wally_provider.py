"""Regression tests for the `wally` (RunAnywhere) cc-backend provider.

Integration coverage:
- worker._resolve_cc_backend_auth_headers: Bearer from the Wally session;
- worker._resolve_cc_backend_api_format: openai;
- worker._effective_cc_backend_model: preserve the profile's model;
- memory_review._review_api_format / _openai_request_headers;
- codex_provider: provider name, model catalog and transport;
- factory.core.wally_auth: session parsing, refresh and renewal;
- factory/config/profiles/cc-wally.json and the Vault service mapping.

Negative cases retain compatibility with openrouter, opencode-go and the
Anthropic path.
"""

from __future__ import annotations

import json
import os
import unittest
from pathlib import Path
from unittest.mock import patch

from factory.core import codex_provider, memory_review, wally_auth, worker
from factory.core.runtime import get_profile_config, list_profile_names
from factory.core.vault.broker import SERVICE_ENV_MAP

FAKE_TOKEN = "wally-test-token-0123456789"


class WallyAuthHeadersTest(unittest.TestCase):
    """worker._resolve_cc_backend_auth_headers for provider='wally'."""

    def test_wally_uses_bearer_from_session(self):
        with patch.object(wally_auth, "get_wally_access_token", return_value=FAKE_TOKEN):
            headers = worker._resolve_cc_backend_auth_headers({"provider": "wally"})
        self.assertEqual(headers, {"Authorization": f"Bearer {FAKE_TOKEN}"})

    def test_wally_ignores_api_format_switch(self):
        with patch.object(wally_auth, "get_wally_access_token", return_value=FAKE_TOKEN):
            headers = worker._resolve_cc_backend_auth_headers(
                {"provider": "wally"}, api_format="anthropic",
            )
        self.assertEqual(headers, {"Authorization": f"Bearer {FAKE_TOKEN}"})

    def test_wally_auth_failure_is_explicit(self):
        with patch.object(
            wally_auth,
            "get_wally_access_token",
            side_effect=wally_auth.WallyAuthError("no session"),
        ):
            with self.assertRaises(RuntimeError):
                worker._resolve_cc_backend_auth_headers({"provider": "wally"})

    def test_openrouter_without_env_is_still_none(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertIsNone(
                worker._resolve_cc_backend_auth_headers(
                    {"provider": "openrouter"}, api_format="openai",
                )
            )

    def test_opencode_go_bearer_path_intact(self):
        with patch.dict(os.environ, {"OPENCODE_API_KEY": "oc-key"}, clear=True):
            headers = worker._resolve_cc_backend_auth_headers(
                {"provider": "opencode-go"}, api_format="openai",
            )
        self.assertEqual(headers["Authorization"], "Bearer oc-key")
        self.assertIn("x-opencode-session", headers)


class WallyApiFormatTest(unittest.TestCase):
    def test_wally_is_openai_format(self):
        self.assertEqual(
            worker._resolve_cc_backend_api_format({"provider": "wally"}, "glm-5.3-flash"),
            "openai",
        )

    def test_wally_api_format_wins_over_anthropic_config(self):
        self.assertEqual(
            worker._resolve_cc_backend_api_format(
                {"provider": "wally", "api_format": "anthropic"}, "glm-5.3-flash",
            ),
            "openai",
        )

    def test_anthropic_provider_untouched(self):
        self.assertEqual(
            worker._resolve_cc_backend_api_format({"provider": "glm"}, "glm-5.3-flash"),
            "anthropic",
        )


class WallyModelResolutionTest(unittest.TestCase):
    def test_wally_model_is_not_resolved_from_local_backend(self):
        def _boom(*_args, **_kwargs):  # pragma: no cover - must not be called
            raise AssertionError("local /v1/models probe must not run for wally")

        with patch.object(worker.urllib.request, "urlopen", _boom):
            model = worker._effective_cc_backend_model(
                provider="wally",
                configured_model="glm-5.3-flash",
                task_metadata={},
                base_url="https://inference.runanywhere.ai/v1",
            )
        self.assertEqual(model, "glm-5.3-flash")

    def test_local_provider_still_probes_backend(self):
        class _Response:
            def __enter__(self):
                return self

            def __exit__(self, *_exc):
                return False

            def read(self):
                return json.dumps({"data": [{"id": "local-model"}]}).encode()

        with patch.object(worker.urllib.request, "urlopen", lambda *a, **k: _Response()):
            model = worker._effective_cc_backend_model(
                provider="local",
                configured_model="fallback-model",
                task_metadata={},
                base_url="http://127.0.0.1:8001/v1",
            )
        self.assertEqual(model, "local-model")


class WallyMemoryReviewTest(unittest.TestCase):
    def test_review_api_format_is_openai(self):
        self.assertEqual(
            memory_review._review_api_format({"provider": "wally"}, "glm-5.3-flash"),
            "openai",
        )

    def test_review_headers_use_wally_token(self):
        with patch.object(wally_auth, "get_wally_access_token", return_value=FAKE_TOKEN):
            headers = memory_review._openai_request_headers({"provider": "wally"})
        self.assertEqual(headers, {"Authorization": f"Bearer {FAKE_TOKEN}"})

    def test_review_headers_empty_when_session_missing(self):
        with patch.object(
            wally_auth,
            "get_wally_access_token",
            side_effect=wally_auth.WallyAuthError("no session"),
        ):
            self.assertEqual(memory_review._openai_request_headers({"provider": "wally"}), {})

    def test_review_headers_for_other_provider_untouched(self):
        with patch.dict(os.environ, {"OPENCODE_API_KEY": "oc-key"}, clear=True):
            headers = memory_review._openai_request_headers(
                {"provider": "opencode-go"}, api_format="openai",
            )
        self.assertEqual(headers["Authorization"], "Bearer oc-key")


class WallyProviderCatalogTest(unittest.TestCase):
    def test_user_facing_name(self):
        self.assertEqual(codex_provider.user_facing_codex_provider("wally"), "RunAnywhere (Wally)")

    def test_transport_is_openai(self):
        self.assertEqual(
            codex_provider.model_transport_for_provider("wally", "gemma-4"), "openai",
        )

    def test_catalog_uses_live_model_list(self):
        with patch.object(wally_auth, "list_wally_models", return_value=["gemma-4", "glm-5.3-flash"]):
            models = codex_provider.suggested_models_for_provider("wally")
            catalog = codex_provider.model_catalog_for_provider("wally")
        self.assertEqual(models, ["gemma-4", "glm-5.3-flash"])
        self.assertEqual(
            catalog,
            [
                {"id": "gemma-4", "transport": "openai"},
                {"id": "glm-5.3-flash", "transport": "openai"},
            ],
        )

    def test_catalog_falls_back_to_known_models_without_session(self):
        with patch.object(
            wally_auth, "get_wally_session", side_effect=wally_auth.WallyAuthError("no session"),
        ):
            models = wally_auth.list_wally_models()
        self.assertIn("glm-5.3-flash", models)

    def test_opencode_go_catalog_untouched(self):
        models = codex_provider.suggested_models_for_provider("opencode-go")
        self.assertIn("deepseek-v4-pro", models)


class WallySessionTest(unittest.TestCase):
    def test_payload_parsing_with_unix_expiry(self):
        session = wally_auth._session_from_payload(
            {
                "access_token": FAKE_TOKEN,
                "refresh_token": "refresh-1",
                "console_url": "https://inference.runanywhere.ai",
                "expires_at": 4_100_000_000,
                "email": "user@example.com",
                "plan": "beta",
            },
            source="test",
        )
        assert session is not None
        self.assertEqual(session.access_token, FAKE_TOKEN)
        self.assertEqual(session.expires_at, 4_100_000_000)
        self.assertFalse(session.is_expired())

    def test_payload_parsing_with_iso_expiry_and_expires_in(self):
        iso = wally_auth._session_from_payload(
            {"access_token": FAKE_TOKEN, "expires_at": "2100-01-01T00:00:00Z"},
            source="test",
        )
        assert iso is not None
        self.assertIsNotNone(iso.expires_at)
        self.assertFalse(iso.is_expired())

        relative = wally_auth._session_from_payload(
            {"access_token": FAKE_TOKEN, "expires_in": "3600"}, source="test",
        )
        assert relative is not None
        self.assertGreater(relative.expires_at or 0, 0)

    def test_expired_session_is_detected(self):
        session = wally_auth.WallySession(
            access_token=FAKE_TOKEN,
            refresh_token="refresh-1",
            console_url="https://inference.runanywhere.ai",
            expires_at=1,
            email="",
            plan="",
            source="test",
        )
        self.assertTrue(session.is_expired())

    def test_refresh_rotates_token_and_keeps_refresh_token(self):
        stale = wally_auth.WallySession(
            access_token="stale",
            refresh_token="refresh-1",
            console_url="https://inference.runanywhere.ai",
            expires_at=1,
            email="user@example.com",
            plan="beta",
            source="test",
        )
        calls: list[tuple[str, dict]] = []

        def _fake_http(url, *, method="GET", payload=None, token=None, timeout_s=30):
            calls.append((url, payload or {}))
            return {"access_token": "fresh", "expires_in": "3600"}

        with patch.object(wally_auth, "_http_json", _fake_http), patch.object(
            wally_auth, "_store_session_to_file", lambda session: True,
        ):
            refreshed = wally_auth.refresh_wally_session(stale)

        self.assertEqual(refreshed.access_token, "fresh")
        self.assertEqual(refreshed.refresh_token, "refresh-1")
        self.assertEqual(calls[0][0], "https://inference.runanywhere.ai/auth/cli/refresh")
        self.assertEqual(calls[0][1], {"refresh_token": "refresh-1"})

    def test_refresh_without_refresh_token_is_rejected(self):
        session = wally_auth.WallySession(
            access_token="stale",
            refresh_token="",
            console_url="https://inference.runanywhere.ai",
            expires_at=1,
            email="",
            plan="",
            source="test",
        )
        with self.assertRaises(wally_auth.WallyAuthError):
            wally_auth.refresh_wally_session(session)

    def test_env_session_is_used_first(self):
        with patch.dict(os.environ, {"WALLY_ACCESS_TOKEN": "env-token"}, clear=True):
            session = wally_auth.load_wally_session(use_cache=False)
        assert session is not None
        self.assertEqual(session.access_token, "env-token")
        self.assertEqual(session.source, "env")

    def test_credentials_path_override(self):
        with patch.dict(os.environ, {"FACTORY_WALLY_CREDENTIALS": "/tmp/x/credentials.json"}, clear=True):
            self.assertEqual(str(wally_auth.credentials_path()), "/tmp/x/credentials.json")

    def test_api_base_url(self):
        self.assertEqual(wally_auth.api_base_url(), "https://inference.runanywhere.ai/v1")


class WallyProfileAndVaultTest(unittest.TestCase):
    def test_bundled_profile_exists_and_is_valid(self):
        profile_path = Path(codex_provider.__file__).resolve().parents[1] / "config" / "profiles" / "cc-wally.json"
        self.assertTrue(profile_path.exists(), f"missing bundled profile: {profile_path}")
        payload = json.loads(profile_path.read_text(encoding="utf-8"))
        self.assertEqual(payload["backend"], "cc-backend")
        self.assertEqual(payload["provider"], "wally")
        self.assertEqual(payload["model"], "glm-5.3-flash")
        self.assertEqual(payload["api_format"], "openai")

    def test_profile_config_is_resolvable(self):
        config = get_profile_config("cc-wally")
        self.assertEqual(config["backend"], "cc-backend")
        self.assertEqual(config["provider"], "wally")
        self.assertEqual(config["base_url"], "https://inference.runanywhere.ai/v1")
        self.assertEqual(config["api_format"], "openai")

    def test_profile_listing_includes_bundled_profiles(self):
        self.assertIn("cc-wally", list_profile_names())

    def test_vault_env_map_exposes_wally_tokens(self):
        self.assertEqual(
            SERVICE_ENV_MAP["wally"],
            {"WALLY_ACCESS_TOKEN": "{secret}", "WALLY_REFRESH_TOKEN": "{secret}"},
        )


if __name__ == "__main__":
    unittest.main()
