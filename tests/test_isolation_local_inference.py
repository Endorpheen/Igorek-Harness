#!/usr/bin/env python3
"""Unit tests for the controlled host-local inference route (plan todo 17).

No KVM required. The route is a pure policy artifact over the todo-16 egress
module, so every assertion here is deterministic:

* a local profile (base_url on the host's loopback) gets **exactly one** accept
  rule - the QEMU user-net host alias plus the configured port - and nothing
  else; a non-local profile gets no such rule;
* the guest's own ``127.0.0.1`` is never the host's loopback: the only exposed
  host destination is the gateway alias;
* when the route is disabled by policy the rendered ruleset contains no rule,
  the policy carries the stable reason and a clear "no fallback" message, and a
  probe to the endpoint is dropped;
* the probe evaluator answers from the rendered ruleset itself (first match
  wins, chain default is drop), so "the guest can reach the port and nothing
  else" is checked against the artifact that would be loaded in the guest.

The probe evaluator models a *new* outbound flow to a non-loopback destination;
the guest's own loopback, ``ct state established`` and DHCP lines never carry a
fresh probe and are intentionally not modelled.
"""

from __future__ import annotations

import contextlib
import io
import os
import sys
import unittest
import xml.etree.ElementTree as ET
from collections.abc import Mapping
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(
    os.environ.get("IGOREK_FACTORY_REPO", Path(__file__).resolve().parents[1])
)
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from factory.core import runtime as runtime_module  # noqa: E402
from factory.core.isolation.domain import build_domain_xml  # noqa: E402
from factory.core.isolation.network import (  # noqa: E402
    BASELINE_ALLOW_HOSTS,
    DEFAULT_DNS_SERVERS,
    EGRESS_DENY_CIDRS,
    ENV_LOCAL_INFERENCE,
    HOST_LOOPBACK_ALIAS,
    HOST_LOOPBACK_HOSTS,
    LOCAL_INFERENCE_DISABLED_REASON,
    LOCAL_INFERENCE_GATE_AUTO,
    LOCAL_INFERENCE_GATE_OFF,
    LOCAL_INFERENCE_GATE_ON,
    METADATA_ADDRESS,
    NetworkPolicy,
    build_network_policy,
    domain_interface_xml,
    evaluate_egress,
)
from factory.core.isolation import network_cli  # noqa: E402
from factory.core.isolation.domain import DomainSpec  # noqa: E402

# Global (publicly routable) fixture addresses: every public allow must pin.
FIXTURE_ADDRESSES: dict[str, tuple[str, ...]] = {
    "pypi.org": ("151.101.0.223",),
    "files.pythonhosted.org": ("151.101.0.63",),
    "api.github.com": ("140.82.121.5",),
    "api.telegram.org": ("149.154.167.220",),
    "openrouter.ai": ("104.18.2.1",),
    "opencode.ai": ("104.18.3.1",),
    "inference.runanywhere.ai": ("76.76.21.21",),
}
FIXTURE_MCP_SERVERS = ""
LOCAL_BASE_URL = "http://127.0.0.1:8001"
NON_LOCAL_BASE_URL = "https://inference.runanywhere.ai/v1"


def fixture_resolver(
    host: str, mapping: Mapping[str, tuple[str, ...]] = FIXTURE_ADDRESSES
) -> tuple[str, ...]:
    return tuple(mapping.get(host, ()))


def fake_env(**overrides: str) -> dict[str, str]:
    env = {
        "MCP_SERVERS": FIXTURE_MCP_SERVERS,
        "FACTORY_BOLTBOOK_HEARTBEAT_ENABLED": "false",
    }
    env.update(overrides)
    return env


def policy(
    base_url: str,
    *,
    profile: str = "cc-local",
    env: Mapping[str, str] | None = None,
    local_inference: str | None = None,
) -> NetworkPolicy:
    return build_network_policy(
        profile,
        base_url=base_url,
        env=fake_env() if env is None else env,
        resolver=fixture_resolver,
        local_inference=local_inference,
    )


