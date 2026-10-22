#!/usr/bin/env python3
"""Unit tests for the per-session libvirt domain lifecycle (no KVM required).

The libvirt connection and the command runner are the only two external seams;
both are replaced with fakes here, so this suite never needs a hypervisor. The
real-KVM smoke is decorated with the availability probe and skips with
``SKIP: no KVM`` (never a fake pass).
"""

from __future__ import annotations

import importlib.util
import os
import re
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(
    os.environ.get("IGOREK_FACTORY_REPO", Path(__file__).resolve().parents[1])
)
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from factory.core.isolation import host_escape  # noqa: E402
from factory.core.isolation.domain import (  # noqa: E402
    OVERLAY_DIR_ENV,
    CommandResult,
    DomainSpec,
    IsolationError,
    SessionDomain,
    SubprocessRunner,
    build_domain_xml,
    domain_name,
    kvm_unavailable_reason,
    open_libvirt_connection,
    overlay_path,
    resolve_overlay_dir,
    virtiofs_socket_for_session,
)

_KVM_REASON = kvm_unavailable_reason()
_SAFE_DOMAIN_NAME = re.compile(r"^igorek-session-[A-Za-z0-9_-]+$")


class FakeDomainHandle:
    """Minimal stand-in for ``libvirt.virDomain``."""

    def __init__(self, name: str) -> None:
        self._name = name
        self.active = False
        self.create_calls = 0
        self.shutdown_calls = 0
        self.destroy_calls = 0
        self.undefine_calls = 0
        self.fail_with: Exception | None = None

    def name(self) -> str:
        return self._name

    def _maybe_fail(self) -> None:
        if self.fail_with is not None:
            raise self.fail_with

    def create(self) -> int:
        self._maybe_fail()
        self.create_calls += 1
        self.active = True
        return 0

    def shutdown(self) -> int:
        self._maybe_fail()
        self.shutdown_calls += 1
        return 0

    def destroy(self) -> int:
        self._maybe_fail()
        self.destroy_calls += 1
        self.active = False
        return 0

    def undefine(self) -> int:
        self._maybe_fail()
        self.undefine_calls += 1
        return 0

    def isActive(self) -> int:
        return 1 if self.active else 0


class FakeConnection:
    """Minimal stand-in for ``libvirt.virConnect``."""

    def __init__(self, define_error: Exception | None = None) -> None:
        self.define_error = define_error
        self.defined_xml: list[str] = []
        self.domains: dict[str, FakeDomainHandle] = {}

    def defineXML(self, xml: str) -> FakeDomainHandle:
        if self.define_error is not None:
            raise self.define_error
        self.defined_xml.append(xml)
        name = xml.split("<name>", 1)[1].split("</name>", 1)[0]
        handle = FakeDomainHandle(name)
        self.domains[name] = handle
        return handle

    def lookupByName(self, name: str) -> FakeDomainHandle:
        if name not in self.domains:
            raise LookupError(name)
        return self.domains[name]

    def close(self) -> None:
        return None


class RecordingRunner:
    """Records every argv; returns queued results (default: exit 0)."""

    def __init__(
        self,
        results: list[CommandResult] | None = None,
        on_call: Callable[[list[str]], None] | None = None,
    ) -> None:
        self.calls: list[list[str]] = []
        self.results = list(results or [])
        self.on_call = on_call

    def __call__(self, argv) -> CommandResult:
        self.calls.append(list(argv))
        if self.on_call is not None:
            self.on_call(list(argv))
        if self.results:
            return self.results.pop(0)
        return CommandResult(0)


