#!/usr/bin/env python3
"""Performance measurement harness for the per-session KVM isolation path (plan todo 27).

Measures the eight metrics the plan's success criteria name - cold start, warm
start, RAM overhead, CPU overhead, disk overhead, command latency, local-inference
impact and workspace attach time - twice per invocation (so the artifact shows
variance), and writes a raw JSON artifact plus a human summary. Every metric
carries the exact command that produced it and the environment it ran in; the
methodology follows credativ FrOSCon2026 (the kernel dominates boot size, so the
base image is pinned in the environment) and the sandbox-bench caveat that
VMM-internal timings are not end-to-end timings (every value here is measured
from outside the VMM).

Metric definitions (all measured on the host, never inside the guest):

* ``cold_start`` - wall time from ``IsolationManager.ensure_vm`` for a brand-new
  session to the guest runner completing the vsock channel handshake;
* ``warm_start`` - wall time to obtain the live VM handle and guest endpoint for
  a session whose VM is already running (no boot). A stopped VM is a cold start
  until the teardown policy (todo 26) lands, which the artifact notes say;
* ``ram_overhead`` - resident set size (MiB) of the session's QEMU process;
* ``cpu_overhead`` - CPU-time delta of the QEMU process over an idle window, as
  percent of one host core;
* ``disk_overhead`` - physical size (MiB) of the session qcow2 overlay;
* ``command_latency`` - median wall time (ms) of pinned guest ``Read`` calls
  through the vsock channel (no inference in the loop);
* ``local_inference_impact`` - percent latency change of host-local inference
  without vs with the running VM (skipped, with a reason, when no host-local
  endpoint is configured or reachable);
* ``workspace_attach_time`` - wall time from domain start to the guest observing
  the exported workspace via a pinned ``Read`` of the workspace canary.

Honesty rules (structural, never wording):

* the structural probe is the gate: no ``/dev/kvm`` (or no ``AF_VSOCK``) means
  ``SKIP: no KVM (<reason>)``, exit code 2 (the shell maps it to 0) and no
  artifact file is written - there is no simulated VM and no fabricated number;
* a measured outcome cannot exist without a finite value, an exact command and a
  recorded environment; a skipped outcome cannot carry a value;
* one metric measured in one run but skipped in another refuses to aggregate;
* a failed or timed-out measurement fails the whole run and no artifact is
  written (no partial/misleading success output).

Run: ``factory/scripts/isolation_bench.sh`` (or
``python tests/test_isolation_bench.py --real --out <json> --summary <txt>``).
The pure harness logic is unit-tested with fakes below; the real backend is only
constructed behind ``--real`` on a KVM host.

# allow: SIZE_OK - the harness is one artifact: the unit-tested pure logic (probe,
# outcome validation, aggregation, artifact) and the guarded real measurement
# backend implement one checklist; splitting them would separate each metric from
# the contract that keeps it honest.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import platform
import shlex
import shutil
import socket
import statistics
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final, Protocol

REPO_ROOT = Path(
    os.environ.get("IGOREK_FACTORY_REPO", Path(__file__).resolve().parents[1])
)
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from factory.core.cc_backend_trace import TRACE_FILENAME  # noqa: E402
from factory.core.isolation.base_image import default_image_path  # noqa: E402
from factory.core.isolation.channel import SessionId  # noqa: E402
from factory.core.isolation.config import (  # noqa: E402
    DEFAULT_VSOCK_PORT,
    ENV_BASE_IMAGE,
    ENV_MODE,
    ENV_OVERLAY_DIR,
    ENV_VSOCK_PORT,
    MODE_KVM,
    IsolationConfig,
)
from factory.core.isolation.domain import domain_name  # noqa: E402
from factory.core.isolation.errors import IsolationError  # noqa: E402
from factory.core.isolation.manager import (  # noqa: E402
    IsolationManager,
    SessionSpec,
    SessionVm,
)
from factory.core.isolation.workspace import (  # noqa: E402
    GUEST_WORKSPACE_PATH,
    WorkspacePolicy,
    resolve_workspace_export,
)
from factory.core.runtime import get_profile_config  # noqa: E402
from factory.core.trace_taxonomy import CcTraceEvent  # noqa: E402

# --------------------------------------------------------------------------- #
# Vocabulary
# --------------------------------------------------------------------------- #

EXIT_OK: Final = 0
EXIT_FAIL: Final = 1
EXIT_SKIP: Final = 2

KVM_AVAILABLE_ENV: Final = "KVM_AVAILABLE"
KVM_AVAILABLE_TRUE: Final = frozenset({"1", "true", "yes", "on", "kvm"})
KVM_DEVICE: Final = Path("/dev/kvm")

ENV_BENCH_INFERENCE_URL: Final = "IGOREK_ISOLATION_BENCH_INFERENCE_URL"

DEFAULT_RUNS: Final = 2
DEFAULT_PROFILE: Final = "cc-wally"
DEFAULT_BOOT_TIMEOUT_S: Final = 240.0
DEFAULT_WAIT_TIMEOUT_S: Final = 120.0
DEFAULT_IDLE_WINDOW_S: Final = 30.0
DEFAULT_LATENCY_ITERATIONS: Final = 5
DEFAULT_INFERENCE_ITERATIONS: Final = 5

BENCH_DIR_NAME: Final = ".igorek-isolation-bench"
CANARY_NAME: Final = "canary.txt"

ARTIFACT_SCHEMA_VERSION: Final = 1

METRIC_COLD_START: Final = "cold_start"
METRIC_WARM_START: Final = "warm_start"
METRIC_RAM_OVERHEAD: Final = "ram_overhead"
METRIC_CPU_OVERHEAD: Final = "cpu_overhead"
METRIC_DISK_OVERHEAD: Final = "disk_overhead"
METRIC_COMMAND_LATENCY: Final = "command_latency"
METRIC_LOCAL_INFERENCE: Final = "local_inference_impact"
METRIC_WORKSPACE_ATTACH: Final = "workspace_attach_time"

METRIC_ORDER: Final = (
    METRIC_COLD_START,
    METRIC_WARM_START,
    METRIC_RAM_OVERHEAD,
    METRIC_CPU_OVERHEAD,
    METRIC_DISK_OVERHEAD,
    METRIC_COMMAND_LATENCY,
    METRIC_LOCAL_INFERENCE,
    METRIC_WORKSPACE_ATTACH,
)

METRIC_UNITS: Final = {
    METRIC_COLD_START: "s",
    METRIC_WARM_START: "s",
    METRIC_RAM_OVERHEAD: "MiB",
    METRIC_CPU_OVERHEAD: "%",
    METRIC_DISK_OVERHEAD: "MiB",
    METRIC_COMMAND_LATENCY: "ms",
    METRIC_LOCAL_INFERENCE: "%",
    METRIC_WORKSPACE_ATTACH: "s",
}

METHODOLOGY_NOTES: Final = (
    "methodology: credativ FrOSCon2026 (base-image/kernel size dominates boot); "
    "sandbox-bench caveat: every value is measured from outside the VMM "
    "(end-to-end), never VMM-internal",
    "two consecutive passes per invocation; the artifact records both runs and "
    "the spread between them",
    "a stopped session VM is a cold start until the teardown policy (todo 26) "
    "lands; warm_start measures attaching to a live VM, not a reboot",
)


class BenchError(Exception):
    """A measurement cannot produce its evidence; ``reason`` is machine-stable."""

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
    """True only for an explicit opt-in ``KVM_AVAILABLE=1``-style value."""
    return str(env.get(KVM_AVAILABLE_ENV, "")).strip().lower() in KVM_AVAILABLE_TRUE


def vsock_unavailable_reason() -> str | None:
    """``AF_VSOCK`` must exist on the host or no guest can ever connect."""
    family = getattr(socket, "AF_VSOCK", None)
    if not isinstance(family, int):
        return "AF_VSOCK is not available on this host"
    return None


def structural_probe(
    *,
    device_exists: Callable[[], bool] | None = None,
    capability_reason: Callable[[], str | None] | None = None,
    vsock_reason: Callable[[], str | None] | None = None,
) -> str | None:
    """Real-host readiness; ``None`` means a measured run is possible."""
    exists = device_exists if device_exists is not None else lambda: KVM_DEVICE.exists()
    if not exists():
        return "no /dev/kvm on this host"
    capabilities = (
        capability_reason
        if capability_reason is not None
        else _capability_unavailable_reason
    )
    try:
        reason = capabilities()
    except Exception as exc:  # probe boundary: an unusable probe is "not ready"
        return f"capability probe failed: {exc}"
    if reason is not None:
        return reason
    return (vsock_reason if vsock_reason is not None else vsock_unavailable_reason)()


def _capability_unavailable_reason() -> str | None:
    from factory.core.isolation.domain import kvm_unavailable_reason

    return kvm_unavailable_reason()


def probe_availability(
    env: Mapping[str, str],
    *,
    structure: Callable[[], str | None] | None = None,
) -> str | None:
    """``None`` when the benchmark may run; otherwise the skip reason (never a pass).

    The structural probe is checked first so a host without ``/dev/kvm`` reports
    the real reason; only a structurally ready host additionally needs the
    explicit ``KVM_AVAILABLE=1`` opt-in that the PoC harness uses.
    """
    probe = structure if structure is not None else structural_probe
    reason = probe()
    if reason is not None:
        return reason
    if not kvm_available_requested(env):
        return f"{KVM_AVAILABLE_ENV} unset (set it to enable the real VM benchmark)"
    return None


# --------------------------------------------------------------------------- #
# Metric outcomes: a number is impossible without value + command + environment
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class MetricOutcome:
    """One metric measured (or explicitly skipped) in one run."""

    name: str
    status: str  # "measured" | "skipped"
    unit: str
    command: str
    environment: Mapping[str, str]
    value: float | None = None
    reason: str = ""
    detail: str = ""
    samples: tuple[float, ...] = ()

    def __post_init__(self) -> None:
        if self.name not in METRIC_UNITS:
            raise BenchError("unknown_metric", self.name)
        if self.status not in ("measured", "skipped"):
            raise BenchError("invalid_status", f"{self.name}: {self.status!r}")
        if self.unit != METRIC_UNITS[self.name]:
            raise BenchError(
                "metric_unit_mismatch",
                f"{self.name}: {self.unit!r} != {METRIC_UNITS[self.name]!r}",
            )
        if not str(self.command).strip():
            raise BenchError(
                "command_not_recorded", f"{self.name}: an exact command is required"
            )
        if not self.environment:
            raise BenchError(
                "environment_not_recorded", f"{self.name}: the environment is required"
            )
        if self.status == "measured":
            value = self.value
            if value is None or not math.isfinite(float(value)):
                raise BenchError(
                    "invalid_measurement", f"{self.name}: {value!r} is not a finite number"
                )
            if self.reason:
                raise BenchError(
                    "measured_with_reason", f"{self.name}: a measured outcome carries no skip reason"
                )
        else:
            if self.value is not None:
                raise BenchError(
                    "skipped_with_value", f"{self.name}: a skip must not carry a number"
                )
            if not self.reason.strip():
                raise BenchError("skip_without_reason", f"{self.name}: a skip needs a reason")


def measured(
    name: str,
    value: float | None,
    *,
    command: str,
    environment: Mapping[str, str],
    detail: str = "",
    samples: Sequence[float] = (),
) -> MetricOutcome:
    """A measured outcome; refuses anything without value/command/environment."""
    if name not in METRIC_UNITS:
        raise BenchError("unknown_metric", name)
    return MetricOutcome(
        name=name,
        status="measured",
        unit=METRIC_UNITS[name],
        command=command,
        environment=dict(environment),
        value=value,
        detail=detail,
        samples=tuple(float(item) for item in samples),
    )


def skipped(
    name: str,
    *,
    command: str,
    environment: Mapping[str, str],
    reason: str,
) -> MetricOutcome:
    """An explicitly skipped outcome: no number, a reason, a command and an environment."""
    if name not in METRIC_UNITS:
        raise BenchError("unknown_metric", name)
    return MetricOutcome(
        name=name,
        status="skipped",
        unit=METRIC_UNITS[name],
        command=command,
        environment=dict(environment),
        value=None,
        reason=reason,
    )


def render_metric_line(outcome: MetricOutcome) -> str:
    """One operator-facing line per outcome (never a number for a skip)."""
    run = outcome.environment.get("run", "?")
    if outcome.status == "measured":
        value = float(outcome.value or 0.0)
        return (
            f"MEASURE: {outcome.name} run={run} value={value:.3f}{outcome.unit} "
            f"command={outcome.command}"
        )
    return f"SKIP: {outcome.name} run={run} ({outcome.reason}) command={outcome.command}"


# --------------------------------------------------------------------------- #
# Aggregation across runs (variance) and the artifact contract
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class MetricAggregate:
    """One metric aggregated over the invocation's consecutive runs."""

    name: str
    unit: str
    status: str
    command: str
    environment: Mapping[str, str]
    runs: tuple[float, ...]
    samples: tuple[float, ...]
    minimum: float | None
    maximum: float | None
    mean: float | None
    median: float | None
    stdev: float | None
    spread_pct: float | None
    reason: str = ""


