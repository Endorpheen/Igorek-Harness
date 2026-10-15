"""Todo 12 regression lock: additive ``isolation`` technical status field.

The mobile bridge status payloads (session list, session detail, task status)
carry an additive ``isolation`` value derived from the resolved isolation
config and narrowed to the payload's own backend (todo 35): ``"none"`` for
``off``/unknown modes and for non-``cc-backend`` backends, ``"kvm"`` only for an
active ``kvm`` mode of a ``cc-backend`` profile. Session creation stays host-side
and instant: it must not boot a VM - the VM boot belongs to the first task.
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
    ISOLATION_KVM,
    ISOLATION_KVM_UNAVAILABLE,
    ISOLATION_NONE,
    isolation_status_value,
)
from factory.core.mobile_bridge.service import MobileService
from factory.core.runtime import get_db_path
from factory.core.schema import initialize_database


def _config(*, mode: str, enabled: bool = False) -> IsolationConfig:
    return IsolationConfig(
        mode=mode,
        enabled=enabled,
        reason=None,
        base_image=None,
        overlay_dir=Path("/tmp/isolation-overlays"),
        libvirt_uri="qemu:///session",
        vsock_port=5005,
        idle_ttl_s=900,
        max_vms=4,
        session_vcpus=2,
        session_memory_mb=2048,
        session_disk_gb=8,
    )


class IsolationStatusFieldTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self._env = patch.dict(os.environ, {"IGOREK_FACTORY_HOME": self._tmp.name})
        self._env.start()
        self.base_image = Path(self._tmp.name) / "base.qcow2"
        self.base_image.write_bytes(b"qcow2")
        self._write_profile("cc-local-8081", backend="cc-backend")
        # The host preflight is faked so a ``kvm`` request can count as
        # operational without a real /dev/kvm; the off/unknown cases never probe.
        self._caps = patch(
            "factory.core.isolation.config.check_host_capabilities",
            return_value=HostCapabilities(True, True, True, True, None),
        )
        self._caps.start()
        initialize_database(get_db_path())
        self.session_id = "chat-20260922-120000-kvm001"
        self._write_session(self.session_id)

    def tearDown(self) -> None:
        self._caps.stop()
        self._env.stop()
        self._tmp.cleanup()

    def _write_profile(self, profile: str, *, backend: str) -> None:
        directory = Path(self._tmp.name) / "profiles"
        directory.mkdir(exist_ok=True)
        (directory / f"{profile}.json").write_text(
            json.dumps({"backend": backend}), encoding="utf-8"
        )

    def _kvm_env(self) -> dict[str, str]:
        return {
            "IGOREK_ISOLATION_MODE": "kvm",
            "IGOREK_ISOLATION_BASE_IMAGE": str(self.base_image),
        }

    def _write_session(self, session_id: str, *, profile: str = "cc-local-8081") -> Path:
        path = _session_path(session_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "type": "meta",
                    "session_id": session_id,
                    "created_at": 1_700_000_000,
                    "profile": profile,
                    "model": "deepseek-v4.1-flash",
                    "provider": "opencode",
                    "cwd": self._tmp.name,
                },
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )
        return path

    def _service(self) -> MobileService:
        return MobileService(profile="cc-local-8081", workdir=self._tmp.name)

    def _task_payload(self, service: MobileService) -> dict:
        created = create_task(
            text="проверка isolation",
            profile="cc-local-8081",
            metadata={"session": self.session_id, "source": "mobile_bridge"},
        )
        return service.get_task(str(created["task_id"]))

    def test_status_value_maps_the_config_mode_not_a_literal(self) -> None:
        # A resolved config is the single source of the value: an ``off`` config
        # reports ``none`` even when a base image is configured; a satisfied
        # ``kvm`` config reports ``kvm``; a requested kvm that is not operational
        # (``enabled`` false) reports ``kvm-unavailable``, never ``kvm``.
        self.assertEqual(isolation_status_value(_config(mode=MODE_OFF)), ISOLATION_NONE)
        self.assertEqual(
            isolation_status_value(_config(mode=MODE_KVM, enabled=True)),
            ISOLATION_KVM,
        )
        self.assertEqual(
            isolation_status_value(_config(mode=MODE_KVM, enabled=False)),
            ISOLATION_KVM_UNAVAILABLE,
        )

    def test_off_mode_payloads_report_none(self) -> None:
        with patch.dict(os.environ, {"IGOREK_ISOLATION_MODE": "off"}):
            service = self._service()
            detail = service.get_session(self.session_id)
            overview = service.sessions_overview()
            task = self._task_payload(service)

        self.assertEqual(detail["isolation"], ISOLATION_NONE)
        self.assertEqual(
            [entry["isolation"] for entry in overview["sessions"]],
            [ISOLATION_NONE],
        )
        self.assertEqual(task["isolation"], ISOLATION_NONE)

    def test_absent_mode_payloads_report_none(self) -> None:
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("IGOREK_ISOLATION_MODE", None)
            service = self._service()
            detail = service.get_session(self.session_id)

        self.assertEqual(detail["isolation"], ISOLATION_NONE)

    def test_unknown_mode_payloads_report_none(self) -> None:
        with patch.dict(os.environ, {"IGOREK_ISOLATION_MODE": "bwrap"}):
            service = self._service()
            detail = service.get_session(self.session_id)
            task = self._task_payload(service)

        self.assertEqual(detail["isolation"], ISOLATION_NONE)
        self.assertEqual(task["isolation"], ISOLATION_NONE)

    def test_kvm_mode_payloads_report_kvm(self) -> None:
        with patch.dict(os.environ, self._kvm_env()):
            service = self._service()
            detail = service.get_session(self.session_id)
            overview = service.sessions_overview()
            task = self._task_payload(service)

        self.assertEqual(detail["isolation"], ISOLATION_KVM)
        self.assertEqual(
            [entry["isolation"] for entry in overview["sessions"]],
            [ISOLATION_KVM],
        )
        self.assertEqual(task["isolation"], ISOLATION_KVM)

    def test_payload_value_follows_the_resolved_config(self) -> None:
        # The service must consume ``IsolationConfig``: a patched config factory
        # changes the payload value with no environment involvement.
        with patch(
            "factory.core.isolation.status.IsolationConfig.from_env",
            return_value=_config(mode=MODE_KVM, enabled=True),
        ):
            kvm_service = self._service()
            self.assertEqual(
                kvm_service.get_session(self.session_id)["isolation"],
                ISOLATION_KVM,
            )
        with patch(
            "factory.core.isolation.status.IsolationConfig.from_env",
            return_value=_config(mode=MODE_OFF),
        ):
            off_service = self._service()
            self.assertEqual(
                off_service.get_session(self.session_id)["isolation"],
                ISOLATION_NONE,
            )

    def test_create_session_stays_host_side_with_kvm_status(self) -> None:
        # Session creation is instant and host-side: it reports the additive
        # isolation value but must never construct or touch the VM manager - the
        # first task owns the VM boot latency and its failures.
        with patch.dict(os.environ, self._kvm_env()):
            service = self._service()
            with patch(
                "factory.core.isolation.manager.IsolationManager.from_env",
                side_effect=AssertionError("create_session must not touch the isolation manager"),
            ), patch(
                "factory.core.isolation.manager.IsolationManager.ensure_vm",
                side_effect=AssertionError("create_session must not boot a VM"),
            ):
                payload = service.create_session(
                    profile="cc-local-8081",
                    cwd=self._tmp.name,
                    idempotency_key="todo12-instant",
                )
                second = service.get_session(str(payload["session_id"]))

        self.assertEqual(payload["isolation"], ISOLATION_KVM)
        self.assertEqual(second["isolation"], ISOLATION_KVM)
        self.assertEqual(second["session_id"], payload["session_id"])

    def test_existing_payload_fields_are_untouched(self) -> None:
        with patch.dict(os.environ, self._kvm_env()):
            service = self._service()
            detail = service.get_session(self.session_id)

        self.assertIn("messages", detail)
        self.assertIn("tasks", detail)
        self.assertIn("meta", detail)
        self.assertIn("running_task_id", detail)


if __name__ == "__main__":
    unittest.main()
