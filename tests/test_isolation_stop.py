#!/usr/bin/env python3
"""Stop/cancel propagation and guaranteed VM teardown (plan todo 11).

A mobile Stop keeps its existing transitions (``waiting_approval``/``processing``
-> ``cancelling``, ``stop|kill`` -> ``dead_letter``; ``cli_tasks.cancel_task``)
and must additionally cross the VM boundary: the manager sends ``cancel`` over
the channel, waits briefly for the guest acknowledgement (a
``result{status:"canceled"}`` frame) and then destroys the domain and removes the
overlay - ack or no ack. A mid-task Stop that gets a cancellation ack ends only
that task and retains the session VM; an unacknowledged cancellation is treated
as unrecoverable and tears the VM down. Explicit session cancellation still
destroys the VM.

Adversarial classes covered here:

* misleading success - every test asserts the exact status/reason and the
  concrete teardown (destroy/undefine calls, overlay absence, released binding),
  never just a return value;
* stale state - after a stop the reaper finds nothing (no orphan domain/overlay);
* resumable/cancel - a stop mid-run beats a just-collected approval and a
  completion race, and the guest result vocabulary stays the DB's own;
* fail-closed - a canceled guest result is accepted and normalized (regression
  for ``invalid_result_status``), a malformed ack cannot abort the teardown.

Every boundary is a fake (libvirt connection, ``qemu-img`` runner, channel
registry, preflight, workspace policy), so the suite runs without KVM.
"""

from __future__ import annotations

import os
import socket
import sqlite3
import sys
import tempfile
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
from factory.core.cli_tasks import create_task  # noqa: E402
from factory.core.isolation.channel import (  # noqa: E402
    ChannelConfig,
    ChannelMessage,
    MessageKind,
    PeerCid,
    PeerIdentity,
    PeerSession,
    SessionMap,
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
from factory.core.isolation.manager import (  # noqa: E402
    CANCEL_FRAME_REASON,
    DEFAULT_CANCEL_ACK_TIMEOUT_S,
    STOP_REASON_CANCEL_TIMEOUT,
    STOP_REASON_CANCELED,
    STOP_REASON_NO_VM,
    IsolationManager,
    ManagerSeams,
    SessionSpec,
    StopReport,
)
from factory.core.isolation.preflight import REASON_OK, PreflightResult  # noqa: E402
from factory.core.isolation.workspace import WorkspacePolicy  # noqa: E402
from factory.core.mobile_bridge.service import MobileService  # noqa: E402
from factory.core.runtime import get_db_path  # noqa: E402
from factory.core.schema import initialize_database  # noqa: E402
from factory.core.worker import Worker  # noqa: E402

FAKE_CID = 42
SESSION_ID = "sess-1"
TASK_ID = "task-1"


# --------------------------------------------------------------------------- #
# Fakes: libvirt domain/connection and the host channel registry
# --------------------------------------------------------------------------- #


class _FakeDomainHandle:
    """Minimal ``libvirt.virDomain``: lifecycle counters + live vsock XML."""

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


class _FakeConnection:
    def __init__(self) -> None:
        self.defined: dict[str, _FakeDomainHandle] = {}
        self.closed = False

    def defineXML(self, xml: str) -> _FakeDomainHandle:
        import xml.etree.ElementTree as ET

        name = ET.fromstring(xml).findtext("name") or ""
        handle = _FakeDomainHandle(name, cid=FAKE_CID + len(self.defined))
        self.defined[name] = handle
        return handle

    def lookupByName(self, name: str) -> _FakeDomainHandle:
        return self.defined[name]

    def close(self) -> None:
        self.closed = True


class _FakeEndpoint:
    """Fake host half of one guest connection; queued inbound frames."""

    def __init__(self, messages: list[ChannelMessage] | None = None) -> None:
        self.sent: list[tuple[MessageKind, dict[str, Any]]] = []
        self._messages = list(messages or [])
        self.closed = False
        self.recv_timeouts: list[float] = []

    def send(self, kind: MessageKind, payload: dict[str, Any]) -> None:
        self.sent.append((kind, dict(payload)))

    def recv_from_guest(self, timeout_s: float | None = None) -> ChannelMessage | None:
        if timeout_s is not None:
            self.recv_timeouts.append(float(timeout_s))
        return self._messages.pop(0) if self._messages else None

    def close(self) -> None:
        self.closed = True


class _StopAwareEndpoint:
    """Fake endpoint whose only inbound frame is the cancel acknowledgement.

    It answers the ``cancel`` frame with ``result{status:"canceled"}`` when
    ``ack`` is true; otherwise the guest stays silent and the manager must
    destroy the VM on the TTL. The recorded ``recv_timeouts`` prove the manager
    asked the channel for a bounded wait instead of polling unbounded.
    """

    def __init__(self, *, ack: bool) -> None:
        self.sent: list[tuple[MessageKind, dict[str, Any]]] = []
        self.closed = False
        self.recv_timeouts: list[float] = []
        self._ack = ack
        self._ack_message: ChannelMessage | None = None

    def send(self, kind: MessageKind, payload: dict[str, Any]) -> None:
        self.sent.append((kind, dict(payload)))
        if kind is MessageKind.CANCEL and self._ack and self._ack_message is None:
            self._ack_message = ChannelMessage(
                kind=MessageKind.RESULT, seq=2, payload=canceled_result_payload()
            )

    def recv_from_guest(self, timeout_s: float | None = None) -> ChannelMessage | None:
        if timeout_s is not None:
            self.recv_timeouts.append(float(timeout_s))
        if self._ack_message is not None:
            message, self._ack_message = self._ack_message, None
            return message
        return None

    def close(self) -> None:
        self.closed = True


class _FakeRegistry:
    def __init__(self) -> None:
        self.endpoints: dict[str, _FakeEndpoint] = {}
        self.close_calls = 0

    def session_for(self, session_id: PeerCid) -> _FakeEndpoint | None:
        return self.endpoints.get(str(session_id))

    def close(self) -> None:
        self.close_calls += 1


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #


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


def canceled_result_payload() -> dict[str, Any]:
    """The guest runner's documented shape for a host-canceled run."""
    return {"status": "canceled", "output_text": "", "usage": {}, "tool_calls_count": 0}


def result_message(status: str = "ok", **overrides: Any) -> ChannelMessage:
    payload: dict[str, Any] = {
        "status": status,
        "output_text": "",
        "usage": {},
        "tool_calls_count": 0,
    }
    payload.update(overrides)
    return ChannelMessage(kind=MessageKind.RESULT, seq=1, payload=payload)


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
        "current_session": SESSION_ID,
        "source_platform": "android",
        "artifact_workdir": "",
    }
    payload.update(overrides)
    return payload