def _measured_value(outcome: MetricOutcome) -> float:
    value = outcome.value
    if value is None:
        raise BenchError("missing_value", f"{outcome.name} is measured but carries no value")
    return float(value)


def aggregate_metric(outcomes: Sequence[MetricOutcome]) -> MetricAggregate:
    """Aggregate one metric's per-run outcomes; mixed measured/skip refuses to merge."""
    if not outcomes:
        raise BenchError("no_outcomes", "an aggregate needs at least one outcome")
    names = {item.name for item in outcomes}
    if len(names) != 1:
        raise BenchError("metric_name_mismatch", f"mixed metrics: {sorted(names)}")
    name = outcomes[0].name
    units = {item.unit for item in outcomes}
    if len(units) != 1:
        raise BenchError("metric_unit_mismatch", f"{name}: {sorted(units)}")
    statuses = {item.status for item in outcomes}
    if len(statuses) != 1:
        raise BenchError(
            "inconsistent_metric_outcomes",
            f"{name}: measured in one run but skipped in another",
        )
    commands = {item.command for item in outcomes}
    command = outcomes[0].command if len(commands) == 1 else "(per-run; see runs)"
    environments = {
        tuple(sorted((str(key), str(value)) for key, value in item.environment.items()))
        for item in outcomes
    }
    environment = dict(outcomes[0].environment) if len(environments) == 1 else {}
    unit = outcomes[0].unit
    samples = tuple(sample for item in outcomes for sample in item.samples)
    if statuses == {"skipped"}:
        reasons = sorted({item.reason for item in outcomes})
        return MetricAggregate(
            name=name,
            unit=unit,
            status="skipped",
            command=command,
            environment=environment,
            runs=(),
            samples=samples,
            minimum=None,
            maximum=None,
            mean=None,
            median=None,
            stdev=None,
            spread_pct=None,
            reason="; ".join(reasons),
        )
    values = tuple(_measured_value(item) for item in outcomes)
    mean = statistics.fmean(values)
    minimum = min(values)
    maximum = max(values)
    return MetricAggregate(
        name=name,
        unit=unit,
        status="measured",
        command=command,
        environment=environment,
        runs=values,
        samples=samples,
        minimum=minimum,
        maximum=maximum,
        mean=mean,
        median=statistics.median(values),
        stdev=statistics.stdev(values) if len(values) >= 2 else 0.0,
        spread_pct=((maximum - minimum) / mean * 100.0) if mean > 0 else 0.0,
    )


def build_artifact(
    *,
    passes: Sequence[Sequence[MetricOutcome]],
    host: Mapping[str, Any],
    driver_argv: Sequence[str],
    generated_at: str,
    notes: Sequence[str] = (),
) -> dict[str, Any]:
    """Assemble the raw artifact; refuses any pass that misses a metric."""
    if not passes:
        raise BenchError("no_runs", "at least one measured pass is required")
    per_pass: list[dict[str, MetricOutcome]] = []
    for index, outcomes in enumerate(passes, start=1):
        by_name: dict[str, MetricOutcome] = {}
        for outcome in outcomes:
            if outcome.name in by_name:
                raise BenchError("duplicate_metric", f"pass {index}: {outcome.name}")
            by_name[outcome.name] = outcome
        missing = [name for name in METRIC_ORDER if name not in by_name]
        extra = sorted(name for name in by_name if name not in METRIC_ORDER)
        if missing or extra:
            raise BenchError(
                "metric_set_mismatch",
                f"pass {index}: missing={missing} extra={extra}",
            )
        per_pass.append(by_name)

    metrics: dict[str, Any] = {}
    for name in METRIC_ORDER:
        outcomes = [by_name[name] for by_name in per_pass]
        aggregate = aggregate_metric(outcomes)
        entry: dict[str, Any] = {
            "status": aggregate.status,
            "unit": aggregate.unit,
            "command": aggregate.command,
            "environment": dict(aggregate.environment),
            "runs": [
                {
                    "run": index,
                    "status": outcome.status,
                    "value": outcome.value,
                    "command": outcome.command,
                    "environment": dict(outcome.environment),
                    "reason": outcome.reason,
                    "detail": outcome.detail,
                    "samples": list(outcome.samples),
                }
                for index, outcome in enumerate(outcomes, start=1)
            ],
        }
        if aggregate.status == "measured":
            entry["stats"] = {
                "min": aggregate.minimum,
                "max": aggregate.maximum,
                "mean": aggregate.mean,
                "median": aggregate.median,
                "stdev": aggregate.stdev,
                "spread_pct": aggregate.spread_pct,
            }
        else:
            entry["reason"] = aggregate.reason
        metrics[name] = entry

    return {
        "schema_version": ARTIFACT_SCHEMA_VERSION,
        "kind": "isolation-bench",
        "generated_at": generated_at,
        "driver": {"argv": [str(item) for item in driver_argv], "runs": len(passes)},
        "host": dict(host),
        "metrics": metrics,
        "notes": [str(item) for item in notes],
    }


def render_summary(artifact: Mapping[str, Any]) -> str:
    """Human summary: every metric with its runs, stats and exact command."""
    host = artifact.get("host") or {}
    lines = [
        f"isolation bench - generated {artifact.get('generated_at', '?')} - "
        f"schema v{artifact.get('schema_version', '?')}",
        f"host: {host.get('hostname', '?')} kernel={host.get('kernel', '?')} "
        f"kvm_device={host.get('kvm_device', '?')} python={host.get('python', '?')}",
        f"driver: {shlex.join([str(item) for item in (artifact.get('driver') or {}).get('argv', [])])}",
    ]
    for name in METRIC_ORDER:
        metric = (artifact.get("metrics") or {}).get(name) or {}
        unit = metric.get("unit", "?")
        if metric.get("status") == "measured":
            stats = metric.get("stats") or {}
            runs = ", ".join(
                f"{float(item['value']):.3f}" for item in metric.get("runs") or []
            )
            lines.append(
                f"{name}: unit={unit} runs=[{runs}] mean={float(stats.get('mean', 0.0)):.3f} "
                f"min={float(stats.get('min', 0.0)):.3f} max={float(stats.get('max', 0.0)):.3f} "
                f"stdev={float(stats.get('stdev', 0.0)):.3f} "
                f"spread_pct={float(stats.get('spread_pct', 0.0)):.1f}"
            )
        else:
            lines.append(f"{name}: SKIP ({metric.get('reason', '?')}) unit={unit}")
        lines.append(f"  command={metric.get('command', '?')}")
    for note in artifact.get("notes") or []:
        lines.append(f"note: {note}")
    return "\n".join(lines) + "\n"


