#!/usr/bin/env python3
"""Per-session resource limits (plan todo 24, Wave 4): the CPU/memory caps in the
domain XML, the overlay disk size cap on disk, and containment/reporting of a
guest that hits one of its limits.

Two layers are asserted as concrete numbers, never as "limited":

* the domain XML is the libvirt contract for the per-domain cgroup
  (``<cputune>`` -> ``cpu.max``, ``<memtune>`` -> ``memory.max``), so the test
  reads the generated XML back and compares the configured budget;
* the overlay cap is the on-disk qcow2 virtual size, so the test reads it with
  the real ``qemu-img`` (and our own header parser) after a real
  ``qemu-img create``, and proves a write past the cap is refused while the host
  file stays bounded.

No hypervisor is required: the libvirt connection, the channel and the runner
are injected fakes. The overlay-cap tests run the real ``qemu-img``/``qemu-io``
and skip only when those binaries are missing (never a fake pass).

The containment layer has two ends and the last class here drives both for real:
the real in-guest :class:`GuestRunner` (over the real framed channel, as in
``tests/test_isolation_guest_runner.py``) answers a real ``OSError(ENOSPC)`` with
a classified error frame, and the real :class:`IsolationManager` turns that into
destroy+undefine, a terminal ``limit_exceeded:disk`` and one
``isolation.limit_exceeded`` host trace record. Only the hypervisor boundary
(libvirt + ``qemu-img``) stays a fake there.
"""

from __future__ import annotations

import errno
import json
import os
import shutil
import socket
import sys
import tempfile
import threading
import unittest
import xml.etree.ElementTree as ET
from collections.abc import Callable
from pathlib import Path
from typing import Any
from unittest import mock

REPO_ROOT = Path(
    os.environ.get("IGOREK_FACTORY_REPO", Path(__file__).resolve().parents[1])
)
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from factory.core.cc_backend_trace import TRACE_FILENAME  # noqa: E402
from factory.core.isolation import workspace as workspace_module  # noqa: E402
from factory.core.isolation.channel import (  # noqa: E402
    ChannelConfig,
    ChannelMessage,
    ChannelServer,
    MessageKind,
    PeerCid,
    PeerSession,
    SessionId,
    SessionMap,
    guest_connect,
)
from factory.core.isolation.config import (  # noqa: E402
    DEFAULT_SESSION_DISK_GB,
    MODE_KVM,
    HostCapabilities,
    IsolationConfig,
)
from factory.core.isolation.domain import (  # noqa: E402
    CommandResult,
    DomainSpec,
    SessionDomain,
    SubprocessRunner,
    build_domain_xml,
    domain_name,
    overlay_path,
)
from factory.core.isolation.egress_enforce import (  # noqa: E402
    EGRESS_TABLE_REF,
    ApplyResult,
)
from factory.core.isolation.errors import IsolationError  # noqa: E402
from factory.core.isolation.extensions import TaskExtensions  # noqa: E402
from factory.core.isolation.guest_runner import (  # noqa: E402
    GuestRunner,
    RunnerSeams,
    RunnerTuning,
)
from factory.core.isolation.limits import (  # noqa: E402
    CPU_PERIOD_US,
    GIB_BYTES,
    LIMIT_DISK,
    LIMIT_FD,
    LIMIT_MEMORY,
    LIMIT_PIDS,
    MEMORY_OVERHEAD_MIB,
    assert_overlay_cap,
    classify_limit_breach,
    cpu_quota_us,
    limit_failure_result,
    limit_token_for_error,
    memory_hard_limit_mib,
    qcow2_virtual_size,
)
from factory.core.isolation.manager import (  # noqa: E402
    GUEST_ARTIFACT_DIR,
    IsolationManager,
    ManagerSeams,
    SessionSpec,
)
from factory.core.isolation.overlay_guard import OverlayLeaseGuard  # noqa: E402
from factory.core.isolation.preflight import REASON_OK, PreflightResult  # noqa: E402
from factory.core.isolation.session_egress import SessionEgress  # noqa: E402
from factory.core.isolation.workspace import WorkspacePolicy  # noqa: E402

FAKE_CID = 42
QA_CAP_GIB = 2
QA_CAP_BYTES = QA_CAP_GIB * GIB_BYTES
QA_MEMORY_MIB = 2048
QEMU_IMG = shutil.which("qemu-img")
QEMU_IO = shutil.which("qemu-io")
E2E_CHANNEL = ChannelConfig(port=5005)


def fake_egress_builder(
    profile: str, base_url: str, *, host_local_ports: tuple[int, ...] = ()
) -> SessionEgress:
    """Deterministic stand-in for the host renderer (unit tests never resolve DNS)."""
    return SessionEgress(profile=profile, ruleset="# fake ruleset\n")


