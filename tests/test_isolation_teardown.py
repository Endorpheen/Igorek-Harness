#!/usr/bin/env python3
"""VM teardown and persistence policy for per-session KVM isolation (plan todo 26).

The lifecycle is ``cold -> starting -> warm -> idle -> stopped -> reaped`` with
an idle TTL and a session-stop trigger. A VM with an in-flight dispatch is never
torn down by the idle path (that is what keeps a checkpoint write from being cut
in half); a quiescent VM past its TTL is destroyed and reaped through the same
host-state release the stop/cancel path uses.

Persistence is host-authoritative and split into four documented classes: the
frozen base image (permanent), the per-session overlay (disposable), the shared
workspace (permanent, exported through the single virtiofs device) and the
in-guest caches (disposable). Checkpoints and ``session_changes`` are produced
inside the shared workspace path, so they land on the host and survive a VM
teardown; in-guest installs/temp files/caches die with the overlay.

Adversarial classes covered here:

* misleading success - every teardown case asserts the VM is actually gone
  (libvirt destroy/undefine counters, overlay absence, closed endpoint, released
  CID binding/lease/export/admission slot), never a truthy report alone;
* stale state - the lifecycle record moves to ``reaped`` and a restart builds a
  new VM generation instead of reusing a torn-down one;
* dirty worktree - every write in this suite stays under ``tmp_path`` and the
  workspace bytes are compared before/after each teardown;
* paused/in-flight work - a dispatch that is still running blocks the idle sweep
  and a checkpoint written during that window stays byte-identical;
* off-mode parity - isolation ``off`` starts no sweeper, tracks no lifecycle and
  refuses to create a VM.

Every external boundary is a fake (libvirt connection, ``qemu-img`` runner, vsock
channel, preflight, workspace policy, clock), so the suite runs without KVM.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch

REPO_ROOT = Path(
    os.environ.get("IGOREK_FACTORY_REPO", Path(__file__).resolve().parents[1])
)
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from factory.core.isolation import manager as manager_module  # noqa: E402
from factory.core.isolation.channel import (  # noqa: E402
    ChannelMessage,
    MessageKind,
    PeerCid,
    SessionId,
    SessionMap,
)
from factory.core.isolation.config import (  # noqa: E402
    MODE_KVM,
    MODE_OFF,
    HostCapabilities,
    IsolationConfig,
)
from factory.core.isolation.domain import (  # noqa: E402
    CommandResult,
    DomainSpec,
    domain_name,
    overlay_for_session,
)
from factory.core.isolation.errors import IsolationError  # noqa: E402
from factory.core.isolation.guest_runner import (  # noqa: E402
    GuestRunner,
    RunnerSeams,
    RunnerTuning,
)
from factory.core.isolation.guest_task import DEFAULT_GUEST_ROOTS  # noqa: E402
from factory.core.isolation.lifecycle import (  # noqa: E402
    LifecycleState,
    LifecycleTracker,
    TeardownDecision,
    TeardownTrigger,
    teardown_decision,
)
from factory.core.isolation.persistence import (  # noqa: E402
    DURABLE_DATA_DIRNAME,
    PERSISTENCE_POLICY,
    PersistenceClass,
    durable_data_root,
    persistence_rule,
    persistence_summary,
)
from factory.core.isolation.manager import (  # noqa: E402
    STOP_REASON_IDLE_TTL,
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
from factory.core.durable_root_scope import durable_root_within_workdir  # noqa: E402
from factory.core.checkpoint_manager import (  # noqa: E402
    DEFAULT_EXCLUDES,
    CheckpointManager,
    _shadow_repo_path,
)
from factory.core.session_changes import (  # noqa: E402
    TaskChangeTracker,
    _task_changes_path,
    is_internal_path,
)

SESSION_A = "sess-1"
TASK_ID = "task-1"
FAKE_CID = 71


# --------------------------------------------------------------------------- #
# Pure policy: lifecycle states, idle TTL decision, persistence classes
# --------------------------------------------------------------------------- #


class LifecyclePolicyTest(unittest.TestCase):
    """The tracker/policy module is pure Python and testable without a manager."""

    def test_state_chain_follows_cold_starting_warm_idle_stopped_reaped(self) -> None:
        # Given: a tracker with a controllable clock
        clock = FakeClock()
        tracker = LifecycleTracker(ttl_s=60.0, clock=clock)

        # Then: an unknown session is cold and has no record
        self.assertIs(tracker.state_of(SESSION_A), LifecycleState.COLD)
        self.assertIsNone(tracker.record_of(SESSION_A))

        # When: the VM creation and the first dispatch run their transitions
        tracker.begin_start(SESSION_A)
        self.assertIs(tracker.state_of(SESSION_A), LifecycleState.STARTING)
        tracker.mark_warm(SESSION_A)
        self.assertIs(tracker.state_of(SESSION_A), LifecycleState.WARM)
        tracker.begin_dispatch(SESSION_A)
        self.assertIs(tracker.state_of(SESSION_A), LifecycleState.WARM)
        self.assertEqual(tracker.record_of(SESSION_A).in_flight, 1)
        tracker.finish_dispatch(SESSION_A)
        self.assertIs(tracker.state_of(SESSION_A), LifecycleState.IDLE)
        self.assertEqual(tracker.record_of(SESSION_A).in_flight, 0)

        # When: the session stops, then the host state is fully released
        tracker.mark_stopped(SESSION_A, TeardownTrigger.SESSION_STOP)
        self.assertIs(tracker.state_of(SESSION_A), LifecycleState.STOPPED)
        tracker.mark_reaped(SESSION_A)
        self.assertIs(tracker.state_of(SESSION_A), LifecycleState.REAPED)
        self.assertEqual(
            tracker.record_of(SESSION_A).last_trigger, TeardownTrigger.SESSION_STOP
        )

        # Then: a restart is a new generation, not a revived old VM
        tracker.begin_start(SESSION_A)
        self.assertIs(tracker.state_of(SESSION_A), LifecycleState.STARTING)
        self.assertEqual(tracker.record_of(SESSION_A).generation, 2)

    def test_idle_expiry_selects_only_quiescent_vms(self) -> None:
        clock = FakeClock()
        tracker = LifecycleTracker(ttl_s=60.0, clock=clock)
        for session_id in ("idle", "in-flight"):
            tracker.begin_start(session_id)
            tracker.mark_warm(session_id)
        tracker.begin_dispatch("in-flight")
        tracker.begin_dispatch("idle")
        tracker.finish_dispatch("idle")

        # When: time passes beyond the TTL for the idle VM
        clock.advance(61.0)
        # And: a fresh VM finishes a dispatch at the current moment
        tracker.begin_start("fresh")
        tracker.mark_warm("fresh")
        tracker.begin_dispatch("fresh")
        tracker.finish_dispatch("fresh")

        # Then: only the quiescent, expired VM is selected
        self.assertEqual(tracker.idle_expired(), ("idle",))
        # And: the in-flight dispatch can never be swept
        self.assertEqual(tracker.record_of("in-flight").in_flight, 1)
        tracker.mark_stopped("in-flight", TeardownTrigger.SESSION_STOP)
        self.assertEqual(tracker.idle_expired(), ("idle",))

    def test_teardown_decision_keeps_in_flight_and_fresh_idle(self) -> None:
        clock = FakeClock()
        tracker = LifecycleTracker(ttl_s=30.0, clock=clock)
        tracker.begin_start(SESSION_A)
        tracker.mark_warm(SESSION_A)
        record = tracker.record_of(SESSION_A)

        # Then: a fresh warm VM is kept
        self.assertIs(
            teardown_decision(record, now=clock(), ttl_s=30.0), TeardownDecision.KEEP
        )
        # And: an in-flight VM is kept no matter how old
        tracker.begin_dispatch(SESSION_A)
        in_flight = tracker.record_of(SESSION_A)
        self.assertIs(
            teardown_decision(in_flight, now=clock() + 10_000.0, ttl_s=30.0),
            TeardownDecision.KEEP,
        )
        # And: a stopped/reaped record is never a sweep candidate
        tracker.mark_stopped(SESSION_A, TeardownTrigger.IDLE_TTL)
        stopped = tracker.record_of(SESSION_A)
        self.assertIs(
            teardown_decision(stopped, now=clock() + 10_000.0, ttl_s=30.0),
            TeardownDecision.KEEP,
        )
        # And: an idle VM past its TTL is stop-and-reap
        tracker.begin_start("other")
        tracker.mark_warm("other")
        clock.advance(31.0)
        idle = tracker.record_of("other")
        self.assertIs(
            teardown_decision(idle, now=clock(), ttl_s=30.0),
            TeardownDecision.STOP_AND_REAP,
        )

    def test_persistence_policy_documents_the_four_classes(self) -> None:
        # Then: every documented class is present exactly once
        self.assertEqual(
            set(PERSISTENCE_POLICY),
            {
                PersistenceClass.BASE_IMAGE,
                PersistenceClass.OVERLAY,
                PersistenceClass.WORKSPACE,
                PersistenceClass.CACHE,
            },
        )
        # And: only the base image and the shared workspace are durable
        self.assertTrue(persistence_rule(PersistenceClass.BASE_IMAGE).durable)
        self.assertFalse(persistence_rule(PersistenceClass.OVERLAY).durable)
        self.assertTrue(persistence_rule(PersistenceClass.WORKSPACE).durable)
        self.assertFalse(persistence_rule(PersistenceClass.CACHE).durable)
        # And: the summary renders every class with its durability
        summary = "\n".join(persistence_summary())
        for rule_class in PERSISTENCE_POLICY:
            self.assertIn(str(rule_class), summary)

    def test_cache_class_is_disposable_and_guest_only(self) -> None:
        rule = persistence_rule(PersistenceClass.CACHE)
        self.assertFalse(rule.durable)
        # Then: no cache location is inside the exported workspace (which would
        # make the cache durable on the host) or inside the durable root
        durable_root = durable_data_root(GUEST_WORKSPACE_PATH)
        for location in rule.locations:
            self.assertFalse(location.startswith(f"{GUEST_WORKSPACE_PATH}/"))
            self.assertNotEqual(location, GUEST_WORKSPACE_PATH)
            self.assertFalse(location.startswith(f"{durable_root}/"))

    def test_durable_data_root_is_inside_the_shared_workspace(self) -> None:
        # Then: the guest durable root is the hidden dir inside the export
        self.assertEqual(
            durable_data_root(GUEST_WORKSPACE_PATH),
            f"{GUEST_WORKSPACE_PATH}/{DURABLE_DATA_DIRNAME}",
        )
        # And: the host computes the same relative root from its own export path
        host_root = durable_data_root("/srv/workspaces/task-1")
        self.assertEqual(
            host_root, f"/srv/workspaces/task-1/{DURABLE_DATA_DIRNAME}"
        )
        # And: a workdir with a trailing slash normalizes to the same path
        self.assertEqual(
            durable_data_root("/workspace/"),
            f"{GUEST_WORKSPACE_PATH}/{DURABLE_DATA_DIRNAME}",
        )

    def test_durable_data_root_refuses_an_empty_workdir(self) -> None:
        with self.assertRaises(ValueError):
            durable_data_root("   ")


# --------------------------------------------------------------------------- #
# Manager fakes (no hypervisor, no KVM)
# --------------------------------------------------------------------------- #


class FakeClock:
    """Monotonic clock the tests advance by hand."""

    def __init__(self, start: float = 1_000.0) -> None:
        self._value = float(start)

    def __call__(self) -> float:
        return self._value

    def advance(self, seconds: float) -> None:
        self._value += float(seconds)


class CidAllocator:
    """Host-global CID allocator: one libvirt daemon hands out distinct CIDs."""

    def __init__(self, first: int = 64) -> None:
        self._next = first

    def take(self) -> int:
        cid = self._next
        self._next += 1
        return cid


class FakeDomainHandle:
    """Minimal ``libvirt.virDomain`` with lifecycle counters."""

    def __init__(self, name: str, cid: int) -> None:
        self._name = name
        self._cid = cid
        self.active = False
        self.create_calls = 0
        self.destroy_calls = 0
        self.undefine_calls = 0

    def name(self) -> str:
        return self._name

    def create(self) -> int:
        self.create_calls += 1
        self.active = True
        return 0

    def destroy(self) -> int:
        if not self.active:
            raise RuntimeError("domain is not running")
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
        return (
            "<domain><devices><vsock model='virtio'>"
            f"<cid auto='yes' address='{self._cid}'/>"
            "</vsock></devices></domain>"
        )


class FakeConnection:
    """Fake libvirt connection over one host-global CID allocator."""

    def __init__(self, allocator: CidAllocator) -> None:
        self._allocator = allocator
        self.defined: dict[str, FakeDomainHandle] = {}
        self.closed = False

    def defineXML(self, xml: str) -> FakeDomainHandle:
        import xml.etree.ElementTree as ET

        name = ET.fromstring(xml).findtext("name") or ""
        handle = FakeDomainHandle(name, self._allocator.take())
        self.defined[name] = handle
        return handle

    def lookupByName(self, name: str) -> FakeDomainHandle:
        if name not in self.defined:
            raise KeyError(f"no such domain: {name}")
        return self.defined[name]

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


class FakeEndpoint:
    """Fake host half of one guest connection."""

    def __init__(
        self,
        messages: list[ChannelMessage] | None = None,
        *,
        blocking: bool = False,
    ) -> None:
        self.sent: list[tuple[MessageKind, dict[str, Any]]] = []
        self._messages = list(messages or [])
        self._blocking = blocking
        self._release = threading.Event()
        self.closed = False

    def send(self, kind: MessageKind, payload: dict[str, Any]) -> None:
        self.sent.append((kind, dict(payload)))

    def recv_from_guest(self, timeout_s: float | None = None) -> ChannelMessage | None:
        if self._blocking and not self._release.is_set():
            self._release.wait(timeout=None if timeout_s is None else timeout_s)
        return self._messages.pop(0) if self._messages else None

    def release(self) -> None:
        self._release.set()

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


def enabled_config(root: Path, *, idle_ttl_s: int = 60) -> IsolationConfig:
    base = root / "base.qcow2"
    base.write_bytes(b"qcow2-placeholder")
    config = IsolationConfig.from_env(
        {
            "IGOREK_ISOLATION_MODE": MODE_KVM,
            "IGOREK_ISOLATION_BASE_IMAGE": str(base),
            "IGOREK_ISOLATION_OVERLAY_DIR": str(root / "overlays"),
            "IGOREK_ISOLATION_VSOCK_PORT": "5011",
            "IGOREK_ISOLATION_IDLE_TTL_S": str(idle_ttl_s),
        },
        data_root=root,
        capability_probe=lambda uri: HostCapabilities(True, True, True, True),
    )
    assert config.enabled, config.reason
    return config


def make_policy(root: Path) -> WorkspacePolicy:
    return WorkspacePolicy(
        home=root / "home",
        data_root=root / "data",
        host_uid=os.getuid(),
        host_gid=os.getgid(),
    )


def dispatch_payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "task_id": TASK_ID,
        "profile": "cc-test",
        "model": "test-model",
        "base_url": "http://127.0.0.1:8001",
        "api_format": "anthropic",
        "system_prompt": "system",
        "user_message": "user",
        "skills_text": "skills",
        "foundation_context": "foundation",
        "filesystem_mode": "workspace",
        "guard_mode": "on",
        "max_tokens": 4000,
        "timeout_s": 1200,
        "trailing_context_text": "",
        "current_session": SESSION_A,
        "source_platform": "mobile_bridge",
        "source_chat_id": "42",
        "input_files": [],
        "enabled_tools": [{"name": "Read"}],
        "history_messages": [],
        "checkpoint_config": {"enabled": True},
    }
    payload.update(overrides)
    return payload


def ok_result_message() -> ChannelMessage:
    return ChannelMessage(
        kind=MessageKind.RESULT,
        seq=1,
        payload={
            "status": "ok",
            "output_text": "done",
            "usage": {"prompt": 1, "completion": 2},
            "tool_calls_count": 1,
        },
    )


class ManagerHarness(unittest.TestCase):
    """Temp root, kvm config, a fake clock and fakes for every host boundary."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="igorek-teardown-")
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.workspace = self.root / "workspace"
        self.workspace.mkdir()
        self.overlay_dir = self.root / "overlays"
        self.clock = FakeClock()
        self.allocator = CidAllocator()
        self.connection = FakeConnection(self.allocator)
        self.qemu_img = FakeQemuImg()
        self.registry = FakeRegistry()
        self.session_map = SessionMap()
        self.leases = OverlayLeaseGuard()
        self.addCleanup(self.leases.close)

    def build_manager(
        self,
        *,
        config: IsolationConfig | None = None,
        registry: FakeRegistry | None = None,
    ) -> IsolationManager:
        def tracking_preflight(cfg: IsolationConfig) -> PreflightResult:
            return PreflightResult(ok=True, mode=cfg.mode, reason=REASON_OK)

        seams = ManagerSeams(
            connect=lambda uri: self.connection,
            runner=self.qemu_img,
            channel=registry if registry is not None else self.registry,
            session_map=self.session_map,
            workspace_policy=make_policy(self.root),
            overlay_leases=self.leases,
            preflight_fn=tracking_preflight,
            egress=fake_egress_builder,
            lifecycle_clock=self.clock,
        )
        return IsolationManager(config or enabled_config(self.root), seams=seams)

    def session(self, session_id: str = SESSION_A) -> SessionSpec:
        return SessionSpec(session_id=session_id, workspace_cwd=str(self.workspace))

    def domain_name_for(self, session_id: str = SESSION_A) -> str:
        return domain_name(
            DomainSpec(
                session_id=session_id,
                base_image=self.root / "base.qcow2",
                workspace_host_path=self.workspace,
                overlay_dir=self.overlay_dir,
            )
        )

    def overlay_for(self, session_id: str = SESSION_A) -> Path:
        return overlay_for_session(session_id, self.overlay_dir)

    def durable_root(self) -> Path:
        return self.workspace / DURABLE_DATA_DIRNAME

    def start_vm(self, manager: IsolationManager) -> Any:
        """Start the fake VM and materialize the overlay the VMM would own."""
        vm = manager.ensure_vm(self.session())
        overlay = Path(vm.domain.overlay or "")
        overlay.parent.mkdir(parents=True, exist_ok=True)
        overlay.write_bytes(b"qcow2-overlay")
        return vm

    def run_one_dispatch(
        self, manager: IsolationManager, *, result: ChannelMessage | None = None
    ) -> dict[str, Any]:
        """Dispatch one turn so the session reaches the idle state."""
        endpoint = FakeEndpoint([result if result is not None else ok_result_message()])
        self.registry.endpoints[SESSION_A] = endpoint
        return manager.run_task(
            {"task_id": TASK_ID, "metadata": {"session": SESSION_A}},
            str(self.workspace),
            dispatch_payload(artifact_workdir=str(self.root / "artifacts")),
        )

    def workspace_snapshot(self) -> dict[str, bytes]:
        return {
            str(path.relative_to(self.workspace)): path.read_bytes()
            for path in sorted(self.workspace.rglob("*"))
            if path.is_file()
        }

    def task(self, session_id: str = SESSION_A) -> dict[str, Any]:
        return {"task_id": TASK_ID, "metadata": {"session": session_id}}


