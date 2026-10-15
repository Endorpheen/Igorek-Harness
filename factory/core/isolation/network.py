"""Per-session default-deny egress policy for isolated session VMs (todo 16).

The session VM gets exactly one network path: a QEMU user-mode (slirp)
interface. There is no host bridge, no LAN adjacency and no ``hostfwd`` in the
domain XML (:func:`domain_interface_xml`). Containment is enforced inside the
guest by an nftables ruleset whose chain is ``policy drop``: the only egress
that survives is one pinned IP:port rule per configured allow entry.

The allowlist is generated from configuration, never hand-maintained; the
configuration sources (provider base URLs from the code constants, the MCP
endpoint table for ``MCP_SERVERS``, the baseline hosts, the Boltbook flag, the
operator extra list) live in :mod:`factory.core.isolation.network_sources`:

* provider base URLs from ``codex_provider``/``wally_auth`` plus the session's
  own ``base_url`` when it is a public endpoint;
* MCP endpoints for the servers configured in ``MCP_SERVERS``
  (``factory/config/gateway.default.env``);
* the baseline every agent session needs: PyPI, ``api.github.com``,
  ``api.telegram.org``;
* ``api.boltbook.ai`` only while ``FACTORY_BOLTBOOK_HEARTBEAT_ENABLED`` is on;
* operator extras from ``IGOREK_ISOLATION_EGRESS_ALLOW`` (``host[:port]``).

Fail-closed rules: an unknown profile falls back to the bounded minimal set
(baseline + provider defaults) with a visible warning, never allow-all; a
hostname that cannot be resolved, or resolves only to non-public addresses,
keeps its entry for diagnostics but renders no rule. No accept rule is ever
emitted without a concrete public pinned address.

**Threat model - coarse containment.** The ruleset is IP-based: hostnames are
resolved once, at VM start, and those addresses are trusted for the whole
session. That bounds the reachable surface but is not per-host assurance - an
allowed address is reachable on every allowed port and is not separated from
other services behind the same address, and a poisoned answer at pin time is
indistinguishable from a good one. IP-literal connections and DNS-rebinding
bypasses of the allowlist are the documented limit of this model; the rendered
ruleset header repeats that note for operators.

**Host-local inference (plan todo 17).** A profile whose ``base_url`` points at
the host's own loopback (the ``runtime._LOCAL_MODEL_HOSTS`` set) needs exactly
one host service and nothing else. That service is rendered as a single accept
rule for the QEMU user-net gateway alias (``10.0.2.2``) on the configured port,
placed *before* the host-loopback deny: every other host port stays dropped, the
guest's own ``127.0.0.1`` is never the host's loopback, and no LAN address is
opened. The route is gated by ``IGOREK_ISOLATION_LOCAL_INFERENCE`` (``auto``
default, ``on``, ``off``; an unknown value fails closed to ``off``). When the
gate is off, no rule is rendered, the policy carries
:data:`LOCAL_INFERENCE_DISABLED_REASON` with a clear "no fallback" message, and
a guest call to the endpoint is simply dropped - never silently widened. A
non-loopback endpoint (LAN, IPv6 loopback) gets no route. The route target is
never resolved through DNS: the guest-side destination is the constant gateway
alias, so a poisoned answer cannot widen it.

**Blocked egress is never silent.** Every deny rule logs with
:data:`BLOCKED_HOST_LOG_PREFIX` before dropping and the diagnostic vocabulary
lives in :mod:`factory.core.isolation.network_blocked`, re-exported here: a
guest that hits the wall produces an ``isolation.blocked_host`` trace record
instead of a silent hang.
"""

# allow: SIZE_OK - one cohesive egress contract (allowlist decision + the
# single local-inference route + ruleset rendering + the probe that answers
# from that rendered ruleset + deny vocabulary + interface shape); splitting
# them would let the rendered policy drift from the decision it enforces and
# from the verdict the probe reports.

from __future__ import annotations

import ipaddress
import json
import os
import shutil
import subprocess
import xml.etree.ElementTree as ET
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Final