class FakeEgressApplier:
    """Guest-side stand-in: records the ruleset instead of running nft."""

    def __init__(self) -> None:
        self.applied: list[str] = []

    def __call__(self, ruleset: str) -> ApplyResult:
        self.applied.append(ruleset)
        return ApplyResult(
            applied=True,
            verified=True,
            table=EGRESS_TABLE_REF,
            ruleset_sha256="0" * 64,
            rule_count=1,
            listed_rules=1,
        )


class RecordingRunner:
    """Records argv; delegates to the real ``qemu-img`` when asked."""

    def __init__(self, delegate: bool = False) -> None:
        self.calls: list[list[str]] = []
        self._delegate = delegate
        self._real = SubprocessRunner()

    def __call__(self, argv: list[str]) -> CommandResult:
        self.calls.append(list(argv))
        if self._delegate:
            return self._real(argv)
        return CommandResult(0)


class IgnoringCapRunner(RecordingRunner):
    """A tool that ignores the requested image size (adversarial stand-in)."""

    def __init__(self, ignored_size: int) -> None:
        super().__init__(delegate=True)
        self._ignored_size = ignored_size

    def __call__(self, argv: list[str]) -> CommandResult:
        forced = [str(self._ignored_size) if arg.isdigit() else arg for arg in argv]
        return super().__call__(forced)


class OverlayTouchRunner(RecordingRunner):
    """Records argv and materializes the overlay a real ``qemu-img create`` makes."""

    def __call__(self, argv: list[str]) -> CommandResult:
        result = super().__call__(argv)
        if len(argv) >= 2 and argv[0] == "qemu-img" and argv[1] == "create":
            Path(argv[-2]).write_bytes(b"")
        return result


class FakeDomainHandle:
    """Minimal stand-in for ``libvirt.virDomain`` (lifecycle + live XML)."""

    def __init__(self, name: str, cid: int | None = FAKE_CID) -> None:
        self._name = name
        self._cid = cid
        self.xml = ""
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

    def shutdown(self) -> int:
        return 0

    def destroy(self) -> int:
        self.destroy_calls += 1
        self.active = False
        return 0

    def undefine(self) -> int:
        self.undefine_calls += 1
        return 0

    def isActive(self) -> int:
        return 1 if self.active else 0

    def XMLDesc(self, flags: int = 0) -> str:
        address = f" address='{self._cid}'" if self._cid is not None else ""
        return (
            "<domain><devices><vsock model='virtio'>"
            f"<cid auto='yes'{address}/>"
            "</vsock></devices></domain>"
        )


class FakeConnection:
    """Fake libvirt connection: remembers defined domains by their XML name."""

    def __init__(self, cid: int | None = FAKE_CID) -> None:
        self.cid = cid
        self.defined: dict[str, FakeDomainHandle] = {}
        self.closed = False

    def defineXML(self, xml: str) -> FakeDomainHandle:
        name = ET.fromstring(xml).findtext("name") or ""
        cid = None if self.cid is None else self.cid + len(self.defined)
        handle = FakeDomainHandle(name, cid)
        handle.xml = xml
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


class FakeRegistry:
    """Fake channel registry keyed by the host-owned session id."""

    def __init__(self) -> None:
        self.endpoints: dict[str, FakeEndpoint] = {}
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


def limit_error_message(reason: str) -> ChannelMessage:
    return ChannelMessage(kind=MessageKind.ERROR, seq=1, payload={"reason": reason})


class LimitPolicyTests(unittest.TestCase):
    """The configured budget -> cgroup contract, as pure arithmetic."""

    def test_cpu_quota_is_the_configured_vcpu_budget(self) -> None:
        self.assertEqual(CPU_PERIOD_US, 100_000)
        self.assertEqual(cpu_quota_us(1), 100_000)
        self.assertEqual(cpu_quota_us(2), 200_000)
        self.assertEqual(cpu_quota_us(4), 400_000)

    def test_memory_hard_limit_is_the_budget_plus_the_vmm_overhead(self) -> None:
        self.assertEqual(MEMORY_OVERHEAD_MIB, 256)
        self.assertEqual(memory_hard_limit_mib(2048), 2304)
        self.assertEqual(memory_hard_limit_mib(512), 768)
        self.assertGreater(memory_hard_limit_mib(128), 128)

    def test_limit_reasons_are_classified_by_exact_token(self) -> None:
        cases = {
            "enospc": LIMIT_DISK,
            "no_space_left": LIMIT_DISK,
            "disk_full": LIMIT_DISK,
            "oom": LIMIT_MEMORY,
            "out_of_memory": LIMIT_MEMORY,
            "cannot_allocate_memory": LIMIT_MEMORY,
            "pids_limit": LIMIT_PIDS,
            "fork_failed": LIMIT_PIDS,
            "thread_creation_failed": LIMIT_PIDS,
            "too_many_open_files": LIMIT_FD,
            "emfile": LIMIT_FD,
            "fd_limit": LIMIT_FD,
        }
        for reason, expected in cases.items():
            with self.subTest(reason=reason):
                breach = classify_limit_breach(reason)
                assert breach is not None
                self.assertEqual(breach.kind, expected)
                self.assertEqual(breach.reason, reason)
                self.assertTrue(breach.detail)

    def test_unknown_and_prose_reasons_are_not_limit_breaches(self) -> None:
        for reason in ("", "executor_failed", "disk full", "enospc_error", "ENOSPC"):
            with self.subTest(reason=reason):
                self.assertIsNone(classify_limit_breach(reason))

    def test_limit_failure_result_is_terminal_and_names_the_breach(self) -> None:
        breach = classify_limit_breach("enospc")
        assert breach is not None
        result = limit_failure_result(breach, session_id="sess-1")

        self.assertEqual(result["status"], "fail")
        self.assertEqual(result["failure_reason"], f"limit_exceeded:{LIMIT_DISK}")
        self.assertIn("enospc", result["failure_details"])
        self.assertIn("sess-1", result["failure_details"])
        self.assertEqual(result["error"], result["failure_details"])
        self.assertEqual(result["usage"], {})
        self.assertEqual(result["tool_calls_count"], 0)


