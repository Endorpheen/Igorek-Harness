#!/usr/bin/env python3
"""TDD suite for keeping provider credentials out of the guest (plan todo 18).

The contract this suite pins:

* **Host-side egress auth injection is the preferred path.** A host component
  (``factory/core/isolation/egress_auth.py``) proxies provider traffic and adds
  ``Authorization``/``x-api-key`` itself, so the guest holds no long-lived
  provider key. Every known cc-backend provider has an injectable route: Wally
  (``factory/core/wally_auth.py`` JWT refresh is the hardest case, driven by the
  hard-exit test below: an expired env session, a real ``/auth/cli/refresh``
  round trip, and a completion request the guest issued without any credential),
  OpenRouter, OpenCode and the Anthropic-compatible GLM/MiniMax (``x-api-key``).
* **The fallback is explicit, never silent.** A base URL with no injectable
  route (an unknown host) falls back to the existing host-side ephemeral Vault
  materialization (``worker._materialize_vault_env``) and the plan carries a
  documented :class:`ResidualRisk` entry; MCP credential-bearing servers have
  their own registered residual (the todo-31 placement).
* **The base image contains no secrets.** The build recipe and the image
  manifest are scanned for secret-named assignments, secret-named keys and
  token-shaped values; the scanner is proven to catch a planted secret first.
* **A proxy failure is fail-closed.** An unreachable/unstartable proxy produces
  a clear structured error and never leaks a stored key into the guest.
* **Broker-op policy is host-side, approval-gated and audited.** The todo-19
  broker does not exist yet, so the policy seam is pinned here.

Adversarial classes covered here:

* misleading success - the proxy test asserts the upstream *saw* the injected
  ``Authorization`` header and that the guest env scan found nothing, never just
  that a proxy object exists;
* secret leakage - no token appears in a guest env, in a task frame, in proxy
  logs or in the scanned image sources;
* dirty worktree - every write stays under ``tempfile`` roots.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from http import client as http_client
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from unittest.mock import patch

REPO_ROOT = Path(
    os.environ.get("IGOREK_FACTORY_REPO", Path(__file__).resolve().parents[1])
)
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from factory.core import wally_auth  # noqa: E402
from factory.core.isolation.base_image import (  # noqa: E402
    ImageBuildInfo,
    build_manifest,
)
from factory.core.isolation.broker_seam import (  # noqa: E402
    HOST_POLICY_SOURCE,
    HostOpPolicy,
    evaluate_host_op_request,
    host_op_audit_record,
)
from factory.core.isolation.channel import (  # noqa: E402
    ChannelMessage,
    MessageKind,
    SessionMap,
)
from factory.core.isolation.config import (  # noqa: E402
    MODE_KVM,
    HostCapabilities,
    IsolationConfig,
)
from factory.core.isolation.domain import CommandResult  # noqa: E402
from factory.core.isolation.egress_auth import (  # noqa: E402
    EgressAuthProxy,
    ProviderAuthInjector,
    ProxyConfig,
)
from factory.core.isolation.errors import IsolationError  # noqa: E402
from factory.core.isolation.manager import (  # noqa: E402
    IsolationManager,
    ManagerSeams,
)
from factory.core.isolation.preflight import REASON_OK, PreflightResult  # noqa: E402
from factory.core.isolation.secret_policy import (  # noqa: E402
    ENV_EGRESS_AUTH,
    ENV_EGRESS_AUTH_BIND,
    MODE_AUTO,
    MODE_EPHEMERAL_ENV,
    MODE_PROXY,
    PROVIDER_WALLY,
    REASON_EPHEMERAL_ENV_MODE,
    REASON_NOT_INJECTABLE,
    ProviderSecretPolicy,
    ProxyEndpoint,
    build_fallback_plan,
    build_proxy_plan,
    mcp_credential_residual,
    route_for_base_url,
    wally_route,
)
from factory.core.isolation.secret_residual import MCP_RESIDUAL_MODE  # noqa: E402
from factory.core.isolation.secret_scan import (  # noqa: E402
    SECRET_KEY_NAMES,
    GuestEnvLeakError,
    assert_guest_env_clean,
    manifest_secret_keys,
    mask_secret,
    scan_base_image_sources,
    scan_guest_env_for_secrets,
    scan_text_for_secrets,
)
from factory.core.isolation.workspace import WorkspacePolicy  # noqa: E402
from factory.core.trace_taxonomy import CcTraceEvent  # noqa: E402

RECIPE_PATH = REPO_ROOT / "factory" / "scripts" / "build_isolation_image.sh"
DEFAULT_WALLY_BASE_URL = f"{wally_auth.WALLY_DEFAULT_CONSOLE_URL}/v1"

EXPIRED_TOKEN = "expired-access-token-0001"
REFRESH_TOKEN = "refresh-token-0001"
FRESH_TOKEN = "fresh-access-token-0002"
HOST_INJECTED_TOKEN = "host-injected-token-0003"
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"


class FakeWallyUpstream:
    """Minimal Wally console: refresh endpoint plus one OpenAI-compatible call."""

    def __init__(self, *, accepted_token: str = FRESH_TOKEN) -> None:
        self.accepted_token = accepted_token
        self.refresh_calls = 0
        self.completion_authorizations: list[str] = []
        self.seen_paths: list[str] = []
        upstream = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args: Any) -> None:  # noqa: ARG002 - silence
                return None

            def do_POST(self) -> None:  # noqa: N802 - http.server API
                length = int(self.headers.get("Content-Length") or 0)
                body = self.rfile.read(length) if length else b""
                upstream.seen_paths.append(self.path)
                if self.path == "/auth/cli/refresh":
                    upstream.refresh_calls += 1
                    payload = json.loads(body or b"{}")
                    if payload.get("refresh_token") != REFRESH_TOKEN:
                        self._send(401, {"error": "invalid_refresh_token"})
                        return
                    self._send(
                        200,
                        {
                            "access_token": FRESH_TOKEN,
                            "refresh_token": "refresh-token-0002",
                            "expires_at": int(time.time()) + 3600,
                            "email": "igorek@example.com",
                            "plan": "pro",
                        },
                    )
                    return
                if self.path == "/v1/chat/completions":
                    upstream.completion_authorizations.append(
                        self.headers.get("Authorization", "")
                    )
                    if self.headers.get("Authorization") == f"Bearer {upstream.accepted_token}":
                        self._send(
                            200,
                            {
                                "id": "cmpl-1",
                                "choices": [
                                    {
                                        "message": {
                                            "role": "assistant",
                                            "content": "pong",
                                        }
                                    }
                                ],
                            },
                        )
                    else:
                        self._send(401, {"error": "unauthorized"})
                    return
                self._send(404, {"error": "not_found"})

            def _send(self, status: int, payload: dict[str, Any]) -> None:
                data = json.dumps(payload).encode("utf-8")
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(
            target=self.server.serve_forever, name="fake-wally-upstream", daemon=True
        )
        self.thread.start()

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.server.server_address[1]}"

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()


def post_json(url: str, payload: dict[str, Any], headers: dict[str, str] | None = None):
    data = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url, data=data, headers={"Content-Type": "application/json", **(headers or {})},
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        return response.status, response.read().decode("utf-8")


def post_json_raw(port: int, path: str, payload: dict[str, Any]) -> tuple[int, str]:
    """POST without following redirects (the guest's view of a 3xx answer)."""
    connection = http_client.HTTPConnection("127.0.0.1", port, timeout=10)
    try:
        connection.request(
            "POST",
            path,
            body=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        response = connection.getresponse()
        return response.status, response.read().decode("utf-8")
    finally:
        connection.close()


class RecordingServer:
    """Records every request it receives (a would-be redirect target)."""

    def __init__(self) -> None:
        self.requests: list[tuple[str, str, str]] = []
        recorder = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args: Any) -> None:  # noqa: ARG002 - silence
                return None

            def do_POST(self) -> None:  # noqa: N802 - http.server API
                length = int(self.headers.get("Content-Length") or 0)
                if length:
                    self.rfile.read(length)
                recorder.requests.append(
                    (self.command, self.path, self.headers.get("Authorization", ""))
                )
                self.send_response(200)
                self.send_header("Content-Length", "2")
                self.end_headers()
                self.wfile.write(b"{}")

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.server.server_address[1]}"

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()


