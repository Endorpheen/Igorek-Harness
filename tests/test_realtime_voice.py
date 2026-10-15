"""Regressive realtime voice tests: config, credentials, registry, service, HTTP.

Tests read production code and mock all network calls—no test accesses OpenAI, Vault, or other network services. A security invariant is explicitly verified: the long-lived ``OPENAI_API_KEY`` and ephemeral secrets are not included in HTTP responses or returned dictionaries.
"""

from __future__ import annotations

import http.client
import json
import os
import tempfile
import threading
import time
import unittest
import urllib.error
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import MagicMock, patch
from urllib.parse import urlsplit

from factory.core.mobile_bridge.server import API_PREFIX, BridgeConfig, MobileBridge, build_server
from factory.core.realtime_voice import config as rt_config
from factory.core.realtime_voice import credentials
from factory.core.realtime_voice.credentials import RealtimeError
from factory.core.realtime_voice.registry import VoiceCallRegistry, VoiceRateLimiter
from factory.core.realtime_voice.service import VoiceService, VoiceUnavailable
from factory.core.runtime import get_db_path
from factory.core.schema import initialize_database
from factory.web_ui.server import _COOKIE, _STATIC_TYPES, _Server

_LONG_KEY = "sk-live-REGRESSION-long-lived-key"
_EPHEMERAL = "ek-REGRESSION-ephemeral-secret"

_VALID_OFFER = (
    "v=0\r\n"
    "o=- 1 1 IN IP4 127.0.0.1\r\n"
    "s=-\r\n"
    "t=0 0\r\n"
    "m=audio 9 UDP/TLS/RTP/SAVPF 111\r\n"
    "a=sendrecv\r\n"
)
_VALID_ANSWER = (
    "v=0\r\n"
    "o=- 2 2 IN IP4 127.0.0.1\r\n"
    "s=-\r\n"
    "t=0 0\r\n"
    "m=audio 9 UDP/TLS/RTP/SAVPF 111\r\n"
    "a=recvonly\r\n"
)

_RT_ENV_KEYS = (
    "IGOREK_REALTIME_ENABLED",
    "IGOREK_REALTIME_TIER",
    "IGOREK_REALTIME_MODEL",
    "IGOREK_REALTIME_VOICE",
    "IGOREK_REALTIME_TRANSCRIPTION_MODEL",
    "IGOREK_REALTIME_INSTRUCTIONS",
    "IGOREK_REALTIME_TTL_S",
    "IGOREK_REALTIME_BASE_URL",
    "IGOREK_REALTIME_AGENT",
    "OPENAI_API_KEY",
)


def _realtime_env(**values: str):
    """Deterministic environment: realtime keys are empty unless explicitly set."""
    overrides = {name: "" for name in _RT_ENV_KEYS}
    overrides.update(values)
    return patch.dict(os.environ, overrides)


class _FakeHttpResponse:
    """Minimal HTTP response for urllib: use context manager + read()."""

    def __init__(
        self,
        *,
        payload: dict | None = None,
        raw: bytes | None = None,
        headers: dict | None = None,
    ) -> None:
        self._raw = raw if raw is not None else json.dumps(payload or {}).encode("utf-8")
        self.headers = headers or {}

    def read(self) -> bytes:
        return self._raw

    def __enter__(self) -> "_FakeHttpResponse":
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        return False


