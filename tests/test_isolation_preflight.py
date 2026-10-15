"""Regression tests for the fail-closed KVM preflight and version handshake (todo 34).

A requested ``kvm`` mode must never start without the host capabilities
(KVM/libvirt/virtiofsd/qemu-img) and a base image whose manifest is intact and
whose recorded tool version matches the host expectation. Every refusal is
structural: ``ok=False`` plus an ``isolation_unavailable:<detail>`` reason, never
a silent degradation to a non-isolated run. ``off`` stays side-effect free - no
capability probe and no image hashing.

All checks run through injected fakes, so the suite needs no real KVM. The one
test that touches the real host skips loudly when KVM is absent and never fakes
a pass.
"""

from __future__ import annotations

import dataclasses
import os
import shutil
import tempfile
import unittest
from pathlib import Path

from factory.core.isolation import base_image as base_image_module
from factory.core.isolation.base_image import (
    GUEST_HANDSHAKE_ID,
    ImageBuildInfo,
    VerifyResult,
    build_manifest,
    write_manifest,
)
from factory.core.isolation.config import (
    DEFAULT_LIBVIRT_URI,
    ENV_BASE_IMAGE,
    ENV_MODE,
    CapabilityProbe,
    HostCapabilities,
    IsolationConfig,
    check_host_capabilities,
)
from factory.core.isolation.errors import IsolationError
from factory.core.isolation.preflight import (
    PreflightError,
    PreflightResult,
    enforce_guest_version,
    expected_guest_version,
    preflight,
    verify_guest_version,
)

TEST_TOOL_VERSION = "9.9.9-preflight-test"
IMAGE_BYTES = b"qcow2-image-bytes"


def _caps(
    kvm_present: bool = True,
    libvirt_reachable: bool = True,
    virtiofsd_present: bool = True,
    qemu_img_present: bool = True,
    reason: str | None = None,
) -> HostCapabilities:
    return HostCapabilities(
        kvm_present=kvm_present,
        libvirt_reachable=libvirt_reachable,
        virtiofsd_present=virtiofsd_present,
        qemu_img_present=qemu_img_present,
        reason=reason,
    )


def _probe_ok(_uri: str) -> HostCapabilities:
    return _caps()


def _probe_missing_kvm(_uri: str) -> HostCapabilities:
    return _caps(kvm_present=False, reason="kvm_missing")


def _probe_missing_virtiofsd(_uri: str) -> HostCapabilities:
    return _caps(virtiofsd_present=False, reason="virtiofsd_missing")


def _probe_must_not_run(_uri: str) -> HostCapabilities:
    raise AssertionError("capability probe must not run when it cannot affect the result")


def _verifier_must_not_run(
    image_path: str | os.PathLike[str],
    *,
    manifest_path: str | os.PathLike[str] | None = None,
    expected_tool_version: str | None = None,
) -> VerifyResult:
    raise AssertionError("image verification must not run when preflight already refused")


class _PreflightFixture(unittest.TestCase):
    """Shared temp dir, fake base image + manifest, and config helpers."""

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="isolation-preflight-test-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def _write_image(self, tool_version: str = TEST_TOOL_VERSION) -> Path:
        """Create ``base.qcow2`` plus its manifest, as the build recipe does."""
        image = self.tmp / "base.qcow2"
        image.write_bytes(IMAGE_BYTES)
        manifest = build_manifest(
            image,
            ImageBuildInfo(tool_version=tool_version, repo_commit="test"),
        )
        write_manifest(manifest, image)
        return image

    def _kvm_env(self, image: Path) -> dict[str, str]:
        return {ENV_MODE: "kvm", ENV_BASE_IMAGE: str(image)}

    def _kvm_config(
        self,
        image: Path,
        capability_probe: CapabilityProbe = _probe_ok,
    ) -> IsolationConfig:
        return IsolationConfig.from_env(
            self._kvm_env(image),
            data_root=self.tmp,
            capability_probe=capability_probe,
        )