class RedirectingServer:
    """Answers every POST with a 302 to ``location``."""

    def __init__(self, location: str) -> None:
        self.location = location
        target = location

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args: Any) -> None:  # noqa: ARG002 - silence
                return None

            def do_POST(self) -> None:  # noqa: N802 - http.server API
                length = int(self.headers.get("Content-Length") or 0)
                if length:
                    self.rfile.read(length)
                self.send_response(302)
                self.send_header("Location", target)
                self.send_header("Content-Length", "0")
                self.end_headers()

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.server.server_address[1]}"

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()


class BaseImageSecretScanTest(unittest.TestCase):
    """The base image (recipe + manifest) must contain no secrets."""

    def test_recipe_has_no_secret_assignments_or_token_values(self) -> None:
        self.assertTrue(RECIPE_PATH.is_file(), str(RECIPE_PATH))
        findings = scan_base_image_sources(recipe_path=RECIPE_PATH)
        self.assertEqual([], [finding.reason for finding in findings])

    def test_manifest_has_no_secret_keys_or_token_values(self) -> None:
        with tempfile.TemporaryDirectory(prefix="igorek-image-") as tmp:
            image = Path(tmp) / "base.qcow2"
            image.write_bytes(b"qcow2-placeholder")
            manifest = build_manifest(
                image, ImageBuildInfo(tool_version="0.1.740", repo_commit="deadbeef")
            )
            payload = manifest.to_dict()
            self.assertEqual((), manifest_secret_keys(payload))
            self.assertEqual(
                [], [finding.reason for finding in scan_base_image_sources(manifest=payload)]
            )

    def test_scanner_catches_a_planted_secret(self) -> None:
        findings = scan_text_for_secrets(
            "WALLY_ACCESS_TOKEN=sk-abcdefghijklmnopqrstuvwxyz012345\n"
        )
        reasons = {finding.reason for finding in findings}
        self.assertIn("secret_assignment:WALLY_ACCESS_TOKEN", reasons)
        self.assertTrue(any(reason.startswith("secret_value:") for reason in reasons))

    def test_manifest_secret_key_detection_catches_a_planted_key(self) -> None:
        self.assertEqual(
            ("OPENAI_API_KEY",),
            manifest_secret_keys({"OPENAI_API_KEY": "redacted", "sha256": "ab"}),
        )

    def test_secret_key_names_cover_every_vault_mapped_env(self) -> None:
        from factory.core.vault.broker import SERVICE_ENV_MAP

        expected = {
            name for mapping in SERVICE_ENV_MAP.values() for name in mapping
        }
        self.assertTrue(expected.issubset(SECRET_KEY_NAMES))


