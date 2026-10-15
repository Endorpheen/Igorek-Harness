"""Regression tests for the session-isolation config and host preflight (todo 1).

The feature flag must default to ``off`` and must never silently degrade a
requested ``kvm`` mode into ``off``: every refusal carries a structural reason
string. Environment resolution is tested through the injected ``env`` mapping
and capability checks through an injected probe, so no KVM host is required.
"""

from __future__ import annotations

import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from factory.core.isolation import config as isolation_config_module
from factory.core.isolation.config import (
    DEFAULT_IDLE_TTL_S,
    DEFAULT_LIBVIRT_URI,
    DEFAULT_MAX_VMS,
    DEFAULT_SESSION_DISK_GB,
    DEFAULT_SESSION_MEMORY_MB,
    DEFAULT_SESSION_VCPUS,
    DEFAULT_VSOCK_PORT,
    HostCapabilities,
    IsolationConfig,
    check_host_capabilities,
)
from factory.core.runtime import get_data_root


def _caps(**overrides: object) -> HostCapabilities:
    values: dict[str, object] = {
        "kvm_present": True,
        "libvirt_reachable": True,
        "virtiofsd_present": True,
        "qemu_img_present": True,
        "reason": None,
    }
    values.update(overrides)
    return HostCapabilities(**values)  # type: ignore[arg-type]


def _probe_ok(_uri: str) -> HostCapabilities:
    return _caps()


def _probe_missing_kvm(_uri: str) -> HostCapabilities:
    return _caps(kvm_present=False, reason="kvm_missing")


def _probe_must_not_run(_uri: str) -> HostCapabilities:
    raise AssertionError("capability probe must not run when it cannot affect the result")


