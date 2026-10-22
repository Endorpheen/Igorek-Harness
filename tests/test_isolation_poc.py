#!/usr/bin/env python3
"""End-to-end PoC for the per-session KVM boundary.

Use one existing profile and synthetic project workspace to check:
1. `profile_and_workspace_ready`: resolve a `cc-backend` profile and validate
   the requested workspace through the single-export policy.
2. `session_vm_started`: boot the real libvirt domain, qcow2 overlay, virtiofs
   export and vsock channel. The guest `igorek-factory guest-runner` connects;
   its `ExecStartPre` validates hardened `--unshare-all` bwrap arguments.
3. `approval_requested`: a blocked `Write` reaches the host, parks with a
   canonicalized payload and waits for the host's decision. The guest never
   approves itself; the manager owns the VM's pause/resume lifecycle.
4. `file_written_in_host_workspace`: `allow_once` resumes the pinned call via
   `forced_tool_call` and `approved_tool_calls`; the exported host file changes.
5. `host_home_not_reachable`: host `$HOME`/`~/.ssh` canaries are inaccessible at
   the same absolute guest paths, while a shared workspace control is readable.
   Use harness-pinned `Read` calls, independent of model interpretation.
6. `host_dbus_systemd_not_reachable`: a guest `systemd-run --user` unit does not
   appear on the host bus, the host marker is invisible to the guest and their
   bus sockets differ.
7. `host_processes_invisible`: a uniquely marked host process is invisible in
   the guest; an in-guest process supplies the positive control.
8. `stop_tears_the_vm_down`: exercise Stop and its cancellation/cleanup contract.
9. `vm_and_overlay_gone` / `no_leftovers`: assert domain, overlay and endpoint
   cleanup through the `CLEANUP:` receipt; any leftover fails the run.

Reporting rules:
* Require `KVM_AVAILABLE` and actual KVM/libvirt/virtiofsd/`qemu-img`/`AF_VSOCK`
  capability. Otherwise print `SKIP: no KVM (<reason>)` and return the skip code.
* Print individual `PASS`/`FAIL`/`SKIP` verdicts. A failed step can skip only
  dependent steps; any failure or unexpected skip fails the run.
* Assert the cleanup receipt rather than assuming cleanup succeeded.

Use an image from `factory/scripts/build_isolation_image.sh`. A frozen image
may already contain bootstrap. Older images need a copy-on-write derivative
prepared through `virt-customize`, recorded as
`builder=virt-customize-poc-bootstrap`.

The channel forbids shell-command keys in `task` frames, so pin `Write`/`Read`
through the closed guest loopback provider instead of placing a `Bash` command
on the wire. The D-Bus/process probes use the profile's real model endpoint,
which can issue guest `Bash`. These live probes deliberately disable inner
scope/sandbox layers (`guard_mode=off`, `filesystem_mode=full-dangerous` for reads,
`IGOREK_BASH_SANDBOX=off` in the guest unit) to measure the VM boundary itself.
These flags belong only to the explicit negative-test guest, not production defaults.

Run `factory/scripts/isolation_poc.sh --profile <p> --workspace <abs dir>` or
`python tests/test_isolation_poc.py --real --profile <p> --workspace <dir>`.

# allow: SIZE_OK - the pure harness, verdicts and guarded real backend implement
# one checklist; keep assertions beside the behavior they verify.
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Final, Protocol

REPO_ROOT = Path(
    os.environ.get("IGOREK_FACTORY_REPO", Path(__file__).resolve().parents[1])
)
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from factory.core.cc_backend_trace import TRACE_FILENAME  # noqa: E402
from factory.core.isolation.base_image import (  # noqa: E402
    ImageBuildInfo,
    build_manifest,
    default_image_path,
    load_manifest,
    manifest_path_for,
    resolve_tool_version,
    write_manifest,
)
from factory.core.isolation.channel import (  # noqa: E402
    JsonValue,
    MessageKind,
    SessionId,
)
from factory.core.isolation.config import (  # noqa: E402
    ENV_BASE_IMAGE,
    ENV_MODE,
    ENV_OVERLAY_DIR,
    ENV_VSOCK_PORT,
    MODE_KVM,
    IsolationConfig,
)
from factory.core.isolation.domain import (  # noqa: E402
    DomainSpec,
    domain_name,
    kvm_unavailable_reason,
    open_libvirt_connection,
    overlay_path,
)
from factory.core.isolation.errors import IsolationError  # noqa: E402
from factory.core.isolation.guest_bwrap import (  # noqa: E402
    APPARMOR_PROFILE_PATH,
    BWRAP_APPARMOR_PROFILE,
    GUEST_PROBE_BIN,
    USERNS_SYSCTL_FILE,
    PROBE_OK_TEXT,
    SANDBOX_UNAVAILABLE_PREFIX,
    USERNS_SYSCTL_ENTRIES,
    probe_bwrap_argv,
)
from factory.core.isolation.egress_auth import ProviderAuthInjector  # noqa: E402
from factory.core.isolation.manager import (  # noqa: E402
    IsolationManager,
    ManagerSeams,
    SessionSpec,
    SessionVm,
)
from factory.core.isolation.probe_verification import ProbeLedger  # noqa: E402
from factory.core.isolation.secret_policy import ENV_EGRESS_AUTH  # noqa: E402
from factory.core.isolation.workspace import (  # noqa: E402
    GUEST_WORKSPACE_PATH,
    WorkspacePolicy,
    resolve_workspace_export,
)
from factory.core.runtime import get_data_root, get_profile_config  # noqa: E402
from factory.core.trace_taxonomy import CcTraceEvent  # noqa: E402

# --------------------------------------------------------------------------- #
# Vocabulary
# --------------------------------------------------------------------------- #

EXIT_OK: Final = 0
EXIT_FAIL: Final = 1
EXIT_SKIP: Final = 2

KVM_AVAILABLE_ENV: Final = "KVM_AVAILABLE"
KVM_AVAILABLE_TRUE: Final = frozenset({"1", "true", "yes", "on", "kvm"})

DEFAULT_POC_VSOCK_PORT: Final = 43011
DEFAULT_BOOT_TIMEOUT_S: Final = 240.0
DEFAULT_WAIT_TIMEOUT_S: Final = 120.0
DEFAULT_RUN_TIMEOUT_S: Final = 1200.0
DEFAULT_PROFILE: Final = "cc-wally"

POC_DIR_NAME: Final = ".igorek-isolation-poc"
GUEST_UNIT_NAME: Final = "igorek-guest-runner.service"
GUEST_RUNNER_BIN: Final = "/home/agent/.local/bin/igorek-factory"
GUEST_BWRAP_PROBE_FILE: Final = "bwrap_probe.txt"
FORCED_WRITE_ID: Final = "forced_poc_write"
CLOSED_PROVIDER_URL: Final = "http://127.0.0.1:9/v1"

ASSERT_PREPARE: Final = "profile_and_workspace_ready"
ASSERT_SESSION: Final = "session_vm_started"
ASSERT_APPROVAL: Final = "approval_requested"
ASSERT_WRITE: Final = "file_written_in_host_workspace"
ASSERT_HOME: Final = "host_home_not_reachable"
ASSERT_DBUS: Final = "host_dbus_systemd_not_reachable"
ASSERT_PROCS: Final = "host_processes_invisible"
ASSERT_STOP: Final = "stop_tears_the_vm_down"
ASSERT_TEARDOWN: Final = "vm_and_overlay_gone"
ASSERT_CLEAN: Final = "no_leftovers"


class PocBackendError(Exception):
    """A backend step cannot produce its evidence; ``reason`` is machine-stable."""

    def __init__(self, reason: str, detail: str = "") -> None:
        super().__init__(reason)
        self.reason = str(reason)
        self.detail = str(detail)

    def __str__(self) -> str:
        return f"{self.reason}: {self.detail}" if self.detail else self.reason


# --------------------------------------------------------------------------- #
# Availability probe (the honesty gate)
# --------------------------------------------------------------------------- #


def kvm_available_requested(env: Mapping[str, str]) -> bool:
    """True only for an explicit opt-in `KVM_AVAILABLE=1`-style value."""
    return str(env.get(KVM_AVAILABLE_ENV, "")).strip().lower() in KVM_AVAILABLE_TRUE


def vsock_unavailable_reason() -> str | None:
    """`AF_VSOCK` must exist on the host or no guest can ever connect."""
    family = getattr(socket, "AF_VSOCK", None)
    if not isinstance(family, int):
        return "AF_VSOCK is not available on this host"
    return None


def structural_probe() -> str | None:
    """Real-host readiness; ``None`` means a run is possible."""
    try:
        reason = kvm_unavailable_reason()
    except Exception as exc:  # probe boundary: an unusable probe is "not ready"
        return f"capability probe failed: {exc}"
    if reason is not None:
        return reason
    return vsock_unavailable_reason()


def probe_availability(
    env: Mapping[str, str],
    *,
    structure: Callable[[], str | None] = structural_probe,
) -> str | None:
    """``None`` when the PoC may run; otherwise the skip reason (never a pass)."""
    if not kvm_available_requested(env):
        return f"{KVM_AVAILABLE_ENV} unset (set it to enable the real VM run)"
    return structure()


# --------------------------------------------------------------------------- #
# Reporting: one line per assertion, cleanup receipt
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class Assertion:
    name: str
    status: str  # PASS | FAIL | SKIP
    detail: str = ""


@dataclass
class AssertionReport:
    """Ordered per-assertion verdicts; a FAIL or an unexpected SKIP fails the run."""

    emit: Callable[[str], None] = print
    results: list[Assertion] = field(default_factory=list)

    def record(self, name: str, ok: bool | None, detail: str = "") -> None:
        status = "PASS" if ok is True else "FAIL" if ok is False else "SKIP"
        self.results.append(Assertion(name, status, detail))
        suffix = f" - {detail}" if detail else ""
        self.emit(f"{status}: {name}{suffix}")

    @property
    def failed(self) -> list[Assertion]:
        return [item for item in self.results if item.status == "FAIL"]

    @property
    def skipped(self) -> list[Assertion]:
        return [item for item in self.results if item.status == "SKIP"]

    @property
    def passed(self) -> list[Assertion]:
        return [item for item in self.results if item.status == "PASS"]

    @property
    def ok(self) -> bool:
        return not self.failed and not self.skipped and bool(self.passed)


@dataclass
class CleanupReceipt:
    """What cleanup removed; anything still present is a leftover that fails the run."""

    removed: list[str] = field(default_factory=list)
    leftovers: list[str] = field(default_factory=list)

    def add(self, name: str, removed: bool, detail: str = "") -> None:
        line = f"{name} ({detail})" if detail else name
        if removed:
            self.removed.append(line)
        else:
            self.leftovers.append(line)

    @property
    def clean(self) -> bool:
        return not self.leftovers

    def summary(self) -> str:
        if self.clean:
            return f"removed {len(self.removed)} item(s), no leftovers"
        return f"LEFTOVERS: {', '.join(self.leftovers)}"

    def render(self) -> list[str]:
        lines = [f"CLEANUP: removed {item}" for item in self.removed]
        lines.extend(f"CLEANUP: LEFTOVER {item}" for item in self.leftovers)
        return lines


def wait_until(
    predicate: Callable[[], bool],
    timeout_s: float,
    *,
    interval_s: float = 0.5,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
    what: str = "condition",
) -> None:
    """Bounded wait; raises :class:`TimeoutError` instead of hanging forever."""
    deadline = clock() + max(0.0, float(timeout_s))
    while True:
        if predicate():
            return
        if clock() >= deadline:
            raise TimeoutError(f"timed out after {timeout_s}s waiting for {what}")
        sleep(interval_s)


def _error_detail(exc: BaseException) -> str:
    if isinstance(exc, PocBackendError):
        return str(exc)
    reason = getattr(exc, "reason", "")
    detail = getattr(exc, "detail", "")
    if reason:
        return f"{reason}: {detail}" if detail else str(reason)
    return f"{type(exc).__name__}: {exc}"


# --------------------------------------------------------------------------- #
# Evidence (what each backend step observed) and its verdicts
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class PrepareEvidence:
    profile: str
    workspace_host: str
    base_image: str
    bootstrap: str  # "base" | "derived"
    guest_bootstrap_tool: str


@dataclass(frozen=True, slots=True)
class SessionEvidence:
    domain: str
    overlay: str
    host_workspace: str
    export_guest: str
    peer_cid: int
    connected: bool
    boot_seconds: float
    bwrap_probe: str = ""


@dataclass(frozen=True, slots=True)
class ApprovalEvidence:
    status: str
    blocked_tools: tuple[Mapping[str, JsonValue], ...]
    host_workspace: str
    vm_stopped: bool


@dataclass(frozen=True, slots=True)
class WriteEvidence:
    host_file: str
    expected_content: str
    actual_content: str
    forced_tool_ok: bool


@dataclass(frozen=True, slots=True)
class HomeEvidence:
    canary_content: str
    host_tmp_canary_path: str
    control_path: str
    tmp_canary_result: str
    control_result: str
    report: str
    canary_name: str
    ssh_canary_name: str


@dataclass(frozen=True, slots=True)
class DbusEvidence:
    report: str
    host_marker_unit: str
    host_bus_inode: int | None
    guest_probe_unit: str


@dataclass(frozen=True, slots=True)
class ProcessEvidence:
    report: str
    host_proc_marker: str
    guest_proc_marker: str


@dataclass(frozen=True, slots=True)
class StopEvidence:
    result_status: str
    failure_reason: str
    call_started: bool
    domain_gone: bool
    overlay_gone: bool


@dataclass(frozen=True, slots=True)
class TeardownEvidence:
    domain_gone: bool
    overlay_gone: bool
    overlay_dir_empty: bool


def _marker(report: str, name: str) -> str | None:
    """Value of one `NAME=value` marker line of a guest probe report."""
    prefix = f"{name}="
    for line in report.splitlines():
        if line.startswith(prefix):
            return line[len(prefix) :].strip()
    return None


def verdict_prepare(evidence: PrepareEvidence) -> tuple[bool, str]:
    if not evidence.profile or not evidence.workspace_host:
        return False, "profile or workspace unresolved"
    detail = (
        f"profile={evidence.profile} workspace={evidence.workspace_host} "
        f"bootstrap={evidence.bootstrap}"
    )
    if evidence.bootstrap == "derived":
        detail += f" (virt-customize via {evidence.guest_bootstrap_tool})"
    return True, detail


def verdict_session(evidence: SessionEvidence) -> tuple[bool, str]:
    if not evidence.connected:
        return False, f"guest runner never connected (domain={evidence.domain})"
    if not evidence.domain or not evidence.overlay:
        return False, "domain or overlay was not created"
    if PROBE_OK_TEXT not in evidence.bwrap_probe:
        return False, (
            "in-guest bwrap probe did not report "
            f"{PROBE_OK_TEXT!r} (got {evidence.bwrap_probe.strip()!r})"
        )
    return True, (
        f"domain={evidence.domain} cid={evidence.peer_cid} "
        f"boot={evidence.boot_seconds:.1f}s "
        f"export={evidence.host_workspace}->{evidence.export_guest} "
        f"bwrap={evidence.bwrap_probe.strip()}"
    )


def verdict_approval(evidence: ApprovalEvidence) -> tuple[bool, str]:
    if evidence.status != "waiting_approval":
        return False, (
            f"task status is {evidence.status!r}, expected 'waiting_approval' "
            f"for source_platform=android (blocked={len(evidence.blocked_tools)})"
        )
    if not evidence.blocked_tools:
        return False, "no approval_blocked_tools crossed the channel"
    blocked = evidence.blocked_tools[0]
    missing = [key for key in ("tool", "target", "signature", "tool_use_id") if not blocked.get(key)]
    if missing:
        return False, f"blocked payload is missing {missing}"
    target = str(blocked.get("target") or "")
    if not target or not target.startswith(evidence.host_workspace.rstrip("/")):
        return False, f"blocked target was not canonicalized to the host workspace: {target!r}"
    if not evidence.vm_stopped:
        return False, "the parked session VM was left running"
    return True, (
        f"tool={blocked.get('tool')} tool_use_id={blocked.get('tool_use_id')} "
        f"target={target} parked_vm=torn_down"
    )


def verdict_write(evidence: WriteEvidence) -> tuple[bool, str]:
    if not evidence.forced_tool_ok:
        return False, "the guest trace carries no forced Write tool result"
    if evidence.actual_content != evidence.expected_content:
        return False, (
            f"host workspace file content mismatch at {evidence.host_file}: "
            f"{evidence.actual_content!r}"
        )
    return True, f"host file {evidence.host_file} matches the guest Write through the export"


def verdict_home(evidence: HomeEvidence) -> tuple[bool, str]:
    if evidence.canary_content not in evidence.control_result:
        return False, (
            "positive control failed: the shared control file "
            f"{evidence.control_path} was not readable inside the guest "
            f"(result={evidence.control_result[:120]!r})"
        )
    if evidence.canary_content in evidence.tmp_canary_result:
        return False, f"host /tmp canary content is readable inside the guest at {evidence.host_tmp_canary_path}"
    if "File not found" not in evidence.tmp_canary_result:
        return False, (
            f"the guest's /tmp was not proven distinct from the host's: "
            f"{evidence.host_tmp_canary_path} -> {evidence.tmp_canary_result[:120]!r}"
        )
    if _marker(evidence.report, "POC_CONTROL_READ") != "ok":
        return False, "the live probe could not read the shared control file either"
    for label, marker in (
        ("$HOME canary", "POC_HOME_CANARY"),
        ("~/.ssh canary", "POC_SSH_CANARY"),
    ):
        if _marker(evidence.report, marker) != "absent":
            return False, f"host {label} is visible inside the guest"
        if _marker(evidence.report, f"{marker}_READABLE") != "no":
            return False, f"host {label} content was readable inside the guest"
    return True, (
        f"host /tmp canary {evidence.host_tmp_canary_path} reads 'File not found' "
        f"inside the guest (the guest rootfs is not the host filesystem), host "
        f"$HOME/{evidence.canary_name} and ~/.ssh/{evidence.ssh_canary_name} are "
        "absent and unreadable, and the shared control file is readable"
    )


def verdict_dbus(evidence: DbusEvidence) -> tuple[bool, str]:
    if _marker(evidence.report, "POC_PROBE") != "dbus":
        return False, "guest dbus probe produced no report"
    if evidence.host_marker_unit:
        in_units = _marker(evidence.report, "POC_HOST_MARKER_IN_UNITS")
        in_cat = _marker(evidence.report, "POC_HOST_MARKER_IN_CAT")
        in_bus = _marker(evidence.report, "POC_HOST_MARKER_IN_BUS")
        if in_units != "0" or in_cat == "0" or in_bus != "0":
            return False, (
                f"host user unit {evidence.host_marker_unit} is visible inside the guest "
                f"(units={in_units} cat_rc={in_cat} bus={in_bus})"
            )
    guest_bus = _marker(evidence.report, "POC_GUEST_BUS_INODE")
    if evidence.host_bus_inode is None:
        reach = "host user bus absent -> structurally unreachable"
    else:
        if guest_bus not in (None, "", "none") and int(guest_bus) == evidence.host_bus_inode:
            return False, "the guest reports the host bus socket inode: /run is shared"
        reach = f"guest bus inode={guest_bus or 'none'} != host inode={evidence.host_bus_inode}"
    systemctl_rc = _marker(evidence.report, "POC_SYSTEMCTL_RC")
    busctl_rc = _marker(evidence.report, "POC_BUSCTL_RC")
    if systemctl_rc not in (None, "", "0") and busctl_rc not in (None, "", "0"):
        where = "systemctl/busctl fail in the guest"
    else:
        where = (
            "guest has its own user manager; the host bus stays unreachable "
            f"(systemctl_rc={systemctl_rc} busctl_rc={busctl_rc})"
        )
    return True, f"{where}; {reach}; systemd-run escape closed"


def verdict_processes(evidence: ProcessEvidence) -> tuple[bool, str]:
    if _marker(evidence.report, "POC_PROBE") != "procs":
        return False, "guest process probe produced no report"
    if _marker(evidence.report, "POC_MARKER_MISSING") is not None:
        return False, "the probe marker files were missing; the scan would match everything"
    control = _marker(evidence.report, "POC_GUEST_PROC_MATCHES")
    if control != "1":
        return False, (
            f"positive control failed: the in-guest marker process count is "
            f"{control!r}, expected '1'"
        )
    host_matches = _marker(evidence.report, "POC_HOST_PROC_MATCHES")
    if host_matches != "0":
        return False, (
            f"host process marker {evidence.host_proc_marker} is visible inside "
            f"the guest (matches={host_matches})"
        )
    return True, (
        f"host marker process invisible; the in-guest control process was seen "
        f"(matches=1) out of the guest's own /proc"
    )


def verdict_stop(evidence: StopEvidence) -> tuple[bool, str]:
    if not evidence.call_started:
        return False, "the probe call never started; the stop was not measured mid-dispatch"
    if evidence.failure_reason not in ("canceled", "cancel_timeout"):
        return False, (
            f"unexpected stop result: status={evidence.result_status!r} "
            f"reason={evidence.failure_reason!r}"
        )
    if not evidence.domain_gone or not evidence.overlay_gone:
        return False, (
            f"stop left state behind: domain_gone={evidence.domain_gone} "
            f"overlay_gone={evidence.overlay_gone}"
        )
    suffix = "" if evidence.failure_reason == "canceled" else " (documented timeout fallback)"
    return True, (
        f"in-flight dispatch canceled mid-call ({evidence.failure_reason}{suffix}); "
        "domain and overlay gone"
    )


def verdict_teardown(evidence: TeardownEvidence) -> tuple[bool, str]:
    if not (evidence.domain_gone and evidence.overlay_gone and evidence.overlay_dir_empty):
        return False, (
            f"domain_gone={evidence.domain_gone} overlay_gone={evidence.overlay_gone} "
            f"overlay_dir_empty={evidence.overlay_dir_empty}"
        )
    return True, "session domain undefined, overlay removed, overlay dir empty"


def verdict_clean(receipt: CleanupReceipt) -> tuple[bool, str]:
    return receipt.clean, receipt.summary()


# --------------------------------------------------------------------------- #
# Live probe script (POSIX sh; the model's pinned Bash call runs it in the guest)
# --------------------------------------------------------------------------- #

LIVE_PROBE_SCRIPT_TEMPLATE: Final = """#!/bin/sh
D="{guest_control}"
{{
  echo "PROBE_NONCE={nonce_home}"
  echo "POC_PROBE=home"
  echo "GUEST_HOME=$HOME"
  if grep -qF {canary_content} "$D/host_home_canary.txt" 2>/dev/null; then echo "POC_CONTROL_READ=ok"; else echo "POC_CONTROL_READ=denied"; fi
  if [ -e "$HOME/{canary_name}" ]; then echo "POC_HOME_CANARY=present"; else echo "POC_HOME_CANARY=absent"; fi
  if grep -qF {canary_content} "$HOME/{canary_name}" 2>/dev/null; then echo "POC_HOME_CANARY_READABLE=yes"; else echo "POC_HOME_CANARY_READABLE=no"; fi
  if [ -e "$HOME/.ssh/{ssh_canary_name}" ]; then echo "POC_SSH_CANARY=present"; else echo "POC_SSH_CANARY=absent"; fi
  if grep -qF {canary_content} "$HOME/.ssh/{ssh_canary_name}" 2>/dev/null; then echo "POC_SSH_CANARY_READABLE=yes"; else echo "POC_SSH_CANARY_READABLE=no"; fi
}} > "$D/.probe_home.txt.tmp" 2>&1
mv "$D/.probe_home.txt.tmp" "$D/probe_home.txt"
{{
  echo "PROBE_NONCE={nonce_dbus}"
  echo "POC_PROBE=dbus"
  systemctl --user status >/tmp/poc_sctl 2>&1; echo "POC_SYSTEMCTL_RC=$?"; head -5 /tmp/poc_sctl
  busctl --user list >/tmp/poc_bus 2>&1; echo "POC_BUSCTL_RC=$?"; head -15 /tmp/poc_bus
  systemctl --user list-units --no-legend --no-pager >/tmp/poc_units 2>&1; echo "POC_LIST_UNITS_RC=$?"; head -20 /tmp/poc_units
  echo "POC_HOST_MARKER_IN_UNITS=$(grep -c {host_marker_unit} /tmp/poc_units 2>/dev/null | head -1)"
  echo "POC_HOST_MARKER_IN_BUS=$(grep -c {host_marker_unit} /tmp/poc_bus 2>/dev/null | head -1)"
  systemctl --user cat {host_marker_unit}.service >/dev/null 2>&1; echo "POC_HOST_MARKER_IN_CAT=$?"
  systemd-run --user --wait --pipe --unit={guest_probe_unit} true >/tmp/poc_run 2>&1; echo "POC_SYSTEMD_RUN_RC=$?"; head -3 /tmp/poc_run
  if [ -e /run/user/$(id -u)/bus ]; then
    echo "POC_GUEST_BUS_INODE=$(stat -Lc %i /run/user/$(id -u)/bus 2>/dev/null)"
  else
    echo "POC_GUEST_BUS_INODE=none"
  fi
}} > "$D/.probe_dbus.txt.tmp" 2>&1
mv "$D/.probe_dbus.txt.tmp" "$D/probe_dbus.txt"
{{
  echo "PROBE_NONCE={nonce_procs}"
  echo "POC_PROBE=procs"
  M=$(cat "$D/host_proc_marker.txt" 2>/dev/null)
  G=$(cat "$D/guest_proc_marker.txt" 2>/dev/null)
  if [ -z "$M" ] || [ -z "$G" ]; then
    echo "POC_MARKER_MISSING=1"
    echo "POC_HOST_PROC_MATCHES=unknown"
    echo "POC_GUEST_PROC_MATCHES=unknown"
  else
    cp /bin/sleep "$D/$G" 2>/dev/null || cp /usr/bin/sleep "$D/$G"
    "$D/$G" 25 >/dev/null 2>&1 &
    sleep 0.5
    HM=0; for p in /proc/[0-9]*/cmdline; do if tr "\\0" " " < "$p" 2>/dev/null | grep -qF "$M"; then HM=$((HM+1)); fi; done
    echo "POC_HOST_PROC_MATCHES=$HM"
    GM=0; for p in /proc/[0-9]*/cmdline; do if tr "\\0" " " < "$p" 2>/dev/null | grep -qF "$G"; then GM=$((GM+1)); fi; done
    echo "POC_GUEST_PROC_MATCHES=$GM"
  fi
  echo "POC_PS_BEGIN"; ps -ef 2>&1 | head -30; echo "POC_PS_END"
}} > "$D/.probe_procs.txt.tmp" 2>&1
mv "$D/.probe_procs.txt.tmp" "$D/probe_procs.txt"
sleep 90
"""


def live_probe_script(
    *,
    guest_control: str,
    host_marker_unit: str,
    guest_probe_unit: str,
    canary_name: str,
    ssh_canary_name: str,
    canary_content: str,
    nonces: Mapping[str, str] | None = None,
) -> str:
    """The pinned live-probe script; ``nonces`` are the host-issued challenges."""
    issued = dict(nonces or {})
    return LIVE_PROBE_SCRIPT_TEMPLATE.format(
        guest_control=guest_control,
        host_marker_unit=host_marker_unit,
        guest_probe_unit=guest_probe_unit,
        canary_name=canary_name,
        ssh_canary_name=ssh_canary_name,
        canary_content=canary_content,
        nonce_home=issued.get("home", ""),
        nonce_dbus=issued.get("dbus", ""),
        nonce_procs=issued.get("procs", ""),
    )


def live_probe_user_message(guest_control: str) -> str:
    """The exact instruction sent to the model for the live probes."""
    return (
        "Run exactly this one command with the Bash tool and nothing else, then stop: "
        f"sh {guest_control}/poc_probe.sh"
    )

# --------------------------------------------------------------------------- #
# The runnable harness: ordered steps, per-assertion verdicts, mandatory cleanup
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class PocRequest:
    """Everything one PoC run needs before the environment is touched."""

    profile: str = DEFAULT_PROFILE
    workspace: str = ""
    session_id: str = ""
    vsock_port: int = DEFAULT_POC_VSOCK_PORT
    boot_timeout_s: float = DEFAULT_BOOT_TIMEOUT_S
    wait_timeout_s: float = DEFAULT_WAIT_TIMEOUT_S
    poc_root: str = ""


class PocBackend(Protocol):
    """Host-side environment for the PoC; faked in the unit tests, real on a KVM host."""

    def prepare(self) -> PrepareEvidence: ...
    def start_session(self) -> SessionEvidence: ...
    def request_write(self) -> ApprovalEvidence: ...
    def approve_write(self) -> WriteEvidence: ...
    def probe_home(self) -> HomeEvidence: ...
    def probe_dbus(self) -> DbusEvidence: ...
    def probe_processes(self) -> ProcessEvidence: ...
    def stop_session(self) -> StopEvidence: ...
    def teardown_facts(self) -> TeardownEvidence: ...
    def cleanup(self) -> CleanupReceipt: ...


Step = tuple[str, Callable[[], Any], Callable[[Any], tuple[bool, str]]]


class PocHarness:
    """Runs the checklist against a backend; every step prints its own verdict."""

    def __init__(
        self,
        request: PocRequest,
        backend: PocBackend,
        *,
        emit: Callable[[str], None] = print,
        report: AssertionReport | None = None,
    ) -> None:
        self.request = request
        self.backend = backend
        self._emit = emit
        self.report = report if report is not None else AssertionReport(emit=emit)

    def _steps(self) -> tuple[Step, ...]:
        return (
            (ASSERT_PREPARE, self.backend.prepare, lambda value: verdict_prepare(value)),
            (ASSERT_SESSION, self.backend.start_session, lambda value: verdict_session(value)),
            (ASSERT_APPROVAL, self.backend.request_write, lambda value: verdict_approval(value)),
            (ASSERT_WRITE, self.backend.approve_write, lambda value: verdict_write(value)),
            (ASSERT_HOME, self.backend.probe_home, lambda value: verdict_home(value)),
            (ASSERT_DBUS, self.backend.probe_dbus, lambda value: verdict_dbus(value)),
            (ASSERT_PROCS, self.backend.probe_processes, lambda value: verdict_processes(value)),
            (ASSERT_STOP, self.backend.stop_session, lambda value: verdict_stop(value)),
            (ASSERT_TEARDOWN, self.backend.teardown_facts, lambda value: verdict_teardown(value)),
        )

    def _run_steps(self) -> None:
        failed_at = ""
        for name, step, verdict in self._steps():
            if failed_at:
                self.report.record(name, None, f"depends on failed {failed_at}")
                continue
            try:
                evidence = step()
            except Exception as exc:  # harness boundary: report, never crash
                self.report.record(name, False, _error_detail(exc))
                failed_at = name
                continue
            try:
                ok, detail = verdict(evidence)
            except Exception as exc:  # a bad evidence shape is a FAIL
                self.report.record(name, False, f"evidence unreadable: {_error_detail(exc)}")
                failed_at = name
                continue
            self.report.record(name, ok, detail)
            if not ok:
                failed_at = name

    def _cleanup(self) -> CleanupReceipt:
        try:
            return self.backend.cleanup()
        except Exception as exc:  # cleanup failure is itself a leftover
            receipt = CleanupReceipt()
            receipt.leftovers.append(f"cleanup crashed: {_error_detail(exc)}")
            return receipt

    def run(self) -> bool:
        """Run every step, always clean up, print the receipt and the final RESULT."""
        receipt = CleanupReceipt(leftovers=["cleanup did not run"])
        try:
            self._run_steps()
        finally:
            receipt = self._cleanup()
        for line in receipt.render():
            self._emit(line)
        self.report.record(ASSERT_CLEAN, *verdict_clean(receipt))
        ok = self.report.ok
        self._emit(f"RESULT: {'PASS' if ok else 'FAIL'}")
        return ok


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="isolation_poc",
        description="End-to-end PoC for the per-session KVM isolation boundary.",
    )
    parser.add_argument("--real", action="store_true", help="run the real KVM PoC")
    parser.add_argument(
        "--profile",
        default=os.environ.get("IGOREK_ISOLATION_POC_PROFILE", DEFAULT_PROFILE),
        help=f"existing profile name (default: {DEFAULT_PROFILE})",
    )
    parser.add_argument(
        "--workspace",
        default=os.environ.get("IGOREK_ISOLATION_POC_WORKSPACE", ""),
        help="existing absolute project workspace directory",
    )
    parser.add_argument("--session-id", default="", help="session id override")
    parser.add_argument(
        "--vsock-port",
        type=int,
        default=int(os.environ.get(ENV_VSOCK_PORT) or DEFAULT_POC_VSOCK_PORT),
        help=f"host vsock port (default: {DEFAULT_POC_VSOCK_PORT})",
    )
    parser.add_argument(
        "--boot-timeout-s", type=float, default=DEFAULT_BOOT_TIMEOUT_S, help="guest boot budget"
    )
    parser.add_argument(
        "--wait-timeout-s", type=float, default=DEFAULT_WAIT_TIMEOUT_S, help="per-step budget"
    )
    parser.add_argument("--poc-root", default="", help="scratch dir (created by the caller)")
    parser.add_argument(
        "--probe-only",
        action="store_true",
        help="print the availability verdict and exit without touching anything",
    )
    return parser


def main(
    argv: Sequence[str] | None = None,
    *,
    env: Mapping[str, str] | None = None,
    emit: Callable[[str], None] = print,
    availability: Callable[..., str | None] = probe_availability,
    backend_factory: Callable[..., PocBackend] | None = None,
) -> int:
    """Entry point: probe -> SKIP, or run the harness and return 0/1."""
    args = _build_parser().parse_args(argv)
    source = os.environ if env is None else env

    reason = availability(source)
    if reason is not None:
        emit(f"SKIP: no KVM ({reason})")
        return EXIT_SKIP
    if args.probe_only:
        emit("READY: KVM probe passed")
        return EXIT_OK
    if not args.real:
        emit("FAIL: pass --real to run the PoC (or --probe-only for the availability check)")
        return EXIT_FAIL

    request = PocRequest(
        profile=str(args.profile),
        workspace=str(args.workspace),
        session_id=str(args.session_id),
        vsock_port=int(args.vsock_port),
        boot_timeout_s=float(args.boot_timeout_s),
        wait_timeout_s=float(args.wait_timeout_s),
        poc_root=str(args.poc_root),
    )
    factory = backend_factory
    if factory is None:
        factory = RealPocBackend
    try:
        backend = factory(request, env=source, emit=emit)
    except Exception as exc:  # setup failure must not crash the CLI
        emit(f"FAIL: harness_setup - {_error_detail(exc)}")
        emit("RESULT: FAIL")
        return EXIT_FAIL
    harness = PocHarness(request, backend, emit=emit)
    return EXIT_OK if harness.run() else EXIT_FAIL


def _install_sigterm_cleanup() -> None:
    """Turn SIGTERM into an exception so the harness `finally` still cleans up."""

    def _raise(signum: int, _frame: object) -> None:
        raise KeyboardInterrupt(f"signal {signum}")

    signal.signal(signal.SIGTERM, _raise)


# --------------------------------------------------------------------------- #
# Task payload assembly (shared by the real backend and the wire-policy test)
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class PocTaskContext:
    """Host facts every PoC task frame is built from (never a raw host path)."""

    profile: str
    profile_config: Mapping[str, Any]
    session_id: str
    provider_endpoint: str
    artifact_workdir: str
    timeout_s: int = int(DEFAULT_WAIT_TIMEOUT_S)
    max_tokens: int = 512


def resolve_provider_url(context: PocTaskContext) -> str:
    """The endpoint the guest loop calls: the profile's, or a closed loopback port."""
    if context.provider_endpoint == "closed":
        return CLOSED_PROVIDER_URL
    return str(context.profile_config.get("base_url") or "")


