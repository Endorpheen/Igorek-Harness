"""Configuration sources for the per-session egress allowlist (plan todo 16).

This module owns the only place where a *configured* endpoint is read and
turned into a candidate allow entry: the provider base URLs taken from the code
constants, the MCP server table for the servers named in ``MCP_SERVERS``, the
baseline hosts, the Boltbook flag and the operator extra list. It also owns the
one network boundary - resolving a hostname to the IPs that will be pinned into
the nftables ruleset - which is why the resolver is injected everywhere.

An endpoint that cannot be parsed, a server missing from the table or a
non-Ip DNS server never raises: it yields a warning for the caller to surface.
"""

from __future__ import annotations

import ipaddress
import logging
import re
import socket
from collections.abc import Callable, Mapping, Sequence
from types import MappingProxyType
from typing import Final
from urllib.parse import urlsplit

from ..codex_provider import OPENCODE_GO_BASE_URL, OPENROUTER_BASE_URL
from ..wally_auth import WALLY_DEFAULT_CONSOLE_URL

logger = logging.getLogger(__name__)

__all__ = [
    "BASELINE_ALLOW_HOSTS",
    "BOLTBOOK_HOST",
    "DEFAULT_ALLOW_PORTS",
    "DEFAULT_DNS_SERVERS",
    "ENV_BOLTBOOK_HEARTBEAT",
    "ENV_DNS_SERVERS",
    "ENV_EGRESS_EXTRA_ALLOW",
    "ENV_GREP_APP_URL",
    "ENV_LOCAL_INFERENCE",
    "ENV_MCP_SERVERS",
    "HOST_LOOPBACK_HOSTS",
    "LOCAL_INFERENCE_GATE_AUTO",
    "LOCAL_INFERENCE_GATE_OFF",
    "LOCAL_INFERENCE_GATE_ON",
    "MCP_ENDPOINT_HOSTS",
    "PROVIDER_BASE_URLS",
    "VALID_LOCAL_INFERENCE_GATES",
    "HostResolver",
    "boltbook_enabled",
    "endpoint_from_extra",
    "endpoint_from_url",
    "is_host_loopback",
    "is_local_host",
    "is_public",
    "local_inference_gate",
    "mcp_hosts",
    "pin_addresses",
    "resolve_dns_servers",
    "split_csv",
    "system_resolver",
    "valid_host",
]

DEFAULT_ALLOW_PORTS: Final[frozenset[int]] = frozenset({443})
DEFAULT_DNS_SERVERS: Final[tuple[str, ...]] = ("10.0.2.3",)

BASELINE_ALLOW_HOSTS: Final[tuple[str, ...]] = (
    "pypi.org",
    "files.pythonhosted.org",
    "api.github.com",
    "api.telegram.org",
)

PROVIDER_BASE_URLS: Final[Mapping[str, str]] = MappingProxyType(
    {
        "openrouter": OPENROUTER_BASE_URL,
        "opencode-go": OPENCODE_GO_BASE_URL,
        "wally": WALLY_DEFAULT_CONSOLE_URL,
    }
)

BOLTBOOK_HOST: Final[str] = "api.boltbook.ai"

#: Upstream endpoints of the in-guest MCP servers (verified against the server
#: sources in ``factory/mcp_servers``); a server missing here is omitted with a
#: warning instead of being guessed at.
MCP_ENDPOINT_HOSTS: Final[Mapping[str, tuple[str, ...]]] = MappingProxyType(
    {
        "fal_image": ("fal.run",),
        "xsearch": ("api.twitter.com",),
        "coingecko": ("api.coingecko.com",),
        "tavily": ("api.tavily.com",),
        "openrouter": ("openrouter.ai",),
        "grep_app": ("mcp.grep.app",),
        # context7 runs through ``npx``: the guest must reach the npm registry
        # to fetch the package before it can call its own API.
        "context7": ("mcp.context7.com", "context7.com", "registry.npmjs.org"),
    }
)

ENV_MCP_SERVERS: Final[str] = "MCP_SERVERS"
ENV_GREP_APP_URL: Final[str] = "GREP_APP_MCP_URL"
ENV_BOLTBOOK_HEARTBEAT: Final[str] = "FACTORY_BOLTBOOK_HEARTBEAT_ENABLED"
ENV_EGRESS_EXTRA_ALLOW: Final[str] = "IGOREK_ISOLATION_EGRESS_ALLOW"
ENV_DNS_SERVERS: Final[str] = "IGOREK_ISOLATION_DNS_SERVERS"
ENV_LOCAL_INFERENCE: Final[str] = "IGOREK_ISOLATION_LOCAL_INFERENCE"

#: Host-loopback names that make a profile a "local model host" (the same set
#: ``runtime._LOCAL_MODEL_HOSTS`` recognises; a parity test pins the two).
HOST_LOOPBACK_HOSTS: Final[frozenset[str]] = frozenset(
    {"127.0.0.1", "localhost", "::1", "0.0.0.0"}
)

LOCAL_INFERENCE_GATE_AUTO: Final[str] = "auto"
LOCAL_INFERENCE_GATE_ON: Final[str] = "on"
LOCAL_INFERENCE_GATE_OFF: Final[str] = "off"
VALID_LOCAL_INFERENCE_GATES: Final[frozenset[str]] = frozenset(
    {LOCAL_INFERENCE_GATE_AUTO, LOCAL_INFERENCE_GATE_ON, LOCAL_INFERENCE_GATE_OFF}
)

_FALSY: Final[frozenset[str]] = frozenset({"0", "false", "no", "off", "disabled", "none"})
_TRUTHY: Final[frozenset[str]] = frozenset({"1", "true", "yes", "on", "enabled"})
_HOSTNAME_RE: Final[re.Pattern[str]] = re.compile(
    r"^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+$"
)