class DomainXmlLimitTests(unittest.TestCase):
    """The generated XML must carry the configured caps, not libvirt defaults."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="igorek-limits-xml-")
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name)
        self.base_image = root / "base.qcow2"
        self.base_image.write_bytes(b"qcow2-base")
        self.workspace = root / "workspace"
        self.workspace.mkdir()
        self.overlay_dir = root / "overlays"
        self.spec = DomainSpec(
            session_id="limits-1",
            base_image=self.base_image,
            workspace_host_path=self.workspace,
            overlay_dir=self.overlay_dir,
            vcpus=2,
            memory_mib=QA_MEMORY_MIB,
            disk_gib=QA_CAP_GIB,
        )

    def test_xml_carries_the_cpu_cap_as_a_cgroup_quota(self) -> None:
        root = ET.fromstring(build_domain_xml(self.spec))
        cputune = root.find("cputune")
        assert cputune is not None
        period = cputune.find("period")
        quota = cputune.find("quota")
        assert period is not None and quota is not None

        self.assertEqual(period.text, "100000")
        self.assertEqual(quota.text, "200000")
        self.assertEqual(root.findtext("vcpu"), "2")

    def test_xml_carries_the_memory_cap_as_a_cgroup_hard_limit(self) -> None:
        root = ET.fromstring(build_domain_xml(self.spec))
        memtune = root.find("memtune")
        assert memtune is not None
        hard = memtune.find("hard_limit")
        soft = memtune.find("soft_limit")
        assert hard is not None and soft is not None

        self.assertEqual(hard.get("unit"), "MiB")
        self.assertEqual(hard.text, "2304")
        self.assertEqual(soft.get("unit"), "MiB")
        self.assertEqual(soft.text, "2048")
        self.assertEqual(root.findtext("memory"), "2048")
        self.assertEqual(root.findtext("currentMemory"), "2048")

    def test_xml_caps_track_a_non_default_spec(self) -> None:
        spec = DomainSpec(
            session_id="limits-3",
            base_image=self.base_image,
            workspace_host_path=self.workspace,
            overlay_dir=self.overlay_dir,
            vcpus=3,
            memory_mib=1536,
            disk_gib=4,
        )
        root = ET.fromstring(build_domain_xml(spec))

        self.assertEqual(root.findtext("cputune/quota"), "300000")
        self.assertEqual(root.findtext("memtune/hard_limit"), "1792")
        self.assertEqual(root.findtext("memtune/soft_limit"), "1536")

    def test_xml_host_fd_surface_is_closed_and_fixed(self) -> None:
        spec = DomainSpec(
            session_id="limits-fd",
            base_image=self.base_image,
            workspace_host_path=self.workspace,
            overlay_dir=self.overlay_dir,
            network=True,
        )
        root = ET.fromstring(build_domain_xml(spec))
        devices = root.find("devices")
        assert devices is not None

        for tag in ("hostdev", "chardev", "rng", "redirdev", "graphics", "audio"):
            with self.subTest(tag=tag):
                self.assertEqual(root.findall(f".//{tag}"), [])
        self.assertEqual(len(devices.findall("disk")), 1)
        self.assertEqual(len(devices.findall("filesystem")), 1)
        self.assertEqual(len(devices.findall("vsock")), 1)
        self.assertEqual(len(devices.findall("interface")), 1)
        self.assertEqual(devices.find("interface").get("type"), "user")
        file_sources = [
            source
            for source in root.findall(".//source")
            if "file" in source.attrib or "dir" in source.attrib
        ]
        self.assertEqual(len(file_sources), 3)
        self.assertEqual(
            devices.find("disk/backingStore/source").get("file"), str(spec.base_image)
        )
        for source in file_sources:
            for value in source.attrib.values():
                self.assertTrue(Path(value).is_absolute(), value)

    def test_invalid_disk_cap_is_refused_before_any_side_effect(self) -> None:
        runner = RecordingRunner()
        connection = FakeConnection()
        domain = SessionDomain(connection, runner)
        spec = DomainSpec(
            session_id="limits-0",
            base_image=self.base_image,
            workspace_host_path=self.workspace,
            overlay_dir=self.overlay_dir,
            disk_gib=0,
        )

        with self.assertRaises(IsolationError) as caught:
            domain.define(spec)

        self.assertEqual(caught.exception.reason, "invalid_limits")
        self.assertEqual(runner.calls, [])
        self.assertEqual(connection.defined, {})

    def test_domain_default_cap_matches_the_config_default(self) -> None:
        spec = DomainSpec(
            session_id="limits-default",
            base_image=self.base_image,
            workspace_host_path=self.workspace,
            overlay_dir=self.overlay_dir,
        )
        self.assertEqual(spec.disk_gib, DEFAULT_SESSION_DISK_GB)


class Qcow2SizeParserTests(unittest.TestCase):
    def test_missing_or_non_qcow2_files_have_no_readable_size(self) -> None:
        with tempfile.TemporaryDirectory(prefix="igorek-limits-parser-") as tmp:
            missing = Path(tmp) / "missing.qcow2"
            self.assertIsNone(qcow2_virtual_size(missing))

            plain = Path(tmp) / "plain.bin"
            plain.write_bytes(b"not-a-qcow2-header")
            self.assertIsNone(qcow2_virtual_size(plain))

    def test_assert_overlay_cap_is_a_noop_for_a_missing_overlay(self) -> None:
        with tempfile.TemporaryDirectory(prefix="igorek-limits-parser-") as tmp:
            missing = Path(tmp) / "missing.qcow2"
            self.assertIsNone(assert_overlay_cap(missing, disk_gib=QA_CAP_GIB))


@unittest.skipIf(QEMU_IMG is None, "SKIP: qemu-img is not installed")
class OverlayCapTests(unittest.TestCase):
    """The disk cap is the on-disk qcow2 virtual size; prove it with real tools."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="igorek-limits-overlay-")
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.runner = RecordingRunner(delegate=True)
        self.connection = FakeConnection()
        self.workspace = self.root / "workspace"
        self.workspace.mkdir()
        self.overlay_dir = self.root / "overlays"
        self.base_image = self.root / "base.qcow2"
        created = self.runner(["qemu-img", "create", "-f", "qcow2", str(self.base_image), "8G"])
        self.assertEqual(created.returncode, 0, created.stderr)
        self.runner.calls.clear()
        self.spec = DomainSpec(
            session_id="cap-2g",
            base_image=self.base_image,
            workspace_host_path=self.workspace,
            overlay_dir=self.overlay_dir,
            disk_gib=QA_CAP_GIB,
        )

    def _info(self, overlay: Path) -> dict[str, Any]:
        result = SubprocessRunner()(["qemu-img", "info", "--output=json", str(overlay)])
        self.assertEqual(result.returncode, 0, result.stderr)
        return dict(json.loads(result.stdout))

    def test_create_argv_passes_the_cap_as_the_image_size(self) -> None:
        SessionDomain(self.connection, self.runner).define(self.spec)

        create = self.runner.calls[0]
        self.assertEqual(
            create,
            [
                "qemu-img",
                "create",
                "-f",
                "qcow2",
                "-F",
                "qcow2",
                "-b",
                str(self.base_image),
                str(overlay_path(self.spec)),
                str(QA_CAP_BYTES),
            ],
        )

    def test_overlay_virtual_size_equals_the_configured_cap(self) -> None:
        SessionDomain(self.connection, self.runner).define(self.spec)
        overlay = overlay_path(self.spec)

        self.assertEqual(qcow2_virtual_size(overlay), QA_CAP_BYTES)
        self.assertEqual(self._info(overlay)["virtual-size"], QA_CAP_BYTES)
        self.assertLess(overlay.stat().st_size, QA_CAP_BYTES)
        self.assertEqual(qcow2_virtual_size(self.base_image), 8 * GIB_BYTES)

    def test_write_past_the_cap_is_refused_and_the_host_file_stays_bounded(self) -> None:
        SessionDomain(self.connection, self.runner).define(self.spec)
        overlay = overlay_path(self.spec)

        if QEMU_IO is None:
            self.skipTest("SKIP: qemu-io is not installed")
        write = SubprocessRunner()(
            [
                "qemu-io",
                "-f",
                "qcow2",
                "-c",
                f"write -P 0 {QA_CAP_BYTES} 4096",
                str(overlay),
            ]
        )

        self.assertNotEqual(write.returncode, 0)
        self.assertIn("failed", (write.stdout + write.stderr).lower())
        self.assertLess(overlay.stat().st_size, QA_CAP_BYTES)
        self.assertEqual(qcow2_virtual_size(overlay), QA_CAP_BYTES)

    def test_cap_below_the_base_size_shrinks_the_overlay_only(self) -> None:
        SessionDomain(self.connection, self.runner).define(self.spec)

        self.assertEqual(qcow2_virtual_size(overlay_path(self.spec)), QA_CAP_BYTES)
        self.assertEqual(qcow2_virtual_size(self.base_image), 8 * GIB_BYTES)

    def test_cap_above_the_base_size_grows_the_overlay_to_the_cap(self) -> None:
        small_base = self.root / "small-base.qcow2"
        created = self.runner(
            ["qemu-img", "create", "-f", "qcow2", str(small_base), str(64 * 1024 * 1024)]
        )
        self.assertEqual(created.returncode, 0, created.stderr)
        spec = DomainSpec(
            session_id="cap-grow",
            base_image=small_base,
            workspace_host_path=self.workspace,
            overlay_dir=self.overlay_dir,
            disk_gib=QA_CAP_GIB,
        )

        SessionDomain(self.connection, self.runner).define(spec)

        self.assertEqual(qcow2_virtual_size(overlay_path(spec)), QA_CAP_BYTES)

    def test_an_overlay_that_ignored_the_cap_is_refused_and_discarded(self) -> None:
        ignoring = IgnoringCapRunner(ignored_size=8 * GIB_BYTES)
        domain = SessionDomain(self.connection, ignoring)

        with self.assertRaises(IsolationError) as caught:
            domain.define(self.spec)

        self.assertEqual(caught.exception.reason, "overlay_size_mismatch")
        self.assertIn(str(QA_CAP_BYTES), caught.exception.detail)
        self.assertFalse(overlay_path(self.spec).exists())
        self.assertEqual(self.connection.defined, {})

    def test_teardown_removes_the_capped_overlay_without_orphans(self) -> None:
        domain = SessionDomain(self.connection, self.runner)
        domain.define(self.spec)
        domain.start()
        overlay = overlay_path(self.spec)

        domain.teardown()

        self.assertFalse(overlay.exists())
        self.assertEqual(list(self.overlay_dir.glob("*.qcow2")), [])
        domain.define(self.spec)
        self.assertEqual(qcow2_virtual_size(overlay), QA_CAP_BYTES)


