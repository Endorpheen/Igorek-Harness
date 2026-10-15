"""Per-provider credential placement decision for kvm sessions (plan todo 18).

The preferred path is **host-side egress auth injection**: the guest speaks to a
host proxy that adds ``Authorization`` itself, so no long-lived provider key
ever enters the VM. The prototype covers Wally - the hardest case, because the
access token is short-lived and ``factory.core.wally_auth`` refreshes it through
``/auth/cli/refresh`` - and the injection is only relied upon after the hard
exit test in ``tests/test_isolation_secrets.py`` passes.

Providers that are not injectable fall back to the existing host-side ephemeral
Vault materialization (``worker._materialize_vault_env``) and carry a
:class:`ResidualRisk` entry: the fallback is always explicit, never silent.
MCP credential-bearing servers have their own registered residual (the todo-31
placement decision puts their ephemeral env in the guest server spec).

Mode resolution (``IGOREK_ISOLATION_EGRESS_AUTH``):

* ``auto`` (default) - inject for every provider with a route whose
  ``injectable`` flag is set (Wally today); everything else falls back;
* ``proxy`` - same, but an injectable provider without a token provider is
  refused rather than degraded (a base URL with no known route yields no plan);
* ``ephemeral_env`` - force the documented fallback for every provider.

The bind/guest-host settings come from the host environment only
(``IGOREK_ISOLATION_EGRESS_AUTH_BIND``, ``IGOREK_ISOLATION_EGRESS_AUTH_GUEST_HOST``);
nothing in a guest payload can influence this decision.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Final
from urllib.parse import urlsplit
from uuid import uuid4

from ..codex_provider import OPENCODE_GO_BASE_URL, OPENROUTER_BASE_URL
from ..trace_taxonomy import CcTraceEvent
from ..wally_auth import (
    WALLY_CONSOLE_URL_ENV,
    WALLY_DEFAULT_CONSOLE_URL,
    get_wally_access_token,
)
from .extensions import CapabilityStatus
from .network import HOST_LOOPBACK_ALIAS
from .secret_residual import ResidualRisk, mcp_credential_residual, provider_residual

logger = logging.getLogger(__name__)

__all__ = [
    "ENV_EGRESS_AUTH",
    "ENV_EGRESS_AUTH_BIND",
    "ENV_EGRESS_AUTH_GUEST_HOST",
    "MODE_AUTO",
    "MODE_EPHEMERAL_ENV",
    "MODE_PROXY",
    "PROVIDER_WALLY",
    "REASON_EPHEMERAL_ENV_MODE",
    "REASON_NOT_INJECTABLE",
    "REASON_PROXY_UNAVAILABLE",
    "ProviderRoute",
    "ProviderSecretPlan",
    "ProviderSecretPolicy",
    "ProxyEndpoint",
    "ResidualRisk",
    "build_fallback_plan",
    "build_proxy_plan",
    "mcp_credential_residual",
    "provider_routes",
    "route_for_base_url",
    "wally_route",
]

PROVIDER_WALLY: Final[str] = "wally"

MODE_AUTO: Final[str] = "auto"
MODE_PROXY: Final[str] = "proxy"
MODE_EPHEMERAL_ENV: Final[str] = "ephemeral_env"
VALID_MODES: Final[frozenset[str]] = frozenset({MODE_AUTO, MODE_PROXY, MODE_EPHEMERAL_ENV})

ENV_EGRESS_AUTH: Final[str] = "IGOREK_ISOLATION_EGRESS_AUTH"
ENV_EGRESS_AUTH_BIND: Final[str] = "IGOREK_ISOLATION_EGRESS_AUTH_BIND"
ENV_EGRESS_AUTH_GUEST_HOST: Final[str] = "IGOREK_ISOLATION_EGRESS_AUTH_GUEST_HOST"

REASON_EPHEMERAL_ENV_MODE: Final[str] = "ephemeral_env_mode_forced"
REASON_NOT_INJECTABLE: Final[str] = "provider_not_injectable"
REASON_PROXY_UNAVAILABLE: Final[str] = "egress_auth_proxy_unavailable"

DEFAULT_BIND_HOST: Final[str] = "127.0.0.1"
DEFAULT_BIND_PORT: Final[int] = 0


@dataclass(frozen=True, slots=True)
class ProviderRoute:
    """One provider endpoint the host knows how to reach.

    ``injectable`` is set only for providers whose credential flow the
    host-side proxy has been proven to serve (Wally today); ``path_suffix`` is
    the API path the guest's base URL must keep (``/v1`` for Wally's
    OpenAI-compatible surface).
    """

    provider: str
    upstream_base: str
    injectable: bool
    path_suffix: str = ""
    token_provider: Callable[[], str] | None = None
    extra_headers: tuple[tuple[str, str], ...] = ()
    auth_header: str = "Authorization"
    auth_prefix: str = "Bearer "


@dataclass(frozen=True, slots=True)
class ProviderSecretPolicy:
    """Host-side policy: how provider credentials are placed for kvm sessions."""

    mode: str = MODE_AUTO
    bind_host: str = DEFAULT_BIND_HOST
    bind_port: int = DEFAULT_BIND_PORT
    guest_host: str = HOST_LOOPBACK_ALIAS
    timeout_s: float = 120.0

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> ProviderSecretPolicy:
        source = os.environ if env is None else env
        raw = str(source.get(ENV_EGRESS_AUTH) or "").strip().lower()
        mode = raw or MODE_AUTO
        if mode not in VALID_MODES:
            logger.warning(
                "Unknown %s=%r; using %s", ENV_EGRESS_AUTH, raw, MODE_AUTO
            )
            mode = MODE_AUTO
        bind_host, bind_port = _parse_bind(source.get(ENV_EGRESS_AUTH_BIND))
        guest_host = (
            str(source.get(ENV_EGRESS_AUTH_GUEST_HOST) or "").strip()
            or HOST_LOOPBACK_ALIAS
        )
        return cls(
            mode=mode,
            bind_host=bind_host,
            bind_port=bind_port,
            guest_host=guest_host,
        )


@dataclass(frozen=True, slots=True)
class ProxyEndpoint:
    """Where the host proxy listens and how the guest addresses it."""

    bind_host: str
    port: int
    guest_host: str
    capability: str = ""


@dataclass(frozen=True, slots=True)
class ProviderSecretPlan:
    """The decision for one dispatch: proxy (no guest key) or documented fallback."""

    provider: str
    mode: str
    upstream_base_url: str
    guest_base_url: str
    guest_env: tuple[tuple[str, str], ...] = ()
    residual: ResidualRisk | None = None
    proxy_bind: tuple[str, int] | None = None

    def guest_env_map(self) -> dict[str, str]:
        return {key: value for key, value in self.guest_env}

    def capability_status(self) -> CapabilityStatus:
        """The taxonomy-shaped status the host trace records for this dispatch."""
        if self.mode == MODE_PROXY:
            return CapabilityStatus(
                CcTraceEvent.ISOLATION_PROVIDER_AUTH_PROXIED,
                self.provider,
                "host_egress_auth_injection",
                detail=f"guest_origin={urlsplit(self.guest_base_url).scheme}://{urlsplit(self.guest_base_url).netloc}",
            )
        reason = self.residual.reason if self.residual is not None else "unknown"
        return CapabilityStatus(
            CcTraceEvent.ISOLATION_PROVIDER_AUTH_RESIDUAL,
            self.provider,
            reason,
            detail=f"mode={self.mode}",
        )


GLM_BASE_URL: Final[str] = "https://api.z.ai/api/anthropic"
MINIMAX_BASE_URL: Final[str] = "https://api.minimax.io/anthropic"


def wally_route(env: Mapping[str, str] | None = None) -> ProviderRoute:
    """The Wally route, with its console URL resolved from the host environment."""
    source = os.environ if env is None else env
    base = str(source.get(WALLY_CONSOLE_URL_ENV) or "").strip() or WALLY_DEFAULT_CONSOLE_URL
    return ProviderRoute(
        provider=PROVIDER_WALLY,
        upstream_base=base.rstrip("/"),
        injectable=True,
        path_suffix="/v1",
        token_provider=get_wally_access_token,
    )


def _opencode_go_token() -> str:
    return os.environ.get("OPENCODE_API_KEY", "").strip()


def _opencode_extra_headers() -> tuple[tuple[str, str], ...]:
    """The non-secret headers the opencode gateway needs for routing."""
    return (
        ("User-Agent", "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36"),
        ("Origin", "https://opencode.ai"),
        ("x-opencode-session", uuid4().hex),
    )


def opencode_go_route(env: Mapping[str, str] | None = None) -> ProviderRoute:
    """The opencode (zen) route: the host injects OPENCODE_API_KEY for the guest.

    ``OPENCODE_GO_BASE_URL`` carries the full ``/zen/go/v1`` path, so the route
    keeps an empty suffix and the guest's base URL is the proxy root; the proxy
    forwards ``<path>`` onto that base and adds the gateway's routing headers.
    """
    return ProviderRoute(
        provider="opencode-go",
        upstream_base=OPENCODE_GO_BASE_URL,
        injectable=True,
        path_suffix="",
        token_provider=_opencode_go_token,
        extra_headers=_opencode_extra_headers(),
    )


def _first_env(*names: str) -> str:
    for name in names:
        value = os.environ.get(name, "").strip()
        if value:
            return value
    return ""


def _openrouter_token() -> str:
    return _first_env("OPENROUTER_API_KEY", "CODEX_OPENROUTER_API_KEY", "FACTORY_OPENROUTER_API_KEY")


def _glm_token() -> str:
    return _first_env("ANTHROPIC_AUTH_TOKEN", "GLM_API_KEY")


def _minimax_token() -> str:
    return _first_env("ANTHROPIC_AUTH_TOKEN", "MINIMAX_API_KEY")


def openrouter_route(env: Mapping[str, str] | None = None) -> ProviderRoute:
    """The OpenRouter route: a bearer key injected by the host proxy."""
    return ProviderRoute(
        provider="openrouter",
        upstream_base=OPENROUTER_BASE_URL,
        injectable=True,
        path_suffix="",
        token_provider=_openrouter_token,
    )


def glm_route(env: Mapping[str, str] | None = None) -> ProviderRoute:
    """The z.ai (GLM) Anthropic-compatible route: an ``x-api-key``."""
    return ProviderRoute(
        provider="glm",
        upstream_base=GLM_BASE_URL,
        injectable=True,
        path_suffix="",
        token_provider=_glm_token,
        auth_header="x-api-key",
        auth_prefix="",
    )


def minimax_route(env: Mapping[str, str] | None = None) -> ProviderRoute:
    """The MiniMax Anthropic-compatible route: an ``x-api-key``."""
    return ProviderRoute(
        provider="minimax",
        upstream_base=MINIMAX_BASE_URL,
        injectable=True,
        path_suffix="",
        token_provider=_minimax_token,
        auth_header="x-api-key",
        auth_prefix="",
    )


def provider_routes(env: Mapping[str, str] | None = None) -> tuple[ProviderRoute, ...]:
    """Known provider endpoints; each injectable one is served by the host proxy."""
    return (
        wally_route(env),
        openrouter_route(env),
        opencode_go_route(env),
        glm_route(env),
        minimax_route(env),
    )


def route_for_base_url(
    base_url: str, env: Mapping[str, str] | None = None
) -> ProviderRoute | None:
    """The provider route whose host matches ``base_url`` (host-only match)."""
    host = urlsplit(str(base_url or "")).netloc.lower()
    if not host:
        return None
    for route in provider_routes(env):
        if urlsplit(route.upstream_base).netloc.lower() == host:
            return route
    return None


def build_proxy_plan(
    route: ProviderRoute,
    base_url: str,
    *,
    endpoint: ProxyEndpoint,
) -> ProviderSecretPlan:
    """The injection plan: guest base URL points at the host proxy, no env at all."""
    if not route.injectable:
        raise ValueError(f"provider {route.provider!r} is not injectable")
    return ProviderSecretPlan(
        provider=route.provider,
        mode=MODE_PROXY,
        upstream_base_url=route.upstream_base,
        guest_base_url=(f"http://{endpoint.guest_host}:{endpoint.port}"
                        + (f"/{endpoint.capability}" if endpoint.capability else "")
                        + route.path_suffix),
        guest_env=(),
        residual=None,
        proxy_bind=(endpoint.bind_host, endpoint.port),
    )


def build_fallback_plan(
    provider: str,
    base_url: str,
    *,
    reason: str,
) -> ProviderSecretPlan:
    """The documented fallback: base URL unchanged, residual risk recorded.

    This module never materializes a credential - the existing host-side
    ``worker._materialize_vault_env`` window is the credential path - so the
    plan carries no env values and states the residual instead.
    """
    residual = provider_residual(provider, reason, mode=MODE_EPHEMERAL_ENV)
    return ProviderSecretPlan(
        provider=provider,
        mode=MODE_EPHEMERAL_ENV,
        upstream_base_url=base_url,
        guest_base_url=base_url,
        guest_env=(),
        residual=residual,
    )


def _parse_bind(raw: object) -> tuple[str, int]:
    text = str(raw or "").strip()
    if not text:
        return DEFAULT_BIND_HOST, DEFAULT_BIND_PORT
    host, separator, port_text = text.rpartition(":")
    if not separator:
        logger.warning("Invalid %s=%r; using the default bind", ENV_EGRESS_AUTH_BIND, raw)
        return DEFAULT_BIND_HOST, DEFAULT_BIND_PORT
    try:
        port = int(port_text, 10)
    except ValueError:
        port = -1
    if not host or not 0 <= port <= 65535:
        logger.warning("Invalid %s=%r; using the default bind", ENV_EGRESS_AUTH_BIND, raw)
        return DEFAULT_BIND_HOST, DEFAULT_BIND_PORT
    return host, port