def build_poc_task_payload(
    context: PocTaskContext,
    *,
    task_id: str,
    user_message: str,
    forced: Mapping[str, Any] | None = None,
    metadata: Mapping[str, Any] | None = None,
    filesystem_mode: str = "",
) -> dict[str, Any]:
    """One worker-shaped dispatch payload for `manager.run_task`.

    `guard_mode` is deliberately `off`: the PoC measures the VM boundary itself,
    so no in-guest scope/sandbox layer may mask the guest's real reach; the
    payload is still narrow (no host paths - the channel payload policy refuses
    them, and the manager maps the resume metadata host->guest).
    """
    payload: dict[str, Any] = {
        "task_id": task_id,
        "profile": context.profile,
        "model": str(context.profile_config.get("model") or ""),
        "base_url": resolve_provider_url(context),
        "api_format": str(context.profile_config.get("api_format") or "openai"),
        "system_prompt": (
            "Isolation PoC harness. Call the pinned tool exactly as instructed; "
            "do not add commentary."
        ),
        "user_message": user_message,
        "skills_text": "",
        "foundation_context": "",
        "filesystem_mode": filesystem_mode
        or str(context.profile_config.get("filesystem_mode") or "workspace"),
        "guard_mode": "off",
        "max_tokens": context.max_tokens,
        "timeout_s": context.timeout_s,
        "current_session": context.session_id,
        "source_platform": "android",
        "artifact_workdir": context.artifact_workdir,
        "trailing_context_text": "",
        "input_files": [],
        "enabled_tools": [],
        "history_messages": [],
    }
    merged: dict[str, Any] = dict(metadata or {})
    if forced is not None:
        merged["forced_tool_call"] = dict(forced)
    if merged:
        payload["metadata"] = merged
    return payload