def accept_targets(ruleset: str) -> list[tuple[str, frozenset[int], str]]:
    """Every ``ip/ip6 daddr <addr> ... accept`` rule as ``(addr, ports, proto)``."""
    found: list[tuple[str, frozenset[int], str]] = []
    for line in ruleset.splitlines():
        stripped = line.strip()
        if stripped.startswith("#") or " accept" not in stripped:
            continue
        parts = stripped.split()
        if "daddr" not in parts:
            continue
        address = parts[parts.index("daddr") + 1]
        protocol = ""
        if "tcp" in parts:
            protocol = "tcp"
        elif "udp" in parts:
            protocol = "udp"
        ports: frozenset[int] = frozenset()
        if "dport" in parts:
            tokens = parts[parts.index("dport") + 1 :]
            if tokens and tokens[0] == "{":
                tokens = tokens[1:]
                collected: list[int] = []
                for token in tokens:
                    if token == "}":
                        break
                    collected.append(int(token.rstrip(",")))
                ports = frozenset(collected)
            elif tokens:
                ports = frozenset({int(tokens[0].rstrip("}"))})
        found.append((address, ports, protocol))
    return found


def gateway_rules(policy_result: NetworkPolicy) -> list[tuple[str, frozenset[int], str]]:
    return [
        target
        for target in accept_targets(policy_result.ruleset())
        if target[0] == HOST_LOOPBACK_ALIAS
    ]


class LocalProfileRouteTest(unittest.TestCase):
    def test_local_profile_renders_exactly_one_gateway_rule(self) -> None:
        result = policy(LOCAL_BASE_URL)
        route = result.local_inference
        self.assertIsNotNone(route)
        assert route is not None
        self.assertTrue(route.enabled)
        self.assertEqual("127.0.0.1", route.target_host)
        self.assertEqual(8001, route.target_port)
        self.assertEqual("127.0.0.1:8001", route.target)
        self.assertEqual(HOST_LOOPBACK_ALIAS, route.gateway_address)
        self.assertEqual(LOCAL_INFERENCE_GATE_AUTO, route.policy)
        self.assertEqual("", route.reason)
        self.assertEqual(
            [(HOST_LOOPBACK_ALIAS, frozenset({8001}), "tcp")], gateway_rules(result)
        )
        # The route line names the host-local endpoint it stands for.
        self.assertIn("local-inference 127.0.0.1:8001", result.ruleset())

    def test_localhost_name_and_wildcard_bind_are_host_local_too(self) -> None:
        for base, port in (
            ("http://localhost:11434/v1", 11434),
            ("http://0.0.0.0:8080/v1", 8080),
        ):
            with self.subTest(base=base):
                result = policy(base)
                route = result.local_inference
                self.assertIsNotNone(route)
                assert route is not None
                self.assertTrue(route.enabled)
                self.assertEqual(port, route.target_port)
                self.assertEqual(
                    [(HOST_LOOPBACK_ALIAS, frozenset({port}), "tcp")],
                    gateway_rules(result),
                )

    def test_non_local_profile_gets_no_host_route(self) -> None:
        result = policy(NON_LOCAL_BASE_URL)
        self.assertIsNone(result.local_inference)
        self.assertEqual([], gateway_rules(result))
        for address, _ports, _protocol in accept_targets(result.ruleset()):
            self.assertNotIn(address, EGRESS_DENY_CIDRS, address)

    def test_lan_endpoint_gets_no_host_route(self) -> None:
        result = policy("http://192.168.1.50:8000/v1")
        self.assertIsNone(result.local_inference)
        self.assertEqual([], gateway_rules(result))
        self.assertIn(
            "local_endpoint_not_egress_allowed",
            [warning.reason for warning in result.warnings],
        )

    def test_route_is_not_added_to_the_public_allowlist(self) -> None:
        result = policy(LOCAL_BASE_URL)
        self.assertNotIn("127.0.0.1", result.allow_hosts())
        self.assertNotIn(("127.0.0.1", 8001), result.pinned_endpoints())

    def test_gate_argument_overrides_the_environment(self) -> None:
        result = policy(
            LOCAL_BASE_URL,
            env=fake_env(**{ENV_LOCAL_INFERENCE: "auto"}),
            local_inference="off",
        )
        route = result.local_inference
        self.assertIsNotNone(route)
        assert route is not None
        self.assertFalse(route.enabled)
        self.assertEqual(LOCAL_INFERENCE_GATE_OFF, route.policy)