# --------------------------------------------------------------------------- #
# (a) Idle TTL: stop + reap, never touching an in-flight dispatch
# --------------------------------------------------------------------------- #


class IdleTeardownTest(ManagerHarness):
    def test_idle_ttl_stops_and_reaps_the_vm_and_releases_host_state(self) -> None:
        # Given: a live session VM that finished a dispatch and went idle
        manager = self.build_manager()
        self.addCleanup(manager.close)
        vm = self.start_vm(manager)
        endpoint = FakeEndpoint([ok_result_message()])
        self.registry.endpoints[SESSION_A] = endpoint
        result = manager.run_task(
            self.task(),
            str(self.workspace),
            dispatch_payload(artifact_workdir=str(self.root / "artifacts")),
        )
        self.assertEqual(result["status"], "ok")
        self.assertIs(manager.lifecycle_state(SESSION_A), LifecycleState.IDLE)
        handle = self.connection.defined[self.domain_name_for()]
        overlay = self.overlay_for()
        self.assertTrue(overlay.exists())
        self.assertEqual(manager.capacity_snapshot().usage.vms, 1)

        # When: the idle TTL expires and the policy is applied
        self.clock.advance(61.0)
        reports = manager.reap_idle()

        # Then: the VM is actually gone, not merely reported gone
        self.assertEqual(len(reports), 1)
        self.assertEqual(reports[0].session_id, SESSION_A)
        self.assertEqual(reports[0].reason, STOP_REASON_IDLE_TTL)
        self.assertTrue(reports[0].domain_destroyed)
        self.assertTrue(reports[0].overlay_removed)
        self.assertEqual(handle.destroy_calls, 1)
        self.assertEqual(handle.undefine_calls, 1)
        self.assertEqual(handle.isActive(), 0)
        self.assertFalse(overlay.exists())
        self.assertTrue(endpoint.closed)
        self.assertEqual(manager.capacity_snapshot().usage.vms, 0)
        self.assertIsNone(self.session_map.session_for_cid(vm.peer_cid))
        self.assertEqual(tuple(self.session_map.sessions()), ())
        self.assertNotIn(SESSION_A, self.leases.held)
        # And: the lifecycle record is terminal for this generation
        self.assertIs(manager.lifecycle_state(SESSION_A), LifecycleState.REAPED)
        # And: a second sweep is a no-op, never a double teardown
        self.assertEqual(manager.reap_idle(), ())

    def test_in_flight_dispatch_blocks_the_idle_sweep_until_it_finishes(self) -> None:
        # Given: a dispatch that is still waiting for its guest result
        manager = self.build_manager()
        self.addCleanup(manager.close)
        endpoint = FakeEndpoint([ok_result_message()], blocking=True)
        self.registry.endpoints[SESSION_A] = endpoint
        results: list[dict[str, Any]] = []
        errors: list[BaseException] = []

        def dispatch() -> None:
            try:
                results.append(
                    manager.run_task(
                        self.task(),
                        str(self.workspace),
                        dispatch_payload(artifact_workdir=str(self.root / "artifacts")),
                    )
                )
            except BaseException as exc:  # reported to the test
                errors.append(exc)

        thread = threading.Thread(target=dispatch, daemon=True)
        thread.start()
        self.assertTrue(
            self._wait_for(
                lambda: manager.lifecycle_state(SESSION_A) is LifecycleState.WARM
                and manager.lifecycle.record_of(SESSION_A).in_flight == 1
            ),
            "dispatch never entered the in-flight state",
        )
        handle = self.connection.defined[self.domain_name_for()]
        overlay = self.overlay_for()
        self.assertTrue(overlay.exists())

        # When: the TTL passes while the dispatch is in flight
        self.clock.advance(600.0)
        reports = manager.reap_idle()

        # Then: the sweep refused to touch a VM that is still working
        self.assertEqual(reports, ())
        self.assertEqual(handle.destroy_calls, 0)
        self.assertEqual(handle.undefine_calls, 0)
        self.assertEqual(handle.isActive(), 1)
        self.assertTrue(overlay.exists())
        self.assertFalse(endpoint.closed)
        self.assertIs(manager.lifecycle_state(SESSION_A), LifecycleState.WARM)

        # When: the guest answers and the dispatch finishes
        endpoint.release()
        thread.join(timeout=5.0)
        self.assertFalse(thread.is_alive())
        self.assertEqual(errors, [])
        self.assertEqual(results[0]["status"], "ok")
        self.assertIs(manager.lifecycle_state(SESSION_A), LifecycleState.IDLE)

        # Then: the TTL now applies to the quiescent VM
        self.clock.advance(61.0)
        reports = manager.reap_idle()
        self.assertEqual([report.reason for report in reports], [STOP_REASON_IDLE_TTL])
        self.assertEqual(handle.destroy_calls, 1)
        self.assertFalse(overlay.exists())

    def test_checkpoint_write_in_flight_is_never_corrupted_by_a_sweep(self) -> None:
        # Given: an in-flight dispatch that is writing a checkpoint artifact
        manager = self.build_manager()
        self.addCleanup(manager.close)
        endpoint = FakeEndpoint([ok_result_message()], blocking=True)
        self.registry.endpoints[SESSION_A] = endpoint
        results: list[dict[str, Any]] = []
        thread = threading.Thread(
            target=lambda: results.append(
                manager.run_task(
                    self.task(),
                    str(self.workspace),
                    dispatch_payload(artifact_workdir=str(self.root / "artifacts")),
                )
            ),
            daemon=True,
        )
        thread.start()
        self.assertTrue(
            self._wait_for(
                lambda: manager.lifecycle.record_of(SESSION_A) is not None
                and manager.lifecycle.record_of(SESSION_A).in_flight == 1
            ),
            "dispatch never entered the in-flight state",
        )
        checkpoint = self.durable_root() / "checkpoints" / "abc123" / "HEAD"
        checkpoint.parent.mkdir(parents=True, exist_ok=True)
        payload = b"ref: refs/heads/baseline\n"
        checkpoint.write_bytes(payload)
        before = self.workspace_snapshot()

        # When: the TTL passes and the sweeper runs mid-write
        self.clock.advance(600.0)
        self.assertEqual(manager.reap_idle(), ())

        # Then: the checkpoint is byte-identical and the dispatch still finishes
        self.assertEqual(checkpoint.read_bytes(), payload)
        self.assertEqual(self.workspace_snapshot(), before)
        endpoint.release()
        thread.join(timeout=5.0)
        self.assertEqual(results[0]["status"], "ok")
        self.assertEqual(checkpoint.read_bytes(), payload)
        self.assertEqual(self.workspace_snapshot(), before)

    def test_idle_sweeper_thread_reaps_after_the_ttl(self) -> None:
        # Given: a production-style sweeper with a short interval and fake clock
        manager = self.build_manager()
        self.addCleanup(manager.close)
        self.start_vm(manager)
        self.run_one_dispatch(manager)
        handle = self.connection.defined[self.domain_name_for()]
        overlay = self.overlay_for()
        manager.start_idle_sweeper(interval_s=0.01)
        self.assertTrue(manager.sweeper_running)

        # When: the TTL expires
        self.clock.advance(61.0)

        # Then: the daemon applies the same policy without an explicit call
        self.assertTrue(
            self._wait_for(
                lambda: manager.lifecycle_state(SESSION_A) is LifecycleState.REAPED
            ),
            "the idle sweeper never reaped the expired VM",
        )
        self.assertEqual(handle.destroy_calls, 1)
        self.assertEqual(handle.undefine_calls, 1)
        self.assertFalse(overlay.exists())

        # And: stopping the sweeper is clean
        manager.stop_idle_sweeper()
        self.assertFalse(manager.sweeper_running)

    def test_from_env_starts_the_sweeper_for_an_enabled_config(self) -> None:
        config = enabled_config(self.root)
        with patch.object(
            manager_module.IsolationConfig, "from_env", return_value=config
        ):
            manager = IsolationManager.from_env({})
        self.addCleanup(manager.close)
        self.assertTrue(manager.sweeper_running)
        manager.stop_idle_sweeper()
        self.assertFalse(manager.sweeper_running)

    def _wait_for(self, predicate: Any, timeout_s: float = 5.0) -> bool:
        deadline = time.monotonic() + timeout_s
        while time.monotonic() < deadline:
            if predicate():
                return True
            time.sleep(0.01)
        return bool(predicate())