def forced_tool_call(
    name: str, tool_input: Mapping[str, Any], *, tool_use_id: str
) -> dict[str, Any]:
    """The harness-pinned tool call the PoC dispatches (deterministic, no model guess)."""
    return {
        "name": name,
        "input": dict(tool_input),
        "tool_use_id": tool_use_id,
        "source": "poc_harness",
    }


def resume_metadata_from_blocked(blocked: Mapping[str, Any]) -> dict[str, Any]:
    """The `allow_once` resume shape the mobile service stores (service.py:1590-1620)."""
    forced: dict[str, Any] = {
        "name": str(blocked.get("tool") or ""),
        "input": dict(blocked.get("input") or {}),
        "source": str(blocked.get("source") or ""),
        "tool_use_id": str(blocked.get("tool_use_id") or ""),
    }
    reason = str(blocked.get("reason") or "")
    if reason:
        forced["reason"] = reason
    return {
        "forced_tool_call": forced,
        "approved_tool_calls": [str(blocked.get("signature") or "")],
    }


# --------------------------------------------------------------------------- #
# Real backend (KVM host only; everything is bounded and fail-closed)
# --------------------------------------------------------------------------- #

GUEST_UNIT_TEMPLATE: Final = """[Unit]
Description=Igorek isolation guest runner (PoC bootstrap)
After=local-fs.target network-online.target remote-fs.target
Wants=network-online.target
RequiresMountsFor={probe_dir}

[Service]
Type=simple
User=agent
Group=agent
Environment=HOME=/home/agent
Environment=IGOREK_ISOLATION_VSOCK_PORT={port}
Environment=IGOREK_BASH_SANDBOX=off
ExecStartPre=-{probe_bin} {probe_out}
ExecStart={runner} guest-runner --vsock-cid 2 --vsock-port {port}
Restart=always
RestartSec=2

[Install]
WantedBy=multi-user.target
"""

FSTAB_LINE: Final = "workspace /workspace virtiofs defaults,nofail 0 0"
IMAGE_TIMEOUT_S: Final = 1800.0
BOOTSTRAP_BUILDER: Final = "virt-customize-poc-bootstrap"


