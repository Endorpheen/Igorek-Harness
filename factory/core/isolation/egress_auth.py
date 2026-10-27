"""Host-side egress auth injection for kvm sessions (plan todo 18).

The guest calls a provider through this host proxy; the proxy strips whatever
authorization the guest sent, asks the host-side token provider for a *valid*
credential (for Wally: ``wally_auth.get_wally_access_token``, which refreshes
the JWT through ``/auth/cli/refresh`` when the session is expired) and forwards
the request with that ``Authorization`` header. The guest therefore needs no
provider key at all, and a provider call it makes is authorized by the host.

Fail-closed rules:

* a guest-supplied ``Authorization``/``x-api-key`` is dropped, never forwarded;
* a token that cannot be obtained, an unreachable upstream or a body above the
  size cap produces a structured 502/413 error - the proxy never falls back to
  an unauthenticated upstream call and never echoes a secret;
* log lines carry the method, path and status only - never a header or a token.

This module is the prototype boundary: it is wired into the manager only through
the opt-in ``ManagerSeams.provider_secrets`` seam (the host-local route for the
guest, plan todo 17, and the host-op broker, plan todo 19, land separately).
"""

# allow: SIZE_OK - one cohesive host-side injection boundary (the proxy
# transport, the token injection and the injector that owns the proxy lifecycle);
# splitting the handler from its lifecycle would let the two halves of the same
# security invariant drift apart.

from __future__ import annotations

import json
import logging
import os
import hmac
import secrets
import threading
import urllib.error
import urllib.request
from urllib.parse import urlsplit, urlunsplit
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler
from typing import Final

from ..wally_auth import get_wally_access_token
from ..http_server import BoundedHTTPServer, HeaderDeadlineMixin
from .errors import IsolationError
from .secret_policy import (
    MODE_EPHEMERAL_ENV,
    ProviderRoute,
    ProviderSecretPlan,
    ProviderSecretPolicy,
    ProxyEndpoint,
    REASON_EPHEMERAL_ENV_MODE,
    REASON_NOT_INJECTABLE,
    REASON_PROXY_UNAVAILABLE,
    build_fallback_plan,
    build_proxy_plan,
    route_for_base_url,
)

logger = logging.getLogger(__name__)

__all__ = ["EgressAuthProxy", "ProviderAuthInjector", "ProxyConfig"]

MAX_BODY_BYTES: Final[int] = 32 * 1024 * 1024

_HOP_BY_HOP: Final[frozenset[str]] = frozenset(
    {
        "connection",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "te",
        "trailers",
        "transfer-encoding",
        "upgrade",
    }
)
_DROP_REQUEST_HEADERS: Final[frozenset[str]] = _HOP_BY_HOP | frozenset(
    {
        "host",
        "authorization",
        "x-api-key",
        "x-goog-api-key",
        "accept-encoding",
        "content-length",
    }
)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Never follow a redirect: the injected Authorization stays on the route."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: ANN001
        return None


_OPENER = urllib.request.build_opener(_NoRedirect)


def _upstream_url(base: str, target: str) -> str:
    if (
        not target.startswith("/") or target.startswith("//")
        or "\\" in target or "#" in target
        or any(ord(char) <= 32 or ord(char) == 127 for char in target)
    ):
        raise ValueError("invalid_request_target")
    upstream = urlsplit(base)
    path = urlsplit(target)
    if (
        upstream.scheme not in {"https", "http"} or not upstream.hostname
        or upstream.username is not None or upstream.password is not None
        or upstream.query or upstream.fragment
        or "\\" in base or any(ord(char) <= 32 or ord(char) == 127 for char in base)
        or path.scheme or path.netloc
    ):
        raise ValueError("invalid_upstream_route")
    upstream.port
    url = urlunsplit((upstream.scheme, upstream.netloc,
                     upstream.path.rstrip("/") + path.path, path.query, ""))
    resolved = urlsplit(url)
    if (resolved.scheme, resolved.netloc) != (upstream.scheme, upstream.netloc):
        raise ValueError("upstream_origin_changed")
    return url


@dataclass(frozen=True, slots=True)
class ProxyConfig:
    """One provider route as the host proxy needs it (all host-side values)."""

    upstream_base: str
    token_provider: Callable[[], str] = get_wally_access_token
    bind_host: str = "127.0.0.1"
    bind_port: int = 0
    timeout_s: float = 30.0
    log_fn: Callable[[str], None] | None = None
    extra_headers: tuple[tuple[str, str], ...] = ()
    auth_header: str = "Authorization"
    auth_prefix: str = "Bearer "


