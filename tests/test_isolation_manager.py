#!/usr/bin/env python3
"""Unit tests for the per-session isolation manager facade (no KVM required).

Every external boundary the manager composes is injected as a fake: the libvirt
connection, the ``qemu-img`` runner, the vsock channel registry, the preflight
and the workspace policy. The suite therefore runs on any host; the real-KVM
paths live behind the availability-guarded smoke in
``tests/test_isolation_domain.py``.

Adversarial classes covered here:

* misleading success - assertions check concrete sent frames / normalized
  values, never just truthiness;
* stale state - ``reap_orphans`` must delete a leftover overlay and undefine a
  leftover domain that no live session owns, while leaving live sessions alone;
* fail-closed - every refusal raises the canonical ``IsolationError`` with a
  machine-readable ``code`` and leaves no half-started VM behind.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
import time
import unittest
import xml.etree.ElementTree as ET
from dataclasses import replace
from pathlib import Path
from typing import Any
from unittest.mock import patch

REPO_ROOT = Path(
    os.environ.get("IGOREK_FACTORY_REPO", Path(__file__).resolve().parents[1])
)
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

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
    HostCapabilities,
    IsolationConfig,
)
from factory.core.isolation.broker_ops import BROKER_VERSION  # noqa: E402
from factory.core.isolation.host_op_runtime import (  # noqa: E402
    build_host_op_runtime,
)
from factory.core.isolation.session_egress import SessionEgress  # noqa: E402
from factory.core.isolation.domain import (  # noqa: E402
    CommandResult,
    DomainSpec,
    domain_name,
)
from factory.core.isolation.errors import IsolationError  # noqa: E402
from factory.core.isolation.artifact_map import (  # noqa: E402
    ROUTE_FILENAME,
    guest_artifact_dir,
    host_artifact_dir,
    read_artifact_route,
)
from factory.core.isolation.extensions import (  # noqa: E402
    MCP_CREDENTIAL_EPHEMERAL_ENV,
    MCP_CREDENTIAL_HOST_BROKER,
    MCP_CREDENTIAL_NONE,
    MCP_PLACEMENT_HOST_BROKERED,
    McpServerPlacement,
    TaskExtensions,
)
from factory.core.isolation.manager import (  # noqa: E402
    DOMAIN_NAME_PREFIX,
    GUEST_ARTIFACT_DIR,
    IsolationManager,
    ManagerSeams,
    SessionSpec,
    resolve_vsock_cid,
)
from factory.core.isolation.preflight import REASON_OK, PreflightResult  # noqa: E402
from factory.core.isolation.secret_policy import (  # noqa: E402
    MODE_EPHEMERAL_ENV,
    ProviderSecretPlan,
)
from factory.core.isolation.workspace import (  # noqa: E402
    GUEST_WORKSPACE_PATH,
    WorkspacePolicy,
)

FAKE_CID = 42
GUEST_ENV_LEAK_REASON = "guest_env_secret_leak"
#: Read-only host op (no subprocess); the todo-22 host-op tests allow it by env.
READ_ONLY_OP = "runtime.status"


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
    """Minimal stand-in for ``libvirt.virDomain`` (lifecycle + live XML)."""

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
            raise RuntimeError("create refused")
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

    def __init__(
        self, cid: int | None = FAKE_CID, *, fail_create: bool = False
    ) -> None:
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


class FakeEndpoint:
    """Fake host half of one guest connection (PeerSession stand-in)."""

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


class HeartbeatEndpoint(FakeEndpoint):
    """Endpoint that answers every receive with a heartbeat and no result."""

    def __init__(self, *, beat_delay_s: float = 0.02) -> None:
        super().__init__()
        self.beats = 0
        self._beat_delay_s = beat_delay_s

    def recv_from_guest(self, timeout_s: float | None = None) -> ChannelMessage | None:
        time.sleep(self._beat_delay_s)
        self.beats += 1
        return ChannelMessage(
            kind=MessageKind.HEARTBEAT, seq=self.beats, payload={"state": "alive"}
        )


class FakeRegistry:
    """Fake channel registry keyed by the host-owned session id."""

    def __init__(self, endpoints: dict[str, FakeEndpoint] | None = None) -> None:
        self.endpoints = dict(endpoints or {})
        self.closed = False

    def session_for(self, session_id: SessionId) -> FakeEndpoint | None:
        return self.endpoints.get(str(session_id))

    def close(self) -> None:
        self.closed = True


def make_policy(root: Path) -> WorkspacePolicy:
    return WorkspacePolicy(
        home=root / "home",
        data_root=root / "data",
        host_uid=os.getuid(),
    )


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


def dispatch_payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "task_id": "task-1",
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
        "current_session": "sess-1",
        "source_platform": "mobile_bridge",
        "source_chat_id": "42",
        "input_files": [],
        "enabled_tools": [{"name": "Read"}],
        "history_messages": [{"role": "user", "content": "hi"}],
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


class ManagerHarness(unittest.TestCase):
    """Shared fixture: temp root, enabled kvm config, fakes for every boundary."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="igorek-manager-")
        self.root = Path(self._tmp.name)
        self.workspace = self.root / "workspace"
        self.workspace.mkdir()
        self.overlay_dir = self.root / "overlays"
        self.connection = FakeConnection()
        self.registry = FakeRegistry()
        self.session_map = SessionMap()
        self.preflight_calls: list[IsolationConfig] = []

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def build_manager(
        self,
        *,
        preflight_ok: bool = True,
        cid_resolver: Any = None,
        connection: FakeConnection | None = None,
        config: IsolationConfig | None = None,
        use_real_preflight: bool = False,
        seams_overrides: dict[str, Any] | None = None,
    ) -> IsolationManager:
        manager_connection = connection or self.connection

        def tracking_preflight(cfg: IsolationConfig) -> PreflightResult:
            self.preflight_calls.append(cfg)
            if preflight_ok:
                return PreflightResult(ok=True, mode=cfg.mode, reason=REASON_OK)
            return PreflightResult(
                ok=False,
                mode=cfg.mode,
                reason="isolation_unavailable:capability_unavailable:kvm_missing",
            )

        seams = ManagerSeams(
            connect=lambda uri: manager_connection,
            runner=lambda argv: CommandResult(0),
            channel=self.registry,
            session_map=self.session_map,
            workspace_policy=make_policy(self.root),
        )
        if not use_real_preflight:
            seams = ManagerSeams(
                connect=seams.connect,
                runner=seams.runner,
                channel=seams.channel,
                session_map=seams.session_map,
                workspace_policy=seams.workspace_policy,
                preflight_fn=tracking_preflight,
                cid_resolver=cid_resolver or resolve_vsock_cid,
            )
        # The egress renderer resolves DNS in production; unit tests always
        # inject a deterministic builder unless a test overrides it.
        overrides: dict[str, Any] = {"egress": fake_egress_builder}
        overrides.update(seams_overrides or {})
        return IsolationManager(
            config or enabled_config(self.root), seams=replace(seams, **overrides)
        )

    def session(self, session_id: str = "sess-1") -> SessionSpec:
        return SessionSpec(session_id=session_id, workspace_cwd=str(self.workspace))

    def domain_name_for(self, session_id: str = "sess-1") -> str:
        return domain_name(
            DomainSpec(
                session_id=session_id,
                base_image=self.root / "base.qcow2",
                workspace_host_path=self.workspace,
                overlay_dir=self.overlay_dir,
            )
        )

    def task(
        self, session_id: str = "sess-1", task_id: str = "task-1"
    ) -> dict[str, Any]:
        return {"task_id": task_id, "metadata": {"session": session_id}}

    def host_op_runtime(self) -> tuple[Any, Path]:
        """A real todo-22 gate over temp audit/park files, with the op allowed."""
        audit_path = self.root / "host_op_audit.jsonl"
        with patch.dict(os.environ, {"IGOREK_ISOLATION_HOST_OPS_ALLOW": READ_ONLY_OP}):
            runtime = build_host_op_runtime(
                audit_path=audit_path, park_path=self.root / "host_op_parked.json"
            )
        assert runtime is not None
        return runtime, audit_path

    @staticmethod
    def audit_phases(path: Path) -> list[str]:
        return [
            str(json.loads(line).get("phase"))
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]


