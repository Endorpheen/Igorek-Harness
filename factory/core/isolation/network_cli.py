"""CLI view of the per-session egress policy (plan todo 16).

Host-side operator helper, kept separate from the policy module so importing the
policy never builds an argument parser (same split as ``base_image_cli``).
Prints the allow entries, the domain network fragment and the nftables ruleset
for one profile without starting a VM; used for manual QA and incident review.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Sequence

from .network import (
    EgressProbe,
    NetworkPolicy,
    build_network_policy,
    domain_interface_xml,
    evaluate_egress,
)

__all__ = ["main", "render_policy_report", "render_probe_report"]


def render_policy_report(policy: NetworkPolicy) -> str:
    """Human-readable policy dump used for manual QA and operator review."""
    lines = [
        f"profile: {policy.profile or '<none>'}",
        f"fallback (default provider set): {'yes' if policy.fallback else 'no'}",
        f"dns servers: {', '.join(policy.dns_servers)}",
        f"local inference gate: {policy.local_inference_gate}",
        f"warnings: {len(policy.warnings)}",
    ]
    for warning in policy.warnings:
        detail = f" ({warning.detail})" if warning.detail else ""
        lines.append(
            f"  - [{warning.reason}] {warning.subject}{detail} <- {warning.source}"
        )
    lines.append(f"allow entries: {len(policy.allows)}")
    for entry in sorted(policy.allows, key=lambda item: item.host):
        addresses = ", ".join(entry.addresses) if entry.addresses else "UNPINNED (no rule)"
        ports = ",".join(str(port) for port in sorted(entry.ports))
        lines.append(f"  - {entry.host}:{ports} -> {addresses} ({entry.source})")
    lines.append(_render_route_line(policy))
    lines.append("")
    lines.append("--- domain network fragment ---")
    lines.append(domain_interface_xml().rstrip("\n"))
    lines.append("")
    lines.append("--- nftables ruleset ---")
    lines.append(policy.ruleset().rstrip("\n"))
    return "\n".join(lines) + "\n"


def render_probe_report(probe: EgressProbe) -> str:
    """One egress probe verdict against the rendered ruleset."""
    verdict = "ALLOWED" if probe.allowed else "DROPPED"
    lines = [
        "--- egress probe ---",
        f"probe {probe.destination}:{probe.port}/{probe.protocol} -> {verdict}"
        f" ({probe.reason})",
    ]
    if probe.matched_rule:
        lines.append(f"matched rule: {probe.matched_rule}")
    return "\n".join(lines) + "\n"


def _render_route_line(policy: NetworkPolicy) -> str:
    route = policy.local_inference
    if route is None:
        return "local inference route: none"
    if not route.enabled:
        return f"local inference route: DISABLED ({route.reason}) - {route.status_message()}"
    return (
        f"local inference route: enabled {route.target} -> guest"
        f" {route.gateway_address}:{route.target_port} (policy {route.policy})"
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m factory.core.isolation.network_cli",
        description=(
            "Print the per-session egress allowlist, the domain network "
            "fragment and the nftables ruleset."
        ),
    )
    parser.add_argument("--profile", default="", help="worker profile (e.g. cc-wally)")
    parser.add_argument(
        "--base-url", default="", help="resolved provider base URL of the session"
    )
    parser.add_argument(
        "--no-resolve",
        action="store_true",
        help="skip DNS pinning (renders the deny-only ruleset)",
    )
    parser.add_argument(
        "--probe",
        default="",
        metavar="HOST:PORT",
        help="evaluate one outbound flow against the rendered ruleset",
    )
    parser.add_argument(
        "--probe-proto",
        default="tcp",
        choices=("tcp", "udp"),
        help="protocol of --probe (default: tcp)",
    )
    return parser


def _no_resolver(host: str) -> tuple[str, ...]:
    return ()


def _parse_probe(raw: str) -> tuple[str, int]:
    host, separator, port_text = raw.rpartition(":")
    if not separator or not host.strip():
        raise ValueError(raw)
    try:
        port = int(port_text, 10)
    except ValueError:
        raise ValueError(raw) from None
    if not 1 <= port <= 65535:
        raise ValueError(raw)
    return host.strip().strip("[]"), port


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    resolver = _no_resolver if args.no_resolve else None
    policy = build_network_policy(args.profile, base_url=args.base_url, resolver=resolver)
    sys.stdout.write(render_policy_report(policy))
    probe_text = args.probe.strip()
    if probe_text:
        try:
            destination, port = _parse_probe(probe_text)
        except ValueError:
            parser.error(f"invalid --probe {probe_text!r}; expected HOST:PORT")
        probe = evaluate_egress(policy.ruleset(), destination, port, args.probe_proto)
        sys.stdout.write(render_probe_report(probe))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