class RoutePolicyGateTest(unittest.TestCase):
    def test_disabled_by_policy_renders_no_rule_and_a_clear_error(self) -> None:
        result = policy(LOCAL_BASE_URL, env=fake_env(**{ENV_LOCAL_INFERENCE: "off"}))
        route = result.local_inference
        self.assertIsNotNone(route)
        assert route is not None
        self.assertFalse(route.enabled)
        self.assertEqual(LOCAL_INFERENCE_DISABLED_REASON, route.reason)
        self.assertEqual(LOCAL_INFERENCE_GATE_OFF, route.policy)
        self.assertEqual([], gateway_rules(result))
        # Still failed closed: the host-loopback deny is present and unmodified.
        self.assertIn(f"ip daddr {HOST_LOOPBACK_ALIAS} log prefix", result.ruleset())
        warnings = {
            warning.reason
            for warning in result.warnings
            if warning.subject == "127.0.0.1:8001"
        }
        self.assertIn(LOCAL_INFERENCE_DISABLED_REASON, warnings)
        message = route.status_message()
        self.assertIn("disabled by policy", message)
        self.assertIn("IGOREK_ISOLATION_LOCAL_INFERENCE=off", message)
        self.assertIn("127.0.0.1:8001", message)
        self.assertIn("no unrestricted network fallback", message)
        self.assertFalse(evaluate_egress(result.ruleset(), HOST_LOOPBACK_ALIAS, 8001).allowed)

    def test_enabled_route_status_message_names_the_gateway(self) -> None:
        route = policy(LOCAL_BASE_URL).local_inference
        assert route is not None
        self.assertIn("enabled", route.status_message())
        self.assertIn(f"{HOST_LOOPBACK_ALIAS}:8001", route.status_message())

    def test_unknown_gate_value_fails_closed(self) -> None:
        result = policy(LOCAL_BASE_URL, env=fake_env(**{ENV_LOCAL_INFERENCE: "sometimes"}))
        route = result.local_inference
        self.assertIsNotNone(route)
        assert route is not None
        self.assertFalse(route.enabled)
        self.assertEqual(LOCAL_INFERENCE_GATE_OFF, route.policy)
        self.assertEqual([], gateway_rules(result))
        self.assertIn(
            "invalid_local_inference_gate",
            [warning.reason for warning in result.warnings],
        )

    def test_gate_on_with_a_non_local_endpoint_warns(self) -> None:
        result = policy(NON_LOCAL_BASE_URL, env=fake_env(**{ENV_LOCAL_INFERENCE: "on"}))
        self.assertIsNone(result.local_inference)
        self.assertEqual(LOCAL_INFERENCE_GATE_ON, result.local_inference_gate)
        self.assertIn(
            "local_inference_not_host_loopback",
            [warning.reason for warning in result.warnings],
        )
        self.assertEqual([], gateway_rules(result))

    def test_ipv6_loopback_endpoint_is_refused(self) -> None:
        result = policy("http://[::1]:8001/v1")
        self.assertIsNone(result.local_inference)
        self.assertEqual([], gateway_rules(result))
        self.assertIn(
            "local_inference_unsupported_family",
            [warning.reason for warning in result.warnings],
        )

    def test_default_gate_is_auto(self) -> None:
        result = policy(LOCAL_BASE_URL, env=fake_env())
        route = result.local_inference
        assert route is not None
        self.assertEqual(LOCAL_INFERENCE_GATE_AUTO, route.policy)
        self.assertTrue(route.enabled)


