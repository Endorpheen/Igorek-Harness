#!/usr/bin/env python3
"""todo 33 (kvm-session-isolation): admission control for concurrent session VMs.

The plan bounds a host to ``IGOREK_ISOLATION_MAX_VMS`` concurrent VMs with
RAM/disk quotas. A session that exceeds capacity must be *queued* (never
silently failed), must emit a visible capacity-wait status, and must start as
soon as capacity frees; the host must never over-commit. The same accounting is
host-wide, so a second manager (another profile's worker process) sees the same
budget, and a resource-exhaustion failure mid-start must refuse the VM cleanly
with an error status and release the acquired budget.

Coverage layers:

* ``CapacityLimitsTest`` - config -> budget derivation and fail-closed validation;
* ``AdmissionControllerTest`` - the flock-backed host-wide slot/usage machinery;
* ``ManagerAdmissionTest`` - the worker path: queue -> visible status ->
  starts when capacity frees; clean refusal on a failed start; released
  accounting on every teardown path; off-mode untouched;
* ``CapacityProbeTest`` - the mini-PC max-VM measurement recorder: it must
  measure when a KVM host is available and otherwise record an honest SKIP with
  a structural reason and no fabricated number.

Adversarial classes covered here:

* misleading success - the queued session's wait is asserted from the trace
  record written for the task, and its endpoint must have received no ``TASK``
  frame while it waits; the probe artifact must carry ``max_vms: null`` when
  skipped;
* stale state - capacity accounting is released on stop and on a failed start;
  a stale slot file left by a dead process is not counted and is reclaimed;
* over-commit - the number of active domains never exceeds MAX_VMS, and two
  managers over one overlay directory share one budget.
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import threading
import time
import unittest
from unittest import mock
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

REPO_ROOT = Path(
    os.environ.get("IGOREK_FACTORY_REPO", Path(__file__).resolve().parents[1])
)
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from factory.core.cc_backend_trace import TRACE_FILENAME  # noqa: E402
from factory.core.isolation.admission import (  # noqa: E402
    CODE_CONFLICT,
    CODE_STATE_CORRUPT,
    CODE_WAIT_CANCELED,
    CODE_WAIT_TIMEOUT,
    REASON_DISK,
    REASON_MAX_VMS,
    REASON_MEMORY,
    STATE_CANCELED,
    STATE_TIMEOUT,
    STATE_WAITING,
    AdmissionController,
    CapacityLimits,
    CapacitySnapshot,
    CapacityUsage,
    CapacityWait,
    emit_capacity_wait,
)
from factory.core.isolation.capacity_probe import (  # noqa: E402
    KVM_AVAILABLE_ENV,
    REASON_MEASURED,
    REASON_OPTIN_REQUIRED,
    REASON_REACHED_CEILING,
    CapacityProbeResult,
    ProbeAttempt,
    machine_label,
    physical_ceiling,
    run_probe,
    write_probe_artifacts,
)
from factory.core.isolation.channel import (  # noqa: E402
    ChannelMessage,
    MessageKind,
    PeerCid,
    SessionId,
    SessionMap,
)
from factory.core.isolation.config import (  # noqa: E402
    DEFAULT_ADMISSION_WAIT_S,
    ENV_HOST_DISK_GB,
    ENV_HOST_MEMORY_MB,
    MODE_KVM,
    HostCapabilities,
    IsolationConfig,
)
from factory.core.isolation.domain import (  # noqa: E402
    CommandResult,
    DomainSpec,
    domain_name,
)
from factory.core.isolation.errors import IsolationError  # noqa: E402
from factory.core.isolation.limits import memory_hard_limit_mib  # noqa: E402
from factory.core.isolation.manager import (  # noqa: E402
    IsolationManager,
    ManagerSeams,
    SessionSpec,
)
from factory.core.isolation.overlay_guard import OverlayLeaseGuard  # noqa: E402
from factory.core.isolation.preflight import REASON_OK, PreflightResult  # noqa: E402
from factory.core.isolation.session_egress import SessionEgress  # noqa: E402
from factory.core.isolation.workspace import (  # noqa: E402
    GUEST_WORKSPACE_PATH,
    WorkspacePolicy,
)

SESSION_1 = "sess-1"
SESSION_2 = "sess-2"
SESSION_3 = "sess-3"
FAKE_CID = 42


def fake_egress_builder(
    profile: str, base_url: str, *, host_local_ports: tuple[int, ...] = ()
) -> SessionEgress:
    """Deterministic stand-in for the host renderer (unit tests never resolve DNS)."""
    return SessionEgress(
        profile=profile,
        ruleset=f"# fake egress ruleset for {profile} ({base_url})\n",
        pinned_hosts=(),
        host_local_ports=tuple(host_local_ports),
    )


class FakeDomainHandle:
    """Minimal ``libvirt.virDomain`` stand-in with a switchable start failure."""

    def __init__(self, name: str, cid: int | None = FAKE_CID) -> None:
        self._name = name
        self._cid = cid
        self.active = False
        self.create_calls = 0
        self.destroy_calls = 0
        self.undefine_calls = 0
        self.fail_create = False

    def name(self) -> str:
        return self._name

    def create(self) -> int:
        if self.fail_create:
            raise RuntimeError("cannot allocate memory for the guest")
        self.create_calls += 1
        self.active = True
        return 0

    def destroy(self) -> int:
        self.destroy_calls += 1
        self.active = False
        return 0

    def shutdown(self) -> int:
        return 0

    def undefine(self) -> int:
        self.undefine_calls += 1
        return 0

    def isActive(self) -> int:
        return 1 if self.active else 0

    def XMLDesc(self, flags: int = 0) -> str:
        if self._cid is None:
            return (
                "<domain><devices><vsock model='virtio'>"
                "<cid auto='yes'/></vsock></devices></domain>"
            )
        return (
            "<domain><devices><vsock model='virtio'>"
            f"<cid auto='yes' address='{self._cid}'/>"
            "</vsock></devices></domain>"
        )


class FakeConnection:
    """Fake libvirt connection: remembers defined domains by their XML name."""

    def __init__(self, cid: int | None = FAKE_CID, *, fail_create: bool = False) -> None:
        self.cid = cid
        self.fail_create = fail_create
        self.defined: dict[str, FakeDomainHandle] = {}
        self.closed = False

    def defineXML(self, xml: str) -> FakeDomainHandle:
        name = ET.fromstring(xml).findtext("name") or ""
        cid = None if self.cid is None else self.cid + len(self.defined)
        handle = FakeDomainHandle(name, cid)
        handle.fail_create = self.fail_create
        self.defined[name] = handle
        return handle

    def lookupByName(self, name: str) -> FakeDomainHandle:
        return self.defined[name]

    def close(self) -> None:
        self.closed = True

    def active_domains(self) -> list[FakeDomainHandle]:
        return [handle for handle in self.defined.values() if handle.isActive()]


class FakeEndpoint:
    """Fake host half of one guest connection (``PeerSession`` stand-in)."""

    def __init__(self, messages: list[ChannelMessage] | None = None) -> None:
        self.sent: list[tuple[MessageKind, dict[str, Any]]] = []
        self._messages = list(messages or [])
        self.closed = False

    def send(self, kind: MessageKind, payload: dict[str, Any]) -> None:
        self.sent.append((kind, dict(payload)))

    def recv_from_guest(self, timeout_s: float | None = None) -> ChannelMessage | None:
        return self._messages.pop(0) if self._messages else None

    def close(self) -> None:
        self.closed = True


class FakeRegistry:
    """Fake channel registry keyed by the host-owned session id."""

    def __init__(self, endpoints: dict[str, FakeEndpoint] | None = None) -> None:
        self.endpoints = dict(endpoints or {})
        self.closed = False

    def session_for(self, session_id: SessionId) -> FakeEndpoint | None:
        return self.endpoints.get(str(session_id))

    def close(self) -> None:
        self.closed = True


class FakeQemuImg:
    """``qemu-img`` runner that materializes the overlay it is asked to create."""

    def __init__(self) -> None:
        self.calls: list[list[str]] = []

    def __call__(self, argv: list[str]) -> CommandResult:
        self.calls.append([str(item) for item in argv])
        Path(str(argv[-2])).write_bytes(b"qcow2-overlay")
        return CommandResult(0)


def make_policy(root: Path) -> WorkspacePolicy:
    return WorkspacePolicy(
        home=root / "home",
        data_root=root / "data",
        host_uid=os.getuid(),
    )


def enabled_config(root: Path, **env_overrides: str) -> IsolationConfig:
    base = root / "base.qcow2"
    base.write_bytes(b"qcow2-placeholder")
    env = {
        "IGOREK_ISOLATION_MODE": MODE_KVM,
        "IGOREK_ISOLATION_BASE_IMAGE": str(base),
        "IGOREK_ISOLATION_OVERLAY_DIR": str(root / "overlays"),
        "IGOREK_ISOLATION_VSOCK_PORT": "5005",
    }
    env.update(env_overrides)
    config = IsolationConfig.from_env(
        env,
        data_root=root,
        capability_probe=lambda uri: HostCapabilities(True, True, True, True),
    )
    assert config.enabled, config.reason
    return config


def dispatch_payload(session_id: str, *, artifact_workdir: str | None = None) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "task_id": f"task-{session_id}",
        "profile": "cc-test",
        "model": "test-model",
        "base_url": "http://127.0.0.1:8001",
        "api_format": "anthropic",
        "system_prompt": "system prompt",
        "user_message": "user message",
        "skills_text": "",
        "foundation_context": "",
        "filesystem_mode": "workspace",
        "guard_mode": "on",
        "max_tokens": 4000,
        "timeout_s": 1200,
        "trailing_context_text": "",
        "current_session": session_id,
        "source_platform": "mobile_bridge",
        "source_chat_id": "42",
        "input_files": [],
        "enabled_tools": [{"name": "Read"}],
        "history_messages": [],
        "checkpoint_config": {"enabled": True},
    }
    if artifact_workdir is not None:
        payload["artifact_workdir"] = artifact_workdir
    return payload


def result_message(output_text: str) -> ChannelMessage:
    return ChannelMessage(
        kind=MessageKind.RESULT,
        seq=1,
        payload={
            "status": "ok",
            "output_text": output_text,
            "usage": {"prompt": 1, "completion": 2, "lat_ms_p50": 3},
            "tool_calls_count": 1,
        },
    )


def read_trace_records(directory: Path, kind: str) -> list[dict[str, Any]]:
    path = Path(directory) / TRACE_FILENAME
    if not path.exists():
        return []
    records: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(record, dict) and record.get("kind") == kind:
            records.append(record)
    return records


def wait_for_trace_record(directory: Path, kind: str, timeout: float = 5.0) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        records = read_trace_records(directory, kind)
        if records:
            return records[0]
        time.sleep(0.02)
    raise AssertionError(f"no {kind!r} trace record appeared under {directory}")


def wait_until(predicate, timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return False


class CapacityLimitsTest(unittest.TestCase):
    """The aggregate budget: derived from MAX_VMS, overridable, fail-closed."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="igorek-admission-limits-")
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def test_budget_derives_from_max_vms_and_per_session_limits(self) -> None:
        # Given: a config with MAX_VMS=2 and the default per-session limits
        config = enabled_config(self.root, IGOREK_ISOLATION_MAX_VMS="2")

        # When: the admission budget is derived from it
        limits = CapacityLimits.from_config(config)

        # Then: it is exactly max_vms VMs of guest memory + VMM overhead and disk caps
        self.assertEqual(limits.max_vms, 2)
        self.assertEqual(
            limits.memory_mib, 2 * memory_hard_limit_mib(config.session_memory_mb)
        )
        self.assertEqual(limits.disk_gib, 2 * config.session_disk_gb)

    def test_budget_honours_explicit_host_overrides(self) -> None:
        # Given: explicit RAM/disk budgets below the derived totals
        config = enabled_config(
            self.root,
            IGOREK_ISOLATION_MAX_VMS="4",
            IGOREK_ISOLATION_SESSION_MEMORY_MB="1024",
            IGOREK_ISOLATION_SESSION_DISK_GB="4",
            IGOREK_ISOLATION_HOST_MEMORY_MB="2560",
            IGOREK_ISOLATION_HOST_DISK_GB="12",
        )

        # When: the budget is derived
        limits = CapacityLimits.from_config(config)

        # Then: the operator's budget wins (the stricter ceiling)
        self.assertEqual(limits.max_vms, 4)
        self.assertEqual(config.host_memory_mb, 2560)
        self.assertEqual(config.host_disk_gb, 12)
        self.assertEqual(limits.memory_mib, 2560)
        self.assertEqual(limits.disk_gib, 12)

    def test_admission_wait_default_and_override(self) -> None:
        default_config = enabled_config(self.root)
        override = enabled_config(
            self.root, IGOREK_ISOLATION_ADMISSION_WAIT_S="30"
        )
        self.assertEqual(default_config.admission_wait_s, DEFAULT_ADMISSION_WAIT_S)
        self.assertEqual(override.admission_wait_s, 30)

    def test_memory_budget_below_one_vm_fails_closed(self) -> None:
        # Given: a RAM budget smaller than a single VM's footprint
        config = IsolationConfig.from_env(
            {
                "IGOREK_ISOLATION_MODE": MODE_KVM,
                "IGOREK_ISOLATION_BASE_IMAGE": str(self._base_image()),
                "IGOREK_ISOLATION_SESSION_MEMORY_MB": "2048",
                "IGOREK_ISOLATION_HOST_MEMORY_MB": "1024",
            },
            data_root=self.root,
            capability_probe=lambda uri: HostCapabilities(True, True, True, True),
        )

        # Then: kvm mode is refused with the exact env name, never clamped silently
        self.assertFalse(config.enabled)
        self.assertEqual(config.reason, f"invalid_config:{ENV_HOST_MEMORY_MB}")

    def test_disk_budget_below_one_vm_fails_closed(self) -> None:
        config = IsolationConfig.from_env(
            {
                "IGOREK_ISOLATION_MODE": MODE_KVM,
                "IGOREK_ISOLATION_BASE_IMAGE": str(self._base_image()),
                "IGOREK_ISOLATION_SESSION_DISK_GB": "8",
                "IGOREK_ISOLATION_HOST_DISK_GB": "4",
            },
            data_root=self.root,
            capability_probe=lambda uri: HostCapabilities(True, True, True, True),
        )

        self.assertFalse(config.enabled)
        self.assertEqual(config.reason, f"invalid_config:{ENV_HOST_DISK_GB}")

    def _base_image(self) -> Path:
        image = self.root / "quota-base.qcow2"
        image.write_bytes(b"qcow2")
        return image