from .network_blocked import (
    BLOCKED_HOST_LOG_PREFIX,
    BLOCKED_HOST_REASON,
    BlockedHostEvent,
    TraceWriter,
    blocked_host_record,
    emit_blocked_host_lines,
    parse_blocked_host_line,
)
from .network_sources import (
    BASELINE_ALLOW_HOSTS,
    BOLTBOOK_HOST,
    DEFAULT_ALLOW_PORTS,
    DEFAULT_DNS_SERVERS,
    ENV_BOLTBOOK_HEARTBEAT,
    ENV_EGRESS_EXTRA_ALLOW,
    ENV_LOCAL_INFERENCE,
    ENV_MCP_SERVERS,
    HOST_LOOPBACK_HOSTS,
    LOCAL_INFERENCE_GATE_AUTO,
    LOCAL_INFERENCE_GATE_OFF,
    LOCAL_INFERENCE_GATE_ON,
    MCP_ENDPOINT_HOSTS,
    PROVIDER_BASE_URLS,
    VALID_LOCAL_INFERENCE_GATES,
    HostResolver,
    boltbook_enabled as boltbook_flag,
    endpoint_from_extra,
    endpoint_from_url,
    is_host_loopback,
    is_local_host,
    is_public,
    local_inference_gate,
    mcp_hosts,
    pin_addresses,
    resolve_dns_servers,
    split_csv,
    system_resolver,
    valid_host,
)

__all__ = [
    "BASELINE_ALLOW_HOSTS",
    "BLOCKED_HOST_LOG_PREFIX",
    "BLOCKED_HOST_REASON",
    "BOLTBOOK_HOST",
    "DEFAULT_ALLOW_PORTS",
    "DEFAULT_DNS_SERVERS",
    "EGRESS_DENY_CIDRS",
    "EGRESS_DENY_IPV6_CIDRS",
    "ENV_BOLTBOOK_HEARTBEAT",
    "ENV_EGRESS_EXTRA_ALLOW",
    "ENV_LOCAL_INFERENCE",
    "ENV_PUBLIC_WEB_EGRESS",
    "ENV_MCP_SERVERS",
    "HOST_LOOPBACK_ALIAS",
    "HOST_LOOPBACK_HOSTS",
    "INTERFACE_MODEL",
    "INTERFACE_TYPE",
    "LOCAL_INFERENCE_DISABLED_REASON",
    "LOCAL_INFERENCE_GATE_AUTO",
    "LOCAL_INFERENCE_GATE_OFF",
    "LOCAL_INFERENCE_GATE_ON",
    "MCP_ENDPOINT_HOSTS",
    "METADATA_ADDRESS",
    "PROVIDER_BASE_URLS",
    "BlockedHostEvent",
    "EgressAllow",
    "EgressProbe",
    "EgressWarning",
    "HostResolver",
    "LocalInferenceRoute",
    "NetworkPolicy",
    "TraceWriter",
    "blocked_host_record",
    "build_network_policy",
    "domain_interface_xml",
    "emit_blocked_host_lines",
    "evaluate_egress",
    "parse_blocked_host_line",
    "system_resolver",
    "system_host_interface_addresses",
]

#: The QEMU user-net address through which the guest sees the host's loopback.
HOST_LOOPBACK_ALIAS: Final[str] = "10.0.2.2"
METADATA_ADDRESS: Final[str] = "169.254.169.254"

EGRESS_DENY_CIDRS: Final[tuple[str, ...]] = (
    "0.0.0.0/8",
    HOST_LOOPBACK_ALIAS,
    METADATA_ADDRESS,
    "127.0.0.0/8",
    "10.0.0.0/8",
    "100.64.0.0/10",
    "172.16.0.0/12",
    "192.168.0.0/16",
    "169.254.0.0/16",
    "192.0.0.0/24",
    "192.0.2.0/24",
    "192.88.99.0/24",
    "198.18.0.0/15",
    "198.51.100.0/24",
    "203.0.113.0/24",
    "224.0.0.0/4",
    "240.0.0.0/4",
)
EGRESS_DENY_IPV6_CIDRS: Final[tuple[str, ...]] = (
    "::/128", "::1/128", "::ffff:0:0/96", "64:ff9b::/96", "100::/64",
    "2001:db8::/32", "2001:10::/28", "fe80::/10", "fc00::/7", "ff00::/8",
)