class StopHarness(unittest.TestCase):
    """Temp root, enabled kvm config, fakes for every host boundary."""

    CANCEL_TTL_S = 0.05

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="igorek-stop-")
        self.root = Path(self._tmp.name)
        self.workspace = self.root / "workspace"
        self.workspace.mkdir()
        self.connection = _FakeConnection()
        self.registry = _FakeRegistry()
        self.session_map = SessionMap()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def build_manager(self) -> IsolationManager:
        seams = ManagerSeams(
            connect=lambda uri: self.connection,
            runner=lambda argv: CommandResult(0),
            channel=self.registry,
            session_map=self.session_map,
            workspace_policy=WorkspacePolicy(
                home=self.root / "home",
                data_root=self.root / "data",
                host_uid=os.getuid(),
            ),
            preflight_fn=lambda config: PreflightResult(
                ok=True, mode=config.mode, reason=REASON_OK
            ),
        )
        return IsolationManager(
            enabled_config(self.root),
            seams=seams,
            cancel_ack_timeout_s=self.CANCEL_TTL_S,
        )

    def session(self, session_id: str = SESSION_ID) -> SessionSpec:
        return SessionSpec(session_id=session_id, workspace_cwd=str(self.workspace))

    def domain(self, session_id: str = SESSION_ID) -> _FakeDomainHandle:
        spec = DomainSpec(
            session_id=session_id,
            base_image=self.root / "base.qcow2",
            workspace_host_path=self.workspace,
            overlay_dir=self.root / "overlays",
        )
        return self.connection.defined[domain_name(spec)]

    def task(self, session_id: str = SESSION_ID) -> dict[str, Any]:
        return {"task_id": TASK_ID, "metadata": {"session": session_id}}

    def start_vm_with_overlay(
        self, manager: IsolationManager, session_id: str = SESSION_ID
    ) -> Path:
        """Bring up the fake VM and materialize the overlay the VM would own."""
        vm = manager.ensure_vm(self.session(session_id))
        overlay = Path(vm.domain.overlay or "")
        overlay.parent.mkdir(parents=True, exist_ok=True)
        overlay.write_bytes(b"overlay")
        return overlay

    def assert_vm_gone(
        self,
        overlay: Path,
        endpoint: _FakeEndpoint | _StopAwareEndpoint | None,
        session_id: str = SESSION_ID,
    ) -> None:
        """The teardown actually happened: no domain, no overlay, no binding."""
        domain = self.domain(session_id)
        self.assertEqual(domain.destroy_calls, 1)
        self.assertEqual(domain.undefine_calls, 1)
        self.assertEqual(domain.isActive(), 0)
        self.assertFalse(overlay.exists())
        self.assertIsNone(self.session_map.session_for_cid(PeerCid(FAKE_CID)))
        if endpoint is not None:
            self.assertTrue(endpoint.closed)