def _command_result(returncode: int, stdout: str = "", stderr: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess([], returncode, stdout, stderr)


def run_bounded(argv: Sequence[str], *, timeout_s: float) -> subprocess.CompletedProcess[str]:
    """Run one host command under a hard timeout; never raises for a missing tool."""
    try:
        return subprocess.run(  # noqa: S603 - argv list, no shell
            list(argv),
            capture_output=True,
            text=True,
            errors="replace",
            check=False,
            timeout=timeout_s,
        )
    except FileNotFoundError as exc:
        return _command_result(127, "", f"{exc}")
    except subprocess.TimeoutExpired as exc:
        return _command_result(124, str(exc.stdout or ""), f"timed out after {timeout_s}s")


def _bus_socket_inode() -> int | None:
    bus = Path(f"/run/user/{os.getuid()}/bus")
    try:
        return bus.stat().st_ino if bus.exists() else None
    except OSError:
        return None


class RealPocBackend:
    """The real host environment: profile, workspace, image, manager and probes."""

    def __init__(
        self,
        request: PocRequest,
        *,
        env: Mapping[str, str],
        emit: Callable[[str], None] = print,
    ) -> None:
        self._request = request
        self._env = dict(env)
        self._emit = emit
        self._token = uuid.uuid4().hex[:8]
        self._session_id = request.session_id.strip() or f"poc-{request.profile}-{self._token}"
        if request.poc_root.strip():
            poc_root = Path(request.poc_root).expanduser()
            if (
                not poc_root.is_absolute()
                or poc_root.is_symlink()
                or not poc_root.is_dir()
                or any(poc_root.iterdir())
            ):
                raise PocBackendError("unsafe_poc_root", "--poc-root must be an empty absolute directory")
            self._poc_root = poc_root
        else:
            self._poc_root = Path(
                tempfile.mkdtemp(prefix="igorek-isolation-poc-", dir=str(self._poc_parent()))
            )
        self._overlay_dir = self._poc_root / "overlays"
        self._artifact_dir = self._poc_root / "artifacts"
        self._image_dir = self._poc_root / "image"
        self._profile: dict[str, Any] = {}
        self._profile_name = str(request.profile)
        self._workspace_host = Path()
        self._guest_workspace = GUEST_WORKSPACE_PATH
        self._control_dir: Path | None = None
        self._guest_control = ""
        self._canaries: list[Path] = []
        self._canary_name = f"igorek-poc-home-canary-{self._token}.txt"
        self._ssh_canary_name = f"igorek-poc-ssh-canary-{self._token}.txt"
        self._tmp_canary_name = f"igorek-poc-tmp-canary-{self._token}.txt"
        self._canary_content = f"POC-CANARY-CONTENT-{self._token}"
        self._host_proc_marker = f"igorek-poc-host-proc-{self._token}"
        self._guest_proc_marker = f"igorek-poc-guest-proc-{self._token}"
        self._host_marker_unit = f"igorek-poc-host-{self._token}"
        self._guest_probe_unit = f"poc-guest-{self._token}"
        self._host_bus_inode: int | None = None
        self._marker_proc: subprocess.Popen[str] | None = None
        self._marker_unit_created = False
        self._base_image = Path()
        self._derived_image: Path | None = None
        self._bootstrap = "base"
        self._bootstrap_tool = ""
        self._config: IsolationConfig | None = None
        self._manager: IsolationManager | None = None
        self._server_thread: threading.Thread | None = None
        self._server_error: str | None = None
        self._stop_event = threading.Event()
        self._session_vm: SessionVm | None = None
        self._own_connection: Any = None
        self._write_name = f"poc_write_{self._token}.txt"
        self._write_content = f"isolation-poc {self._token}\n"
        self._blocked: Mapping[str, Any] | None = None
        self._live_thread: threading.Thread | None = None
        self._live_cancel = threading.Event()
        self._live_result: dict[str, Any] = {}
        self._live_error: BaseException | None = None
        self._probe_ledger = ProbeLedger()
        self._probe_challenges: dict[str, Any] = {}

    # -- lifecycle ---------------------------------------------------------- #

    def prepare(self) -> PrepareEvidence:
        request = self._request
        if not request.workspace.strip():
            raise PocBackendError("workspace_missing", "pass --workspace <absolute dir>")
        try:
            profile = get_profile_config(self._profile_name)
        except (FileNotFoundError, ValueError) as exc:
            raise PocBackendError("profile_not_found", str(exc)) from exc
        backend = str(profile.get("backend") or "")
        if backend != "cc-backend":
            raise PocBackendError(
                "profile_not_cc_backend", f"{self._profile_name}: backend={backend!r}"
            )
        try:
            export = resolve_workspace_export(
                request.workspace, policy=WorkspacePolicy.from_environment()
            )
        except IsolationError as exc:
            raise PocBackendError(f"workspace_denied:{exc.reason}", exc.detail) from exc
        self._profile = dict(profile)
        self._workspace_host = Path(str(export["host_path"]))
        self._guest_workspace = str(export["guest_path"])
        self._control_dir = self._workspace_host / POC_DIR_NAME / self._token
        self._guest_control = f"{self._guest_workspace}/{POC_DIR_NAME}/{self._token}"
        self._control_dir.mkdir(parents=True, exist_ok=True)
        self._artifact_dir.mkdir(parents=True, exist_ok=True)
        self._overlay_dir.mkdir(parents=True, exist_ok=True)
        self._write_control_files()
        self._start_host_marker_process()
        self._start_host_marker_unit()
        self._base_image = Path(
            self._env.get(ENV_BASE_IMAGE, "").strip() or str(default_image_path())
        ).expanduser()
        if not self._base_image.is_file():
            raise PocBackendError("base_image_missing", f"no base image at {self._base_image}")
        image = self._prepare_guest_image()
        config_env = dict(self._env)
        config_env.update(
            {
                ENV_MODE: MODE_KVM,
                ENV_BASE_IMAGE: str(image),
                ENV_OVERLAY_DIR: str(self._overlay_dir),
                ENV_VSOCK_PORT: str(self._request.vsock_port),
            }
        )
        config = IsolationConfig.from_env(config_env)
        if not config.enabled:
            raise PocBackendError(
                "isolation_config_refused", config.reason or "isolation config is disabled"
            )
        self._config = config
        return PrepareEvidence(
            profile=self._profile_name,
            workspace_host=str(self._workspace_host),
            base_image=str(image),
            bootstrap=self._bootstrap,
            guest_bootstrap_tool=self._bootstrap_tool,
        )

    def _manager_seams(self) -> ManagerSeams:
        """The provider-auth seam, wired exactly like production ``from_env``.

        The PoC builds its own ``IsolationConfig`` (its own overlay/artifact
        dirs), so it cannot call ``IsolationManager.from_env``; it mirrors that
        constructor's opt-in instead: ``IGOREK_ISOLATION_EGRESS_AUTH`` enables
        the host-side proxy (plan todo 18), so a remote provider call is
        authorized by the host and the guest never holds the key. Without it the
        manager behaves exactly as before.
        """
        source = os.environ
        if not str(source.get(ENV_EGRESS_AUTH) or "").strip():
            return ManagerSeams()
        return ManagerSeams(provider_secrets=ProviderAuthInjector.from_env(source))

    def start_session(self) -> SessionEvidence:
        if self._manager is None:
            if self._config is None:
                raise PocBackendError("not_prepared", "prepare() must run first")
            self._manager = IsolationManager(self._config, seams=self._manager_seams())
            self._start_channel_server()
        started = time.monotonic()
        vm = self._ensure_session_ready()
        overlay = vm.domain.overlay
        connection = self._manager.channel_server.session_for(SessionId(self._session_id))
        return SessionEvidence(
            domain=domain_name(vm.spec),
            overlay=str(overlay) if overlay is not None else "",
            host_workspace=str(vm.export["host_path"]),
            export_guest=str(vm.export["guest_path"]),
            peer_cid=int(vm.peer_cid),
            connected=connection is not None,
            boot_seconds=time.monotonic() - started,
            bwrap_probe=self._await_bwrap_probe(),
        )

    def request_write(self) -> ApprovalEvidence:
        self._ensure_session_ready()
        task_id = f"poc-approval-{self._token}"
        forced = forced_tool_call(
            "Write",
            {
                "file_path": str(self._workspace_host / self._write_name),
                "content": self._write_content,
            },
            tool_use_id=FORCED_WRITE_ID,
        )
        payload = self._task_payload(
            task_id=task_id,
            user_message="PoC approval probe: write the pinned artifact.",
            forced=forced,
        )
        result = self._dispatch(task_id, payload)
        blocked = tuple(
            item
            for item in (result.get("approval_blocked_tools") or [])
            if isinstance(item, dict)
        )
        self._blocked = blocked[0] if blocked else None
        return ApprovalEvidence(
            status=str(result.get("status") or ""),
            blocked_tools=blocked,
            host_workspace=str(self._workspace_host),
            vm_stopped=self._session_gone(),
        )

    def approve_write(self) -> WriteEvidence:
        if self._blocked is None:
            raise PocBackendError(
                "approval_not_recorded", "request_write() produced no blocked payload"
            )
        self._ensure_session_ready()
        metadata = resume_metadata_from_blocked(self._blocked)
        task_id = f"poc-write-{self._token}"
        payload = self._task_payload(
            task_id=task_id,
            user_message="PoC write resume (allow_once).",
            metadata=metadata,
            provider_endpoint="closed",
        )
        self._dispatch(task_id, payload)
        path = self._workspace_host / self._write_name
        wait_until(path.is_file, self._request.wait_timeout_s, what=f"host file {path}")
        content = path.read_text(encoding="utf-8", errors="replace")
        return WriteEvidence(
            host_file=str(path),
            expected_content=self._write_content,
            actual_content=content,
            forced_tool_ok=self._forced_tool_result_ok(FORCED_WRITE_ID, "Write"),
        )

    def probe_home(self) -> HomeEvidence:
        tmp_canary = str(Path(tempfile.gettempdir()) / self._tmp_canary_name)
        control = f"{self._guest_control}/host_home_canary.txt"
        tmp_result = self._forced_read_preview("tmp_canary", tmp_canary)
        control_result = self._forced_read_preview("control_canary", control)
        self._start_live_probe()
        return HomeEvidence(
            canary_content=self._canary_content,
            host_tmp_canary_path=tmp_canary,
            control_path=control,
            tmp_canary_result=tmp_result,
            control_result=control_result,
            report=self._await_artifact("home"),
            canary_name=self._canary_name,
            ssh_canary_name=self._ssh_canary_name,
        )

    def probe_dbus(self) -> DbusEvidence:
        self._start_live_probe()
        return DbusEvidence(
            report=self._await_artifact("dbus"),
            host_marker_unit=self._host_marker_unit if self._marker_unit_created else "",
            host_bus_inode=self._host_bus_inode,
            guest_probe_unit=self._guest_probe_unit,
        )

    def probe_processes(self) -> ProcessEvidence:
        self._start_live_probe()
        return ProcessEvidence(
            report=self._await_artifact("procs"),
            host_proc_marker=self._host_proc_marker,
            guest_proc_marker=self._guest_proc_marker,
        )

    def stop_session(self) -> StopEvidence:
        self._start_live_probe()
        started = self._wait_for_bash_call_started(timeout_s=self._request.wait_timeout_s)
        self._live_cancel.set()
        thread = self._live_thread
        if thread is not None:
            thread.join(timeout=self._request.wait_timeout_s)
        result = self._live_result
        facts = self.teardown_facts()
        return StopEvidence(
            result_status=str(result.get("status") or ""),
            failure_reason=str(result.get("failure_reason") or ""),
            call_started=started,
            domain_gone=facts.domain_gone,
            overlay_gone=facts.overlay_gone,
        )

    def teardown_facts(self) -> TeardownEvidence:
        overlay_dir_empty = True
        if self._overlay_dir.is_dir():
            overlay_dir_empty = not any(self._overlay_dir.glob("*.qcow2"))
        domain_gone = True
        overlay_gone = True
        vm = self._session_vm
        if vm is not None:
            domain_gone = self._domain_absent(domain_name(vm.spec))
            overlay_gone = not overlay_path(vm.spec).exists()
        return TeardownEvidence(
            domain_gone=domain_gone,
            overlay_gone=overlay_gone,
            overlay_dir_empty=overlay_dir_empty,
        )

    def cleanup(self) -> CleanupReceipt:
        receipt = CleanupReceipt()
        self._stop_host_marker_process(receipt)
        self._remove_canaries(receipt)
        self._reset_host_marker_unit(receipt)
        if self._manager is not None:
            try:
                self._manager.close()
            except Exception as exc:  # leftover reporting
                receipt.leftovers.append(f"manager close failed: {_error_detail(exc)}")
        vm = self._session_vm
        if vm is not None:
            name = domain_name(vm.spec)
            receipt.add("session_domain", self._domain_absent(name), name)
            overlay = vm.domain.overlay
            if overlay is not None:
                try:
                    Path(overlay).unlink(missing_ok=True)
                except OSError as exc:
                    receipt.leftovers.append(f"overlay unlink failed: {exc}")
                receipt.add("session_overlay", not Path(overlay).exists(), str(overlay))
        if self._server_thread is not None:
            self._stop_event.set()
            self._server_thread.join(timeout=10.0)
            receipt.add("channel_server", not self._server_thread.is_alive())
        if self._own_connection is not None:
            try:
                self._own_connection.close()
            except Exception:  # a closed probe connection is fine
                pass
            self._own_connection = None
        control_dir = self._control_dir
        if control_dir is not None:
            expected = self._workspace_host / POC_DIR_NAME / self._token
            if not control_dir.is_absolute() or control_dir != expected:
                receipt.leftovers.append(f"workspace_control_dir unsafe path ({control_dir})")
            else:
                if control_dir.is_dir():
                    shutil.rmtree(control_dir, ignore_errors=True)
                if control_dir.parent.is_dir():
                    try:
                        control_dir.parent.rmdir()
                    except OSError:
                        pass
                receipt.add("workspace_control_dir", not control_dir.exists(), str(control_dir))
        write_path = self._workspace_host / self._write_name
        if write_path.exists():
            write_path.unlink(missing_ok=True)
        receipt.add("workspace_poc_file", not write_path.exists(), str(write_path))
        if self._poc_root.exists():
            shutil.rmtree(self._poc_root, ignore_errors=True)
        receipt.add("poc_root", not self._poc_root.exists(), str(self._poc_root))
        return receipt

    # -- internals ---------------------------------------------------------- #

    @staticmethod
    def _poc_parent() -> Path:
        parent = get_data_root() / "isolation"
        parent.mkdir(parents=True, exist_ok=True)
        return parent

    def _task_payload(
        self,
        *,
        task_id: str,
        user_message: str,
        forced: Mapping[str, Any] | None = None,
        metadata: Mapping[str, Any] | None = None,
        provider_endpoint: str = "profile",
        filesystem_mode: str = "",
    ) -> dict[str, Any]:
        context = PocTaskContext(
            profile=self._profile_name,
            profile_config=self._profile,
            session_id=self._session_id,
            provider_endpoint=provider_endpoint,
            artifact_workdir=str(self._artifact_dir),
            timeout_s=int(max(30.0, self._request.wait_timeout_s)),
        )
        return build_poc_task_payload(
            context,
            task_id=task_id,
            user_message=user_message,
            forced=forced,
            metadata=metadata,
            filesystem_mode=filesystem_mode,
        )

    def _write_control_files(self) -> None:
        (self._control_dir / "host_home_canary.txt").write_text(
            self._canary_content + "\n", encoding="utf-8"
        )
        (self._control_dir / "ssh_canary_expected_absent.txt").write_text(
            self._canary_content + "\n", encoding="utf-8"
        )
        (self._control_dir / "host_proc_marker.txt").write_text(
            self._host_proc_marker + "\n", encoding="utf-8"
        )
        (self._control_dir / "guest_proc_marker.txt").write_text(
            self._guest_proc_marker + "\n", encoding="utf-8"
        )
        home_canary = Path.home() / self._canary_name
        home_canary.write_text(self._canary_content + "\n", encoding="utf-8")
        self._canaries.append(home_canary)
        tmp_canary = Path(tempfile.gettempdir()) / self._tmp_canary_name
        tmp_canary.write_text(self._canary_content + "\n", encoding="utf-8")
        self._canaries.append(tmp_canary)
        ssh_dir = Path.home() / ".ssh"
        if ssh_dir.is_dir():
            ssh_canary = ssh_dir / self._ssh_canary_name
            ssh_canary.write_text(self._canary_content + "\n", encoding="utf-8")
            self._canaries.append(ssh_canary)

    def _start_host_marker_process(self) -> None:
        self._marker_proc = subprocess.Popen(  # noqa: S603 - argv list, no shell
            [
                sys.executable,
                "-c",
                "import sys, time; time.sleep(float(sys.argv[1]))",
                str(int(max(60.0, self._request.wait_timeout_s * 4))),
                self._host_proc_marker,
            ],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    def _start_host_marker_unit(self) -> None:
        self._host_bus_inode = _bus_socket_inode()
        if self._host_bus_inode is None:
            return
        completed = run_bounded(
            [
                "systemd-run",
                "--user",
                f"--unit={self._host_marker_unit}",
                "--wait",
                "--pipe",
                "true",
            ],
            timeout_s=30.0,
        )
        self._marker_unit_created = completed.returncode == 0

    def _remove_canaries(self, receipt: CleanupReceipt) -> None:
        for path in self._canaries:
            try:
                path.unlink(missing_ok=True)
            except OSError as exc:
                receipt.leftovers.append(f"canary removal failed: {exc}")
            receipt.add("host_canary", not path.exists(), str(path))
        self._canaries = []

    def _stop_host_marker_process(self, receipt: CleanupReceipt) -> None:
        proc = self._marker_proc
        if proc is None:
            return
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5.0)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5.0)
        receipt.add("host_marker_process", proc.poll() is not None, f"pid={proc.pid}")
        self._marker_proc = None

    def _reset_host_marker_unit(self, receipt: CleanupReceipt) -> None:
        if not self._marker_unit_created:
            return
        run_bounded(["systemctl", "--user", "stop", f"{self._host_marker_unit}.service"], timeout_s=20.0)
        run_bounded(["systemctl", "--user", "reset-failed", f"{self._host_marker_unit}.service"], timeout_s=20.0)
        probe = run_bounded(
            ["systemctl", "--user", "cat", f"{self._host_marker_unit}.service"], timeout_s=20.0
        )
        receipt.add("host_marker_unit", probe.returncode != 0, self._host_marker_unit)
        self._marker_unit_created = False

    def _prepare_guest_image(self) -> Path:
        if self._guest_bootstrap_present(self._base_image):
            self._bootstrap = "base"
            self._bootstrap_tool = "image"
            return self._base_image
        self._bootstrap = "derived"
        self._bootstrap_tool = "virt-customize"
        return self._derive_poc_image(self._base_image)

    @staticmethod
    def _guest_bootstrap_present(image: Path) -> bool:
        unit = run_bounded(
            ["virt-cat", "-a", str(image), f"/etc/systemd/system/{GUEST_UNIT_NAME}"],
            timeout_s=IMAGE_TIMEOUT_S,
        )
        if unit.returncode != 0 or "guest-runner" not in unit.stdout:
            return False
        if Path(GUEST_PROBE_BIN).name not in unit.stdout:
            return False
        wants = run_bounded(
            ["virt-ls", "-a", str(image), "/etc/systemd/system/multi-user.target.wants"],
            timeout_s=IMAGE_TIMEOUT_S,
        )
        if wants.returncode != 0 or GUEST_UNIT_NAME not in wants.stdout.split():
            return False
        fstab = run_bounded(
            ["virt-cat", "-a", str(image), "/etc/fstab"], timeout_s=IMAGE_TIMEOUT_S
        )
        return fstab.returncode == 0 and "workspace /workspace virtiofs" in fstab.stdout

    def _derive_poc_image(self, source: Path) -> Path:
        self._image_dir.mkdir(parents=True, exist_ok=True)
        target = self._image_dir / "poc-base.qcow2"
        created = run_bounded(
            ["qemu-img", "create", "-f", "qcow2", "-F", "qcow2", "-b", str(source.resolve()), str(target)],
            timeout_s=120.0,
        )
        if created.returncode != 0:
            raise PocBackendError("derived_image_create_failed", created.stderr.strip())
        unit_file = self._image_dir / GUEST_UNIT_NAME
        unit_file.write_text(
            GUEST_UNIT_TEMPLATE.format(
                port=self._request.vsock_port,
                runner=GUEST_RUNNER_BIN,
                probe_dir=self._guest_control,
                probe_bin=GUEST_PROBE_BIN,
                probe_out=f"{self._guest_control}/{GUEST_BWRAP_PROBE_FILE}",
            ),
            encoding="utf-8",
        )
        policy_files = self._render_bwrap_policy_files()
        customize = run_bounded(
            [
                "virt-customize",
                "-a",
                str(target),
                "--run-command",
                "install -d -o agent -g agent -m 0755 /workspace",
                "--copy-in",
                f"{unit_file}:/etc/systemd/system/",
                "--copy-in",
                f"{policy_files['sysctl']}:{Path(USERNS_SYSCTL_FILE).parent}",
                "--copy-in",
                f"{policy_files['profile']}:{Path(APPARMOR_PROFILE_PATH).parent}",
                "--copy-in",
                f"{policy_files['probe']}:{Path(GUEST_PROBE_BIN).parent}",
                "--run-command",
                (
                    f"ln -sf /etc/systemd/system/{GUEST_UNIT_NAME} "
                    f"/etc/systemd/system/multi-user.target.wants/{GUEST_UNIT_NAME}"
                ),
                "--run-command",
                (
                    f'grep -q "workspace /workspace virtiofs" /etc/fstab || '
                    f'echo "{FSTAB_LINE}" >> /etc/fstab'
                ),
                "--run-command",
                f"chmod 0755 {GUEST_PROBE_BIN}",
                "--run-command",
                f"test -f {USERNS_SYSCTL_FILE} && test -f {APPARMOR_PROFILE_PATH}",
                "--run-command",
                f"sysctl -p {USERNS_SYSCTL_FILE} >/dev/null 2>&1 || true",
                "--run-command",
                (
                    f"command -v apparmor_parser >/dev/null 2>&1 && "
                    f"apparmor_parser -r -W {APPARMOR_PROFILE_PATH} >/dev/null 2>&1 || true"
                ),
                "--run-command",
                f"test -x {GUEST_RUNNER_BIN}",
            ],
            timeout_s=IMAGE_TIMEOUT_S,
        )
        if customize.returncode != 0:
            detail = (customize.stderr or customize.stdout or "").strip()
            raise PocBackendError("guest_bootstrap_failed", detail[-400:])
        tool_version = resolve_tool_version()
        manifest_base = manifest_path_for(source)
        if manifest_base.is_file():
            try:
                tool_version = load_manifest(manifest_base).tool_version
            except Exception:  # provenance falls back to the host tool
                tool_version = resolve_tool_version()
        manifest = build_manifest(
            target,
            ImageBuildInfo(
                tool_version=tool_version,
                repo_commit="",
                distro="debian-13-poc",
                kernel="linux-image-cloud-amd64",
                builder=BOOTSTRAP_BUILDER,
                guest_user="agent",
            ),
        )
        write_manifest(manifest, target)
        return target

    def _render_bwrap_policy_files(self) -> dict[str, Path]:
        """Guest bwrap-readiness artifacts applied to the derived PoC image.

        The probe runs the exact hardened argv from
        :func:`factory.core.isolation.guest_bwrap.probe_bwrap_argv`, so a
        passing marker means the sandboxed bash invocation really works.
        """
        probe = self._image_dir / Path(GUEST_PROBE_BIN).name
        probe_argv = " ".join(
            shlex.quote(part) for part in probe_bwrap_argv("/usr/bin/bwrap")
        )
        probe.write_text(
            "#!/bin/bash\n"
            'out="${1:-/tmp/igorek-bwrap-probe.txt}"\n'
            f"if {probe_argv} >/tmp/igorek-bwrap-probe.log 2>&1; then\n"
            f"  printf '%s\\n' '{PROBE_OK_TEXT}' > \"$out\"\n"
            "else\n"
            f"  printf '%s\\n' '{SANDBOX_UNAVAILABLE_PREFIX}: namespace_blocked' > \"$out\"\n"
            "fi\n",
            encoding="utf-8",
        )
        probe.chmod(0o755)
        sysctl = self._image_dir / Path(USERNS_SYSCTL_FILE).name
        sysctl.write_text(
            "".join(f"{key} = {value}\n" for key, value in USERNS_SYSCTL_ENTRIES),
            encoding="utf-8",
        )
        profile = self._image_dir / BWRAP_APPARMOR_PROFILE
        profile.write_text(
            "abi <abi/4.0>,\n"
            "include <tunables/global>\n"
            f"profile {BWRAP_APPARMOR_PROFILE} /usr/bin/bwrap flags=(unconfined) {{\n"
            "  userns,\n"
            f"  include if exists <local/{BWRAP_APPARMOR_PROFILE}>\n"
            "}\n",
            encoding="utf-8",
        )
        return {"probe": probe, "sysctl": sysctl, "profile": profile}

    def _await_bwrap_probe(self) -> str:
        """Verify the required guest probe or read the legacy PoC marker."""
        if self._bootstrap == "base":
            unit = run_bounded(
                ["virt-cat", "-a", str(self._base_image), f"/etc/systemd/system/{GUEST_UNIT_NAME}"],
                timeout_s=IMAGE_TIMEOUT_S,
            )
            lines = set(unit.stdout.splitlines()) if unit.returncode == 0 else set()
            if (
                f"ExecStartPre={GUEST_PROBE_BIN}" in lines
                and "Requires=workspace.mount igorek-egress-guard.service" in lines
                and self._manager is not None
                and self._manager.channel_server.session_for(SessionId(self._session_id)) is not None
            ):
                return f"{PROBE_OK_TEXT} (required ExecStartPre completed before handshake)"
            return ""
        path = self._control_dir / GUEST_BWRAP_PROBE_FILE
        try:
            wait_until(
                path.is_file,
                self._request.boot_timeout_s,
                what=f"guest bwrap probe marker {path}",
            )
        except TimeoutError:
            return ""
        try:
            return path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            return ""

    def _start_channel_server(self) -> None:
        if self._server_thread is not None or self._manager is None:
            return
        self._stop_event.clear()

        def _serve() -> None:
            try:
                self._manager.channel_server.serve_forever(self._stop_event)  # type: ignore[union-attr]
            except Exception as exc:  # surfaced to the waiter below
                self._server_error = _error_detail(exc)

        self._server_thread = threading.Thread(
            target=_serve, name="poc-channel-server", daemon=True
        )
        self._server_thread.start()

    def _ensure_session_ready(self) -> SessionVm:
        if self._manager is None or self._config is None:
            raise PocBackendError("not_prepared", "prepare() must run first")
        try:
            vm = self._manager.ensure_vm(
                SessionSpec(self._session_id, str(self._workspace_host))
            )
        except IsolationError as exc:
            raise PocBackendError(f"session_start_failed:{exc.reason}", exc.detail) from exc
        self._session_vm = vm
        if self._manager.channel_server.session_for(SessionId(self._session_id)) is None:
            self._wait_for_endpoint()
        return vm

    def _wait_for_endpoint(self) -> None:
        assert self._manager is not None

        def _connected() -> bool:
            if self._server_error:
                raise PocBackendError("channel_server_failed", self._server_error)
            return (
                self._manager.channel_server.session_for(SessionId(self._session_id))
                is not None
            )

        try:
            wait_until(_connected, self._request.boot_timeout_s, what="the guest runner connection")
        except TimeoutError as exc:
            raise PocBackendError(
                "guest_runner_not_connected",
                f"domain={domain_name(self._session_vm.spec) if self._session_vm else '?'} "
                f"bootstrap={self._bootstrap}: the guest never opened the vsock channel "
                f"(the image must start igorek-factory guest-runner and mount /workspace) ({exc})",
            ) from exc

    def _dispatch(
        self,
        task_id: str,
        payload: Mapping[str, Any],
        *,
        cancel_check: Callable[[], bool] | None = None,
    ) -> dict[str, Any]:
        return self._run_dispatch(task_id, payload, cancel_check=cancel_check)

    def _run_dispatch(
        self,
        task_id: str,
        payload: Mapping[str, Any],
        *,
        cancel_check: Callable[[], bool] | None = None,
    ) -> dict[str, Any]:
        if self._manager is None:
            raise PocBackendError("not_prepared", "prepare() must run first")
        task: dict[str, Any] = {"task_id": task_id, "metadata": {"session": self._session_id}}
        try:
            return self._manager.run_task(
                task,
                str(self._workspace_host),
                payload,
                cancel_check=cancel_check,
            )
        except IsolationError as exc:
            raise PocBackendError(f"dispatch_failed:{exc.reason}", exc.detail) from exc

    def _forced_read_preview(self, name: str, guest_path: str) -> str:
        self._ensure_session_ready()
        task_id = f"poc-read-{name}-{self._token}"
        tool_id = f"forced_poc_{name}"
        payload = self._task_payload(
            task_id=task_id,
            user_message=f"PoC read probe {name}.",
            forced=forced_tool_call("Read", {"file_path": guest_path}, tool_use_id=tool_id),
            provider_endpoint="closed",
            filesystem_mode="full-dangerous",
        )
        self._dispatch(task_id, payload)
        return self._forced_result_preview(tool_id)

    def _start_live_probe(self) -> None:
        if self._live_thread is not None:
            return
        self._ensure_session_ready()
        self._probe_challenges = {
            name: self._probe_ledger.issue(name) for name in ("home", "dbus", "procs")
        }
        script = self._control_dir / "poc_probe.sh"
        script.write_text(
            live_probe_script(
                guest_control=self._guest_control,
                host_marker_unit=self._host_marker_unit,
                guest_probe_unit=self._guest_probe_unit,
                canary_name=self._canary_name,
                ssh_canary_name=self._ssh_canary_name,
                canary_content=self._canary_content,
                nonces={
                    name: challenge.nonce for name, challenge in self._probe_challenges.items()
                },
            ),
            encoding="utf-8",
        )
        script.chmod(0o755)
        task_id = f"poc-live-{self._token}"
        payload = self._task_payload(
            task_id=task_id,
            user_message=live_probe_user_message(self._guest_control),
            provider_endpoint="profile",
        )

        def _dispatch() -> None:
            try:
                self._live_result.update(
                    self._run_dispatch(task_id, payload, cancel_check=self._live_cancel.is_set)
                )
            except Exception as exc:  # reported as stop/probe evidence
                self._live_error = exc

        self._live_thread = threading.Thread(
            target=_dispatch, name="poc-live-dispatch", daemon=True
        )
        self._live_thread.start()

    def _await_artifact(self, name: str) -> str:
        artifact = self._control_dir / f"probe_{name}.txt"
        try:
            wait_until(artifact.is_file, self._request.wait_timeout_s, what=f"guest {name} report")
        except TimeoutError as exc:
            error = self._live_error
            detail = f" (dispatch error: {_error_detail(error)})" if error else ""
            raise PocBackendError(
                "probe_artifact_missing",
                f"the guest never wrote {artifact}; the model must run "
                f"{self._guest_control}/poc_probe.sh with Bash{detail} ({exc})",
            ) from exc
        report = artifact.read_text(encoding="utf-8", errors="replace")
        challenge = self._probe_challenges.get(name)
        if challenge is None:
            raise PocBackendError(
                "probe_report_unissued", f"no nonce challenge was issued for {name!r}"
            )
        verdict = self._probe_ledger.verify(
            name,
            report,
            artifact_mtime=artifact.stat().st_mtime,
            host_observations=self._probe_host_observations(name),
        )
        if not verdict.ok:
            raise PocBackendError(f"probe_report_rejected:{verdict.reason}", verdict.detail)
        return report

    def _probe_host_observations(self, name: str) -> dict[str, bool]:
        """Host-observed facts that must hold before a probe report is believed."""
        if name == "home":
            return {"canary_files_present": all(path.exists() for path in self._canaries)}
        if name == "dbus":
            return {"host_bus_socket_present": self._host_bus_inode is not None}
        if name == "procs":
            return {
                "host_marker_process_alive": self._marker_proc is not None
                and self._marker_proc.poll() is None
            }
        return {}

    # -- public surface for the todo-28 negative security suite ------------- #

    def guest_control_dir(self) -> str:
        """Guest path of this run's control dir (script and report drop point)."""
        if not self._guest_control:
            raise PocBackendError("not_prepared", "prepare() must run first")
        return self._guest_control

    def workspace_host_path(self) -> str:
        """Host realpath of the single workspace export of this session."""
        if not self._workspace_host:
            raise PocBackendError("not_prepared", "prepare() must run first")
        return str(self._workspace_host)

    @property
    def session_spec(self) -> DomainSpec:
        """The live session's domain spec, for host-side libvirt observations."""
        self._ensure_session_ready()
        vm = self._session_vm
        if vm is None:
            raise PocBackendError("not_prepared", "the session VM is not running")
        return vm.spec

    def write_control_file(self, name: str, content: str) -> None:
        """Write one host-owned file into the shared control dir (guest-visible)."""
        if not self._control_dir.is_dir():
            raise PocBackendError("not_prepared", "prepare() must run first")
        (self._control_dir / name).write_text(content, encoding="utf-8")

    def run_guest_script(self, script: str, *, report_name: str) -> str:
        """Run one guest probe script through the model-driven live path.

        The caller owns the script text and its nonce; the host writes the
        script into the session control dir, asks the model to run it with Bash
        exactly once, and returns the raw report text. Verifying the report is
        the caller's job.
        """
        self._ensure_session_ready()
        script_name = f"{report_name}.sh"
        path = self._control_dir / script_name
        path.write_text(script, encoding="utf-8")
        path.chmod(0o755)
        task_id = f"probe-{report_name}-{self._token}"
        payload = self._task_payload(
            task_id=task_id,
            user_message=(
                "Run exactly this one command with the Bash tool and nothing else, "
                f"then stop: sh {self._guest_control}/{script_name}"
            ),
            provider_endpoint="profile",
        )
        self._dispatch(task_id, payload)
        return self._await_report(report_name)

    def report_mtime(self, report_name: str) -> float:
        """Host mtime of one probe report (the freshness anchor of the tamper check)."""
        path = self._control_dir / report_name
        if not path.is_file():
            raise PocBackendError("probe_artifact_missing", f"no report at {path}")
        return path.stat().st_mtime

    def _await_report(self, report_name: str) -> str:
        path = self._control_dir / report_name
        try:
            wait_until(
                path.is_file,
                self._request.wait_timeout_s,
                what=f"guest report {report_name}",
            )
        except TimeoutError as exc:
            raise PocBackendError(
                "probe_artifact_missing",
                f"the guest never wrote {path}; the model must run it with Bash ({exc})",
            ) from exc
        return path.read_text(encoding="utf-8", errors="replace")

    def _trace_records(self) -> list[dict[str, Any]]:
        path = self._artifact_dir / TRACE_FILENAME
        if not path.is_file():
            return []
        records: list[dict[str, Any]] = []
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(item, dict):
                records.append(item)
        return records

    def _forced_result_preview(self, tool_use_id: str) -> str:
        for record in self._trace_records():
            if record.get("kind") != CcTraceEvent.TOOL_RESULT:
                continue
            if str(record.get("tool_use_id") or "") != tool_use_id:
                continue
            return str(record.get("raw_result_preview") or "")
        raise PocBackendError(
            "missing_trace_result", f"no tool.result record for {tool_use_id} was relayed"
        )

    def _forced_tool_result_ok(self, tool_use_id: str, tool_name: str) -> bool:
        for record in self._trace_records():
            if record.get("kind") != CcTraceEvent.TOOL_RESULT:
                continue
            if str(record.get("tool_use_id") or "") != tool_use_id:
                continue
            if str(record.get("tool_name") or "") != tool_name:
                continue
            preview = str(record.get("raw_result_preview") or "")
            if int(record.get("raw_result_len") or 0) > 0 and "Error executing" not in preview:
                return True
        return False

    def _wait_for_bash_call_started(self, *, timeout_s: float) -> bool:
        def _seen() -> bool:
            for record in self._trace_records():
                if record.get("kind") != CcTraceEvent.TOOL_REQUEST_BATCH:
                    continue
                tools = record.get("tools")
                if not isinstance(tools, list):
                    continue
                if any(
                    isinstance(tool, dict) and str(tool.get("name") or "") == "Bash"
                    for tool in tools
                ):
                    return True
            return False

        try:
            wait_until(_seen, timeout_s, interval_s=0.2, what="the guest Bash call to start")
        except TimeoutError:
            return False
        return True

    def _session_gone(self) -> bool:
        facts = self.teardown_facts()
        return facts.domain_gone and facts.overlay_gone

    def _domain_absent(self, name: str) -> bool:
        if self._config is None:
            return False
        try:
            if self._own_connection is None:
                self._own_connection = open_libvirt_connection(self._config.libvirt_uri)
        except IsolationError:
            return False
        try:
            self._own_connection.lookupByName(name)
        except Exception:  # libvirt boundary: "no such domain" is the expected outcome
            return True
        return False


