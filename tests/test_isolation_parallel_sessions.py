#!/usr/bin/env python3
"""todo 23 (kvm-session-isolation): parallel per-session VMs across profiles.

Two sessions (e.g. ``cc-wally`` and ``cc-glm``) run concurrently, each in its
own VM: its own libvirt domain, its own disposable qcow2 overlay, its own vsock
CID and its own single workspace export. Nothing writable is shared and no
session can reach a sibling's VM - neither through the host's routing table
(``SessionMap`` refuses to rebind a live CID and the manager refuses to dispatch
when the CID binding names another session) nor through the overlay directory
(a live sibling's overlay is leased, so ``reap_orphans`` in one manager cannot
destroy a running VM owned by another manager/process).

Adversarial classes covered here:

* misleading success - every identity assertion compares concrete values
  (domain name, overlay path, CID, export realpath) between the two sessions,
  never "both started";
* cross-VM access - a guest connecting with a sibling's CID is refused
  (``unknown_peer_cid``), a guest *claiming* a sibling's session id in its hello
  is still bound to its own host-observed CID, and a second manager cannot adopt
  a live session's overlay (``overlay_busy``) or workspace (``workspace_busy``);
* stale/interfering state - reaping orphans in one manager leaves a sibling
  manager's live VM, overlay and dispatch intact;
* off mode - stays byte-identical: no VM, no lease, no lock state.
"""

from __future__ import annotations

import os
import socket
import sys
import tempfile
import threading
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