class ManagerLimitTests(unittest.TestCase):
    """Config -> spec -> domain wiring, and containment of a breached guest."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="igorek-limits-manager-")
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
        self.runner = RecordingRunner()

    def build_manager(
        self, config: IsolationConfig | None = None
    ) -> IsolationManager:
        def tracking_preflight(cfg: IsolationConfig) -> PreflightResult:
            return PreflightResult(ok=True, mode=cfg.mode, reason=REASON_OK)

        seams = ManagerSeams(
            connect=lambda uri: self.connection,
            runner=self.runner,
            channel=self.registry,
            session_map=self.session_map,
            workspace_policy=make_policy(self.root),
            overlay_leases=self.leases,
            preflight_fn=tracking_preflight,
            egress=fake_egress_builder,
        )
        return IsolationManager(config or enabled_config(self.root), seams=seams)

    def session(self, session_id: str = "sess-1") -> SessionSpec:
        workspace = self.workspace if session_id == "sess-1" else self.workspace_b
        return SessionSpec(session_id=session_id, workspace_cwd=str(workspace))

    def task(self, session_id: str = "sess-1") -> dict[str, Any]:
        return {"task_id": "task-1", "metadata": {"session": session_id}}

    def domain_name_for(self, session_id: str = "sess-1") -> str:
        return domain_name(
            DomainSpec(
                session_id=session_id,
                base_image=self.root / "base.qcow2",
                workspace_host_path=(
                    self.workspace if session_id == "sess-1" else self.workspace_b
                ),
                overlay_dir=self.overlay_dir,
            )
        )

    def test_configured_disk_cap_reaches_the_overlay_command(self) -> None:
        config = enabled_config(self.root, IGOREK_ISOLATION_SESSION_DISK_GB="2")
        manager = self.build_manager(config)

        manager.ensure_vm(self.session())

        create = self.runner.calls[0]
        self.assertEqual(create[0], "qemu-img")
        self.assertEqual(create[-1], str(QA_CAP_BYTES))

    def test_configured_cpu_and_memory_caps_reach_the_domain_xml(self) -> None:
        config = enabled_config(
            self.root,
            IGOREK_ISOLATION_SESSION_VCPUS="1",
            IGOREK_ISOLATION_SESSION_MEMORY_MB="512",
        )
        manager = self.build_manager(config)

        manager.ensure_vm(self.session())

        xml = self.connection.defined[self.domain_name_for()].xml
        root = ET.fromstring(xml)
        self.assertEqual(root.findtext("vcpu"), "1")
        self.assertEqual(root.findtext("cputune/quota"), "100000")
        self.assertEqual(root.findtext("memtune/soft_limit"), "512")
        self.assertEqual(root.findtext("memtune/hard_limit"), "768")

    def test_guest_disk_limit_error_is_contained_and_reported(self) -> None:
        artifacts = self.root / "artifacts"
        artifacts.mkdir()
        endpoint = FakeEndpoint([limit_error_message("enospc")])
        self.registry.endpoints["sess-1"] = endpoint
        manager = self.build_manager()
        vm = manager.ensure_vm(self.session())
        handle = self.connection.defined[self.domain_name_for()]
        overlay = Path(vm.domain.overlay or "")

        result = manager.run_task(
            self.task(),
            str(self.workspace),
            dispatch_payload(artifact_workdir=str(artifacts)),
        )

        self.assertEqual(result["status"], "fail")
        self.assertEqual(result["failure_reason"], f"limit_exceeded:{LIMIT_DISK}")
        self.assertIn("enospc", result["failure_details"])
        self.assertEqual(handle.destroy_calls, 1)
        self.assertEqual(handle.undefine_calls, 1)
        self.assertEqual(handle.isActive(), 0)
        self.assertTrue(endpoint.closed)
        self.assertFalse(self.leases.is_leased(overlay))
        self.assertIsNone(self.session_map.session_for_cid(PeerCid(FAKE_CID)))
        records = [
            json.loads(line)
            for line in (artifacts / TRACE_FILENAME).read_text(encoding="utf-8").splitlines()
        ]
        limit_records = [
            record for record in records if record["kind"] == "isolation.limit_exceeded"
        ]
        self.assertEqual(len(limit_records), 1)
        self.assertEqual(limit_records[0]["subject"], "sess-1")
        self.assertEqual(limit_records[0]["reason"], "enospc")
        self.assertEqual(limit_records[0]["limit"], LIMIT_DISK)

    def test_guest_oom_is_contained_as_a_memory_breach(self) -> None:
        self.registry.endpoints["sess-1"] = FakeEndpoint([limit_error_message("oom")])
        manager = self.build_manager()
        manager.ensure_vm(self.session())

        result = manager.run_task(self.task(), str(self.workspace), dispatch_payload())

        self.assertEqual(result["status"], "fail")
        self.assertEqual(result["failure_reason"], f"limit_exceeded:{LIMIT_MEMORY}")

    def test_contained_session_can_start_a_fresh_vm(self) -> None:
        self.registry.endpoints["sess-1"] = FakeEndpoint(
            [limit_error_message("pids_limit")]
        )
        manager = self.build_manager()
        first = manager.ensure_vm(self.session())
        manager.run_task(self.task(), str(self.workspace), dispatch_payload())

        second = manager.ensure_vm(self.session())

        self.assertIsNot(first, second)
        self.assertIsNot(first.domain, second.domain)
        self.assertEqual(self.connection.defined[self.domain_name_for()].create_calls, 1)
        self.assertNotEqual(first.peer_cid, second.peer_cid)
        self.assertEqual(
            self.session_map.session_for_cid(second.peer_cid), "sess-1"
        )

    def test_limit_containment_leaves_a_sibling_session_running(self) -> None:
        self.registry.endpoints["sess-1"] = FakeEndpoint([limit_error_message("enospc")])
        self.registry.endpoints["sess-2"] = FakeEndpoint([])
        manager = self.build_manager()
        manager.ensure_vm(self.session("sess-1"))
        manager.ensure_vm(self.session("sess-2"))
        sibling = self.connection.defined[self.domain_name_for("sess-2")]

        result = manager.run_task(self.task(), str(self.workspace), dispatch_payload())

        self.assertEqual(result["status"], "fail")
        self.assertEqual(sibling.destroy_calls, 0)
        self.assertEqual(sibling.undefine_calls, 0)
        self.assertEqual(sibling.isActive(), 1)

    def test_non_limit_guest_error_still_raises_the_guest_error(self) -> None:
        self.registry.endpoints["sess-1"] = FakeEndpoint(
            [limit_error_message("executor_failed")]
        )
        manager = self.build_manager()
        manager.ensure_vm(self.session())

        with self.assertRaises(IsolationError) as caught:
            manager.run_task(self.task(), str(self.workspace), dispatch_payload())

        self.assertEqual(caught.exception.code, "guest_error")
        self.assertIn("executor_failed", caught.exception.detail)

    def test_off_mode_is_unchanged_and_creates_no_vm(self) -> None:
        config = IsolationConfig.from_env(
            {"IGOREK_ISOLATION_MODE": "off"}, data_root=self.root
        )
        manager = self.build_manager(config)

        with self.assertRaises(IsolationError) as caught:
            manager.run_task(self.task(), str(self.workspace), dispatch_payload())

        self.assertEqual(caught.exception.code, "isolation_not_enabled")
        self.assertEqual(self.runner.calls, [])
        self.assertEqual(self.connection.defined, {})


class LimitErrorTokenTests(unittest.TestCase):
    """The guest half's production of the exact tokens the host classifies."""

    def test_real_exhaustion_errors_map_onto_the_documented_tokens(self) -> None:
        cases = [
            (OSError(errno.ENOSPC, "No space left on device"), "enospc", LIMIT_DISK),
            (
                OSError(errno.EDQUOT, "Disk quota exceeded"),
                "disk_quota_exceeded",
                LIMIT_DISK,
            ),
            (
                OSError(errno.ENOMEM, "Cannot allocate memory"),
                "cannot_allocate_memory",
                LIMIT_MEMORY,
            ),
            (MemoryError(), "oom", LIMIT_MEMORY),
            (OSError(errno.EMFILE, "Too many open files"), "emfile", LIMIT_FD),
            (OSError(errno.ENFILE, "File table overflow"), "fd_limit", LIMIT_FD),
            (
                BlockingIOError(errno.EAGAIN, "Resource temporarily unavailable"),
                "fork_failed",
                LIMIT_PIDS,
            ),
        ]

        for error, token, kind in cases:
            with self.subTest(token=token):
                # When: a real executor failure is classified
                produced = limit_token_for_error(error)

                # Then: it names the documented token for its class
                self.assertEqual(produced, token)
                breach = classify_limit_breach(token)
                self.assertIsNotNone(breach)
                assert breach is not None
                self.assertEqual(breach.kind, kind)

    def test_non_limit_failures_have_no_token(self) -> None:
        cases = [
            RuntimeError("boom"),
            OSError(errno.EACCES, "Permission denied"),
            OSError("no errno"),
            ValueError("x"),
            None,
        ]

        for error in cases:
            with self.subTest(error=repr(error)):
                self.assertIsNone(limit_token_for_error(error))


