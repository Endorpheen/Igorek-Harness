"""Max-feasible-concurrent-VM measurement for the host capacity report (todo 33).

The admission budget must be grounded in what the host can actually run. This
module measures the number of session VMs the current host can boot
concurrently, or records an honest SKIP with a structural reason when it cannot
measure (no KVM, libvirt/virtiofsd/qemu-img missing, no base image, no explicit
``KVM_AVAILABLE=1`` opt-in, unknown host RAM/disk, or no room for a single VM).

Honesty rules (mirroring the todo-27 bench harness):

* nothing is booted and no number is printed unless the host is structurally
  ready *and* the operator opted in with ``KVM_AVAILABLE=1``;
* a skipped verdict carries ``max_vms: null`` - never an estimated or fabricated
  count;
* the ceiling is bounded by what the host physically has (RAM/disk headroom for
  the configured per-VM footprint) and by ``--max-vms``, so the probe cannot
  overshoot the machine on purpose;
* every started VM is torn down before the artifact is written, and the artifact
  records the exact commands/API calls that produced it.

Run it on the target KVM host to obtain the real number:

    KVM_AVAILABLE=1 ./.venv/bin/python -m factory.core.isolation.capacity_probe \
        --out-json .omo/evidence/isolation-capacity-probe-mini-pc.json \
        --out-summary .omo/evidence/isolation-capacity-probe-mini-pc.txt

# allow: SIZE_OK - one honest measurement contract: the readiness gate, the
# physical ceiling, the boot loop and the artifact rendering must not drift
# apart, or a skipped verdict could start carrying a fabricated number.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import platform
import shutil
import sys
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import date
from pathlib import Path
from typing import Any, Final, Protocol

from .config import HostCapabilities, IsolationConfig, check_host_capabilities
from .errors import IsolationError
from .limits import GIB_BYTES, memory_hard_limit_mib
from .manager import IsolationManager, SessionSpec

logger = logging.getLogger(__name__)

__all__ = [
    "KVM_AVAILABLE_ENV",
    "PROBE_INTERFACE",
    "REASON_BASE_IMAGE_MISSING",
    "REASON_HOST_CAPACITY_UNKNOWN",
    "REASON_MEASURED",
    "REASON_NO_CAPACITY",
    "REASON_OPTIN_REQUIRED",
    "REASON_REACHED_CEILING",
    "CapacityProbeResult",
    "ProbeAttempt",
    "default_host_disk_gib",
    "default_host_memory_mib",
    "machine_label",
    "physical_ceiling",
    "probe_readiness",
    "run_probe",
    "write_probe_artifacts",
]

KVM_AVAILABLE_ENV: Final[str] = "KVM_AVAILABLE"
PROBE_INTERFACE: Final[str] = "isolation-capacity"
PROBE_SESSION_PREFIX: Final[str] = "capacity-probe-"
REASON_OPTIN_REQUIRED: Final[str] = "kvm_available_optin_required"
REASON_MEASURED: Final[str] = "measured"
REASON_REACHED_CEILING: Final[str] = "reached_ceiling"
REASON_HOST_CAPACITY_UNKNOWN: Final[str] = "host_capacity_unknown"
REASON_BASE_IMAGE_MISSING: Final[str] = "base_image_missing"
REASON_NO_CAPACITY: Final[str] = "no_capacity_for_one_vm"
REASON_CAPABILITY: Final[str] = "capability_unavailable"
BASE_IMAGE_ENV: Final[str] = "IGOREK_ISOLATION_BASE_IMAGE"
_MEMINFO_PATH: Final[Path] = Path("/proc/meminfo")
_TRUTHY: Final[frozenset[str]] = frozenset({"1", "true", "yes", "on"})


class ProbeManager(Protocol):
    """The manager surface the measurement drives (``IsolationManager`` in prod)."""

    def ensure_vm(self, session: SessionSpec) -> object: ...
    def stop(self, session_id: str) -> None: ...
    def close(self) -> None: ...


ProbeManagerFactory = Callable[[IsolationConfig], ProbeManager]


@dataclass(frozen=True, slots=True)
class ProbeAttempt:
    """One ``ensure_vm`` attempt of the incremental measurement."""

    index: int
    session_id: str
    started: bool
    reason: str
    detail: str

    def to_record(self) -> dict[str, Any]:
        return {
            "index": self.index,
            "session_id": self.session_id,
            "started": self.started,
            "reason": self.reason,
            "detail": self.detail,
        }


@dataclass(frozen=True, slots=True)
class CapacityProbeResult:
    """The measurement verdict: a number, a ceiling, or an honest SKIP."""

    hostname: str
    machine: str
    ready: bool
    reason: str
    max_vms: int | None
    ceiling: int
    attempts: tuple[ProbeAttempt, ...]
    commands: tuple[str, ...]
    kvm_available_optin: bool

    @property
    def skipped(self) -> bool:
        return self.max_vms is None

    def to_artifact(self) -> dict[str, Any]:
        return {
            "probe": PROBE_INTERFACE,
            "date": date.today().isoformat(),
            "hostname": self.hostname,
            "machine": self.machine,
            "ready": self.ready,
            "skipped": self.skipped,
            "reason": self.reason,
            "kvm_available_optin": self.kvm_available_optin,
            "ceiling": self.ceiling,
            "max_vms": self.max_vms,
            "attempts": [attempt.to_record() for attempt in self.attempts],
            "commands": list(self.commands),
        }

    def render(self) -> str:
        state = (
            f"SKIP: {self.reason}"
            if self.skipped
            else f"MEASURED: max_vms={self.max_vms} (ceiling={self.ceiling}, {self.reason})"
        )
        lines = [
            f"{PROBE_INTERFACE} probe",
            f"host: {self.hostname} ({self.machine})",
            f"state: {state}",
            f"kvm_available_optin: {self.kvm_available_optin}",
        ]
        for attempt in self.attempts:
            outcome = "started" if attempt.started else f"refused:{attempt.reason}"
            lines.append(f"attempt {attempt.index}: {attempt.session_id} {outcome}")
        if self.commands:
            lines.append("commands:")
            lines.extend(f"  {command}" for command in self.commands)
        return "\n".join(lines)


def machine_label(hostname: str | None = None) -> str:
    """Use an explicit deployment label, otherwise the current host name."""
    name = os.environ.get("IGOREK_MACHINE_LABEL", "").strip()
    return name or (platform.node() if hostname is None else hostname).strip() or "unknown-host"


def default_host_memory_mib() -> int:
    """Total physical RAM in MiB, or 0 when the host does not report it."""
    try:
        for line in _MEMINFO_PATH.read_text(encoding="utf-8").splitlines():
            if line.startswith("MemTotal:"):
                parts = line.split()
                return int(parts[1]) // 1024
    except (OSError, ValueError, IndexError):
        logger.warning("isolation capacity probe: cannot read %s", _MEMINFO_PATH)
    return 0


def default_host_disk_gib(path: str | os.PathLike[str]) -> int:
    """Free space in GiB on the filesystem holding ``path`` (0 when unknown)."""
    candidate = Path(path)
    while not candidate.exists() and candidate != candidate.parent:
        candidate = candidate.parent
    try:
        return int(shutil.disk_usage(candidate).free // GIB_BYTES)
    except OSError:
        logger.warning("isolation capacity probe: cannot stat %s", candidate)
        return 0


def physical_ceiling(
    *,
    memory_mib: int,
    disk_gib: int,
    per_vm_memory_mib: int,
    per_vm_disk_gib: int,
) -> int:
    """How many VMs of this footprint the host physically fits (never negative)."""
    if per_vm_memory_mib < 1 or per_vm_disk_gib < 1:
        return 0
    return max(0, min(memory_mib // per_vm_memory_mib, disk_gib // per_vm_disk_gib))


def probe_readiness(
    env: Mapping[str, str] | None = None,
    *,
    capabilities: HostCapabilities | None = None,
) -> tuple[bool, str]:
    """Structural readiness of this host for a real measurement."""
    source = os.environ if env is None else env
    caps = capabilities if capabilities is not None else check_host_capabilities()
    if not caps.ok:
        return False, caps.reason or REASON_CAPABILITY
    image = _base_image(source)
    if image is None or not image.is_file():
        return False, REASON_BASE_IMAGE_MISSING
    return True, ""


def run_probe(
    *,
    env: Mapping[str, str] | None = None,
    capabilities: HostCapabilities | None = None,
    max_vms: int = 0,
    workspace: str | None = None,
    manager_factory: ProbeManagerFactory | None = None,
    host_memory_mib: Callable[[], int] | None = None,
    host_disk_gib: Callable[[], int] | None = None,
) -> CapacityProbeResult:
    """Measure the max feasible concurrent VMs, or record why it cannot be done."""
    source = os.environ if env is None else env
    hostname = platform.node()
    machine = machine_label(hostname)
    optin = _truthy(source.get(KVM_AVAILABLE_ENV))
    setup_commands = _setup_commands(max_vms)
    ready, reason = probe_readiness(source, capabilities=capabilities)
    if not ready:
        return _skipped(
            hostname=hostname,
            machine=machine,
            reason=reason,
            ready=False,
            optin=optin,
            commands=setup_commands,
        )
    if not optin:
        return _skipped(
            hostname=hostname,
            machine=machine,
            reason=REASON_OPTIN_REQUIRED,
            ready=True,
            optin=optin,
            commands=setup_commands,
        )
    config = IsolationConfig.from_env(
        source,
        capability_probe=lambda _uri: (
            capabilities if capabilities is not None else check_host_capabilities()
        ),
    )
    if not config.enabled:
        return _skipped(
            hostname=hostname,
            machine=machine,
            reason=f"config_refused:{config.reason}",
            ready=False,
            optin=optin,
            commands=setup_commands,
        )
    per_vm_memory = memory_hard_limit_mib(config.session_memory_mb)
    memory_fn = host_memory_mib if host_memory_mib is not None else default_host_memory_mib
    disk_fn = host_disk_gib if host_disk_gib is not None else _disk_probe(config)
    ceiling = physical_ceiling(
        memory_mib=memory_fn(),
        disk_gib=disk_fn(),
        per_vm_memory_mib=per_vm_memory,
        per_vm_disk_gib=config.session_disk_gb,
    )
    if max_vms > 0:
        ceiling = min(ceiling, max_vms)
    if ceiling < 1:
        return _skipped(
            hostname=hostname,
            machine=machine,
            reason=REASON_HOST_CAPACITY_UNKNOWN
            if memory_fn() < 1 or disk_fn() < 1
            else REASON_NO_CAPACITY,
            ready=True,
            optin=optin,
            commands=setup_commands,
        )
    workspace_path = Path(workspace).expanduser() if workspace else Path.cwd()
    probe_config = replace(config, max_vms=ceiling, host_memory_mb=0, host_disk_gb=0)
    manager = (
        manager_factory(probe_config)
        if manager_factory is not None
        else IsolationManager(probe_config)
    )
    attempts: list[ProbeAttempt] = []
    started: list[str] = []
    try:
        for index in range(1, ceiling + 1):
            session = SessionSpec(
                session_id=f"{PROBE_SESSION_PREFIX}{index:02d}",
                workspace_cwd=str(workspace_path),
            )
            try:
                manager.ensure_vm(session)
            except IsolationError as exc:
                attempts.append(
                    ProbeAttempt(index, session.session_id, False, exc.code, exc.detail)
                )
                break
            started.append(session.session_id)
            attempts.append(ProbeAttempt(index, session.session_id, True, "", ""))
    finally:
        for session_id in started:
            manager.stop(session_id)
        manager.close()
    reached = len(started) == ceiling and bool(attempts) and attempts[-1].started
    return CapacityProbeResult(
        hostname=hostname,
        machine=machine,
        ready=True,
        reason=REASON_REACHED_CEILING if reached else REASON_MEASURED,
        max_vms=len(started),
        ceiling=ceiling,
        attempts=tuple(attempts),
        commands=(
            *setup_commands,
            (
                f"ensure_vm(SessionSpec(session_id='{PROBE_SESSION_PREFIX}NN', "
                f"workspace_cwd='{workspace_path}')) x{ceiling} then stop() each"
            ),
        ),
        kvm_available_optin=optin,
    )


def write_probe_artifacts(
    result: CapacityProbeResult,
    *,
    out_json: str | os.PathLike[str],
    out_summary: str | os.PathLike[str],
) -> None:
    """Write the raw JSON artifact and the human summary."""
    json_path = Path(out_json)
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(
        json.dumps(result.to_artifact(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    summary_path = Path(out_summary)
    summary_path.parent.mkdir(parents=True, exist_ok=True)
    summary_path.write_text(result.render() + "\n", encoding="utf-8")


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry: 0 when measured, 2 on an honest SKIP, 1 on an error."""
    parser = argparse.ArgumentParser(
        prog="isolation-capacity-probe",
        description="Measure the max feasible concurrent session VMs on this host.",
    )
    parser.add_argument(
        "--max-vms",
        type=int,
        default=0,
        help="hard upper bound for the measurement (0 = physical ceiling)",
    )
    parser.add_argument("--workspace", default=None, help="existing project workspace")
    parser.add_argument(
        "--out-json",
        default=f".omo/evidence/isolation-capacity-probe-{date.today().isoformat()}.json",
    )
    parser.add_argument(
        "--out-summary",
        default=f".omo/evidence/isolation-capacity-probe-{date.today().isoformat()}.txt",
    )
    parser.add_argument(
        "--probe-only",
        action="store_true",
        help="print the structural readiness verdict and exit",
    )
    args = parser.parse_args(argv)
    if args.probe_only:
        ready, reason = probe_readiness()
        if ready:
            print(f"READY: {platform.node()} ({machine_label()})")
            return 0
        print(f"SKIP: {reason}")
        return 2
    try:
        result = run_probe(
            max_vms=max(0, int(args.max_vms)),
            workspace=args.workspace,
        )
    except IsolationError as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1
    write_probe_artifacts(result, out_json=args.out_json, out_summary=args.out_summary)
    print(result.render())
    print(f"artifact: {args.out_json}")
    print(f"summary: {args.out_summary}")
    return 2 if result.skipped else 0