class EnsureVmTest(ManagerHarness):
    def test_ensure_vm_defines_starts_and_binds_peer_cid(self) -> None:
        manager = self.build_manager()
        vm = manager.ensure_vm(self.session())

        self.assertEqual(vm.session_id, "sess-1")
        self.assertEqual(vm.peer_cid, PeerCid(FAKE_CID))
        self.assertEqual(vm.export["host_path"], str(self.workspace.resolve()))
        self.assertEqual(vm.export["guest_path"], GUEST_WORKSPACE_PATH)
        self.assertEqual(list(self.connection.defined), [self.domain_name_for()])
        handle = self.connection.defined[self.domain_name_for()]
        self.assertEqual(handle.create_calls, 1)
        self.assertEqual(self.session_map.session_for_cid(PeerCid(FAKE_CID)), "sess-1")

    def test_ensure_vm_starts_the_vsock_listener(self) -> None:
        from unittest import mock

        import factory.core.isolation.channel as channel_module

        class _Listener:
            def settimeout(self, _value: float) -> None:
                return None

            def accept(self) -> None:
                raise TimeoutError("no guest")

            def close(self) -> None:
                return None

        manager = self.build_manager(seams_overrides={"channel": None})
        self.assertIsNone(manager._listener_thread)
        with mock.patch.object(
            channel_module, "open_vsock_listener", return_value=_Listener()
        ):
            manager.ensure_vm(self.session())
            self.assertIsNotNone(manager._listener_thread)
            self.assertTrue(manager._listener_thread.is_alive())

    def test_new_session_takes_over_an_idle_export(self) -> None:
        # Given: a warm VM for session A on the shared workspace
        manager = self.build_manager()
        manager.ensure_vm(self.session("sess-a"))

        # When: session B asks for the same workspace
        vm = manager.ensure_vm(self.session("sess-b"))

        # Then: the idle holder is stopped and B owns the export
        self.assertEqual("sess-b", vm.session_id)
        self.assertNotIn("sess-a", manager._vms)
        self.assertIsNone(manager._exports.held.get("sess-a"))
        self.assertEqual(
            os.path.realpath(str(self.workspace)),
            manager._exports.held.get("sess-b"),
        )

    def test_ensure_vm_is_idempotent_for_one_session(self) -> None:
        manager = self.build_manager()
        first = manager.ensure_vm(self.session())
        second = manager.ensure_vm(self.session())

        self.assertIs(first, second)
        self.assertEqual(len(self.connection.defined), 1)
        self.assertEqual(
            self.connection.defined[self.domain_name_for()].create_calls, 1
        )

    def test_ensure_vm_creates_a_distinct_domain_per_session(self) -> None:
        manager = self.build_manager()
        other = self.root / "workspace-b"
        other.mkdir()
        manager.ensure_vm(self.session("sess-a"))
        manager.ensure_vm(SessionSpec(session_id="sess-b", workspace_cwd=str(other)))

        self.assertEqual(len(self.connection.defined), 2)
        self.assertNotEqual(
            self.domain_name_for("sess-a"), self.domain_name_for("sess-b")
        )

    def test_ensure_vm_refuses_when_preflight_is_not_ok(self) -> None:
        manager = self.build_manager(preflight_ok=False)
        with self.assertRaises(IsolationError) as caught:
            manager.ensure_vm(self.session())

        self.assertEqual(caught.exception.code, "isolation_unavailable")
        self.assertEqual(
            caught.exception.reason,
            "isolation_unavailable:capability_unavailable:kvm_missing",
        )
        self.assertEqual(self.connection.defined, {})

    def test_ensure_vm_refuses_off_mode_before_any_probe(self) -> None:
        off_config = IsolationConfig.from_env({}, data_root=self.root)
        manager = self.build_manager(config=off_config)
        with self.assertRaises(IsolationError) as caught:
            manager.ensure_vm(self.session())

        self.assertEqual(caught.exception.code, "isolation_not_enabled")
        self.assertEqual(self.preflight_calls, [])
        self.assertEqual(self.connection.defined, {})

    def test_ensure_vm_refuses_denied_workspace_before_preflight(self) -> None:
        manager = self.build_manager()
        denied = SessionSpec(
            session_id="sess-1", workspace_cwd=str(make_policy(self.root).home)
        )
        with self.assertRaises(IsolationError) as caught:
            manager.ensure_vm(denied)

        self.assertEqual(caught.exception.code, "sensitive_path_denied")
        self.assertEqual(self.preflight_calls, [])
        self.assertEqual(self.connection.defined, {})

    def test_ensure_vm_fails_closed_when_cid_is_unresolved(self) -> None:
        manager = self.build_manager(cid_resolver=lambda connection, name: None)
        with self.assertRaises(IsolationError) as caught:
            manager.ensure_vm(self.session())

        self.assertEqual(caught.exception.code, "vsock_cid_unavailable")
        handle = self.connection.defined[self.domain_name_for()]
        self.assertEqual(handle.undefine_calls, 1)
        self.assertEqual(handle.destroy_calls, 1)

    def test_ensure_vm_fails_closed_on_peer_cid_conflict(self) -> None:
        self.session_map.assign(PeerCid(FAKE_CID), SessionId("someone-else"))
        manager = self.build_manager()
        with self.assertRaises(IsolationError) as caught:
            manager.ensure_vm(self.session())

        self.assertEqual(caught.exception.code, "peer_cid_conflict")
        self.assertEqual(
            self.connection.defined[self.domain_name_for()].undefine_calls, 1
        )
        self.assertEqual(
            self.session_map.session_for_cid(PeerCid(FAKE_CID)), "someone-else"
        )

    def test_ensure_vm_fails_closed_when_domain_start_fails(self) -> None:
        broken = FakeConnection(fail_create=True)
        manager = self.build_manager(connection=broken)
        with self.assertRaises(IsolationError) as caught:
            manager.ensure_vm(self.session())

        self.assertEqual(caught.exception.code, "domain_start_failed")
        handle = broken.defined[self.domain_name_for()]
        self.assertEqual(handle.undefine_calls, 1)

    def test_ensure_vm_rebuilds_only_after_libvirt_reports_stopped_domain(self) -> None:
        manager = self.build_manager()
        first_vm = manager.ensure_vm(self.session())
        first_domain = self.connection.defined[self.domain_name_for()]
        first_domain.active = False  # libvirt confirms the guest really stopped

        resumed_vm = manager.ensure_vm(self.session())

        self.assertIsNot(resumed_vm, first_vm)
        self.assertEqual(resumed_vm.export["host_path"], str(self.workspace.resolve()))
        self.assertEqual(first_domain.destroy_calls, 0)
        self.assertEqual(first_domain.undefine_calls, 1)
        self.assertEqual(len(self.connection.defined), 1)
        self.assertTrue(self.connection.defined[self.domain_name_for()].active)

    def test_ensure_vm_rejects_workspace_change_for_live_session(self) -> None:
        other = self.root / "other-workspace"
        other.mkdir()
        manager = self.build_manager()
        manager.ensure_vm(self.session())
        with self.assertRaises(IsolationError) as caught:
            manager.ensure_vm(
                SessionSpec(session_id="sess-1", workspace_cwd=str(other))
            )

        self.assertEqual(caught.exception.code, "workspace_changed")
        self.assertEqual(
            self.connection.defined[self.domain_name_for()].create_calls, 1
        )

    def test_ensure_vm_uses_the_real_preflight_by_default(self) -> None:
        manager = self.build_manager(use_real_preflight=True)
        with self.assertRaises(IsolationError) as caught:
            manager.ensure_vm(self.session())

        # The real preflight refuses (no KVM/libvirt/virtiofsd on a test host)
        # and the manager surfaces the structured refusal verbatim.
        self.assertEqual(caught.exception.code, "isolation_unavailable")
        self.assertTrue(caught.exception.reason.startswith("isolation_unavailable:"))
        self.assertEqual(self.connection.defined, {})