# --------------------------------------------------------------------------- #
# Tests: the pure harness is exercised with fakes; the real path stays guarded
# --------------------------------------------------------------------------- #

import pytest  # noqa: E402

from factory.core.isolation.channel import (  # noqa: E402
    ChannelConfig,
    ChannelError,
    parse_message,
)


def good_home_report(*, control: str = "ok", home: str = "absent", home_read: str = "no",
                     ssh: str = "absent", ssh_read: str = "no",
                     guest_home: str = "/home/agent") -> str:
    return "\n".join(
        [
            "POC_PROBE=home",
            f"GUEST_HOME={guest_home}",
            f"POC_CONTROL_READ={control}",
            f"POC_HOME_CANARY={home}",
            f"POC_HOME_CANARY_READABLE={home_read}",
            f"POC_SSH_CANARY={ssh}",
            f"POC_SSH_CANARY_READABLE={ssh_read}",
        ]
    )


def good_dbus_report(*, units: str = "0", cat: str = "1", bus: str = "0",
                     inode: str = "9999", systemctl_rc: str = "1",
                     busctl_rc: str = "1") -> str:
    return "\n".join(
        [
            "POC_PROBE=dbus",
            f"POC_SYSTEMCTL_RC={systemctl_rc}",
            f"POC_BUSCTL_RC={busctl_rc}",
            f"POC_HOST_MARKER_IN_UNITS={units}",
            f"POC_HOST_MARKER_IN_BUS={bus}",
            f"POC_HOST_MARKER_IN_CAT={cat}",
            "POC_SYSTEMD_RUN_RC=1",
            f"POC_GUEST_BUS_INODE={inode}",
        ]
    )


