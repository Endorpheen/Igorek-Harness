#!/usr/bin/env python3
"""Fail-safe behaviour of the isolation layer (plan todo 25).

A VMM that dies mid-task must not take the host or a sibling down: the task
becomes a terminal ``fail`` with a clear ``error_message``, the dead VM's
domain/overlay/endpoint/lease are released, and a sibling session keeps running.
A host process that died mid-task leaves a domain definition, an overlay and a
task row stuck in ``processing``; the gateway-start recovery reaps the first two
through the existing manager reaper and marks the orphaned task failed - while a
task legitimately parked in ``waiting_approval`` (whose VM remains warm,
todo 9) is never reaped and never failed.

Adversarial classes covered here:

* misleading success - every case asserts the exact terminal status plus the
  concrete cleanup (destroy/undefine counters, overlay absence, released lease,
  closed endpoint, released CID binding), never a truthy result;
* stale state - a leftover from a dead host process (domain + overlay + endpoint
  + CID binding) is removed by ``reap_orphans``/gateway start, and after a
  containment the reaper finds nothing;
* dirty workspace - the single workspace export keeps its exact bytes across a
  VM loss and across the startup recovery (no user data is deleted);
* paused task - a ``waiting_approval`` row is neither reaped nor failed, and
  still resumes;
* live sibling - a task whose overlay is still leased by a live process (or is
  younger than the grace window) is left alone.

Every external boundary is a fake (libvirt connection, ``qemu-img`` runner,
vsock channel, preflight, workspace policy), so the suite runs without KVM.
"""

from __future__ import annotations

import json
import os
import sqlite3
import sys
import tempfile
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