def _skipped(
    *,
    hostname: str,
    machine: str,
    reason: str,
    ready: bool,
    optin: bool,
    commands: tuple[str, ...],
) -> CapacityProbeResult:
    return CapacityProbeResult(
        hostname=hostname,
        machine=machine,
        ready=ready,
        reason=reason,
        max_vms=None,
        ceiling=0,
        attempts=(),
        commands=commands,
        kvm_available_optin=optin,
    )


def _setup_commands(max_vms: int) -> tuple[str, ...]:
    bound = str(max_vms) if max_vms > 0 else "auto"
    return (
        "test -e /dev/kvm",
        f"python -m factory.core.isolation.capacity_probe --max-vms {bound}",
    )


def _base_image(source: Mapping[str, str]) -> Path | None:
    raw = source.get(BASE_IMAGE_ENV)
    text = str(raw).strip() if raw is not None else ""
    return Path(text).expanduser() if text else None


def _disk_probe(config: IsolationConfig) -> Callable[[], int]:
    overlay_dir = config.overlay_dir

    def probe() -> int:
        return default_host_disk_gib(overlay_dir)

    return probe


def _truthy(value: object) -> bool:
    return str(value or "").strip().lower() in _TRUTHY


if __name__ == "__main__":
    raise SystemExit(main())