def good_procs_report(*, host_matches: str = "0", guest_matches: str = "1",
                      marker_missing: bool = False) -> str:
    lines = ["POC_PROBE=procs"]
    if marker_missing:
        lines.append("POC_MARKER_MISSING=1")
    lines.extend(
        [
            f"POC_HOST_PROC_MATCHES={host_matches}",
            f"POC_GUEST_PROC_MATCHES={guest_matches}",
            "POC_PS_BEGIN",
            "UID PID PPID C STIME TTY TIME CMD",
            "POC_PS_END",
        ]
    )
    return "\n".join(lines)


def good_prepare() -> PrepareEvidence:
    return PrepareEvidence(
        profile="cc-wally",
        workspace_host="/home/example/project",
        base_image="/data/isolation/images/base.qcow2",
        bootstrap="derived",
        guest_bootstrap_tool="virt-customize",
    )


def good_session() -> SessionEvidence:
    return SessionEvidence(
        domain="igorek-session-poc-1",
        overlay="/data/isolation/overlays/poc-1.qcow2",
        host_workspace="/home/example/project",
        export_guest="/workspace",
        peer_cid=42,
        connected=True,
        boot_seconds=12.5,
        bwrap_probe=f"{PROBE_OK_TEXT}\n",
    )


def good_approval(*, status: str = "waiting_approval", vm_stopped: bool = True,
                  target: str = "/home/example/project/poc_write_1.txt") -> ApprovalEvidence:
    return ApprovalEvidence(
        status=status,
        blocked_tools=(
            {
                "tool": "Write",
                "input": {"file_path": target, "content": "x"},
                "target": target,
                "signature": f"Write:file_path={target}",
                "tool_use_id": "forced_poc_write",
            },
        ),
        host_workspace="/home/example/project",
        vm_stopped=vm_stopped,
    )


def good_write(*, content: str = "isolation-poc 1\n", forced_ok: bool = True) -> WriteEvidence:
    return WriteEvidence(
        host_file="/home/example/project/poc_write_1.txt",
        expected_content="isolation-poc 1\n",
        actual_content=content,
        forced_tool_ok=forced_ok,
    )


def good_home(*, control_result: str = "POC-CANARY-CONTENT-1\n",
              tmp_canary_result: str = "File not found: /tmp/igorek-poc-tmp-canary-1.txt",
              canary_content: str = "POC-CANARY-CONTENT-1",
              report: str = "") -> HomeEvidence:
    return HomeEvidence(
        canary_content=canary_content,
        host_tmp_canary_path="/tmp/igorek-poc-tmp-canary-1.txt",
        control_path="/workspace/.igorek-isolation-poc/1/host_home_canary.txt",
        tmp_canary_result=tmp_canary_result,
        control_result=control_result,
        report=report or good_home_report(),
        canary_name="igorek-poc-home-canary-1.txt",
        ssh_canary_name="igorek-poc-ssh-canary-1.txt",
    )


def good_dbus(*, units: str = "0", cat: str = "1", bus: str = "0", inode: str = "9999",
              systemctl_rc: str = "1", busctl_rc: str = "1",
              host_marker_unit: str = "igorek-poc-host-1",
              host_bus_inode: int | None = 4242) -> DbusEvidence:
    return DbusEvidence(
        report=good_dbus_report(
            units=units,
            cat=cat,
            bus=bus,
            inode=inode,
            systemctl_rc=systemctl_rc,
            busctl_rc=busctl_rc,
        ),
        host_marker_unit=host_marker_unit,
        host_bus_inode=host_bus_inode,
        guest_probe_unit="poc-guest-1",
    )


def good_procs(**overrides: str) -> ProcessEvidence:
    return ProcessEvidence(
        report=good_procs_report(**overrides),
        host_proc_marker="igorek-poc-host-proc-1",
        guest_proc_marker="igorek-poc-guest-proc-1",
    )


def good_stop(*, reason: str = "canceled", started: bool = True,
              domain_gone: bool = True, overlay_gone: bool = True) -> StopEvidence:
    return StopEvidence(
        result_status="fail",
        failure_reason=reason,
        call_started=started,
        domain_gone=domain_gone,
        overlay_gone=overlay_gone,
    )


