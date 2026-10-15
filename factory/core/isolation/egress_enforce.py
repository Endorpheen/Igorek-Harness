"""Runtime enforcement of the rendered per-session egress ruleset (plan todo 40).

The todo-16/17 generators decided the policy; nothing applied it. This module is
the enforcement half: it takes the ruleset
:class:`~factory.core.isolation.session_egress.SessionEgress` carries (rendered
by :meth:`NetworkPolicy.ruleset`), hands it to ``nft`` inside the guest and
proves the kernel actually took it.

Three callers, one contract:

* the **boot step** (``python3 -m factory.core.isolation.egress_enforce
  --boot-baseline``, installed and enabled by ``build_isolation_image.sh``)
  applies the strictest baseline at VM boot - default deny plus the pinned DNS
  accepts, no allow entry, because no session exists yet;
* the **guest runner** applies the per-session ruleset (the session's allowlist
  plus the one local-inference route when enabled) that arrives with the task,
  before any agent code runs;
* the **operator CLI** (``--render``/``--apply``) renders or applies a ruleset by
  hand for manual QA.

Fail-closed rules, none of which has an unrestricted fallback:

* only a default-deny ``igorek_egress`` output chain carrying the blocked-host
  log prefix may pass through :func:`apply_egress_ruleset`; anything else is
  refused as :data:`REASON_INVALID_RULESET`;
* a non-zero ``nft`` exit is :data:`REASON_APPLY_FAILED`;
* a zero exit whose table cannot be read back, or whose listing does not carry
  the rendered addresses, the deny rules and the same rule count, is
  :data:`REASON_NOT_VERIFIED` - an apply is never reported as done on trust;
* ``destroy table`` precedes the rendered table in the same ``nft`` batch, so
  re-applying is idempotent and the kernel commits the replacement atomically.

The ruleset text is the only thing that crosses to the guest; the allowlist
decision itself (DNS pinning, the local-inference gate, the operator extras)
stays host-side in :mod:`factory.core.isolation.network`, and the value the
decision travels in is :mod:`factory.core.isolation.session_egress`.
"""

from __future__ import annotations

import argparse
import hashlib
import ipaddress
import os
import subprocess
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Protocol

from .errors import IsolationError
from .network import build_network_policy
from .network_blocked import BLOCKED_HOST_LOG_PREFIX

__all__ = [
    "BOOT_BASELINE_PROFILE",
    "EGRESS_BOOT_BIN",
    "EGRESS_TABLE",
    "EGRESS_TABLE_REF",
    "EGRESS_UNIT_NAME",
    "EGRESS_UNIT_PATH",
    "GUEST_DMESG_SYSCTL",
    "NFT_BIN",
    "NFT_BIN_ENV",
    "REASON_APPLY_FAILED",
    "REASON_INVALID_RULESET",
    "REASON_NOT_VERIFIED",
    "ApplyResult",
    "NftResult",
    "NftRunner",
    "SubprocessNftRunner",
    "apply_egress_ruleset",
    "boot_baseline_ruleset",
    "main",
    "resolve_nft_bin",
]

NFT_BIN: Final[str] = "nft"
#: Override for the ``nft`` executable. The guest runner runs as the
#: unprivileged agent user, which has no CAP_NET_ADMIN; the base image installs a
#: root-owned copy of ``nft`` with the file capability ``cap_net_admin+ep`` and
#: points this variable at it. The runner process itself must stay
#: capability-free: ``bwrap`` refuses to run ("Unexpected capabilities but not
#: setuid") when the process holds any capability, so the capability lives only
#: in the helper the runner execs, never in the runner.
NFT_BIN_ENV: Final[str] = "IGOREK_ISOLATION_NFT_BIN"
EGRESS_TABLE: Final[str] = "igorek_egress"
EGRESS_TABLE_REF: Final[str] = f"inet {EGRESS_TABLE}"
#: The boot baseline belongs to no profile: no session allowlist exists yet.
BOOT_BASELINE_PROFILE: Final[str] = "<boot-baseline>"