class ProviderSecretPlanTest(unittest.TestCase):
    """The per-provider decision: proxy (preferred) or documented fallback."""

    def test_wally_route_is_injectable_and_matches_its_base_url(self) -> None:
        route = wally_route({})
        self.assertEqual(PROVIDER_WALLY, route.provider)
        self.assertTrue(route.injectable)
        matched = route_for_base_url(DEFAULT_WALLY_BASE_URL, {})
        self.assertIsNotNone(matched)
        assert matched is not None
        self.assertEqual(route.provider, matched.provider)
        self.assertEqual(route.upstream_base, matched.upstream_base)
        self.assertTrue(matched.injectable)

    def test_unknown_provider_has_no_route_and_is_left_untouched(self) -> None:
        route = route_for_base_url("https://unknown.example/v1", {})
        self.assertIsNone(route)

    def test_every_cc_backend_provider_route_is_injectable(self) -> None:
        from factory.core.codex_provider import OPENCODE_GO_BASE_URL
        from factory.core.isolation.secret_policy import GLM_BASE_URL, MINIMAX_BASE_URL

        expected_header = {
            OPENROUTER_BASE_URL: "Authorization",
            OPENCODE_GO_BASE_URL: "Authorization",
            GLM_BASE_URL: "x-api-key",
            MINIMAX_BASE_URL: "x-api-key",
            DEFAULT_WALLY_BASE_URL: "Authorization",
        }
        for base, header in expected_header.items():
            route = route_for_base_url(base, {})
            self.assertIsNotNone(route, base)
            assert route is not None
            self.assertTrue(route.injectable, base)
            self.assertEqual(header, route.auth_header, base)

    def test_opencode_go_route_is_injectable_and_carries_the_key(self) -> None:
        import os as _os
        from unittest import mock as _mock

        from factory.core.codex_provider import OPENCODE_GO_BASE_URL
        from factory.core.isolation.secret_policy import MODE_PROXY, ProxyEndpoint

        route = route_for_base_url(OPENCODE_GO_BASE_URL, {})
        self.assertIsNotNone(route)
        assert route is not None
        self.assertTrue(route.injectable)
        plan = build_proxy_plan(
            route,
            OPENCODE_GO_BASE_URL,
            endpoint=ProxyEndpoint(bind_host="127.0.0.1", port=4999, guest_host="10.0.2.2"),
        )
        self.assertEqual(MODE_PROXY, plan.mode)
        self.assertEqual("http://10.0.2.2:4999", plan.guest_base_url)
        self.assertEqual(OPENCODE_GO_BASE_URL, plan.upstream_base_url)
        with _mock.patch.dict(_os.environ, {"OPENCODE_API_KEY": "k"}):
            self.assertEqual("k", route.token_provider())

    def test_proxy_plan_rewrites_the_guest_base_url_and_keeps_env_empty(self) -> None:
        route = wally_route({})
        plan = build_proxy_plan(
            route,
            DEFAULT_WALLY_BASE_URL,
            endpoint=ProxyEndpoint(bind_host="127.0.0.1", port=5011, guest_host="10.0.2.2"),
        )
        self.assertEqual(MODE_PROXY, plan.mode)
        self.assertEqual("http://10.0.2.2:5011/v1", plan.guest_base_url)
        self.assertEqual({}, plan.guest_env_map())
        self.assertIsNone(plan.residual)
        self.assertEqual((), plan.guest_env)
        self.assertEqual(
            (), scan_guest_env_for_secrets(plan.guest_env_map(), secret_values=(EXPIRED_TOKEN,))
        )

    def test_fallback_plan_carries_a_documented_residual_entry(self) -> None:
        plan = build_fallback_plan(
            PROVIDER_WALLY,
            DEFAULT_WALLY_BASE_URL,
            reason=REASON_EPHEMERAL_ENV_MODE,
        )
        self.assertEqual(MODE_EPHEMERAL_ENV, plan.mode)
        self.assertEqual(DEFAULT_WALLY_BASE_URL, plan.guest_base_url)
        residual = plan.residual
        self.assertIsNotNone(residual)
        assert residual is not None
        self.assertEqual(PROVIDER_WALLY, residual.subject)
        self.assertTrue(residual.accepted)
        self.assertTrue(residual.risk.strip())
        self.assertTrue(residual.mitigation.strip())
        self.assertEqual(
            [], [finding.reason for finding in scan_text_for_secrets(json.dumps(residual.to_dict()))]
        )

    def test_non_injectable_provider_fallback_records_the_reason(self) -> None:
        plan = build_fallback_plan(
            "openrouter",
            OPENROUTER_BASE_URL,
            reason=REASON_NOT_INJECTABLE,
        )
        assert plan.residual is not None
        self.assertEqual(REASON_NOT_INJECTABLE, plan.residual.reason)

    def test_mcp_credential_servers_have_a_registered_residual(self) -> None:
        self.assertEqual(MODE_EPHEMERAL_ENV, MCP_RESIDUAL_MODE)
        entries = mcp_credential_residual(("tavily", "xsearch", "fal_image"))
        subjects = [entry.subject for entry in entries]
        self.assertEqual(["mcp:tavily", "mcp:xsearch", "mcp:fal_image"], subjects)
        for entry in entries:
            self.assertTrue(entry.accepted)
            self.assertTrue(entry.risk.strip())
            self.assertTrue(entry.mitigation.strip())

    def test_policy_from_env_resolves_modes_and_falls_back_on_garbage(self) -> None:
        self.assertEqual(MODE_AUTO, ProviderSecretPolicy.from_env({}).mode)
        self.assertEqual(
            MODE_EPHEMERAL_ENV,
            ProviderSecretPolicy.from_env({ENV_EGRESS_AUTH: "ephemeral_env"}).mode,
        )
        self.assertEqual(
            MODE_PROXY, ProviderSecretPolicy.from_env({ENV_EGRESS_AUTH: "proxy"}).mode
        )
        with self.assertLogs("factory.core.isolation.secret_policy", level="WARNING"):
            garbage = ProviderSecretPolicy.from_env({ENV_EGRESS_AUTH: "nonsense"})
        self.assertEqual(MODE_AUTO, garbage.mode)
        bound = ProviderSecretPolicy.from_env(
            {ENV_EGRESS_AUTH: "proxy", ENV_EGRESS_AUTH_BIND: "127.0.0.1:5011"}
        )
        self.assertEqual("127.0.0.1", bound.bind_host)
        self.assertEqual(5011, bound.bind_port)