# --------------------------------------------------------------------------- #
# (a) stop -> cancel sent -> VM and overlay gone, documented outcome
# --------------------------------------------------------------------------- #


class CancelSentTest(StopHarness):
    def test_cancel_sends_the_cancel_frame_and_destroys_the_vm(self) -> None:
        endpoint = _FakeEndpoint([result_message("canceled")])
        self.registry.endpoints[SESSION_ID] = endpoint
        manager = self.build_manager()
        overlay = self.start_vm_with_overlay(manager)
        self.assertEqual(self.domain().create_calls, 1)

        report = manager.cancel(self.session())

        self.assertEqual(
            report,
            StopReport(
                session_id=SESSION_ID,
                reason=STOP_REASON_CANCELED,
                acknowledged=True,
                domain_destroyed=True,
                overlay_removed=True,
            ),
        )
        self.assertEqual(
            endpoint.sent, [(MessageKind.CANCEL, {"reason": CANCEL_FRAME_REASON})]
        )
        self.assert_vm_gone(overlay, endpoint)

    def test_cancel_is_idempotent_after_the_vm_is_gone(self) -> None:
        endpoint = _FakeEndpoint([result_message("canceled")])
        self.registry.endpoints[SESSION_ID] = endpoint
        manager = self.build_manager()
        self.start_vm_with_overlay(manager)
        manager.cancel(self.session())

        report = manager.cancel(self.session())

        self.assertEqual(report.reason, STOP_REASON_NO_VM)
        self.assertFalse(report.acknowledged)
        self.assertFalse(report.domain_destroyed)
        self.assertFalse(report.overlay_removed)
        # Only the first stop crossed the channel.
        self.assertEqual(len(endpoint.sent), 1)

    def test_the_plain_teardown_does_not_send_a_cancel_frame(self) -> None:
        # The approval-park teardown must stay immediate: no TTL wait, no frame.
        endpoint = _FakeEndpoint()
        self.registry.endpoints[SESSION_ID] = endpoint
        manager = self.build_manager()
        overlay = self.start_vm_with_overlay(manager)

        manager.stop(self.session())

        self.assertEqual(endpoint.sent, [])
        self.assert_vm_gone(overlay, endpoint)


# --------------------------------------------------------------------------- #
# (b) no ack -> forced destroy, cancel_timeout recorded, no orphan
# --------------------------------------------------------------------------- #