class PreflightModeOffTest(_PreflightFixture):
    def test_explicit_off_is_ok_without_probing_or_verifying(self) -> None:
        config = IsolationConfig.from_env(
            {ENV_MODE: "off"},
            data_root=self.tmp,
            capability_probe=_probe_must_not_run,
        )
        result = preflight(
            config,
            capability_probe=_probe_must_not_run,
            image_verifier=_verifier_must_not_run,
        )
        self.assertIsInstance(result, PreflightResult)
        self.assertTrue(result.ok)
        self.assertEqual(result.mode, "off")
        self.assertEqual(result.reason, "mode_off")
        self.assertIsNone(result.capabilities)

    def test_absent_mode_is_ok_without_probing_or_verifying(self) -> None:
        config = IsolationConfig.from_env(
            {},
            data_root=self.tmp,
            capability_probe=_probe_must_not_run,
        )
        result = preflight(
            config,
            capability_probe=_probe_must_not_run,
            image_verifier=_verifier_must_not_run,
        )
        self.assertTrue(result.ok)
        self.assertEqual(result.mode, "off")
        self.assertEqual(result.reason, "mode_off")


class PreflightRefusalTest(_PreflightFixture):
    def test_missing_kvm_is_refused_with_structural_reason(self) -> None:
        config = self._kvm_config(self._write_image())
        result = preflight(config, capability_probe=_probe_missing_kvm, tool_version=TEST_TOOL_VERSION)
        self.assertFalse(result.ok)
        self.assertEqual(result.mode, "kvm")
        self.assertEqual(result.reason, "isolation_unavailable:kvm_missing")
        self.assertIsNotNone(result.capabilities)
        assert result.capabilities is not None
        self.assertFalse(result.capabilities.kvm_present)

    def test_hidden_virtiofsd_is_refused_with_structural_reason(self) -> None:
        config = self._kvm_config(self._write_image())
        result = preflight(
            config,
            capability_probe=_probe_missing_virtiofsd,
            tool_version=TEST_TOOL_VERSION,
        )
        self.assertFalse(result.ok)
        self.assertEqual(result.reason, "isolation_unavailable:virtiofsd_missing")

    def test_capability_refusal_skips_image_verification(self) -> None:
        config = self._kvm_config(self._write_image())
        result = preflight(
            config,
            capability_probe=_probe_missing_kvm,
            image_verifier=_verifier_must_not_run,
            tool_version=TEST_TOOL_VERSION,
        )
        self.assertEqual(result.reason, "isolation_unavailable:kvm_missing")

    def test_stale_image_tool_version_is_refused(self) -> None:
        image = self._write_image(tool_version="1.0.0-old")
        config = self._kvm_config(image)
        result = preflight(config, capability_probe=_probe_ok, tool_version=TEST_TOOL_VERSION)
        self.assertFalse(result.ok)
        self.assertEqual(result.reason, "isolation_unavailable:version_mismatch")

    def test_tampered_image_is_refused(self) -> None:
        image = self._write_image()
        image.write_bytes(IMAGE_BYTES + b"-tampered")
        config = self._kvm_config(image)
        result = preflight(config, capability_probe=_probe_ok, tool_version=TEST_TOOL_VERSION)
        self.assertFalse(result.ok)
        self.assertEqual(result.reason, "isolation_unavailable:checksum_mismatch")

    def test_image_without_manifest_is_refused(self) -> None:
        image = self.tmp / "base.qcow2"
        image.write_bytes(IMAGE_BYTES)
        config = self._kvm_config(image)
        result = preflight(config, capability_probe=_probe_ok, tool_version=TEST_TOOL_VERSION)
        self.assertFalse(result.ok)
        self.assertEqual(result.reason, "isolation_unavailable:missing_manifest")

    def test_vanished_image_is_refused(self) -> None:
        image = self._write_image()
        config = self._kvm_config(image)
        image.unlink()
        result = preflight(config, capability_probe=_probe_ok, tool_version=TEST_TOOL_VERSION)
        self.assertFalse(result.ok)
        self.assertEqual(result.reason, "isolation_unavailable:missing_image")

    def test_unreadable_image_is_refused_not_raised(self) -> None:
        def verifier(
            image_path: str | os.PathLike[str],
            *,
            manifest_path: str | os.PathLike[str] | None = None,
            expected_tool_version: str | None = None,
        ) -> VerifyResult:
            raise OSError("permission denied")

        config = self._kvm_config(self._write_image())
        result = preflight(
            config,
            capability_probe=_probe_ok,
            image_verifier=verifier,
            tool_version=TEST_TOOL_VERSION,
        )
        self.assertFalse(result.ok)
        self.assertTrue(result.reason.startswith("isolation_unavailable:image_unreadable:"), result.reason)

    def test_config_level_refusal_is_surfaced_and_not_overridden(self) -> None:
        config = self._kvm_config(self._write_image(), capability_probe=_probe_missing_kvm)
        self.assertFalse(config.enabled)
        result = preflight(
            config,
            capability_probe=_probe_ok,
            image_verifier=_verifier_must_not_run,
            tool_version=TEST_TOOL_VERSION,
        )
        self.assertFalse(result.ok)
        self.assertEqual(result.mode, "kvm")
        self.assertTrue(result.reason.startswith("isolation_unavailable:"))
        self.assertIn("kvm_missing", result.reason)

    def test_unknown_mode_is_refused(self) -> None:
        config = dataclasses.replace(self._kvm_config(self._write_image()), mode="bwrap")
        result = preflight(
            config,
            capability_probe=_probe_must_not_run,
            image_verifier=_verifier_must_not_run,
        )
        self.assertFalse(result.ok)
        self.assertEqual(result.reason, "isolation_unavailable:unknown_mode:bwrap")

    def test_kvm_without_base_image_is_refused(self) -> None:
        config = dataclasses.replace(self._kvm_config(self._write_image()), base_image=None)
        result = preflight(
            config,
            capability_probe=_probe_must_not_run,
            image_verifier=_verifier_must_not_run,
        )
        self.assertFalse(result.ok)
        self.assertEqual(result.reason, "isolation_unavailable:base_image_missing")

    def test_refusal_never_degrades_to_off_or_ok(self) -> None:
        config = self._kvm_config(self._write_image())
        for probe in (_probe_missing_kvm, _probe_missing_virtiofsd):
            with self.subTest(probe=probe.__name__):
                result = preflight(config, capability_probe=probe, tool_version=TEST_TOOL_VERSION)
                self.assertFalse(result.ok)
                self.assertEqual(result.mode, "kvm")
                self.assertTrue(result.reason.startswith("isolation_unavailable:"), result.reason)