class RealtimeConfigTest(unittest.TestCase):
    def test_default_tier_is_mini_with_mini_model(self):
        with _realtime_env():
            self.assertEqual(rt_config.default_tier(), "mini")
            self.assertEqual(rt_config.model_for_tier("mini"), "gpt-realtime-2.1-mini")
            self.assertEqual(rt_config.resolve_model(None), "gpt-realtime-2.1-mini")

    def test_full_tier_selects_full_model(self):
        with _realtime_env(IGOREK_REALTIME_TIER="full"):
            self.assertEqual(rt_config.default_tier(), "full")
            self.assertEqual(rt_config.resolve_model(rt_config.default_tier()), "gpt-realtime-2.1")

    def test_unknown_tier_falls_back_to_mini(self):
        with _realtime_env(IGOREK_REALTIME_TIER="gigantic"):
            self.assertEqual(rt_config.default_tier(), "mini")

    def test_explicit_model_override_wins_over_tier(self):
        with _realtime_env(IGOREK_REALTIME_TIER="mini", IGOREK_REALTIME_MODEL="gpt-realtime-custom"):
            self.assertEqual(rt_config.resolve_model("mini"), "gpt-realtime-custom")
        with _realtime_env(IGOREK_REALTIME_TIER="full", IGOREK_REALTIME_MODEL="gpt-realtime-custom"):
            self.assertEqual(rt_config.resolve_model("full"), "gpt-realtime-custom")

    def test_ttl_is_clamped_to_supported_range(self):
        with _realtime_env(IGOREK_REALTIME_TTL_S="5"):
            self.assertEqual(rt_config.ttl_seconds(), rt_config.MIN_TTL_S)
            self.assertEqual(rt_config.ttl_seconds(), 60)
        with _realtime_env(IGOREK_REALTIME_TTL_S="999999"):
            self.assertEqual(rt_config.ttl_seconds(), rt_config.MAX_TTL_S)
            self.assertEqual(rt_config.ttl_seconds(), 7200)
        with _realtime_env(IGOREK_REALTIME_TTL_S="900"):
            self.assertEqual(rt_config.ttl_seconds(), 900)
        with _realtime_env(IGOREK_REALTIME_TTL_S="not-a-number"):
            self.assertEqual(rt_config.ttl_seconds(), rt_config.DEFAULT_TTL_S)
        with _realtime_env():
            self.assertEqual(rt_config.ttl_seconds(), rt_config.DEFAULT_TTL_S)

    def test_public_config_has_exact_key_set(self):
        with _realtime_env():
            payload = rt_config.public_config()

        self.assertEqual(set(payload), {"enabled", "tier", "model", "voice", "tiers"})
        self.assertTrue(payload["enabled"])
        self.assertEqual(payload["tier"], "mini")
        self.assertEqual(payload["model"], "gpt-realtime-2.1-mini")
        self.assertEqual(payload["voice"], rt_config.DEFAULT_VOICE)
        self.assertEqual(set(payload["tiers"]), {"full", "mini"})


class SessionPayloadTest(unittest.TestCase):
    def test_default_payload_matches_realtime_contract(self):
        with _realtime_env():
            payload = rt_config.build_session_payload(session_id="session-1")

        self.assertEqual(payload["type"], "realtime")
        self.assertEqual(payload["model"], "gpt-realtime-2.1-mini")
        self.assertEqual(payload["output_modalities"], ["audio"])

        # Relay mode: OpenAI only performs voice recognition and speech synthesis. Jobs/tools and
        # auto-responses are disabled—all work is handled by the Igorek session.
        self.assertNotIn("tools", payload)
        self.assertNotIn("tool_choice", payload)
        self.assertIn("озвучка", payload["instructions"])
        self.assertIn("НЕ вызываешь инструменты", payload["instructions"])

        turn_detection = payload["audio"]["input"]["turn_detection"]
        self.assertIs(turn_detection["interrupt_response"], True)
        self.assertIs(turn_detection["create_response"], False)

        transcription = payload["audio"]["input"]["transcription"]
        self.assertEqual(transcription["model"], "gpt-4o-mini-transcribe")
        self.assertEqual(payload["audio"]["output"]["voice"], rt_config.voice())
        self.assertEqual(payload["audio"]["output"]["voice"], rt_config.DEFAULT_VOICE)

    def test_env_overrides_shape_model_voice_and_transcription(self):
        with _realtime_env(
            IGOREK_REALTIME_VOICE="echo",
            IGOREK_REALTIME_TRANSCRIPTION_MODEL="whisper-realtime-test",
        ):
            payload = rt_config.build_session_payload(session_id="session-1", tier="full")

        self.assertEqual(payload["model"], "gpt-realtime-2.1")
        self.assertEqual(payload["audio"]["output"]["voice"], "echo")
        self.assertEqual(payload["audio"]["input"]["transcription"]["model"], "whisper-realtime-test")

    def test_explicit_tier_argument_overrides_env_default(self):
        with _realtime_env(IGOREK_REALTIME_TIER="mini"):
            payload = rt_config.build_session_payload(session_id="session-1", tier="full")

        self.assertEqual(payload["model"], "gpt-realtime-2.1")