class RealGuestLimitContainmentTests(unittest.TestCase):
    """End-to-end containment with the real guest runner and the real manager.

    The verifier's repro drove the real runner with ``OSError(28)`` and got a
    plain ``fail/executor_error``: the VM stayed up, no ``isolation.limit_exceeded``
    was traced and the task reported the wrong reason. Here the real guest runner
    answers over the real framed channel and the real manager dispatches to it,
    so a real ENOSPC reaches ``destroy``+``undefine`` and ``limit_exceeded:disk``.
    Only the hypervisor boundary (libvirt + ``qemu-img``) is a fake.
    """

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="igorek-limits-e2e-")
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.workspace = self.root / "workspace"
        self.workspace.mkdir()
        self.workspace_b = self.root / "workspace-b"
        self.workspace_b.mkdir()
        self.artifacts = self.root / "artifacts"
        self.artifacts.mkdir()
        self.guest_workspace = self.root / "guest-workspace"
        self.overlay_dir = self.root / "overlays"
        self.connection = FakeConnection()
        self.sessions = SessionMap()
        self.server = ChannelServer(E2E_CHANNEL, self.sessions)
        self.addCleanup(self.server.close)
        self.leases = OverlayLeaseGuard()
        self.addCleanup(self.leases.close)
        self.runner = OverlayTouchRunner()
        self.guest_errors: list[BaseException] = []
        self.guest_exit_codes: list[int] = []

    def build_manager(self) -> IsolationManager:
        def tracking_preflight(cfg: IsolationConfig) -> PreflightResult:
            return PreflightResult(ok=True, mode=cfg.mode, reason=REASON_OK)

        seams = ManagerSeams(
            connect=lambda uri: self.connection,
            runner=self.runner,
            channel=self.server,
            session_map=self.sessions,
            workspace_policy=make_policy(self.root),
            overlay_leases=self.leases,
            preflight_fn=tracking_preflight,
            egress=fake_egress_builder,
            extensions=lambda payload: TaskExtensions(),
        )
        return IsolationManager(enabled_config(self.root), seams=seams)

    def session(self, session_id: str = "sess-1") -> SessionSpec:
        workspace = self.workspace if session_id == "sess-1" else self.workspace_b
        return SessionSpec(session_id=session_id, workspace_cwd=str(workspace))

    def task(self, session_id: str = "sess-1") -> dict[str, Any]:
        return {"task_id": "task-1", "metadata": {"session": session_id}}

    def domain_name_for(self, session_id: str = "sess-1") -> str:
        return domain_name(
            DomainSpec(
                session_id=session_id,
                base_image=self.root / "base.qcow2",
                workspace_host_path=(
                    self.workspace if session_id == "sess-1" else self.workspace_b
                ),
                overlay_dir=self.overlay_dir,
            )
        )

    def start_guest(
        self, make_error: Callable[[], BaseException]
    ) -> tuple[socket.socket, threading.Thread]:
        """Run a real GuestRunner in a thread; returns its host socket and thread."""
        srv_sock, cli_sock = socket.socketpair()
        applier = FakeEgressApplier()
        errors = self.guest_errors
        exit_codes = self.guest_exit_codes

        def executor(**kwargs: Any) -> dict[str, Any]:
            raise make_error()

        def guest() -> None:
            try:
                session = guest_connect(cli_sock, E2E_CHANNEL)
                runner = GuestRunner(
                    session,
                    seams=RunnerSeams(
                        executor=executor,
                        apply_egress=applier,
                        kernel_log=lambda: (),
                    ),
                    tuning=RunnerTuning(
                        guest_roots=E2E_CHANNEL.guest_roots, poll_interval_s=0.01
                    ),
                )
                try:
                    exit_codes.append(runner.serve_one())
                finally:
                    session.close()
            except BaseException as exc:  # reported to the test
                errors.append(exc)

        thread = threading.Thread(target=guest, daemon=True)
        thread.start()
        return srv_sock, thread

    def dispatch_and_contain(
        self, make_error: Callable[[], BaseException], expected_kind: str
    ) -> dict[str, Any]:
        """Drive one real guest failure through the real manager; return the record."""
        srv_sock, thread = self.start_guest(make_error)
        manager = self.build_manager()
        # In a VM the single export is mounted at the production guest path; this
        # in-process guest has no mount, so the export root is mapped into
        # tmp_path (still inside the /tmp guest root the runner validates). The
        # artifact dir rides under that root, so it needs no separate mapping.
        with mock.patch.object(
            workspace_module, "GUEST_WORKSPACE_PATH", str(self.guest_workspace)
        ):
            vm = manager.ensure_vm(self.session())
            overlay = Path(vm.domain.overlay or "")
            self.assertTrue(overlay.exists())
            manager.ensure_vm(self.session("sess-2"))
            sibling = self.connection.defined[self.domain_name_for("sess-2")]
            host = self.server.handle_connection(srv_sock, PeerCid(FAKE_CID))
            self.assertIsNotNone(host)
            assert host is not None
            handle = self.connection.defined[self.domain_name_for()]
            result = manager.run_task(
                self.task(),
                str(self.workspace),
                dispatch_payload(artifact_workdir=str(self.artifacts), timeout_s=10),
            )

        self.assertEqual(result["status"], "fail")
        self.assertEqual(result["failure_reason"], f"limit_exceeded:{expected_kind}")
        self.assertEqual(handle.destroy_calls, 1)
        self.assertEqual(handle.undefine_calls, 1)
        self.assertEqual(handle.isActive(), 0)
        self.assertTrue(host.closed)
        self.assertFalse(overlay.exists())
        orphans = [
            path
            for path in self.overlay_dir.glob("*.qcow2")
            if path.stem.startswith("sess-1-")
        ]
        self.assertEqual(orphans, [])
        self.assertIsNone(self.sessions.session_for_cid(PeerCid(FAKE_CID)))
        self.assertEqual(sibling.destroy_calls, 0)
        self.assertEqual(sibling.undefine_calls, 0)
        self.assertEqual(sibling.isActive(), 1)
        records = [
            json.loads(line)
            for line in (self.artifacts / TRACE_FILENAME)
            .read_text(encoding="utf-8")
            .splitlines()
        ]
        limit_records = [
            record for record in records if record["kind"] == "isolation.limit_exceeded"
        ]
        self.assertEqual(len(limit_records), 1)
        self.assertEqual(limit_records[0]["subject"], "sess-1")
        self.assertEqual(limit_records[0]["limit"], expected_kind)
        thread.join(timeout=5.0)
        self.assertFalse(thread.is_alive())
        self.assertEqual(self.guest_errors, [])
        self.assertEqual(self.guest_exit_codes, [0])
        manager.stop("sess-2")
        return limit_records[0]

    def test_real_guest_enospc_is_contained_end_to_end(self) -> None:
        # Given: a real guest whose executor dies with a real ENOSPC
        # When: the real manager dispatches to it over the real channel
        record = self.dispatch_and_contain(
            lambda: OSError(errno.ENOSPC, "No space left on device"), LIMIT_DISK
        )

        # Then: the breach is contained and reported as a disk limit
        self.assertEqual(record["reason"], "enospc")

    def test_real_guest_oom_is_contained_end_to_end(self) -> None:
        # Given: a real guest whose executor dies with MemoryError
        # When: the real manager dispatches to it over the real channel
        record = self.dispatch_and_contain(lambda: MemoryError(), LIMIT_MEMORY)

        # Then: the breach is contained and reported as a memory limit
        self.assertEqual(record["reason"], "oom")


if __name__ == "__main__":
    unittest.main()