class PreflightReadyTest(_PreflightFixture):
    def test_capabilities_and_matching_image_produce_ok(self) -> None:
        config = self._kvm_config(self._write_image())
        result = preflight(config, capability_probe=_probe_ok, tool_version=TEST_TOOL_VERSION)
        self.assertTrue(result.ok)
        self.assertEqual(result.mode, "kvm")
        self.assertEqual(result.reason, "ok")
        self.assertIsNotNone(result.capabilities)

    def test_capability_probe_receives_the_configured_libvirt_uri(self) -> None:
        seen: list[str] = []

        def probe(uri: str) -> HostCapabilities:
            seen.append(uri)
            return _caps()

        config = dataclasses.replace(
            self._kvm_config(self._write_image()),
            libvirt_uri="qemu:///system",
        )
        result = preflight(config, capability_probe=probe, tool_version=TEST_TOOL_VERSION)
        self.assertTrue(result.ok)
        self.assertEqual(seen, ["qemu:///system"])


class GuestHandshakeTest(unittest.TestCase):
    def test_expected_guest_version_carries_the_tool_version(self) -> None:
        self.assertEqual(
            expected_guest_version("1.2.3"),
            f"{GUEST_HANDSHAKE_ID}/1.2.3",
        )

    def test_verify_guest_version_accepts_the_expected_report(self) -> None:
        expected = expected_guest_version("1.2.3")
        self.assertTrue(verify_guest_version(expected, tool_version="1.2.3"))
        self.assertTrue(verify_guest_version(f"  {expected}  ", tool_version="1.2.3"))

    def test_verify_guest_version_rejects_mismatch_and_missing_report(self) -> None:
        expected = expected_guest_version("1.2.3")
        self.assertFalse(verify_guest_version(expected, tool_version="9.9.9"))
        self.assertFalse(verify_guest_version(f"{GUEST_HANDSHAKE_ID}/1.0.0", tool_version="1.2.3"))
        self.assertFalse(verify_guest_version(None, tool_version="1.2.3"))
        self.assertFalse(verify_guest_version("", tool_version="1.2.3"))

    def test_enforce_guest_version_returns_the_accepted_report(self) -> None:
        expected = expected_guest_version("1.2.3")
        self.assertEqual(enforce_guest_version(expected, tool_version="1.2.3"), expected)

    def test_enforce_guest_version_raises_on_mismatch(self) -> None:
        with self.assertRaises(PreflightError) as caught:
            enforce_guest_version("igorek-isolation-guest/1.0.0", tool_version="1.2.3")
        self.assertEqual(caught.exception.reason, "guest_version_mismatch")

    def test_enforce_guest_version_raises_on_missing_report(self) -> None:
        with self.assertRaises(PreflightError) as caught:
            enforce_guest_version(None, tool_version="1.2.3")
        self.assertEqual(caught.exception.reason, "guest_version_mismatch")