class RunTaskTest(ManagerHarness):
    def test_apk_delivery_is_scoped_in_wire_and_revoked_on_success(self):
        self._check_apk_delivery(False)

    def test_apk_delivery_is_revoked_on_payload_failure(self):
        self._check_apk_delivery(True)

    def _check_apk_delivery(self, fail):
        from factory.core.isolation import manager as manager_module
        endpoint = FakeEndpoint([result_message()])
        self.registry.endpoints["sess-1"] = endpoint
        manager = self.build_manager(seams_overrides={"apk_delivery_enabled": True})
        with patch.object(manager_module, "ApkDelivery") as broker:
            broker.return_value.port = 12346
            broker.return_value.endpoint = "http://10.0.2.2:12346/" + "b" * 64 + "/send-apk"
            if fail:
                with patch.object(manager, "_wire_task_payload", side_effect=IsolationError("fixture_failure", "test")):
                    with self.assertRaises(IsolationError):
                        manager.run_task(self.task(), str(self.workspace), dispatch_payload())
            else:
                manager.run_task(self.task(), str(self.workspace), dispatch_payload())
                self.assertEqual(endpoint.sent[0][1]["apk_delivery_url"], broker.return_value.endpoint)
                self.assertIn(12346, endpoint.sent[0][1]["egress"]["host_local_ports"])
            broker.return_value.close.assert_called_once_with()


    def test_git_gateway_is_host_selected_in_wire_egress_and_revoked_after_dispatch(self):
        from factory.core.isolation import manager as manager_module
        from factory.core.isolation.git_gateway import GitRepositoryGrant
        from factory.core.isolation.git_gateway_contract import GitGatewayRoute
        grant = GitRepositoryGrant("http://192.168.50.10:8034/owner/project", True)
        route = GitGatewayRoute(grant.url, "http://10.0.2.2:12345/" + "a" * 64)
        endpoint = FakeEndpoint([result_message()])
        self.registry.endpoints["sess-1"] = endpoint
        manager = self.build_manager(seams_overrides={"git_repositories": (grant,)})
        with patch.object(manager_module, "grant_for_workspace", return_value=grant), patch.object(manager_module, "GitGateway") as gateway:
            gateway.return_value.route = route
            gateway.return_value.port = 12345
            manager.run_task(self.task(), str(self.workspace), dispatch_payload())
            wire = endpoint.sent[0][1]
            self.assertEqual(wire["git_gateway"], route.to_wire())
            self.assertIn(12345, wire["egress"]["host_local_ports"])
            gateway.assert_called_once_with(grant, "sess-1")
            gateway.return_value.close.assert_called_once_with()


    def test_git_gateway_is_revoked_when_payload_build_fails(self):
        from factory.core.isolation import manager as manager_module
        from factory.core.isolation.git_gateway import GitRepositoryGrant
        grant = GitRepositoryGrant("http://192.168.50.10:8034/owner/project", True)
        self.registry.endpoints["sess-1"] = FakeEndpoint([])
        manager = self.build_manager(seams_overrides={"git_repositories": (grant,)})
        with patch.object(manager_module, "grant_for_workspace", return_value=grant), patch.object(manager_module, "GitGateway") as gateway, patch.object(manager, "_wire_task_payload", side_effect=IsolationError("fixture_failure", "test")):
            gateway.return_value.port = 12345
            with self.assertRaises(IsolationError):
                manager.run_task(self.task(), str(self.workspace), dispatch_payload())
            gateway.return_value.close.assert_called_once_with()

    def test_run_task_sends_wire_payload_and_normalizes_result(self) -> None:
        endpoint = FakeEndpoint(
            [result_message({"approval_blocked_tools": [{"tool": "Write"}]})]
        )
        self.registry.endpoints["sess-1"] = endpoint
        manager = self.build_manager()

        result = manager.run_task(
            self.task(), str(self.workspace), dispatch_payload()
        )

        self.assertEqual(
            result,
            {
                "status": "ok",
                "output_text": "done",
                "usage": {"prompt": 1, "completion": 2, "lat_ms_p50": 3},
                "tool_calls_count": 4,
                "approval_blocked_tools": [{"tool": "Write"}],
            },
        )
        self.assertEqual(len(endpoint.sent), 1)
        kind, wire = endpoint.sent[0]
        self.assertIs(kind, MessageKind.TASK)
        self.assertEqual(wire["workdir"], GUEST_WORKSPACE_PATH)
        self.assertEqual(
            wire["artifact_workdir"],
            guest_artifact_dir(GUEST_WORKSPACE_PATH, "task-1"),
        )
        self.assertTrue(
            wire["artifact_workdir"].startswith(
                f"{GUEST_WORKSPACE_PATH}/.igorek-artifacts/"
            )
        )
        self.assertNotIn(GUEST_ARTIFACT_DIR, wire["artifact_workdir"])
        self.assertEqual(wire["system_prompt"], "system")
        self.assertEqual(wire["task_id"], "task-1")
        self.assertEqual(wire["timeout_s"], 1200)
        self.assertEqual(wire["enabled_tools"], [{"name": "Read"}])
        # Host-only boundaries never cross the wire.
        for host_only in ("log_fn", "request_headers", "metadata"):
            self.assertNotIn(host_only, wire)

    def test_run_task_rewrites_guest_workspace_paths_to_host_paths(self) -> None:
        # Given: a guest answer that names a created file by its guest path, a
        # look-alike without a path boundary, and an echoed host path
        host = str(self.workspace.resolve())
        output = (
            f"Готово: `'{GUEST_WORKSPACE_PATH}/sample-diagram.svg'`, "
            f"skip {GUEST_WORKSPACE_PATH}foo, keep {host}/result.txt"
        )
        endpoint = FakeEndpoint([result_message({"output_text": output})])
        self.registry.endpoints["sess-1"] = endpoint
        manager = self.build_manager()

        # When: the isolated result crosses the host boundary
        result = manager.run_task(self.task(), str(self.workspace), dispatch_payload())

        # Then: only boundary-aligned guest paths become host paths
        self.assertEqual(result["status"], "ok")
        self.assertEqual(
            result["output_text"],
            f"Готово: `'{host}/sample-diagram.svg'`, "
            f"skip {GUEST_WORKSPACE_PATH}foo, keep {host}/result.txt",
        )

    def test_run_task_writes_the_host_artifact_route_for_the_workspace_journal(self) -> None:
        # Given: a dispatch with a host artifact workdir
        host_artifacts = self.root / "host-artifacts"
        endpoint = FakeEndpoint([result_message()])
        self.registry.endpoints["sess-1"] = endpoint
        manager = self.build_manager()

        # When: the turn is dispatched
        manager.run_task(
            self.task(),
            str(self.workspace),
            dispatch_payload(artifact_workdir=str(host_artifacts)),
        )

        # Then: the route names the host mirror of the guest artifact subtree,
        # which is the workspace dir the guest writes through virtiofs
        self.assertTrue((host_artifacts / ROUTE_FILENAME).is_file())
        self.assertEqual(
            read_artifact_route(host_artifacts),
            host_artifact_dir(str(self.workspace.resolve()), "task-1"),
        )

    def test_run_task_keeps_runtime_artifact_paths_out_of_the_host_rewrite(self) -> None:
        # Given: an answer mentioning the artifact subtree, a look-alike name and
        # a real user file in the same workspace
        host = str(self.workspace.resolve())
        artifact = guest_artifact_dir(GUEST_WORKSPACE_PATH, "task-1")
        output = (
            f"runtime {artifact}/subagents.jsonl, "
            f"lookalike {GUEST_WORKSPACE_PATH}/.igorek-artifacts-other/keep.txt, "
            f"user {GUEST_WORKSPACE_PATH}/result.txt"
        )
        endpoint = FakeEndpoint([result_message({"output_text": output})])
        self.registry.endpoints["sess-1"] = endpoint
        manager = self.build_manager()

        # When: the isolated result crosses the host boundary
        result = manager.run_task(self.task(), str(self.workspace), dispatch_payload())

        # Then: the artifact subtree stays a guest path (never attached), while
        # the look-alike and the user file still map to host paths
        self.assertEqual(
            result["output_text"],
            f"runtime {artifact}/subagents.jsonl, "
            f"lookalike {host}/.igorek-artifacts-other/keep.txt, "
            f"user {host}/result.txt",
        )

    def test_run_task_relays_only_typed_web_request_to_host(self) -> None:
        request = ChannelMessage(
            kind=MessageKind.WEB_REQUEST,
            seq=1,
            payload={
                "request_id": "12345678-1234-1234-1234-123456789abc",
                "operation": "search",
                "args": {"query": "security docs", "limit": 2},
            },
        )
        endpoint = FakeEndpoint([request, result_message()])
        self.registry.endpoints["sess-1"] = endpoint
        manager = self.build_manager()

        with patch(
            "factory.core.cc_backend_tools.execute_isolated_web_tool",
            return_value='{"success":true}',
        ) as execute_web:
            result = manager.run_task(self.task(), str(self.workspace), dispatch_payload())

        self.assertEqual("ok", result["status"])
        execute_web.assert_called_once_with("search", {"query": "security docs", "limit": 2})
        replies = [(kind, payload) for kind, payload in endpoint.sent if kind is MessageKind.WEB_REPLY]
        self.assertEqual(1, len(replies))
        self.assertEqual(
            {"request_id": "12345678-1234-1234-1234-123456789abc", "result": '{"success":true}'},
            replies[0][1],
        )

    def test_run_task_relays_memory_forget_to_host_memory_store(self) -> None:
        request = ChannelMessage(
            kind=MessageKind.MEMORY_FORGET_REQUEST,
            seq=1,
            payload={
                "request_id": "12345678-1234-1234-1234-123456789abc",
                "target": "Вес Игоря — 103.4 кг",
            },
        )
        endpoint = FakeEndpoint([request, result_message()])
        self.registry.endpoints["sess-1"] = endpoint
        manager = self.build_manager()
        payload = dispatch_payload(user_message="Удали из памяти «Вес Игоря — 103.4 кг».")

        with patch(
            "factory.core.cc_backend_tools.execute_isolated_memory_forget",
            return_value='{"changed":true,"action":"forget"}',
        ) as execute_forget:
            result = manager.run_task(self.task(), str(self.workspace), payload)

        self.assertEqual("ok", result["status"])
        execute_forget.assert_called_once_with(
            {"target": request.payload["target"]}, user_message=payload["user_message"],
            task_id=payload['task_id'], timeout_s=1200.0,
        )
        replies = [
            (kind, body)
            for kind, body in endpoint.sent
            if kind is MessageKind.MEMORY_FORGET_REPLY
        ]
        self.assertEqual(1, len(replies))
        self.assertEqual(
            {"request_id": request.payload["request_id"], "result": '{"changed":true,"action":"forget"}'},
            replies[0][1],
        )

    def test_run_task_relays_vault_store_with_only_trusted_task_context(self) -> None:
        from types import SimpleNamespace
        from unittest.mock import Mock
        args = {'source_path': 'token.txt', 'name': 'Generated key', 'kind': 'secret'}
        request = ChannelMessage(kind=MessageKind.VAULT_STORE_REQUEST, seq=1,
            payload={'request_id': '12345678-1234-1234-1234-123456789abc', 'args': args})
        endpoint = FakeEndpoint([request, result_message()])
        self.registry.endpoints['sess-1'] = endpoint
        save = Mock(return_value='{"ok":true}')
        manager = self.build_manager(seams_overrides={'vault_value_writer': SimpleNamespace(save=save)})
        result = manager.run_task(self.task(), str(self.workspace), dispatch_payload())
        self.assertEqual(result['status'], 'ok')
        save.assert_called_once_with(args, workdir=str(self.workspace), task_id='task-1', session_id='sess-1', profile='cc-test')
        replies = [body for kind, body in endpoint.sent if kind is MessageKind.VAULT_STORE_REPLY]
        self.assertEqual(replies[0]['result'], '{"ok":true}')

    def test_run_task_relays_session_search_to_host_store(self) -> None:
        request = ChannelMessage(
            kind=MessageKind.SESSION_SEARCH_REQUEST,
            seq=1,
            payload={
                "request_id": "12345678-1234-1234-1234-123456789abc",
                "args": {
                    "query": "memory architecture",
                    "limit": 2,
                    "role_filter": "user,assistant",
                    "mode": "semantic",
                },
            },
        )
        endpoint = FakeEndpoint([request, result_message()])
        self.registry.endpoints["sess-1"] = endpoint
        manager = self.build_manager()
        payload = dispatch_payload(current_session="sess-1")

        with patch(
            "factory.core.cc_backend_tools.execute_isolated_session_search",
            return_value='{"success":true,"semantic_available":true}',
        ) as execute_search:
            result = manager.run_task(self.task(), str(self.workspace), payload)

        self.assertEqual("ok", result["status"])
        execute_search.assert_called_once_with(
            request.payload["args"], current_session="sess-1"
        )
        replies = [
            (kind, body)
            for kind, body in endpoint.sent
            if kind is MessageKind.SESSION_SEARCH_REPLY
        ]
        self.assertEqual(1, len(replies))
        self.assertEqual(
            {
                "request_id": "12345678-1234-1234-1234-123456789abc",
                "result": '{"success":true,"semantic_available":true}',
            },
            replies[0][1],
        )

    def test_run_task_relays_steer_request_and_maps_attachment_paths(self) -> None:
        attachment = self.workspace / ".igorek_uploads" / "session-a" / "image.png"
        attachment.parent.mkdir(parents=True)
        attachment.write_bytes(b"image")
        request = ChannelMessage(
            kind=MessageKind.STEER_REQUEST,
            seq=1,
            payload={"request_id": "12345678-1234-1234-1234-123456789abc"},
        )
        endpoint = FakeEndpoint([request, result_message()])
        self.registry.endpoints["sess-1"] = endpoint
        manager = self.build_manager()
        payload = dispatch_payload(
            pending_steer_provider=lambda: [
                {
                    "text": f"проверь вложение {attachment.resolve()}",
                    "message_id": "message-1",
                    "input_files": [str(attachment.resolve())],
                    "selected_skills": [],
                }
            ]
        )

        result = manager.run_task(self.task(), str(self.workspace), payload)

        self.assertEqual(result["status"], "ok")
        replies = [(kind, body) for kind, body in endpoint.sent if kind is MessageKind.STEER_REPLY]
        self.assertEqual(len(replies), 1)
        self.assertEqual(
            replies[0][1],
            {
                "request_id": "12345678-1234-1234-1234-123456789abc",
                "items": [
                    {
                        "text": f"проверь вложение {GUEST_WORKSPACE_PATH}/.igorek_uploads/session-a/image.png",
                        "message_id": "message-1",
                        "input_files": [f"{GUEST_WORKSPACE_PATH}/.igorek_uploads/session-a/image.png"],
                        "selected_skills": [],
                    }
                ],
            },
        )

    def test_run_task_reuses_one_vm_for_repeated_turns(self) -> None:
        endpoint = FakeEndpoint([result_message(), result_message()])
        self.registry.endpoints["sess-1"] = endpoint
        manager = self.build_manager()

        manager.run_task(self.task(), str(self.workspace), dispatch_payload())
        manager.run_task(self.task(), str(self.workspace), dispatch_payload())

        self.assertEqual(len(self.connection.defined), 1)
        self.assertEqual(
            self.connection.defined[self.domain_name_for()].create_calls, 1
        )
        self.assertEqual(
            [kind for kind, _ in endpoint.sent], [MessageKind.TASK, MessageKind.TASK]
        )

    def test_run_task_falls_back_to_task_id_as_session(self) -> None:
        endpoint = FakeEndpoint([result_message()])
        self.registry.endpoints["task-7"] = endpoint
        manager = self.build_manager()

        manager.run_task(
            {"task_id": "task-7", "metadata": {}},
            str(self.workspace),
            dispatch_payload(current_session=None),
        )

        self.assertEqual(
            list(self.connection.defined), [self.domain_name_for("task-7")]
        )

    def test_run_task_requires_a_known_guest_endpoint(self) -> None:
        manager = self.build_manager()
        with self.assertRaises(IsolationError) as caught:
            manager.run_task(self.task(), str(self.workspace), dispatch_payload())

        self.assertEqual(caught.exception.code, "channel_unavailable")
        self.assertEqual(len(self.connection.defined), 1)

    def test_run_task_waits_for_guest_runner_reconnect(self) -> None:
        endpoint = FakeEndpoint([result_message()])
        manager = self.build_manager(seams_overrides={"endpoint_wait_s": 1.0})
        attempts = 0

        def reconnect(session_id: SessionId) -> FakeEndpoint | None:
            nonlocal attempts
            attempts += 1
            return endpoint if attempts >= 3 else None

        with patch.object(self.registry, "session_for", side_effect=reconnect):
            result = manager.run_task(self.task(), str(self.workspace), dispatch_payload())

        self.assertEqual(result["status"], "ok")
        self.assertGreaterEqual(attempts, 3)
        self.assertEqual([kind for kind, _ in endpoint.sent], [MessageKind.TASK])

    def test_run_task_contains_a_silent_guest_as_guest_stalled(self) -> None:
        # Given: a live VM whose guest never sends a single frame
        endpoint = FakeEndpoint()
        self.registry.endpoints["sess-1"] = endpoint
        manager = self.build_manager(
            seams_overrides={"guest_stall_timeout_s": 0.05}
        )

        # When: the host waits for a result that never comes
        result = manager.run_task(self.task(), str(self.workspace), dispatch_payload())

        # Then: the stall is detected fast, the task fails and the VM is torn down
        self.assertEqual(result["status"], "fail")
        self.assertEqual(result["failure_reason"], "guest_stalled")
        self.assertIn("guest_stalled", str(result["error"]))
        handle = self.connection.defined[self.domain_name_for()]
        self.assertEqual(handle.destroy_calls, 1)
        self.assertEqual(handle.undefine_calls, 1)
        self.assertTrue(endpoint.closed)

    def test_run_task_contains_a_missing_result_as_result_timeout(self) -> None:
        # Given: a guest that keeps heartbeating but never produces a result
        endpoint = HeartbeatEndpoint()
        self.registry.endpoints["sess-1"] = endpoint
        manager = self.build_manager()
        with patch(
            "factory.core.isolation.manager._result_timeout", return_value=0.3
        ):
            # When: the result deadline passes while heartbeats still flow
            result = manager.run_task(
                self.task(), str(self.workspace), dispatch_payload()
            )

        # Then: the turn is a contained result_timeout, never a raised exception
        self.assertEqual(result["status"], "fail")
        self.assertEqual(result["failure_reason"], "result_timeout")
        self.assertNotEqual(result["failure_reason"], "guest_stalled")
        self.assertTrue(endpoint.closed)

    def test_heartbeats_keep_a_slow_guest_out_of_the_stall_detector(self) -> None:
        # Given: a guest silent about results, beating faster than the stall window
        endpoint = HeartbeatEndpoint()
        self.registry.endpoints["sess-1"] = endpoint
        manager = self.build_manager(
            seams_overrides={"guest_stall_timeout_s": 0.05}
        )
        with patch(
            "factory.core.isolation.manager._result_timeout", return_value=0.25
        ):
            # When: the host waits past several stall windows
            result = manager.run_task(
                self.task(), str(self.workspace), dispatch_payload()
            )

        # Then: the beats refreshed activity and the guest was never stalled
        self.assertGreaterEqual(endpoint.beats, 3)
        self.assertEqual(result["failure_reason"], "result_timeout")

    def test_run_task_raises_on_guest_error_frame(self) -> None:
        error = ChannelMessage(
            kind=MessageKind.ERROR, seq=1, payload={"reason": "executor_failed"}
        )
        self.registry.endpoints["sess-1"] = FakeEndpoint([error])
        manager = self.build_manager()
        with self.assertRaises(IsolationError) as caught:
            manager.run_task(self.task(), str(self.workspace), dispatch_payload())

        self.assertEqual(caught.exception.code, "guest_error")
        self.assertIn("executor_failed", caught.exception.detail)

    def test_run_task_skips_event_frames_before_the_result(self) -> None:
        event = ChannelMessage(
            kind=MessageKind.EVENT, seq=1, payload={"event": "tool.result"}
        )
        self.registry.endpoints["sess-1"] = FakeEndpoint([event, result_message()])
        manager = self.build_manager()

        result = manager.run_task(
            self.task(), str(self.workspace), dispatch_payload()
        )

        self.assertEqual(result["status"], "ok")

    def test_run_task_skips_a_malformed_approval_frame_and_still_normalizes_the_result(self) -> None:
        # An approval frame without the inert envelope cannot be resolved (todo 9),
        # so the relay must skip it with a warning and keep awaiting the result.
        approval = ChannelMessage(
            kind=MessageKind.APPROVAL_REQUEST, seq=1, payload={"tool": "Write"}
        )
        self.registry.endpoints["sess-1"] = FakeEndpoint([approval, result_message()])
        manager = self.build_manager()

        result = manager.run_task(self.task(), str(self.workspace), dispatch_payload())

        self.assertEqual(result["status"], "ok")
        self.assertNotIn("approval_blocked_tools", result)

    def test_run_task_refuses_guest_host_op_frames(self) -> None:
        host_op = ChannelMessage(
            kind=MessageKind.HOST_OP,
            seq=1,
            payload={"op": "runtime.restart", "args": {}},
        )
        self.registry.endpoints["sess-1"] = FakeEndpoint([host_op])
        manager = self.build_manager()
        with self.assertRaises(IsolationError) as caught:
            manager.run_task(self.task(), str(self.workspace), dispatch_payload())

        self.assertEqual(caught.exception.code, "unexpected_guest_frame")

    def test_run_task_parks_a_guest_host_op_through_the_wired_runtime(self) -> None:
        # Given: a kvm dispatch whose host-op runtime is wired (todo 22)
        runtime, audit_path = self.host_op_runtime()
        host_op = ChannelMessage(
            kind=MessageKind.HOST_OP,
            seq=9,
            payload={"version": BROKER_VERSION, "op": READ_ONLY_OP, "args": {}},
        )
        endpoint = FakeEndpoint([host_op, result_message()])
        self.registry.endpoints["sess-1"] = endpoint
        manager = self.build_manager(seams_overrides={"host_ops": runtime})

        # When: the guest asks for the typed op with no approval yet
        result = manager.run_task(
            self.task(), str(self.workspace), dispatch_payload(source_platform="android")
        )

        # Then: the broker answered the guest and parked the host-op approval
        self.assertEqual("waiting_approval", result["status"])
        blocked = result["approval_blocked_tools"]
        self.assertEqual(1, len(blocked))
        self.assertEqual("host_op", blocked[0]["kind"])
        self.assertEqual(READ_ONLY_OP, blocked[0]["op"])
        host_op_answers = [payload for kind, payload in endpoint.sent if kind is MessageKind.HOST_OP]
        self.assertEqual(1, len(host_op_answers))
        self.assertEqual("approval_required", host_op_answers[0]["status"])
        self.assertEqual(1, len(runtime.gate.pending_ids))
        self.assertTrue(manager._vms["sess-1"].domain.is_active())
        self.assertEqual(0, self.connection.defined[self.domain_name_for()].destroy_calls)

        # And: nothing executed without a decision (decision audit only)
        self.assertEqual(["decision"], self.audit_phases(audit_path))

    def test_run_task_executes_a_session_granted_guest_host_op(self) -> None:
        # Given: the same session was already granted this op
        runtime, audit_path = self.host_op_runtime()
        runtime.gate.grant_session(READ_ONLY_OP, session_id="sess-1")
        host_op = ChannelMessage(
            kind=MessageKind.HOST_OP,
            seq=10,
            payload={"version": BROKER_VERSION, "op": READ_ONLY_OP, "args": {}},
        )
        endpoint = FakeEndpoint([host_op, result_message()])
        self.registry.endpoints["sess-1"] = endpoint
        manager = self.build_manager(seams_overrides={"host_ops": runtime})

        # When: the guest asks for it again
        result = manager.run_task(self.task(), str(self.workspace), dispatch_payload())

        # Then: it ran immediately instead of parking again
        self.assertEqual("ok", result["status"])
        self.assertNotIn("approval_blocked_tools", result)
        host_op_answers = [payload for kind, payload in endpoint.sent if kind is MessageKind.HOST_OP]
        self.assertEqual(1, len(host_op_answers))
        self.assertEqual("ok", host_op_answers[0]["status"])
        self.assertEqual(0, len(runtime.gate.pending_ids))
        self.assertEqual(["decision", "outcome"], self.audit_phases(audit_path))

    def test_run_task_refuses_unknown_result_status(self) -> None:
        broken = ChannelMessage(
            kind=MessageKind.RESULT,
            seq=1,
            payload={"status": "success", "output_text": "", "usage": {}},
        )
        self.registry.endpoints["sess-1"] = FakeEndpoint([broken])
        manager = self.build_manager()
        with self.assertRaises(IsolationError) as caught:
            manager.run_task(self.task(), str(self.workspace), dispatch_payload())

        self.assertEqual(caught.exception.code, "invalid_result_status")

    def test_run_task_refuses_non_serializable_payload_values(self) -> None:
        self.registry.endpoints["sess-1"] = FakeEndpoint([result_message()])
        manager = self.build_manager()
        with self.assertRaises(IsolationError) as caught:
            manager.run_task(
                self.task(),
                str(self.workspace),
                dispatch_payload(
                    enabled_tools=[{"name": "Read", "call": lambda: None}]
                ),
            )

        self.assertEqual(caught.exception.code, "payload_not_serializable")

    def test_run_task_refuses_missing_dispatch_fields(self) -> None:
        self.registry.endpoints["sess-1"] = FakeEndpoint([result_message()])
        manager = self.build_manager()
        payload = dispatch_payload()
        del payload["system_prompt"]
        with self.assertRaises(IsolationError) as caught:
            manager.run_task(self.task(), str(self.workspace), payload)

        self.assertEqual(caught.exception.code, "payload_missing")
        self.assertEqual(
            caught.exception.reason, "payload_missing:system_prompt"
        )