INTERFACE_TYPE: Final[str] = "user"
INTERFACE_MODEL: Final[str] = "virtio"

#: Stable reason code for a local-inference route withheld by the policy gate.
LOCAL_INFERENCE_DISABLED_REASON: Final[str] = "local_inference_disabled_by_policy"
ENV_PUBLIC_WEB_EGRESS: Final[str] = "IGOREK_ISOLATION_PUBLIC_WEB_EGRESS"


@dataclass(frozen=True, slots=True)
class EgressAllow:
    """One configured allow entry: a host reachable on ``ports`` only.

    ``addresses`` are the public IPs pinned at policy-build time; an empty tuple
    means the entry could not be pinned (resolution failed or every answer was
    non-public) and therefore renders no rule in the ruleset.
    """

    host: str
    ports: frozenset[int]
    source: str
    addresses: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class EgressWarning:
    """A configured endpoint that could not be turned into an allow rule."""

    subject: str
    reason: str
    source: str
    detail: str = ""


@dataclass(frozen=True, slots=True)
class LocalInferenceRoute:
    """The one policy-gated exception to the host-loopback deny (plan todo 17).

    ``target_host``/``target_port`` are where the host service actually listens;
    ``gateway_address`` is the only destination the guest may dial. Guest
    loopback is never the host's loopback, so the rule is pinned to the QEMU
    user-net alias - never to ``127.0.0.1``.
    """

    target_host: str
    target_port: int
    gateway_address: str = HOST_LOOPBACK_ALIAS
    policy: str = LOCAL_INFERENCE_GATE_AUTO
    enabled: bool = True
    reason: str = ""

    @property
    def target(self) -> str:
        """``host:port`` of the host-local endpoint this route stands for."""
        return f"{self.target_host}:{self.target_port}"

    def status_message(self) -> str:
        """Operator/task-facing message for the enabled and disabled states."""
        if self.enabled:
            return (
                f"local inference route enabled"
                f" (IGOREK_ISOLATION_LOCAL_INFERENCE={self.policy}): guest reaches"
                f" host-local endpoint {self.target} at"
                f" {self.gateway_address}:{self.target_port}"
            )
        return (
            f"local inference route disabled by policy"
            f" (IGOREK_ISOLATION_LOCAL_INFERENCE={self.policy}): the guest cannot"
            f" reach the host-local endpoint {self.target}; no unrestricted"
            f" network fallback is applied"
        )


@dataclass(frozen=True, slots=True)
class EgressProbe:
    """Verdict for one outbound flow evaluated against a rendered ruleset."""

    destination: str
    port: int
    protocol: str
    allowed: bool
    reason: str
    matched_rule: str = ""


@dataclass(frozen=True, slots=True)
class NetworkPolicy:
    """The per-session egress decision: pinned allows, warnings, DNS and denies."""

    profile: str
    allows: tuple[EgressAllow, ...]
    warnings: tuple[EgressWarning, ...]
    dns_servers: tuple[str, ...]
    fallback: bool
    local_inference_gate: str = LOCAL_INFERENCE_GATE_AUTO
    local_inference: LocalInferenceRoute | None = None
    host_local_ports: tuple[int, ...] = ()
    """Host-side ports the guest may reach on the gateway alias (todo 18 proxy)."""
    public_web_egress: bool = False
    host_addresses: tuple[str, ...] = ()

    def allow_hosts(self) -> tuple[str, ...]:
        """Configured allow hosts, sorted and deduplicated."""
        return tuple(sorted({entry.host for entry in self.allows}))

    def pinned_endpoints(self) -> tuple[tuple[str, int], ...]:
        """Every public allowlist ``(address, port)`` that renders an accept rule."""
        return tuple(
            sorted(
                (address, port)
                for entry in self.allows
                for address in entry.addresses
                for port in entry.ports
            )
        )

    def address_hosts(self) -> dict[str, str]:
        """Reverse map for diagnostics: pinned address back to its allow host."""
        mapping: dict[str, str] = {}
        for entry in self.allows:
            for address in entry.addresses:
                mapping.setdefault(address, entry.host)
        return mapping

    def ruleset(self) -> str:
        """Render the deterministic in-guest nftables ruleset."""
        return _render_ruleset(self)


