"""Todo 35 regression lock: the ``isolation`` status value never over-claims.

Only ``cc-backend`` sessions run inside a VM in the KVM isolation plan, and even
a ``cc-backend`` session may claim ``"kvm"`` only when the resolved isolation
config is a *satisfied* kvm request. ``claude``/``codex`` sessions (and unknown
profiles) report ``"none"``; a requested-but-unavailable kvm configuration
reports ``"kvm-unavailable"``. Every assertion pins the exact string (no
truthiness), and status lookups must never construct or enter the isolation
manager - no VM is created for an out-of-scope backend.
"""

from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from factory.core.cli_chat import _session_path
from factory.core.cli_tasks import create_task
from factory.core.isolation.config import (
    MODE_KVM,
    MODE_OFF,
    HostCapabilities,
    IsolationConfig,
)
from factory.core.isolation.status import (
    ISOLATION_BACKEND,
    ISOLATION_KVM,
    ISOLATION_KVM_UNAVAILABLE,
    ISOLATION_NONE,
    isolation_status_for_backend,
    isolation_status_value,
    resolve_isolation_status,
)
from factory.core.mobile_bridge.service import MobileService
from factory.core.runtime import get_db_path
from factory.core.schema import initialize_database

CC_PROFILE = "scope-cc"
CODEX_PROFILE = "scope-codex"
CLAUDE_PROFILE = "scope-claude"


def _config(*, mode: str, enabled: bool) -> IsolationConfig:
    return IsolationConfig(
        mode=mode,
        enabled=enabled,
        reason=None,
        base_image=Path("/tmp/scope-base.qcow2"),
        overlay_dir=Path("/tmp/scope-overlays"),
        libvirt_uri="qemu:///session",
        vsock_port=5005,
        idle_ttl_s=900,
        max_vms=4,
        session_vcpus=2,
        session_memory_mb=2048,
        session_disk_gb=8,
    )


class IsolationStatusMappingTest(unittest.TestCase):
    """Pure mapping: config truth first, backend scope second."""

    def test_requested_but_unavailable_kvm_never_claims_kvm(self) -> None:
        self.assertEqual(
            isolation_status_value(_config(mode=MODE_KVM, enabled=False)),
            ISOLATION_KVM_UNAVAILABLE,
        )
        self.assertNotEqual(
            isolation_status_value(_config(mode=MODE_KVM, enabled=False)),
            ISOLATION_KVM,
        )
        self.assertEqual(
            isolation_status_value(_config(mode=MODE_KVM, enabled=True)),
            ISOLATION_KVM,
        )
        self.assertEqual(
            isolation_status_value(_config(mode=MODE_OFF, enabled=False)),
            ISOLATION_NONE,
        )

    def test_non_cc_backend_values_are_pinned_to_none(self) -> None:
        values = (ISOLATION_KVM, ISOLATION_KVM_UNAVAILABLE, ISOLATION_NONE)
        backends = ("claude", "codex", "", "unknown-backend", "cc-backendX")
        for value in values:
            for backend in backends:
                with self.subTest(value=value, backend=backend):
                    self.assertEqual(
                        isolation_status_for_backend(value, backend),
                        ISOLATION_NONE,
                    )

    def test_cc_backend_preserves_the_config_value(self) -> None:
        values = (ISOLATION_KVM, ISOLATION_KVM_UNAVAILABLE, ISOLATION_NONE)
        for value in values:
            with self.subTest(value=value):
                self.assertEqual(
                    isolation_status_for_backend(value, ISOLATION_BACKEND),
                    value,
                )

    def test_resolve_isolation_status_reports_unavailable_kvm(self) -> None:
        self.assertEqual(resolve_isolation_status({}), ISOLATION_NONE)
        requested = resolve_isolation_status({"IGOREK_ISOLATION_MODE": "kvm"})
        self.assertEqual(requested, ISOLATION_KVM_UNAVAILABLE)
        self.assertNotEqual(requested, ISOLATION_KVM)