class CancelTimeoutTest(StopHarness):
    def test_a_silent_guest_is_destroyed_anyway_with_cancel_timeout(self) -> None:
        endpoint = _StopAwareEndpoint(ack=False)
        self.registry.endpoints[SESSION_ID] = endpoint
        manager = self.build_manager()
        overlay = self.start_vm_with_overlay(manager)

        report = manager.cancel(self.session())

        self.assertEqual(report.reason, STOP_REASON_CANCEL_TIMEOUT)
        self.assertFalse(report.acknowledged)
        self.assertTrue(report.domain_destroyed)
        self.assertTrue(report.overlay_removed)
        # The manager asked the channel for a bounded ack wait and never more
        # than the configured TTL.
        self.assertTrue(endpoint.recv_timeouts)
        self.assertLessEqual(max(endpoint.recv_timeouts), self.CANCEL_TTL_S + 1e-6)
        self.assert_vm_gone(overlay, endpoint)
        # Nothing is left for the reaper: no orphan domain and no orphan overlay.
        self.assertEqual(manager.reap_orphans(), 0)

    def test_a_stop_without_a_live_endpoint_still_destroys_the_vm(self) -> None:
        # The guest never connected (or already vanished); teardown is not
        # conditional on the channel.
        manager = self.build_manager()
        overlay = self.start_vm_with_overlay(manager)

        report = manager.cancel(self.session())

        self.assertEqual(report.reason, STOP_REASON_CANCEL_TIMEOUT)
        self.assertFalse(report.acknowledged)
        self.assert_vm_gone(overlay, None)
        self.assertEqual(manager.reap_orphans(), 0)

    def test_the_default_ack_ttl_is_positive_and_bounded(self) -> None:
        self.assertGreater(DEFAULT_CANCEL_ACK_TIMEOUT_S, 0)
        self.assertLessEqual(DEFAULT_CANCEL_ACK_TIMEOUT_S, 60)

    def test_cancel_ack_ignores_unrelated_frames_without_attribute_error(self) -> None:
        # Given: a guest that emits heartbeat/host-op/error/cancel frames before acks
        frames = [
            ChannelMessage(kind=kind, seq=index, payload=payload)
            for index, (kind, payload) in enumerate(
                (
                    (MessageKind.HEARTBEAT, {"state": "alive"}),
                    (MessageKind.HOST_OP, {"op": "runtime.status", "args": {}}),
                    (MessageKind.ERROR, {"reason": "boom"}),
                    (MessageKind.CANCEL, {"reason": "user"}),
                )
            )
        ]
        endpoint = _FakeEndpoint([*frames, result_message("canceled")])
        self.registry.endpoints[SESSION_ID] = endpoint
        manager = self.build_manager()
        overlay = self.start_vm_with_overlay(manager)

        # When: a stop waits for the cancel acknowledgement
        result = manager.run_task(
            self.task(),
            str(self.workspace),
            dispatch_payload(),
            cancel_check=lambda: True,
        )

        # Then: the ignored frames never crash the ack loop and the VM stays warm
        self.assertEqual(result["status"], "fail")
        self.assertEqual(result["failure_reason"], STOP_REASON_CANCELED)
        self.assertEqual(self.domain().destroy_calls, 0)
        self.assertTrue(overlay.exists())


# --------------------------------------------------------------------------- #
# (c) waiting_approval / no VM: handled without error, not cancel-failed
# --------------------------------------------------------------------------- #


class NoVmStopTest(StopHarness):
    def test_stop_for_a_session_without_a_vm_is_a_clean_noop(self) -> None:
        manager = self.build_manager()

        report = manager.cancel(self.session())

        self.assertEqual(report.reason, STOP_REASON_NO_VM)
        self.assertFalse(report.acknowledged)
        self.assertFalse(report.domain_destroyed)
        self.assertFalse(report.overlay_removed)
        # Nothing was touched: no domain defined, no channel traffic.
        self.assertEqual(self.connection.defined, {})
        self.assertEqual(self.registry.endpoints, {})

    def test_stop_after_a_park_is_also_a_clean_noop(self) -> None:
        # todo 9 stops the guest on a blocked call: the parked task has no VM.
        manager = self.build_manager()

        manager.stop(self.session())
        report = manager.cancel(self.session())

        self.assertEqual(report.reason, STOP_REASON_NO_VM)
        self.assertEqual(self.connection.defined, {})