def write_artifacts(
    *,
    json_path: Path,
    summary_path: Path | None,
    artifact: Mapping[str, Any],
    summary: str,
) -> None:
    """Write the raw JSON artifact and (when requested) the human summary."""
    json_path.parent.mkdir(parents=True, exist_ok=True)
    json_path.write_text(
        json.dumps(artifact, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    if summary_path is not None:
        summary_path.parent.mkdir(parents=True, exist_ok=True)
        summary_path.write_text(summary, encoding="utf-8")


def _utc_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def collect_host_facts() -> dict[str, Any]:
    """Host facts for the artifact; nothing is fabricated when a file is absent."""
    facts: dict[str, Any] = {
        "hostname": socket.gethostname(),
        "kernel": platform.release(),
        "python": sys.version.split()[0],
        "kvm_device": KVM_DEVICE.exists(),
    }
    cpu_count = os.cpu_count()
    if cpu_count:
        facts["cpu_count"] = cpu_count
    model = _cpu_model()
    if model:
        facts["cpu_model"] = model
    memory = _mem_total_mib()
    if memory:
        facts["mem_total_mib"] = memory
    return facts


def _cpu_model() -> str:
    try:
        for line in Path("/proc/cpuinfo").read_text(encoding="utf-8").splitlines():
            if line.lower().startswith("model name"):
                return line.split(":", 1)[1].strip()
    except OSError:
        return ""
    return ""


def _mem_total_mib() -> int:
    try:
        for line in Path("/proc/meminfo").read_text(encoding="utf-8").splitlines():
            if line.startswith("MemTotal:"):
                return int(line.split()[1]) // 1024
    except (OSError, ValueError, IndexError):
        return 0
    return 0


# --------------------------------------------------------------------------- #
# Cleanup receipt and command helpers
# --------------------------------------------------------------------------- #


@dataclass
class CleanupReceipt:
    """What cleanup removed; anything still present is a leftover that fails the run."""

    removed: list[str] = field(default_factory=list)
    leftovers: list[str] = field(default_factory=list)

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


def driver_command(argv: Sequence[str], step: str) -> str:
    """The exact driver invocation a metric step ran under."""
    base = (
        shlex.join([str(item) for item in argv])
        if argv
        else "python tests/test_isolation_bench.py"
    )
    return f"{base} [{step}]"


def is_host_local_url(url: str) -> bool:
    """True when ``url`` points at this host (loopback), never at the LAN."""
    from urllib.parse import urlsplit

    try:
        hostname = urlsplit(url).hostname or ""
    except ValueError:
        return False
    return hostname.lower() in {"127.0.0.1", "localhost", "::1", "0.0.0.0"}


def run_bounded(argv: Sequence[str], *, timeout_s: float) -> subprocess.CompletedProcess[str]:
    """Run one host command under a hard timeout; never raises for a missing tool."""
    try:
        return subprocess.run(  # noqa: S603 - argv list, no shell
            [str(item) for item in argv],
            capture_output=True,
            text=True,
            errors="replace",
            check=False,
            timeout=timeout_s,
        )
    except FileNotFoundError as exc:
        return subprocess.CompletedProcess([], 127, "", f"{exc}")
    except subprocess.TimeoutExpired as exc:
        return subprocess.CompletedProcess(
            [], 124, str(exc.stdout or ""), f"timed out after {timeout_s}s"
        )


# --------------------------------------------------------------------------- #
# Harness: every metric, every run; cleanup always; artifact only when honest
# --------------------------------------------------------------------------- #


class BenchBackend(Protocol):
    """The measurement seam: faked in the unit tests, real on a KVM host."""

    def measure(self, metric: str, run_index: int) -> MetricOutcome: ...
    def end_run(self, run_index: int) -> None: ...
    def cleanup(self) -> CleanupReceipt: ...


@dataclass(frozen=True, slots=True)
class BenchRun:
    """One invocation's outcome: artifact, summary and the cleanup receipt."""

    artifact: dict[str, Any]
    summary: str
    receipt: CleanupReceipt

    @property
    def ok(self) -> bool:
        return self.receipt.clean


class BenchHarness:
    """Runs the metric checklist over N consecutive passes and builds the artifact."""

    def __init__(
        self,
        runs: int,
        backend: BenchBackend,
        *,
        host_facts: Callable[[], dict[str, Any]] = collect_host_facts,
        driver_argv: Sequence[str] = (),
        emit: Callable[[str], None] = print,
        generated_at: Callable[[], str] = _utc_now,
        notes: Sequence[str] = METHODOLOGY_NOTES,
    ) -> None:
        self._runs = max(1, int(runs))
        self._backend = backend
        self._host_facts = host_facts
        self._driver_argv = tuple(str(item) for item in driver_argv)
        self._emit = emit
        self._generated_at = generated_at
        self._notes = tuple(str(item) for item in notes)

    def run(self) -> BenchRun:
        """Measure every metric in every run, clean up, and only then build the artifact."""
        passes: list[list[MetricOutcome]] = []
        receipt = CleanupReceipt(leftovers=["cleanup did not run"])
        try:
            for run_index in range(1, self._runs + 1):
                outcomes: list[MetricOutcome] = []
                for metric in METRIC_ORDER:
                    outcome = self._backend.measure(metric, run_index)
                    if outcome.name != metric:
                        raise BenchError(
                            "metric_mismatch",
                            f"asked for {metric}, backend returned {outcome.name}",
                        )
                    self._emit(render_metric_line(outcome))
                    outcomes.append(outcome)
                self._backend.end_run(run_index)
                passes.append(outcomes)
        finally:
            try:
                receipt = self._backend.cleanup()
            except Exception as exc:  # a cleanup crash is a leftover
                receipt = CleanupReceipt()
                receipt.leftovers.append(f"cleanup crashed: {type(exc).__name__}: {exc}")
        for line in receipt.render():
            self._emit(line)
        artifact = build_artifact(
            passes=passes,
            host=self._host_facts(),
            driver_argv=self._driver_argv,
            generated_at=self._generated_at(),
            notes=self._notes,
        )
        return BenchRun(artifact=artifact, summary=render_summary(artifact), receipt=receipt)


# --------------------------------------------------------------------------- #
# Real backend (KVM host only; every probe bounded, every skip explicit)
# --------------------------------------------------------------------------- #


@dataclass(frozen=True, slots=True)
class BenchRequest:
    """Everything one measured invocation needs before the host is touched."""

    profile: str = DEFAULT_PROFILE
    workspace: str = ""
    runs: int = DEFAULT_RUNS
    vsock_port: int = DEFAULT_VSOCK_PORT
    boot_timeout_s: float = DEFAULT_BOOT_TIMEOUT_S
    wait_timeout_s: float = DEFAULT_WAIT_TIMEOUT_S
    idle_window_s: float = DEFAULT_IDLE_WINDOW_S
    latency_iterations: int = DEFAULT_LATENCY_ITERATIONS
    inference_iterations: int = DEFAULT_INFERENCE_ITERATIONS
    bench_root: str = ""
    driver_argv: tuple[str, ...] = ()


@dataclass
class _RunState:
    """Per-pass state: the session, its VM and the baseline inference series."""

    run_index: int
    session_id: str
    baseline_inference_ms: tuple[float, ...] = ()
    baseline_inference_error: str = ""
    vm: SessionVm | None = None
    domain: str = ""
    overlay: str = ""
    domain_started_at: float = 0.0
    emulator_pid: int | None = None


class RealBenchBackend:
    """The real host environment: profile, workspace, manager and bounded probes."""

    def __init__(
        self,
        request: BenchRequest,
        *,
        env: Mapping[str, str],
        emit: Callable[[str], None] = print,
    ) -> None:
        self._request = request
        self._env = dict(env)
        self._emit = emit
        self._token = uuid.uuid4().hex[:8]
        self._bench_root = (
            Path(request.bench_root).expanduser()
            if request.bench_root.strip()
            else Path(tempfile.mkdtemp(prefix="igorek-isolation-bench-"))
        )
        self._overlay_dir = self._bench_root / "overlays"
        self._artifact_dir = self._bench_root / "artifacts"
        self._profile_name = str(request.profile)
        self._profile: dict[str, Any] = {}
        self._workspace_host = Path()
        self._guest_workspace = GUEST_WORKSPACE_PATH
        self._control_dir: Path | None = None
        self._guest_control = ""
        self._base_image = Path()
        self._canary_content = f"isolation-bench-canary-{self._token}"
        self._config: IsolationConfig | None = None
        self._manager: IsolationManager | None = None
        self._server_thread: threading.Thread | None = None
        self._server_error: str = ""
        self._stop_event = threading.Event()
        self._runs: dict[int, _RunState] = {}
        self._receipt = CleanupReceipt()
        self._prepared = False

    # -- preparation -------------------------------------------------------- #

    def _prepare(self) -> None:
        if self._prepared:
            return
        request = self._request
        if not request.workspace.strip():
            raise BenchError("workspace_missing", "pass --workspace <absolute dir>")
        try:
            profile = get_profile_config(self._profile_name)
        except (FileNotFoundError, ValueError) as exc:
            raise BenchError("profile_not_found", str(exc)) from exc
        backend = str(profile.get("backend") or "")
        if backend != "cc-backend":
            raise BenchError(
                "profile_not_cc_backend", f"{self._profile_name}: backend={backend!r}"
            )
        try:
            export = resolve_workspace_export(
                request.workspace, policy=WorkspacePolicy.from_environment()
            )
        except IsolationError as exc:
            raise BenchError(f"workspace_denied:{exc.reason}", exc.detail) from exc
        self._profile = dict(profile)
        self._workspace_host = Path(str(export["host_path"]))
        self._guest_workspace = str(export["guest_path"])
        self._control_dir = self._workspace_host / BENCH_DIR_NAME / self._token
        self._guest_control = f"{self._guest_workspace}/{BENCH_DIR_NAME}/{self._token}"
        self._control_dir.mkdir(parents=True, exist_ok=True)
        (self._control_dir / CANARY_NAME).write_text(
            self._canary_content + "\n", encoding="utf-8"
        )
        self._artifact_dir.mkdir(parents=True, exist_ok=True)
        self._overlay_dir.mkdir(parents=True, exist_ok=True)
        self._base_image = Path(
            self._env.get(ENV_BASE_IMAGE, "").strip() or str(default_image_path())
        ).expanduser()
        if not self._base_image.is_file():
            raise BenchError("base_image_missing", f"no base image at {self._base_image}")
        config_env = dict(self._env)
        config_env.update(
            {
                ENV_MODE: MODE_KVM,
                ENV_BASE_IMAGE: str(self._base_image),
                ENV_OVERLAY_DIR: str(self._overlay_dir),
                ENV_VSOCK_PORT: str(request.vsock_port),
            }
        )
        config = IsolationConfig.from_env(config_env)
        if not config.enabled:
            raise BenchError(
                "isolation_config_refused", config.reason or "isolation config is disabled"
            )
        self._config = config
        self._manager = IsolationManager(config)
        self._start_channel_server()
        self._prepared = True

    def _start_channel_server(self) -> None:
        if self._server_thread is not None or self._manager is None:
            return
        self._stop_event.clear()

        def _serve() -> None:
            manager = self._manager
            if manager is None:
                return
            try:
                manager.channel_server.serve_forever(self._stop_event)
            except Exception as exc:  # surfaced to the waiter
                self._server_error = f"{type(exc).__name__}: {exc}"

        self._server_thread = threading.Thread(
            target=_serve, name="bench-channel-server", daemon=True
        )
        self._server_thread.start()

    # -- metric dispatch ---------------------------------------------------- #

    def measure(self, metric: str, run_index: int) -> MetricOutcome:
        handlers: dict[str, Callable[[int], MetricOutcome]] = {
            METRIC_COLD_START: self._cold_start,
            METRIC_WARM_START: self._warm_start,
            METRIC_RAM_OVERHEAD: self._ram_overhead,
            METRIC_CPU_OVERHEAD: self._cpu_overhead,
            METRIC_DISK_OVERHEAD: self._disk_overhead,
            METRIC_COMMAND_LATENCY: self._command_latency,
            METRIC_LOCAL_INFERENCE: self._local_inference_impact,
            METRIC_WORKSPACE_ATTACH: self._workspace_attach_time,
        }
        handler = handlers.get(metric)
        if handler is None:
            raise BenchError("unknown_metric", metric)
        return handler(int(run_index))

    def end_run(self, run_index: int) -> None:
        """Stop this pass's VM so the next pass measures a clean host."""
        state = self._runs.get(run_index)
        if state is None or state.vm is None:
            return
        self._stop_vm(state)

    def _stop_vm(self, state: _RunState) -> None:
        manager = self._manager
        if manager is None:
            state.vm = None
            return
        try:
            manager.stop(state.session_id)
        except IsolationError as exc:
            self._receipt.leftovers.append(
                f"session VM {state.session_id}: stop failed ({exc.reason})"
            )
        else:
            self._receipt.removed.append(f"session VM {state.session_id}")
        state.vm = None

    # -- metrics ------------------------------------------------------------ #

    def _cold_start(self, run_index: int) -> MetricOutcome:
        self._prepare()
        state = self._begin_run(run_index)
        manager = self._require_manager()
        environment = self._metric_environment(state)
        command = driver_command(self._request.driver_argv, METRIC_COLD_START)
        started = time.monotonic()
        try:
            vm = manager.ensure_vm(
                SessionSpec(state.session_id, str(self._workspace_host))
            )
        except IsolationError as exc:
            raise BenchError(f"cold_start_failed:{exc.reason}", exc.detail) from exc
        state.vm = vm
        state.domain = domain_name(vm.spec)
        state.overlay = str(vm.domain.overlay) if vm.domain.overlay is not None else ""
        state.domain_started_at = time.monotonic()
        self._wait_for_endpoint(state.session_id)
        elapsed = time.monotonic() - started
        return measured(
            METRIC_COLD_START,
            elapsed,
            command=command,
            environment=environment,
            detail=(
                "method=IsolationManager.ensure_vm (overlay create + domain define/start) "
                "then the guest runner's vsock channel handshake"
            ),
            samples=(elapsed,),
        )

    def _warm_start(self, run_index: int) -> MetricOutcome:
        state = self._require_run(run_index)
        manager = self._require_manager()
        command = driver_command(self._request.driver_argv, METRIC_WARM_START)
        started = time.monotonic()
        vm = manager.ensure_vm(SessionSpec(state.session_id, str(self._workspace_host)))
        endpoint_ready = (
            manager.channel_server.session_for(SessionId(state.session_id)) is not None
        )
        elapsed = time.monotonic() - started
        if vm.session_id != state.session_id or not endpoint_ready:
            raise BenchError(
                "warm_start_no_live_vm",
                f"session {state.session_id}: live VM/endpoint not available",
            )
        return measured(
            METRIC_WARM_START,
            elapsed,
            command=command,
            environment=self._metric_environment(state),
            detail=(
                "method=IsolationManager.ensure_vm on an existing live VM (no boot) plus "
                "the channel endpoint check; a stopped VM is a cold start until todo 26"
            ),
            samples=(elapsed,),
        )

    def _workspace_attach_time(self, run_index: int) -> MetricOutcome:
        state = self._require_run(run_index)
        if state.domain_started_at <= 0.0:
            raise BenchError(
                "attach_before_start", f"run {run_index}: cold_start must run first"
            )
        preview = self._pinned_read(
            state, label=f"attach-{run_index}", guest_path=self._guest_canary_path()
        )
        elapsed = time.monotonic() - state.domain_started_at
        if self._canary_content not in preview:
            raise BenchError(
                "workspace_not_visible",
                f"the guest did not read the workspace canary: {preview[:200]!r}",
            )
        return measured(
            METRIC_WORKSPACE_ATTACH,
            elapsed,
            command=driver_command(self._request.driver_argv, METRIC_WORKSPACE_ATTACH),
            environment=self._metric_environment(state),
            detail=(
                "method=time from domain start to the guest observing the exported "
                "workspace via a pinned Read of the workspace canary"
            ),
            samples=(elapsed,),
        )

    def _ram_overhead(self, run_index: int) -> MetricOutcome:
        state = self._require_run(run_index)
        pid = self._emulator_pid(state)
        command = f"pgrep -f qemu-system.*{state.domain} && ps -o rss= -p <pid>"
        environment = self._metric_environment(state)
        if pid is None:
            return skipped(
                METRIC_RAM_OVERHEAD,
                command=command,
                environment=environment,
                reason=f"emulator process not found for domain {state.domain!r}",
            )
        result = run_bounded(
            ["ps", "-o", "rss=", "-p", str(pid)], timeout_s=self._request.wait_timeout_s
        )
        rss_kib = _parse_first_int(result.stdout)
        if result.returncode != 0 or rss_kib is None:
            return skipped(
                METRIC_RAM_OVERHEAD,
                command=f"ps -o rss= -p {pid}",
                environment=environment,
                reason=f"ps failed (rc={result.returncode}) for pid {pid}",
            )
        return measured(
            METRIC_RAM_OVERHEAD,
            rss_kib / 1024.0,
            command=f"ps -o rss= -p {pid}",
            environment={**environment, "emulator_pid": str(pid)},
            detail="method=resident set size (VmRSS) of the session's QEMU process",
            samples=(rss_kib / 1024.0,),
        )

    def _cpu_overhead(self, run_index: int) -> MetricOutcome:
        state = self._require_run(run_index)
        pid = self._emulator_pid(state)
        window = max(0.1, float(self._request.idle_window_s))
        environment = {**self._metric_environment(state), "idle_window_s": f"{window:.1f}"}
        if pid is None:
            return skipped(
                METRIC_CPU_OVERHEAD,
                command="awk '{print $14+$15}' /proc/<pid>/stat",
                environment=environment,
                reason=f"emulator process not found for domain {state.domain!r}",
            )
        command = (
            f"awk '{{print $14+$15}}' /proc/{pid}/stat  (sampled twice, {window:.1f}s apart)"
        )
        first = self._cpu_jiffies(pid)
        if first is None:
            return skipped(
                METRIC_CPU_OVERHEAD,
                command=command,
                environment={**environment, "emulator_pid": str(pid)},
                reason=f"cannot read /proc/{pid}/stat",
            )
        time.sleep(window)
        second = self._cpu_jiffies(pid)
        if second is None:
            return skipped(
                METRIC_CPU_OVERHEAD,
                command=command,
                environment={**environment, "emulator_pid": str(pid)},
                reason=f"cannot re-read /proc/{pid}/stat after the idle window",
            )
        ticks = float(os.sysconf("SC_CLK_TCK"))
        percent = max(0.0, second - first) / ticks / window * 100.0
        return measured(
            METRIC_CPU_OVERHEAD,
            percent,
            command=command,
            environment={**environment, "emulator_pid": str(pid)},
            detail=(
                "method=CPU-time delta of the QEMU process over an idle window, "
                "as percent of one host core"
            ),
            samples=(percent,),
        )

    def _disk_overhead(self, run_index: int) -> MetricOutcome:
        state = self._require_run(run_index)
        environment = self._metric_environment(state)
        overlay = state.overlay
        if not overlay or not Path(overlay).is_file():
            return skipped(
                METRIC_DISK_OVERHEAD,
                command=f"qemu-img info --output=json {overlay or '<overlay>'}",
                environment=environment,
                reason=f"overlay not found: {overlay or '<none>'}",
            )
        command = f"qemu-img info --output=json {overlay}"
        result = run_bounded(
            ["qemu-img", "info", "--output=json", overlay],
            timeout_s=self._request.wait_timeout_s,
        )
        if result.returncode != 0:
            return skipped(
                METRIC_DISK_OVERHEAD,
                command=command,
                environment=environment,
                reason=f"qemu-img failed (rc={result.returncode})",
            )
        try:
            payload = json.loads(result.stdout)
        except json.JSONDecodeError as exc:
            return skipped(
                METRIC_DISK_OVERHEAD,
                command=command,
                environment=environment,
                reason=f"qemu-img produced unparsable JSON: {exc}",
            )
        actual_size = payload.get("actual-size") if isinstance(payload, dict) else None
        if not isinstance(actual_size, int) or actual_size < 0:
            return skipped(
                METRIC_DISK_OVERHEAD,
                command=command,
                environment=environment,
                reason="qemu-img reported no actual-size",
            )
        return measured(
            METRIC_DISK_OVERHEAD,
            actual_size / (1024.0 * 1024.0),
            command=command,
            environment=environment,
            detail="method=physical (actual) size of the session qcow2 overlay",
            samples=(actual_size / (1024.0 * 1024.0),),
        )

    def _command_latency(self, run_index: int) -> MetricOutcome:
        state = self._require_run(run_index)
        iterations = max(1, int(self._request.latency_iterations))
        samples: list[float] = []
        for index in range(iterations):
            started = time.monotonic()
            preview = self._pinned_read(
                state,
                label=f"latency-{run_index}-{index}",
                guest_path=self._guest_canary_path(),
            )
            elapsed_ms = (time.monotonic() - started) * 1000.0
            if self._canary_content not in preview:
                raise BenchError(
                    "canary_not_read", f"latency probe {index}: {preview[:200]!r}"
                )
            samples.append(elapsed_ms)
        return measured(
            METRIC_COMMAND_LATENCY,
            statistics.median(samples),
            command=driver_command(self._request.driver_argv, METRIC_COMMAND_LATENCY),
            environment={
                **self._metric_environment(state),
                "iterations": str(iterations),
            },
            detail=(
                "method=pinned guest Read of the workspace canary through the vsock "
                f"channel (no inference); samples_ms={[round(item, 3) for item in samples]}"
            ),
            samples=tuple(samples),
        )

    def _local_inference_impact(self, run_index: int) -> MetricOutcome:
        state = self._require_run(run_index)
        url = self._inference_url()
        iterations = max(1, int(self._request.inference_iterations))
        environment = self._metric_environment(state)
        if not url:
            return skipped(
                METRIC_LOCAL_INFERENCE,
                command="(no local inference endpoint configured)",
                environment=environment,
                reason=(
                    "no host-local inference endpoint: set "
                    f"{ENV_BENCH_INFERENCE_URL} or use a profile whose base_url is loopback"
                ),
            )
        command = f"curl -s -o /dev/null -w '%{{time_total}}' {url}  (x{iterations})"
        environment = {**environment, "url": url, "iterations": str(iterations)}
        if state.baseline_inference_error:
            return skipped(
                METRIC_LOCAL_INFERENCE,
                command=command,
                environment=environment,
                reason=f"baseline probe without the VM failed: {state.baseline_inference_error}",
            )
        with_vm, error = self._curl_series(url, iterations)
        if error:
            return skipped(
                METRIC_LOCAL_INFERENCE,
                command=command,
                environment=environment,
                reason=f"probe with the VM running failed: {error}",
            )
        baseline = state.baseline_inference_ms
        baseline_mean = statistics.fmean(baseline)
        if baseline_mean <= 0:
            return skipped(
                METRIC_LOCAL_INFERENCE,
                command=command,
                environment=environment,
                reason="baseline latency is zero; a percent impact is undefined",
            )
        with_vm_mean = statistics.fmean(with_vm)
        impact = (with_vm_mean - baseline_mean) / baseline_mean * 100.0
        return measured(
            METRIC_LOCAL_INFERENCE,
            impact,
            command=command,
            environment=environment,
            detail=(
                "method=host-local inference latency without vs with the running VM; "
                f"baseline_mean_ms={baseline_mean:.3f} vm_mean_ms={with_vm_mean:.3f}"
            ),
            samples=tuple(baseline) + tuple(with_vm),
        )

    # -- run plumbing ------------------------------------------------------- #

    def _begin_run(self, run_index: int) -> _RunState:
        state = _RunState(
            run_index=run_index, session_id=f"bench-{self._token}-r{run_index}"
        )
        url = self._inference_url()
        if url:
            state.baseline_inference_ms, state.baseline_inference_error = self._curl_series(
                url, max(1, int(self._request.inference_iterations))
            )
        self._runs[run_index] = state
        return state

    def _require_run(self, run_index: int) -> _RunState:
        state = self._runs.get(run_index)
        if state is None:
            raise BenchError(
                "run_not_started", f"run {run_index}: cold_start must run first"
            )
        return state

    def _require_manager(self) -> IsolationManager:
        if self._manager is None:
            raise BenchError("not_prepared", "prepare() must run first")
        return self._manager

    def _wait_for_endpoint(self, session_id: str) -> None:
        manager = self._require_manager()
        deadline = time.monotonic() + max(0.0, float(self._request.boot_timeout_s))

        def _connected() -> bool:
            if self._server_error:
                raise BenchError("channel_server_failed", self._server_error)
            return manager.channel_server.session_for(SessionId(session_id)) is not None

        while time.monotonic() < deadline:
            if _connected():
                return
            time.sleep(0.5)
        raise BenchError(
            "guest_runner_not_connected",
            f"session {session_id}: the guest never opened the vsock channel within "
            f"{self._request.boot_timeout_s}s",
        )

    def _metric_environment(self, state: _RunState) -> dict[str, str]:
        config = self._config
        if config is None:
            raise BenchError("not_prepared", "prepare() must run first")
        return {
            "IGOREK_ISOLATION_MODE": MODE_KVM,
            "profile": self._profile_name,
            "workspace_host": str(self._workspace_host),
            "workspace_guest": self._guest_workspace,
            "base_image": str(self._base_image),
            "overlay_dir": str(self._overlay_dir),
            "vsock_port": str(config.vsock_port),
            "session": state.session_id,
            "domain": state.domain,
            "overlay": state.overlay,
            "vcpus": str(config.session_vcpus),
            "memory_mib": str(config.session_memory_mb),
            "run": str(state.run_index),
        }

    def _emulator_pid(self, state: _RunState) -> int | None:
        if state.emulator_pid is not None:
            return state.emulator_pid
        if not state.domain:
            return None
        result = run_bounded(
            ["pgrep", "-f", f"qemu-system.*{state.domain}"],
            timeout_s=self._request.wait_timeout_s,
        )
        pid = _parse_first_int(result.stdout)
        if result.returncode != 0 or pid is None:
            return None
        state.emulator_pid = pid
        return pid

    def _cpu_jiffies(self, pid: int) -> float | None:
        result = run_bounded(
            ["awk", "{print $14+$15}", f"/proc/{pid}/stat"],
            timeout_s=self._request.wait_timeout_s,
        )
        if result.returncode != 0:
            return None
        try:
            return float(result.stdout.strip().splitlines()[0])
        except (ValueError, IndexError):
            return None

    def _guest_canary_path(self) -> str:
        return f"{self._guest_control}/{CANARY_NAME}"

    def _inference_url(self) -> str:
        explicit = self._env.get(ENV_BENCH_INFERENCE_URL, "").strip()
        if explicit:
            return explicit
        base_url = str(self._profile.get("base_url") or "").strip()
        if base_url and is_host_local_url(base_url):
            return base_url
        return ""

    def _curl_series(self, url: str, iterations: int) -> tuple[tuple[float, ...], str]:
        """Latency samples in ms; on the first failure returns ``((), reason)``."""
        values: list[float] = []
        for _ in range(max(1, int(iterations))):
            result = run_bounded(
                ["curl", "-s", "-o", "/dev/null", "-w", "%{time_total}", url],
                timeout_s=self._request.wait_timeout_s,
            )
            if result.returncode != 0:
                return (), f"curl rc={result.returncode}"
            try:
                values.append(float(result.stdout.strip()) * 1000.0)
            except ValueError:
                return (), f"unparsable curl output {result.stdout.strip()[:80]!r}"
        return tuple(values), ""

    # -- dispatch of pinned calls ------------------------------------------ #

    def _pinned_read(self, state: _RunState, *, label: str, guest_path: str) -> str:
        tool_use_id = f"bench_read_{label}"
        payload = self._task_payload(
            task_id=f"bench-{label}-{self._token}",
            session_id=state.session_id,
            user_message="Isolation benchmark pinned read; no model call.",
            forced={
                "name": "Read",
                "input": {"file_path": guest_path},
                "tool_use_id": tool_use_id,
                "source": "bench_harness",
            },
            filesystem_mode="full-dangerous",
        )
        self._dispatch(f"bench-{label}-{self._token}", state.session_id, payload)
        return self._forced_result_preview(tool_use_id)

    def _task_payload(
        self,
        *,
        task_id: str,
        session_id: str,
        user_message: str,
        forced: Mapping[str, Any] | None = None,
        filesystem_mode: str = "",
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "task_id": task_id,
            "profile": self._profile_name,
            "model": str(self._profile.get("model") or ""),
            "base_url": str(self._profile.get("base_url") or ""),
            "api_format": str(self._profile.get("api_format") or "openai"),
            "system_prompt": (
                "Isolation benchmark harness. Call the pinned tool exactly as "
                "instructed; do not add commentary."
            ),
            "user_message": user_message,
            "skills_text": "",
            "foundation_context": "",
            "filesystem_mode": filesystem_mode
            or str(self._profile.get("filesystem_mode") or "workspace"),
            "guard_mode": "off",
            "max_tokens": 256,
            "timeout_s": int(self._request.wait_timeout_s),
            "current_session": session_id,
            "source_platform": "android",
            "artifact_workdir": str(self._artifact_dir),
            "trailing_context_text": "",
            "input_files": [],
            "enabled_tools": [],
            "history_messages": [],
        }
        if forced is not None:
            payload["metadata"] = {"forced_tool_call": dict(forced)}
        return payload

    def _dispatch(
        self, task_id: str, session_id: str, payload: Mapping[str, Any]
    ) -> dict[str, Any]:
        manager = self._require_manager()
        task: dict[str, Any] = {"task_id": task_id, "metadata": {"session": session_id}}
        try:
            return manager.run_task(task, str(self._workspace_host), payload)
        except IsolationError as exc:
            raise BenchError(f"dispatch_failed:{exc.reason}", exc.detail) from exc

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
        raise BenchError(
            "missing_trace_result", f"no tool.result record for {tool_use_id} was relayed"
        )

    # -- cleanup ------------------------------------------------------------ #

    def cleanup(self) -> CleanupReceipt:
        """Stop every live VM, close the manager and remove the harness artifacts."""
        for state in self._runs.values():
            if state.vm is not None:
                self._stop_vm(state)
        manager = self._manager
        if manager is not None:
            try:
                manager.close()
            except IsolationError as exc:
                self._receipt.leftovers.append(f"manager close failed: {exc.reason}")
            else:
                self._receipt.removed.append("manager handles")
            self._manager = None
        if self._server_thread is not None:
            self._stop_event.set()
            self._server_thread.join(timeout=5.0)
            self._server_thread = None
        if self._control_dir is not None:
            self._remove_tree(self._control_dir, "bench control dir")
        if self._bench_root.exists():
            self._remove_tree(self._bench_root, "bench root")
        return self._receipt

    def _remove_tree(self, path: Path, label: str) -> None:
        shutil.rmtree(path, ignore_errors=True)
        if path.exists():
            self._receipt.leftovers.append(f"{label} {path}")
        else:
            self._receipt.removed.append(f"{label} {path}")


def _parse_first_int(text: str) -> int | None:
    for token in text.split():
        try:
            return int(token)
        except ValueError:
            continue
    return None


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="isolation_bench",
        description="Performance measurement harness for the per-session KVM isolation path.",
    )
    parser.add_argument("--real", action="store_true", help="run the real KVM benchmark")
    parser.add_argument(
        "--runs",
        type=int,
        default=DEFAULT_RUNS,
        help=f"consecutive measurement passes (default: {DEFAULT_RUNS})",
    )
    parser.add_argument("--out", default="", help="raw JSON artifact path (required)")
    parser.add_argument("--summary", default="", help="human summary path")
    parser.add_argument(
        "--profile",
        default=os.environ.get("IGOREK_ISOLATION_BENCH_PROFILE", DEFAULT_PROFILE),
        help=f"existing cc-backend profile (default: {DEFAULT_PROFILE})",
    )
    parser.add_argument(
        "--workspace",
        default=os.environ.get("IGOREK_ISOLATION_BENCH_WORKSPACE", ""),
        help="existing absolute project workspace directory",
    )
    parser.add_argument(
        "--vsock-port",
        type=int,
        default=int(os.environ.get(ENV_VSOCK_PORT) or DEFAULT_VSOCK_PORT),
        help=f"host vsock port (default: {DEFAULT_VSOCK_PORT})",
    )
    parser.add_argument(
        "--boot-timeout-s", type=float, default=DEFAULT_BOOT_TIMEOUT_S, help="guest boot budget"
    )
    parser.add_argument(
        "--wait-timeout-s", type=float, default=DEFAULT_WAIT_TIMEOUT_S, help="per-probe budget"
    )
    parser.add_argument(
        "--idle-window-s",
        type=float,
        default=DEFAULT_IDLE_WINDOW_S,
        help=f"CPU idle-drain window (default: {DEFAULT_IDLE_WINDOW_S})",
    )
    parser.add_argument(
        "--latency-iterations",
        type=int,
        default=DEFAULT_LATENCY_ITERATIONS,
        help=f"pinned-call latency samples (default: {DEFAULT_LATENCY_ITERATIONS})",
    )
    parser.add_argument(
        "--inference-iterations",
        type=int,
        default=DEFAULT_INFERENCE_ITERATIONS,
        help=f"local-inference samples (default: {DEFAULT_INFERENCE_ITERATIONS})",
    )
    parser.add_argument("--bench-root", default="", help="scratch dir (created by the caller)")
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
    availability: Callable[[Mapping[str, str]], str | None] = probe_availability,
    backend_factory: Callable[..., BenchBackend] | None = None,
) -> int:
    """Entry point: probe -> SKIP, or run the benchmark and return 0/1."""
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
        emit("FAIL: pass --real to run the benchmark (or --probe-only for the availability check)")
        return EXIT_FAIL
    if not str(args.out).strip():
        emit("FAIL: --out <json path> is required so the raw artifact is recorded")
        return EXIT_FAIL

    driver_argv = (
        tuple(sys.argv)
        if argv is None
        else ("isolation_bench", *[str(item) for item in argv])
    )
    request = BenchRequest(
        profile=str(args.profile),
        workspace=str(args.workspace),
        runs=max(1, int(args.runs)),
        vsock_port=int(args.vsock_port),
        boot_timeout_s=float(args.boot_timeout_s),
        wait_timeout_s=float(args.wait_timeout_s),
        idle_window_s=float(args.idle_window_s),
        latency_iterations=max(1, int(args.latency_iterations)),
        inference_iterations=max(1, int(args.inference_iterations)),
        bench_root=str(args.bench_root),
        driver_argv=driver_argv,
    )
    factory = backend_factory if backend_factory is not None else RealBenchBackend
    try:
        backend = factory(request, env=source, emit=emit)
        run = BenchHarness(request.runs, backend, driver_argv=driver_argv, emit=emit).run()
    except BenchError as exc:
        emit(f"FAIL: {exc}")
        emit("RESULT: FAIL")
        return EXIT_FAIL
    except Exception as exc:  # setup failure must not crash the CLI
        emit(f"FAIL: harness_setup - {type(exc).__name__}: {exc}")
        emit("RESULT: FAIL")
        return EXIT_FAIL
    emit(run.summary)
    if not run.ok:
        emit(f"CLEANUP: {run.receipt.summary()}")
        emit("RESULT: FAIL")
        return EXIT_FAIL
    write_artifacts(
        json_path=Path(str(args.out)),
        summary_path=Path(str(args.summary)) if str(args.summary).strip() else None,
        artifact=run.artifact,
        summary=run.summary,
    )
    emit(f"ARTIFACT: {args.out}")
    if str(args.summary).strip():
        emit(f"SUMMARY: {args.summary}")
    emit("RESULT: PASS")
    return EXIT_OK