# --------------------------------------------------------------------------- #
# (b) Persistence: the workspace, trace and checkpoints survive; the overlay dies
# --------------------------------------------------------------------------- #


class PersistenceTest(ManagerHarness):
    def test_workspace_checkpoint_changes_and_trace_survive_teardown(self) -> None:
        # Given: a live VM and the durable artifacts of one turn
        manager = self.build_manager()
        self.addCleanup(manager.close)
        self.start_vm(manager)
        (self.workspace / "notes.txt").write_text("keep me", encoding="utf-8")
        durable = self.durable_root()
        checkpoint = durable / "checkpoints" / "deadbeef" / "HEAD"
        checkpoint.parent.mkdir(parents=True, exist_ok=True)
        checkpoint.write_bytes(b"ref: refs/heads/baseline\n")
        changes = durable / "session_changes" / f"{TASK_ID}.json"
        changes.parent.mkdir(parents=True, exist_ok=True)
        changes.write_text('{"task_id": "task-1"}', encoding="utf-8")
        artifacts = self.root / "artifacts"
        artifacts.mkdir()
        trace = artifacts / "cc_backend_trace.jsonl"
        trace.write_text('{"kind": "task.finish"}\n', encoding="utf-8")
        before = self.workspace_snapshot()

        # When: the VM is torn down through the idle path
        self.run_one_dispatch(manager)
        self.clock.advance(61.0)
        self.assertEqual(len(manager.reap_idle()), 1)
        self.assertFalse(self.overlay_for().exists())

        # Then: every persistent artifact is byte-identical on the host
        self.assertEqual(self.workspace_snapshot(), before)
        self.assertEqual(
            (self.workspace / "notes.txt").read_text(encoding="utf-8"), "keep me"
        )
        self.assertEqual(checkpoint.read_bytes(), b"ref: refs/heads/baseline\n")
        self.assertEqual(
            changes.read_text(encoding="utf-8"), '{"task_id": "task-1"}'
        )
        # And: the host trace kept its record of the turn (the relay may append
        # capability/limit records of its own, never truncate)
        self.assertIn('{"kind": "task.finish"}', trace.read_text(encoding="utf-8"))
        # And: the durable artifacts live inside the shared workspace path
        self.assertTrue(checkpoint.is_relative_to(self.workspace))
        self.assertTrue(changes.is_relative_to(self.workspace))

    def test_overlay_is_the_only_disposable_vm_layer_and_is_removed(self) -> None:
        manager = self.build_manager()
        self.addCleanup(manager.close)
        self.start_vm(manager)
        overlay = self.overlay_for()
        # Given: a simulated in-guest cache/install that only exists on the overlay
        overlay.write_bytes(b"qcow2-overlay" + b"guest-cache-bytes")

        # When: the session is stopped by the explicit stop trigger
        manager.stop(SESSION_A)

        # Then: the disposable layer (and everything on it) is gone
        self.assertFalse(overlay.exists())
        self.assertFalse(self.overlay_for().exists())
        # And: no cache directory was created on any host path of the session
        self.assertFalse((self.workspace / ".cache").exists())
        self.assertFalse((self.durable_root() / "cache").exists())
        self.assertFalse(persistence_rule(PersistenceClass.CACHE).durable)

    def test_no_guest_cache_path_is_inside_the_workspace_export(self) -> None:
        # Then: the cache class never names a path inside the export or the
        # durable root, so a cache can never ride the shared workspace out of a
        # teardown alive
        durable_root = durable_data_root(GUEST_WORKSPACE_PATH)
        for location in persistence_rule(PersistenceClass.CACHE).locations:
            self.assertFalse(location == GUEST_WORKSPACE_PATH)
            self.assertFalse(location.startswith(f"{GUEST_WORKSPACE_PATH}/"))
            self.assertFalse(location.startswith(f"{durable_root}/"))

    def test_durable_data_dir_is_internal_under_the_configured_durable_root(self) -> None:
        # Given: the workdir-relative durable root the guest runner configures
        # (todo 43 ties the exclusion to that root, not to a global dirname)
        durable_root = durable_root_within_workdir(
            workdir=GUEST_WORKSPACE_PATH,
            data_root=durable_data_root(GUEST_WORKSPACE_PATH),
        )
        self.assertEqual(durable_root, DURABLE_DATA_DIRNAME)

        # Then: the durable artifacts inside the workspace are never reported as
        # user file changes (they are runtime state, not the user's work)
        self.assertTrue(
            is_internal_path(
                f"{DURABLE_DATA_DIRNAME}/session_changes/task-1.json",
                durable_root=durable_root,
            )
        )
        self.assertTrue(is_internal_path(DURABLE_DATA_DIRNAME, durable_root=durable_root))
        self.assertFalse(is_internal_path("src/main.py", durable_root=durable_root))
        self.assertFalse(
            is_internal_path(f"src/{DURABLE_DATA_DIRNAME}/x", durable_root=durable_root)
        )
        self.assertFalse(is_internal_path(f"{DURABLE_DATA_DIRNAME}-other/x"))
        # And: off mode (no configured durable root) records the name as user data
        self.assertFalse(
            is_internal_path(f"{DURABLE_DATA_DIRNAME}/session_changes/task-1.json")
        )

    def test_checkpoint_excludes_skip_the_configured_durable_root(self) -> None:
        # Then: the global defaults are untouched, so off mode is byte-identical
        self.assertNotIn(f"{DURABLE_DATA_DIRNAME}/", DEFAULT_EXCLUDES)
        # And: the checkpoint shadow repo never snapshots its own durable root
        # (which lives inside the workspace) when one is configured there
        manager = CheckpointManager(data_root=durable_data_root(GUEST_WORKSPACE_PATH))
        self.assertIn(
            f"{DURABLE_DATA_DIRNAME}/",
            manager._shadow_excludes(GUEST_WORKSPACE_PATH),
        )