class StopAndReapTest(ManagerHarness):
    def test_stop_tears_down_the_domain_and_releases_the_binding(self) -> None:
        endpoint = FakeEndpoint()
        self.registry.endpoints["sess-1"] = endpoint
        manager = self.build_manager()
        manager.ensure_vm(self.session())

        manager.stop(self.session())

        handle = self.connection.defined[self.domain_name_for()]
        self.assertEqual(handle.destroy_calls, 1)
        self.assertEqual(handle.undefine_calls, 1)
        self.assertIsNone(self.session_map.session_for_cid(PeerCid(FAKE_CID)))
        self.assertTrue(endpoint.closed)

    def test_stop_is_idempotent_for_unknown_sessions(self) -> None:
        manager = self.build_manager()
        manager.stop("never-started")
        manager.stop(
            SessionSpec(session_id="never-started", workspace_cwd=str(self.workspace))
        )

    def test_reap_orphans_removes_stale_domain_and_overlay(self) -> None:
        self.overlay_dir.mkdir(parents=True)
        stale_overlay = self.overlay_dir / "stale-session-12345678.qcow2"
        stale_overlay.write_bytes(b"stale")
        stale_domain = self.connection.defineXML(
            f"<domain><name>{DOMAIN_NAME_PREFIX}{stale_overlay.stem}</name></domain>"
        )
        stale_domain.create()

        manager = self.build_manager()
        reaped = manager.reap_orphans()

        self.assertEqual(reaped, 1)
        self.assertEqual(stale_domain.destroy_calls, 1)
        self.assertEqual(stale_domain.undefine_calls, 1)
        self.assertFalse(stale_overlay.exists())

    def test_reap_orphans_keeps_live_session_overlays(self) -> None:
        manager = self.build_manager()
        vm = manager.ensure_vm(self.session())
        live_overlay = Path(vm.domain.overlay or "")
        live_overlay.parent.mkdir(parents=True, exist_ok=True)
        live_overlay.write_bytes(b"live")

        reaped = manager.reap_orphans()

        self.assertEqual(reaped, 0)
        self.assertTrue(live_overlay.exists())
        self.assertEqual(
            self.connection.defined[self.domain_name_for()].undefine_calls, 0
        )

    def test_reap_orphans_removes_overlay_without_a_defined_domain(self) -> None:
        self.overlay_dir.mkdir(parents=True)
        orphan = self.overlay_dir / "unknown-session-99999999.qcow2"
        orphan.write_bytes(b"orphan")

        manager = self.build_manager()
        self.assertEqual(manager.reap_orphans(), 1)
        self.assertFalse(orphan.exists())

    def test_reap_orphans_is_zero_without_an_overlay_dir(self) -> None:
        manager = self.build_manager()
        self.assertEqual(manager.reap_orphans(), 0)

    def test_domain_name_prefix_matches_the_domain_module(self) -> None:
        spec = DomainSpec(
            session_id="prefix-check",
            base_image=self.root / "base.qcow2",
            workspace_host_path=self.workspace,
            overlay_dir=self.overlay_dir,
        )
        self.assertTrue(domain_name(spec).startswith(DOMAIN_NAME_PREFIX))

    def test_close_stops_every_session_and_closes_the_connection(self) -> None:
        manager = self.build_manager()
        other = self.root / "workspace-b"
        other.mkdir()
        manager.ensure_vm(self.session("sess-a"))
        manager.ensure_vm(SessionSpec(session_id="sess-b", workspace_cwd=str(other)))

        manager.close()

        for session_id in ("sess-a", "sess-b"):
            self.assertEqual(
                self.connection.defined[
                    self.domain_name_for(session_id)
                ].undefine_calls,
                1,
            )
        self.assertTrue(self.connection.closed)
        self.assertTrue(self.registry.closed)