class GuestEnvScanTest(unittest.TestCase):
    """The guest env scan refuses provider token keys and token values."""

    def test_scan_rejects_provider_token_keys(self) -> None:
        leaked = {"PATH": "/usr/bin", "WALLY_ACCESS_TOKEN": "some-token"}
        self.assertEqual(("WALLY_ACCESS_TOKEN",), scan_guest_env_for_secrets(leaked))

    def test_scan_rejects_a_secret_value_under_an_unrelated_key(self) -> None:
        leaked = {"PATH": "/usr/bin", "HARMLESS_SETTING": HOST_INJECTED_TOKEN}
        self.assertEqual(
            ("HARMLESS_SETTING",),
            scan_guest_env_for_secrets(leaked, secret_values=(HOST_INJECTED_TOKEN,)),
        )

    def test_assert_guest_env_clean_raises_without_echoing_the_value(self) -> None:
        with self.assertRaises(GuestEnvLeakError) as caught:
            assert_guest_env_clean({"WALLY_ACCESS_TOKEN": HOST_INJECTED_TOKEN})
        self.assertNotIn(HOST_INJECTED_TOKEN, str(caught.exception))
        self.assertEqual("guest_env_secret_leak", caught.exception.code)

    def test_mask_secret_never_echoes_the_value(self) -> None:
        self.assertEqual("***", mask_secret(HOST_INJECTED_TOKEN))
        self.assertEqual("", mask_secret(""))