class VoiceRegistryTest(unittest.TestCase):
    @staticmethod
    def _create(registry: VoiceCallRegistry, *, session_id: str = "session-1", expires_at: float | None = None):
        return registry.create(
            session_id=session_id,
            ephemeral=f"ek-{session_id}",
            expires_at=time.time() + 3600 if expires_at is None else expires_at,
            model="gpt-realtime-2.1-mini",
            voice="marin",
        )

    def test_create_get_pop_round_trip(self):
        registry = VoiceCallRegistry()
        call = self._create(registry)

        self.assertIs(registry.get(call.call_id), call)
        self.assertEqual(registry.count(), 1)
        self.assertIs(registry.pop(call.call_id), call)
        self.assertIsNone(registry.get(call.call_id))
        self.assertEqual(registry.count(), 0)

    def test_expired_call_is_pruned_by_get_and_count(self):
        registry = VoiceCallRegistry()
        call = self._create(registry, expires_at=time.time() - 1)

        self.assertIsNone(registry.get(call.call_id))
        self.assertEqual(registry.count(), 0)

    def test_expired_calls_are_pruned_when_a_new_call_is_created(self):
        registry = VoiceCallRegistry()
        expired = self._create(registry, session_id="expired", expires_at=time.time() - 1)
        fresh = self._create(registry, session_id="fresh")

        self.assertIsNone(registry.get(expired.call_id))
        self.assertIs(registry.get(fresh.call_id), fresh)
        self.assertEqual(registry.count(), 1)

    def test_exceeding_max_calls_evicts_oldest(self):
        registry = VoiceCallRegistry(max_calls=2)
        first = self._create(registry, session_id="first")
        second = self._create(registry, session_id="second")
        third = self._create(registry, session_id="third")

        self.assertIsNone(registry.get(first.call_id))
        self.assertIs(registry.get(second.call_id), second)
        self.assertIs(registry.get(third.call_id), third)
        self.assertEqual(registry.count(), 2)

    def test_limiter_rejects_second_call_within_min_interval(self):
        limiter = VoiceRateLimiter(min_interval_s=60.0)

        self.assertTrue(limiter.allow("session-1"))
        self.assertFalse(limiter.allow("session-1"))
        self.assertTrue(limiter.allow("session-2"))

    def test_limiter_rejects_calls_over_window_quota(self):
        limiter = VoiceRateLimiter(min_interval_s=0.0, window_s=3600.0, max_per_window=2)

        self.assertTrue(limiter.allow("session-1"))
        self.assertTrue(limiter.allow("session-1"))
        self.assertFalse(limiter.allow("session-1"))