# --------------------------------------------------------------------------- #
# (c) Restart policy: reuse a live VM, recreate a fresh one after teardown
# --------------------------------------------------------------------------- #


class RestartTest(ManagerHarness):
    def test_live_vm_is_reused_without_a_second_define(self) -> None:
        manager = self.build_manager()
        self.addCleanup(manager.close)
        first = self.start_vm(manager)
        handle = self.connection.defined[self.domain_name_for()]

        # When: the same session asks for its VM again
        second = manager.ensure_vm(self.session())

        # Then: the live VM is reused, never re-defined
        self.assertIs(first, second)
        self.assertEqual(handle.create_calls, 1)
        self.assertEqual(len(self.connection.defined), 1)
        self.assertEqual(len(self.qemu_img.calls), 1)
        self.assertIs(manager.lifecycle_state(SESSION_A), LifecycleState.WARM)
        self.assertEqual(manager.lifecycle.record_of(SESSION_A).generation, 1)

    def test_restart_after_teardown_recreates_a_fresh_vm_over_the_same_workspace(
        self,
    ) -> None:
        # Given: a session whose first VM was reaped after the idle TTL
        manager = self.build_manager()
        self.addCleanup(manager.close)
        self.start_vm(manager)
        (self.workspace / "notes.txt").write_text("keep me", encoding="utf-8")
        self.run_one_dispatch(manager)
        self.clock.advance(61.0)
        self.assertEqual(len(manager.reap_idle()), 1)
        first_handle = self.connection.defined[self.domain_name_for()]
        self.assertEqual(first_handle.undefine_calls, 1)
        self.assertFalse(self.overlay_for().exists())

        # When: the session runs again
        vm = manager.ensure_vm(self.session())

        # Then: a fresh VM generation was created over the same workspace
        self.assertEqual(vm.spec.workspace_host_path, self.workspace)
        self.assertIs(manager.lifecycle_state(SESSION_A), LifecycleState.WARM)
        record = manager.lifecycle.record_of(SESSION_A)
        self.assertEqual(record.generation, 2)
        self.assertEqual(record.dispatches, 1)  # cumulative across generations
        self.assertEqual(len(self.connection.defined), 1)
        self.assertEqual(self.overlay_for().exists(), True)
        self.assertEqual(len(self.qemu_img.calls), 2)
        # And: the host workspace bytes are exactly what the user had
        self.assertEqual(
            (self.workspace / "notes.txt").read_text(encoding="utf-8"), "keep me"
        )
        # And: the new VM is usable (the old CID binding was released)
        result = self.run_one_dispatch(manager)
        self.assertEqual(result["status"], "ok")
        self.assertIs(manager.lifecycle_state(SESSION_A), LifecycleState.IDLE)

    def test_session_stop_trigger_reaps_the_vm_and_records_the_trigger(self) -> None:
        manager = self.build_manager()
        self.addCleanup(manager.close)
        self.start_vm(manager)
        handle = self.connection.defined[self.domain_name_for()]

        # When: the session-stop trigger fires while the VM is warm
        manager.stop(SESSION_A)

        # Then: the VM is gone and the trigger is recorded
        self.assertEqual(handle.destroy_calls, 1)
        self.assertEqual(handle.undefine_calls, 1)
        self.assertFalse(self.overlay_for().exists())
        self.assertIs(manager.lifecycle_state(SESSION_A), LifecycleState.REAPED)
        self.assertEqual(
            manager.lifecycle.record_of(SESSION_A).last_trigger,
            TeardownTrigger.SESSION_STOP,
        )
        # And: stopping an unknown session is a no-op, never a fabricated state
        manager.stop("sess-unknown")
        self.assertIs(manager.lifecycle_state("sess-unknown"), LifecycleState.COLD)

    def test_cancel_tears_down_the_idle_vm_and_records_the_stop(self) -> None:
        manager = self.build_manager()
        self.addCleanup(manager.close)
        self.start_vm(manager)
        endpoint = FakeEndpoint()
        self.registry.endpoints[SESSION_A] = endpoint
        handle = self.connection.defined[self.domain_name_for()]

        # When: the session is canceled (mobile Stop)
        report = manager.cancel(SESSION_A)

        # Then: the VM is destroyed and the lifecycle records the stop trigger
        self.assertTrue(report.domain_destroyed)
        self.assertTrue(report.overlay_removed)
        self.assertEqual(handle.destroy_calls, 1)
        self.assertEqual(handle.undefine_calls, 1)
        self.assertIs(manager.lifecycle_state(SESSION_A), LifecycleState.REAPED)
        self.assertEqual(
            manager.lifecycle.record_of(SESSION_A).last_trigger,
            TeardownTrigger.SESSION_STOP,
        )