def build_network_policy(
    profile: str,
    *,
    base_url: str = "",
    env: Mapping[str, str] | None = None,
    resolver: HostResolver | None = None,
    boltbook_enabled: bool | None = None,
    local_inference: str | None = None,
    host_local_ports: Iterable[int] = (),
    extra_mcp_servers: Iterable[str] = (),
    host_addresses: Iterable[str] | None = None,
) -> NetworkPolicy:
    """Derive the per-session egress policy from configuration.

    ``boltbook_enabled`` and ``local_inference`` override the env flags for
    callers that already resolved them; every other input comes from ``env``
    (default: ``os.environ``) and the code constants. The resolver is the only
    network boundary and is injected in tests.

    ``host_local_ports`` are host-side only (never read from a guest payload):
    each one renders a single accept to the QEMU user-net gateway alias for the
    documented host-local service (the todo-18 egress-auth proxy). Every other
    host port stays denied.
    """
    source = os.environ if env is None else env
    resolve = resolver if resolver is not None else system_resolver
    warnings: list[EgressWarning] = []
    entries: list[tuple[str, frozenset[int], str]] = []

    def add(host: str, ports: frozenset[int], source_label: str) -> None:
        cleaned = host.strip().lower()
        if not cleaned:
            return
        for index, (existing, existing_ports, existing_source) in enumerate(entries):
            if existing == cleaned:
                entries[index] = (existing, existing_ports | ports, existing_source)
                return
        entries.append((cleaned, ports, source_label))

    for host in BASELINE_ALLOW_HOSTS:
        add(host, DEFAULT_ALLOW_PORTS, "baseline")
    for name, url in PROVIDER_BASE_URLS.items():
        host, ports = endpoint_from_url(url)
        add(host, ports, f"provider:{name}")

    gate, invalid_gate = _resolve_local_inference_gate(source, local_inference)
    local_route = _local_inference_route(base_url, gate, warnings)
    if invalid_gate:
        warnings.append(
            EgressWarning(
                invalid_gate, "invalid_local_inference_gate", "policy.local_inference"
            )
        )

    accepted_base = False
    base = (base_url or "").strip()
    if base:
        host, ports = endpoint_from_url(base)
        if not host:
            warnings.append(EgressWarning(base, "base_url_missing", "profile.base_url"))
        elif is_local_host(host):
            detail = ""
            if local_route is not None and local_route.enabled:
                detail = local_route.status_message()
            warnings.append(
                EgressWarning(
                    host, "local_endpoint_not_egress_allowed", "profile.base_url", detail
                )
            )
        else:
            add(host, ports, "profile.base_url")
            accepted_base = True
    else:
        warnings.append(
            EgressWarning(profile or "<none>", "base_url_missing", "profile.base_url")
        )

    for name in split_csv(source.get(ENV_MCP_SERVERS)):
        hosts = mcp_hosts(name, source)
        if hosts is None:
            warnings.append(EgressWarning(name, "mcp_endpoint_unknown", "mcp"))
            continue
        for host in hosts:
            add(host, DEFAULT_ALLOW_PORTS, f"mcp:{name}")

    for name in extra_mcp_servers:
        cleaned = str(name).strip()
        if not cleaned:
            continue
        hosts = mcp_hosts(cleaned, source)
        if hosts is None:
            warnings.append(EgressWarning(cleaned, "mcp_endpoint_unknown", "mcp.profile"))
            continue
        for host in hosts:
            add(host, DEFAULT_ALLOW_PORTS, f"mcp:{cleaned}")

    enabled = boltbook_flag(source) if boltbook_enabled is None else boltbook_enabled
    if enabled:
        add(BOLTBOOK_HOST, DEFAULT_ALLOW_PORTS, "boltbook")

    for raw in split_csv(source.get(ENV_EGRESS_EXTRA_ALLOW)):
        host, ports = endpoint_from_extra(raw)
        if not host or not valid_host(host):
            warnings.append(EgressWarning(raw, "invalid_extra_allow", "operator"))
            continue
        add(host, ports, "operator")

    dns_servers, dns_warnings = resolve_dns_servers(source)
    warnings.extend(
        EgressWarning(subject, reason, "dns", detail)
        for subject, reason, detail in dns_warnings
    )

    raw_public_web = str(source.get(ENV_PUBLIC_WEB_EGRESS, "off")).strip().lower()
    public_web_enabled = raw_public_web in {"1", "true", "yes", "on", "public"}
    if raw_public_web not in {"", "0", "false", "no", "off", "disabled", "1", "true", "yes", "on", "public"}:
        warnings.append(EgressWarning(raw_public_web, "invalid_public_web_egress", "policy"))
        public_web_enabled = False
    parsed_host_addresses: tuple[str, ...] = ()
    if public_web_enabled:
        try:
            raw_addresses = system_host_interface_addresses() if host_addresses is None else tuple(host_addresses)
            normalized: set[str] = set()
            for raw_address in raw_addresses:
                address = ipaddress.ip_address(str(raw_address).split("/", 1)[0])
                if not address.is_loopback and not address.is_unspecified:
                    normalized.add(f"{address}/{address.max_prefixlen}")
            if not normalized:
                raise ValueError("no non-loopback host addresses found")
            parsed_host_addresses = tuple(sorted(normalized))
        except (OSError, ValueError, subprocess.SubprocessError, json.JSONDecodeError) as exc:
            warnings.append(EgressWarning("host interfaces", "host_address_inventory_failed", "policy", type(exc).__name__))
            public_web_enabled = False

    allows: list[EgressAllow] = []
    for host, ports, source_label in entries:
        addresses = pin_addresses(host, resolve)
        public = tuple(address for address in addresses if is_public(address))
        refused = tuple(address for address in addresses if not is_public(address))
        if refused:
            warnings.append(
                EgressWarning(
                    host, "non_public_address", source_label, detail=",".join(refused)
                )
            )
        if not addresses:
            warnings.append(EgressWarning(host, "resolve_failed", source_label))
        allows.append(
            EgressAllow(host=host, ports=ports, source=source_label, addresses=public)
        )

    return NetworkPolicy(
        profile=profile,
        allows=tuple(allows),
        warnings=tuple(warnings),
        dns_servers=dns_servers,
        fallback=not accepted_base,
        local_inference_gate=gate,
        local_inference=local_route,
        host_local_ports=_host_local_ports(host_local_ports),
        public_web_egress=public_web_enabled,
        host_addresses=parsed_host_addresses,
    )