class PreflightErrorCatchabilityTest(unittest.TestCase):
    """A handshake refusal must be catchable as the canonical ``IsolationError``.

    Regression (todo 37): ``PreflightError`` used to be a private
    ``RuntimeError`` subclass, so ``except IsolationError`` around the boot
    handshake let the refusal escape as an unhandled exception even though every
    other isolation module raised the shared type.
    """

    def test_preflight_error_is_a_subclass_of_isolation_error(self) -> None:
        self.assertTrue(issubclass(PreflightError, IsolationError))

    def test_mismatch_is_caught_by_the_canonical_isolation_error(self) -> None:
        caught: IsolationError | None = None
        try:
            enforce_guest_version("igorek-isolation-guest/1.0.0", tool_version="1.2.3")
        except IsolationError as exc:
            caught = exc

        self.assertIsNotNone(caught)
        assert caught is not None
        self.assertEqual(caught.code, "guest_version_mismatch")
        self.assertEqual(caught.reason, "guest_version_mismatch")

    def test_missing_report_is_caught_by_the_canonical_isolation_error(self) -> None:
        caught: IsolationError | None = None
        try:
            enforce_guest_version(None, tool_version="1.2.3")
        except IsolationError as exc:
            caught = exc

        self.assertIsNotNone(caught)
        assert caught is not None
        self.assertEqual(caught.code, "guest_version_mismatch")


class RealHostPreflightTest(_PreflightFixture):
    """Honest host checks: dual-branch assert or a loud SKIP - never a fake pass."""

    def test_default_wiring_end_to_end(self) -> None:
        """Production wiring (real probe + real verify_image), no injected fakes."""
        tool_version = base_image_module.resolve_tool_version()
        image = self._write_image(tool_version=tool_version)
        config = IsolationConfig.from_env(
            self._kvm_env(image),
            data_root=self.tmp,
            capability_probe=check_host_capabilities,
        )
        result = preflight(config)
        if config.enabled:
            self.assertTrue(result.ok, result.reason)
            self.assertEqual(result.reason, "ok")
        else:
            self.assertFalse(result.ok)
            self.assertEqual(result.mode, "kvm")
            self.assertTrue(result.reason.startswith("isolation_unavailable:"), result.reason)

    def test_installed_base_image_on_a_real_kvm_host(self) -> None:
        caps = check_host_capabilities(DEFAULT_LIBVIRT_URI)
        if not caps.ok:
            self.skipTest(f"real KVM unavailable on this host: {caps.reason}")
        image = base_image_module.default_image_path()
        if not image.is_file():
            self.skipTest(f"no installed base image at {image}; nothing to preflight")
        config = IsolationConfig.from_env(
            self._kvm_env(image),
            data_root=self.tmp,
        )
        result = preflight(config)
        if result.reason == "isolation_unavailable:version_mismatch":
            self.skipTest("installed default image belongs to an older tool version")
        self.assertTrue(result.ok, result.reason)


if __name__ == "__main__":
    unittest.main()


class GuestChannelContractTest(_PreflightFixture):
    def test_desktop_channel_matches_verified_manifest_and_mismatch_is_refused(self):
        image = self._write_image()
        manifest = base_image_module.load_manifest(base_image_module.manifest_path_for(image))
        write_manifest(dataclasses.replace(manifest, vsock_port=40519), image)
        config = dataclasses.replace(self._kvm_config(image), vsock_port=40519)
        self.assertTrue(preflight(config, capability_probe=_probe_ok, tool_version=TEST_TOOL_VERSION).ok)
        refused = preflight(dataclasses.replace(config, vsock_port=5005), capability_probe=_probe_ok, tool_version=TEST_TOOL_VERSION)
        self.assertFalse(refused.ok)
        self.assertIn('guest_channel_mismatch', refused.reason)

    def test_old_manifest_without_port_keeps_gateway_default(self):
        import json
        image = self._write_image()
        path = base_image_module.manifest_path_for(image)
        payload = json.loads(path.read_text()); payload.pop('vsock_port')
        path.write_text(json.dumps(payload))
        self.assertEqual(base_image_module.load_manifest(path).vsock_port, 5005)
        self.assertTrue(preflight(self._kvm_config(image), capability_probe=_probe_ok, tool_version=TEST_TOOL_VERSION).ok)