# --------------------------------------------------------------------------- #
# TESTS: the pure harness is exercised with fakes; the real path stays guarded
# --------------------------------------------------------------------------- #

import pytest  # noqa: E402

import unittest  # noqa: E402


def fake_pass(
    run_index: int,
    *,
    cold: float = 10.0,
    warm: float = 0.5,
    skip_inference: bool = False,
) -> list[MetricOutcome]:
    """One complete pass of all eight metrics, scripted for the pure tests."""
    outcomes: list[MetricOutcome] = []
    for name in METRIC_ORDER:
        if name == METRIC_COLD_START:
            value = cold
        elif name == METRIC_WARM_START:
            value = warm
        elif name == METRIC_LOCAL_INFERENCE and skip_inference:
            outcomes.append(
                skipped(
                    name,
                    command="curl -s -o /dev/null -w '%{time_total}' http://127.0.0.1:9/",
                    environment={"url": "http://127.0.0.1:9/"},
                    reason="endpoint unreachable (rc=7)",
                )
            )
            continue
        else:
            value = 1.0 + run_index
        outcomes.append(
            measured(
                name,
                value,
                command=f"python tests/test_isolation_bench.py --real [{name}] run-{run_index}",
                environment={"run": str(run_index)},
            )
        )
    return outcomes