class CredentialsTest(unittest.TestCase):
    def test_mint_client_secret_returns_value_and_expiry_without_key(self):
        response = _FakeHttpResponse(payload={"value": _EPHEMERAL, "expires_at": 1899999999})
        with patch.object(credentials.urllib.request, "urlopen", return_value=response) as mocked:
            result = credentials.mint_client_secret(
                session_payload={"type": "realtime"},
                ttl_s=300,
                key=_LONG_KEY,
                base_url="https://api.openai.com",
            )

        self.assertEqual(result, {"value": _EPHEMERAL, "expires_at": 1899999999})
        self.assertNotIn(_LONG_KEY, json.dumps(result))
        request = mocked.call_args.args[0]
        self.assertEqual(request.full_url, "https://api.openai.com/v1/realtime/client_secrets")
        self.assertEqual(request.get_header("Authorization"), f"Bearer {_LONG_KEY}")
        sent = json.loads(request.data.decode("utf-8"))
        self.assertEqual(sent["expires_after"], {"anchor": "created_at", "seconds": 300})
        self.assertEqual(sent["session"], {"type": "realtime"})

    def test_mint_client_secret_rejects_response_without_value(self):
        response = _FakeHttpResponse(payload={"expires_at": 1899999999})
        with patch.object(credentials.urllib.request, "urlopen", return_value=response):
            with self.assertRaises(RealtimeError) as ctx:
                credentials.mint_client_secret(
                    session_payload={}, ttl_s=60, key=_LONG_KEY, base_url="https://api.openai.com"
                )

        self.assertEqual(ctx.exception.code, "invalid_response")

    def test_create_call_rejects_invalid_sdp_before_network(self):
        cases = {
            "empty": "",
            "no_version": "m=audio 9 UDP/TLS/RTP/SAVPF 111\r\n",
            "no_audio_media": "v=0\r\no=- 1 1 IN IP4 127.0.0.1\r\nm=video 9 UDP/TLS/RTP/SAVPF 96\r\n",
        }
        for name, sdp in cases.items():
            with self.subTest(name=name), patch.object(credentials.urllib.request, "urlopen") as mocked:
                with self.assertRaises(RealtimeError) as ctx:
                    credentials.create_call(
                        sdp=sdp, ephemeral=_EPHEMERAL, base_url="https://api.openai.com"
                    )

                self.assertEqual(ctx.exception.code, "invalid_sdp")
                self.assertEqual(ctx.exception.status, 400)
                mocked.assert_not_called()

    def test_create_call_returns_answer_and_location_call_id(self):
        response = _FakeHttpResponse(
            raw=_VALID_ANSWER.encode("utf-8"),
            headers={"Location": "https://api.openai.com/v1/realtime/calls/rtc_PROVIDER1"},
        )
        with patch.object(credentials.urllib.request, "urlopen", return_value=response) as mocked:
            result = credentials.create_call(
                sdp=_VALID_OFFER, ephemeral=_EPHEMERAL, base_url="https://api.openai.com"
            )

        self.assertEqual(result, {"sdp": _VALID_ANSWER, "call_id": "rtc_PROVIDER1"})
        request = mocked.call_args.args[0]
        self.assertEqual(request.full_url, "https://api.openai.com/v1/realtime/calls")
        self.assertEqual(request.get_header("Authorization"), f"Bearer {_EPHEMERAL}")
        self.assertEqual(request.headers.get("Content-type"), "application/sdp")
        self.assertNotIn(_EPHEMERAL, json.dumps(result))

    def test_http_401_maps_to_key_rejected(self):
        error = urllib.error.HTTPError(
            "https://api.openai.com/v1/realtime/client_secrets", 401, "Unauthorized", {}, None
        )
        with patch.object(credentials.urllib.request, "urlopen", side_effect=error):
            with self.assertRaises(RealtimeError) as ctx:
                credentials.mint_client_secret(
                    session_payload={}, ttl_s=60, key=_LONG_KEY, base_url="https://api.openai.com"
                )

        self.assertEqual(ctx.exception.code, "key_rejected")
        self.assertEqual(ctx.exception.status, 502)
        self.assertNotIn(_LONG_KEY, str(ctx.exception))

    def test_openai_api_key_uses_env_value_and_skips_broker(self):
        with _realtime_env(OPENAI_API_KEY=_LONG_KEY):
            with patch("factory.core.vault.runtime.build_services") as broker:
                value = credentials.openai_api_key()

        self.assertEqual(value, _LONG_KEY)
        broker.assert_not_called()