class AdmissionControllerTest(unittest.TestCase):
    """Host-wide slot accounting: bounded, idempotent, self-healing."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="igorek-admission-")
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.directory = self.root / "admission"

    def controller(
        self,
        *,
        max_vms: int = 2,
        memory_mib: int = 10_000,
        disk_gib: int = 100,
        wait_timeout_s: float = 3.0,
        poll_interval_s: float = 0.02,
    ) -> AdmissionController:
        return AdmissionController(
            CapacityLimits(max_vms=max_vms, memory_mib=memory_mib, disk_gib=disk_gib),
            directory=self.directory,
            wait_timeout_s=wait_timeout_s,
            poll_interval_s=poll_interval_s,
        )

    def test_snapshot_tracks_vms_memory_and_disk(self) -> None:
        # Given: a controller with a 2-VM budget
        controller = self.controller()
        self.addCleanup(controller.close)

        # When: one session is admitted with a 2 GiB footprint
        controller.acquire("sess-a", memory_mib=2048, disk_gib=8)

        # Then: the snapshot reflects exactly that usage and the free headroom
        snapshot = controller.snapshot()
        self.assertEqual(snapshot.usage, CapacityUsage(vms=1, memory_mib=2048, disk_gib=8))
        self.assertEqual(snapshot.limits.max_vms, 2)
        self.assertEqual(snapshot.free_vms, 1)
        self.assertEqual(snapshot.free_memory_mib, 7952)
        self.assertEqual(snapshot.free_disk_gib, 92)

    def test_second_admit_fills_the_budget_and_a_third_waits(self) -> None:
        # Given: both slots taken
        controller = self.controller(max_vms=2)
        self.addCleanup(controller.close)
        controller.acquire("sess-a", memory_mib=2048, disk_gib=8)
        controller.acquire("sess-b", memory_mib=2048, disk_gib=8)
        waits: list[CapacityWait] = []

        # When: a third session asks for capacity
        with self.assertRaises(IsolationError) as caught:
            controller.acquire(
                "sess-c",
                memory_mib=2048,
                disk_gib=8,
                on_wait=waits.append,
                timeout_s=0.3,
            )

        # Then: it waited with a visible status naming the exhausted limit and
        # never entered the budget; the timeout is explicit, not a silent failure
        self.assertEqual(caught.exception.code, CODE_WAIT_TIMEOUT)
        self.assertEqual(waits[0].state, STATE_WAITING)
        self.assertEqual(waits[0].reason, REASON_MAX_VMS)
        self.assertEqual(waits[0].position, 1)
        self.assertEqual(waits[0].snapshot.usage.vms, 2)
        self.assertEqual(waits[-1].state, STATE_TIMEOUT)
        self.assertEqual(controller.snapshot().usage.vms, 2)

    def test_a_release_lets_the_waiter_start(self) -> None:
        # Given: a full budget and a session blocked on it
        controller = self.controller(max_vms=1)
        self.addCleanup(controller.close)
        controller.acquire("sess-a", memory_mib=2048, disk_gib=8)
        admitted: list[CapacitySnapshot] = []
        first_wait = threading.Event()

        def waiter() -> None:
            admitted.append(
                controller.acquire(
                    "sess-b",
                    memory_mib=2048,
                    disk_gib=8,
                    on_wait=lambda wait: first_wait.set(),
                    timeout_s=5.0,
                )
            )

        thread = threading.Thread(target=waiter, daemon=True)
        thread.start()
        self.assertTrue(first_wait.wait(5.0), "the waiter never reported a wait")

        # When: the holder releases capacity
        self.assertTrue(controller.release("sess-a"))
        thread.join(timeout=5.0)

        # Then: the queued session is admitted, exactly once
        self.assertFalse(thread.is_alive())
        self.assertEqual(len(admitted), 1)
        self.assertEqual(controller.snapshot().usage.vms, 1)
        self.assertEqual(controller.held_sessions, ("sess-b",))

    def test_acquisition_is_idempotent_per_session(self) -> None:
        controller = self.controller()
        self.addCleanup(controller.close)

        controller.acquire("sess-a", memory_mib=2048, disk_gib=8)
        controller.acquire("sess-a", memory_mib=2048, disk_gib=8)

        self.assertEqual(controller.snapshot().usage.vms, 1)
        self.assertEqual(controller.snapshot().usage.memory_mib, 2048)

    def test_release_is_idempotent_and_unknown_sessions_are_a_noop(self) -> None:
        controller = self.controller()
        self.addCleanup(controller.close)
        controller.acquire("sess-a", memory_mib=2048, disk_gib=8)

        self.assertTrue(controller.release("sess-a"))
        self.assertFalse(controller.release("sess-a"))
        self.assertFalse(controller.release("never-admitted"))
        self.assertEqual(controller.snapshot().usage.vms, 0)

    def test_memory_quota_blocks_before_max_vms(self) -> None:
        # Given: room for 1 VM in RAM although MAX_VMS is 4
        controller = self.controller(max_vms=4, memory_mib=2304, disk_gib=100)
        self.addCleanup(controller.close)
        controller.acquire("sess-a", memory_mib=2304, disk_gib=8)
        waits: list[CapacityWait] = []

        # When: a second session asks for a VM
        with self.assertRaises(IsolationError):
            controller.acquire(
                "sess-b",
                memory_mib=2304,
                disk_gib=8,
                on_wait=waits.append,
                timeout_s=0.3,
            )

        # Then: the visible reason is the RAM quota, not the VM count
        self.assertEqual(waits[0].reason, REASON_MEMORY)
        self.assertEqual(waits[0].snapshot.usage.vms, 1)

    def test_disk_quota_blocks_before_max_vms(self) -> None:
        controller = self.controller(max_vms=4, memory_mib=100_000, disk_gib=8)
        self.addCleanup(controller.close)
        controller.acquire("sess-a", memory_mib=2048, disk_gib=8)
        waits: list[CapacityWait] = []

        with self.assertRaises(IsolationError):
            controller.acquire(
                "sess-b",
                memory_mib=2048,
                disk_gib=8,
                on_wait=waits.append,
                timeout_s=0.3,
            )

        self.assertEqual(waits[0].reason, REASON_DISK)

    def test_combined_limits_report_both_dimensions(self) -> None:
        controller = self.controller(max_vms=1, memory_mib=2304, disk_gib=8)
        self.addCleanup(controller.close)
        controller.acquire("sess-a", memory_mib=2304, disk_gib=8)
        waits: list[CapacityWait] = []

        with self.assertRaises(IsolationError):
            controller.acquire(
                "sess-b",
                memory_mib=2304,
                disk_gib=8,
                on_wait=waits.append,
                timeout_s=0.3,
            )

        self.assertEqual(
            waits[0].reason, f"{REASON_MAX_VMS}+{REASON_MEMORY}+{REASON_DISK}"
        )

    def test_abort_leaves_no_slot_and_reports_the_cancel(self) -> None:
        controller = self.controller(max_vms=1)
        self.addCleanup(controller.close)
        controller.acquire("sess-a", memory_mib=2048, disk_gib=8)
        waits: list[CapacityWait] = []
        errors: list[IsolationError] = []
        cancelled = threading.Event()

        # Given: a stop that arrives while the session is queued
        def should_abort() -> bool:
            return cancelled.is_set()

        def waiter() -> None:
            try:
                controller.acquire(
                    "sess-b",
                    memory_mib=2048,
                    disk_gib=8,
                    on_wait=waits.append,
                    should_abort=should_abort,
                    timeout_s=5.0,
                )
            except IsolationError as exc:
                errors.append(exc)

        thread = threading.Thread(target=waiter, daemon=True)
        thread.start()
        self.assertTrue(
            wait_until(lambda: any(w.state == STATE_WAITING for w in waits)),
            "the waiter never reported a wait",
        )
        cancelled.set()
        thread.join(timeout=5.0)

        # Then: it aborts cleanly, the visible status names the cancel and no
        # slot is leaked for a session that never started
        self.assertFalse(thread.is_alive())
        self.assertEqual(waits[-1].state, STATE_CANCELED)
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0].code, CODE_WAIT_CANCELED)
        self.assertEqual(controller.snapshot().usage.vms, 1)
        self.assertNotIn("sess-b", controller.held_sessions)

    def test_a_stale_slot_from_a_dead_process_is_not_counted_and_is_reclaimed(self) -> None:
        # Given: a slot file left behind by a crashed process (no live flock)
        self.directory.mkdir(parents=True, exist_ok=True)
        stale = self.directory / AdmissionController.slot_name("dead-session")
        stale.write_text(
            json.dumps(
                {
                    "session_id": "dead-session",
                    "pid": 999_999,
                    "memory_mib": 2304,
                    "disk_gib": 8,
                }
            ),
            encoding="utf-8",
        )
        controller = self.controller(max_vms=1)
        self.addCleanup(controller.close)

        # When: a new controller inspects the host
        usage_before = controller.snapshot().usage

        # Then: the dead holder frees no capacity for anyone and its file is gone
        self.assertEqual(usage_before.vms, 0)
        self.assertFalse(stale.exists())

        # And: the freed capacity is usable
        controller.acquire("sess-live", memory_mib=2304, disk_gib=8)
        self.assertEqual(controller.snapshot().usage.vms, 1)

    def test_two_controllers_share_one_host_budget(self) -> None:
        # Given: two managers (e.g. two worker processes) over one overlay dir
        limits = CapacityLimits(max_vms=1, memory_mib=2304, disk_gib=8)
        first = AdmissionController(limits, directory=self.directory, poll_interval_s=0.02)
        second = AdmissionController(
            limits, directory=self.directory, wait_timeout_s=5.0, poll_interval_s=0.02
        )
        self.addCleanup(first.close)
        self.addCleanup(second.close)
        first.acquire("sess-a", memory_mib=2304, disk_gib=8)
        admitted: list[CapacitySnapshot] = []
        waited = threading.Event()

        def waiter() -> None:
            admitted.append(
                second.acquire(
                    "sess-b",
                    memory_mib=2304,
                    disk_gib=8,
                    on_wait=lambda wait: waited.set(),
                    timeout_s=5.0,
                )
            )

        thread = threading.Thread(target=waiter, daemon=True)
        thread.start()
        self.assertTrue(waited.wait(5.0), "the second controller never saw the budget full")
        self.assertEqual(second.snapshot().usage.vms, 1)
        self.assertEqual(admitted, [])

        # When: the holder releases
        first.release("sess-a")
        thread.join(timeout=5.0)

        # Then: the sibling's queued session is admitted, and the budget is 1, never 2
        self.assertFalse(thread.is_alive())
        self.assertEqual(len(admitted), 1)
        self.assertEqual(second.snapshot().usage.vms, 1)

    def test_corrupt_live_slot_state_is_refused(self) -> None:
        # Given: a live slot whose recorded budget is unreadable
        self.directory.mkdir(parents=True, exist_ok=True)
        import fcntl

        path = self.directory / AdmissionController.slot_name("sess-broken")
        fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_CLOEXEC, 0o600)
        fcntl.flock(fd, fcntl.LOCK_EX)
        os.write(fd, b"{not json")
        controller = self.controller()
        self.addCleanup(controller.close)
        self.addCleanup(os.close, fd)

        # When/Then: an unreadable live holder is fail-closed, never counted as zero
        with self.assertRaises(IsolationError) as caught:
            controller.snapshot()
        self.assertEqual(caught.exception.code, CODE_STATE_CORRUPT)

    def test_emit_capacity_wait_writes_the_taxonomy_record(self) -> None:
        # Given: a wait status and an artifact workdir
        artifacts = self.root / "artifacts"
        artifacts.mkdir()
        wait = CapacityWait(
            session_id="sess-3",
            state=STATE_WAITING,
            reason=REASON_MAX_VMS,
            position=2,
            snapshot=CapacitySnapshot(
                limits=CapacityLimits(max_vms=2, memory_mib=4608, disk_gib=16),
                usage=CapacityUsage(vms=2, memory_mib=4608, disk_gib=16),
            ),
        )

        # When: it is emitted
        self.assertTrue(emit_capacity_wait(str(artifacts), wait))

        # Then: the mobile pipeline sees a structural isolation.capacity_wait record
        records = read_trace_records(artifacts, "isolation.capacity_wait")
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["subject"], "sess-3")
        self.assertEqual(records[0]["state"], STATE_WAITING)
        self.assertEqual(records[0]["reason"], REASON_MAX_VMS)
        self.assertEqual(records[0]["position"], 2)
        self.assertEqual(records[0]["usage_vms"], 2)
        self.assertEqual(records[0]["limit_vms"], 2)

    def test_emit_capacity_wait_without_a_workdir_is_not_a_crash(self) -> None:
        wait = CapacityWait(
            session_id="sess-3",
            state=STATE_WAITING,
            reason=REASON_MAX_VMS,
            position=1,
            snapshot=CapacitySnapshot(
                limits=CapacityLimits(max_vms=1, memory_mib=2304, disk_gib=8),
                usage=CapacityUsage(vms=1, memory_mib=2304, disk_gib=8),
            ),
        )
        self.assertFalse(emit_capacity_wait(None, wait))

    def test_conflicting_live_slot_for_the_same_session_is_refused(self) -> None:
        # Given: session X already runs under another controller (other process)
        limits = CapacityLimits(max_vms=2, memory_mib=10_000, disk_gib=100)
        first = AdmissionController(limits, directory=self.directory)
        second = AdmissionController(
            limits, directory=self.directory, wait_timeout_s=0.2
        )
        self.addCleanup(first.close)
        self.addCleanup(second.close)
        first.acquire("sess-x", memory_mib=2048, disk_gib=8)

        # When: a sibling controller tries to start the same session
        with self.assertRaises(IsolationError) as caught:
            second.acquire("sess-x", memory_mib=2048, disk_gib=8)

        # Then: it fails closed instead of double-booking one session
        self.assertEqual(caught.exception.code, CODE_CONFLICT)
        self.assertEqual(first.snapshot().usage.vms, 1)


class AdmissionHarness(unittest.TestCase):
    """Manager-level harness: fakes for every boundary, real admission logic."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="igorek-admission-mgr-")
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.workspaces = {}
        for session_id in (SESSION_1, SESSION_2, SESSION_3):
            workspace = self.root / f"workspace-{session_id}"
            workspace.mkdir()
            self.workspaces[session_id] = workspace
        self.connection = FakeConnection()
        self.runner = FakeQemuImg()
        self.registry = FakeRegistry()
        self.session_map = SessionMap()
        self.leases = OverlayLeaseGuard()
        self.addCleanup(self.leases.close)

    def build_manager(
        self,
        config: IsolationConfig | None = None,
        *,
        admission: AdmissionController | None = None,
        connection: FakeConnection | None = None,
    ) -> IsolationManager:
        seams = ManagerSeams(
            connect=lambda uri: connection or self.connection,
            runner=self.runner,
            channel=self.registry,
            session_map=self.session_map,
            workspace_policy=make_policy(self.root),
            overlay_leases=self.leases,
            preflight_fn=lambda cfg: PreflightResult(ok=True, mode=cfg.mode, reason=REASON_OK),
            egress=fake_egress_builder,
            admission=admission,
        )
        return IsolationManager(config or enabled_config(self.root), seams=seams)

    def session(self, session_id: str) -> SessionSpec:
        return SessionSpec(
            session_id=session_id, workspace_cwd=str(self.workspaces[session_id])
        )

    def start(self, manager: IsolationManager, session_id: str) -> None:
        manager.ensure_vm(self.session(session_id))

    def artifacts(self, session_id: str) -> Path:
        directory = self.root / f"artifacts-{session_id}"
        directory.mkdir(exist_ok=True)
        return directory

    def dispatch_in_thread(
        self,
        manager: IsolationManager,
        session_id: str,
        *,
        cancel_check=None,
    ) -> tuple[threading.Thread, dict[str, Any], list[BaseException]]:
        results: dict[str, Any] = {}
        errors: list[BaseException] = []

        def dispatch() -> None:
            try:
                results["value"] = manager.run_task(
                    {"task_id": f"task-{session_id}", "metadata": {"session": session_id}},
                    str(self.workspaces[session_id]),
                    dispatch_payload(
                        session_id, artifact_workdir=str(self.artifacts(session_id))
                    ),
                    cancel_check=cancel_check,
                )
            except BaseException as exc:  # surfaced to the test
                errors.append(exc)

        thread = threading.Thread(target=dispatch, daemon=True)
        thread.start()
        return thread, results, errors