class FakeBenchBackend:
    """Scripted backend: records the calls, raises on demand, can leave leftovers."""

    def __init__(
        self,
        values: Mapping[tuple[str, int], MetricOutcome] | None = None,
        *,
        fail_on: str = "",
        leftovers: Sequence[str] = (),
    ) -> None:
        self.calls: list[tuple[str, int]] = []
        self.ended: list[int] = []
        self.cleanup_calls = 0
        self._values = dict(values or {})
        self._fail_on = fail_on
        self._receipt = CleanupReceipt()
        if leftovers:
            self._receipt.leftovers.extend(leftovers)
        else:
            self._receipt.removed.append("fake bench root")

    def measure(self, metric: str, run_index: int) -> MetricOutcome:
        self.calls.append((metric, run_index))
        if metric == self._fail_on:
            raise BenchError("step_failed", metric)
        scripted = self._values.get((metric, run_index))
        if scripted is not None:
            return scripted
        return measured(
            metric,
            1.0 + run_index,
            command=f"fake [{metric}] run-{run_index}",
            environment={"run": str(run_index)},
        )

    def end_run(self, run_index: int) -> None:
        self.ended.append(run_index)

    def cleanup(self) -> CleanupReceipt:
        self.cleanup_calls += 1
        return self._receipt