class _ProxyHandler(HeaderDeadlineMixin, BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "igorek-egress-auth/1.0"

    def do_GET(self) -> None:  # noqa: N802 - http.server API
        self._forward()

    def do_POST(self) -> None:  # noqa: N802 - http.server API
        self._forward()

    def log_message(self, format: str, *args: object) -> None:
        return None

    def _config(self) -> ProxyConfig:
        server = self.server
        if not isinstance(server, _ProxyServer):
            raise RuntimeError("egress auth handler attached to a foreign server")
        return server.proxy_config

    def _forward(self) -> None:
        config = self._config()
        target = self.path
        parts = target.split('/', 2)
        self.path = '/' + parts[2] if len(parts) == 3 else '/'
        if (len(parts) != 3 or parts[0] or not parts[1].isascii()
                or not hmac.compare_digest(parts[1], self.server.client_capability)):
            self._fail('client_auth_required', 'invalid_proxy_capability', status=401)
            return
        try:
            target = self.path
            url = _upstream_url(config.upstream_base, target)
        except (ValueError, IndexError):
            self._fail("invalid_request", "bad_request_target", status=400)
            return
        body = self._read_body()
        if body is None:
            return
        try:
            token = str(config.token_provider() or "").strip()
        except Exception as exc:  # reported as a clear 502
            self._fail("provider_auth_unavailable", type(exc).__name__)
            return
        if not token:
            self._fail("provider_auth_unavailable", "empty_token")
            return
        headers = {
            name: value
            for name, value in self.headers.items()
            if name.lower() not in _DROP_REQUEST_HEADERS
        }
        for name, value in config.extra_headers:
            headers[name] = value
        headers[config.auth_header] = f"{config.auth_prefix}{token}"
        request = urllib.request.Request(
            url,
            data=body if body else None,
            headers=headers,
            method=self.command,
        )
        try:
            with _OPENER.open(request, timeout=config.timeout_s) as response:
                self._relay_stream(response.status, response.headers, response)
        except urllib.error.HTTPError as exc:
            self._relay_stream(exc.code, exc.headers, exc)
        except (urllib.error.URLError, OSError, TimeoutError) as exc:
            self._fail("upstream_unreachable", type(exc).__name__)

    def _read_body(self) -> bytes | None:
        raw_length = self.headers.get("Content-Length") or "0"
        try:
            length = int(raw_length, 10)
        except ValueError:
            length = -1
        if length < 0:
            self._fail("invalid_request", "bad_content_length", status=400)
            return None
        if length > MAX_BODY_BYTES:
            self._fail("request_too_large", f"content_length={length}", status=413)
            return None
        return self.rfile.read(length) if length else b""

    def _relay_stream(self, status: int, headers: Mapping[str, str], stream: object) -> None:
        """Relay the upstream body incrementally, flushing every chunk.

        The guest's model call streams (SSE), so buffering the whole body would
        hide the answer until the upstream finished. The body is sent
        close-delimited (no Content-Length) and flushed per chunk; the upstream
        Content-Length, when present, only bounds the read loop.
        """
        self.send_response(status)
        self.send_header("Content-Type", headers.get("Content-Type") or "application/json")
        encoding = headers.get("Content-Encoding")
        if encoding:
            self.send_header("Content-Encoding", encoding)
        self.send_header("Cache-Control", "no-store")
        self.close_connection = True
        self.send_header("Connection", "close")
        self.end_headers()
        # read1 returns whatever is already available; read() would block until
        # the whole Content-Length, which is exactly the buffering we avoid.
        read = getattr(stream, "read1", None) or getattr(stream, "read", None)
        if not callable(read):
            self._log(f"{self.command} {self.path} -> {status} (no body)")
            return
        try:
            while True:
                chunk = read(65536)
                if not chunk:
                    break
                self.wfile.write(chunk)
                self.wfile.flush()
        except (OSError, ValueError):
            pass
        self._log(f"{self.command} {self.path} -> {status} (streamed)")

    def _fail(self, code: str, reason: str, *, status: int = 502) -> None:
        payload = json.dumps({"error": code, "reason": reason}).encode("utf-8")
        self.close_connection = True
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)
        self._log(f"{self.command} {self.path} -> {status} {code}")

    def _log(self, message: str) -> None:
        config = self._config()
        if config.log_fn is not None:
            config.log_fn(message)
        else:
            logger.info("egress-auth proxy: %s", message)