class WallyAuthInjectionHardExitTest(unittest.TestCase):
    """Hard exit test: the Wally injection prototype must actually authorize.

    If this test cannot pass, the injection approach is not relied upon: the
    policy is switched to ``IGOREK_ISOLATION_EGRESS_AUTH=ephemeral_env`` and the
    residual risk is recorded instead.
    """

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="igorek-wally-")
        self.root = Path(self._tmp.name)
        wally_auth._CACHE["session"] = None
        wally_auth._CACHE["fetched_at"] = 0.0

    def tearDown(self) -> None:
        wally_auth._CACHE["session"] = None
        wally_auth._CACHE["fetched_at"] = 0.0
        self._tmp.cleanup()

    def _wally_env(self, upstream_base: str) -> dict[str, str]:
        return {
            wally_auth.WALLY_ACCESS_TOKEN_ENV: EXPIRED_TOKEN,
            wally_auth.WALLY_EXPIRES_AT_ENV: str(int(time.time()) - 60),
            wally_auth.WALLY_REFRESH_TOKEN_ENV: REFRESH_TOKEN,
            wally_auth.WALLY_CONSOLE_URL_ENV: upstream_base,
            wally_auth.WALLY_CREDENTIALS_ENV: str(self.root / "credentials.json"),
        }

    def test_proxy_refreshes_the_expired_session_and_injects_authorization(self) -> None:
        upstream = FakeWallyUpstream()
        self.addCleanup(upstream.close)
        logs: list[str] = []
        with patch.dict(os.environ, self._wally_env(upstream.base_url), clear=False):
            proxy = EgressAuthProxy(
                ProxyConfig(
                    upstream_base=upstream.base_url,
                    token_provider=wally_auth.get_wally_access_token,
                    log_fn=logs.append,
                )
            )
            proxy.start()
            self.addCleanup(proxy.stop)

            # The guest side: a plain client that holds no provider token at all.
            guest_env = {"PATH": "/usr/bin", "HOME": "/home/agent"}
            self.assertEqual(
                (),
                scan_guest_env_for_secrets(
                    guest_env, secret_values=(EXPIRED_TOKEN, REFRESH_TOKEN, FRESH_TOKEN)
                ),
            )
            status, body = post_json(
                f"http://127.0.0.1:{proxy.port}/{proxy.capability}/v1/chat/completions",
                {"model": "glm-5.3-flash", "messages": [{"role": "user", "content": "ping"}]},
            )

        self.assertEqual(200, status)
        self.assertEqual("pong", json.loads(body)["choices"][0]["message"]["content"])
        self.assertEqual(1, upstream.refresh_calls)
        self.assertEqual([f"Bearer {FRESH_TOKEN}"], upstream.completion_authorizations)
        for secret in (EXPIRED_TOKEN, REFRESH_TOKEN, FRESH_TOKEN):
            self.assertFalse(any(secret in line for line in logs), logs)

    def test_proxy_streams_the_upstream_body_without_buffering(self) -> None:
        import socket as _socket
        import threading
        import time as _time
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

        first = b"data: first\n\n"
        second = b"data: second\n\n"

        class _Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *args: object) -> None:
                return None

            def do_POST(self) -> None:
                length = int(self.headers.get("Content-Length") or 0)
                self.rfile.read(length)
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Content-Length", str(len(first) + len(second)))
                self.end_headers()
                self.wfile.write(first)
                self.wfile.flush()
                _time.sleep(1.5)
                self.wfile.write(second)
                self.wfile.flush()

        upstream = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        threading.Thread(target=upstream.serve_forever, daemon=True).start()
        self.addCleanup(upstream.server_close)
        self.addCleanup(upstream.shutdown)

        proxy = EgressAuthProxy(
            ProxyConfig(
                upstream_base=f"http://127.0.0.1:{upstream.server_address[1]}",
                token_provider=lambda: "k",
            )
        )
        proxy.start()
        self.addCleanup(proxy.stop)

        sock = _socket.create_connection(("127.0.0.1", proxy.port), timeout=5)
        self.addCleanup(sock.close)
        body = b'{"m":1}'
        sock.sendall(
            (f"POST /{proxy.capability}/v1/chat/completions HTTP/1.1\r\nHost: x\r\n"
             f"Content-Type: application/json\r\nContent-Length: {len(body)}\r\n\r\n").encode()
            + body
        )
        started = _time.monotonic()
        received = b""
        while first not in received and _time.monotonic() - started < 1.0:
            received += sock.recv(4096)
        self.assertIn(first, received, "the first chunk must arrive before the upstream finishes")
        self.assertNotIn(second, received, "the buffered body must not have been sent yet")
        while second not in received:
            chunk = sock.recv(4096)
            if not chunk:
                break
            received += chunk
        self.assertIn(second, received)

    def test_proxy_strips_guest_authorization_and_replaces_it_host_side(self) -> None:
        upstream = FakeWallyUpstream(accepted_token=HOST_INJECTED_TOKEN)
        self.addCleanup(upstream.close)
        with patch.dict(os.environ, self._wally_env(upstream.base_url), clear=False):
            proxy = EgressAuthProxy(
                ProxyConfig(
                    upstream_base=upstream.base_url,
                    token_provider=lambda: HOST_INJECTED_TOKEN,
                )
            )
            proxy.start()
            self.addCleanup(proxy.stop)
            status, _body = post_json(
                f"http://127.0.0.1:{proxy.port}/{proxy.capability}/v1/chat/completions",
                {"model": "glm-5.3-flash", "messages": []},
                headers={"Authorization": "Bearer guest-supplied", "x-api-key": "guest-key"},
            )

        self.assertEqual(200, status)
        self.assertEqual([f"Bearer {HOST_INJECTED_TOKEN}"], upstream.completion_authorizations)
        self.assertEqual(["/v1/chat/completions"], upstream.seen_paths)

    def test_injector_proxy_mode_returns_a_plan_with_no_guest_credentials(self) -> None:
        injector = ProviderAuthInjector(
            ProviderSecretPolicy(mode=MODE_PROXY),
            token_provider=lambda: HOST_INJECTED_TOKEN,
        )
        self.addCleanup(injector.close)
        plan = injector.plan(DEFAULT_WALLY_BASE_URL)
        assert plan is not None
        self.assertEqual(MODE_PROXY, plan.mode)
        self.assertEqual({}, plan.guest_env_map())
        self.assertTrue(plan.guest_base_url.startswith("http://10.0.2.2:"))
        self.assertTrue(plan.guest_base_url.endswith("/v1"))
        self.assertEqual((), scan_guest_env_for_secrets(plan.guest_env_map()))

    def test_proxy_unreachable_fails_clear_without_leaking_a_key(self) -> None:
        upstream = FakeWallyUpstream()
        self.addCleanup(upstream.close)
        with patch.dict(os.environ, self._wally_env(upstream.base_url), clear=False):
            proxy = EgressAuthProxy(
                ProxyConfig(upstream_base=upstream.base_url, token_provider=lambda: HOST_INJECTED_TOKEN)
            )
            proxy.start()
            port = proxy.port
            proxy.stop()
            guest_env = {"PATH": "/usr/bin"}
            with self.assertRaises(urllib.error.URLError):
                post_json(
                    f"http://127.0.0.1:{port}/v1/chat/completions",
                    {"model": "glm-5.3-flash", "messages": []},
                )
        self.assertEqual(
            (),
            scan_guest_env_for_secrets(
                guest_env, secret_values=(EXPIRED_TOKEN, REFRESH_TOKEN, FRESH_TOKEN)
            ),
        )
        self.assertEqual([], upstream.completion_authorizations)

    def test_proxy_never_carries_the_token_through_a_redirect(self) -> None:
        exfil = RecordingServer()
        self.addCleanup(exfil.close)
        redirector = RedirectingServer(f"{exfil.base_url}/steal")
        self.addCleanup(redirector.close)
        proxy = EgressAuthProxy(
            ProxyConfig(
                upstream_base=redirector.base_url,
                token_provider=lambda: HOST_INJECTED_TOKEN,
            )
        )
        proxy.start()
        self.addCleanup(proxy.stop)
        status, _body = post_json_raw(
            proxy.port, f"/{proxy.capability}/v1/chat/completions", {"model": "glm-5.3-flash", "messages": []}
        )
        self.assertEqual(302, status)
        self.assertEqual([], exfil.requests)

    def test_unstartable_proxy_is_a_structured_failure_with_no_guest_key(self) -> None:
        injector = ProviderAuthInjector(
            ProviderSecretPolicy(mode=MODE_PROXY, bind_host="192.0.2.1"),
            token_provider=lambda: HOST_INJECTED_TOKEN,
        )
        self.addCleanup(injector.close)
        with self.assertRaises(IsolationError) as caught:
            injector.plan(DEFAULT_WALLY_BASE_URL)
        self.assertEqual("egress_auth_proxy_unavailable", caught.exception.code)
        self.assertNotIn(HOST_INJECTED_TOKEN, str(caught.exception))