class VoiceServiceTest(unittest.TestCase):
    @staticmethod
    def _service(*, min_interval_s: float = 0.0) -> VoiceService:
        return VoiceService(limiter=VoiceRateLimiter(min_interval_s=min_interval_s))

    @staticmethod
    def _mint_result() -> dict:
        return {"value": _EPHEMERAL, "expires_at": 1899999999}

    def test_start_session_returns_public_contract_without_secrets(self):
        service = self._service()
        minted = MagicMock(return_value=self._mint_result())
        with (
            _realtime_env(),
            patch.object(credentials, "openai_api_key", return_value=_LONG_KEY),
            patch.object(credentials, "mint_client_secret", minted),
        ):
            result = service.start_session("session-1", tier="full")

        self.assertEqual(set(result), {"call_id", "expires_at", "model", "voice"})
        self.assertEqual(result["expires_at"], 1899999999)
        self.assertEqual(result["model"], "gpt-realtime-2.1")
        self.assertEqual(result["voice"], rt_config.DEFAULT_VOICE)
        serialized = json.dumps(result)
        self.assertNotIn(_LONG_KEY, serialized)
        self.assertNotIn(_EPHEMERAL, serialized)

        minted.assert_called_once()
        self.assertEqual(minted.call_args.kwargs["key"], _LONG_KEY)
        self.assertEqual(minted.call_args.kwargs["ttl_s"], rt_config.ttl_seconds())
        session_payload = minted.call_args.kwargs["session_payload"]
        self.assertNotIn("tools", session_payload)
        self.assertIs(
            session_payload["audio"]["input"]["turn_detection"]["create_response"], False
        )
        stored = service.registry.get(result["call_id"])
        self.assertIsNotNone(stored)
        self.assertEqual(stored.ephemeral, _EPHEMERAL)

    def test_start_session_without_key_raises_no_key(self):
        with _realtime_env(), patch.object(credentials, "openai_api_key", return_value=""):
            with self.assertRaises(VoiceUnavailable) as ctx:
                self._service().start_session("session-1")

        self.assertEqual(ctx.exception.code, "no_key")
        self.assertEqual(ctx.exception.status, 503)

    def test_start_session_when_disabled_raises_disabled(self):
        with _realtime_env(IGOREK_REALTIME_ENABLED="0"):
            with self.assertRaises(VoiceUnavailable) as ctx:
                self._service().start_session("session-1")

        self.assertEqual(ctx.exception.code, "disabled")

    def test_start_session_rate_limits_repeat_calls(self):
        service = self._service(min_interval_s=60.0)
        with (
            _realtime_env(),
            patch.object(credentials, "openai_api_key", return_value=_LONG_KEY),
            patch.object(credentials, "mint_client_secret", return_value=self._mint_result()),
        ):
            service.start_session("session-rl")
            with self.assertRaises(VoiceUnavailable) as ctx:
                service.start_session("session-rl")

        self.assertEqual(ctx.exception.code, "rate_limited")
        self.assertEqual(ctx.exception.status, 429)

    def test_relay_sdp_for_unknown_call_raises_call_not_found(self):
        create_call = MagicMock()
        with patch.object(credentials, "create_call", create_call):
            with self.assertRaises(VoiceUnavailable) as ctx:
                self._service().relay_sdp("missing-call", _VALID_OFFER)

        self.assertEqual(ctx.exception.code, "call_not_found")
        self.assertEqual(ctx.exception.status, 404)
        create_call.assert_not_called()

    def test_relay_sdp_forwards_ephemeral_and_hangup_uses_provider_call_id(self):
        service = self._service()
        create_call = MagicMock(return_value={"sdp": _VALID_ANSWER, "call_id": "rtc_PROVIDER9"})
        provider_hangup = MagicMock()
        with (
            _realtime_env(),
            patch.object(credentials, "openai_api_key", return_value=_LONG_KEY),
            patch.object(credentials, "mint_client_secret", return_value=self._mint_result()),
            patch.object(credentials, "create_call", create_call),
        ):
            started = service.start_session("session-1")
            relayed = service.relay_sdp(started["call_id"], _VALID_OFFER)

        self.assertEqual(relayed, {"sdp": _VALID_ANSWER})
        create_call.assert_called_once_with(sdp=_VALID_OFFER, ephemeral=_EPHEMERAL)
        stored = service.registry.get(started["call_id"])
        self.assertEqual(stored.provider_call_id, "rtc_PROVIDER9")

        with (
            patch.object(credentials, "openai_api_key", return_value=_LONG_KEY),
            patch.object(credentials, "hangup", provider_hangup),
        ):
            result = service.hangup(started["call_id"])

        self.assertEqual(result, {"ok": True})
        provider_hangup.assert_called_once_with(provider_call_id="rtc_PROVIDER9", key=_LONG_KEY)
        self.assertIsNone(service.registry.get(started["call_id"]))

    def test_public_config_reports_no_key(self):
        with _realtime_env(), patch.object(credentials, "openai_api_key", return_value=""):
            payload = self._service().public_config()

        self.assertFalse(payload["enabled"])
        self.assertEqual(payload["reason"], "no_key")
        self.assertEqual(payload["tier"], "mini")

    def test_public_config_reports_disabled(self):
        with _realtime_env(IGOREK_REALTIME_ENABLED="0"):
            payload = self._service().public_config()

        self.assertFalse(payload["enabled"])
        self.assertEqual(payload["reason"], "disabled")

    def test_public_config_with_key_exposes_no_secret(self):
        with _realtime_env(), patch.object(credentials, "openai_api_key", return_value=_LONG_KEY):
            payload = self._service().public_config()

        self.assertTrue(payload["enabled"])
        self.assertEqual(payload["reason"], "")
        self.assertNotIn(_LONG_KEY, json.dumps(payload))