class ManagerAdmissionTest(AdmissionHarness):
    def test_a_third_session_waits_visibly_then_starts_when_capacity_frees(self) -> None:
        # Given: MAX_VMS=2 with two live sessions
        manager = self.build_manager(
            enabled_config(self.root, IGOREK_ISOLATION_MAX_VMS="2")
        )
        self.start(manager, SESSION_1)
        self.start(manager, SESSION_2)
        endpoint = FakeEndpoint([result_message("done-s3")])
        self.registry.endpoints[SESSION_3] = endpoint

        # When: a third session dispatches a turn
        thread, results, errors = self.dispatch_in_thread(manager, SESSION_3)
        record = wait_for_trace_record(self.artifacts(SESSION_3), "isolation.capacity_wait")

        # Then: it is queued with a visible status and nothing was over-committed
        # (the derived budget is exactly two VMs, so all three dimensions are full)
        self.assertEqual(record["state"], STATE_WAITING)
        self.assertEqual(
            record["reason"], f"{REASON_MAX_VMS}+{REASON_MEMORY}+{REASON_DISK}"
        )
        self.assertEqual(record["subject"], SESSION_3)
        self.assertEqual(record["usage_vms"], 2)
        self.assertEqual(record["limit_vms"], 2)
        self.assertEqual(len(self.connection.active_domains()), 2)
        self.assertEqual(endpoint.sent, [])
        self.assertEqual(manager.capacity_snapshot().usage.vms, 2)
        self.assertEqual(thread.is_alive(), True)

        # When: capacity frees
        manager.stop(SESSION_1)
        thread.join(timeout=5.0)

        # Then: the queued session starts and completes, and the host still runs 2 VMs
        self.assertFalse(thread.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(results["value"]["status"], "ok")
        self.assertEqual(results["value"]["output_text"], "done-s3")
        self.assertEqual([kind for kind, _ in endpoint.sent], [MessageKind.TASK])
        self.assertEqual(manager.capacity_snapshot().usage.vms, 2)
        self.assertEqual(len(self.connection.active_domains()), 2)
        self.assertIn(domain_name_of(self.workspaces[SESSION_3]), self.connection.defined)

    def test_capacity_accounting_is_released_on_every_teardown_path(self) -> None:
        # Given: a single-VM budget and one live session
        manager = self.build_manager(
            enabled_config(self.root, IGOREK_ISOLATION_MAX_VMS="1")
        )
        self.start(manager, SESSION_1)
        self.assertEqual(manager.capacity_snapshot().usage.vms, 1)

        # When: the session is stopped
        manager.stop(SESSION_1)

        # Then: the budget is free again and another session can start
        self.assertEqual(manager.capacity_snapshot().usage.vms, 0)
        self.start(manager, SESSION_2)
        self.assertEqual(manager.capacity_snapshot().usage.vms, 1)

    def test_resource_exhaustion_mid_start_refuses_cleanly_and_releases_capacity(self) -> None:
        # Given: a host whose VMM cannot allocate memory for the guest
        failing = FakeConnection(fail_create=True)
        manager = self.build_manager(
            enabled_config(self.root, IGOREK_ISOLATION_MAX_VMS="1"),
            connection=failing,
        )

        # When: the session asks for a VM
        with self.assertRaises(IsolationError) as caught:
            self.start(manager, SESSION_1)

        # Then: the refusal is a structured start failure, the half-started VM is
        # gone and its capacity was released - the ledger is not left dirty
        self.assertEqual(caught.exception.code, "domain_start_failed")
        self.assertEqual(manager.capacity_snapshot().usage.vms, 0)
        self.assertEqual(failing.active_domains(), [])
        self.assertEqual(
            [handle.undefine_calls for handle in failing.defined.values()], [1]
        )
        self.assertEqual(list((self.root / "overlays").glob("*.qcow2")), [])

        # And: once the resource pressure is gone the same session starts cleanly
        failing.fail_create = False
        self.start(manager, SESSION_1)
        self.assertEqual(manager.capacity_snapshot().usage.vms, 1)
        self.assertEqual(len(failing.active_domains()), 1)

    def test_capacity_timeout_is_visible_and_leaves_no_slot(self) -> None:
        # Given: a full budget and a short admission wait
        admission = AdmissionController(
            CapacityLimits(max_vms=1, memory_mib=100_000, disk_gib=100),
            directory=self.root / "overlays" / "admission",
            wait_timeout_s=0.3,
            poll_interval_s=0.02,
        )
        self.addCleanup(admission.close)
        manager = self.build_manager(
            enabled_config(self.root, IGOREK_ISOLATION_MAX_VMS="1"),
            admission=admission,
        )
        self.start(manager, SESSION_1)

        # When: a second session dispatches a turn
        with self.assertRaises(IsolationError) as caught:
            manager.run_task(
                {"task_id": f"task-{SESSION_2}", "metadata": {"session": SESSION_2}},
                str(self.workspaces[SESSION_2]),
                dispatch_payload(
                    SESSION_2, artifact_workdir=str(self.artifacts(SESSION_2))
                ),
            )

        # Then: the timeout is a named structural failure with the wait status on
        # the trace, and the queued session never consumed a slot
        self.assertEqual(caught.exception.code, CODE_WAIT_TIMEOUT)
        records = read_trace_records(self.artifacts(SESSION_2), "isolation.capacity_wait")
        self.assertEqual([record["state"] for record in records], [STATE_WAITING, STATE_TIMEOUT])
        self.assertEqual(manager.capacity_snapshot().usage.vms, 1)

        # And: the budget is genuinely still owned by the running session only
        manager.stop(SESSION_1)
        self.assertEqual(manager.capacity_snapshot().usage.vms, 0)

    def test_a_queued_session_aborts_when_the_task_is_stopped(self) -> None:
        # Given: a queued session whose task the user stops while it waits
        admission = AdmissionController(
            CapacityLimits(max_vms=1, memory_mib=100_000, disk_gib=100),
            directory=self.root / "overlays" / "admission",
            wait_timeout_s=5.0,
            poll_interval_s=0.02,
        )
        self.addCleanup(admission.close)
        manager = self.build_manager(
            enabled_config(self.root, IGOREK_ISOLATION_MAX_VMS="1"),
            admission=admission,
        )
        self.start(manager, SESSION_1)
        stopped = threading.Event()

        # When: the queued dispatch notices the stop
        thread, _, errors = self.dispatch_in_thread(
            manager, SESSION_2, cancel_check=lambda: stopped.is_set()
        )
        wait_for_trace_record(self.artifacts(SESSION_2), "isolation.capacity_wait")
        stopped.set()
        thread.join(timeout=5.0)

        # Then: it never starts a VM, the status names the cancel and no slot leaks
        self.assertFalse(thread.is_alive())
        self.assertEqual(len(errors), 1)
        self.assertIsInstance(errors[0], IsolationError)
        self.assertEqual(errors[0].code, CODE_WAIT_CANCELED)
        canceled_records = [
            record
            for record in read_trace_records(
                self.artifacts(SESSION_2), "isolation.capacity_wait"
            )
            if record["state"] == STATE_CANCELED
        ]
        self.assertEqual(len(canceled_records), 1)
        self.assertEqual(len(self.connection.active_domains()), 1)
        self.assertEqual(manager.capacity_snapshot().usage.vms, 1)

    def test_two_managers_cannot_over_commit_the_shared_budget(self) -> None:
        # Given: two managers over one overlay dir, each with MAX_VMS=1
        config = enabled_config(self.root, IGOREK_ISOLATION_MAX_VMS="1")
        first = self.build_manager(config)
        second = self.build_manager(
            config,
            admission=AdmissionController(
                CapacityLimits.from_config(config),
                directory=self.root / "overlays" / "admission",
                wait_timeout_s=0.3,
                poll_interval_s=0.02,
            ),
        )
        self.start(first, SESSION_1)

        # When: the sibling manager tries to start its own session
        with self.assertRaises(IsolationError) as caught:
            self.start(second, SESSION_2)

        # Then: the host-wide budget refuses it and only one VM is live
        self.assertEqual(caught.exception.code, CODE_WAIT_TIMEOUT)
        self.assertEqual(len(self.connection.active_domains()), 1)
        self.assertEqual(first.capacity_snapshot().usage.vms, 1)
        self.assertEqual(second.capacity_snapshot().usage.vms, 1)

        # And: after the holder stops, the sibling's session starts
        first.stop(SESSION_1)
        self.start(second, SESSION_2)
        self.assertEqual(len(self.connection.active_domains()), 1)

    def test_off_mode_creates_no_admission_state(self) -> None:
        # Given: an off-mode manager
        off_config = IsolationConfig.from_env({}, data_root=self.root)
        manager = self.build_manager(off_config)

        # When: a session asks for a VM and the snapshot is read
        with self.assertRaises(IsolationError) as caught:
            self.start(manager, SESSION_1)
        snapshot = manager.capacity_snapshot()

        # Then: off mode is refused before any side effect, including admission
        self.assertEqual(caught.exception.code, "isolation_not_enabled")
        self.assertEqual(snapshot.usage.vms, 0)
        self.assertFalse(Path(off_config.overlay_dir).exists())
        self.assertEqual(self.runner.calls, [])

    def test_wait_record_names_the_exact_blocking_dimension_and_position(self) -> None:
        # Given: two queued sessions behind a full single-slot budget
        admission = AdmissionController(
            CapacityLimits(max_vms=1, memory_mib=100_000, disk_gib=100),
            directory=self.root / "overlays" / "admission",
            wait_timeout_s=5.0,
            poll_interval_s=0.02,
        )
        self.addCleanup(admission.close)
        manager = self.build_manager(
            enabled_config(self.root, IGOREK_ISOLATION_MAX_VMS="1"),
            admission=admission,
        )
        self.start(manager, SESSION_1)
        self.registry.endpoints[SESSION_2] = FakeEndpoint([result_message("done-s2")])
        self.registry.endpoints[SESSION_3] = FakeEndpoint([result_message("done-s3")])
        thread_2, results_2, _ = self.dispatch_in_thread(manager, SESSION_2)
        wait_for_trace_record(self.artifacts(SESSION_2), "isolation.capacity_wait")

        # When: a third session queues behind the second
        thread_3, results_3, _ = self.dispatch_in_thread(manager, SESSION_3)
        record = wait_for_trace_record(self.artifacts(SESSION_3), "isolation.capacity_wait")

        # Then: its visible position is 2 and the reason names MAX_VMS
        self.assertEqual(record["position"], 2)
        self.assertEqual(record["reason"], REASON_MAX_VMS)
        self.assertEqual(record["usage_vms"], 1)
        self.assertEqual(record["limit_vms"], 1)

        # When: the holder stops, exactly one queued session is admitted and its
        # warm VM keeps the slot, so the other stays queued - never failed
        manager.stop(SESSION_1)
        self.assertTrue(
            wait_until(lambda: bool(results_2) or bool(results_3)),
            "neither queued session started after capacity freed",
        )
        admitted = SESSION_2 if results_2 else SESSION_3
        queued = SESSION_3 if results_2 else SESSION_2
        self.assertEqual(manager.capacity_snapshot().usage.vms, 1)
        self.assertEqual(
            read_trace_records(self.artifacts(queued), "isolation.capacity_wait")[-1][
                "state"
            ],
            STATE_WAITING,
        )

        # When: the warm VM is torn down too, the queued session starts
        manager.stop(admitted)
        thread_2.join(timeout=5.0)
        thread_3.join(timeout=5.0)
        self.assertFalse(thread_2.is_alive())
        self.assertFalse(thread_3.is_alive())
        self.assertEqual(results_2["value"]["status"], "ok")
        self.assertEqual(results_3["value"]["status"], "ok")
        self.assertEqual(manager.capacity_snapshot().usage.vms, 1)


class FakeProbeManager:
    """Stand-in for ``IsolationManager`` used by the capacity probe measurement."""

    def __init__(self, *, fail_at: int | None = None) -> None:
        self.fail_at = fail_at
        self.started: list[str] = []
        self.stopped: list[str] = []
        self.closed = False

    def ensure_vm(self, session: SessionSpec) -> object:
        if self.fail_at is not None and len(self.started) + 1 == self.fail_at:
            raise IsolationError("domain_start_failed", "cannot allocate memory")
        self.started.append(session.session_id)
        return object()

    def stop(self, session_id: str) -> None:
        self.stopped.append(str(session_id))

    def close(self) -> None:
        self.closed = True


class CapacityProbeTest(unittest.TestCase):
    """The measurement artifact: honest numbers or an honest SKIP."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="igorek-capacity-probe-")
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.workspace = self.root / "workspace"
        self.workspace.mkdir()
        self.base_image = self.root / "base.qcow2"
        self.base_image.write_bytes(b"qcow2")

    def probe_env(self, **overrides: str) -> dict[str, str]:
        env = {
            "IGOREK_ISOLATION_MODE": MODE_KVM,
            "IGOREK_ISOLATION_BASE_IMAGE": str(self.base_image),
            "IGOREK_ISOLATION_OVERLAY_DIR": str(self.root / "overlays"),
            "IGOREK_ISOLATION_MAX_VMS": "4",
            # Pin per-VM size so the probe arithmetic below is independent of the
            # global default.
            "IGOREK_ISOLATION_SESSION_MEMORY_MB": "2048",
        }
        env.update(overrides)
        return env

    def ready_caps(self) -> HostCapabilities:
        return HostCapabilities(True, True, True, True)

    def test_missing_kvm_records_a_skip_with_no_number(self) -> None:
        # Given: a host without /dev/kvm
        host = FakeProbeManager()
        result = run_probe(
            env=self.probe_env(),
            capabilities=HostCapabilities(False, True, True, True, reason="kvm_missing"),
            manager_factory=lambda config: host,
            host_memory_mib=lambda: 8192,
            host_disk_gib=lambda: 64,
        )

        # Then: the verdict is a structural SKIP, nothing was booted and no
        # fabricated number is present anywhere in the artifact
        self.assertTrue(result.skipped)
        self.assertIsNone(result.max_vms)
        self.assertEqual(result.reason, "kvm_missing")
        self.assertEqual(host.started, [])
        artifact = result.to_artifact()
        self.assertIsNone(artifact["max_vms"])
        self.assertTrue(artifact["skipped"])
        self.assertIn("SKIP", result.render())

    def test_missing_optin_records_a_skip(self) -> None:
        # Given: a KVM-ready host, but the operator did not opt in
        result = run_probe(
            env=self.probe_env(),
            capabilities=self.ready_caps(),
            manager_factory=lambda config: FakeProbeManager(),
            host_memory_mib=lambda: 8192,
            host_disk_gib=lambda: 64,
        )

        self.assertTrue(result.skipped)
        self.assertEqual(result.reason, REASON_OPTIN_REQUIRED)
        self.assertIsNone(result.max_vms)

    def test_optin_boots_until_a_start_fails_and_records_the_count(self) -> None:
        # Given: a ready host with room for 3 VMs and a VMM that fails on the 3rd
        host = FakeProbeManager(fail_at=3)
        result = run_probe(
            env=self.probe_env(**{KVM_AVAILABLE_ENV: "1"}),
            capabilities=self.ready_caps(),
            max_vms=3,
            workspace=str(self.workspace),
            manager_factory=lambda config: host,
            host_memory_mib=lambda: 3 * memory_hard_limit_mib(2048),
            host_disk_gib=lambda: 64,
        )

        # Then: the measured maximum is the number that actually started, the
        # failing attempt is recorded, and every started VM is torn down
        self.assertFalse(result.skipped)
        self.assertEqual(result.max_vms, 2)
        self.assertEqual(result.reason, REASON_MEASURED)
        self.assertEqual([attempt.started for attempt in result.attempts], [True, True, False])
        self.assertEqual(result.attempts[-1].reason, "domain_start_failed")
        self.assertEqual(host.started, ["capacity-probe-01", "capacity-probe-02"])
        self.assertEqual(host.stopped, ["capacity-probe-01", "capacity-probe-02"])
        self.assertTrue(host.closed)
        artifact = result.to_artifact()
        self.assertEqual(artifact["max_vms"], 2)
        self.assertEqual(artifact["machine"], machine_label())
        self.assertTrue(
            any("factory.core.isolation.capacity_probe" in command for command in artifact["commands"])
        )

    def test_reaching_the_ceiling_is_reported_as_a_ceiling_not_a_measurement(self) -> None:
        host = FakeProbeManager()
        result = run_probe(
            env=self.probe_env(**{KVM_AVAILABLE_ENV: "1"}),
            capabilities=self.ready_caps(),
            max_vms=2,
            workspace=str(self.workspace),
            manager_factory=lambda config: host,
            host_memory_mib=lambda: 3 * memory_hard_limit_mib(2048),
            host_disk_gib=lambda: 64,
        )

        self.assertEqual(result.max_vms, 2)
        self.assertEqual(result.reason, REASON_REACHED_CEILING)
        self.assertIn("reached_ceiling", result.render())

    def test_physical_ceiling_is_the_minimum_of_memory_and_disk(self) -> None:
        self.assertEqual(
            physical_ceiling(
                memory_mib=3 * 2304,
                disk_gib=64,
                per_vm_memory_mib=2304,
                per_vm_disk_gib=8,
            ),
            3,
        )
        self.assertEqual(
            physical_ceiling(
                memory_mib=100_000,
                disk_gib=16,
                per_vm_memory_mib=2304,
                per_vm_disk_gib=8,
            ),
            2,
        )
        self.assertEqual(
            physical_ceiling(
                memory_mib=1000,
                disk_gib=64,
                per_vm_memory_mib=2304,
                per_vm_disk_gib=8,
            ),
            0,
        )

    def test_unknown_host_capacity_is_a_skip_not_a_guess(self) -> None:
        result = run_probe(
            env=self.probe_env(**{KVM_AVAILABLE_ENV: "1"}),
            capabilities=self.ready_caps(),
            workspace=str(self.workspace),
            manager_factory=lambda config: FakeProbeManager(),
            host_memory_mib=lambda: 0,
            host_disk_gib=lambda: 0,
        )

        self.assertTrue(result.skipped)
        self.assertIsNone(result.max_vms)
        self.assertEqual(result.reason, "host_capacity_unknown")

    def test_write_probe_artifacts_round_trips(self) -> None:
        result = CapacityProbeResult(
            hostname="example-host",
            machine="main-pc",
            ready=False,
            reason="kvm_missing",
            max_vms=None,
            ceiling=0,
            attempts=(ProbeAttempt(1, "capacity-probe-01", False, "domain_start_failed", "boom"),),
            commands=("test -e /dev/kvm",),
            kvm_available_optin=False,
        )
        out_json = self.root / "probe.json"
        out_summary = self.root / "probe.txt"

        write_probe_artifacts(result, out_json=out_json, out_summary=out_summary)

        payload = json.loads(out_json.read_text(encoding="utf-8"))
        self.assertEqual(payload["max_vms"], None)
        self.assertEqual(payload["reason"], "kvm_missing")
        self.assertEqual(payload["attempts"][0]["started"], False)
        self.assertIn("SKIP", out_summary.read_text(encoding="utf-8"))

    def test_machine_label_uses_configuration_or_a_portable_host_name(self) -> None:
        with mock.patch.dict(os.environ, {"IGOREK_MACHINE_LABEL": "test-node"}):
            self.assertEqual(machine_label("synthetic-host"), "test-node")
        with mock.patch.dict(os.environ, {"IGOREK_MACHINE_LABEL": ""}):
            self.assertEqual(machine_label("workstation-example"), "workstation-example")


def domain_name_of(workspace: Path) -> str:
    """The libvirt domain name the manager will define for a workspace's session."""
    return domain_name(
        DomainSpec(
            session_id=SESSION_3,
            base_image=Path("/nonexistent-base.qcow2"),
            workspace_host_path=workspace,
            overlay_dir=Path("/nonexistent-overlays"),
        )
    )


if __name__ == "__main__":
    unittest.main()