class ProbeTests(unittest.TestCase):
    def test_structural_probe_reports_a_missing_kvm_device(self) -> None:
        reason = structural_probe(
            device_exists=lambda: False,
            capability_reason=lambda: None,
            vsock_reason=lambda: None,
        )
        self.assertEqual(reason, "no /dev/kvm on this host")

    def test_structural_probe_passes_through_the_capability_reason(self) -> None:
        reason = structural_probe(
            device_exists=lambda: True,
            capability_reason=lambda: "missing virtiofsd",
            vsock_reason=lambda: None,
        )
        self.assertEqual(reason, "missing virtiofsd")

    def test_structural_probe_reports_a_missing_af_vsock(self) -> None:
        reason = structural_probe(
            device_exists=lambda: True,
            capability_reason=lambda: None,
            vsock_reason=lambda: "AF_VSOCK is not available on this host",
        )
        self.assertEqual(reason, "AF_VSOCK is not available on this host")

    def test_structural_probe_is_ready_when_everything_is_present(self) -> None:
        self.assertIsNone(
            structural_probe(
                device_exists=lambda: True,
                capability_reason=lambda: None,
                vsock_reason=lambda: None,
            )
        )

    def test_probe_refuses_when_the_structural_probe_fails_even_with_opt_in(self) -> None:
        reason = probe_availability(
            {"KVM_AVAILABLE": "1"}, structure=lambda: "no /dev/kvm on this host"
        )
        self.assertEqual(reason, "no /dev/kvm on this host")

    def test_probe_requires_an_explicit_opt_in_only_when_the_host_is_ready(self) -> None:
        reason = probe_availability({}, structure=lambda: None)
        self.assertIsNotNone(reason)
        self.assertIn("KVM_AVAILABLE", str(reason))
        for value in ("1", "true", "TRUE", "yes", "on", "kvm"):
            self.assertIsNone(
                probe_availability({"KVM_AVAILABLE": value}, structure=lambda: None)
            )
        for value in ("", "0", "no", "off", "disabled"):
            self.assertIsNotNone(
                probe_availability({"KVM_AVAILABLE": value}, structure=lambda: None)
            )