class BrokerOpPolicySeamTest(unittest.TestCase):
    """The todo-19 broker policy seam: host-side, approval-gated, audited."""

    def test_unknown_operation_is_denied_and_audited(self) -> None:
        policy = HostOpPolicy.from_host_env({})
        decision = evaluate_host_op_request("shell", policy=policy, approval_granted=True)
        self.assertFalse(decision.allowed)
        self.assertEqual("unknown_operation", decision.reason)
        record = host_op_audit_record(decision)
        self.assertEqual(CcTraceEvent.ISOLATION_HOST_OP, record["kind"])
        self.assertFalse(record["allowed"])

    def test_allowed_operation_requires_approval_then_is_audited(self) -> None:
        policy = HostOpPolicy.from_host_env({})
        pending = evaluate_host_op_request(
            "runtime.restart", policy=policy, approval_granted=False
        )
        self.assertFalse(pending.allowed)
        self.assertTrue(pending.requires_approval)
        self.assertEqual("approval_required", pending.reason)
        granted = evaluate_host_op_request(
            "runtime.restart", policy=policy, approval_granted=True
        )
        self.assertTrue(granted.allowed)
        self.assertTrue(granted.audited)
        self.assertTrue(host_op_audit_record(granted)["allowed"])

    def test_guest_supplied_policy_is_rejected(self) -> None:
        guest_policy = HostOpPolicy(
            allowed_ops=frozenset({"runtime.restart"}), source="guest_payload"
        )
        decision = evaluate_host_op_request(
            "runtime.restart", policy=guest_policy, approval_granted=True
        )
        self.assertFalse(decision.allowed)
        self.assertEqual("guest_policy_rejected", decision.reason)
        self.assertEqual(HOST_POLICY_SOURCE, HostOpPolicy.from_host_env({}).source)