class _FakeVoiceService:
    """Mocking VoiceService for HTTP tests: no network or Vault access."""

    def __init__(self) -> None:
        self.config_payload = {
            "enabled": True,
            "tier": "mini",
            "model": "gpt-realtime-2.1-mini",
            "voice": "marin",
            "tiers": ["full", "mini"],
            "reason": "",
        }
        self.config_calls = 0
        self.started: list[tuple[str, object]] = []
        self.relayed: list[tuple[str, str]] = []
        self.hungup: list[str] = []

    def public_config(self) -> dict:
        self.config_calls += 1
        return dict(self.config_payload)

    def start_session(self, session_id: str, tier: object = None) -> dict:
        self.started.append((session_id, tier))
        return {
            "call_id": "call-http-1",
            "expires_at": 1900000000,
            "model": "gpt-realtime-2.1-mini",
            "voice": "marin",
        }

    def relay_sdp(self, call_id: str, sdp: str) -> dict:
        self.relayed.append((call_id, sdp))
        return {"sdp": _VALID_ANSWER}

    def hangup(self, call_id: str) -> dict:
        self.hungup.append(call_id)
        return {"ok": True}


class _BridgeHttpTestCase(unittest.TestCase):
    """Same HTTP harness as in tests/test_mobile_bridge_server.py."""

    def _make_voice(self):
        self.voice = _FakeVoiceService()
        return self.voice

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._env = patch.dict(
            os.environ,
            {"IGOREK_FACTORY_HOME": self._tmp.name, "FACTORY_MOBILE_PROFILE": "cc-local-8081"},
        )
        self._env.start()
        initialize_database(get_db_path())
        self.bridge = MobileBridge(
            config=BridgeConfig(profile="cc-local-8081", poll_interval_s=5.0),
            voice=self._make_voice(),
        )
        self.server = build_server(self.bridge, host="127.0.0.1", port=0)
        self.port = int(self.server.server_address[1])
        self._thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self._thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self._thread.join(timeout=5)
        self._env.stop()
        self._tmp.cleanup()

    def _request(self, method: str, path: str, *, body: dict | None = None, token: str | None = None):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        payload = json.dumps(body or {}) if body is not None else None
        connection.request(method, path, body=payload, headers=headers)
        response = connection.getresponse()
        raw = response.read().decode("utf-8")
        connection.close()
        parsed = json.loads(raw) if raw else {}
        return response.status, parsed, raw

    def _pair(self, *, name: str = "Pixel") -> str:
        code = self.bridge.store.create_pairing_code()["code"]
        status, payload, _ = self._request(
            "POST", f"{API_PREFIX}/pair/complete", body={"code": code, "name": name}
        )
        self.assertEqual(status, 201, payload)
        return payload["token"]


class MobileVoiceRoutesTest(_BridgeHttpTestCase):
    def test_voice_config_requires_device_token(self):
        status, payload, _ = self._request("GET", f"{API_PREFIX}/voice/config")

        self.assertEqual(status, 401, payload)
        self.assertEqual(self.voice.config_calls, 0)

    def test_voice_config_returns_public_payload_with_token(self):
        token = self._pair()

        status, payload, raw = self._request("GET", f"{API_PREFIX}/voice/config", token=token)

        self.assertEqual(status, 200, payload)
        self.assertEqual(payload, self.voice.config_payload)
        self.assertEqual(self.voice.config_calls, 1)
        self.assertNotIn(_LONG_KEY, raw)
        self.assertNotIn(_EPHEMERAL, raw)

    def test_voice_session_requires_session_id(self):
        token = self._pair()

        status, payload, _ = self._request(
            "POST", f"{API_PREFIX}/voice/session", body={}, token=token
        )

        self.assertEqual(status, 400, payload)
        self.assertEqual(self.voice.started, [])

    def test_voice_sdp_requires_sdp(self):
        token = self._pair()

        status, payload, _ = self._request(
            "POST", f"{API_PREFIX}/voice/sdp", body={"call_id": "call-1"}, token=token
        )

        self.assertEqual(status, 400, payload)
        self.assertEqual(self.voice.relayed, [])