class ResolveVsockCidTest(unittest.TestCase):
    def test_reads_the_assigned_cid_from_live_xml(self) -> None:
        connection = FakeConnection(cid=7)
        handle = connection.defineXML("<domain><name>d</name></domain>")
        self.assertIsNotNone(handle)
        self.assertEqual(resolve_vsock_cid(connection, "d"), PeerCid(7))

    def test_returns_none_when_the_live_xml_has_no_cid(self) -> None:
        connection = FakeConnection(cid=None)
        connection.defineXML("<domain><name>d</name></domain>")
        self.assertIsNone(resolve_vsock_cid(connection, "d"))

    def test_returns_none_for_an_unknown_domain(self) -> None:
        self.assertIsNone(resolve_vsock_cid(FakeConnection(), "missing"))


class SessionWarmReconnectTest(ManagerHarness):
    """Completed mobile tasks keep their VM and recover a stale idle channel."""

    def test_task_then_idle_channel_reconnect_reuses_vm_and_workspace(self) -> None:
        manager = self.build_manager(seams_overrides={"endpoint_wait_s": 1.0})
        restored = FakeEndpoint([result_message({"output_text": "second task done"})])
        registry = self.registry

        class ClosedAtDispatchEndpoint(FakeEndpoint):
            fail_next_task = False

            def send(self, kind: MessageKind, payload: dict[str, Any]) -> None:
                if self.fail_next_task and kind is MessageKind.TASK:
                    self.fail_next_task = False
                    self.closed = True
                    registry.endpoints["sess-1"] = restored
                    raise ChannelError("channel_closed")
                if self.closed:
                    raise ChannelError("channel_closed")
                super().send(kind, payload)

        first = ClosedAtDispatchEndpoint([result_message()])
        registry.endpoints["sess-1"] = first
        first_result = manager.run_task(
            self.task(task_id="task-1"), str(self.workspace),
            dispatch_payload(task_id="task-1", current_session="sess-1"),
        )
        self.assertEqual(first_result["status"], "ok")
        warm_vm = manager._vms["sess-1"]
        domain = self.connection.defined[self.domain_name_for()]
        overlay = warm_vm.domain.overlay

        # Given: the guest's idle listener loses its channel just as the next
        # task is dispatched, then systemd supplies a fresh peer in that VM.
        first.fail_next_task = True
        second_result = manager.run_task(
            self.task(task_id="task-2"), str(self.workspace),
            dispatch_payload(task_id="task-2", current_session="sess-1"),
        )

        self.assertEqual(second_result["status"], "ok")
        self.assertEqual(second_result["output_text"], "second task done")
        self.assertIs(manager._vms["sess-1"], warm_vm)
        self.assertTrue(domain.active)
        self.assertEqual(domain.create_calls, 1)
        self.assertEqual(domain.destroy_calls, 0)
        self.assertEqual(manager._vms["sess-1"].domain.overlay, overlay)
        self.assertEqual(manager._vms["sess-1"].export["host_path"], str(self.workspace.resolve()))
        self.assertEqual([kind for kind, _ in restored.sent], [MessageKind.TASK])


