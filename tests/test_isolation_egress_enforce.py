#!/usr/bin/env python3
"""Runtime enforcement of the rendered egress policy (plan todo 40).

The todo-16/17 policy generators were correct but had no production caller: a
real VM would have run on the QEMU user-net defaults (including host-loopback
reachability) and the deny rules would never have been installed. This suite
pins the enforcement half of that contract:

* the rendered per-session ruleset (allow entries, the single local-inference
  route, the pinned DNS accepts and the logged denies) is what ``nft -f``
  receives - not a "wired" placeholder;
* an apply that the kernel did not take is refused, never reported as applied;
* the boot baseline is the strictest ruleset (default deny + DNS, no allow);
* the base image recipe installs and enables the in-guest boot step, and the
  boot step applies exactly the rendered baseline;
* a blocked probe (a kernel denial line) becomes an ``isolation.blocked_host``
  trace record with the pinned host named;
* a secret-tainted guest env is refused before dispatch (manager suite).
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

REPO_ROOT = Path(
    os.environ.get("IGOREK_FACTORY_REPO", Path(__file__).resolve().parents[1])
)
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from factory.core.cc_backend_trace import TRACE_FILENAME  # noqa: E402
from factory.core.isolation.egress_enforce import (  # noqa: E402
    BOOT_BASELINE_PROFILE,
    EGRESS_BOOT_BIN,
    EGRESS_TABLE_REF,
    EGRESS_UNIT_PATH,
    GUEST_DMESG_SYSCTL,
    NFT_BIN,
    NFT_BIN_ENV,
    REASON_APPLY_FAILED,
    REASON_INVALID_RULESET,
    REASON_NOT_VERIFIED,
    NftResult,
    apply_egress_ruleset,
    boot_baseline_ruleset,
    main as egress_main,
    resolve_nft_bin,
)
from factory.core.isolation.errors import IsolationError  # noqa: E402
from factory.core.isolation.session_egress import (  # noqa: E402
    SessionEgress,
    build_session_egress,
)
from factory.core.isolation.network import (  # noqa: E402
    BLOCKED_HOST_LOG_PREFIX,
    DEFAULT_DNS_SERVERS,
    HOST_LOOPBACK_ALIAS,
    build_network_policy,
)
from factory.core.isolation.network_blocked import BlockedHostWatcher  # noqa: E402

SCRIPT = REPO_ROOT / "factory" / "scripts" / "build_isolation_image.sh"

# Public fixture addresses (the same set the todo-16 policy suite uses), so the
# rendered allow entries are deterministic and need no DNS.
FIXTURE_ADDRESSES: dict[str, tuple[str, ...]] = {
    "pypi.org": ("151.101.0.223",),
    "files.pythonhosted.org": ("151.101.0.63",),
    "api.github.com": ("140.82.121.5",),
    "api.telegram.org": ("149.154.167.220",),
    "openrouter.ai": ("104.18.2.1",),
    "opencode.ai": ("104.18.3.1",),
    "inference.runanywhere.ai": ("76.76.21.21",),
    "mcp.grep.app": ("216.198.79.1",),
    "api.boltbook.ai": ("104.18.5.1",),
}
FIXTURE_BASE_URL = "https://inference.runanywhere.ai/v1"
LOCAL_BASE_URL = "http://127.0.0.1:8001"
FIXTURE_MCP_SERVERS = "grep_app"

KERNEL_DENIAL_LINE = (
    "igorek-egress-blocked: IN=eth0 OUT= MAC=52:54:00:12:34:56 SRC=10.0.2.15 "
    "DST=10.0.2.2 LEN=60 PROTO=TCP SPT=43210 DPT=8001 WINDOW=64240 RES=0x00 SYN"
)


def fixture_resolver(host: str) -> tuple[str, ...]:
    return tuple(FIXTURE_ADDRESSES.get(host, ()))


def fake_env() -> dict[str, str]:
    return {
        "MCP_SERVERS": FIXTURE_MCP_SERVERS,
        "FACTORY_BOLTBOOK_HEARTBEAT_ENABLED": "true",
    }


def accept_daddr_targets(ruleset: str) -> set[str]:
    """Every ``daddr`` target of an accept rule in a rendered ruleset."""
    targets: set[str] = set()
    for raw in ruleset.splitlines():
        line = raw.split("#", 1)[0].strip()
        tokens = line.split()
        if not tokens or tokens[-1] != "accept" or "daddr" not in tokens:
            continue
        targets.add(tokens[tokens.index("daddr") + 1])
    return targets


def _listing_of(applied: str) -> str:
    """Emulate ``nft list table``: the applied rules, comments stripped."""
    lines: list[str] = []
    for raw in applied.splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line or line.startswith("destroy ") or line in {"{", "}"}:
            continue
        lines.append("\t\t" + line)
    return (
        f"table {EGRESS_TABLE_REF} {{\n\tchain output {{\n"
        + "\n".join(lines)
        + "\n\t}\n}\n"
    )


class FakeNft:
    """Fake ``nft``: records argv+stdin and answers a listing of what it took."""

    def __init__(
        self,
        *,
        apply_rc: int = 0,
        list_rc: int = 0,
        listing: str | None = None,
    ) -> None:
        self.calls: list[tuple[tuple[str, ...], str]] = []
        self.applied = ""
        self._apply_rc = apply_rc
        self._list_rc = list_rc
        self._listing = listing

    def __call__(self, argv, *, stdin: str = "") -> NftResult:
        self.calls.append((tuple(argv), stdin))
        if "-f" in argv:
            self.applied = stdin
            if self._apply_rc != 0:
                return NftResult(self._apply_rc, "", "nft: syntax error")
            return NftResult(0, "", "")
        if self._list_rc != 0:
            return NftResult(self._list_rc, "", "nft: no such table")
        text = self._listing if self._listing is not None else _listing_of(self.applied)
        return NftResult(0, text, "")


class ApplyEgressRulesetTest(unittest.TestCase):
    """The rendered ruleset must reach the kernel and be verified there."""

    def test_apply_feeds_the_rendered_session_ruleset_to_nft(self) -> None:
        # Given: a per-session policy rendered for a known profile
        egress = build_session_egress(
            "cc-wally",
            base_url=FIXTURE_BASE_URL,
            env=fake_env(),
            resolver=fixture_resolver,
        )
        runner = FakeNft()

        # When: the rendered ruleset is applied
        result = apply_egress_ruleset(egress.ruleset, runner=runner)

        # Then: the kernel received exactly that ruleset, replaced atomically
        self.assertTrue(result.applied)
        self.assertTrue(result.verified)
        self.assertEqual(2, len(runner.calls))
        argv, stdin = runner.calls[0]
        self.assertEqual((NFT_BIN, "-f", "-"), argv)
        self.assertTrue(stdin.startswith(f"destroy table {EGRESS_TABLE_REF}\n"))
        self.assertIn("table inet igorek_egress {", stdin)
        self.assertIn("policy drop", stdin)
        self.assertIn(BLOCKED_HOST_LOG_PREFIX, stdin)
        self.assertIn(
            "ip daddr 151.101.0.223 tcp dport { 443 } accept", stdin
        )
        self.assertEqual(egress.ruleset, stdin.split("\n", 1)[1])
        # Then: the listing call is the verification, not a second apply
        self.assertEqual((NFT_BIN, "list", "table", "inet", "igorek_egress"), runner.calls[1][0])
        self.assertEqual(result.rule_count, result.listed_rules)

    def test_apply_uses_the_file_capped_nft_helper_when_configured(self) -> None:
        # Given: the guest runner points nft at the file-capped helper copy
        egress = build_session_egress(
            "cc-local",
            base_url=LOCAL_BASE_URL,
            env=fake_env(),
            resolver=fixture_resolver,
        )
        runner = FakeNft()

        # When: the ruleset is applied under the helper override
        with mock.patch.dict(os.environ, {NFT_BIN_ENV: "/usr/local/bin/igorek-nft"}):
            apply_egress_ruleset(egress.ruleset, runner=runner)

        # Then: both the apply and the verification used the helper, not plain nft
        self.assertEqual(("/usr/local/bin/igorek-nft", "-f", "-"), runner.calls[0][0])
        self.assertEqual(
            ("/usr/local/bin/igorek-nft", "list", "table", "inet", "igorek_egress"),
            runner.calls[1][0],
        )

    def test_resolve_nft_bin_defaults_to_plain_nft(self) -> None:
        self.assertEqual(NFT_BIN, resolve_nft_bin({}))

    def test_apply_carries_the_local_inference_route_when_enabled(self) -> None:
        # Given: a local profile whose host service is the only host exception
        egress = build_session_egress(
            "cc-local",
            base_url=LOCAL_BASE_URL,
            env=fake_env(),
            resolver=fixture_resolver,
        )
        runner = FakeNft()

        # When: it is applied
        apply_egress_ruleset(egress.ruleset, runner=runner)

        # Then: exactly one gateway-alias accept for that port is in the batch
        self.assertIn(
            f"ip daddr {HOST_LOOPBACK_ALIAS} tcp dport {{ 8001 }} accept",
            runner.applied,
        )
        self.assertEqual(
            1, runner.applied.count(f"ip daddr {HOST_LOOPBACK_ALIAS} tcp dport")
        )
        # Then: the host-loopback deny for every other port is still rendered
        self.assertIn(
            f'ip daddr {HOST_LOOPBACK_ALIAS} log prefix "{BLOCKED_HOST_LOG_PREFIX}"'
            " counter drop",
            runner.applied,
        )

    def test_apply_carries_a_host_local_proxy_port_when_configured(self) -> None:
        # Given: the todo-18 egress-auth proxy port (host-local, single port)
        egress = build_session_egress(
            "cc-wally",
            base_url=FIXTURE_BASE_URL,
            env=fake_env(),
            resolver=fixture_resolver,
            host_local_ports=(5011,),
        )
        runner = FakeNft()

        # When: it is applied
        apply_egress_ruleset(egress.ruleset, runner=runner)

        # Then: the proxy port is the one host-local accept
        self.assertIn(
            f"ip daddr {HOST_LOOPBACK_ALIAS} tcp dport {{ 5011 }} accept",
            runner.applied,
        )
        self.assertEqual(
            1, runner.applied.count(f"ip daddr {HOST_LOOPBACK_ALIAS} tcp dport")
        )

    def test_apply_refuses_when_nft_rejects_the_ruleset(self) -> None:
        runner = FakeNft(apply_rc=1)
        with self.assertRaises(IsolationError) as caught:
            apply_egress_ruleset(boot_baseline_ruleset(), runner=runner)
        self.assertEqual(REASON_APPLY_FAILED, caught.exception.code)

    def test_apply_refuses_a_success_the_kernel_did_not_take(self) -> None:
        """Adversarial: exit 0 with an empty table must never count as applied."""
        runner = FakeNft(listing=f"table {EGRESS_TABLE_REF} {{\n}}\n")
        with self.assertRaises(IsolationError) as caught:
            apply_egress_ruleset(boot_baseline_ruleset(), runner=runner)
        self.assertEqual(REASON_NOT_VERIFIED, caught.exception.code)

    def test_apply_refuses_when_the_listing_cannot_be_read(self) -> None:
        runner = FakeNft(list_rc=1)
        with self.assertRaises(IsolationError) as caught:
            apply_egress_ruleset(boot_baseline_ruleset(), runner=runner)
        self.assertEqual(REASON_NOT_VERIFIED, caught.exception.code)

    def test_apply_refuses_a_ruleset_that_is_not_the_default_deny_table(self) -> None:
        with self.assertRaises(IsolationError) as caught:
            apply_egress_ruleset(
                "table inet other { chain output { policy accept; } }",
                runner=FakeNft(),
            )
        self.assertEqual(REASON_INVALID_RULESET, caught.exception.code)


class BootBaselineTest(unittest.TestCase):
    """The boot-time ruleset is the strictest one: deny everything but DNS."""

    def test_boot_baseline_is_default_deny_with_dns_and_no_allows(self) -> None:
        ruleset = boot_baseline_ruleset()
        self.assertIn(BOOT_BASELINE_PROFILE, ruleset)
        self.assertIn("policy drop", ruleset)
        self.assertEqual(set(DEFAULT_DNS_SERVERS), accept_daddr_targets(ruleset))
        self.assertIn(BLOCKED_HOST_LOG_PREFIX, ruleset)

    def test_module_entrypoint_renders_the_boot_baseline(self) -> None:
        result = subprocess.run(
            [sys.executable, "-m", "factory.core.isolation.egress_enforce", "--render"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(0, result.returncode, result.stderr)
        self.assertIn("policy drop", result.stdout)

    def test_boot_baseline_renders_the_host_loopback_deny(self) -> None:
        ruleset = boot_baseline_ruleset()
        self.assertIn(
            f'ip daddr {HOST_LOOPBACK_ALIAS} log prefix "{BLOCKED_HOST_LOG_PREFIX}"'
            " counter drop",
            ruleset,
        )

    def test_boot_cli_applies_the_rendered_default_deny_ruleset(self) -> None:
        runner = FakeNft()
        code = egress_main(["--boot-baseline"], runner=runner)
        self.assertEqual(0, code)
        self.assertEqual(2, len(runner.calls))
        self.assertIn("policy drop", runner.applied)
        self.assertEqual(set(DEFAULT_DNS_SERVERS), accept_daddr_targets(runner.applied))


class SessionEgressPlanTest(unittest.TestCase):
    """The per-session plan is the host-rendered decision carried to the guest."""

    def test_plan_renders_the_same_ruleset_as_the_policy_generator(self) -> None:
        egress = build_session_egress(
            "cc-wally",
            base_url=FIXTURE_BASE_URL,
            env=fake_env(),
            resolver=fixture_resolver,
        )
        policy = build_network_policy(
            "cc-wally",
            base_url=FIXTURE_BASE_URL,
            env=fake_env(),
            resolver=fixture_resolver,
        )
        self.assertEqual(policy.ruleset(), egress.ruleset)
        self.assertEqual(policy.allow_hosts(), egress.allow_hosts)
        self.assertEqual(policy.address_hosts(), egress.pinned_map())

    def test_plan_wire_round_trip_keeps_the_ruleset_and_the_pinned_map(self) -> None:
        egress = build_session_egress(
            "cc-wally",
            base_url=FIXTURE_BASE_URL,
            env=fake_env(),
            resolver=fixture_resolver,
        )
        restored = SessionEgress.from_wire(egress.to_wire())
        self.assertEqual(egress, restored)
        self.assertEqual("pypi.org", restored.pinned_map()["151.101.0.223"])

    def test_plan_wire_section_passes_the_channel_payload_policy(self) -> None:
        """The rendered ruleset must be able to cross the real channel."""
        from factory.core.isolation.channel import (
            ChannelConfig,
            MessageKind,
            payload_policy_violation,
        )

        for profile, base_url in (("cc-wally", FIXTURE_BASE_URL), ("cc-local", LOCAL_BASE_URL)):
            with self.subTest(profile=profile):
                egress = build_session_egress(
                    profile,
                    base_url=base_url,
                    env=fake_env(),
                    resolver=fixture_resolver,
                )
                self.assertIsNone(
                    payload_policy_violation(
                        MessageKind.TASK, {"egress": egress.to_wire()}, ChannelConfig()
                    )
                )

    def test_plan_refuses_a_malformed_wire_section(self) -> None:
        from factory.core.isolation.session_egress import EgressWireError

        for value in ({"ruleset": ""}, {"ruleset": 42}, "not-an-object", None):
            with self.subTest(value=value):
                with self.assertRaises(EgressWireError):
                    SessionEgress.from_wire(value)

    def test_plan_reports_the_local_inference_target_only_when_enabled(self) -> None:
        local = build_session_egress(
            "cc-local",
            base_url=LOCAL_BASE_URL,
            env=fake_env(),
            resolver=fixture_resolver,
        )
        self.assertEqual("127.0.0.1:8001", local.local_inference_target)
        disabled = build_session_egress(
            "cc-local",
            base_url=LOCAL_BASE_URL,
            env={**fake_env(), "IGOREK_ISOLATION_LOCAL_INFERENCE": "off"},
            resolver=fixture_resolver,
        )
        self.assertEqual("", disabled.local_inference_target)
        self.assertNotIn("tcp dport { 8001 } accept", disabled.ruleset)


class ImageRecipeEgressTests(unittest.TestCase):
    """The base image recipe must install and enable the in-guest boot step."""

    @classmethod
    def setUpClass(cls) -> None:
        cls.script = SCRIPT.read_text(encoding="utf-8")

    def test_recipe_installs_nftables_and_the_egress_guard_unit(self) -> None:
        self.assertIn("nftables", self.script)
        self.assertIn(EGRESS_UNIT_PATH, self.script)
        self.assertIn(EGRESS_BOOT_BIN, self.script)

    def test_recipe_boot_step_runs_the_rendered_baseline(self) -> None:
        self.assertIn("factory.core.isolation.egress_enforce", self.script)
        self.assertIn("--boot-baseline", self.script)
        body = _guest_setup_body(self.script)
        self.assertIn(EGRESS_BOOT_BIN, body)
        self.assertIn("--boot-baseline", body)

    def test_recipe_enables_the_unit_for_boot(self) -> None:
        body = _guest_setup_body(self.script)
        self.assertIn(
            f"multi-user.target.wants/{Path(EGRESS_UNIT_PATH).name}", body
        )
        self.assertIn("ExecStart=", body)
        self.assertIn("Before=network.target", body)

    def test_recipe_lets_the_agent_read_the_deny_log(self) -> None:
        """The blocked-host diagnostics need the guest kernel log readable."""
        body = _guest_setup_body(self.script)
        self.assertIn(GUEST_DMESG_SYSCTL, body)
        self.assertIn("sysctl -p", body)

    def test_recipe_grants_nft_to_a_helper_not_the_runner(self) -> None:
        """The runner must stay capability-free so bwrap can run inside the guest."""
        body = _guest_setup_body(self.script)
        self.assertIn("setcap cap_net_admin+ep /usr/local/bin/igorek-nft", body)
        self.assertIn(
            "Environment=IGOREK_ISOLATION_NFT_BIN=/usr/local/bin/igorek-nft", body
        )
        self.assertNotIn("AmbientCapabilities=CAP_NET_ADMIN", body)
        self.assertNotIn("CapabilityBoundingSet=CAP_NET_ADMIN", body)


def _guest_setup_body(script: str) -> str:
    """Payload of the ``<<'GUEST_SETUP'`` heredoc applied to the guest image."""
    start_marker = "<<'GUEST_SETUP'"
    start = script.index(start_marker) + len(start_marker)
    end = script.index("\nGUEST_SETUP\n", start)
    return script[start:end]


class BlockedHostWatcherTest(unittest.TestCase):
    """A blocked probe must become an isolation.blocked_host record."""

    def test_poll_turns_a_denial_line_into_a_trace_record(self) -> None:
        with tempfile.TemporaryDirectory(prefix="igorek-egress-") as tmp:
            watcher = BlockedHostWatcher(
                tmp,
                lambda: (KERNEL_DENIAL_LINE, "unrelated kernel chatter"),
                pinned_hosts={HOST_LOOPBACK_ALIAS: "egress-auth-proxy"},
            )

            events = watcher.poll()

            self.assertEqual(1, len(events))
            self.assertEqual("10.0.2.2", events[0].destination)
            self.assertEqual(8001, events[0].port)
            self.assertEqual("egress-auth-proxy", events[0].pinned_host)
            records = [
                json.loads(line)
                for line in (Path(tmp) / TRACE_FILENAME)
                .read_text(encoding="utf-8")
                .splitlines()
            ]
            self.assertEqual(["isolation.blocked_host"], [r["kind"] for r in records])
            self.assertEqual("egress_denied", records[0]["reason"])

    def test_poll_is_quiet_when_the_source_has_nothing(self) -> None:
        with tempfile.TemporaryDirectory(prefix="igorek-egress-") as tmp:
            watcher = BlockedHostWatcher(tmp, lambda: ())
            self.assertEqual((), watcher.poll())
            self.assertFalse((Path(tmp) / TRACE_FILENAME).exists())

    def test_poll_never_raises_when_the_source_fails(self) -> None:
        def broken() -> tuple[str, ...]:
            raise OSError("dmesg is gone")

        with tempfile.TemporaryDirectory(prefix="igorek-egress-") as tmp:
            watcher = BlockedHostWatcher(tmp, broken)
            self.assertEqual((), watcher.poll())


if __name__ == "__main__":
    unittest.main()