class _ProxyServer(BoundedHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, config: ProxyConfig, address: tuple[str, int]) -> None:
        self.proxy_config = config
        self.client_capability = secrets.token_urlsafe(32)
        super().__init__(address, _ProxyHandler)


class EgressAuthProxy:
    """The host half of the injection: a loopback proxy that adds the token."""

    def __init__(self, config: ProxyConfig) -> None:
        self._config = config
        self._server: _ProxyServer | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._server is not None:
            return
        server = _ProxyServer(self._config, (self._config.bind_host, self._config.bind_port))
        self._server = server
        self._thread = threading.Thread(
            target=server.serve_forever, name="igorek-egress-auth", daemon=True
        )
        self._thread.start()

    @property
    def port(self) -> int:
        if self._server is None:
            raise RuntimeError("egress auth proxy is not started")
        return int(self._server.server_address[1])

    @property
    def capability(self) -> str:
        if self._server is None:
            raise RuntimeError("egress auth proxy is not started")
        return self._server.client_capability

    def stop(self) -> None:
        server = self._server
        if server is None:
            return
        server.shutdown()
        server.server_close()
        if self._thread is not None:
            self._thread.join(timeout=5.0)
        self._server = None
        self._thread = None


class ProviderAuthInjector:
    """Owns the host proxy and turns a provider base URL into a secret plan.

    ``plan`` returns ``None`` for a base URL the host has no route for (a local
    model endpoint, an unknown provider): no credential policy applies and the
    payload is left untouched.
    """

    def __init__(
        self,
        policy: ProviderSecretPolicy | None = None,
        *,
        env: Mapping[str, str] | None = None,
        token_provider: Callable[[], str] | None = None,
    ) -> None:
        self._policy = policy if policy is not None else ProviderSecretPolicy()
        self._env = os.environ if env is None else env
        self._token_provider = token_provider
        self._proxy: EgressAuthProxy | None = None

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> ProviderAuthInjector:
        return cls(ProviderSecretPolicy.from_env(env), env=env)

    def plan(self, base_url: str) -> ProviderSecretPlan | None:
        route = route_for_base_url(base_url, self._env)
        if route is None:
            return None
        if not route.injectable:
            return build_fallback_plan(
                route.provider,
                base_url,
                reason=REASON_NOT_INJECTABLE,
            )
        if self._policy.mode == MODE_EPHEMERAL_ENV:
            return build_fallback_plan(
                route.provider,
                base_url,
                reason=REASON_EPHEMERAL_ENV_MODE,
            )
        proxy = self.ensure_proxy(route)
        return build_proxy_plan(
            route,
            base_url,
            endpoint=ProxyEndpoint(
                bind_host=self._policy.bind_host,
                port=proxy.port,
                guest_host=self._policy.guest_host,
                capability=proxy.capability,
            ),
        )

    def ensure_proxy(self, route: ProviderRoute) -> EgressAuthProxy:
        """Start (once) the host proxy for ``route``; a bind failure is fail-closed."""
        if self._proxy is not None:
            return self._proxy
        provider = self._token_provider or route.token_provider
        if provider is None:
            raise IsolationError(
                REASON_PROXY_UNAVAILABLE, f"{route.provider}:no_token_provider"
            )
        config = ProxyConfig(
            upstream_base=route.upstream_base,
            token_provider=provider,
            bind_host=self._policy.bind_host,
            bind_port=self._policy.bind_port,
            timeout_s=self._policy.timeout_s,
            extra_headers=route.extra_headers,
            auth_header=route.auth_header,
            auth_prefix=route.auth_prefix,
        )
        proxy = EgressAuthProxy(config)
        try:
            proxy.start()
        except OSError as exc:
            raise IsolationError(
                REASON_PROXY_UNAVAILABLE, f"{route.provider}:{type(exc).__name__}"
            ) from exc
        self._proxy = proxy
        return proxy

    def close(self) -> None:
        if self._proxy is not None:
            self._proxy.stop()
            self._proxy = None