class IsolationConfigTest(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="isolation-config-test-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def _base_image(self) -> Path:
        image = self.tmp / "base.qcow2"
        image.write_bytes(b"qcow2")
        return image

    def _kvm_env(self, **extra: str) -> dict[str, str]:
        env = {
            "IGOREK_ISOLATION_MODE": "kvm",
            "IGOREK_ISOLATION_BASE_IMAGE": str(self._base_image()),
        }
        env.update(extra)
        return env

    def test_default_mode_is_off_without_env(self) -> None:
        config = IsolationConfig.from_env({}, data_root=self.tmp, capability_probe=_probe_must_not_run)
        self.assertEqual(config.mode, "off")
        self.assertFalse(config.enabled)
        self.assertEqual(config.reason, "mode_off")

    def test_explicit_off_mode_is_off(self) -> None:
        config = IsolationConfig.from_env(
            {"IGOREK_ISOLATION_MODE": "off"},
            data_root=self.tmp,
            capability_probe=_probe_must_not_run,
        )
        self.assertEqual(config.mode, "off")
        self.assertFalse(config.enabled)

    def test_unknown_mode_falls_back_to_off_with_warning(self) -> None:
        with self.assertLogs("factory.core.isolation.config", level="WARNING") as logs:
            config = IsolationConfig.from_env(
                {"IGOREK_ISOLATION_MODE": "vm"},
                data_root=self.tmp,
                capability_probe=_probe_must_not_run,
            )
        self.assertEqual(config.mode, "off")
        self.assertFalse(config.enabled)
        self.assertIn("unknown_mode", str(config.reason))
        self.assertTrue(any("vm" in line for line in logs.output))

    def test_empty_mode_falls_back_to_off_with_warning(self) -> None:
        with self.assertLogs("factory.core.isolation.config", level="WARNING"):
            config = IsolationConfig.from_env(
                {"IGOREK_ISOLATION_MODE": "   "},
                data_root=self.tmp,
                capability_probe=_probe_must_not_run,
            )
        self.assertEqual(config.mode, "off")
        self.assertFalse(config.enabled)
        self.assertIn("unknown_mode", str(config.reason))

    def test_kvm_requires_existing_base_image(self) -> None:
        missing = self.tmp / "missing.qcow2"
        config = IsolationConfig.from_env(
            {"IGOREK_ISOLATION_MODE": "kvm", "IGOREK_ISOLATION_BASE_IMAGE": str(missing)},
            data_root=self.tmp,
            capability_probe=_probe_must_not_run,
        )
        self.assertEqual(config.mode, "kvm")
        self.assertFalse(config.enabled)
        self.assertEqual(config.reason, "base_image_missing")
        self.assertEqual(config.base_image, missing)

    def test_kvm_without_base_image_env_fails_closed(self) -> None:
        config = IsolationConfig.from_env(
            {"IGOREK_ISOLATION_MODE": "kvm"},
            data_root=self.tmp,
            capability_probe=_probe_must_not_run,
        )
        self.assertFalse(config.enabled)
        self.assertEqual(config.reason, "base_image_missing")
        self.assertIsNone(config.base_image)

    def test_kvm_with_directory_as_base_image_fails_closed(self) -> None:
        config = IsolationConfig.from_env(
            {"IGOREK_ISOLATION_MODE": "kvm", "IGOREK_ISOLATION_BASE_IMAGE": str(self.tmp)},
            data_root=self.tmp,
            capability_probe=_probe_must_not_run,
        )
        self.assertFalse(config.enabled)
        self.assertEqual(config.reason, "base_image_missing")

    def test_kvm_fails_closed_when_capabilities_missing(self) -> None:
        config = IsolationConfig.from_env(
            self._kvm_env(),
            data_root=self.tmp,
            capability_probe=_probe_missing_kvm,
        )
        self.assertEqual(config.mode, "kvm")
        self.assertFalse(config.enabled)
        self.assertEqual(config.reason, "capability_unavailable:kvm_missing")

    def test_kvm_enabled_with_image_and_capabilities(self) -> None:
        config = IsolationConfig.from_env(self._kvm_env(), data_root=self.tmp, capability_probe=_probe_ok)
        self.assertEqual(config.mode, "kvm")
        self.assertTrue(config.enabled)
        self.assertIsNone(config.reason)

    def test_kvm_probe_receives_configured_libvirt_uri(self) -> None:
        seen: list[str] = []

        def probe(uri: str) -> HostCapabilities:
            seen.append(uri)
            return _caps()

        config = IsolationConfig.from_env(
            self._kvm_env(IGOREK_ISOLATION_LIBVIRT_URI="qemu:///system"),
            data_root=self.tmp,
            capability_probe=probe,
        )
        self.assertTrue(config.enabled)
        self.assertEqual(config.libvirt_uri, "qemu:///system")
        self.assertEqual(seen, ["qemu:///system"])

    def test_off_mode_never_runs_the_capability_probe(self) -> None:
        config = IsolationConfig.from_env({}, data_root=self.tmp, capability_probe=_probe_must_not_run)
        self.assertFalse(config.enabled)

    def test_overlay_dir_defaults_under_data_root(self) -> None:
        config = IsolationConfig.from_env({}, data_root=self.tmp, capability_probe=_probe_ok)
        self.assertEqual(config.overlay_dir, self.tmp / "isolation" / "overlays")

    def test_overlay_dir_default_uses_real_data_root(self) -> None:
        config = IsolationConfig.from_env({}, capability_probe=_probe_ok)
        self.assertEqual(config.overlay_dir, get_data_root() / "isolation" / "overlays")

    def test_overlay_dir_env_override(self) -> None:
        override = self.tmp / "custom-overlays"
        config = IsolationConfig.from_env(
            {"IGOREK_ISOLATION_OVERLAY_DIR": str(override)},
            data_root=self.tmp,
            capability_probe=_probe_ok,
        )
        self.assertEqual(config.overlay_dir, override)

    def test_libvirt_uri_default(self) -> None:
        config = IsolationConfig.from_env({}, data_root=self.tmp, capability_probe=_probe_ok)
        self.assertEqual(config.libvirt_uri, DEFAULT_LIBVIRT_URI)
        self.assertEqual(DEFAULT_LIBVIRT_URI, "qemu:///session")

    def test_env_is_read_from_os_environ_when_not_injected(self) -> None:
        with mock.patch.dict(os.environ, {"IGOREK_ISOLATION_LIBVIRT_URI": "qemu:///system"}, clear=False):
            config = IsolationConfig.from_env(data_root=self.tmp, capability_probe=_probe_ok)
            self.assertEqual(config.libvirt_uri, "qemu:///system")

    def test_vsock_port_default_is_non_privileged(self) -> None:
        config = IsolationConfig.from_env({}, data_root=self.tmp, capability_probe=_probe_ok)
        self.assertEqual(config.vsock_port, DEFAULT_VSOCK_PORT)
        self.assertGreaterEqual(config.vsock_port, 1024)

    def test_vsock_port_override(self) -> None:
        config = IsolationConfig.from_env(
            self._kvm_env(IGOREK_ISOLATION_VSOCK_PORT="6225"),
            data_root=self.tmp,
            capability_probe=_probe_ok,
        )
        self.assertEqual(config.vsock_port, 6225)

    def test_privileged_vsock_port_fails_closed(self) -> None:
        config = IsolationConfig.from_env(
            self._kvm_env(IGOREK_ISOLATION_VSOCK_PORT="80"),
            data_root=self.tmp,
            capability_probe=_probe_ok,
        )
        self.assertFalse(config.enabled)
        self.assertEqual(config.reason, "invalid_config:IGOREK_ISOLATION_VSOCK_PORT")

    def test_non_numeric_vsock_port_fails_closed(self) -> None:
        config = IsolationConfig.from_env(
            self._kvm_env(IGOREK_ISOLATION_VSOCK_PORT="abc"),
            data_root=self.tmp,
            capability_probe=_probe_ok,
        )
        self.assertFalse(config.enabled)
        self.assertEqual(config.reason, "invalid_config:IGOREK_ISOLATION_VSOCK_PORT")

    def test_limits_use_defaults(self) -> None:
        config = IsolationConfig.from_env({}, data_root=self.tmp, capability_probe=_probe_ok)
        self.assertEqual(config.idle_ttl_s, DEFAULT_IDLE_TTL_S)
        self.assertEqual(config.max_vms, DEFAULT_MAX_VMS)
        self.assertEqual(config.session_vcpus, DEFAULT_SESSION_VCPUS)
        self.assertEqual(config.session_memory_mb, DEFAULT_SESSION_MEMORY_MB)
        self.assertEqual(config.session_disk_gb, DEFAULT_SESSION_DISK_GB)

    def test_limits_parse_from_env(self) -> None:
        config = IsolationConfig.from_env(
            self._kvm_env(
                IGOREK_ISOLATION_IDLE_TTL_S="120",
                IGOREK_ISOLATION_MAX_VMS="2",
                IGOREK_ISOLATION_SESSION_VCPUS="4",
                IGOREK_ISOLATION_SESSION_MEMORY_MB="4096",
                IGOREK_ISOLATION_SESSION_DISK_GB="20",
            ),
            data_root=self.tmp,
            capability_probe=_probe_ok,
        )
        self.assertTrue(config.enabled)
        self.assertEqual(config.idle_ttl_s, 120)
        self.assertEqual(config.max_vms, 2)
        self.assertEqual(config.session_vcpus, 4)
        self.assertEqual(config.session_memory_mb, 4096)
        self.assertEqual(config.session_disk_gb, 20)

    def test_invalid_limit_fails_closed_in_kvm_mode(self) -> None:
        config = IsolationConfig.from_env(
            self._kvm_env(IGOREK_ISOLATION_MAX_VMS="0"),
            data_root=self.tmp,
            capability_probe=_probe_ok,
        )
        self.assertFalse(config.enabled)
        self.assertEqual(config.reason, "invalid_config:IGOREK_ISOLATION_MAX_VMS")

    def test_off_mode_ignores_invalid_numbers(self) -> None:
        config = IsolationConfig.from_env(
            {"IGOREK_ISOLATION_MODE": "off", "IGOREK_ISOLATION_MAX_VMS": "nope"},
            data_root=self.tmp,
            capability_probe=_probe_must_not_run,
        )
        self.assertEqual(config.mode, "off")
        self.assertFalse(config.enabled)
        self.assertEqual(config.max_vms, DEFAULT_MAX_VMS)


class HostCapabilitiesTest(unittest.TestCase):
    def test_ok_true_when_all_present(self) -> None:
        caps = _caps()
        self.assertTrue(caps.ok)
        self.assertIsNone(caps.reason)

    def test_ok_false_when_a_capability_is_missing(self) -> None:
        caps = _caps(kvm_present=False, reason="kvm_missing")
        self.assertFalse(caps.ok)
        self.assertEqual(caps.reason, "kvm_missing")

    def _patched(self, **flags: bool) -> HostCapabilities:
        with (
            mock.patch.object(isolation_config_module, "_kvm_device_available", return_value=flags["kvm"]),
            mock.patch.object(isolation_config_module, "_libvirt_reachable", return_value=flags["libvirt"]),
            mock.patch.object(isolation_config_module, "_virtiofsd_available", return_value=flags["virtiofsd"]),
            mock.patch.object(isolation_config_module, "_qemu_img_available", return_value=flags["qemu_img"]),
        ):
            return check_host_capabilities("qemu:///session")

    def test_check_host_capabilities_all_present(self) -> None:
        caps = self._patched(kvm=True, libvirt=True, virtiofsd=True, qemu_img=True)
        self.assertTrue(caps.ok)
        self.assertIsNone(caps.reason)
        self.assertTrue(caps.kvm_present)
        self.assertTrue(caps.libvirt_reachable)
        self.assertTrue(caps.virtiofsd_present)
        self.assertTrue(caps.qemu_img_present)

    def test_check_host_capabilities_reports_kvm_missing_first(self) -> None:
        caps = self._patched(kvm=False, libvirt=False, virtiofsd=False, qemu_img=False)
        self.assertFalse(caps.ok)
        self.assertEqual(caps.reason, "kvm_missing")

    def test_check_host_capabilities_reports_libvirt_missing(self) -> None:
        caps = self._patched(kvm=True, libvirt=False, virtiofsd=True, qemu_img=True)
        self.assertFalse(caps.ok)
        self.assertEqual(caps.reason, "libvirt_unreachable")

    def test_check_host_capabilities_reports_virtiofsd_missing(self) -> None:
        caps = self._patched(kvm=True, libvirt=True, virtiofsd=False, qemu_img=True)
        self.assertFalse(caps.ok)
        self.assertEqual(caps.reason, "virtiofsd_missing")

    def test_check_host_capabilities_reports_qemu_img_missing(self) -> None:
        caps = self._patched(kvm=True, libvirt=True, virtiofsd=True, qemu_img=False)
        self.assertFalse(caps.ok)
        self.assertEqual(caps.reason, "qemu_img_missing")

    def test_check_host_capabilities_returns_bools_on_real_host(self) -> None:
        caps = check_host_capabilities("qemu:///session")
        self.assertIsInstance(caps.kvm_present, bool)
        self.assertIsInstance(caps.libvirt_reachable, bool)
        self.assertIsInstance(caps.virtiofsd_present, bool)
        self.assertIsInstance(caps.qemu_img_present, bool)
        if caps.ok:
            self.assertIsNone(caps.reason)
        else:
            self.assertIsInstance(caps.reason, str)
            self.assertTrue(caps.reason)


if __name__ == "__main__":
    unittest.main()