class MetricOutcomeTests(unittest.TestCase):
    def test_measured_outcome_records_value_command_and_environment(self) -> None:
        outcome = measured(
            METRIC_COLD_START,
            12.5,
            command="python tests/test_isolation_bench.py --real",
            environment={"session": "s1"},
            detail="method=IsolationManager.ensure_vm",
        )
        self.assertEqual(outcome.status, "measured")
        self.assertAlmostEqual(float(outcome.value or 0.0), 12.5)
        self.assertEqual(outcome.unit, "s")
        self.assertEqual(dict(outcome.environment)["session"], "s1")
        self.assertIn("ensure_vm", outcome.detail)

    def test_measured_outcome_refuses_a_missing_command_or_environment(self) -> None:
        with self.assertRaises(BenchError):
            measured(METRIC_COLD_START, 1.0, command="", environment={"a": "b"})
        with self.assertRaises(BenchError):
            measured(METRIC_COLD_START, 1.0, command="cmd", environment={})

    def test_measured_outcome_refuses_a_non_finite_value(self) -> None:
        for value in (None, float("nan"), float("inf"), float("-inf")):
            with self.assertRaises(BenchError):
                measured(METRIC_COLD_START, value, command="cmd", environment={"a": "b"})

    def test_measured_outcome_refuses_an_unknown_metric(self) -> None:
        with self.assertRaises(BenchError):
            measured("made_up_metric", 1.0, command="cmd", environment={"a": "b"})

    def test_skipped_outcome_carries_no_number_and_needs_a_reason(self) -> None:
        outcome = skipped(
            METRIC_LOCAL_INFERENCE,
            command="curl -s -o /dev/null -w '%{time_total}' http://127.0.0.1:9/",
            environment={"url": "http://127.0.0.1:9/"},
            reason="endpoint unreachable (rc=7)",
        )
        self.assertEqual(outcome.status, "skipped")
        self.assertIsNone(outcome.value)
        self.assertIn("unreachable", outcome.reason)
        with self.assertRaises(BenchError):
            skipped(METRIC_LOCAL_INFERENCE, command="cmd", environment={"a": "b"}, reason="")
        with self.assertRaises(BenchError):
            MetricOutcome(
                name=METRIC_LOCAL_INFERENCE,
                status="skipped",
                unit="%",
                command="cmd",
                environment={"a": "b"},
                value=3.0,
                reason="pretend skip",
            )


class AggregateTests(unittest.TestCase):
    def test_aggregate_computes_variance_over_two_runs(self) -> None:
        aggregate = aggregate_metric(
            [
                measured(METRIC_COLD_START, 10.0, command="c", environment={"run": "1"}),
                measured(METRIC_COLD_START, 14.0, command="c", environment={"run": "2"}),
            ]
        )
        self.assertEqual(aggregate.status, "measured")
        self.assertEqual(aggregate.runs, (10.0, 14.0))
        self.assertAlmostEqual(float(aggregate.mean or 0.0), 12.0)
        self.assertAlmostEqual(float(aggregate.median or 0.0), 12.0)
        self.assertAlmostEqual(float(aggregate.minimum or 0.0), 10.0)
        self.assertAlmostEqual(float(aggregate.maximum or 0.0), 14.0)
        self.assertAlmostEqual(
            float(aggregate.stdev or 0.0), statistics.stdev([10.0, 14.0])
        )
        self.assertAlmostEqual(
            float(aggregate.spread_pct or 0.0), (14.0 - 10.0) / 12.0 * 100.0
        )

    def test_aggregate_keeps_a_skip_without_numbers(self) -> None:
        aggregate = aggregate_metric(
            [
                skipped(
                    METRIC_RAM_OVERHEAD,
                    command="ps -o rss= -p <pid>",
                    environment={"a": "b"},
                    reason="emulator process not found",
                ),
                skipped(
                    METRIC_RAM_OVERHEAD,
                    command="ps -o rss= -p <pid>",
                    environment={"a": "b"},
                    reason="emulator process not found",
                ),
            ]
        )
        self.assertEqual(aggregate.status, "skipped")
        self.assertEqual(aggregate.runs, ())
        self.assertIsNone(aggregate.mean)
        self.assertIsNone(aggregate.stdev)
        self.assertIn("not found", aggregate.reason)

    def test_aggregate_refuses_mixed_measured_and_skipped_outcomes(self) -> None:
        with self.assertRaises(BenchError):
            aggregate_metric(
                [
                    measured(METRIC_CPU_OVERHEAD, 1.0, command="c", environment={"a": "b"}),
                    skipped(
                        METRIC_CPU_OVERHEAD,
                        command="c",
                        environment={"a": "b"},
                        reason="virsh missing",
                    ),
                ]
            )

    def test_aggregate_refuses_empty_or_mismatched_names(self) -> None:
        with self.assertRaises(BenchError):
            aggregate_metric([])
        with self.assertRaises(BenchError):
            aggregate_metric(
                [
                    measured(METRIC_COLD_START, 1.0, command="c", environment={"a": "b"}),
                    measured(METRIC_WARM_START, 2.0, command="c", environment={"a": "b"}),
                ]
            )