class EgressEnforcementDispatchTest(ManagerHarness):
    """The per-session ruleset must travel with the dispatch (plan todo 40a)."""

    def test_dispatch_frame_carries_the_per_session_ruleset(self) -> None:
        # Given: a manager whose renderer produced one session ruleset
        built: list[tuple[str, str]] = []

        def builder(
            profile: str, base_url: str, *, host_local_ports: tuple[int, ...] = ()
        ) -> SessionEgress:
            built.append((profile, base_url))
            return SessionEgress(
                profile=profile,
                ruleset="table inet igorek_egress { chain output { policy drop; } }",
                pinned_hosts=(("151.101.0.223", "pypi.org"),),
                allow_hosts=("pypi.org",),
                host_local_ports=tuple(host_local_ports),
            )

        endpoint = FakeEndpoint([result_message()])
        self.registry.endpoints["sess-1"] = endpoint
        manager = self.build_manager(seams_overrides={"egress": builder})

        # When: one turn is dispatched
        result = manager.run_task(self.task(), str(self.workspace), dispatch_payload())

        # Then: the guest frame carries the rendered ruleset and the pinned map
        self.assertEqual("ok", result["status"])
        self.assertEqual([("cc-test", "http://127.0.0.1:8001")], built)
        frames = [payload for kind, payload in endpoint.sent if kind is MessageKind.TASK]
        self.assertEqual(1, len(frames))
        self.assertEqual(
            "table inet igorek_egress { chain output { policy drop; } }",
            frames[0]["egress"]["ruleset"],
        )
        self.assertEqual(
            {"151.101.0.223": "pypi.org"}, frames[0]["egress"]["pinned_hosts"]
        )

    def test_dispatch_opens_the_egress_auth_proxy_port_on_the_gateway_alias(self) -> None:
        # Given: a provider plan whose guest base URL is the host proxy
        plan = ProviderSecretPlan(
            provider="wally",
            mode="proxy",
            upstream_base_url="https://inference.runanywhere.ai",
            guest_base_url="http://10.0.2.2:5011/v1",
            proxy_bind=("127.0.0.1", 5011),
        )
        seen: list[tuple[int, ...]] = []

        def builder(
            profile: str, base_url: str, *, host_local_ports: tuple[int, ...] = ()
        ) -> SessionEgress:
            seen.append(tuple(host_local_ports))
            return SessionEgress(profile=profile, ruleset="# rules\n")

        endpoint = FakeEndpoint([result_message()])
        self.registry.endpoints["sess-1"] = endpoint
        manager = self.build_manager(
            seams_overrides={
                "egress": builder,
                "provider_secrets": _FakeInjector(plan),
            }
        )

        # When: the turn is dispatched
        manager.run_task(self.task(), str(self.workspace), dispatch_payload())

        # Then: exactly the proxy port is allowed host-locally (never widened)
        self.assertEqual([(5011,)], seen)

    def test_dispatch_off_mode_never_builds_an_egress_policy(self) -> None:
        # Given: an off-mode config
        built: list[str] = []

        def builder(
            profile: str, base_url: str, *, host_local_ports: tuple[int, ...] = ()
        ) -> SessionEgress:
            built.append(profile)
            return SessionEgress(profile=profile, ruleset="# rules\n")

        config = IsolationConfig.from_env(
            {"IGOREK_ISOLATION_MODE": "off"}, data_root=self.root
        )
        manager = self.build_manager(
            config=config, seams_overrides={"egress": builder}
        )

        # When/Then: no VM is created and no policy is rendered off-mode
        with self.assertRaises(IsolationError) as caught:
            manager.run_task(self.task(), str(self.workspace), dispatch_payload())
        self.assertEqual("isolation_not_enabled", caught.exception.code)
        self.assertEqual([], built)