class FakeDomainHandle:
    def __init__(self, name: str, cid: int = 42) -> None:
        self._name = name
        self._cid = cid
        self.active = False
        self.create_calls = 0
        self.destroy_calls = 0

    def name(self) -> str:
        return self._name

    def create(self) -> int:
        self.create_calls += 1
        self.active = True
        return 0

    def destroy(self) -> int:
        self.destroy_calls += 1
        self.active = False
        return 0

    def shutdown(self) -> int:
        return 0

    def undefine(self) -> int:
        return 0

    def isActive(self) -> int:  # noqa: N802 - libvirt API
        return 1 if self.active else 0

    def XMLDesc(self, flags: int = 0) -> str:  # noqa: N802 - libvirt API
        return (
            "<domain><devices><vsock model='virtio'>"
            f"<cid auto='yes' address='{self._cid}'/></vsock></devices></domain>"
        )


class FakeConnection:
    def __init__(self) -> None:
        self.defined: dict[str, FakeDomainHandle] = {}

    def defineXML(self, xml: str) -> FakeDomainHandle:  # noqa: N802 - libvirt API
        import xml.etree.ElementTree as ET

        name = ET.fromstring(xml).findtext("name") or ""
        handle = FakeDomainHandle(name, 42 + len(self.defined))
        self.defined[name] = handle
        return handle

    def lookupByName(self, name: str) -> FakeDomainHandle:  # noqa: N802 - libvirt API
        return self.defined[name]

    def close(self) -> None:
        return None


class FakeEndpoint:
    def __init__(self, messages: list[ChannelMessage] | None = None) -> None:
        self.sent: list[tuple[MessageKind, dict[str, Any]]] = []
        self._messages = list(messages or [])

    def send(self, kind: MessageKind, payload: dict[str, Any]) -> None:
        self.sent.append((kind, dict(payload)))

    def recv_from_guest(self, timeout_s: float | None = None) -> ChannelMessage | None:
        return self._messages.pop(0) if self._messages else None

    def close(self) -> None:
        return None


class FakeRegistry:
    def __init__(self, endpoints: dict[str, FakeEndpoint] | None = None) -> None:
        self.endpoints = dict(endpoints or {})

    def session_for(self, session_id: Any) -> FakeEndpoint | None:
        return self.endpoints.get(str(session_id))

    def close(self) -> None:
        return None