REPO_ROOT = Path(
    os.environ.get("IGOREK_FACTORY_REPO", Path(__file__).resolve().parents[1])
)
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from factory.core.isolation import channel  # noqa: E402
from factory.core.isolation.channel import (  # noqa: E402
    PROTOCOL_VERSION,
    ChannelConfig,
    ChannelError,
    ChannelMessage,
    ChannelServer,
    MessageKind,
    PeerCid,
    SessionId,
    SessionMap,
    read_frame,
    write_frame,
)
from factory.core.isolation.config import (  # noqa: E402
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
from factory.core.isolation.manager import (  # noqa: E402
    IsolationManager,
    ManagerSeams,
    SessionSpec,
    SessionVm,
)
from factory.core.isolation.overlay_guard import OverlayLeaseGuard  # noqa: E402
from factory.core.isolation.preflight import REASON_OK, PreflightResult  # noqa: E402
from factory.core.isolation.session_egress import SessionEgress  # noqa: E402
from factory.core.isolation.workspace import (  # noqa: E402
    GUEST_WORKSPACE_PATH,
    WorkspacePolicy,
)

SESSION_A = "sess-wally"
SESSION_B = "sess-glm"
PROFILE_A = "cc-wally"
PROFILE_B = "cc-glm"
TASK_A = "task-wally-1"
TASK_B = "task-glm-1"


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


class CidAllocator:
    """Host-global CID allocator: one libvirt daemon hands out distinct CIDs."""

    def __init__(self, first: int = 64) -> None:
        self._next = first

    def take(self) -> int:
        cid = self._next
        self._next += 1
        return cid


class FakeDomainHandle:
    """Minimal ``libvirt.virDomain`` stand-in that keeps its own live XML."""

    def __init__(self, name: str, cid: int, xml: str) -> None:
        self._name = name
        self._cid = cid
        self.xml = xml
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
        root = ET.fromstring(self.xml)
        cid = root.find(".//vsock/cid")
        if cid is not None:
            cid.set("address", str(self._cid))
        return ET.tostring(root, encoding="unicode")


class FakeConnection:
    """Fake libvirt connection over one host-global CID allocator."""

    def __init__(self, allocator: CidAllocator) -> None:
        self._allocator = allocator
        self.defined: dict[str, FakeDomainHandle] = {}
        self.closed = False

    def defineXML(self, xml: str) -> FakeDomainHandle:
        name = ET.fromstring(xml).findtext("name") or ""
        handle = FakeDomainHandle(name, self._allocator.take(), xml)
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


class FlippableSessionMap(SessionMap):
    """Test double for a corrupted host routing table (CID rebound mid-dispatch)."""

    def __init__(self) -> None:
        super().__init__()
        self.flipped = False

    def session_for_cid(self, peer_cid: PeerCid) -> SessionId | None:
        if self.flipped:
            return SessionId(SESSION_B)
        return super().session_for_cid(peer_cid)


def make_policy(root: Path) -> WorkspacePolicy:
    return WorkspacePolicy(
        home=root / "home",
        data_root=root / "data",
        host_uid=os.getuid(),
        host_gid=os.getgid(),
    )


def enabled_config(root: Path) -> IsolationConfig:
    base = root / "base.qcow2"
    base.write_bytes(b"qcow2-placeholder")
    config = IsolationConfig.from_env(
        {
            "IGOREK_ISOLATION_MODE": MODE_KVM,
            "IGOREK_ISOLATION_BASE_IMAGE": str(base),
            "IGOREK_ISOLATION_OVERLAY_DIR": str(root / "overlays"),
            "IGOREK_ISOLATION_VSOCK_PORT": "5007",
        },
        data_root=root,
        capability_probe=lambda uri: HostCapabilities(True, True, True, True),
    )
    assert config.enabled, config.reason
    return config


def dispatch_payload(session_id: str, profile: str, task_id: str) -> dict[str, Any]:
    return {
        "task_id": task_id,
        "profile": profile,
        "model": "test-model",
        "base_url": "http://127.0.0.1:8001",
        "api_format": "anthropic",
        "system_prompt": f"system prompt for {profile}",
        "user_message": f"user message for {profile}",
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


class ParallelHarness(unittest.TestCase):
    """Two sessions, two managers, one host-global fake libvirt daemon."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="igorek-parallel-")
        self.root = Path(self._tmp.name)
        self.allocator = CidAllocator()
        self.connection = FakeConnection(self.allocator)
        self.runner = FakeQemuImg()
        self.policy = make_policy(self.root)
        self.workspace_a = self.root / "workspace-wally"
        self.workspace_b = self.root / "workspace-glm"
        self.workspace_a.mkdir()
        self.workspace_b.mkdir()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def build_manager(
        self,
        *,
        registry: FakeRegistry | None = None,
        session_map: SessionMap | None = None,
        connection: FakeConnection | None = None,
        config: IsolationConfig | None = None,
        leases: OverlayLeaseGuard | None = None,
    ) -> IsolationManager:
        active_registry = registry if registry is not None else FakeRegistry()
        active_connection = connection if connection is not None else self.connection
        seams = ManagerSeams(
            connect=lambda uri: active_connection,
            runner=self.runner,
            channel=active_registry,
            session_map=session_map if session_map is not None else SessionMap(),
            workspace_policy=self.policy,
            preflight_fn=lambda cfg: PreflightResult(ok=True, mode=cfg.mode, reason=REASON_OK),
            egress=fake_egress_builder,
            overlay_leases=leases,
        )
        return IsolationManager(config or enabled_config(self.root), seams=seams)

    def start_two(
        self, manager_a: IsolationManager, manager_b: IsolationManager
    ) -> tuple[SessionVm, SessionVm]:
        """Start both session VMs concurrently; surface thread exceptions."""
        started: dict[str, SessionVm] = {}
        errors: list[BaseException] = []

        def start(key: str, manager: IsolationManager, session: SessionSpec) -> None:
            try:
                started[key] = manager.ensure_vm(session)
            except BaseException as exc:  # reported to the test
                errors.append(exc)

        threads = [
            threading.Thread(
                target=start,
                args=(
                    "a",
                    manager_a,
                    SessionSpec(session_id=SESSION_A, workspace_cwd=str(self.workspace_a)),
                ),
                daemon=True,
            ),
            threading.Thread(
                target=start,
                args=(
                    "b",
                    manager_b,
                    SessionSpec(session_id=SESSION_B, workspace_cwd=str(self.workspace_b)),
                ),
                daemon=True,
            ),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10.0)
        self.assertEqual(errors, [])
        self.assertEqual(sorted(started), ["a", "b"])
        return started["a"], started["b"]

    def overlay_dir(self) -> Path:
        return self.root / "overlays"


class ParallelIdentityTest(ParallelHarness):
    def test_two_concurrent_sessions_get_distinct_domains_overlays_cids_exports(
        self,
    ) -> None:
        # Given: two managers for two profiles, one host-global libvirt daemon
        manager_a = self.build_manager()
        manager_b = self.build_manager()

        # When: both sessions start their VM concurrently
        vm_a, vm_b = self.start_two(manager_a, manager_b)

        # Then: every identity is distinct and owned by the right session
        self.assertEqual(vm_a.session_id, SESSION_A)
        self.assertEqual(vm_b.session_id, SESSION_B)
        self.assertNotEqual(vm_a.peer_cid, vm_b.peer_cid)
        self.assertIsNotNone(vm_a.peer_cid)
        self.assertIsNotNone(vm_b.peer_cid)
        self.assertNotEqual(domain_name(vm_a.spec), domain_name(vm_b.spec))
        self.assertEqual(sorted(self.connection.defined), sorted([
            domain_name(vm_a.spec),
            domain_name(vm_b.spec),
        ]))
        overlay_a = Path(vm_a.domain.overlay or "")
        overlay_b = Path(vm_b.domain.overlay or "")
        self.assertNotEqual(overlay_a, overlay_b)
        self.assertTrue(overlay_a.is_file())
        self.assertTrue(overlay_b.is_file())
        self.assertTrue(overlay_a.is_relative_to(self.overlay_dir()))
        self.assertTrue(overlay_b.is_relative_to(self.overlay_dir()))
        self.assertEqual(vm_a.export["host_path"], str(self.workspace_a.resolve()))
        self.assertEqual(vm_b.export["host_path"], str(self.workspace_b.resolve()))
        self.assertNotEqual(vm_a.export["host_path"], vm_b.export["host_path"])
        self.assertEqual(vm_a.export["guest_path"], GUEST_WORKSPACE_PATH)
        self.assertEqual(vm_b.export["guest_path"], GUEST_WORKSPACE_PATH)

        # And: each domain XML names exactly its own workspace, nothing of the sibling
        xml_a = self.connection.defined[domain_name(vm_a.spec)].xml
        xml_b = self.connection.defined[domain_name(vm_b.spec)].xml
        self.assertIn(str(self.workspace_a.resolve()), xml_a)
        self.assertNotIn(str(self.workspace_b.resolve()), xml_a)
        self.assertIn(str(self.workspace_b.resolve()), xml_b)
        self.assertNotIn(str(self.workspace_a.resolve()), xml_b)
        for xml in (xml_a, xml_b):
            self.assertEqual(len(ET.fromstring(xml).findall(".//filesystem")), 1)
            self.assertEqual(len(ET.fromstring(xml).findall(".//vsock")), 1)
            self.assertNotIn('accessmode="passthrough"', xml)
            self.assertNotIn(str(overlay_a), xml_b)
            self.assertNotIn(str(overlay_b), xml_a)

    def test_each_manager_routes_only_its_own_cid(self) -> None:
        # Given: two managers with their own per-process session maps
        map_a = SessionMap()
        map_b = SessionMap()
        manager_a = self.build_manager(session_map=map_a)
        manager_b = self.build_manager(session_map=map_b)
        vm_a, vm_b = self.start_two(manager_a, manager_b)

        # Then: a CID is visible only in the map of the process that owns it
        self.assertEqual(map_a.session_for_cid(vm_a.peer_cid), SESSION_A)
        self.assertIsNone(map_a.session_for_cid(vm_b.peer_cid))
        self.assertEqual(map_b.session_for_cid(vm_b.peer_cid), SESSION_B)
        self.assertIsNone(map_b.session_for_cid(vm_a.peer_cid))

    def test_two_concurrent_dispatches_keep_their_own_frames_and_results(self) -> None:
        # Given: two managers, each with its own registry and result stream
        endpoint_a = FakeEndpoint([result_message("done-wally")])
        endpoint_b = FakeEndpoint([result_message("done-glm")])
        manager_a = self.build_manager(registry=FakeRegistry({SESSION_A: endpoint_a}))
        manager_b = self.build_manager(registry=FakeRegistry({SESSION_B: endpoint_b}))
        self.start_two(manager_a, manager_b)
        results: dict[str, dict[str, Any]] = {}
        errors: list[BaseException] = []

        def dispatch(
            key: str,
            manager: IsolationManager,
            session_id: str,
            profile: str,
            task_id: str,
            workspace: Path,
        ) -> None:
            try:
                results[key] = manager.run_task(
                    {"task_id": task_id, "metadata": {"session": session_id}},
                    str(workspace),
                    dispatch_payload(session_id, profile, task_id),
                )
            except BaseException as exc:  # reported to the test
                errors.append(exc)

        # When: both sessions dispatch a turn concurrently
        threads = [
            threading.Thread(
                target=dispatch,
                args=("a", manager_a, SESSION_A, PROFILE_A, TASK_A, self.workspace_a),
                daemon=True,
            ),
            threading.Thread(
                target=dispatch,
                args=("b", manager_b, SESSION_B, PROFILE_B, TASK_B, self.workspace_b),
                daemon=True,
            ),
        ]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=10.0)

        # Then: each caller gets its own result and each guest only its own frame
        self.assertEqual(errors, [])
        self.assertEqual(results["a"]["output_text"], "done-wally")
        self.assertEqual(results["b"]["output_text"], "done-glm")
        self.assertEqual([kind for kind, _ in endpoint_a.sent], [MessageKind.TASK])
        self.assertEqual([kind for kind, _ in endpoint_b.sent], [MessageKind.TASK])
        wire_a = endpoint_a.sent[0][1]
        wire_b = endpoint_b.sent[0][1]
        self.assertEqual(wire_a["task_id"], TASK_A)
        self.assertEqual(wire_a["profile"], PROFILE_A)
        self.assertEqual(wire_b["task_id"], TASK_B)
        self.assertEqual(wire_b["profile"], PROFILE_B)


class CrossVmRefusalTest(ParallelHarness):
    def test_a_guest_with_a_sibling_cid_is_refused(self) -> None:
        # Given: host A knows only its own session's CID
        sessions = SessionMap()
        sessions.assign(PeerCid(101), SessionId(SESSION_A))
        server = ChannelServer(ChannelConfig(port=43201), sessions)
        host_sock, guest_sock = socket.socketpair()
        try:
            write_frame(
                guest_sock,
                {"kind": "hello", "version": PROTOCOL_VERSION, "session_id": SESSION_B},
            )
            # When: a peer presenting the sibling's CID tries to connect
            accepted = server.handle_connection(host_sock, PeerCid(102))

            # Then: it is refused, never mapped to the sibling
            self.assertIsNone(accepted)
            ack = read_frame(guest_sock, max_frame_bytes=4096, timeout_s=2.0)
            self.assertIsInstance(ack, dict)
            assert isinstance(ack, dict)
            self.assertEqual(ack.get("reason"), "unknown_peer_cid")
            self.assertIsNone(sessions.session_for_cid(PeerCid(102)))
        finally:
            guest_sock.close()
            server.close()

    def test_a_claimed_session_id_is_ignored_for_the_host_observed_cid(self) -> None:
        # Given: a host whose map binds CID 101 to session A
        sessions = SessionMap()
        sessions.assign(PeerCid(101), SessionId(SESSION_A))
        server = ChannelServer(ChannelConfig(port=43202), sessions)
        host_sock, guest_sock = socket.socketpair()
        try:
            # When: the guest claims session B (and B's CID) in its hello
            write_frame(
                guest_sock,
                {
                    "kind": "hello",
                    "version": PROTOCOL_VERSION,
                    "session_id": SESSION_B,
                    "cid": 102,
                },
            )
            accepted = server.handle_connection(host_sock, PeerCid(101))

            # Then: the host-observed CID wins and the answer names session A only
            self.assertIsNotNone(accepted)
            assert accepted is not None
            self.assertEqual(accepted.identity.session_id, SESSION_A)
            ack = read_frame(guest_sock, max_frame_bytes=4096, timeout_s=2.0)
            self.assertIsInstance(ack, dict)
            assert isinstance(ack, dict)
            self.assertEqual(ack.get("session_id"), SESSION_A)
            self.assertIsNone(sessions.session_for_cid(PeerCid(102)))
        finally:
            guest_sock.close()
            server.close()

    def test_session_map_refuses_to_rebind_a_live_cid(self) -> None:
        # Given: CID 101 is bound to session A
        sessions = SessionMap()
        sessions.assign(PeerCid(101), SessionId(SESSION_A))

        # When: another session tries to take the same CID
        with self.assertRaises(ChannelError) as caught:
            sessions.assign(PeerCid(101), SessionId(SESSION_B))

        # Then: the takeover is refused and the original binding is intact
        self.assertEqual(caught.exception.reason, "peer_cid_conflict")
        self.assertEqual(sessions.session_for_cid(PeerCid(101)), SESSION_A)

    def test_dispatch_refuses_a_cid_rebound_to_another_session(self) -> None:
        # Given: a live session whose CID the host map later routes to a sibling
        session_map = FlippableSessionMap()
        endpoint = FakeEndpoint([result_message("should-not-run")])
        manager = self.build_manager(
            registry=FakeRegistry({SESSION_A: endpoint}), session_map=session_map
        )
        manager.ensure_vm(
            SessionSpec(session_id=SESSION_A, workspace_cwd=str(self.workspace_a))
        )
        session_map.flipped = True

        # When: the manager is asked to dispatch a turn for session A
        with self.assertRaises(IsolationError) as caught:
            manager.run_task(
                {"task_id": TASK_A, "metadata": {"session": SESSION_A}},
                str(self.workspace_a),
                dispatch_payload(SESSION_A, PROFILE_A, TASK_A),
            )

        # Then: the turn fails closed instead of reaching a sibling's guest
        self.assertEqual(caught.exception.code, "peer_cid_conflict")
        self.assertEqual(endpoint.sent, [])

    def test_a_second_manager_cannot_adopt_a_live_session_overlay(self) -> None:
        # Given: session A's VM is live (its overlay is leased)
        manager_a = self.build_manager()
        vm_a = manager_a.ensure_vm(
            SessionSpec(session_id=SESSION_A, workspace_cwd=str(self.workspace_a))
        )
        overlay_a = Path(vm_a.domain.overlay or "")
        handle_a = self.connection.defined[domain_name(vm_a.spec)]

        # When: a second manager tries to start the same session on another workspace
        manager_b = self.build_manager()
        with self.assertRaises(IsolationError) as caught:
            manager_b.ensure_vm(
                SessionSpec(session_id=SESSION_A, workspace_cwd=str(self.workspace_b))
            )

        # Then: the duplicate live session is refused by admission control
        # (todo 33 refuses a second live slot for one session before the overlay
        # lease is even attempted) and the live VM is untouched
        self.assertEqual(caught.exception.code, "admission_conflict")
        self.assertTrue(overlay_a.is_file())
        self.assertEqual(handle_a.undefine_calls, 0)
        self.assertEqual(handle_a.destroy_calls, 0)

    def test_a_second_session_cannot_export_the_same_workspace(self) -> None:
        # Given: session A exports its workspace
        manager_a = self.build_manager()
        manager_a.ensure_vm(
            SessionSpec(session_id=SESSION_A, workspace_cwd=str(self.workspace_a))
        )

        # When: session B (another profile) asks for the same realpath
        manager_b = self.build_manager()
        with self.assertRaises(IsolationError) as caught:
            manager_b.ensure_vm(
                SessionSpec(session_id=SESSION_B, workspace_cwd=str(self.workspace_a))
            )

        # Then: the single-export invariant still refuses it
        self.assertEqual(caught.exception.code, "workspace_busy")


class ReapIsolationTest(ParallelHarness):
    def test_reaping_one_manager_leaves_a_sibling_vm_untouched(self) -> None:
        # Given: two managers, each with a live VM in the shared overlay dir
        manager_a = self.build_manager()
        manager_b = self.build_manager()
        vm_a, vm_b = self.start_two(manager_a, manager_b)
        overlay_b = Path(vm_b.domain.overlay or "")
        handle_b = self.connection.defined[domain_name(vm_b.spec)]
        self.assertTrue(overlay_b.is_file())

        # When: manager A reaps orphans (its own session table sees only session A)
        reaped = manager_a.reap_orphans()

        # Then: the sibling VM and its overlay survive A's cleanup
        self.assertEqual(reaped, 0)
        self.assertTrue(overlay_b.is_file())
        self.assertEqual(handle_b.undefine_calls, 0)
        self.assertEqual(handle_b.destroy_calls, 0)
        self.assertTrue(Path(vm_a.domain.overlay or "").is_file())

    def test_sibling_vm_still_dispatches_after_a_foreign_reap(self) -> None:
        # Given: two live sessions and a reap performed by the other manager
        manager_a = self.build_manager()
        endpoint_b = FakeEndpoint([result_message("still-alive")])
        manager_b = self.build_manager(registry=FakeRegistry({SESSION_B: endpoint_b}))
        self.start_two(manager_a, manager_b)
        manager_a.reap_orphans()

        # When: session B dispatches a turn after the foreign reap
        result = manager_b.run_task(
            {"task_id": TASK_B, "metadata": {"session": SESSION_B}},
            str(self.workspace_b),
            dispatch_payload(SESSION_B, PROFILE_B, TASK_B),
        )

        # Then: the sibling is unaffected - its frame and result still flow
        self.assertEqual(result["status"], "ok")
        self.assertEqual(result["output_text"], "still-alive")
        self.assertEqual([kind for kind, _ in endpoint_b.sent], [MessageKind.TASK])

    def test_reap_skips_an_overlay_leased_by_a_live_sibling(self) -> None:
        # Given: an overlay leased by a live sibling process (another guard)
        self.overlay_dir().mkdir(parents=True, exist_ok=True)
        overlay = self.overlay_dir() / "crashed-session-00000000.qcow2"
        overlay.write_bytes(b"orphan")
        sibling = OverlayLeaseGuard()
        sibling.acquire(overlay, SESSION_B)
        manager = self.build_manager()

        try:
            # When: this manager reaps while the sibling still holds the lease
            reaped_while_leased = manager.reap_orphans()

            # Then: the live sibling's overlay is never touched
            self.assertEqual(reaped_while_leased, 0)
            self.assertTrue(overlay.is_file())
        finally:
            sibling.close()

        # And: once the sibling is gone the same overlay is a true orphan
        self.assertEqual(manager.reap_orphans(), 1)
        self.assertFalse(overlay.exists())

    def test_reap_removes_a_true_orphan_overlay(self) -> None:
        # Given: an overlay no live process leases
        self.overlay_dir().mkdir(parents=True, exist_ok=True)
        orphan = self.overlay_dir() / "unknown-session-99999999.qcow2"
        orphan.write_bytes(b"orphan")

        # When: the manager reaps
        manager = self.build_manager()
        reaped = manager.reap_orphans()

        # Then: the true orphan is removed
        self.assertEqual(reaped, 1)
        self.assertFalse(orphan.exists())


class OffModeUnchangedTest(ParallelHarness):
    def test_off_mode_starts_no_vm_and_writes_no_parallel_state(self) -> None:
        # Given: an off-mode manager and two sessions
        off_config = IsolationConfig.from_env({}, data_root=self.root)
        manager = self.build_manager(config=off_config)

        # When: both sessions ask for a VM
        for session_id, workspace in (
            (SESSION_A, self.workspace_a),
            (SESSION_B, self.workspace_b),
        ):
            with self.assertRaises(IsolationError) as caught:
                manager.ensure_vm(
                    SessionSpec(session_id=session_id, workspace_cwd=str(workspace))
                )
            # Then: the off path is refused before any side effect
            self.assertEqual(caught.exception.code, "isolation_not_enabled")

        self.assertEqual(self.connection.defined, {})
        self.assertEqual(self.runner.calls, [])
        self.assertFalse(self.overlay_dir().exists())
        self.assertFalse(any(self.root.glob("**/*.lease")))


if __name__ == "__main__":
    unittest.main()