class RulesetShapeTest(unittest.TestCase):
    def test_route_rule_precedes_the_host_loopback_deny(self) -> None:
        ruleset = policy(LOCAL_BASE_URL).ruleset()
        accept_index = ruleset.index(f"ip daddr {HOST_LOOPBACK_ALIAS} tcp dport")
        deny_index = ruleset.index(f"ip daddr {HOST_LOOPBACK_ALIAS} log prefix")
        self.assertLess(accept_index, deny_index)
        self.assertEqual(1, ruleset.count(f"ip daddr {HOST_LOOPBACK_ALIAS} tcp dport"))
        self.assertEqual(1, ruleset.count(f"ip daddr {HOST_LOOPBACK_ALIAS} log prefix"))

    def test_ruleset_never_widens_the_loopback_or_lan(self) -> None:
        for base_url in (LOCAL_BASE_URL, NON_LOCAL_BASE_URL):
            with self.subTest(base_url=base_url):
                ruleset = policy(base_url).ruleset()
                for wildcard in ("0.0.0.0/0", "::/0", "127.0.0.0/8", "10.0.0.0/8"):
                    self.assertNotIn(f"daddr {wildcard} tcp dport", ruleset)
                for address, _ports, _protocol in accept_targets(ruleset):
                    if address == HOST_LOOPBACK_ALIAS:
                        self.assertEqual(LOCAL_BASE_URL, base_url)
                        continue
                    if address in DEFAULT_DNS_SERVERS:
                        continue
                    self.assertFalse(address.startswith("127."), address)
                    self.assertFalse(address.startswith("10."), address)
                    self.assertFalse(address.startswith("192.168."), address)
                    self.assertFalse(address.startswith("169.254."), address)

    def test_ruleset_header_documents_the_threat_model(self) -> None:
        ruleset = policy(LOCAL_BASE_URL).ruleset()
        self.assertIn("DNS-rebinding", ruleset)
        self.assertIn("IP-literal", ruleset)

    def test_host_loopback_hosts_match_runtime_local_model_hosts(self) -> None:
        self.assertEqual(
            set(runtime_module._LOCAL_MODEL_HOSTS),  # noqa: SLF001 - parity pin
            set(HOST_LOOPBACK_HOSTS),
        )

    def test_user_net_interface_does_not_restrict_host_access(self) -> None:
        fragment = domain_interface_xml()
        self.assertNotIn("restrict", fragment)
        root = ET.fromstring(fragment)
        self.assertEqual([], root.findall("source"))
        self.assertEqual([], root.findall("portForward"))
        self.assertNotIn("hostfwd", fragment)

    def test_route_does_not_change_the_domain_interface(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            base = root / "base.qcow2"
            base.write_bytes(b"qcow2")
            workspace = root / "workspace"
            workspace.mkdir()
            spec = DomainSpec(
                session_id="sess-17",
                base_image=base,
                workspace_host_path=workspace,
                overlay_dir=root / "overlays",
                network=True,
            )
            xml = build_domain_xml(spec)
            self.assertEqual(1, len(ET.fromstring(xml).findall("./devices/interface")))
            self.assertNotIn("hostfwd", xml)
            self.assertNotIn("bridge", xml)


class EgressProbeTest(unittest.TestCase):
    def test_probe_allows_the_configured_port_only(self) -> None:
        ruleset = policy(LOCAL_BASE_URL).ruleset()
        allowed = evaluate_egress(ruleset, HOST_LOOPBACK_ALIAS, 8001)
        self.assertTrue(allowed.allowed)
        self.assertEqual("allowed", allowed.reason)
        self.assertIn("tcp dport { 8001 }", allowed.matched_rule)
        for port in (8000, 8002, 8022, 80, 443, 22, 11434):
            with self.subTest(port=port):
                probe = evaluate_egress(ruleset, HOST_LOOPBACK_ALIAS, port)
                self.assertFalse(probe.allowed)
                self.assertIn(probe.reason, {"addr_denied", "default_deny"})
        udp = evaluate_egress(ruleset, HOST_LOOPBACK_ALIAS, 8001, protocol="udp")
        self.assertFalse(udp.allowed)

    def test_probe_without_a_route_is_dropped(self) -> None:
        for base_url in (NON_LOCAL_BASE_URL, "http://192.168.1.50:8000/v1"):
            with self.subTest(base_url=base_url):
                probe = evaluate_egress(policy(base_url).ruleset(), HOST_LOOPBACK_ALIAS, 8001)
                self.assertFalse(probe.allowed)

    def test_probe_of_lan_and_metadata_is_dropped(self) -> None:
        ruleset = policy(LOCAL_BASE_URL).ruleset()
        for destination, port in (
            (METADATA_ADDRESS, 80),
            ("192.168.1.1", 8001),
            ("10.0.0.5", 8001),
            ("127.0.0.1", 8001),
        ):
            with self.subTest(destination=destination):
                probe = evaluate_egress(ruleset, destination, port)
                self.assertFalse(probe.allowed)
                self.assertIn(probe.reason, {"addr_denied", "default_deny"})

    def test_probe_reports_default_deny_for_an_unlisted_public_address(self) -> None:
        probe = evaluate_egress(policy(LOCAL_BASE_URL).ruleset(), "1.1.1.1", 443)
        self.assertFalse(probe.allowed)
        self.assertEqual("default_deny", probe.reason)

    def test_probe_accepts_a_pinned_public_allow(self) -> None:
        probe = evaluate_egress(policy(NON_LOCAL_BASE_URL).ruleset(), "76.76.21.21", 443)
        self.assertTrue(probe.allowed)

    def test_probe_accepts_a_disabled_route_after_the_gate_flips_on(self) -> None:
        disabled = policy(LOCAL_BASE_URL, env=fake_env(**{ENV_LOCAL_INFERENCE: "off"}))
        enabled = policy(LOCAL_BASE_URL, env=fake_env(**{ENV_LOCAL_INFERENCE: "on"}))
        self.assertFalse(evaluate_egress(disabled.ruleset(), HOST_LOOPBACK_ALIAS, 8001).allowed)
        self.assertTrue(evaluate_egress(enabled.ruleset(), HOST_LOOPBACK_ALIAS, 8001).allowed)


class NetworkCliTest(unittest.TestCase):
    def test_report_shows_the_enabled_route(self) -> None:
        report = network_cli.render_policy_report(policy(LOCAL_BASE_URL))
        self.assertIn("local inference route: enabled", report)
        self.assertIn("10.0.2.2:8001", report)

    def test_report_shows_the_disabled_route_error(self) -> None:
        result = policy(LOCAL_BASE_URL, env=fake_env(**{ENV_LOCAL_INFERENCE: "off"}))
        report = network_cli.render_policy_report(result)
        self.assertIn("local inference route: DISABLED", report)
        self.assertIn("no unrestricted network fallback", report)

    def test_cli_probe_accepts_and_drops(self) -> None:
        env = fake_env(**{ENV_LOCAL_INFERENCE: "off"})
        with mock.patch.dict(os.environ, env, clear=False):
            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                code = network_cli.main(
                    [
                        "--profile",
                        "cc-local",
                        "--base-url",
                        LOCAL_BASE_URL,
                        "--no-resolve",
                        "--probe",
                        f"{HOST_LOOPBACK_ALIAS}:8001",
                    ]
                )
        self.assertEqual(0, code)
        text = output.getvalue()
        self.assertIn("DROPPED", text)

    def test_cli_probe_reports_allow_with_the_route(self) -> None:
        output = io.StringIO()
        with mock.patch.dict(
            os.environ, {ENV_LOCAL_INFERENCE: "auto"}, clear=False
        ):
            with contextlib.redirect_stdout(output):
                code = network_cli.main(
                    [
                        "--base-url",
                        LOCAL_BASE_URL,
                        "--no-resolve",
                        "--probe",
                        f"{HOST_LOOPBACK_ALIAS}:8001",
                    ]
                )
        self.assertEqual(0, code)
        text = output.getvalue()
        self.assertIn("ALLOWED", text)
        self.assertIn("tcp dport { 8001 }", text)

    def test_cli_rejects_a_malformed_probe(self) -> None:
        with self.assertRaises(SystemExit) as caught:
            network_cli.main(["--probe", "not-a-probe"])
        self.assertEqual(2, caught.exception.code)


if __name__ == "__main__":
    unittest.main()