class ManagerSecretSeamTest(unittest.TestCase):
    """The manager seam: proxy mode rewrites base_url, default is unchanged."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="igorek-secret-seam-")
        self.root = Path(self._tmp.name)
        self.workspace = self.root / "workspace"
        self.workspace.mkdir()
        self.registry = FakeRegistry()
        self.connection = FakeConnection()
        self.endpoint = FakeEndpoint(
            messages=[
                ChannelMessage(
                    kind=MessageKind.RESULT,
                    seq=1,
                    payload={
                        "status": "ok",
                        "output_text": "done",
                        "usage": {},
                        "tool_calls_count": 0,
                    },
                )
            ]
        )
        self.registry.endpoints["sess-1"] = self.endpoint
        self.artifact = self.root / "artifacts"
        self.artifact.mkdir()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _config(self) -> IsolationConfig:
        base = self.root / "base.qcow2"
        base.write_bytes(b"qcow2-placeholder")
        config = IsolationConfig.from_env(
            {
                "IGOREK_ISOLATION_MODE": MODE_KVM,
                "IGOREK_ISOLATION_BASE_IMAGE": str(base),
                "IGOREK_ISOLATION_OVERLAY_DIR": str(self.root / "overlays"),
                "IGOREK_ISOLATION_VSOCK_PORT": "5005",
            },
            data_root=self.root,
            capability_probe=lambda uri: HostCapabilities(True, True, True, True),
        )
        assert config.enabled, config.reason
        return config

    def _manager(self, injector: ProviderAuthInjector | None = None) -> IsolationManager:
        policy = WorkspacePolicy(
            home=self.root / "home", data_root=self.root / "data", host_uid=os.getuid()
        )

        def preflight_fn(cfg: IsolationConfig) -> PreflightResult:
            return PreflightResult(ok=True, mode=cfg.mode, reason=REASON_OK)

        return IsolationManager(
            self._config(),
            seams=ManagerSeams(
                connect=lambda uri: self.connection,
                runner=lambda argv: CommandResult(0),
                channel=self.registry,
                session_map=SessionMap(),
                workspace_policy=policy,
                preflight_fn=preflight_fn,
                provider_secrets=injector,
            ),
        )

    def _payload(self) -> dict[str, Any]:
        return {
            "task_id": "task-1",
            "profile": "cc-wally",
            "model": "glm-5.3-flash",
            "base_url": DEFAULT_WALLY_BASE_URL,
            "api_format": "openai",
            "system_prompt": "system",
            "user_message": "user",
            "skills_text": "",
            "foundation_context": "",
            "filesystem_mode": "workspace",
            "guard_mode": "on",
            "max_tokens": 512,
            "timeout_s": 30,
            "artifact_workdir": str(self.artifact),
            "input_files": [],
            "history_messages": [],
        }

    def test_without_the_seam_the_base_url_is_unchanged(self) -> None:
        manager = self._manager()
        result = manager.run_task(
            {"task_id": "task-1", "metadata": {"session": "sess-1"}},
            str(self.workspace),
            self._payload(),
        )
        self.assertEqual("ok", result["status"])
        _kind, frame = self.endpoint.sent[0]
        self.assertEqual(DEFAULT_WALLY_BASE_URL, frame["base_url"])

    def test_proxy_seam_rewrites_base_url_and_records_the_decision(self) -> None:
        injector = ProviderAuthInjector(
            ProviderSecretPolicy(mode=MODE_PROXY),
            token_provider=lambda: HOST_INJECTED_TOKEN,
        )
        self.addCleanup(injector.close)
        manager = self._manager(injector)
        result = manager.run_task(
            {"task_id": "task-1", "metadata": {"session": "sess-1"}},
            str(self.workspace),
            self._payload(),
        )
        self.assertEqual("ok", result["status"])
        _kind, frame = self.endpoint.sent[0]
        self.assertNotEqual(DEFAULT_WALLY_BASE_URL, frame["base_url"])
        self.assertTrue(str(frame["base_url"]).startswith("http://10.0.2.2:"))
        serialized = json.dumps(frame)
        for secret in (EXPIRED_TOKEN, REFRESH_TOKEN, FRESH_TOKEN, HOST_INJECTED_TOKEN):
            self.assertNotIn(secret, serialized)
        trace = (self.artifact / "cc_backend_trace.jsonl").read_text(encoding="utf-8")
        self.assertIn(CcTraceEvent.ISOLATION_PROVIDER_AUTH_PROXIED, trace)

    def test_proxy_start_failure_fails_clear_and_sends_no_frame(self) -> None:
        injector = ProviderAuthInjector(
            ProviderSecretPolicy(mode=MODE_PROXY, bind_host="192.0.2.1"),
            token_provider=lambda: HOST_INJECTED_TOKEN,
        )
        self.addCleanup(injector.close)
        manager = self._manager(injector)
        with self.assertRaises(IsolationError) as caught:
            manager.run_task(
                {"task_id": "task-1", "metadata": {"session": "sess-1"}},
                str(self.workspace),
                self._payload(),
            )
        self.assertEqual("egress_auth_proxy_unavailable", caught.exception.code)
        self.assertNotIn(HOST_INJECTED_TOKEN, str(caught.exception))
        self.assertEqual([], self.endpoint.sent)


if __name__ == "__main__":
    unittest.main()