class MobileVoiceSecretsTest(_BridgeHttpTestCase):
    """Real HTTP-based VoiceService: secrets remain on the server."""

    def _make_voice(self):
        self.voice = VoiceService(limiter=VoiceRateLimiter(min_interval_s=0.0))
        return self.voice

    def setUp(self):
        super().setUp()
        get_session = patch.object(
            self.bridge.service, "get_session", return_value={"session_id": "chat-voice"}
        )
        get_session.start()
        self.addCleanup(get_session.stop)

    def test_voice_session_response_never_contains_keys(self):
        token = self._pair()
        with (
            _realtime_env(),
            patch.object(credentials, "openai_api_key", return_value=_LONG_KEY),
            patch.object(credentials, "mint_client_secret", return_value={"value": _EPHEMERAL, "expires_at": 1900000000}),
        ):
            status, payload, raw = self._request(
                "POST",
                f"{API_PREFIX}/voice/session",
                body={"session_id": "chat-voice", "tier": "full"},
                token=token,
            )

        self.assertEqual(status, 200, payload)
        self.assertEqual(set(payload), {"call_id", "expires_at", "model", "voice"})
        self.assertEqual(payload["model"], "gpt-realtime-2.1")
        self.assertNotIn(_LONG_KEY, raw)
        self.assertNotIn(_EPHEMERAL, raw)
        stored = self.voice.registry.get(payload["call_id"])
        self.assertIsNotNone(stored)
        self.assertEqual(stored.ephemeral, _EPHEMERAL)

    def test_sdp_relay_and_hangup_keep_secrets_server_side(self):
        token = self._pair()
        create_call = MagicMock(return_value={"sdp": _VALID_ANSWER, "call_id": "rtc_PROVIDER_HTTP"})
        provider_hangup = MagicMock()
        with (
            _realtime_env(),
            patch.object(credentials, "openai_api_key", return_value=_LONG_KEY),
            patch.object(credentials, "mint_client_secret", return_value={"value": _EPHEMERAL, "expires_at": 1900000000}),
            patch.object(credentials, "create_call", create_call),
        ):
            _, started, _ = self._request(
                "POST", f"{API_PREFIX}/voice/session", body={"session_id": "chat-voice"}, token=token
            )
            status, relayed, raw = self._request(
                "POST",
                f"{API_PREFIX}/voice/sdp",
                body={"call_id": started["call_id"], "sdp": _VALID_OFFER},
                token=token,
            )

        self.assertEqual(status, 200, relayed)
        self.assertEqual(relayed, {"sdp": _VALID_ANSWER})
        self.assertNotIn(_EPHEMERAL, raw)
        create_call.assert_called_once_with(sdp=_VALID_OFFER, ephemeral=_EPHEMERAL)

        with (
            patch.object(credentials, "openai_api_key", return_value=_LONG_KEY),
            patch.object(credentials, "hangup", provider_hangup),
        ):
            status, hung, raw = self._request(
                "POST",
                f"{API_PREFIX}/voice/hangup",
                body={"call_id": started["call_id"]},
                token=token,
            )

        self.assertEqual(status, 200, hung)
        self.assertEqual(hung, {"ok": True})
        provider_hangup.assert_called_once_with(provider_call_id="rtc_PROVIDER_HTTP", key=_LONG_KEY)
        self.assertIsNone(self.voice.registry.get(started["call_id"]))

    def test_voice_sdp_unknown_call_maps_to_404(self):
        token = self._pair()

        status, payload, _ = self._request(
            "POST",
            f"{API_PREFIX}/voice/sdp",
            body={"call_id": "missing", "sdp": _VALID_OFFER},
            token=token,
        )

        self.assertEqual(status, 404, payload)
        self.assertEqual(payload["code"], "call_not_found")


_WEB_TOKEN = "web-ui-voice-test-token"