def system_host_interface_addresses() -> tuple[str, ...]:
    """Return all non-loopback host interface addresses for guest-side denies."""
    executable = shutil.which("ip") or "/usr/sbin/ip"
    result = subprocess.run(
        [executable, "-j", "address", "show"],
        check=True,
        capture_output=True,
        text=True,
        timeout=3,
    )
    interfaces = json.loads(result.stdout)
    if not isinstance(interfaces, list):
        raise ValueError("unexpected interface inventory")
    addresses: set[str] = set()
    for interface in interfaces:
        if not isinstance(interface, dict):
            continue
        for item in interface.get("addr_info", []):
            if not isinstance(item, dict):
                continue
            try:
                address = ipaddress.ip_address(str(item.get("local", "")))
            except ValueError:
                continue
            if not address.is_loopback and not address.is_unspecified:
                addresses.add(str(address))
    if not addresses:
        raise ValueError("no host interface addresses found")
    return tuple(sorted(addresses))


def _resolve_local_inference_gate(
    source: Mapping[str, str], override: str | None
) -> tuple[str, str]:
    if override is None:
        return local_inference_gate(source)
    value = override.strip().lower()
    if value in VALID_LOCAL_INFERENCE_GATES:
        return value, ""
    return LOCAL_INFERENCE_GATE_OFF, override.strip()