class ArtifactTests(unittest.TestCase):
    def test_artifact_carries_every_metric_with_command_environment_and_runs(self) -> None:
        artifact = build_artifact(
            passes=[fake_pass(1, cold=10.0), fake_pass(2, cold=14.0)],
            host={"hostname": "h"},
            driver_argv=["isolation_bench"],
            generated_at="2026-09-23T00:00:00Z",
            notes=["two consecutive passes"],
        )
        self.assertEqual(artifact["schema_version"], 1)
        self.assertEqual(set(artifact["metrics"]), set(METRIC_ORDER))
        cold = artifact["metrics"][METRIC_COLD_START]
        self.assertEqual(cold["status"], "measured")
        self.assertEqual(cold["unit"], "s")
        self.assertEqual(len(cold["runs"]), 2)
        self.assertAlmostEqual(float(cold["runs"][0]["value"]), 10.0)
        self.assertAlmostEqual(float(cold["stats"]["mean"]), 12.0)
        for entry in cold["runs"]:
            self.assertTrue(str(entry["command"]).strip())
            self.assertTrue(entry["environment"])
        self.assertEqual(artifact["notes"], ["two consecutive passes"])

    def test_artifact_records_a_skip_without_numbers(self) -> None:
        artifact = build_artifact(
            passes=[fake_pass(1, skip_inference=True), fake_pass(2, skip_inference=True)],
            host={},
            driver_argv=[],
            generated_at="t",
        )
        inference = artifact["metrics"][METRIC_LOCAL_INFERENCE]
        self.assertEqual(inference["status"], "skipped")
        self.assertNotIn("stats", inference)
        self.assertIsNone(inference["runs"][0]["value"])
        self.assertTrue(str(inference["runs"][0]["reason"]).strip())

    def test_artifact_refuses_a_pass_missing_a_metric(self) -> None:
        incomplete = [item for item in fake_pass(1) if item.name != METRIC_DISK_OVERHEAD]
        with self.assertRaises(BenchError):
            build_artifact(
                passes=[incomplete], host={}, driver_argv=[], generated_at="t"
            )
        with self.assertRaises(BenchError):
            build_artifact(passes=[], host={}, driver_argv=[], generated_at="t")

    def test_summary_lists_every_metric_with_its_value_and_command(self) -> None:
        artifact = build_artifact(
            passes=[fake_pass(1), fake_pass(2)],
            host={},
            driver_argv=[],
            generated_at="t",
        )
        summary = render_summary(artifact)
        for name in METRIC_ORDER:
            self.assertIn(name, summary)
        self.assertIn("mean=", summary)
        self.assertIn("command=", summary)
        self.assertIn("s", summary)

    def test_write_artifacts_writes_json_and_human_summary(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            json_path = Path(tmp) / "isolation-bench.json"
            summary_path = Path(tmp) / "isolation-bench.txt"
            artifact = build_artifact(
                passes=[fake_pass(1), fake_pass(2)],
                host={"hostname": "h"},
                driver_argv=["isolation_bench"],
                generated_at="t",
            )
            write_artifacts(
                json_path=json_path,
                summary_path=summary_path,
                artifact=artifact,
                summary=render_summary(artifact),
            )
            self.assertEqual(json.loads(json_path.read_text(encoding="utf-8")), artifact)
            self.assertIn(METRIC_COLD_START, summary_path.read_text(encoding="utf-8"))


class HarnessTests(unittest.TestCase):
    def test_harness_measures_every_metric_on_every_run_and_shows_variance(self) -> None:
        backend = FakeBenchBackend(
            {
                (METRIC_COLD_START, 1): measured(
                    METRIC_COLD_START, 10.0, command="c1", environment={"run": "1"}
                ),
                (METRIC_COLD_START, 2): measured(
                    METRIC_COLD_START, 14.0, command="c2", environment={"run": "2"}
                ),
            }
        )
        run = BenchHarness(
            2,
            backend,
            host_facts=lambda: {"hostname": "h"},
            driver_argv=["driver"],
            emit=lambda _line: None,
        ).run()
        self.assertTrue(run.ok)
        self.assertEqual(backend.ended, [1, 2])
        self.assertEqual(len(backend.calls), len(METRIC_ORDER) * 2)
        self.assertAlmostEqual(
            float(run.artifact["metrics"][METRIC_COLD_START]["stats"]["mean"]), 12.0
        )
        self.assertIn(METRIC_COLD_START, run.summary)

    def test_harness_refuses_a_backend_that_returns_the_wrong_metric(self) -> None:
        class WrongBackend(FakeBenchBackend):
            def measure(self, metric: str, run_index: int) -> MetricOutcome:
                return measured(METRIC_WARM_START, 1.0, command="c", environment={"a": "b"})

        with self.assertRaises(BenchError):
            BenchHarness(1, WrongBackend(), emit=lambda _line: None).run()

    def test_harness_reports_leftovers_as_a_failed_run(self) -> None:
        backend = FakeBenchBackend(leftovers=["overlay qcow2 left behind"])
        run = BenchHarness(
            1, backend, host_facts=dict, driver_argv=[], emit=lambda _line: None
        ).run()
        self.assertFalse(run.ok)
        self.assertIn("LEFTOVER", "\n".join(run.receipt.render()))

    def test_harness_cleans_up_when_a_metric_fails(self) -> None:
        backend = FakeBenchBackend(fail_on=METRIC_DISK_OVERHEAD)
        with self.assertRaises(BenchError):
            BenchHarness(2, backend, emit=lambda _line: None).run()
        self.assertEqual(backend.cleanup_calls, 1)


class MainTests(unittest.TestCase):
    def test_main_skips_without_kvm_and_writes_no_artifact(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "bench.json"
            summary = Path(tmp) / "bench.txt"
            lines: list[str] = []
            rc = main(
                ["--real", "--out", str(out), "--summary", str(summary)],
                env={"KVM_AVAILABLE": "1"},
                emit=lines.append,
                availability=lambda _source: "no /dev/kvm on this host",
                backend_factory=lambda *_a, **_k: FakeBenchBackend(),
            )
            self.assertEqual(rc, EXIT_SKIP)
            self.assertTrue(
                any(line.startswith("SKIP: no KVM") for line in lines), lines
            )
            self.assertFalse(out.exists())
            self.assertFalse(summary.exists())

    def test_main_probe_only_reports_ready(self) -> None:
        lines: list[str] = []
        rc = main(
            ["--probe-only"],
            env={"KVM_AVAILABLE": "1"},
            emit=lines.append,
            availability=lambda _source: None,
        )
        self.assertEqual(rc, EXIT_OK)
        self.assertIn("READY: KVM probe passed", lines)

    def test_main_requires_the_real_flag_and_an_out_path(self) -> None:
        rc = main(
            [],
            env={"KVM_AVAILABLE": "1"},
            emit=lambda _line: None,
            availability=lambda _source: None,
        )
        self.assertEqual(rc, EXIT_FAIL)
        rc = main(
            ["--real"],
            env={"KVM_AVAILABLE": "1"},
            emit=lambda _line: None,
            availability=lambda _source: None,
        )
        self.assertEqual(rc, EXIT_FAIL)

    def test_main_writes_json_and_summary_on_a_faked_run(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "bench.json"
            summary = Path(tmp) / "bench.txt"
            lines: list[str] = []
            rc = main(
                [
                    "--real",
                    "--runs",
                    "2",
                    "--out",
                    str(out),
                    "--summary",
                    str(summary),
                ],
                env={"KVM_AVAILABLE": "1"},
                emit=lines.append,
                availability=lambda _source: None,
                backend_factory=lambda *_a, **_k: FakeBenchBackend(),
            )
            self.assertEqual(rc, EXIT_OK)
            self.assertTrue(out.is_file())
            self.assertTrue(summary.is_file())
            self.assertIn("RESULT: PASS", lines)
            artifact = json.loads(out.read_text(encoding="utf-8"))
            self.assertEqual(set(artifact["metrics"]), set(METRIC_ORDER))

    def test_main_fails_without_writing_an_artifact_when_a_metric_fails(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "bench.json"
            lines: list[str] = []
            rc = main(
                ["--real", "--out", str(out)],
                env={"KVM_AVAILABLE": "1"},
                emit=lines.append,
                availability=lambda _source: None,
                backend_factory=lambda *_a, **_k: FakeBenchBackend(
                    fail_on=METRIC_DISK_OVERHEAD
                ),
            )
            self.assertEqual(rc, EXIT_FAIL)
            self.assertFalse(out.exists())
            self.assertIn("RESULT: FAIL", lines)

    def test_main_fails_without_writing_an_artifact_when_cleanup_leaves_leftovers(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "bench.json"
            rc = main(
                ["--real", "--out", str(out)],
                env={"KVM_AVAILABLE": "1"},
                emit=lambda _line: None,
                availability=lambda _source: None,
                backend_factory=lambda *_a, **_k: FakeBenchBackend(
                    leftovers=["domain igorek-session-x left defined"]
                ),
            )
            self.assertEqual(rc, EXIT_FAIL)
            self.assertFalse(out.exists())


class HelperTests(unittest.TestCase):
    def test_run_bounded_times_out_instead_of_hanging(self) -> None:
        started = time.monotonic()
        result = run_bounded(["sleep", "5"], timeout_s=0.2)
        elapsed = time.monotonic() - started
        self.assertEqual(result.returncode, 124)
        self.assertLess(elapsed, 3.0)

    def test_run_bounded_reports_a_missing_tool(self) -> None:
        result = run_bounded(["igorek-no-such-binary-xyz"], timeout_s=5.0)
        self.assertEqual(result.returncode, 127)

    def test_driver_command_names_the_exact_driver_and_step(self) -> None:
        command = driver_command(
            ["python", "tests/test_isolation_bench.py", "--real"], METRIC_COLD_START
        )
        self.assertIn("tests/test_isolation_bench.py", command)
        self.assertIn(METRIC_COLD_START, command)

    def test_render_metric_line_for_measured_and_skipped(self) -> None:
        line = render_metric_line(
            measured(METRIC_COLD_START, 1.5, command="c", environment={"a": "b"})
        )
        self.assertIn(METRIC_COLD_START, line)
        self.assertIn("1.500", line)
        line = render_metric_line(
            skipped(
                METRIC_RAM_OVERHEAD,
                command="c",
                environment={"a": "b"},
                reason="emulator process not found",
            )
        )
        self.assertIn("SKIP", line)
        self.assertIn("emulator process not found", line)

    def test_host_local_url_only_accepts_loopback(self) -> None:
        for url in (
            "http://127.0.0.1:11434/v1",
            "http://localhost:8080/v1",
            "http://[::1]:5000/v1",
        ):
            self.assertTrue(is_host_local_url(url), url)
        for url in (
            "https://api.openai.com/v1",
            "http://192.168.0.10:11434/v1",
            "http://10.0.2.2:11434/v1",
            "not a url",
        ):
            self.assertFalse(is_host_local_url(url), url)

    def test_collect_host_facts_reports_this_host_without_fabricating(self) -> None:
        facts = collect_host_facts()
        self.assertTrue(str(facts["hostname"]).strip())
        self.assertIsInstance(facts["kvm_device"], bool)
        self.assertTrue(str(facts["python"]).strip())
        self.assertIn("kernel", facts)

    def test_metric_order_matches_the_plan_metrics_and_units(self) -> None:
        self.assertEqual(
            METRIC_ORDER,
            (
                "cold_start",
                "warm_start",
                "ram_overhead",
                "cpu_overhead",
                "disk_overhead",
                "command_latency",
                "local_inference_impact",
                "workspace_attach_time",
            ),
        )
        self.assertEqual(set(METRIC_UNITS), set(METRIC_ORDER))


if __name__ == "__main__":
    sys.exit(main())