class BackendScopeServiceTest(unittest.TestCase):
    """Status payloads: per-task/per-session scope and no VM construction."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="isolation-scope-")
        self.home = Path(self._tmp.name)
        self._env = patch.dict(
            os.environ,
            {
                "IGOREK_FACTORY_HOME": str(self.home),
                "IGOREK_ISOLATION_MODE": "kvm",
                "IGOREK_ISOLATION_BASE_IMAGE": str(self.home / "base.qcow2"),
            },
        )
        self._env.start()
        (self.home / "base.qcow2").write_bytes(b"qcow2")
        self._caps = patch(
            "factory.core.isolation.config.check_host_capabilities",
            return_value=HostCapabilities(True, True, True, True, None),
        )
        self._caps.start()
        self.addCleanup(self._caps.stop)
        self.addCleanup(self._env.stop)
        self.addCleanup(self._tmp.cleanup)
        initialize_database(get_db_path())
        for name, backend in (
            (CC_PROFILE, ISOLATION_BACKEND),
            (CODEX_PROFILE, "codex"),
            (CLAUDE_PROFILE, "claude"),
        ):
            self._write_profile(name, backend)
        self.service = self._service()

    def _service(self) -> MobileService:
        return MobileService(profile=CC_PROFILE, workdir=str(self.home))

    def _write_profile(self, name: str, backend: str) -> None:
        directory = self.home / "profiles"
        directory.mkdir(exist_ok=True)
        (directory / f"{name}.json").write_text(
            json.dumps({"backend": backend}), encoding="utf-8"
        )

    def _write_session(self, session_id: str, profile: str) -> None:
        path = _session_path(session_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "type": "meta",
                    "session_id": session_id,
                    "created_at": 1_700_000_000,
                    "profile": profile,
                    "model": "scope-model",
                    "provider": "scope-provider",
                    "cwd": str(self.home),
                }
            )
            + "\n",
            encoding="utf-8",
        )

    def _task(self, profile: str, service: MobileService | None = None) -> dict:
        created = create_task(
            text="backend scope",
            profile=profile,
            metadata={"source": "mobile_bridge"},
        )
        return (service or self.service).get_task(str(created["task_id"]))

    def test_codex_and_claude_tasks_report_none(self) -> None:
        self.assertEqual(self._task(CODEX_PROFILE)["isolation"], ISOLATION_NONE)
        self.assertEqual(self._task(CLAUDE_PROFILE)["isolation"], ISOLATION_NONE)

    def test_status_lookup_never_constructs_or_enters_the_isolation_manager(self) -> None:
        with patch(
            "factory.core.isolation.manager.IsolationManager.from_env",
            side_effect=AssertionError("status lookup must not build the manager"),
        ) as from_env, patch(
            "factory.core.isolation.manager.IsolationManager.ensure_vm",
            side_effect=AssertionError("status lookup must not boot a VM"),
        ) as ensure_vm, patch(
            "factory.core.isolation.manager.IsolationManager.run_task",
            side_effect=AssertionError("status lookup must not dispatch into a VM"),
        ) as run_task:
            payload = self._task(CODEX_PROFILE)

        self.assertEqual(payload["isolation"], ISOLATION_NONE)
        from_env.assert_not_called()
        ensure_vm.assert_not_called()
        run_task.assert_not_called()

    def test_operational_kvm_cc_backend_task_reports_kvm(self) -> None:
        self.assertEqual(self._task(CC_PROFILE)["isolation"], ISOLATION_KVM)

    def test_unavailable_kvm_cc_backend_task_reports_truthful_value(self) -> None:
        unavailable = _config(mode=MODE_KVM, enabled=False)
        with patch.object(IsolationConfig, "from_env", return_value=unavailable):
            service = self._service()
            payload = self._task(CC_PROFILE, service=service)

        self.assertEqual(payload["isolation"], ISOLATION_KVM_UNAVAILABLE)
        self.assertNotEqual(payload["isolation"], ISOLATION_KVM)

    def test_unavailable_kvm_never_makes_a_codex_task_look_isolated(self) -> None:
        unavailable = _config(mode=MODE_KVM, enabled=False)
        with patch.object(IsolationConfig, "from_env", return_value=unavailable):
            service = self._service()
            payload = self._task(CODEX_PROFILE, service=service)

        self.assertEqual(payload["isolation"], ISOLATION_NONE)
        self.assertNotEqual(payload["isolation"], ISOLATION_KVM)

    def test_off_mode_cc_backend_task_reports_none(self) -> None:
        with patch.dict(os.environ, {"IGOREK_ISOLATION_MODE": "off"}):
            service = self._service()
            payload = self._task(CC_PROFILE, service=service)

        self.assertEqual(payload["isolation"], ISOLATION_NONE)

    def test_sessions_report_the_scope_of_their_own_profile(self) -> None:
        self._write_session("chat-scope-cc", CC_PROFILE)
        self._write_session("chat-scope-codex", CODEX_PROFILE)

        cc_detail = self.service.get_session("chat-scope-cc")
        codex_detail = self.service.get_session("chat-scope-codex")
        overview = {
            entry["session_id"]: entry["isolation"]
            for entry in self.service.sessions_overview()["sessions"]
        }

        self.assertEqual(cc_detail["isolation"], ISOLATION_KVM)
        self.assertEqual(codex_detail["isolation"], ISOLATION_NONE)
        self.assertEqual(overview["chat-scope-cc"], ISOLATION_KVM)
        self.assertEqual(overview["chat-scope-codex"], ISOLATION_NONE)


if __name__ == "__main__":
    unittest.main()