def _local_inference_route(
    base_url: str,
    gate: str,
    warnings: list[EgressWarning],
) -> LocalInferenceRoute | None:
    base = (base_url or "").strip()
    if not base:
        return None
    host, ports = endpoint_from_url(base)
    if not host:
        return None
    if not is_host_loopback(host):
        if gate == LOCAL_INFERENCE_GATE_ON:
            warnings.append(
                EgressWarning(host, "local_inference_not_host_loopback", "profile.base_url")
            )
        return None
    if ":" in host:
        warnings.append(
            EgressWarning(host, "local_inference_unsupported_family", "profile.base_url")
        )
        return None
    port = min(ports) if ports else 80
    enabled = gate != LOCAL_INFERENCE_GATE_OFF
    route = LocalInferenceRoute(
        target_host=host,
        target_port=port,
        policy=gate,
        enabled=enabled,
        reason="" if enabled else LOCAL_INFERENCE_DISABLED_REASON,
    )
    if not enabled:
        warnings.append(
            EgressWarning(
                route.target,
                LOCAL_INFERENCE_DISABLED_REASON,
                "policy.local_inference",
                detail=route.status_message(),
            )
        )
    return route


def domain_interface_xml() -> str:
    """The single restricted libvirt interface: QEMU user-net, never a bridge."""
    interface = ET.Element("interface", {"type": INTERFACE_TYPE})
    ET.SubElement(interface, "model", {"type": INTERFACE_MODEL})
    ET.indent(interface, space="  ")
    return ET.tostring(interface, encoding="unicode") + "\n"


def _render_ruleset(policy: NetworkPolicy) -> str:
    lines = [
        f"# igorek-egress policy for session profile {policy.profile!r}",
        "# Containment is coarse: hostnames are resolved once, at VM start, and",
        "# every allow is an exact pinned IP:port. IP-literal connections and",
        "# DNS-rebinding are a documented limit of this model (network.py).",
        "# The one host-loopback exception is the controlled local-inference route,",
        "# enabled only for a local profile and pinned to the gateway alias:port.",
        f'# Denied egress is logged with the "{BLOCKED_HOST_LOG_PREFIX}" prefix and',
        "# surfaced as an isolation.blocked_host event, never a silent hang.",
        "table inet igorek_egress {",
        "  chain output {",
        "    type filter hook output priority filter; policy drop;",
        "",
        '    oifname "lo" accept  # the guest\'s own loopback, never the host\'s',
        "",
        "    # DHCP lease renewal from the QEMU user-net stack.",
        "    udp sport 68 udp dport 67 accept",
        "",
        "    # DNS is pinned; no other resolver is reachable (see the denies below).",
    ]
    for server in policy.dns_servers:
        family = _family_of(server)
        lines.append(f"    {family} daddr {server} udp dport 53 accept")
        lines.append(f"    {family} daddr {server} tcp dport 53 accept")
    lines.append("")
    lines.extend(_local_inference_lines(policy))
    lines.extend(_host_local_lines(policy))
    if policy.public_web_egress:
        lines.append("    # Host-assigned addresses remain denied even when public web is enabled.")
        for address in policy.host_addresses:
            lines.append(f'    {"ip6" if ":" in address else "ip"} daddr {address} log prefix "{BLOCKED_HOST_LOG_PREFIX}" counter drop')
    lines.append("    # Host-loopback/LAN/metadata/reserved denies: logged, counted, dropped.")
    for cidr in EGRESS_DENY_CIDRS:
        lines.append(
            f'    ip daddr {cidr} log prefix "{BLOCKED_HOST_LOG_PREFIX}" counter drop'
        )
    for cidr in EGRESS_DENY_IPV6_CIDRS:
        lines.append(
            f'    ip6 daddr {cidr} log prefix "{BLOCKED_HOST_LOG_PREFIX}" counter drop'
        )
    lines.append("")
    if policy.public_web_egress:
        lines.append("    # Public web egress: TCP only, after host and special-use address denies.")
        lines.append("    ip daddr 0.0.0.0/0 tcp dport { 80, 443 } accept  # public HTTP(S)")
        lines.append("")
    lines.append("    # Configured allowlist entries (pinned at VM start).")
    for entry in sorted(policy.allows, key=lambda item: item.host):
        ports = ", ".join(str(port) for port in sorted(entry.ports))
        for address in sorted(entry.addresses):
            lines.append(
                f"    {_family_of(address)} daddr {address} tcp dport {{ {ports} }} accept"
                f"  # {entry.host} ({entry.source})"
            )
    lines.append("  }")
    lines.append("}")
    return "\n".join(lines) + "\n"