from factory.core import worker as worker_module  # noqa: E402
from factory.core.cli_tasks import create_task, resume_waiting_approval_task  # noqa: E402
from factory.core.isolation import startup as startup_module  # noqa: E402
from factory.core.isolation.channel import (  # noqa: E402
    ChannelError,
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
from factory.core.isolation.manager import (  # noqa: E402
    FAILURE_REASON_CHANNEL_LOST,
    FAILURE_REASON_VM_LOST,
    IsolationManager,
    ManagerSeams,
    SessionSpec,
)
from factory.core.isolation.overlay_guard import OverlayLeaseGuard  # noqa: E402
from factory.core.isolation.preflight import REASON_OK, PreflightResult  # noqa: E402
from factory.core.isolation.session_egress import SessionEgress  # noqa: E402
from factory.core.isolation.startup import (  # noqa: E402
    DEFAULT_STARTUP_GRACE_S,
    StartupRecovery,
    recover_isolation_startup,
)
from factory.core.isolation.workspace import WorkspacePolicy  # noqa: E402
from factory.core.runtime import get_db_path  # noqa: E402
from factory.core.schema import initialize_database  # noqa: E402
from factory.core.worker import Worker  # noqa: E402

FAKE_CID = 42
SESSION_A = "sess-1"
SESSION_B = "sess-2"
TASK_ID = "task-1"


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
    """Minimal ``libvirt.virDomain``: lifecycle counters + a settable live state.

    ``active`` is what ``isActive()`` reports; a test sets it to ``False`` to
    simulate the VMM process being killed (libvirt still knows the definition,
    the domain is simply not running any more).
    """

    def __init__(self, name: str, cid: int | None = FAKE_CID) -> None:
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
    """Fake libvirt connection: keeps defined domains and their CID allocation."""

    def __init__(self, cid: int | None = FAKE_CID) -> None:
        self.cid = cid
        self.defined: dict[str, FakeDomainHandle] = {}
        self.closed = False

    def defineXML(self, xml: str) -> FakeDomainHandle:
        import xml.etree.ElementTree as ET

        name = ET.fromstring(xml).findtext("name") or ""
        cid = None if self.cid is None else self.cid + len(self.defined)
        handle = FakeDomainHandle(name, cid)
        self.defined[name] = handle
        return handle

    def lookupByName(self, name: str) -> FakeDomainHandle:
        return self.defined[name]

    def close(self) -> None:
        self.closed = True


class FakeEndpoint:
    """Fake host half of one guest connection.

    ``recv_error`` makes ``recv_from_guest`` raise a real :class:`ChannelError`
    (the peer disconnected mid-dispatch); otherwise the queued messages are
    returned and ``None`` means "the peer closed / no result".
    """

    def __init__(
        self,
        messages: list[ChannelMessage] | None = None,
        *,
        recv_error: ChannelError | None = None,
        on_send=None,
    ) -> None:
        self.sent: list[tuple[MessageKind, dict[str, Any]]] = []
        self._messages = list(messages or [])
        self._recv_error = recv_error
        self._on_send = on_send
        self.closed = False

    def send(self, kind: MessageKind, payload: dict[str, Any]) -> None:
        self.sent.append((kind, dict(payload)))
        if self._on_send is not None:
            self._on_send()

    def recv_from_guest(self, timeout_s: float | None = None) -> ChannelMessage | None:
        if self._recv_error is not None:
            raise self._recv_error
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


def enabled_config(root: Path) -> IsolationConfig:
    base = root / "base.qcow2"
    base.write_bytes(b"qcow2-placeholder")
    config = IsolationConfig.from_env(
        {
            "IGOREK_ISOLATION_MODE": MODE_KVM,
            "IGOREK_ISOLATION_BASE_IMAGE": str(base),
            "IGOREK_ISOLATION_OVERLAY_DIR": str(root / "overlays"),
            "IGOREK_ISOLATION_VSOCK_PORT": "5005",
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


def result_message(payload_overrides: dict[str, Any] | None = None) -> ChannelMessage:
    payload: dict[str, Any] = {
        "status": "ok",
        "output_text": "done",
        "usage": {"prompt": 1, "completion": 2, "lat_ms_p50": 3},
        "tool_calls_count": 4,
    }
    payload.update(payload_overrides or {})
    return ChannelMessage(kind=MessageKind.RESULT, seq=1, payload=payload)


class FailsafeHarness(unittest.TestCase):
    """Temp root, enabled kvm config, fakes for every host boundary."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="igorek-failsafe-")
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.workspace = self.root / "workspace"
        self.workspace.mkdir()
        self.workspace_b = self.root / "workspace-b"
        self.workspace_b.mkdir()
        self.overlay_dir = self.root / "overlays"
        self.connection = FakeConnection()
        self.registry = FakeRegistry()
        self.session_map = SessionMap()
        self.leases = OverlayLeaseGuard()
        self.addCleanup(self.leases.close)

    def build_manager(
        self,
        *,
        config: IsolationConfig | None = None,
        connection: FakeConnection | None = None,
        registry: FakeRegistry | None = None,
    ) -> IsolationManager:
        def tracking_preflight(cfg: IsolationConfig) -> PreflightResult:
            return PreflightResult(ok=True, mode=cfg.mode, reason=REASON_OK)

        active_registry = registry if registry is not None else self.registry
        seams = ManagerSeams(
            connect=lambda uri: connection or self.connection,
            runner=lambda argv: CommandResult(0),
            channel=active_registry,
            session_map=self.session_map,
            workspace_policy=make_policy(self.root),
            overlay_leases=self.leases,
            preflight_fn=tracking_preflight,
            egress=fake_egress_builder,
        )
        return IsolationManager(config or enabled_config(self.root), seams=seams)

    def session(self, session_id: str = SESSION_A) -> SessionSpec:
        workspace = self.workspace if session_id == SESSION_A else self.workspace_b
        return SessionSpec(session_id=session_id, workspace_cwd=str(workspace))

    def domain_name_for(self, session_id: str = SESSION_A) -> str:
        workspace = self.workspace if session_id == SESSION_A else self.workspace_b
        return domain_name(
            DomainSpec(
                session_id=session_id,
                base_image=self.root / "base.qcow2",
                workspace_host_path=workspace,
                overlay_dir=self.overlay_dir,
            )
        )

    def overlay_for(self, session_id: str = SESSION_A) -> Path:
        return overlay_for_session(session_id, self.overlay_dir)

    def start_vm(
        self, manager: IsolationManager, session_id: str = SESSION_A
    ) -> Path:
        """Start the fake VM and materialize the overlay the VMM would own."""
        vm = manager.ensure_vm(self.session(session_id))
        overlay = Path(vm.domain.overlay or "")
        overlay.parent.mkdir(parents=True, exist_ok=True)
        overlay.write_bytes(b"overlay")
        return overlay

    def write_workspace_file(self, name: str = "notes.txt", text: str = "keep me") -> None:
        (self.workspace / name).write_text(text, encoding="utf-8")

    def workspace_snapshot(self) -> dict[str, bytes]:
        return {
            str(path.relative_to(self.workspace)): path.read_bytes()
            for path in sorted(self.workspace.rglob("*"))
            if path.is_file()
        }

    def task(self, session_id: str = SESSION_A, task_id: str = TASK_ID) -> dict[str, Any]:
        return {"task_id": task_id, "metadata": {"session": session_id}}


# --------------------------------------------------------------------------- #
# (a) VMM/guest loss mid-dispatch: fail the task, leave no host state behind
# --------------------------------------------------------------------------- #


class VmmLossTest(FailsafeHarness):
    def test_a_killed_vmm_fails_the_task_with_a_clear_error_and_no_leftovers(self) -> None:
        # Given: a live session VM and a workspace file the agent must not lose
        manager = self.build_manager()
        overlay = self.start_vm(manager)
        self.write_workspace_file()
        before = self.workspace_snapshot()
        endpoint = FakeEndpoint()
        self.registry.endpoints[SESSION_A] = endpoint
        handle = self.connection.defined[self.domain_name_for()]
        # And: the VMM process is killed - libvirt still knows the definition,
        # the live state reports the domain is no longer running
        endpoint._on_send = lambda: setattr(handle, "active", False)

        # When: the dispatch waits for a result that can never arrive
        result = manager.run_task(
            self.task(),
            str(self.workspace),
            dispatch_payload(artifact_workdir=str(self.root / "artifacts")),
        )

        # Then: the task is a terminal failure with a clear, non-empty reason
        self.assertEqual(result["status"], "fail")
        self.assertEqual(result["failure_reason"], FAILURE_REASON_VM_LOST)
        self.assertTrue(str(result["error"]).strip())
        self.assertIn(FAILURE_REASON_VM_LOST, str(result["error"]))
        self.assertNotEqual(result["status"], "ok")
        self.assertNotIn("approval_blocked_tools", result)
        # And: the dead VM is fully released (definition, overlay, routing, lease)
        self.assertEqual(handle.destroy_calls, 0)  # a dead domain cannot be destroyed
        self.assertEqual(handle.undefine_calls, 1)
        self.assertEqual(handle.isActive(), 0)
        self.assertFalse(overlay.exists())
        self.assertTrue(endpoint.closed)
        self.assertIsNone(self.session_map.session_for_cid(PeerCid(FAKE_CID)))
        self.assertFalse(manager.owns_overlay(SESSION_A))
        # And: the reaper finds nothing left (no stale state)
        self.assertEqual(manager.reap_orphans(), 0)
        # And: the user workspace is byte-for-byte unchanged
        self.assertEqual(self.workspace_snapshot(), before)

    def test_a_lost_channel_fails_the_task_and_destroys_the_live_vm(self) -> None:
        # Given: a live VM whose guest connection dies mid-dispatch
        manager = self.build_manager()
        overlay = self.start_vm(manager)
        endpoint = FakeEndpoint(
            recv_error=ChannelError("peer_disconnected", "guest socket closed")
        )
        self.registry.endpoints[SESSION_A] = endpoint
        handle = self.connection.defined[self.domain_name_for()]

        # When: the manager reads the closed channel
        result = manager.run_task(self.task(), str(self.workspace), dispatch_payload())

        # Then: the turn fails with the channel cause and the VM is destroyed
        self.assertEqual(result["status"], "fail")
        self.assertEqual(result["failure_reason"], FAILURE_REASON_CHANNEL_LOST)
        self.assertIn("peer_disconnected", str(result["error"]))
        self.assertEqual(handle.destroy_calls, 1)
        self.assertEqual(handle.undefine_calls, 1)
        self.assertFalse(overlay.exists())
        self.assertTrue(endpoint.closed)
        self.assertIsNone(self.session_map.session_for_cid(PeerCid(FAKE_CID)))

    def test_a_contained_session_can_start_a_fresh_vm(self) -> None:
        # Given: a session whose VM was lost once
        manager = self.build_manager()
        first = manager.ensure_vm(self.session())
        self.registry.endpoints[SESSION_A] = FakeEndpoint(
            on_send=lambda: setattr(self.connection.defined[self.domain_name_for()], "active", False)
        )
        manager.run_task(self.task(), str(self.workspace), dispatch_payload())

        # When: the session asks for a VM again
        second = manager.ensure_vm(self.session())

        # Then: a fresh VM starts with a fresh domain, overlay and routing entry
        self.assertIsNot(first.domain, second.domain)
        self.assertNotEqual(first.peer_cid, second.peer_cid)
        self.assertEqual(
            self.connection.defined[self.domain_name_for()].create_calls, 1
        )
        self.assertEqual(self.session_map.session_for_cid(second.peer_cid), SESSION_A)

    def test_a_lost_vm_leaves_a_sibling_session_completely_untouched(self) -> None:
        # Given: two independent sessions, one of which is about to lose its VMM
        manager = self.build_manager()
        overlay_a = self.start_vm(manager, SESSION_A)
        self.start_vm(manager, SESSION_B)
        self.registry.endpoints[SESSION_A] = FakeEndpoint()
        endpoint_b = FakeEndpoint([result_message({"output_text": "sibling done"})])
        self.registry.endpoints[SESSION_B] = endpoint_b
        self.registry.endpoints[SESSION_A]._on_send = lambda: setattr(
            self.connection.defined[self.domain_name_for(SESSION_A)], "active", False
        )
        sibling_handle = self.connection.defined[self.domain_name_for(SESSION_B)]

        # When: session A loses its VM and session B runs a normal turn
        failed = manager.run_task(
            self.task(SESSION_A, "task-a"),
            str(self.workspace),
            dispatch_payload(task_id="task-a", current_session=SESSION_A),
        )
        completed = manager.run_task(
            self.task(SESSION_B, "task-b"),
            str(self.workspace_b),
            dispatch_payload(task_id="task-b", current_session=SESSION_B),
        )

        # Then: A failed, B succeeded on the same manager
        self.assertEqual(failed["status"], "fail")
        self.assertFalse(overlay_a.exists())
        self.assertEqual(completed["status"], "ok")
        self.assertEqual(completed["output_text"], "sibling done")
        # And: B's VM is still running and was never torn down
        self.assertEqual(sibling_handle.destroy_calls, 0)
        self.assertEqual(sibling_handle.undefine_calls, 0)
        self.assertEqual(sibling_handle.isActive(), 1)
        self.assertTrue(self.overlay_for(SESSION_B).exists())
        self.assertEqual(manager.reap_orphans(), 0)


# --------------------------------------------------------------------------- #
# (b) orphan reaping: leftovers of a dead host process are removed
# --------------------------------------------------------------------------- #


class OrphanReapTest(FailsafeHarness):
    def _make_leftover(self, session_id: str, cid: int) -> tuple[Path, FakeDomainHandle, FakeEndpoint]:
        """State a dead host process leaves behind: overlay, domain, endpoint, CID."""
        spec = DomainSpec(
            session_id=session_id,
            base_image=self.root / "base.qcow2",
            workspace_host_path=self.workspace,
            overlay_dir=self.overlay_dir,
        )
        self.overlay_dir.mkdir(parents=True, exist_ok=True)
        overlay = overlay_for_session(session_id, self.overlay_dir)
        overlay.write_bytes(b"orphan-overlay")
        handle = self.connection.defineXML(f"<domain><name>{domain_name(spec)}</name></domain>")
        handle.create()
        endpoint = FakeEndpoint()
        self.registry.endpoints[session_id] = endpoint
        self.session_map.assign(PeerCid(cid), SessionId(session_id))
        return overlay, handle, endpoint

    def test_reap_removes_the_leftover_domain_overlay_endpoint_and_binding(self) -> None:
        # Given: leftovers no live process owns
        overlay, handle, endpoint = self._make_leftover("sess-dead", 777)
        manager = self.build_manager()

        # When: the reaper runs (on demand or at gateway start)
        reaped = manager.reap_orphans()

        # Then: the domain, the overlay and the routing entry are all gone
        self.assertEqual(reaped, 1)
        self.assertEqual(handle.destroy_calls, 1)
        self.assertEqual(handle.undefine_calls, 1)
        self.assertFalse(overlay.exists())
        self.assertTrue(endpoint.closed)
        self.assertIsNone(self.session_map.session_for_cid(PeerCid(777)))

    def test_reap_leaves_a_live_sibling_vm_and_its_routing_entry_alone(self) -> None:
        # Given: a live VM and a foreign leftover in the same overlay directory
        manager = self.build_manager()
        live_overlay = self.start_vm(manager, SESSION_A)
        live_handle = self.connection.defined[self.domain_name_for(SESSION_A)]
        self.registry.endpoints[SESSION_A] = FakeEndpoint()
        dead_overlay, dead_handle, dead_endpoint = self._make_leftover("sess-dead", 778)

        # When: the reaper runs
        reaped = manager.reap_orphans()

        # Then: only the true orphan is removed
        self.assertEqual(reaped, 1)
        self.assertFalse(dead_overlay.exists())
        self.assertEqual(dead_handle.undefine_calls, 1)
        self.assertTrue(dead_endpoint.closed)
        # And: the live sibling is untouched, including its CID binding
        self.assertTrue(live_overlay.exists())
        self.assertEqual(live_handle.destroy_calls, 0)
        self.assertEqual(live_handle.undefine_calls, 0)
        self.assertEqual(self.session_map.session_for_cid(PeerCid(FAKE_CID)), SESSION_A)


# --------------------------------------------------------------------------- #
# (c) gateway-start recovery: orphaned tasks fail, paused tasks are untouched
# --------------------------------------------------------------------------- #


class GatewayRecoveryTest(unittest.TestCase):
    """Startup recovery against a real tasks DB and a real manager over fakes."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="igorek-recovery-")
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self._env = patch.dict(
            os.environ,
            {
                "IGOREK_FACTORY_HOME": str(self.root / "home"),
                "IGOREK_ISOLATION_MODE": MODE_KVM,
                "IGOREK_ISOLATION_BASE_IMAGE": str(self.root / "base.qcow2"),
                "IGOREK_ISOLATION_OVERLAY_DIR": str(self.root / "overlays"),
            },
        )
        self._env.start()
        self.addCleanup(self._env.stop)
        (self.root / "base.qcow2").write_bytes(b"qcow2-placeholder")
        self.workspace = self.root / "workspace"
        self.workspace.mkdir()
        (self.workspace / "notes.txt").write_text("keep me", encoding="utf-8")
        self.connection = FakeConnection()
        self.registry = FakeRegistry()
        self.session_map = SessionMap()
        self.leases = OverlayLeaseGuard()
        self.addCleanup(self.leases.close)
        initialize_database(get_db_path())

    def build_manager(self, cid: int = FAKE_CID) -> IsolationManager:
        def tracking_preflight(cfg: IsolationConfig) -> PreflightResult:
            return PreflightResult(ok=True, mode=cfg.mode, reason=REASON_OK)

        seams = ManagerSeams(
            connect=lambda uri: self.connection,
            runner=lambda argv: CommandResult(0),
            channel=self.registry,
            session_map=self.session_map,
            workspace_policy=make_policy(self.root),
            overlay_leases=self.leases,
            preflight_fn=tracking_preflight,
            egress=fake_egress_builder,
        )
        config = enabled_config(self.root)
        return IsolationManager(config, seams=seams)

    def kvm_env(self) -> dict[str, str]:
        return {
            "IGOREK_ISOLATION_MODE": MODE_KVM,
            "IGOREK_ISOLATION_BASE_IMAGE": str(self.root / "base.qcow2"),
            "IGOREK_ISOLATION_OVERLAY_DIR": str(self.root / "overlays"),
        }

    def insert_task(
        self, task_id: str, status: str, session_id: str, *, started_at: int
    ) -> None:
        created = create_task(
            text="long task",
            profile="cc-test",
            metadata={"session": session_id, "source": "mobile_bridge"},
        )
        generated = str(created["task_id"])
        with sqlite3.connect(get_db_path()) as conn:
            conn.execute(
                "UPDATE tasks SET task_id = ?, status = ?, started_at = ?, updated_at = ? "
                "WHERE task_id = ?",
                (task_id, status, started_at, started_at, generated),
            )
            conn.commit()

    def task_row(self, task_id: str) -> tuple[str, str | None, int | None]:
        with sqlite3.connect(get_db_path()) as conn:
            row = conn.execute(
                "SELECT status, error_message, completed_at FROM tasks WHERE task_id = ?",
                (task_id,),
            ).fetchone()
        self.assertIsNotNone(row)
        return str(row[0]), row[1], row[2]

    def make_leftover_vm(self, session_id: str, cid: int) -> tuple[Path, FakeDomainHandle]:
        spec = DomainSpec(
            session_id=session_id,
            base_image=self.root / "base.qcow2",
            workspace_host_path=self.workspace,
            overlay_dir=self.root / "overlays",
        )
        overlay_dir = self.root / "overlays"
        overlay_dir.mkdir(parents=True, exist_ok=True)
        overlay = overlay_for_session(session_id, overlay_dir)
        overlay.write_bytes(b"orphan-overlay")
        handle = self.connection.defineXML(f"<domain><name>{domain_name(spec)}</name></domain>")
        handle.create()
        return overlay, handle

    def test_gateway_start_reaps_orphans_and_fails_the_orphaned_task(self) -> None:
        # Given: a task stuck in `processing` from a dead host process, with the
        # VM's domain and overlay still on the host and no live lease covering it
        self.insert_task("task-orphan", "processing", "sess-orphan", started_at=0)
        overlay, handle = self.make_leftover_vm("sess-orphan", 801)
        manager = self.build_manager()

        # When: the gateway-start recovery runs
        report = recover_isolation_startup(
            self.kvm_env(), manager=manager, db_path=get_db_path()
        )

        # Then: the leftovers are reaped
        self.assertEqual(report.mode, MODE_KVM)
        self.assertEqual(report.reaped, 1)
        self.assertFalse(overlay.exists())
        self.assertEqual(handle.destroy_calls, 1)
        self.assertEqual(handle.undefine_calls, 1)
        # And: the orphaned task lands in a failure status with a clear error
        status, error_message, completed_at = self.task_row("task-orphan")
        self.assertEqual(status, "fail")
        self.assertTrue(str(error_message or "").strip())
        self.assertIn("isolation", str(error_message))
        self.assertIsNotNone(completed_at)
        self.assertEqual(report.failed_tasks, ("task-orphan",))
        self.assertEqual(report.error, "")
        # And: the user workspace is untouched
        self.assertEqual(
            (self.workspace / "notes.txt").read_text(encoding="utf-8"), "keep me"
        )

    def test_a_parked_waiting_approval_task_is_neither_reaped_nor_failed(self) -> None:
        # Given: a task paused for approval - todo 9 stopped its VM on purpose
        self.insert_task("task-parked", "waiting_approval", "sess-parked", started_at=0)
        manager = self.build_manager()

        # When: the gateway-start recovery runs
        report = recover_isolation_startup(
            self.kvm_env(), manager=manager, db_path=get_db_path()
        )

        # Then: the paused task keeps its transition and its error stays empty
        status, error_message, completed_at = self.task_row("task-parked")
        self.assertEqual(status, "waiting_approval")
        self.assertIsNone(error_message)
        self.assertIsNone(completed_at)
        self.assertEqual(report.failed_tasks, ())
        self.assertEqual(report.reaped, 0)
        self.assertEqual(report.error, "")
        # And: it is not consumed by the recovery - it still resumes normally
        self.assertTrue(resume_waiting_approval_task("task-parked"))
        self.assertEqual(self.task_row("task-parked")[0], "queued")

    def test_a_task_with_a_live_lease_is_left_for_its_own_processing(self) -> None:
        # Given: a task whose VM is still owned by a live process (a sibling worker)
        self.insert_task("task-live", "processing", "sess-live", started_at=0)
        overlay = overlay_for_session("sess-live", self.root / "overlays")
        overlay.parent.mkdir(parents=True, exist_ok=True)
        overlay.write_bytes(b"live-overlay")
        sibling = OverlayLeaseGuard()
        self.addCleanup(sibling.close)
        sibling.acquire(overlay, "sess-live")
        manager = self.build_manager()

        # When: the gateway starts while the sibling still runs the VM
        report = recover_isolation_startup(
            self.kvm_env(), manager=manager, db_path=get_db_path()
        )

        # Then: the live task is neither failed nor reaped
        self.assertEqual(self.task_row("task-live")[0], "processing")
        self.assertEqual(report.failed_tasks, ())
        self.assertEqual(report.reaped, 0)
        self.assertTrue(overlay.exists())
        self.assertTrue(manager.owns_overlay("sess-live"))

    def test_a_young_processing_task_is_left_to_its_worker(self) -> None:
        # Given: a task that was just claimed (it may be a live worker still
        # starting its VM, before the overlay lease exists)
        self.insert_task(
            "task-young", "processing", "sess-young", started_at=int(time.time())
        )
        manager = self.build_manager()

        # When: the gateway-start recovery runs
        report = recover_isolation_startup(
            self.kvm_env(), manager=manager, db_path=get_db_path()
        )

        # Then: the grace window protects it from a false failure
        self.assertEqual(self.task_row("task-young")[0], "processing")
        self.assertEqual(report.failed_tasks, ())
        self.assertGreater(DEFAULT_STARTUP_GRACE_S, 0)

    def test_recovery_is_a_pure_no_op_off_mode(self) -> None:
        # Given: an off-mode host with a processing row that must not change
        self.insert_task("task-off", "processing", "sess-off", started_at=0)

        # When: the startup recovery runs with no isolation request
        with patch.object(
            startup_module,
            "IsolationManager",
            side_effect=AssertionError("off mode must not build an isolation manager"),
        ):
            report = recover_isolation_startup({}, db_path=get_db_path())

        # Then: it reports off and touches nothing at all
        self.assertEqual(
            report, StartupRecovery(mode=MODE_OFF, reaped=0, failed_tasks=(), error="")
        )
        self.assertEqual(self.task_row("task-off")[0], "processing")

    def test_recovery_reports_a_broken_reaper_without_crashing(self) -> None:
        # Given: a reaper that cannot run (libvirt down)
        self.insert_task("task-orphan", "processing", "sess-orphan", started_at=0)

        class BrokenManager:
            def reap_orphans(self) -> int:
                raise IsolationError("orphan_cleanup_failed", "libvirt unreachable")

            def owns_overlay(self, session_id: str) -> bool:
                return False

        # When: the gateway-start recovery runs
        report = recover_isolation_startup(
            self.kvm_env(), manager=BrokenManager(), db_path=get_db_path()
        )

        # Then: the failure is reported, the host stays healthy, and the task
        # still gets its clear terminal error
        self.assertIn("reap_failed", report.error)
        self.assertEqual(report.failed_tasks, ("task-orphan",))
        self.assertEqual(self.task_row("task-orphan")[0], "fail")

    def test_recovery_reports_a_broken_manager_without_crashing(self) -> None:
        # Given: the manager itself cannot even be built
        with patch.object(
            startup_module,
            "IsolationManager",
            side_effect=IsolationError("libvirt_unreachable", "no connection"),
        ):
            report = recover_isolation_startup(self.kvm_env(), db_path=get_db_path())

        # Then: the error is structured and the host stays healthy
        self.assertEqual(report.mode, MODE_KVM)
        self.assertIn("manager_init_failed", report.error)
        self.assertEqual(report.failed_tasks, ())


# --------------------------------------------------------------------------- #
# (d) worker surface: the contained failure reaches `error_message`
# --------------------------------------------------------------------------- #


class FakeManager:
    """Fake isolation manager returning one prepared result."""

    def __init__(self, result: dict[str, Any]) -> None:
        self.result = dict(result)

    def run_task(self, *, task, workdir, payload, cancel_check=None) -> dict[str, Any]:
        return dict(self.result)


class RecordingSequenceManager:
    """Fake manager that records calls and returns results in order."""

    def __init__(self, results: list[dict[str, Any]]) -> None:
        self.results = [dict(result) for result in results]
        self.calls: list[dict[str, Any]] = []

    def run_task(self, *, task, workdir, payload, cancel_check=None) -> dict[str, Any]:
        self.calls.append(
            {
                "task": task,
                "workdir": workdir,
                "payload": payload,
                "cancel_check": cancel_check,
            }
        )
        return self.results.pop(0)


class WorkerErrorSurfaceTest(unittest.TestCase):
    """A contained isolation failure must land in the DB as status+error_message."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="igorek-failsafe-worker-")
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self._env = patch.dict(os.environ, {"IGOREK_FACTORY_HOME": str(self.root)})
        self._env.start()
        self.addCleanup(self._env.stop)
        initialize_database(get_db_path())
        self.worker = Worker.__new__(Worker)
        self.worker.profile = "cc-failsafe"
        self.worker.pid = 4242
        self.worker.backend = "cc-backend"
        self.worker.model = "failsafe-model"
        self.worker.config = {}

    def manager_failure(self) -> dict[str, Any]:
        detail = (
            "session sess-1 lost its VM (vm_lost: the domain is not running); "
            "the task failed and the host is unaffected"
        )
        return {
            "status": "fail",
            "output_text": "",
            "usage": {"prompt": 0, "completion": 0, "lat_ms_p50": 12},
            "tool_calls_count": 0,
            "failure_reason": FAILURE_REASON_VM_LOST,
            "failure_details": detail,
            "error": detail,
        }

    def test_the_isolation_fail_shape_keeps_the_clear_error(self) -> None:
        # Given: the manager's contained VM-loss failure
        manager_result = self.manager_failure()

        # When: the worker normalizes it for the task table
        result = worker_module._isolation_failure_result(
            manager_result, task_id="task-1"
        )

        # Then: it is a terminal failure that still carries the reason (the
        # generic executor normalizer would drop `error`, leaving an empty
        # `error_message` in the DB)
        self.assertEqual(result["status"], "fail")
        self.assertEqual(result["error"], manager_result["error"])
        self.assertEqual(result["usage"], {"prompt": 0, "completion": 0, "lat_ms_p50": 12})
        self.assertNotEqual(result["status"], "ok")

    def test_an_acknowledged_guest_cancel_is_written_as_cancelled(self) -> None:
        created = create_task(text="долгая задача", profile="cc-failsafe")
        task_id = str(created["task_id"])
        manager_result = {
            "status": "fail",
            "output_text": "",
            "usage": {"prompt": 0, "completion": 0, "lat_ms_p50": 12},
            "tool_calls_count": 0,
            "failure_reason": "canceled",
            "failure_details": "guest run canceled",
            "error": "guest run canceled",
        }
        result = worker_module._isolation_failure_result(manager_result, task_id=task_id)
        self.assertEqual(result["status"], "cancelled")

        with sqlite3.connect(get_db_path()) as conn:
            self.worker.update_task(conn, task_id, result)
        with sqlite3.connect(get_db_path()) as conn:
            row = conn.execute(
                "SELECT status, completed_at FROM tasks WHERE task_id = ?", (task_id,)
            ).fetchone()
        self.assertEqual(row[0], "cancelled")
        self.assertIsNotNone(row[1])

    def test_a_contained_vm_loss_is_written_as_fail_with_the_clear_error(self) -> None:
        # Given: a claimed task and the worker-shaped contained failure
        created = create_task(text="долгая задача", profile="cc-failsafe")
        task_id = str(created["task_id"])
        result = worker_module._isolation_failure_result(
            self.manager_failure(), task_id=task_id
        )

        # When: update_task persists it, exactly as the worker loop does
        with sqlite3.connect(get_db_path()) as conn:
            self.worker.update_task(conn, task_id, result)

        # Then: the task row is a failure with a clear error_message, never a success
        with sqlite3.connect(get_db_path()) as conn:
            row = conn.execute(
                "SELECT status, error_message, completed_at FROM tasks WHERE task_id = ?",
                (task_id,),
            ).fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row[0], "fail")
        self.assertEqual(row[1], self.manager_failure()["error"])
        self.assertIsNotNone(row[2])

    def test_the_isolation_fail_shape_keeps_the_failure_reason(self) -> None:
        # Given: the manager's contained VM-loss failure
        manager_result = self.manager_failure()

        # When: the worker normalizes it for the task table
        result = worker_module._isolation_failure_result(
            manager_result, task_id="task-1"
        )

        # Then: the machine token survives so update_task can classify it
        self.assertEqual(result["failure_reason"], FAILURE_REASON_VM_LOST)

    def test_an_infra_failure_is_marked_in_task_metadata(self) -> None:
        # Given: a claimed task and the worker-shaped VM-loss failure
        created = create_task(text="долгая задача", profile="cc-failsafe")
        task_id = str(created["task_id"])
        result = worker_module._isolation_failure_result(
            self.manager_failure(), task_id=task_id
        )

        # When: update_task persists it
        with sqlite3.connect(get_db_path()) as conn:
            self.worker.update_task(conn, task_id, result)

        # Then: the infra marker and reason are visible structurally
        with sqlite3.connect(get_db_path()) as conn:
            row = conn.execute(
                "SELECT metadata FROM tasks WHERE task_id = ?", (task_id,)
            ).fetchone()
        metadata = json.loads(row[0])
        self.assertIs(metadata.get("infra_failure"), True)
        self.assertEqual(metadata.get("failure_reason"), FAILURE_REASON_VM_LOST)

    def test_an_ordinary_failure_is_not_marked_infrastructure(self) -> None:
        # Given: an ordinary model/tool failure with a non-infra reason
        created = create_task(text="обычная задача", profile="cc-failsafe")
        task_id = str(created["task_id"])
        result = {
            "status": "fail",
            "error": "model refused the request",
            "failure_reason": "http_error",
            "usage": {"prompt": 0, "completion": 0, "lat_ms_p50": 0},
        }

        # When: update_task persists it
        with sqlite3.connect(get_db_path()) as conn:
            self.worker.update_task(conn, task_id, result)

        # Then: no infrastructure marker is written
        with sqlite3.connect(get_db_path()) as conn:
            row = conn.execute(
                "SELECT metadata FROM tasks WHERE task_id = ?", (task_id,)
            ).fetchone()
        metadata = json.loads(row[0]) if row and row[0] else {}
        self.assertNotIn("infra_failure", metadata)
        self.assertNotIn("failure_reason", metadata)
        self.assertEqual(metadata.get("diagnostic_failure_reason"), "http_error")

    def test_missing_approval_record_retries_with_safe_recovery_context(self) -> None:
        # Given: a missing approval record followed by a valid approval request
        missing_record = {
            "status": "fail",
            "failure_reason": "approval_request_missing",
            "failure_details": "waiting_approval without a deliverable record",
            "output_text": "",
            "usage": {"prompt": 10, "completion": 3, "lat_ms_p50": 12},
            "tool_calls_count": 1,
        }
        approval = {
            "status": "waiting_approval",
            "output_text": "approval required",
            "usage": {"prompt": 7, "completion": 2, "lat_ms_p50": 8},
            "tool_calls_count": 1,
            "approval_blocked_tools": [{"tool": "Edit", "target": "report.md"}],
        }
        manager = RecordingSequenceManager([missing_record, approval])
        metadata = {
            "forced_tool_call": {
                "name": "Edit",
                "input": {"file_path": "/workspace/report.md"},
            },
            "approved_tool_calls": ["one-time-signature"],
            "approval_context_messages": [
                {
                    "role": "assistant",
                    "content": [{"type": "text", "text": "Prior approved action"}],
                }
            ],
            "approved_session_rules": [{"tool": "Edit", "file_path": "/workspace/report.md"}],
            "custom_context": "preserve",
        }
        payload = {
            "task_id": "task-recovery",
            "user_message": "Продолжи работу над отчётом",
            "system_prompt": "BASE SYSTEM PROMPT",
            "base_url": "http://127.0.0.1:18081/v1/systemone",
            "metadata": metadata,
        }
        trace_path = self.root / "workspace" / "temp_execution_task-rec" / "cc_backend_trace.jsonl"

        # When: the isolated worker sees the precise missing-record failure
        with (
            patch.object(worker_module, "_get_isolation_manager", return_value=manager),
            patch.object(worker_module, "_isolation_cancel_check", return_value=lambda: False),
            patch.object(worker_module, "_trace_path_for_task", return_value=trace_path),
        ):
            result = self.worker._execute_cc_backend_isolated(
                task={"task_id": "task-recovery"},
                workdir=str(self.root / "workspace"),
                payload=payload,
            )

        # Then: the second dispatch keeps the local route and prior context,
        # drops one-time approval replay data, and reaches a normal approval park
        self.assertEqual(len(manager.calls), 2)
        retry_payload = manager.calls[1]["payload"]
        self.assertEqual(retry_payload["base_url"], payload["base_url"])
        self.assertEqual(retry_payload["user_message"], payload["user_message"])
        self.assertTrue(
            retry_payload["system_prompt"].startswith("BASE SYSTEM PROMPT\n\n")
        )
        self.assertIn("Служебное восстановление", retry_payload["system_prompt"])
        self.assertNotIn("forced_tool_call", retry_payload["metadata"])
        self.assertNotIn("approved_tool_calls", retry_payload["metadata"])
        self.assertEqual(
            retry_payload["metadata"]["approval_context_messages"],
            metadata["approval_context_messages"],
        )
        self.assertEqual(
            retry_payload["metadata"]["approved_session_rules"],
            metadata["approved_session_rules"],
        )
        self.assertEqual(retry_payload["metadata"]["custom_context"], "preserve")
        self.assertEqual(payload["user_message"], "Продолжи работу над отчётом")
        self.assertEqual(result["status"], "waiting_approval")
        self.assertEqual(
            result["approval_blocked_tools"], approval["approval_blocked_tools"]
        )
        self.assertEqual(result["usage"]["prompt"], 17)
        self.assertEqual(result["usage"]["completion"], 5)
        trace_events = [
            json.loads(line)
            for line in trace_path.read_text(encoding="utf-8").splitlines()
        ]
        self.assertEqual(
            [event["kind"] for event in trace_events],
            ["isolation.recovery_started", "isolation.recovery_finished"],
        )
        self.assertEqual(trace_events[0]["reason"], "approval_request_missing")
        self.assertEqual(trace_events[0]["attempt"], 1)
        self.assertEqual(trace_events[1]["status"], "waiting_approval")

    def test_missing_approval_record_does_not_retry_other_failures(self) -> None:
        # Given: a different terminal isolation failure
        failure = {
            "status": "fail",
            "failure_reason": FAILURE_REASON_VM_LOST,
            "failure_details": "the VM is gone",
            "output_text": "",
            "usage": {},
            "tool_calls_count": 0,
        }
        manager = RecordingSequenceManager([failure])

        # When: the worker dispatches the task
        with patch.object(worker_module, "_get_isolation_manager", return_value=manager):
            result = self.worker._execute_cc_backend_isolated(
                task={"task_id": "task-vm-lost"},
                workdir=str(self.root / "workspace"),
                payload={"task_id": "task-vm-lost"},
            )

        # Then: unrelated failures remain single-attempt failures
        self.assertEqual(len(manager.calls), 1)
        self.assertEqual(result["failure_reason"], FAILURE_REASON_VM_LOST)

    def test_missing_approval_record_recovery_is_bounded_to_one_retry(self) -> None:
        # Given: both the first attempt and the recovery attempt lose the record
        missing = {
            "status": "fail",
            "failure_reason": "approval_request_missing",
            "failure_details": "waiting_approval without a deliverable record",
            "output_text": "",
            "usage": {"prompt": 4, "completion": 1},
            "tool_calls_count": 1,
        }
        manager = RecordingSequenceManager([missing, missing])

        # When: the worker attempts the bounded recovery
        with (
            patch.object(worker_module, "_get_isolation_manager", return_value=manager),
            patch.object(worker_module, "_isolation_cancel_check", return_value=lambda: False),
        ):
            result = self.worker._execute_cc_backend_isolated(
                task={"task_id": "task-repeat-missing"},
                workdir=str(self.root / "workspace"),
                payload={"task_id": "task-repeat-missing", "user_message": "Continue"},
            )

        # Then: the worker stops after exactly one recovery dispatch with a clear error
        self.assertEqual(len(manager.calls), 2)
        self.assertEqual(result["failure_reason"], "approval_request_missing")
        self.assertIn(
            "одной попытки автоматического восстановления", result["failure_details"]
        )
        self.assertEqual(result["usage"]["prompt"], 8)
        self.assertEqual(result["usage"]["completion"], 2)

    def test_missing_approval_record_does_not_retry_after_cancel(self) -> None:
        # Given: the task was canceled before the recovery retry could start
        missing = {
            "status": "fail",
            "failure_reason": "approval_request_missing",
            "failure_details": "waiting_approval without a deliverable record",
            "output_text": "",
            "usage": {},
            "tool_calls_count": 1,
        }
        manager = RecordingSequenceManager([missing])

        # When: the cancel check is already active after the first dispatch
        with (
            patch.object(worker_module, "_get_isolation_manager", return_value=manager),
            patch.object(worker_module, "_isolation_cancel_check", return_value=lambda: True),
        ):
            result = self.worker._execute_cc_backend_isolated(
                task={"task_id": "task-canceled"},
                workdir=str(self.root / "workspace"),
                payload={"task_id": "task-canceled"},
            )

        # Then: cancellation wins over the automatic retry
        self.assertEqual(len(manager.calls), 1)
        self.assertEqual(result["failure_reason"], "approval_request_missing")

    def test_a_waiting_approval_result_is_not_turned_into_a_failure(self) -> None:
        # Given: the manager parked the task for an approval
        manager = FakeManager(
            {
                "status": "waiting_approval",
                "output_text": "",
                "usage": {},
                "tool_calls_count": 0,
                "approval_blocked_tools": [{"tool": "Write"}],
            }
        )

        # When: the isolated dispatch seam returns
        with patch.object(
            worker_module, "_get_isolation_manager", return_value=manager
        ):
            result = self.worker._execute_cc_backend_isolated(
                task={"task_id": "task-parked"},
                workdir=str(self.root / "workspace"),
                payload={},
            )

        # Then: the park passes through untouched - no failure is fabricated
        self.assertEqual(result["status"], "waiting_approval")
        self.assertEqual(result["approval_blocked_tools"], [{"tool": "Write"}])
        self.assertNotIn("error", result)


if __name__ == "__main__":
    unittest.main()