REASON_APPLY_FAILED: Final[str] = "egress_ruleset_apply_failed"
REASON_NOT_VERIFIED: Final[str] = "egress_ruleset_not_verified"
REASON_INVALID_RULESET: Final[str] = "egress_ruleset_invalid"

_DESTROY_TABLE_LINE: Final[str] = f"destroy table {EGRESS_TABLE_REF}"
_POLICY_DROP: Final[str] = "policy drop"
_HOOK_OUTPUT: Final[str] = "hook output"

#: Base-image recipe contract (pinned by tests to the shell recipe).
EGRESS_UNIT_NAME: Final[str] = "igorek-egress-guard.service"
EGRESS_UNIT_PATH: Final[str] = f"/etc/systemd/system/{EGRESS_UNIT_NAME}"
EGRESS_BOOT_BIN: Final[str] = "/usr/local/bin/igorek-egress-boot"
GUEST_DMESG_SYSCTL: Final[str] = "kernel.dmesg_restrict = 0"


@dataclass(frozen=True, slots=True)
class NftResult:
    """Outcome of one ``nft`` invocation."""

    returncode: int
    stdout: str = ""
    stderr: str = ""


class NftRunner(Protocol):
    """Seam for the two ``nft`` calls (a fake in tests)."""

    def __call__(self, argv: Sequence[str], *, stdin: str = "") -> NftResult: ...


class SubprocessNftRunner:
    """Default runner: argv only, the ruleset on stdin, no shell."""

    def __call__(self, argv: Sequence[str], *, stdin: str = "") -> NftResult:
        try:
            completed = subprocess.run(
                list(argv),
                input=stdin,
                capture_output=True,
                text=True,
                errors="replace",
                check=False,
            )
        except OSError as exc:
            return NftResult(127, "", f"{argv[0]}: {exc}")
        return NftResult(completed.returncode, completed.stdout, completed.stderr)


@dataclass(frozen=True, slots=True)
class ApplyResult:
    """What one verified apply observed (``verified`` is never assumed)."""

    applied: bool
    verified: bool
    table: str
    ruleset_sha256: str
    rule_count: int
    listed_rules: int


def boot_baseline_ruleset() -> str:
    """The strictest ruleset: default deny + the pinned DNS accepts, no allows.

    No session exists at boot, so no allow entry is rendered: a VM can never
    start on the QEMU user-net defaults. The per-session allowlist replaces
    this table when the task arrives.
    """
    return build_network_policy(
        BOOT_BASELINE_PROFILE, base_url="", env={}, resolver=_no_addresses
    ).ruleset()


def resolve_nft_bin(env: Mapping[str, str] | None = None) -> str:
    """The ``nft`` executable to invoke (``IGOREK_ISOLATION_NFT_BIN`` or ``nft``)."""
    source = os.environ if env is None else env
    return str(source.get(NFT_BIN_ENV) or "").strip() or NFT_BIN


def apply_egress_ruleset(
    ruleset: str, *, runner: NftRunner | None = None
) -> ApplyResult:
    """Apply the rendered ruleset and verify it; never reports an unverified apply."""
    text = str(ruleset or "")
    _refuse_unenforceable(text)
    run = runner if runner is not None else SubprocessNftRunner()
    nft = resolve_nft_bin()
    batch = f"{_DESTROY_TABLE_LINE}\n{text}"
    applied = run([nft, "-f", "-"], stdin=batch)
    if applied.returncode != 0:
        raise IsolationError(REASON_APPLY_FAILED, _detail(applied))
    listed = run([nft, "list", "table", "inet", EGRESS_TABLE])
    if listed.returncode != 0:
        raise IsolationError(REASON_NOT_VERIFIED, _detail(listed))
    listed_rules = _rule_count(listed.stdout)
    if not _listing_carries(text, listed.stdout, listed_rules):
        raise IsolationError(
            REASON_NOT_VERIFIED,
            f"table {EGRESS_TABLE_REF} does not carry the rendered ruleset",
        )
    return ApplyResult(
        applied=True,
        verified=True,
        table=EGRESS_TABLE_REF,
        ruleset_sha256=hashlib.sha256(text.encode("utf-8")).hexdigest(),
        rule_count=_rule_count(text),
        listed_rules=listed_rules,
    )