HostResolver = Callable[[str], Sequence[str]]


def split_csv(value: object) -> tuple[str, ...]:
    """Split a comma-separated env value into stripped, non-empty items."""
    if value is None:
        return ()
    return tuple(item.strip() for item in str(value).split(",") if item.strip())


def system_resolver(host: str) -> tuple[str, ...]:
    """Resolve ``host`` to unique IP literals; an unresolvable host yields ``()``."""
    try:
        infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    except OSError as exc:
        logger.warning("isolation network: cannot resolve %s (%s)", host, exc)
        return ()
    addresses = {str(info[4][0]) for info in infos if info[4]}
    return tuple(sorted(addresses, key=lambda item: (":" in item, item)))


def endpoint_from_url(url: str) -> tuple[str, frozenset[int]]:
    """``(host, ports)`` of an ``http(s)://`` URL; ``("", 443)`` when unparsable."""
    parts = urlsplit(url.strip())
    host = (parts.hostname or "").strip().lower()
    if not host:
        return "", DEFAULT_ALLOW_PORTS
    if parts.port:
        return host, frozenset({parts.port})
    return host, frozenset({443 if parts.scheme == "https" else 80})


def endpoint_from_extra(raw: str) -> tuple[str, frozenset[int]]:
    """``(host, ports)`` of an operator entry (``host`` or ``host:port``)."""
    text = raw.strip()
    if text.startswith(("http://", "https://")):
        return endpoint_from_url(text)
    host, separator, port_text = text.partition(":")
    if not separator:
        return text.lower(), DEFAULT_ALLOW_PORTS
    try:
        port = int(port_text, 10)
    except ValueError:
        return "", DEFAULT_ALLOW_PORTS
    if not 1 <= port <= 65535:
        return "", DEFAULT_ALLOW_PORTS
    return host.strip().lower(), frozenset({port})


def mcp_hosts(name: str, source: Mapping[str, str]) -> tuple[str, ...] | None:
    """Endpoints of a configured MCP server, or ``None`` when it is unknown."""
    if name == "grep_app":
        override = str(source.get(ENV_GREP_APP_URL, "")).strip()
        if override:
            host = endpoint_from_url(override)[0]
            return (host,) if host else None
    hosts = MCP_ENDPOINT_HOSTS.get(name)
    return tuple(hosts) if hosts is not None else None


def boltbook_enabled(source: Mapping[str, str]) -> bool:
    """Mirror the gateway default: Boltbook heartbeat is on unless disabled."""
    raw = source.get(ENV_BOLTBOOK_HEARTBEAT)
    if raw is None:
        return True
    return str(raw).strip().lower() not in _FALSY


def resolve_dns_servers(
    source: Mapping[str, str],
) -> tuple[tuple[str, ...], list[tuple[str, str, str]]]:
    """Pinned DNS servers plus ``(subject, reason, detail)`` warning triples."""
    raw = source.get(ENV_DNS_SERVERS)
    if raw is None or not str(raw).strip():
        return DEFAULT_DNS_SERVERS, []
    servers: list[str] = []
    warnings: list[tuple[str, str, str]] = []
    for item in split_csv(raw):
        try:
            parsed = ipaddress.ip_address(item)
        except ValueError:
            warnings.append((item, "invalid_dns_server", ""))
            continue
        servers.append(str(parsed))
    if not servers:
        return DEFAULT_DNS_SERVERS, warnings
    return tuple(servers), warnings


def pin_addresses(host: str, resolve: HostResolver) -> tuple[str, ...]:
    """The unique addresses to pin for ``host`` (literals never hit the resolver)."""
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        answers = resolve(host)
    else:
        answers = (str(literal),)
    return tuple(
        dict.fromkeys(str(answer).strip() for answer in answers if str(answer).strip())
    )


def is_public(address: str) -> bool:
    """True only for a globally routable address (never LAN/loopback/link-local)."""
    try:
        return ipaddress.ip_address(address).is_global
    except ValueError:
        return False


def is_local_host(host: str) -> bool:
    """True for ``localhost`` and for loopback/private/link-local literals."""
    if host == "localhost":
        return True
    try:
        parsed = ipaddress.ip_address(host)
    except ValueError:
        return False
    return parsed.is_loopback or parsed.is_private or parsed.is_link_local


def is_host_loopback(host: str) -> bool:
    """True only for the host's own loopback names (never a LAN address)."""
    return host.strip().lower() in HOST_LOOPBACK_HOSTS


def local_inference_gate(source: Mapping[str, str]) -> tuple[str, str]:
    """Resolve ``IGOREK_ISOLATION_LOCAL_INFERENCE`` to ``auto|on|off``.

    Unset/empty means ``auto``: the controlled route follows a host-loopback
    base_url. An explicit on/off wins; any other value fails closed to ``off``
    and is returned as the second element so the caller can warn about it.
    """
    raw = source.get(ENV_LOCAL_INFERENCE)
    if raw is None or not str(raw).strip():
        return LOCAL_INFERENCE_GATE_AUTO, ""
    value = str(raw).strip().lower()
    if value == LOCAL_INFERENCE_GATE_AUTO:
        return LOCAL_INFERENCE_GATE_AUTO, ""
    if value in _FALSY:
        return LOCAL_INFERENCE_GATE_OFF, ""
    if value in _TRUTHY:
        return LOCAL_INFERENCE_GATE_ON, ""
    return LOCAL_INFERENCE_GATE_OFF, str(raw).strip()


def valid_host(host: str) -> bool:
    """True for an IP literal or a dotted DNS name safe to put in a ruleset."""
    try:
        ipaddress.ip_address(host)
    except ValueError:
        return bool(_HOSTNAME_RE.match(host))
    return True