def _local_inference_lines(policy: NetworkPolicy) -> list[str]:
    route = policy.local_inference
    if route is None:
        return []
    if not route.enabled:
        return [
            f"    # Local inference route {route.target} is DISABLED by policy",
            f"    # (IGOREK_ISOLATION_LOCAL_INFERENCE={route.policy}): no fallback.",
            "",
        ]
    return [
        "    # Controlled host-local inference route (todo 17): the one host-loopback",
        "    # exception, pinned to the QEMU user-net gateway alias. Every other",
        "    # host port stays denied by the rules below.",
        f"    ip daddr {route.gateway_address} tcp dport {{ {route.target_port} }} accept"
        f"  # local-inference {route.target}",
        "",
    ]


def _host_local_lines(policy: NetworkPolicy) -> list[str]:
    """One accept per configured host-local service port, alias-pinned only."""
    if not policy.host_local_ports:
        return []
    lines = [
        "    # Host-local service routes (todo 18 egress-auth proxy): one accept per",
        "    # configured port on the QEMU user-net gateway alias. Every other host",
        "    # port stays denied by the host-loopback deny below.",
    ]
    for port in policy.host_local_ports:
        lines.append(
            f"    ip daddr {HOST_LOOPBACK_ALIAS} tcp dport {{ {port} }} accept"
            "  # host-local service"
        )
    lines.append("")
    return lines


def _host_local_ports(raw: Iterable[int]) -> tuple[int, ...]:
    """Validated, deduplicated host-local ports; an invalid one is a refusal."""
    ports: set[int] = set()
    for value in raw:
        if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 65535:
            raise ValueError(f"invalid host-local port: {value!r}")
        ports.add(value)
    return tuple(sorted(ports))


def _family_of(address: str) -> str:
    return "ip6" if ":" in address else "ip"


def evaluate_egress(
    ruleset: str, destination: str, port: int, protocol: str = "tcp"
) -> EgressProbe:
    """Verdict for a fresh outbound flow against the rendered ruleset.

    The evaluator walks the chain in order (first match wins, the chain policy
    is drop) and answers for a *new* flow to a non-loopback destination: the
    guest's own ``lo``, ``ct state established`` and DHCP lines never carry such
    a flow and are not modelled. This is the deterministic probe used by tests
    and the operator CLI without a running VM.
    """
    proto = protocol.strip().lower() or "tcp"
    try:
        target = ipaddress.ip_address(destination.strip())
    except ValueError:
        return EgressProbe(destination, port, proto, False, "invalid_destination")
    for raw_line in ruleset.splitlines():
        line = raw_line.split("#", 1)[0].strip()
        tokens = line.split()
        if not tokens or "daddr" not in tokens:
            continue
        family = tokens[0]
        if family not in {"ip", "ip6"}:
            continue
        if (family == "ip6") != (target.version == 6):
            continue
        if not _address_matches(tokens[tokens.index("daddr") + 1], target):
            continue
        if "drop" in tokens:
            return EgressProbe(destination, port, proto, False, "addr_denied", line)
        if "accept" in tokens and _rule_accepts(tokens, port, proto):
            return EgressProbe(destination, port, proto, True, "allowed", line)
    return EgressProbe(destination, port, proto, False, "default_deny")


def _address_matches(token: str, target: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    try:
        network = ipaddress.ip_network(token, strict=False)
    except ValueError:
        return False
    return target in network


def _rule_accepts(tokens: list[str], port: int, protocol: str) -> bool:
    rule_protocol = "tcp" if "tcp" in tokens else "udp" if "udp" in tokens else ""
    if rule_protocol and rule_protocol != protocol:
        return False
    if "dport" not in tokens:
        return True
    return _port_matches(tokens[tokens.index("dport") + 1 :], port)


def _port_matches(tokens: list[str], port: int) -> bool:
    if not tokens:
        return False
    if tokens[0] == "{":
        for token in tokens[1:]:
            if token == "}":
                return False
            if token.rstrip(",") == str(port):
                return True
        return False
    return tokens[0].rstrip("}") == str(port)