class ParkedTaskTransitionTest(unittest.TestCase):
    """A ``waiting_approval`` row keeps its existing transition, never `fail`."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="igorek-stop-parked-")
        self._env = patch.dict(os.environ, {"IGOREK_FACTORY_HOME": self._tmp.name})
        self._env.start()
        initialize_database(get_db_path())
        self.service = MobileService(profile="cc-local-8081", workdir=self._tmp.name)

    def tearDown(self) -> None:
        self._env.stop()
        self._tmp.cleanup()

    def task_status(self, task_id: str) -> tuple[str, str | None]:
        with sqlite3.connect(get_db_path()) as conn:
            row = conn.execute(
                "SELECT status, error_message FROM tasks WHERE task_id = ?",
                (task_id,),
            ).fetchone()
        self.assertIsNotNone(row)
        return str(row[0]), row[1]

    def test_a_waiting_approval_task_is_not_cancel_failed(self) -> None:
        created = create_task(
            text="ждёт подтверждения",
            profile="cc-local-8081",
            metadata={"session": "chat-parked", "source": "mobile_bridge"},
        )
        task_id = created["task_id"]
        with sqlite3.connect(get_db_path()) as conn:
            conn.execute(
                "UPDATE tasks SET status = 'waiting_approval' WHERE task_id = ?",
                (task_id,),
            )
            conn.commit()

        result = self.service.cancel_task(task_id, mode="cancel")

        self.assertTrue(result["cancelled"])
        self.assertEqual(result["status"], "cancelling")
        self.assertFalse(result["terminal"])
        status, error_message = self.task_status(task_id)
        self.assertEqual(status, "cancelling")
        self.assertIsNone(error_message)


# --------------------------------------------------------------------------- #
# (d) regression: the guest's ``canceled`` result is accepted, not rejected
# --------------------------------------------------------------------------- #


class CanceledResultRegressionTest(StopHarness):
    def test_a_canceled_guest_result_is_accepted_and_normalized(self) -> None:
        endpoint = _FakeEndpoint([result_message("canceled")])
        self.registry.endpoints[SESSION_ID] = endpoint
        manager = self.build_manager()

        result = manager.run_task(self.task(), str(self.workspace), dispatch_payload())

        # `canceled` is the guest runner's documented shape; the manager accepts
        # it (no `invalid_result_status`) and folds it into the terminal `fail`
        # the worker already maps to the DB.
        self.assertEqual(
            result,
            {
                "status": "fail",
                "output_text": "",
                "usage": {},
                "tool_calls_count": 0,
                "failure_reason": STOP_REASON_CANCELED,
                "failure_details": "guest run canceled",
                "error": "guest run canceled",
            },
        )
        # Never a park: a canceled android task ends, it does not wait.
        self.assertNotIn("approval_blocked_tools", result)


# --------------------------------------------------------------------------- #
# stop mid-run: cancel only the task; an acknowledged VM stays warm
# --------------------------------------------------------------------------- #


class StopMidRunTest(StopHarness):
    def test_a_stop_mid_run_sends_cancel_and_keeps_acknowledged_session_vm(
        self,
    ) -> None:
        endpoint = _StopAwareEndpoint(ack=True)
        self.registry.endpoints[SESSION_ID] = endpoint
        manager = self.build_manager()
        overlay = self.start_vm_with_overlay(manager)

        result = manager.run_task(
            self.task(), str(self.workspace), dispatch_payload(), cancel_check=lambda: True
        )

        self.assertEqual(result["status"], "fail")
        self.assertEqual(result["failure_reason"], STOP_REASON_CANCELED)
        self.assertEqual(
            [kind for kind, _ in endpoint.sent], [MessageKind.TASK, MessageKind.CANCEL]
        )
        self.assertEqual(
            endpoint.sent[1], (MessageKind.CANCEL, {"reason": CANCEL_FRAME_REASON})
        )
        self.assertEqual(self.domain().destroy_calls, 0)
        self.assertEqual(self.domain().undefine_calls, 0)
        self.assertTrue(self.domain().active)
        self.assertTrue(overlay.exists())
        self.assertIsNotNone(self.session_map.session_for_cid(PeerCid(FAKE_CID)))
        self.assertTrue(endpoint.closed)

    def test_a_stop_mid_run_without_ack_records_cancel_timeout_and_leaves_nothing(
        self,
    ) -> None:
        endpoint = _StopAwareEndpoint(ack=False)
        self.registry.endpoints[SESSION_ID] = endpoint
        manager = self.build_manager()
        overlay = self.start_vm_with_overlay(manager)

        result = manager.run_task(
            self.task(), str(self.workspace), dispatch_payload(), cancel_check=lambda: True
        )

        self.assertEqual(result["status"], "fail")
        self.assertEqual(result["failure_reason"], STOP_REASON_CANCEL_TIMEOUT)
        self.assertIn(STOP_REASON_CANCEL_TIMEOUT, str(result["error"]))
        self.assert_vm_gone(overlay, endpoint)
        self.assertEqual(manager.reap_orphans(), 0)

    def test_a_stop_mid_run_beats_a_just_collected_approval(self) -> None:
        # The guest already asked for approval and the person pressed Stop: the
        # stop owns the task outcome, but an acknowledged session VM is retained.
        blocked = {
            "tool": "Write",
            "input": {"file_path": "/workspace/out.txt"},
            "target": "/workspace/out.txt",
            "signature": 'Write:{"file_path": "/workspace/out.txt"}',
            "tool_use_id": "call_blocked_1",
            "reason": "нужно подтверждение",
            "session_rule": {
                "rule_type": "exact_tool_call",
                "tool": "Write",
                "signature": 'Write:{"file_path": "/workspace/out.txt"}',
            },
        }
        approval = ChannelMessage(
            kind=MessageKind.APPROVAL_REQUEST, seq=1, payload={"record": blocked}
        )
        endpoint = _FakeEndpoint([approval, result_message("canceled")])
        self.registry.endpoints[SESSION_ID] = endpoint
        manager = self.build_manager()
        overlay = self.start_vm_with_overlay(manager)
        checks = iter([False, True])

        result = manager.run_task(
            self.task(),
            str(self.workspace),
            dispatch_payload(),
            cancel_check=lambda: next(checks, True),
        )

        self.assertEqual(result["status"], "fail")
        self.assertEqual(result["failure_reason"], STOP_REASON_CANCELED)
        self.assertNotIn("approval_blocked_tools", result)
        self.assertNotEqual(result["status"], "waiting_approval")
        self.assertEqual(self.domain().destroy_calls, 0)
        self.assertEqual(self.domain().undefine_calls, 0)
        self.assertTrue(self.domain().active)
        self.assertTrue(overlay.exists())
        self.assertTrue(endpoint.closed)

    def test_a_cancel_frame_is_valid_on_the_real_channel(self) -> None:
        # Pin the wire payload against the real channel policy (a host path or a
        # shell key would make the frame fail closed at send time).
        left, right = socket.socketpair()
        config = ChannelConfig()
        try:
            host = PeerSession(left, config, PeerIdentity())
            guest = PeerSession(right, config, PeerIdentity())
            host.send(MessageKind.CANCEL, {"reason": CANCEL_FRAME_REASON})
            message = guest.read(timeout_s=1.0)
        finally:
            left.close()
            right.close()
        self.assertIsNotNone(message)
        self.assertIs(message.kind, MessageKind.CANCEL)
        self.assertEqual(dict(message.payload), {"reason": CANCEL_FRAME_REASON})


# --------------------------------------------------------------------------- #
# worker seam: the mobile `cancelling` transition reaches the manager
# --------------------------------------------------------------------------- #


class WorkerCancelSeamTest(unittest.TestCase):
    """The worker hands the host stop signal to the isolated dispatch."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="igorek-stop-seam-")
        self._env = patch.dict(os.environ, {"IGOREK_FACTORY_HOME": self._tmp.name})
        self._env.start()
        initialize_database(get_db_path())

    def tearDown(self) -> None:
        self._env.stop()
        self._tmp.cleanup()

    def test_the_worker_hands_the_mobile_cancelling_state_to_the_manager(self) -> None:
        created = create_task(text="долгая задача", profile="cc-seam")
        task_id = str(created["task_id"])

        class _RecordingManager:
            def __init__(self) -> None:
                self.calls: list[dict[str, Any]] = []

            def run_task(
                self,
                *,
                task: dict[str, Any],
                workdir: str,
                payload: dict[str, Any],
                cancel_check: Any = None,
            ) -> dict[str, Any]:
                self.calls.append(
                    {
                        "task": task,
                        "workdir": workdir,
                        "payload": payload,
                        "cancel_check": cancel_check,
                    }
                )
                return {
                    "status": "fail",
                    "failure_reason": STOP_REASON_CANCELED,
                    "output_text": "",
                    "usage": {},
                    "tool_calls_count": 0,
                }

        manager = _RecordingManager()
        worker = Worker.__new__(Worker)
        worker.profile = "cc-seam"
        with patch.object(worker_module, "_get_isolation_manager", return_value=manager):
            result = worker._execute_cc_backend_isolated(
                task={"task_id": task_id, "metadata": {"session": "sess-seam"}},
                workdir=self._tmp.name,
                payload={"task_id": task_id},
            )

        self.assertEqual(result["failure_reason"], STOP_REASON_CANCELED)
        self.assertEqual(len(manager.calls), 1)
        cancel_check = manager.calls[0]["cancel_check"]
        self.assertTrue(callable(cancel_check))
        # A queued task is not stopped...
        self.assertFalse(cancel_check())
        # ...and the mobile Stop transition (`cancelling`) is what the manager
        # observes; no new task-status vocabulary is introduced.
        with sqlite3.connect(get_db_path()) as conn:
            conn.execute(
                "UPDATE tasks SET status = 'cancelling' WHERE task_id = ?", (task_id,)
            )
            conn.commit()
        self.assertTrue(cancel_check())


if __name__ == "__main__":
    unittest.main()