def main(argv: Sequence[str] | None = None, *, runner: NftRunner | None = None) -> int:
    """CLI: apply the boot baseline (default), a ruleset file, or render only."""
    parser = argparse.ArgumentParser(
        prog="igorek-egress-guard",
        description="Apply the rendered default-deny guest egress ruleset.",
    )
    parser.add_argument(
        "--boot-baseline",
        action="store_true",
        help="apply the strictest ruleset (default deny + pinned DNS)",
    )
    parser.add_argument(
        "--apply",
        default="",
        metavar="PATH",
        help="apply the ruleset rendered into PATH instead of the baseline",
    )
    parser.add_argument(
        "--render", action="store_true", help="print the ruleset; apply nothing"
    )
    args = parser.parse_args(argv)
    if args.apply:
        try:
            ruleset = Path(args.apply).read_text(encoding="utf-8")
        except OSError as exc:
            print(f"FAIL: egress_ruleset_unreadable: {exc}", file=sys.stderr)
            return 1
    else:
        ruleset = boot_baseline_ruleset()
    if args.render:
        sys.stdout.write(ruleset)
        return 0
    try:
        result = apply_egress_ruleset(ruleset, runner=runner)
    except IsolationError as exc:
        print(f"FAIL: {exc.code}: {exc.detail}", file=sys.stderr)
        return 1
    print(
        f"OK: enforced {result.table} rules={result.rule_count} "
        f"verified={result.verified} sha256={result.ruleset_sha256[:12]}"
    )
    return 0


def _no_addresses(host: str) -> tuple[str, ...]:
    """Boot-baseline resolver: no session allowlist, so nothing is pinned."""
    return ()


def _refuse_unenforceable(text: str) -> None:
    """Only the rendered default-deny egress table may reach the kernel."""
    if not text.strip():
        raise IsolationError(REASON_INVALID_RULESET, "empty ruleset")
    required = (
        f"table {EGRESS_TABLE_REF}",
        _HOOK_OUTPUT,
        _POLICY_DROP,
        BLOCKED_HOST_LOG_PREFIX,
    )
    missing = [marker for marker in required if marker not in text]
    if missing:
        raise IsolationError(REASON_INVALID_RULESET, "missing " + ", ".join(missing))


def _listing_carries(rendered: str, listing: str, listed_rules: int) -> bool:
    """True only when the live table carries the rendered policy, structurally."""
    if _POLICY_DROP not in listing or BLOCKED_HOST_LOG_PREFIX not in listing:
        return False
    if listed_rules != _rule_count(rendered):
        return False
    return _addresses_of(listing) >= _addresses_of(rendered)


def _addresses_of(ruleset: str) -> set[str]:
    """Canonical networks of every ``daddr`` target in a ruleset or listing."""
    found: set[str] = set()
    for raw in ruleset.splitlines():
        tokens = raw.split("#", 1)[0].split()
        if "daddr" not in tokens:
            continue
        index = tokens.index("daddr") + 1
        if index >= len(tokens):
            continue
        try:
            found.add(str(ipaddress.ip_network(tokens[index], strict=False)))
        except ValueError:
            continue
    return found


def _rule_count(ruleset: str) -> int:
    """Rules whose verdict is an accept or a drop (comments stripped)."""
    count = 0
    for raw in ruleset.splitlines():
        tokens = raw.split("#", 1)[0].split()
        if tokens and tokens[-1] in {"accept", "drop"}:
            count += 1
    return count


def _detail(result: NftResult) -> str:
    return (
        result.stderr or result.stdout or f"{NFT_BIN} exited {result.returncode}"
    ).strip()


if __name__ == "__main__":
    raise SystemExit(main())