def good_teardown() -> TeardownEvidence:
    return TeardownEvidence(domain_gone=True, overlay_gone=True, overlay_dir_empty=True)


class FakeBackend:
    """Scripted backend: canned evidence, per-step raising, one cleanup receipt."""

    def __init__(
        self,
        *,
        raise_at: str = "",
        overrides: Mapping[str, Any] | None = None,
        receipt: CleanupReceipt | None = None,
    ) -> None:
        self.calls: list[str] = []
        self._raise_at = raise_at
        self._overrides = dict(overrides or {})
        self._receipt = receipt if receipt is not None else CleanupReceipt()

    def _value(self, name: str, default: Callable[[], Any]) -> Any:
        self.calls.append(name)
        if self._raise_at == name:
            raise PocBackendError("fake_failure", name)
        return self._overrides.get(name, default())

    def prepare(self) -> PrepareEvidence:
        return self._value("prepare", good_prepare)

    def start_session(self) -> SessionEvidence:
        return self._value("start_session", good_session)

    def request_write(self) -> ApprovalEvidence:
        return self._value("request_write", good_approval)

    def approve_write(self) -> WriteEvidence:
        return self._value("approve_write", good_write)

    def probe_home(self) -> HomeEvidence:
        return self._value("probe_home", good_home)

    def probe_dbus(self) -> DbusEvidence:
        return self._value("probe_dbus", good_dbus)

    def probe_processes(self) -> ProcessEvidence:
        return self._value("probe_processes", good_procs)

    def stop_session(self) -> StopEvidence:
        return self._value("stop_session", good_stop)

    def teardown_facts(self) -> TeardownEvidence:
        return self._value("teardown_facts", good_teardown)

    def cleanup(self) -> CleanupReceipt:
        self.calls.append("cleanup")
        return self._receipt


def run_harness(backend: PocBackend) -> tuple[bool, list[str], AssertionReport]:
    lines: list[str] = []
    report = AssertionReport(emit=lines.append)
    harness = PocHarness(PocRequest(profile="cc-wally"), backend, emit=lines.append, report=report)
    return harness.run(), lines, report


def test_probe_skips_without_the_env_flag() -> None:
    # Given: a host that would be ready, but the explicit opt-in flag is absent
    # When/Then: the probe refuses to run instead of booting anything
    reason = probe_availability({}, structure=lambda: None)
    assert reason is not None and KVM_AVAILABLE_ENV in reason


def test_probe_reports_the_structural_reason_when_requested() -> None:
    # Given: KVM_AVAILABLE=1 but the structural probe fails
    # When/Then: the exact structural reason is surfaced
    assert (
        probe_availability({"KVM_AVAILABLE": "1"}, structure=lambda: "missing /dev/kvm")
        == "missing /dev/kvm"
    )


def test_probe_is_ready_when_flag_and_structure_hold() -> None:
    # Given: the flag and a passing structural probe
    # When/Then: no skip reason is produced
    assert probe_availability({"KVM_AVAILABLE": "yes"}, structure=lambda: None) is None


def test_wait_until_is_bounded_and_does_not_hang() -> None:
    # Given: a condition that never becomes true and a fake clock
    state = {"now": 0.0}

    def sleep(seconds: float) -> None:
        state["now"] += max(seconds, 1.0)

    # When/Then: the wait raises instead of hanging forever
    with pytest.raises(TimeoutError):
        wait_until(
            lambda: False, 3.0, clock=lambda: state["now"], sleep=sleep, what="never"
        )


def test_wait_until_returns_when_the_condition_holds() -> None:
    # Given: a condition that turns true on the second poll
    state = {"now": 0.0, "polls": 0}

    def predicate() -> bool:
        state["polls"] += 1
        return state["polls"] >= 2

    wait_until(predicate, 30.0, clock=lambda: state["now"], sleep=lambda _s: None)
    assert state["polls"] == 2


def test_report_renders_each_assertion_and_fails_on_any_failure() -> None:
    # Given: one passing, one failing and one skipped assertion
    lines: list[str] = []
    report = AssertionReport(emit=lines.append)
    report.record("a", True, "ok")
    report.record("b", False, "boom")
    report.record("c", None, "depends")
    # When/Then: every assertion has its own line and the run is not ok
    assert lines == ["PASS: a - ok", "FAIL: b - boom", "SKIP: c - depends"]
    assert len(report.failed) == 1 and len(report.skipped) == 1
    assert report.ok is False


def test_cleanup_receipt_marks_leftovers(tmp_path: Path) -> None:
    # Given: one removed item and one file still on disk
    leftover = tmp_path / "kept.txt"
    leftover.write_text("x", encoding="utf-8")
    receipt = CleanupReceipt()
    receipt.add("removed", True, "gone")
    receipt.add("kept", not leftover.exists(), str(leftover))
    # When/Then: the receipt is dirty and says which item is left
    assert receipt.clean is False
    assert receipt.leftovers == [f"kept ({leftover})"]
    assert any("LEFTOVER kept" in line for line in receipt.render())


def test_harness_happy_path_passes_every_assertion() -> None:
    # Given: a backend that produces the good evidence for every step
    backend = FakeBackend()
    # When: the harness runs
    ok, lines, report = run_harness(backend)
    # Then: every assertion passed in order, cleanup ran, and no FAIL/SKIP exists
    assert ok is True
    assert [item.name for item in report.passed] == [
        ASSERT_PREPARE,
        ASSERT_SESSION,
        ASSERT_APPROVAL,
        ASSERT_WRITE,
        ASSERT_HOME,
        ASSERT_DBUS,
        ASSERT_PROCS,
        ASSERT_STOP,
        ASSERT_TEARDOWN,
        ASSERT_CLEAN,
    ]
    assert report.failed == [] and report.skipped == []
    assert backend.calls == [
        "prepare",
        "start_session",
        "request_write",
        "approve_write",
        "probe_home",
        "probe_dbus",
        "probe_processes",
        "stop_session",
        "teardown_facts",
        "cleanup",
    ]
    assert lines[-1] == "RESULT: PASS"


def test_harness_fails_the_assertion_with_bad_evidence() -> None:
    # Given: a backend whose approval evidence was not parked
    backend = FakeBackend(overrides={"request_write": lambda: good_approval(status="fail")})
    # When: the harness runs
    ok, lines, report = run_harness(backend)
    # Then: exactly that assertion fails and the run fails
    assert ok is False
    assert [item.name for item in report.failed] == [ASSERT_APPROVAL]
    assert any(line.startswith(f"FAIL: {ASSERT_APPROVAL}") for line in lines)
    assert lines[-1] == "RESULT: FAIL"


def test_harness_skips_dependent_steps_after_a_backend_error_and_cleans_up() -> None:
    # Given: a backend that cannot start the session
    backend = FakeBackend(raise_at="start_session")
    # When: the harness runs
    ok, lines, report = run_harness(backend)
    # Then: the failing step is FAIL, its dependants are SKIP (never PASS), cleanup ran
    assert ok is False
    assert [item.name for item in report.failed] == [ASSERT_SESSION]
    assert [item.name for item in report.skipped] == [
        ASSERT_APPROVAL,
        ASSERT_WRITE,
        ASSERT_HOME,
        ASSERT_DBUS,
        ASSERT_PROCS,
        ASSERT_STOP,
        ASSERT_TEARDOWN,
    ]
    assert "cleanup" in backend.calls
    assert lines[-1] == "RESULT: FAIL"


def test_harness_fails_when_cleanup_leaves_something_behind() -> None:
    # Given: a clean assertion run whose cleanup left a file
    receipt = CleanupReceipt()
    receipt.add("stale_overlay", False, "/tmp/leftover.qcow2")
    backend = FakeBackend(receipt=receipt)
    # When: the harness runs
    ok, lines, report = run_harness(backend)
    # Then: the receipt is the last assertion and it fails the run
    assert ok is False
    assert [item.name for item in report.failed] == [ASSERT_CLEAN]
    assert any("CLEANUP: LEFTOVER stale_overlay" in line for line in lines)


def test_harness_reports_a_cleanup_crash_as_a_leftover() -> None:
    # Given: a backend whose cleanup raises
    class BrokenCleanup(FakeBackend):
        def cleanup(self) -> CleanupReceipt:
            raise OSError("no space left")

    # When: the harness runs
    ok, _lines, report = run_harness(BrokenCleanup())
    # Then: the run fails on leftovers instead of crashing
    assert ok is False
    assert [item.name for item in report.failed] == [ASSERT_CLEAN]
    assert "no space left" in report.failed[0].detail


def test_real_poc_rejects_nonempty_scratch_root(tmp_path: Path) -> None:
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    sentinel = scratch / "sentinel.txt"
    sentinel.write_text("keep", encoding="utf-8")

    with pytest.raises(PocBackendError, match="unsafe_poc_root"):
        RealPocBackend(
            PocRequest(profile="cc-wally", workspace=str(tmp_path), poc_root=str(scratch)),
            env={},
        )

    assert sentinel.read_text(encoding="utf-8") == "keep"