class _DomainCase(unittest.TestCase):
    """Given: a temp base image, workspace and injected fake seams."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="igorek_isolation_domain_")
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name)
        self.base_image = root / "base.qcow2"
        self.base_image.write_bytes(b"qcow2-base")
        self.overlay_dir = root / "overlays"
        self.workspace = root / "workspace"
        self.workspace.mkdir()
        self.connection = FakeConnection()
        self.runner = RecordingRunner(
            on_call=lambda argv: Path(argv[-2]).write_bytes(b"qcow2-overlay")
        )
        self.domain = SessionDomain(self.connection, self.runner)
        self.spec = DomainSpec(
            session_id="abc-123",
            base_image=self.base_image,
            workspace_host_path=self.workspace,
            overlay_dir=self.overlay_dir,
        )


class OverlayCreationTests(_DomainCase):
    def test_define_overlay_argv_always_passes_backing_format_and_disk_cap(self) -> None:
        self.domain.define(self.spec)

        self.assertEqual(
            self.runner.calls,
            [
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
                    str(8589934592),
                ]
            ],
        )

    def test_overlay_lives_under_configured_dir(self) -> None:
        self.domain.define(self.spec)

        overlay = overlay_path(self.spec)
        self.assertEqual(overlay.parent, self.overlay_dir)
        self.assertTrue(overlay.is_file())
        self.assertEqual(self.domain.overlay, overlay)

    def test_session_id_cannot_escape_overlay_dir(self) -> None:
        spec = DomainSpec(
            session_id="../../etc/passwd",
            base_image=self.base_image,
            workspace_host_path=self.workspace,
            overlay_dir=self.overlay_dir,
        )

        self.domain.define(spec)

        overlay = overlay_path(spec)
        self.assertEqual(overlay.parent, self.overlay_dir)
        self.assertNotIn("..", overlay.name)
        self.assertTrue(overlay.is_file())

    def test_domain_name_is_libvirt_safe_and_deterministic(self) -> None:
        name = domain_name(self.spec)

        self.assertRegex(name, _SAFE_DOMAIN_NAME)
        self.assertEqual(name, domain_name(self.spec))

    def test_stale_overlay_is_rejected_not_reused(self) -> None:
        stale = overlay_path(self.spec)
        stale.parent.mkdir(parents=True)
        stale.write_bytes(b"stale-untouched")

        with self.assertRaises(IsolationError) as ctx:
            self.domain.define(self.spec)

        self.assertEqual(ctx.exception.reason, "overlay_exists")
        self.assertEqual(self.runner.calls, [])
        self.assertEqual(self.connection.defined_xml, [])
        self.domain.teardown()
        self.assertEqual(stale.read_bytes(), b"stale-untouched")

    def test_define_after_teardown_succeeds(self) -> None:
        self.domain.define(self.spec)
        self.domain.teardown()
        self.assertFalse(overlay_path(self.spec).exists())

        self.domain.define(self.spec)

        self.assertTrue(overlay_path(self.spec).is_file())


class XmlGenerationTests(_DomainCase):
    def test_base_owner_is_preserved_without_disabling_overlay_or_apparmor_labels(self) -> None:
        for external in (False, True):
            spec = replace(self.spec, virtiofs_socket=virtiofs_socket_for_session(self.spec.session_id) if external else None)
            root = ET.fromstring(build_domain_xml(spec))
            disk = root.find("./devices/disk")
            backing = disk.find("backingStore")
            self.assertEqual(backing.get("type"), "file")
            self.assertEqual(backing.find("format").get("type"), "qcow2")
            self.assertEqual(backing.find("source").get("file"), str(spec.base_image))
            self.assertEqual(backing.find("source/seclabel").attrib, {"model": "dac", "relabel": "no"})
            self.assertIsNotNone(backing.find("backingStore"))
            self.assertEqual(disk.find("source").get("file"), str(overlay_path(spec)))
            self.assertEqual(disk.findall("source/seclabel"), [])
            self.assertEqual(root.findall("seclabel"), [])
            self.assertEqual(root.findall(".//seclabel[@model='apparmor']"), [])

    def test_define_returns_deterministic_xml(self) -> None:
        self.assertEqual(build_domain_xml(self.spec), build_domain_xml(self.spec))

        xml = self.domain.define(self.spec)

        self.assertEqual(xml, build_domain_xml(self.spec))
        self.assertEqual(self.connection.defined_xml, [xml])

    def test_xml_has_exactly_one_filesystem_and_one_vsock(self) -> None:
        root = ET.fromstring(build_domain_xml(self.spec))
        devices = root.findall("./devices")

        self.assertEqual(len(devices), 1)
        self.assertEqual(len(devices[0].findall("filesystem")), 1)
        self.assertEqual(len(devices[0].findall("vsock")), 1)
        self.assertEqual(root.findall(".//hostdev"), [])
        self.assertEqual(root.findall(".//interface"), [])

    def test_filesystem_is_virtiofsd_namespace_sandbox_without_passthrough(self) -> None:
        xml = build_domain_xml(self.spec)
        root = ET.fromstring(xml)
        filesystem = root.find("./devices/filesystem")

        assert filesystem is not None
        self.assertEqual(filesystem.get("type"), "mount")
        self.assertIsNone(filesystem.get("accessmode"))
        self.assertNotIn("accessmode", xml)
        self.assertNotIn("passthrough", ET.tostring(filesystem, encoding="unicode"))
        driver = filesystem.find("driver")
        assert driver is not None
        self.assertEqual(driver.get("type"), "virtiofs")
        binary = filesystem.find("binary")
        assert binary is not None
        self.assertEqual(binary.get("path"), "/usr/libexec/virtiofsd")
        sandbox = binary.find("sandbox")
        assert sandbox is not None
        self.assertEqual(sandbox.get("mode"), "namespace")
        source = filesystem.find("source")
        assert source is not None
        self.assertEqual(source.get("dir"), str(self.workspace))

    def test_external_virtiofs_socket_is_exactly_allowlisted(self) -> None:
        socket_path = overlay_path(self.spec).with_suffix(".sock")
        spec = replace(self.spec, virtiofs_socket=socket_path)
        xml = build_domain_xml(spec)
        filesystem = ET.fromstring(xml).find("./devices/filesystem")

        self.assertEqual(filesystem.find("source").get("socket"), str(socket_path))
        self.assertIsNone(filesystem.find("binary"))
        self.assertIsNone(filesystem.find("idmap"))
        self.assertEqual(
            host_escape.scan_domain_xml(
                xml,
                allowed_export_paths=(str(self.workspace),),
                allowed_virtiofs_socket_paths=(str(socket_path),),
            ),
            (),
        )
        self.assertIn(
            "filesystem_socket_not_allowed",
            {violation.code for violation in host_escape.scan_domain_xml(
                xml, allowed_export_paths=(str(self.workspace),)
            )},
        )

    def test_xml_has_no_host_sockets_or_lan_exposure(self) -> None:
        xml = build_domain_xml(self.spec)

        for forbidden in (
            "<hostdev",
            "<interface",
            "<graphics",
            "hostfwd",
            "<source socket",
            "<source dev",
            "<source protocol",
        ):
            self.assertNotIn(forbidden, xml)

    def test_vsock_uses_automatic_cid(self) -> None:
        root = ET.fromstring(build_domain_xml(self.spec))
        vsock = root.find("./devices/vsock")
        assert vsock is not None

        cid = vsock.find("cid")
        assert cid is not None
        self.assertEqual(cid.get("auto"), "yes")
        self.assertIsNone(cid.get("address"))

    def test_xml_reflects_configured_cpu_and_memory_limits(self) -> None:
        spec = DomainSpec(
            session_id="session-limits",
            base_image=self.base_image,
            workspace_host_path=self.workspace,
            overlay_dir=self.overlay_dir,
            vcpus=3,
            memory_mib=1536,
        )
        root = ET.fromstring(build_domain_xml(spec))

        vcpu = root.find("vcpu")
        memory = root.find("memory")
        current_memory = root.find("currentMemory")
        assert vcpu is not None and memory is not None and current_memory is not None
        self.assertEqual(vcpu.text, "3")
        self.assertEqual(memory.text, "1536")
        self.assertEqual(memory.get("unit"), "MiB")
        self.assertEqual(current_memory.text, "1536")

    def test_disk_points_at_overlay_under_overlay_dir(self) -> None:
        root = ET.fromstring(build_domain_xml(self.spec))
        source = root.find("./devices/disk/source")

        assert source is not None
        disk_path = Path(source.get("file", ""))
        self.assertEqual(disk_path.parent, self.overlay_dir)
        self.assertEqual(disk_path, overlay_path(self.spec))


class MalformedInputTests(_DomainCase):
    def test_missing_base_image_fails_before_any_libvirt_or_qemu_call(self) -> None:
        spec = DomainSpec(
            session_id="abc-123",
            base_image=Path(self._tmp.name) / "missing.qcow2",
            workspace_host_path=self.workspace,
            overlay_dir=self.overlay_dir,
        )

        with self.assertRaises(IsolationError) as ctx:
            self.domain.define(spec)

        self.assertEqual(ctx.exception.reason, "base_image_missing")
        self.assertEqual(self.runner.calls, [])
        self.assertEqual(self.connection.defined_xml, [])

    def test_missing_workspace_fails_before_any_call(self) -> None:
        spec = DomainSpec(
            session_id="abc-123",
            base_image=self.base_image,
            workspace_host_path=Path(self._tmp.name) / "nope",
            overlay_dir=self.overlay_dir,
        )

        with self.assertRaises(IsolationError) as ctx:
            self.domain.define(spec)

        self.assertEqual(ctx.exception.reason, "workspace_missing")
        self.assertEqual(self.runner.calls, [])
        self.assertEqual(self.connection.defined_xml, [])

    def test_relative_workspace_is_rejected(self) -> None:
        spec = DomainSpec(
            session_id="abc-123",
            base_image=self.base_image,
            workspace_host_path=Path("relative/workspace"),
            overlay_dir=self.overlay_dir,
        )

        with self.assertRaises(IsolationError) as ctx:
            self.domain.define(spec)

        self.assertEqual(ctx.exception.reason, "invalid_workspace")

    def test_invalid_limits_are_rejected(self) -> None:
        for spec_kwargs in ({"vcpus": 0}, {"memory_mib": 64}):
            with self.subTest(spec_kwargs=spec_kwargs):
                spec = DomainSpec(
                    session_id="abc-123",
                    base_image=self.base_image,
                    workspace_host_path=self.workspace,
                    overlay_dir=self.overlay_dir,
                    **spec_kwargs,
                )
                with self.assertRaises(IsolationError) as ctx:
                    self.domain.define(spec)
                self.assertEqual(ctx.exception.reason, "invalid_limits")

    def test_empty_session_id_is_rejected(self) -> None:
        spec = DomainSpec(
            session_id="   ",
            base_image=self.base_image,
            workspace_host_path=self.workspace,
            overlay_dir=self.overlay_dir,
        )

        with self.assertRaises(IsolationError) as ctx:
            self.domain.define(spec)

        self.assertEqual(ctx.exception.reason, "invalid_session_id")


class FailurePathTests(_DomainCase):
    def test_overlay_create_failure_is_structured_and_discards_partial_overlay(self) -> None:
        def write_partial(argv: list[str]) -> None:
            Path(argv[-2]).write_bytes(b"partial")

        runner = RecordingRunner(
            [CommandResult(1, stderr="qemu-img: backing file format not specified")],
            on_call=write_partial,
        )
        domain = SessionDomain(self.connection, runner)

        with self.assertRaises(IsolationError) as ctx:
            domain.define(self.spec)

        self.assertEqual(ctx.exception.reason, "overlay_create_failed")
        self.assertIn("backing file format", str(ctx.exception))
        self.assertFalse(overlay_path(self.spec).exists())
        self.assertEqual(self.connection.defined_xml, [])

    def test_define_failure_removes_overlay_and_raises_structured_error(self) -> None:
        connection = FakeConnection(define_error=RuntimeError("libvirt: invalid XML"))
        domain = SessionDomain(connection, self.runner)

        with self.assertRaises(IsolationError) as ctx:
            domain.define(self.spec)

        self.assertEqual(ctx.exception.reason, "domain_define_failed")
        self.assertIn("invalid XML", str(ctx.exception))
        self.assertFalse(overlay_path(self.spec).exists())

    def test_start_before_define_is_refused(self) -> None:
        with self.assertRaises(IsolationError) as ctx:
            self.domain.start()

        self.assertEqual(ctx.exception.reason, "domain_not_defined")

    def test_start_failure_is_structured(self) -> None:
        self.domain.define(self.spec)
        handle = self.connection.domains[domain_name(self.spec)]
        handle.fail_with = RuntimeError("qemu: virtiofsd spawn failed")

        with self.assertRaises(IsolationError) as ctx:
            self.domain.start()

        self.assertEqual(ctx.exception.reason, "domain_start_failed")


class LifecycleTests(_DomainCase):
    def test_start_stop_destroy_reach_the_domain_handle(self) -> None:
        self.domain.define(self.spec)
        handle = self.connection.domains[domain_name(self.spec)]

        self.domain.start()
        self.assertEqual(handle.create_calls, 1)
        self.assertTrue(handle.isActive())

        self.domain.stop()
        self.assertEqual(handle.shutdown_calls, 1)

        self.domain.destroy()
        self.assertEqual(handle.destroy_calls, 1)
        self.assertFalse(handle.isActive())

    def test_teardown_removes_overlay_file_and_undefines_domain(self) -> None:
        self.domain.define(self.spec)
        self.domain.start()
        handle = self.connection.domains[domain_name(self.spec)]
        overlay = overlay_path(self.spec)
        self.assertTrue(overlay.is_file())

        self.domain.teardown()

        self.assertFalse(overlay.exists())
        self.assertEqual(handle.destroy_calls, 1)
        self.assertEqual(handle.undefine_calls, 1)

    def test_teardown_is_idempotent(self) -> None:
        self.domain.define(self.spec)
        self.domain.teardown()

        self.domain.teardown()

        self.assertFalse(overlay_path(self.spec).exists())

    def test_teardown_without_define_is_a_noop(self) -> None:
        self.domain.teardown()

        self.assertEqual(self.runner.calls, [])
        self.assertEqual(self.connection.defined_xml, [])


class ExternalVirtiofsLifecycleTests(_DomainCase):
    def test_cleanup_removes_a_socket_not_registered_in_the_kernel(self) -> None:
        import socket

        from factory.core.isolation.domain import cleanup_orphaned_virtiofs_socket

        socket_path = self.overlay_dir / "orphan.sock"
        socket_path.parent.mkdir(parents=True, exist_ok=True)
        stale = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        stale.bind(str(socket_path))
        stale.close()

        self.assertTrue(cleanup_orphaned_virtiofs_socket(socket_path))
        self.assertFalse(socket_path.exists())

    def test_cleanup_preserves_a_socket_registered_in_the_kernel(self) -> None:
        import socket

        from factory.core.isolation.domain import cleanup_orphaned_virtiofs_socket

        socket_path = self.overlay_dir / "live.sock"
        socket_path.parent.mkdir(parents=True, exist_ok=True)
        live = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        live.bind(str(socket_path))
        live.listen()
        self.addCleanup(live.close)

        self.assertFalse(cleanup_orphaned_virtiofs_socket(socket_path))
        self.assertTrue(socket_path.exists())

    def test_cleanup_preserves_regular_files_and_symlinks(self) -> None:
        from factory.core.isolation.domain import cleanup_orphaned_virtiofs_socket

        regular = self.overlay_dir / "regular.sock"
        regular.parent.mkdir(parents=True, exist_ok=True)
        regular.write_text("keep", encoding="utf-8")
        link = self.overlay_dir / "link.sock"
        link.symlink_to(regular)

        self.assertFalse(cleanup_orphaned_virtiofs_socket(regular))
        self.assertFalse(cleanup_orphaned_virtiofs_socket(link))
        self.assertEqual(regular.read_text(encoding="utf-8"), "keep")
        self.assertTrue(link.is_symlink())

    def test_start_removes_only_a_confirmed_orphaned_unix_socket(self) -> None:
        import socket

        from factory.core.isolation import domain as domain_module

        socket_path = self.overlay_dir / f"{domain_module._session_slug(self.spec.session_id)}.sock"
        socket_path.parent.mkdir(parents=True, exist_ok=True)
        stale = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        stale.bind(str(socket_path))
        stale.close()
        spec = replace(self.spec, virtiofs_socket=socket_path)

        class _Proc:
            returncode = None

            def poll(self):
                return None

            def terminate(self):
                return None

            def wait(self, timeout=None):
                return 0

        def start_daemon(_argv, **_kwargs):
            live = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            live.bind(str(socket_path))
            live.listen()
            live.close()
            return _Proc()

        with patch.object(domain_module, "_unix_socket_path_is_live", return_value=False), patch.object(
            domain_module.subprocess, "Popen", side_effect=start_daemon
        ):
            self.domain._start_external_virtiofsd(spec)

        self.assertTrue(socket_path.exists())
        self.assertTrue(self.domain._owned_socket)

    def test_start_preserves_a_live_socket_path(self) -> None:
        from factory.core.isolation import domain as domain_module

        socket_path = self.overlay_dir / f"{domain_module._session_slug(self.spec.session_id)}.sock"
        socket_path.parent.mkdir(parents=True, exist_ok=True)
        socket_path.touch()
        spec = replace(self.spec, virtiofs_socket=socket_path)

        with patch.object(domain_module, "_unix_socket_path_is_live", return_value=True), patch.object(
            domain_module.subprocess, "Popen"
        ) as popen:
            with self.assertRaisesRegex(IsolationError, "virtiofs_socket_exists"):
                self.domain._start_external_virtiofsd(spec)

        self.assertTrue(socket_path.exists())
        popen.assert_not_called()

    def test_start_preserves_non_socket_path(self) -> None:
        from factory.core.isolation import domain as domain_module

        socket_path = self.overlay_dir / f"{domain_module._session_slug(self.spec.session_id)}.sock"
        socket_path.parent.mkdir(parents=True, exist_ok=True)
        socket_path.write_text("keep", encoding="utf-8")
        spec = replace(self.spec, virtiofs_socket=socket_path)

        with patch.object(domain_module.subprocess, "Popen") as popen:
            with self.assertRaisesRegex(IsolationError, "virtiofs_socket_exists"):
                self.domain._start_external_virtiofsd(spec)

        self.assertEqual(socket_path.read_text(encoding="utf-8"), "keep")
        popen.assert_not_called()

    def test_stopping_owned_virtiofsd_removes_socket_and_pidfile(self) -> None:
        socket_path = overlay_path(self.spec).with_suffix(".sock")
        socket_path.parent.mkdir(parents=True, exist_ok=True)
        pidfile = Path(f"{socket_path}.pid")
        socket_path.touch()
        pidfile.write_text("12345", encoding="utf-8")
        spec = replace(self.spec, virtiofs_socket=socket_path)
        self.domain._spec = spec
        self.domain._owned_socket = True

        self.domain._stop_external_virtiofsd()

        self.assertFalse(socket_path.exists())
        self.assertFalse(pidfile.exists())

    def test_daemon_starts_before_vm_and_stops_on_teardown(self) -> None:
        spec = replace(self.spec, virtiofs_socket=overlay_path(self.spec).with_suffix(".sock"))
        events = []
        with patch.object(self.domain, "_start_external_virtiofsd", side_effect=lambda _: events.append("daemon_start")), patch.object(
            self.domain, "_stop_external_virtiofsd", side_effect=lambda: events.append("daemon_stop")
        ):
            self.domain.define(spec)
            self.domain.start()
            events.append("vm_started" if self.domain.is_active() else "vm_stopped")
            self.domain.teardown()

        self.assertEqual(events, ["daemon_start", "vm_started", "daemon_stop"])


class DeploymentSeamTests(_DomainCase):
    def test_resolve_overlay_dir_honours_env_then_data_root(self) -> None:
        with patch.dict(os.environ, {OVERLAY_DIR_ENV: str(self.overlay_dir)}):
            self.assertEqual(resolve_overlay_dir(), self.overlay_dir)

        env = dict(os.environ)
        env.pop(OVERLAY_DIR_ENV, None)
        env["IGOREK_FACTORY_HOME"] = self._tmp.name
        with patch.dict(os.environ, env, clear=True):
            self.assertEqual(
                resolve_overlay_dir(),
                Path(self._tmp.name) / "isolation" / "overlays",
            )

    @unittest.skipIf(
        importlib.util.find_spec("libvirt") is not None,
        "libvirt bindings are installed on this host",
    )
    def test_open_connection_without_libvirt_bindings_fails_closed(self) -> None:
        with self.assertRaises(IsolationError) as ctx:
            open_libvirt_connection()

        self.assertEqual(ctx.exception.reason, "libvirt_unavailable")


@unittest.skipUnless(
    os.environ.get("IGOREK_REAL_KVM_SMOKE") == "1",
    "real KVM smoke requires IGOREK_REAL_KVM_SMOKE=1 and a built image",
)
class RealKvmSmokeTest(unittest.TestCase):
    """Explicit smoke against a real system libvirt host and a built image."""

    def setUp(self) -> None:
        self.uri = os.environ.get("IGOREK_ISOLATION_LIBVIRT_URI", "qemu:///system")
        reason = kvm_unavailable_reason(uri=self.uri)
        if reason is not None:
            self.skipTest(f"SKIP: no system KVM ({reason})")
        self.base = Path(os.environ.get("IGOREK_ISOLATION_BASE_IMAGE", ""))
        if not self.base.is_file():
            self.skipTest("SKIP: IGOREK_ISOLATION_BASE_IMAGE is not a built image")

    def test_real_domain_lifecycle(self) -> None:
        connection = open_libvirt_connection(self.uri)
        self.addCleanup(connection.close)
        with tempfile.TemporaryDirectory(prefix="igorek_kvm_smoke_") as tmp:
            workspace = Path(tmp) / "workspace"
            workspace.mkdir()
            session_id = f"smoke-{os.getpid()}"
            spec = DomainSpec(
                session_id=session_id,
                base_image=self.base,
                workspace_host_path=workspace,
                overlay_dir=resolve_overlay_dir(),
                vcpus=1,
                memory_mib=256,
                disk_gib=int(os.environ.get("IGOREK_ISOLATION_SESSION_DISK_GB", "8")),
                virtiofs_socket=virtiofs_socket_for_session(session_id),
            )
            domain = SessionDomain(connection)
            self.addCleanup(domain.teardown)
            domain.define(spec)
            domain.start()
            self.assertTrue(domain.is_active())
            domain.teardown()
            self.assertFalse(overlay_path(spec).exists())


if __name__ == "__main__":
    unittest.main()
