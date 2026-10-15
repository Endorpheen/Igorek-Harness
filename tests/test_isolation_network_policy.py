#!/usr/bin/env python3
"""Unit tests for the per-session default-deny egress policy (plan todo 16).

No KVM required: the DNS resolver is injected, so every rule in the rendered
nftables set is deterministic. The suite pins the structural invariants that
make the policy a containment boundary rather than a configuration file:

* default-deny with exactly one accept rule per configured allow entry;
* the QEMU user-net host alias, RFC1918/LAN, loopback and the cloud metadata
  address are explicitly denied (and logged, so a denial is never a silent hang);
* an unknown/unresolvable profile falls back to a bounded minimal set - never
  allow-all, and a private/loopback resolution result is refused;
* the domain XML carries exactly one restricted user-mode interface (never a
  bridge, never hostfwd) and the default spec keeps the pre-todo-16 shape.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from collections.abc import Mapping
from pathlib import Path

REPO_ROOT = Path(
    os.environ.get("IGOREK_FACTORY_REPO", Path(__file__).resolve().parents[1])
)
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from factory.core.cc_backend_trace import TRACE_FILENAME  # noqa: E402
from factory.core.codex_provider import (  # noqa: E402
    OPENCODE_GO_BASE_URL,
    OPENROUTER_BASE_URL,
)
from factory.core.isolation.domain import DomainSpec, build_domain_xml  # noqa: E402
from factory.core.isolation.network import (  # noqa: E402
    BASELINE_ALLOW_HOSTS,
    BLOCKED_HOST_LOG_PREFIX,
    BLOCKED_HOST_REASON,
    DEFAULT_DNS_SERVERS,
    EGRESS_DENY_CIDRS,
    EGRESS_DENY_IPV6_CIDRS,
    HOST_LOOPBACK_ALIAS,
    MCP_ENDPOINT_HOSTS,
    METADATA_ADDRESS,
    PROVIDER_BASE_URLS,
    NetworkPolicy,
    blocked_host_record,
    build_network_policy,
    domain_interface_xml,
    emit_blocked_host_lines,
    evaluate_egress,
    parse_blocked_host_line,
)
from factory.core.trace_taxonomy import CcTraceEvent, all_event_kinds  # noqa: E402
from factory.core.wally_auth import WALLY_DEFAULT_CONSOLE_URL  # noqa: E402

# Global (publicly routable) fixture addresses: every allow must be pinnable.
FIXTURE_ADDRESSES: dict[str, tuple[str, ...]] = {
    "pypi.org": ("151.101.0.223",),
    "files.pythonhosted.org": ("151.101.0.63",),
    "api.github.com": ("140.82.121.5",),
    "api.telegram.org": ("149.154.167.220",),
    "openrouter.ai": ("104.18.2.1",),
    "opencode.ai": ("104.18.3.1",),
    "inference.runanywhere.ai": ("76.76.21.21",),
    "mcp.grep.app": ("216.198.79.1",),
    "mcp.context7.com": ("104.18.7.1",),
    "context7.com": ("104.18.8.1",),
    "registry.npmjs.org": ("104.16.24.34",),
    "fal.run": ("104.18.6.1",),
    "api.twitter.com": ("104.244.42.1",),
    "api.coingecko.com": ("104.16.99.1",),
    "api.tavily.com": ("104.18.4.1",),
    "api.boltbook.ai": ("104.18.5.1",),
    "example.com": ("93.184.216.34",),
}
FIXTURE_BASE_URL = "https://inference.runanywhere.ai/v1"
# Mirrors factory/config/gateway.default.env:76-95 (MCP_SERVERS).
FIXTURE_MCP_SERVERS = "fal_image,xsearch,coingecko,tavily,openrouter,grep_app"


def fixture_resolver(
    host: str, mapping: Mapping[str, tuple[str, ...]] = FIXTURE_ADDRESSES
) -> tuple[str, ...]:
    return tuple(mapping.get(host, ()))


def fake_env(**overrides: str) -> dict[str, str]:
    env = {
        "MCP_SERVERS": FIXTURE_MCP_SERVERS,
        "FACTORY_BOLTBOOK_HEARTBEAT_ENABLED": "true",
    }
    env.update(overrides)
    return env


def host_of(url: str) -> str:
    from urllib.parse import urlsplit

    return urlsplit(url).hostname or ""


def policy(
    profile: str = "cc-wally",
    *,
    base_url: str = FIXTURE_BASE_URL,
    env: Mapping[str, str] | None = None,
    resolver=fixture_resolver,
) -> NetworkPolicy:
    return build_network_policy(
        profile,
        base_url=base_url,
        env=fake_env() if env is None else env,
        resolver=resolver,
    )


def accept_addresses(ruleset: str) -> list[str]:
    """Every `ip/ip6 daddr <addr> ... accept` target, in order."""
    found: list[str] = []
    for line in ruleset.splitlines():
        stripped = line.strip()
        if " accept" not in stripped or "daddr" not in stripped:
            continue
        parts = stripped.split()
        for index, token in enumerate(parts):
            if token == "daddr" and index + 1 < len(parts):
                found.append(parts[index + 1])
                break
    return found


class AllowlistGenerationTest(unittest.TestCase):
    def test_known_profile_allows_exactly_the_configured_hosts(self) -> None:
        result = policy()
        expected = {
            *BASELINE_ALLOW_HOSTS,
            "openrouter.ai",
            "opencode.ai",
            "inference.runanywhere.ai",
            "fal.run",
            "api.twitter.com",
            "api.coingecko.com",
            "api.tavily.com",
            "mcp.grep.app",
            "api.boltbook.ai",
        }
        self.assertEqual(expected, set(result.allow_hosts()))
        self.assertFalse(result.fallback)
        self.assertEqual([], list(result.warnings))

    def test_provider_defaults_come_from_the_code_constants(self) -> None:
        urls = dict(PROVIDER_BASE_URLS)
        self.assertEqual(host_of(OPENROUTER_BASE_URL), host_of(urls["openrouter"]))
        self.assertEqual(host_of(OPENCODE_GO_BASE_URL), host_of(urls["opencode-go"]))
        self.assertEqual(
            host_of(WALLY_DEFAULT_CONSOLE_URL), host_of(urls["wally"])
        )
        result = policy()
        for url in urls.values():
            self.assertIn(host_of(url), result.allow_hosts())

    def test_unknown_profile_falls_back_to_the_minimal_bounded_set(self) -> None:
        result = build_network_policy(
            "no-such-profile",
            env=fake_env(MCP_SERVERS=""),
            resolver=fixture_resolver,
        )
        self.assertTrue(result.fallback)
        self.assertEqual(
            {*BASELINE_ALLOW_HOSTS, "openrouter.ai", "opencode.ai", "inference.runanywhere.ai", "api.boltbook.ai"},
            set(result.allow_hosts()),
        )
        self.assertIn("base_url_missing", [warning.reason for warning in result.warnings])
        # Fail-closed: a fallback is a bounded set, never allow-all.
        for wildcard in ("0.0.0.0/0", "::/0"):
            self.assertNotIn(wildcard, result.ruleset())

    def test_local_base_url_is_never_egress_allowed(self) -> None:
        result = policy(base_url="http://127.0.0.1:8001")
        self.assertNotIn("127.0.0.1", result.allow_hosts())
        self.assertIn(
            "local_endpoint_not_egress_allowed",
            [warning.reason for warning in result.warnings],
        )
        for address in accept_addresses(result.ruleset()):
            self.assertFalse(address.startswith("127."), address)

    def test_private_base_url_resolution_is_refused(self) -> None:
        resolver = lambda host: (  # noqa: E731
            ("192.168.1.10",) if host == "providers.internal" else fixture_resolver(host)
        )
        result = policy(base_url="https://providers.internal/v1", resolver=resolver)
        self.assertIn("providers.internal", result.allow_hosts())
        self.assertIn(
            "non_public_address", [warning.reason for warning in result.warnings]
        )
        self.assertNotIn("192.168.1.10", accept_addresses(result.ruleset()))

    def test_boltbook_is_allowed_only_when_enabled(self) -> None:
        enabled = policy(env=fake_env(FACTORY_BOLTBOOK_HEARTBEAT_ENABLED="true"))
        disabled = policy(env=fake_env(FACTORY_BOLTBOOK_HEARTBEAT_ENABLED="false"))
        self.assertIn("api.boltbook.ai", enabled.allow_hosts())
        self.assertNotIn("api.boltbook.ai", disabled.allow_hosts())

    def test_mcp_endpoints_follow_the_configured_servers(self) -> None:
        result = policy(env=fake_env(MCP_SERVERS="tavily,grep_app"))
        self.assertIn("api.tavily.com", result.allow_hosts())
        self.assertIn("mcp.grep.app", result.allow_hosts())
        for absent in ("fal.run", "api.twitter.com", "api.coingecko.com"):
            self.assertNotIn(absent, result.allow_hosts())

    def test_unknown_mcp_server_is_omitted_with_a_visible_warning(self) -> None:
        result = policy(env=fake_env(MCP_SERVERS="tavily,not_a_server"))
        self.assertIn("api.tavily.com", result.allow_hosts())
        warnings = [warning for warning in result.warnings if warning.reason == "mcp_endpoint_unknown"]
        self.assertEqual(["not_a_server"], [warning.subject for warning in warnings])
        self.assertNotIn("not_a_server", result.ruleset())

    def test_mcp_endpoint_table_covers_the_configured_servers(self) -> None:
        configured = [name.strip() for name in FIXTURE_MCP_SERVERS.split(",") if name.strip()]
        for name in configured:
            self.assertIn(name, MCP_ENDPOINT_HOSTS)

    def test_profile_enabled_mcp_endpoints_join_the_allowlist(self) -> None:
        result = build_network_policy(
            "cc-wally",
            base_url=FIXTURE_BASE_URL,
            env=fake_env(),
            resolver=fixture_resolver,
            extra_mcp_servers=("context7",),
        )
        self.assertIn("mcp.context7.com", result.allow_hosts())
        self.assertIn("context7.com", result.allow_hosts())
        self.assertIn("registry.npmjs.org", result.allow_hosts())

    def test_unknown_extra_mcp_server_is_omitted_with_a_visible_warning(self) -> None:
        result = build_network_policy(
            "cc-wally",
            base_url=FIXTURE_BASE_URL,
            env=fake_env(),
            resolver=fixture_resolver,
            extra_mcp_servers=("not_a_server",),
        )
        self.assertNotIn("not_a_server", result.allow_hosts())
        self.assertIn(
            "mcp_endpoint_unknown", [warning.reason for warning in result.warnings]
        )

    def test_dispatch_egress_includes_profile_enabled_mcp_endpoints(self) -> None:
        from unittest.mock import patch

        from factory.core.isolation.manager import build_dispatch_egress

        with patch(
            "factory.core.mcp_config.get_profile_mcp_servers",
            return_value=["context7"],
        ):
            egress = build_dispatch_egress(
                "cc-wally",
                FIXTURE_BASE_URL,
                env=fake_env(),
                resolver=fixture_resolver,
            )
        self.assertIn("registry.npmjs.org", egress.allow_hosts)
        self.assertIn("mcp.context7.com", egress.allow_hosts)

    def test_operator_extras_are_pinned_and_invalid_entries_warn(self) -> None:
        result = policy(
            env=fake_env(IGOREK_ISOLATION_EGRESS_ALLOW="example.com:8443, not a host!!")
        )
        self.assertIn("example.com", result.allow_hosts())
        entry = next(item for item in result.allows if item.host == "example.com")
        self.assertEqual(frozenset({8443}), entry.ports)
        self.assertIn(f"ip daddr 93.184.216.34 tcp dport {{ 8443 }} accept", result.ruleset())
        self.assertIn(
            "invalid_extra_allow", [warning.reason for warning in result.warnings]
        )

    def test_resolution_failure_omits_the_allow_and_warns(self) -> None:
        resolver = lambda host: () if host == "pypi.org" else fixture_resolver(host)  # noqa: E731
        result = policy(resolver=resolver)
        entry = next(item for item in result.allows if item.host == "pypi.org")
        self.assertEqual((), entry.addresses)
        self.assertIn("resolve_failed", [warning.reason for warning in result.warnings])
        self.assertNotIn("pypi.org", result.ruleset())
        self.assertNotIn("151.101.0.223", accept_addresses(result.ruleset()))
        self.assertEqual(
            sorted(set(result.pinned_endpoints())),
            sorted(endpoint for endpoint in set(_accept_endpoints(result.ruleset())) if endpoint[1] == 443),
        )

    def test_public_web_egress_is_opt_in_and_restricted_to_http_https(self) -> None:
        disabled = policy(env=fake_env(IGOREK_ISOLATION_PUBLIC_WEB_EGRESS="off"))
        self.assertNotIn("ip daddr 0.0.0.0/0 tcp dport { 80, 443 } accept", disabled.ruleset())
        enabled = build_network_policy(
            "cc-wally",
            base_url=FIXTURE_BASE_URL,
            env=fake_env(IGOREK_ISOLATION_PUBLIC_WEB_EGRESS="on"),
            resolver=fixture_resolver,
            host_addresses=("203.0.113.19", "2001:db8::7"),
        )
        ruleset = enabled.ruleset()
        self.assertTrue(enabled.public_web_egress)
        self.assertIn("ip daddr 0.0.0.0/0 tcp dport { 80, 443 } accept", ruleset)
        self.assertIn("ip daddr 203.0.113.19/32 log prefix", ruleset)
        self.assertIn("ip6 daddr 2001:db8::7/128 log prefix", ruleset)
        self.assertLess(ruleset.index("ip daddr 203.0.113.19/32 log prefix"), ruleset.index("ip daddr 0.0.0.0/0 tcp dport"))
        self.assertEqual("allowed", evaluate_egress(ruleset, "8.8.8.8", 443).reason)
        self.assertEqual("allowed", evaluate_egress(ruleset, "8.8.8.8", 80).reason)
        self.assertEqual("default_deny", evaluate_egress(ruleset, "8.8.8.8", 22).reason)
        self.assertEqual("addr_denied", evaluate_egress(ruleset, "203.0.113.19", 443).reason)
        self.assertEqual("addr_denied", evaluate_egress(ruleset, "169.254.169.254", 443).reason)

    def test_public_web_denies_special_use_ipv4_ranges_before_the_broad_allow(self) -> None:
        result = build_network_policy(
            "cc-wally",
            env=fake_env(IGOREK_ISOLATION_PUBLIC_WEB_EGRESS="on"),
            resolver=fixture_resolver,
            host_addresses=("192.0.2.44",),
        )
        ruleset = result.ruleset()
        broad_allow = ruleset.index("ip daddr 0.0.0.0/0 tcp dport { 80, 443 } accept")
        for cidr in ("0.0.0.0/8", "100.64.0.0/10", "192.0.0.0/24", "192.0.2.0/24", "198.18.0.0/15", "198.51.100.0/24", "203.0.113.0/24", "224.0.0.0/4", "240.0.0.0/4"):
            deny = ruleset.index(f"ip daddr {cidr} log prefix")
            self.assertLess(deny, broad_allow, cidr)

    def test_public_web_fails_closed_when_host_address_inventory_fails(self) -> None:
        from unittest.mock import patch

        with patch("factory.core.isolation.network.system_host_interface_addresses", side_effect=OSError("ip unavailable")):
            result = build_network_policy(
                "cc-wally",
                base_url=FIXTURE_BASE_URL,
                env=fake_env(IGOREK_ISOLATION_PUBLIC_WEB_EGRESS="on"),
                resolver=fixture_resolver,
            )
        self.assertFalse(result.public_web_egress)
        self.assertNotIn("ip daddr 0.0.0.0/0 tcp dport { 80, 443 } accept", result.ruleset())
        self.assertIn("host_address_inventory_failed", [warning.reason for warning in result.warnings])

    def test_new_task_rules_do_not_keep_stale_established_egress(self) -> None:
        ruleset = policy().ruleset()
        self.assertNotIn("ct state established,related accept", ruleset)

    def test_rule_order_is_deterministic(self) -> None:
        self.assertEqual(policy().ruleset(), policy().ruleset())


class RulesetTest(unittest.TestCase):
    def test_ruleset_is_default_deny(self) -> None:
        ruleset = policy().ruleset()
        self.assertIn("policy drop;", ruleset)
        self.assertIn("table inet igorek_egress", ruleset)

    def test_exactly_one_accept_rule_per_configured_allow(self) -> None:
        result = policy()
        expected = set(result.pinned_endpoints()) | {
            (server, 53) for server in result.dns_servers
        }
        self.assertEqual(sorted(expected), sorted(set(_accept_endpoints(result.ruleset()))))

    def test_ruleset_denies_host_loopback_lan_and_metadata(self) -> None:
        ruleset = policy().ruleset()
        for cidr in (HOST_LOOPBACK_ALIAS, METADATA_ADDRESS, "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "127.0.0.0/8"):
            self.assertIn(cidr, EGRESS_DENY_CIDRS)
            self.assertIn(f"ip daddr {cidr} log prefix", ruleset)
        for cidr in EGRESS_DENY_IPV6_CIDRS:
            self.assertIn(f"ip6 daddr {cidr} log prefix", ruleset)
        self.assertEqual(
            len(EGRESS_DENY_CIDRS) + len(EGRESS_DENY_IPV6_CIDRS),
            ruleset.count("counter drop"),
        )

    def test_denies_precede_general_allows(self) -> None:
        ruleset = policy().ruleset()
        deny_index = ruleset.index(f"ip daddr {HOST_LOOPBACK_ALIAS} log prefix")
        allow_index = ruleset.index("ip daddr 151.101.0.223 tcp dport { 443 } accept")
        self.assertLess(deny_index, allow_index)

    def test_no_accept_rule_targets_a_private_or_metadata_address(self) -> None:
        for address in accept_addresses(policy().ruleset()):
            if address in DEFAULT_DNS_SERVERS:
                continue  # the pinned resolver is the one explicit exception
            self.assertFalse(address.startswith("10."), address)
            self.assertFalse(address.startswith("192.168."), address)
            self.assertFalse(address.startswith("169.254."), address)
            self.assertFalse(address.startswith("127."), address)
            self.assertNotIn(address, {HOST_LOOPBACK_ALIAS, METADATA_ADDRESS})

    def test_every_deny_is_logged_with_the_shared_prefix(self) -> None:
        ruleset = policy().ruleset()
        self.assertIn(f'log prefix "{BLOCKED_HOST_LOG_PREFIX}"', ruleset)
        for line in ruleset.splitlines():
            if "counter drop" in line:
                self.assertIn(BLOCKED_HOST_LOG_PREFIX, line)

    def test_dns_is_pinned_to_the_configured_resolvers(self) -> None:
        default_ruleset = policy().ruleset()
        for server in DEFAULT_DNS_SERVERS:
            self.assertIn(f"ip daddr {server} udp dport 53 accept", default_ruleset)
            self.assertIn(f"ip daddr {server} tcp dport 53 accept", default_ruleset)
        custom = policy(env=fake_env(IGOREK_ISOLATION_DNS_SERVERS="10.0.2.3, 192.0.2.53"))
        self.assertEqual(("10.0.2.3", "192.0.2.53"), custom.dns_servers)
        self.assertIn("ip daddr 192.0.2.53 udp dport 53 accept", custom.ruleset())
        invalid = policy(env=fake_env(IGOREK_ISOLATION_DNS_SERVERS="not-an-ip"))
        self.assertEqual(DEFAULT_DNS_SERVERS, invalid.dns_servers)
        self.assertIn(
            "invalid_dns_server", [warning.reason for warning in invalid.warnings]
        )

    def test_tcp_443_is_the_default_allow_port(self) -> None:
        for entry in policy().allows:
            self.assertEqual(frozenset({443}), entry.ports, entry.host)


def _accept_endpoints(ruleset: str) -> list[tuple[str, int]]:
    found: list[tuple[str, int]] = []
    for line in ruleset.splitlines():
        stripped = line.strip()
        if " accept" not in stripped or "daddr" not in stripped:
            continue
        parts = stripped.split()
        if "dport" not in parts:
            continue
        address = parts[parts.index("daddr") + 1]
        port_token = parts[parts.index("dport") + 1]
        if port_token == "{":
            port_token = parts[parts.index("dport") + 2]
        found.append((address, int(port_token.rstrip("}"))))
    return found


class DomainInterfaceTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self.base_image = root / "base.qcow2"
        self.base_image.write_bytes(b"qcow2")
        self.workspace = root / "workspace"
        self.workspace.mkdir()
        self.overlay_dir = root / "overlays"
        self.root = root

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def spec(self, *, network: bool) -> DomainSpec:
        return DomainSpec(
            session_id="sess-16",
            base_image=self.base_image,
            workspace_host_path=self.workspace,
            overlay_dir=self.overlay_dir,
            network=network,
        )

    def test_domain_xml_carries_exactly_one_restricted_interface(self) -> None:
        xml = build_domain_xml(self.spec(network=True))
        root = ET.fromstring(xml)
        interfaces = root.findall("./devices/interface")
        self.assertEqual(len(interfaces), 1)
        interface = interfaces[0]
        self.assertEqual("user", interface.get("type"))
        model = interface.find("model")
        self.assertIsNotNone(model)
        assert model is not None
        self.assertEqual("virtio", model.get("type"))
        self.assertIsNone(interface.find("source"))
        self.assertIsNone(interface.find("bridge"))
        self.assertIsNone(interface.find("portForward"))
        for forbidden in ("bridge", "hostfwd", "portForward", "openvswitch", "direct"):
            self.assertNotIn(forbidden, xml)

    def test_default_domain_spec_keeps_the_pre_network_shape(self) -> None:
        root = ET.fromstring(build_domain_xml(self.spec(network=False)))
        self.assertEqual([], root.findall(".//interface"))
        root = ET.fromstring(build_domain_xml(self.spec(network=False)))
        self.assertEqual([], root.findall(".//interface"))

    def test_domain_interface_fragment_is_self_contained(self) -> None:
        fragment = ET.fromstring(domain_interface_xml())
        self.assertEqual("user", fragment.get("type"))
        model = fragment.find("model")
        self.assertIsNotNone(model)
        assert model is not None
        self.assertEqual("virtio", model.get("type"))
        self.assertEqual([], fragment.findall("source"))
        self.assertEqual([], fragment.findall("bridge"))


class BlockedHostDiagnosticTest(unittest.TestCase):
    KERNEL_LINE = (
        "Sep 22 10:00:00 igorek kernel: igorek-egress-blocked: IN= OUT=eth0 "
        "SRC=10.0.2.15 DST=169.254.169.254 LEN=60 TOS=0x00 PREC=0x00 TTL=64 "
        "ID=1 DF PROTO=TCP SPT=45123 DPT=80 WINDOW=64240 RES=0x00 SYN URGP=0"
    )
    HOST_ALIAS_LINE = (
        "Sep 22 10:00:01 igorek kernel: igorek-egress-blocked: IN= OUT=eth0 "
        "SRC=10.0.2.15 DST=10.0.2.2 LEN=60 TOS=0x00 PREC=0x00 TTL=64 "
        "ID=2 DF PROTO=TCP SPT=45124 DPT=8080 WINDOW=64240 RES=0x00 SYN URGP=0"
    )

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.workdir = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_kernel_denial_line_maps_to_a_blocked_host_event(self) -> None:
        event = parse_blocked_host_line(
            self.KERNEL_LINE, pinned_hosts={HOST_LOOPBACK_ALIAS: "host-loopback"}
        )
        self.assertIsNotNone(event)
        assert event is not None
        self.assertEqual("169.254.169.254", event.destination)
        self.assertEqual(80, event.port)
        self.assertEqual("tcp", event.protocol)
        self.assertEqual("10.0.2.15", event.source)
        self.assertEqual(45123, event.source_port)
        self.assertEqual("", event.pinned_host)

    def test_pinned_map_names_the_blocked_host_when_known(self) -> None:
        event = parse_blocked_host_line(
            self.HOST_ALIAS_LINE, pinned_hosts={HOST_LOOPBACK_ALIAS: "host-loopback"}
        )
        self.assertIsNotNone(event)
        assert event is not None
        self.assertEqual("10.0.2.2", event.destination)
        self.assertEqual("host-loopback", event.pinned_host)

    def test_unrelated_lines_are_ignored(self) -> None:
        self.assertIsNone(parse_blocked_host_line("random kernel chatter"))
        self.assertIsNone(
            parse_blocked_host_line("igorek-egress-blocked: SRC=10.0.2.15 PROTO=TCP")
        )

    def test_record_uses_the_registered_taxonomy_kind(self) -> None:
        event = parse_blocked_host_line(self.KERNEL_LINE)
        assert event is not None
        record = blocked_host_record(event)
        self.assertEqual("isolation.blocked_host", CcTraceEvent.ISOLATION_BLOCKED_HOST)
        self.assertEqual(CcTraceEvent.ISOLATION_BLOCKED_HOST, record["kind"])
        self.assertIn(record["kind"], all_event_kinds())
        self.assertEqual(BLOCKED_HOST_REASON, record["reason"])
        self.assertEqual("169.254.169.254", record["destination"])
        self.assertEqual(80, record["port"])

    def test_emit_writes_trace_records_and_skips_noise(self) -> None:
        events = emit_blocked_host_lines(
            self.workdir,
            [self.KERNEL_LINE, "noise", self.HOST_ALIAS_LINE],
            pinned_hosts={HOST_LOOPBACK_ALIAS: "host-loopback"},
        )
        self.assertEqual(2, len(events))
        records = [
            json.loads(line)
            for line in (self.workdir / TRACE_FILENAME).read_text(encoding="utf-8").splitlines()
        ]
        self.assertEqual(
            ["isolation.blocked_host", "isolation.blocked_host"],
            [record["kind"] for record in records],
        )
        self.assertEqual("host-loopback", records[1]["pinned_host"])


if __name__ == "__main__":
    unittest.main()