def test_poc_script_rejects_existing_scratch_root(tmp_path: Path) -> None:
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    sentinel = scratch / "sentinel.txt"
    sentinel.write_text("keep", encoding="utf-8")
    script = REPO_ROOT / "factory" / "scripts" / "isolation_poc.sh"

    completed = subprocess.run(
        ["bash", str(script), "--workspace", str(tmp_path), "--poc-root", str(scratch)],
        cwd=tmp_path,
        env={**os.environ, "KVM_AVAILABLE": "1"},
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert completed.returncode == 1
    assert "--poc-root must be a new absolute directory" in completed.stderr
    assert sentinel.read_text(encoding="utf-8") == "keep"


def test_real_cleanup_after_workspace_refusal_preserves_cwd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    sentinel = repo / "sentinel.txt"
    sentinel.write_text("keep", encoding="utf-8")
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    monkeypatch.chdir(repo)
    monkeypatch.setattr(
        sys.modules[__name__], "get_profile_config", lambda _name: {"backend": "cc-backend"}
    )

    def refuse_workspace(*_args: Any, **_kwargs: Any) -> None:
        raise IsolationError("symlink_escape", "blocked")

    monkeypatch.setattr(sys.modules[__name__], "resolve_workspace_export", refuse_workspace)
    backend = RealPocBackend(
        PocRequest(profile="cc-wally", workspace=str(repo), poc_root=str(scratch)),
        env={},
    )

    ok, _lines, report = run_harness(backend)

    assert ok is False
    assert report.failed[0].name == ASSERT_PREPARE
    assert sentinel.read_text(encoding="utf-8") == "keep"
    assert not scratch.exists()


def test_real_cleanup_removes_only_its_initialized_control_dir(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    sentinel = workspace / "sentinel.txt"
    sentinel.write_text("keep", encoding="utf-8")
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    monkeypatch.chdir(workspace)
    backend = RealPocBackend(
        PocRequest(profile="cc-wally", workspace=str(workspace), poc_root=str(scratch)),
        env={},
    )
    backend._workspace_host = workspace
    backend._control_dir = workspace / POC_DIR_NAME / backend._token
    backend._control_dir.mkdir(parents=True)
    (backend._control_dir / "control.txt").write_text("remove", encoding="utf-8")

    receipt = backend.cleanup()

    assert receipt.clean
    assert sentinel.read_text(encoding="utf-8") == "keep"
    assert not backend._control_dir.exists()
    assert not scratch.exists()


def test_harness_fails_the_stop_assertion_when_the_call_never_started() -> None:
    # Given: a stop probe that never observed the in-flight call
    backend = FakeBackend(overrides={"stop_session": lambda: good_stop(started=False)})
    # When: the harness runs
    ok, _lines, report = run_harness(backend)
    # Then: the stop assertion fails and the run fails
    assert ok is False
    assert [item.name for item in report.failed] == [ASSERT_STOP]


def test_verdict_session_requires_the_in_guest_bwrap_probe_marker() -> None:
    # Given: a connected session whose guest never wrote the probe marker
    evidence = replace(good_session(), bwrap_probe="")
    # When: the session verdict runs
    ok, detail = verdict_session(evidence)
    # Then: the missing readiness proof fails the step
    assert ok is False
    assert PROBE_OK_TEXT in detail


def test_verdict_session_rejects_a_failed_probe_marker() -> None:
    # Given: a guest where the hardened bwrap probe failed closed
    evidence = replace(
        good_session(), bwrap_probe=f"{SANDBOX_UNAVAILABLE_PREFIX}: namespace_blocked\n"
    )
    # When: the session verdict runs
    ok, detail = verdict_session(evidence)
    # Then: the failed probe is reported, never silently passed
    assert ok is False
    assert SANDBOX_UNAVAILABLE_PREFIX in detail


def test_verdict_home_accepts_a_clean_boundary_evidence() -> None:
    # Given/When/Then: the deterministic reads and the live report all hold
    ok, detail = verdict_home(good_home())
    assert ok is True and "File not found" in detail


def test_verdict_home_fails_when_the_host_tmp_canary_is_readable() -> None:
    # Given: the guest could read the host /tmp canary (the host FS is shared)
    evidence = good_home(tmp_canary_result="POC-CANARY-CONTENT-1\n")
    # When/Then: the assertion fails
    ok, detail = verdict_home(evidence)
    assert ok is False and "readable" in detail


def test_verdict_home_fails_without_the_positive_control() -> None:
    # Given: the shared control file was not readable either
    evidence = good_home(control_result="File not found: /workspace/...")
    # When/Then: the probe is rejected as unproven rather than passed
    ok, detail = verdict_home(evidence)
    assert ok is False and "positive control" in detail


def test_verdict_home_fails_when_the_live_report_sees_the_host_canary() -> None:
    # Given: the live probe found the host $HOME canary inside the guest
    evidence = good_home(report=good_home_report(home="present"))
    # When/Then: the assertion fails
    ok, detail = verdict_home(evidence)
    assert ok is False and "visible" in detail


def test_verdict_dbus_fails_when_the_host_marker_is_visible_in_the_guest() -> None:
    # Given: the host marker unit shows up in the guest's unit list
    evidence = good_dbus(units="1")
    # When/Then: the escape is not closed
    ok, detail = verdict_dbus(evidence)
    assert ok is False and "visible inside the guest" in detail


def test_verdict_dbus_accepts_a_host_without_a_user_bus() -> None:
    # Given: the host has no user bus socket at all
    evidence = good_dbus(host_bus_inode=None)
    # When/Then: there is nothing to reach, so the assertion holds
    ok, detail = verdict_dbus(evidence)
    assert ok is True and "structurally unreachable" in detail


def test_verdict_processes_fails_when_the_probe_markers_were_missing() -> None:
    # Given: the live probe could not read its marker files
    evidence = ProcessEvidence(
        report=good_procs_report(marker_missing=True),
        host_proc_marker="igorek-poc-host-proc-1",
        guest_proc_marker="igorek-poc-guest-proc-1",
    )
    # When/Then: the scan is rejected instead of matching every process
    ok, detail = verdict_processes(evidence)
    assert ok is False and "missing" in detail


def test_verdict_processes_requires_the_in_guest_positive_control() -> None:
    # Given: the guest's own marker process was not seen (the probe is blind)
    evidence = good_procs(guest_matches="0")
    # When/Then: the assertion fails instead of passing vacuously
    ok, detail = verdict_processes(evidence)
    assert ok is False and "positive control" in detail


def test_verdict_processes_fails_when_a_host_process_is_visible() -> None:
    # Given: the host marker process shows up inside the guest
    evidence = good_procs(host_matches="1")
    # When/Then: the assertion fails
    ok, detail = verdict_processes(evidence)
    assert ok is False and "visible inside the guest" in detail


def test_verdict_approval_requires_a_canonicalized_target() -> None:
    # Given: the blocked target was left as a guest path
    evidence = good_approval(target="/workspace/poc_write_1.txt")
    # When/Then: the canonicalization boundary is not proven
    ok, detail = verdict_approval(evidence)
    assert ok is False and "canonicalized" in detail


def test_verdict_approval_fails_when_the_parked_vm_kept_running() -> None:
    # Given: the parked session VM was not torn down
    evidence = good_approval(vm_stopped=False)
    # When/Then: the assertion fails
    ok, detail = verdict_approval(evidence)
    assert ok is False and "left running" in detail


def test_verdict_stop_fails_when_teardown_is_incomplete() -> None:
    # Given: the stop returned but the overlay survived
    evidence = good_stop(overlay_gone=False)
    # When/Then: the assertion fails
    ok, detail = verdict_stop(evidence)
    assert ok is False and "left state behind" in detail


def test_verdict_teardown_reports_the_exact_missing_fact() -> None:
    # Given: the overlay dir still holds a qcow2 file
    evidence = TeardownEvidence(domain_gone=True, overlay_gone=True, overlay_dir_empty=False)
    # When/Then: the failure names the fact that is wrong
    ok, detail = verdict_teardown(evidence)
    assert ok is False and "overlay_dir_empty=False" in detail


def test_main_prints_skip_without_touching_a_backend() -> None:
    # Given: an environment without the KVM opt-in
    def forbidden_factory(*_args: Any, **_kwargs: Any) -> PocBackend:
        raise AssertionError("the backend must not be built on the skip path")

    lines: list[str] = []
    # When: the CLI runs
    code = main(
        ["--real", "--profile", "cc-wally", "--workspace", "/tmp"],
        env={},
        emit=lines.append,
        backend_factory=forbidden_factory,
    )
    # Then: it prints exactly one SKIP line and exits with the skip code
    assert code == EXIT_SKIP
    assert len(lines) == 1 and lines[0].startswith("SKIP: no KVM")


def test_main_runs_the_harness_and_reports_pass() -> None:
    # Given: a passing fake harness
    lines: list[str] = []
    code = main(
        ["--real", "--profile", "cc-wally", "--workspace", "/tmp"],
        env={"KVM_AVAILABLE": "1"},
        emit=lines.append,
        availability=lambda _env: None,
        backend_factory=lambda *_args, **_kwargs: FakeBackend(),
    )
    # When/Then: every assertion prints and the CLI exits 0
    assert code == EXIT_OK
    assert lines[-1] == "RESULT: PASS"
    assert sum(1 for line in lines if line.startswith("PASS: ")) == 10


def test_main_reports_fail_when_the_harness_fails() -> None:
    # Given: a failing fake harness
    lines: list[str] = []
    code = main(
        ["--real", "--profile", "cc-wally", "--workspace", "/tmp"],
        env={"KVM_AVAILABLE": "1"},
        emit=lines.append,
        availability=lambda _env: None,
        backend_factory=lambda *_args, **_kwargs: FakeBackend(raise_at="prepare"),
    )
    # When/Then: the CLI exits 1 and the result is FAIL
    assert code == EXIT_FAIL
    assert lines[-1] == "RESULT: FAIL"


def test_main_probe_only_reports_readiness() -> None:
    # Given: the KVM probe passes
    lines: list[str] = []
    code = main(
        ["--probe-only"],
        env={"KVM_AVAILABLE": "1"},
        emit=lines.append,
        availability=lambda _env: None,
    )
    # When/Then: no VM is touched and the probe verdict is printed
    assert code == EXIT_OK
    assert lines == ["READY: KVM probe passed"]


def test_main_requires_the_real_flag() -> None:
    # Given: a ready host but no --real
    lines: list[str] = []
    code = main(
        ["--profile", "cc-wally"],
        env={"KVM_AVAILABLE": "1"},
        emit=lines.append,
        availability=lambda _env: None,
    )
    # When/Then: the PoC refuses to run by accident
    assert code == EXIT_FAIL
    assert any(line.startswith("FAIL: pass --real") for line in lines)


def _poc_context(*, provider_endpoint: str = "closed") -> PocTaskContext:
    return PocTaskContext(
        profile="cc-wally",
        profile_config={"model": "glm-5.3-flash", "api_format": "openai"},
        session_id="poc-1",
        provider_endpoint=provider_endpoint,
        artifact_workdir="/tmp/igorek-poc-artifacts",
    )


def _probe_payloads() -> list[tuple[str, dict[str, Any]]]:
    return [
        (
            "live_probe_message",
            build_poc_task_payload(
                _poc_context(provider_endpoint="profile"),
                task_id="t",
                user_message=live_probe_user_message("/workspace/.igorek-isolation-poc/t"),
            ),
        ),
        (
            "forced_read",
            build_poc_task_payload(
                _poc_context(),
                task_id="t",
                user_message="read probe",
                forced=forced_tool_call(
                    "Read",
                    {"file_path": "/tmp/igorek-poc-tmp-canary-1.txt"},
                    tool_use_id="forced_poc_tmp_canary",
                ),
                filesystem_mode="full-dangerous",
            ),
        ),
        (
            "forced_write",
            build_poc_task_payload(
                _poc_context(),
                task_id="t",
                user_message="write probe",
                forced=forced_tool_call(
                    "Write",
                    {"file_path": "/workspace/poc_write.txt", "content": "x"},
                    tool_use_id="forced_poc_write",
                ),
            ),
        ),
    ]


@pytest.mark.parametrize("name,payload", _probe_payloads(), ids=lambda value: str(value)[:24])
def _live_script() -> str:
    return live_probe_script(
        guest_control="/workspace/.igorek-isolation-poc/t",
        host_marker_unit="igorek-poc-host-1",
        guest_probe_unit="poc-guest-1",
        canary_name="home-canary.txt",
        ssh_canary_name="ssh-canary.txt",
        canary_content="POC-CANARY-CONTENT-1",
    )


def test_live_probe_script_emits_every_marker_the_verdicts_read() -> None:
    # Given: the generated live probe script
    script = _live_script()
    # When/Then: every marker a verdict parses is actually emitted by the script
    for marker in (
        "POC_CONTROL_READ",
        "POC_HOME_CANARY",
        "POC_HOME_CANARY_READABLE",
        "POC_SSH_CANARY",
        "POC_SSH_CANARY_READABLE",
        "POC_SYSTEMCTL_RC",
        "POC_BUSCTL_RC",
        "POC_HOST_MARKER_IN_UNITS",
        "POC_HOST_MARKER_IN_BUS",
        "POC_HOST_MARKER_IN_CAT",
        "POC_GUEST_BUS_INODE",
        "POC_HOST_PROC_MATCHES",
        "POC_GUEST_PROC_MATCHES",
        "POC_MARKER_MISSING",
    ):
        assert f"{marker}=" in script
    for probe in ("POC_PROBE=home", "POC_PROBE=dbus", "POC_PROBE=procs"):
        assert probe in script


def test_live_probe_script_publishes_complete_reports() -> None:
    script = _live_script()
    for name in ("home", "dbus", "procs"):
        assert f'}} > "$D/.probe_{name}.txt.tmp" 2>&1' in script
        assert f'mv "$D/.probe_{name}.txt.tmp" "$D/probe_{name}.txt"' in script


def test_live_probe_script_is_valid_posix_shell(tmp_path: Path) -> None:
    # Given: the generated live probe script on disk
    path = tmp_path / "poc_probe.sh"
    path.write_text(_live_script(), encoding="utf-8")
    # When/Then: a POSIX shell parses it without errors
    completed = run_bounded(["sh", "-n", str(path)], timeout_s=30.0)
    assert completed.returncode == 0, completed.stderr


@pytest.mark.parametrize("name,payload", _probe_payloads(), ids=lambda value: str(value)[:24])
def test_probe_payloads_pass_the_product_channel_policy(name: str, payload: dict[str, Any]) -> None:
    # Given: the harness-pinned probe payloads (no shell command can cross the wire)
    assert name
    # When/Then: the product's own payload policy accepts the task frame
    message = parse_message(
        {"kind": "task", "seq": 1, "payload": payload}, ChannelConfig(port=43111)
    )
    assert message.kind is MessageKind.TASK


def test_forced_bash_command_is_refused_by_the_channel() -> None:
    # Given: a forced Bash call (the shape the approval relay cannot pin over the wire)
    payload = build_poc_task_payload(
        _poc_context(),
        task_id="t",
        user_message="probe",
        forced=forced_tool_call("Bash", {"command": "true"}, tool_use_id="x"),
    )
    # When/Then: the channel denies the shell key, which is why the live probes
    # are model-driven through the guest instead of pinned over the wire
    with pytest.raises(ChannelError) as refusal:
        parse_message(
            {"kind": "task", "seq": 1, "payload": payload}, ChannelConfig(port=43111)
        )
    assert refusal.value.reason == "payload_shell_key_denied"


def test_task_payload_with_a_leading_host_path_is_refused() -> None:
    # Given: a forced call that names a host path in the payload
    payload = build_poc_task_payload(
        _poc_context(),
        task_id="t",
        user_message="probe",
        forced=forced_tool_call(
            "Write", {"file_path": "/home/example/project/poc.txt"}, tool_use_id="x"
        ),
    )
    # When/Then: the channel refuses it instead of leaking a host path
    with pytest.raises(ChannelError) as refusal:
        parse_message(
            {"kind": "task", "seq": 1, "payload": payload}, ChannelConfig(port=43111)
        )
    assert refusal.value.reason == "payload_host_path_denied"


def test_closed_endpoint_is_pinned_and_profile_mode_keeps_the_profile_url() -> None:
    # Given: both endpoint modes
    closed = build_poc_task_payload(_poc_context(), task_id="t", user_message="m")
    live = build_poc_task_payload(
        _poc_context(provider_endpoint="profile"), task_id="t", user_message="m"
    )
    # When/Then: closed pins the loopback port, profile keeps the profile's URL
    assert closed["base_url"] == CLOSED_PROVIDER_URL
    assert live["base_url"] == ""


def test_poc_script_exists_and_parses() -> None:
    # Given: the runnable entry point
    script = REPO_ROOT / "factory" / "scripts" / "isolation_poc.sh"
    # When/Then: it exists, is executable and is syntactically valid bash
    assert script.is_file() and os.access(script, os.X_OK)
    completed = run_bounded(["bash", "-n", str(script)], timeout_s=30.0)
    assert completed.returncode == 0, completed.stderr


def test_real_poc_run_is_guarded_by_the_kvm_probe(tmp_path: Path) -> None:
    # Given: the real PoC entry point
    reason = probe_availability(os.environ)
    if reason is not None:
        pytest.skip(f"SKIP: no KVM ({reason})")
    # When: the host is a real KVM host with the opt-in set
    code = main(
        [
            "--real",
            "--profile",
            os.environ.get("IGOREK_ISOLATION_POC_PROFILE", DEFAULT_PROFILE),
            "--workspace",
            str(tmp_path),
        ],
        env=os.environ,
    )
    # Then: every assertion must have passed for real
    assert code == EXIT_OK


if __name__ == "__main__":
    _install_sigterm_cleanup()
    sys.exit(main(sys.argv[1:]))


def test_cleanup_resets_only_its_own_host_marker_unit(monkeypatch):
    backend = object.__new__(RealPocBackend)
    backend._marker_unit_created = True
    backend._host_marker_unit = "igorek-poc-fixture"
    commands = []

    def run(argv, **_kwargs):
        commands.append(argv)
        return subprocess.CompletedProcess(argv, 1 if "cat" in argv else 0, "", "")

    monkeypatch.setattr(sys.modules[__name__], "run_bounded", run)
    receipt = CleanupReceipt()
    backend._reset_host_marker_unit(receipt)
    assert commands == [
        ["systemctl", "--user", "stop", "igorek-poc-fixture.service"],
        ["systemctl", "--user", "reset-failed", "igorek-poc-fixture.service"],
        ["systemctl", "--user", "cat", "igorek-poc-fixture.service"],
    ]
    assert not backend._marker_unit_created
    assert not receipt.leftovers