# --------------------------------------------------------------------------- #
# (d) Off-mode parity: no lifecycle, no sweeper, no VM
# --------------------------------------------------------------------------- #


class OffModeTest(ManagerHarness):
    def test_off_mode_never_starts_a_sweeper_and_reap_idle_is_a_no_op(self) -> None:
        config = IsolationConfig.from_env({}, data_root=self.root)
        self.assertEqual(config.mode, MODE_OFF)
        manager = IsolationManager(config, seams=ManagerSeams(lifecycle_clock=self.clock))
        self.addCleanup(manager.close)

        # When: the sweeper is requested and the idle policy is applied
        manager.start_idle_sweeper(interval_s=0.01)

        # Then: nothing happens at all
        self.assertFalse(manager.sweeper_running)
        self.assertEqual(manager.reap_idle(), ())
        self.assertIs(manager.lifecycle_state(SESSION_A), LifecycleState.COLD)
        self.assertEqual(manager.lifecycle.snapshot(), ())
        self.assertFalse(self.overlay_dir.exists())

    def test_off_mode_refuses_a_vm_and_leaves_no_lifecycle_state(self) -> None:
        config = IsolationConfig.from_env({}, data_root=self.root)
        manager = IsolationManager(config, seams=ManagerSeams(lifecycle_clock=self.clock))
        self.addCleanup(manager.close)

        with self.assertRaises(IsolationError) as raised:
            manager.ensure_vm(self.session())

        self.assertEqual(raised.exception.code, "isolation_not_enabled")
        self.assertIs(manager.lifecycle_state(SESSION_A), LifecycleState.COLD)
        self.assertEqual(self.connection.defined, {})