class _VoiceBridgeHandler(BaseHTTPRequestHandler):
    """Truncated mobile bridge for testing web proxy voice routing paths."""

    protocol_version = "HTTP/1.1"
    calls: list[tuple[str, str, str | None]] = []

    def log_message(self, format, *args):
        return

    def _json(self, status: int, payload: dict) -> None:
        data = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _drain(self) -> None:
        length = int(self.headers.get("Content-Length") or 0)
        if length > 0:
            self.rfile.read(length)

    def do_GET(self):
        self.calls.append((self.command, self.path, self.headers.get("Authorization")))
        if urlsplit(self.path).path == "/mobile/v1/voice/config":
            self._json(
                200,
                {
                    "enabled": True,
                    "tier": "mini",
                    "model": "gpt-realtime-2.1-mini",
                    "voice": "marin",
                    "tiers": ["full", "mini"],
                    "reason": "",
                },
            )
            return
        self._json(404, {"error": "not found"})

    def do_POST(self):
        self.calls.append((self.command, self.path, self.headers.get("Authorization")))
        path = urlsplit(self.path).path
        if path == "/mobile/v1/pair/complete":
            self._drain()
            self._json(201, {"token": _WEB_TOKEN, "name": "Test browser"})
            return
        self._drain()
        self._json(404, {"error": "not found"})


class WebVoiceProxyTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._env = patch.dict(os.environ, {"IGOREK_FACTORY_HOME": self._tmp.name})
        self._env.start()
        _VoiceBridgeHandler.calls = []
        self.bridge = ThreadingHTTPServer(("127.0.0.1", 0), _VoiceBridgeHandler)
        self.bridge_thread = threading.Thread(target=self.bridge.serve_forever, daemon=True)
        self.bridge_thread.start()
        self.server = _Server(0, f"http://127.0.0.1:{self.bridge.server_port}")
        self.port = int(self.server.server_port)
        self.server_thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.server_thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.server_thread.join(timeout=5)
        self.bridge.shutdown()
        self.bridge.server_close()
        self.bridge_thread.join(timeout=5)
        self._env.stop()
        self._tmp.cleanup()

    def _request(self, method: str, path: str, *, body: dict | None = None, cookie: str = ""):
        headers: dict[str, str] = {}
        if body is not None:
            headers["Content-Type"] = "application/json"
        if cookie:
            headers["Cookie"] = cookie
        if method == "POST":
            headers.setdefault("Origin", f"https://127.0.0.1:{self.port}")
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        payload = json.dumps(body).encode("utf-8") if body is not None else None
        connection.request(method, path, body=payload, headers=headers)
        response = connection.getresponse()
        data = response.read()
        result = (response.status, dict(response.headers), data)
        connection.close()
        return result

    def _pair(self) -> str:
        status, headers, _ = self._request("POST", "/api/pair", body={"code": "ABCDEFG"})
        self.assertEqual(status, 201)
        return f"{_COOKIE}={_WEB_TOKEN}"

    def test_voice_script_is_registered_and_served_as_javascript(self):
        self.assertEqual(_STATIC_TYPES.get("voice.js"), "text/javascript; charset=utf-8")

        status, headers, data = self._request("GET", "/voice.js")

        self.assertEqual(status, 200)
        self.assertEqual(headers["Content-Type"], "text/javascript; charset=utf-8")
        self.assertTrue(data)

    def test_voice_config_requires_paired_cookie(self):
        status, _, data = self._request("GET", "/api/voice/config")

        self.assertEqual(status, 401)
        self.assertEqual(_VoiceBridgeHandler.calls, [])
        self.assertNotIn(_WEB_TOKEN.encode(), data)

    def test_voice_config_is_proxied_with_bridge_token(self):
        cookie = self._pair()
        _VoiceBridgeHandler.calls = []

        status, _, data = self._request("GET", "/api/voice/config", cookie=cookie)

        self.assertEqual(status, 200)
        self.assertEqual(json.loads(data)["tier"], "mini")
        self.assertNotIn(_WEB_TOKEN.encode(), data)
        self.assertEqual(
            _VoiceBridgeHandler.calls,
            [("GET", "/mobile/v1/voice/config", f"Bearer {_WEB_TOKEN}")],
        )


if __name__ == "__main__":
    unittest.main()