class GuestEnvGateTest(ManagerHarness):
    """A secret-tainted guest env is refused before dispatch (plan todo 40c)."""

    def _dispatch_with(
        self,
        *,
        plan: ProviderSecretPlan | None = None,
        extensions: TaskExtensions | None = None,
    ) -> tuple[Any, FakeEndpoint]:
        endpoint = FakeEndpoint([result_message()])
        self.registry.endpoints["sess-1"] = endpoint
        overrides: dict[str, Any] = {}
        if plan is not None:
            overrides["provider_secrets"] = _FakeInjector(plan)
        if extensions is not None:
            overrides["extensions"] = lambda payload: extensions
        manager = self.build_manager(seams_overrides=overrides)
        return manager, endpoint

    def test_dispatch_refuses_a_provider_secret_in_the_guest_env(self) -> None:
        # Given: a provider plan that would place a token in the guest env
        plan = ProviderSecretPlan(
            provider="wally",
            mode=MODE_EPHEMERAL_ENV,
            upstream_base_url="https://inference.runanywhere.ai",
            guest_base_url="https://inference.runanywhere.ai/v1",
            guest_env=(("WALLY_ACCESS_TOKEN", "host-injected-token"),),
        )
        manager, endpoint = self._dispatch_with(plan=plan)

        # When: the turn is dispatched
        with self.assertRaises(IsolationError) as caught:
            manager.run_task(self.task(), str(self.workspace), dispatch_payload())

        # Then: refused before the frame is sent - no dispatch, no token in it
        self.assertEqual(GUEST_ENV_LEAK_REASON, caught.exception.code)
        self.assertIn("WALLY_ACCESS_TOKEN", caught.exception.detail)
        self.assertNotIn("host-injected-token", str(caught.exception))
        self.assertEqual([], endpoint.sent)

    def test_dispatch_refuses_a_secret_on_a_credential_free_mcp_placement(self) -> None:
        # Given: an MCP placement claiming no credential but carrying a key
        placement = McpServerPlacement(
            name="tavily",
            program="npx",
            env=(("TAVILY_API_KEY", "tvly-secret"),),
            credential_source=MCP_CREDENTIAL_NONE,
        )
        manager, endpoint = self._dispatch_with(
            extensions=TaskExtensions(mcp_servers=(placement,))
        )

        # When: the turn is dispatched
        with self.assertRaises(IsolationError) as caught:
            manager.run_task(self.task(), str(self.workspace), dispatch_payload())

        # Then: refused before the frame is sent
        self.assertEqual(GUEST_ENV_LEAK_REASON, caught.exception.code)
        self.assertEqual([], endpoint.sent)

    def test_dispatch_refuses_env_on_a_host_brokered_mcp_placement(self) -> None:
        # Given: a brokered server whose spec must carry no env value at all
        placement = McpServerPlacement(
            name="fal_image",
            program="npx",
            env=(("FAL_KEY", "fal-secret"),),
            placement=MCP_PLACEMENT_HOST_BROKERED,
            credential_source=MCP_CREDENTIAL_HOST_BROKER,
        )
        manager, endpoint = self._dispatch_with(
            extensions=TaskExtensions(mcp_servers=(placement,))
        )

        # When/Then: the env-carrying brokered spec is refused before dispatch
        with self.assertRaises(IsolationError) as caught:
            manager.run_task(self.task(), str(self.workspace), dispatch_payload())
        self.assertEqual(GUEST_ENV_LEAK_REASON, caught.exception.code)
        self.assertEqual([], endpoint.sent)

    def test_dispatch_allows_the_documented_mcp_ephemeral_env(self) -> None:
        # Given: the todo-18 documented residual (a server's own ephemeral env)
        placement = McpServerPlacement(
            name="tavily",
            program="npx",
            env=(("TAVILY_API_KEY", "tvly-ephemeral"),),
            credential_source=MCP_CREDENTIAL_EPHEMERAL_ENV,
        )
        manager, endpoint = self._dispatch_with(
            extensions=TaskExtensions(mcp_servers=(placement,))
        )

        # When: the turn is dispatched
        result = manager.run_task(self.task(), str(self.workspace), dispatch_payload())

        # Then: the documented placement still runs
        self.assertEqual("ok", result["status"])
        self.assertEqual(1, len(endpoint.sent))

    def test_dispatch_allows_a_clean_guest_env(self) -> None:
        # Given: no provider env and a credential-free placement
        placement = McpServerPlacement(
            name="context7",
            program="npx",
            env=(("MCP_CONTEXT7_TIMEOUT", "30"),),
            credential_source=MCP_CREDENTIAL_NONE,
        )
        manager, endpoint = self._dispatch_with(
            extensions=TaskExtensions(mcp_servers=(placement,))
        )

        # When/Then: a clean env dispatches normally
        result = manager.run_task(self.task(), str(self.workspace), dispatch_payload())
        self.assertEqual("ok", result["status"])
        self.assertEqual(1, len(endpoint.sent))


class _FakeInjector:
    """Minimal ``ProviderAuthInjector`` stand-in returning one fixed plan."""

    def __init__(self, plan: ProviderSecretPlan) -> None:
        self._plan = plan

    def plan(self, base_url: str) -> ProviderSecretPlan | None:
        return self._plan

    def close(self) -> None:
        return None


if __name__ == "__main__":
    unittest.main()