# --------------------------------------------------------------------------- #
# (e) Guest half: the durable data root lands inside the shared workspace
# --------------------------------------------------------------------------- #


class FakeGuestSession:
    """Minimal ``PeerSession`` stand-in: one task frame, then silence."""

    def __init__(self, payload: dict[str, Any]) -> None:
        import queue

        self._inbox: queue.Queue[ChannelMessage | None] = queue.Queue()
        self._inbox.put(ChannelMessage(kind=MessageKind.TASK, seq=1, payload=payload))
        self._closed = threading.Event()
        self.sent: list[tuple[MessageKind, dict[str, Any]]] = []

    def read(self) -> ChannelMessage | None:
        import queue

        while not self._closed.is_set():
            try:
                return self._inbox.get(timeout=0.01)
            except queue.Empty:
                continue
        return None

    def send(self, kind: MessageKind, payload: dict[str, Any]) -> None:
        self.sent.append((kind, dict(payload)))

    def close(self) -> None:
        self._closed.set()


class DurableArtifactPlumbingTest(unittest.TestCase):
    """The durable root is what the checkpoint/change artifacts actually use."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="igorek-teardown-durable-")
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.workspace = self.root / "workspace"
        self.workspace.mkdir()
        self.durable = self.root / "durable-root"

    def test_checkpoint_shadow_repo_lands_under_the_configured_data_root(self) -> None:
        # Given: a workdir and a durable root outside the process data root
        (self.workspace / "file.txt").write_text("hello", encoding="utf-8")
        manager = CheckpointManager(data_root=str(self.durable))

        # When: a checkpoint is taken
        self.assertTrue(manager.ensure_checkpoint(working_dir=str(self.workspace)))

        # Then: the shadow repo is under the durable root, not the default one
        shadow_repos = list((self.durable / "checkpoints").glob("*/HEAD"))
        self.assertEqual(len(shadow_repos), 1)
        self.assertFalse(
            _shadow_repo_path(str(self.workspace)).exists(),
            "the checkpoint leaked into the process data root",
        )

    def test_session_changes_record_lands_under_the_configured_data_root(self) -> None:
        # Given: a change tracker pointed at the durable root
        tracker = TaskChangeTracker(
            task_id=TASK_ID, workdir=str(self.workspace), data_root=str(self.durable)
        )
        target = self.workspace / "a.txt"
        target.write_text("x", encoding="utf-8")

        # When: a write is recorded
        tracker.record_write(str(target), "x")

        # Then: the record is under the durable root, not the default one
        record = self.durable / "session_changes" / f"{TASK_ID}.json"
        self.assertTrue(record.exists())
        self.assertEqual(json.loads(record.read_text(encoding="utf-8"))["task_id"], TASK_ID)
        self.assertFalse(_task_changes_path(TASK_ID).exists())


class GuestDurableRootTest(unittest.TestCase):
    """The guest runner points the guest data root at the shared workspace."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="igorek-teardown-guest-")
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.workdir = self.root / "workspace"
        self.workdir.mkdir()
        self.artifacts = self.root / "artifacts"

    def _payload(self) -> dict[str, Any]:
        return {
            "payload_version": 1,
            "task_id": TASK_ID,
            "profile": "cc-test",
            "model": "test-model",
            "base_url": "http://127.0.0.1:8001",
            "api_format": "anthropic",
            "system_prompt": "system",
            "user_message": "user",
            "skills_text": "",
            "foundation_context": "",
            "filesystem_mode": "workspace",
            "guard_mode": "on",
            "max_tokens": 512,
            "timeout_s": 30,
            "workdir": str(self.workdir),
            "artifact_workdir": str(self.artifacts),
            "trailing_context_text": "",
        }

    def _run(self, executor: Any) -> FakeGuestSession:
        session = FakeGuestSession(self._payload())
        peer: Any = session
        runner = GuestRunner(
            peer,
            seams=RunnerSeams(executor=executor, apply_egress=_RecordingEgress()),
            tuning=RunnerTuning(guest_roots=DEFAULT_GUEST_ROOTS + (str(self.root),)),
        )
        try:
            self.assertEqual(runner.serve_one(), 0)
        finally:
            session.close()
        return session

    def test_guest_data_root_is_the_durable_root_inside_the_workspace(self) -> None:
        observed: dict[str, Any] = {}

        def executor(**kwargs: Any) -> dict[str, Any]:
            observed["data_root"] = kwargs.get("data_root")
            observed["workdir"] = kwargs["workdir"]
            return {
                "status": "ok",
                "output_text": "",
                "usage": {},
                "tool_calls_count": 0,
            }

        before = os.environ.get("IGOREK_FACTORY_HOME")
        session = self._run(executor)

        # Then: the executor is handed the durable root inside the workspace
        self.assertEqual(
            observed["data_root"], durable_data_root(str(self.workdir))
        )
        self.assertEqual(observed["data_root"], str(self.workdir / DURABLE_DATA_DIRNAME))
        self.assertTrue((self.workdir / DURABLE_DATA_DIRNAME).is_dir())
        self.assertTrue(
            (self.workdir / DURABLE_DATA_DIRNAME).is_relative_to(self.workdir)
        )
        # And: the runner never mutates the process-global data-root env
        self.assertEqual(os.environ.get("IGOREK_FACTORY_HOME"), before)
        # And: the run still produced its result frame
        self.assertEqual(session.sent[-1][0], MessageKind.RESULT)

    def test_unwritable_durable_root_degrades_visibly_without_failing_the_turn(
        self,
    ) -> None:
        # Given: a file where the durable root directory must be
        (self.workdir / DURABLE_DATA_DIRNAME).write_text("not a dir", encoding="utf-8")
        observed: list[dict[str, Any]] = []

        def executor(**kwargs: Any) -> dict[str, Any]:
            observed.append(dict(kwargs))
            return {
                "status": "ok",
                "output_text": "",
                "usage": {},
                "tool_calls_count": 0,
            }

        session = self._run(executor)

        # Then: the turn still ran on the disposable guest data root
        self.assertEqual(len(observed), 1)
        self.assertIsNone(observed[0].get("data_root"))
        # And: the degradation is a visible trace event, never a silent skip
        records = [
            dict(payload["record"])
            for kind, payload in session.sent
            if kind is MessageKind.EVENT and isinstance(payload.get("record"), dict)
        ]
        self.assertEqual(
            [record["kind"] for record in records],
            ["isolation.durable_root_unavailable"],
        )
        # And: the run still produced its result frame
        self.assertEqual(session.sent[-1][0], MessageKind.RESULT)


class _RecordingEgress:
    """Fake egress applier: records the rulesets the runner enforced."""

    def __init__(self) -> None:
        self.applied: list[str] = []

    def __call__(self, ruleset: str) -> Any:
        from factory.core.isolation.egress_enforce import EGRESS_TABLE_REF, ApplyResult

        self.applied.append(ruleset)
        return ApplyResult(
            applied=True,
            verified=True,
            table=EGRESS_TABLE_REF,
            ruleset_sha256="0" * 64,
            rule_count=1,
            listed_rules=1,
        )


if __name__ == "__main__":
    unittest.main()
