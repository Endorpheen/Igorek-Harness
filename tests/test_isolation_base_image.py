#!/usr/bin/env python3
"""Frozen guest base image: manifest round-trip, verification and --verify-only.

The image itself is never built here. All tests use a tiny fake ``base.qcow2``
plus a manifest produced by the module under test, so the verification logic is
exercised without virt-builder/debootstrap/KVM.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(
    os.environ.get("IGOREK_FACTORY_REPO", Path(__file__).resolve().parents[1])
)
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from factory.core.isolation import base_image  # noqa: E402

SCRIPT = REPO_ROOT / "factory" / "scripts" / "build_isolation_image.sh"
TOOL_VERSION = "0.1.750"
NEW_TOOL_VERSION = "0.1.751"


class BaseImageTestCase(unittest.TestCase):
    """Common fixture: a temporary fake image and a valid manifest."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="igorek_base_image_")
        self.dir = Path(self._tmp.name)
        self.image = self.dir / "base.qcow2"
        self.image.write_bytes(b"qcow2-fake-image-bytes" * 64)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def make_info(self, **overrides: object) -> "base_image.ImageBuildInfo":
        values: dict[str, object] = {
            "tool_version": TOOL_VERSION,
            "repo_commit": "deadbeef",
            "distro": "debian-13-minimal",
            "kernel": "linux-image-cloud-amd64",
            "builder": "virt-builder",
            "guest_user": "agent",
        }
        values.update(overrides)
        return base_image.ImageBuildInfo(**values)  # type: ignore[arg-type]

    def write_valid_manifest(self) -> "base_image.BaseImageManifest":
        manifest = base_image.build_manifest(self.image, self.make_info())
        base_image.write_manifest(manifest, self.image)
        return manifest

    def flip_image_byte(self) -> None:
        data = bytearray(self.image.read_bytes())
        data[0] ^= 0xFF
        self.image.write_bytes(bytes(data))


class ManifestRoundTripTests(BaseImageTestCase):
    def test_build_manifest_records_sha256_size_and_metadata(self) -> None:
        manifest = base_image.build_manifest(
            self.image, self.make_info(), built_at="2026-09-22T10:00:00Z"
        )
        expected_sha = hashlib.sha256(self.image.read_bytes()).hexdigest()
        self.assertEqual(manifest.sha256, expected_sha)
        self.assertEqual(manifest.sha256, base_image.compute_sha256(self.image))
        self.assertEqual(manifest.size_bytes, self.image.stat().st_size)
        self.assertEqual(manifest.tool_version, TOOL_VERSION)
        self.assertEqual(manifest.repo_commit, "deadbeef")
        self.assertEqual(manifest.built_at, "2026-09-22T10:00:00Z")
        self.assertEqual(manifest.schema_version, base_image.MANIFEST_SCHEMA_VERSION)

    def test_manifest_round_trips_through_disk(self) -> None:
        original = self.write_valid_manifest()
        loaded = base_image.load_manifest(base_image.manifest_path_for(self.image))
        self.assertEqual(loaded, original)
        self.assertEqual(
            base_image.manifest_path_for(self.image).name,
            "base.qcow2.manifest.json",
        )

    def test_manifest_file_is_json_with_required_keys(self) -> None:
        self.write_valid_manifest()
        payload = json.loads(
            base_image.manifest_path_for(self.image).read_text(encoding="utf-8")
        )
        for key in (
            "schema_version",
            "image_name",
            "sha256",
            "size_bytes",
            "built_at",
            "tool_version",
            "repo_commit",
            "distro",
            "kernel",
            "builder",
            "guest_user",
            "handshake_version",
            "supply_chain",
        ):
            self.assertIn(key, payload, f"manifest is missing {key}")

    def test_manifest_records_machine_readable_guest_handshake(self) -> None:
        manifest = self.write_valid_manifest()
        self.assertIsInstance(manifest.handshake_version, str)
        self.assertEqual(
            manifest.handshake_version,
            base_image.expected_guest_version(TOOL_VERSION),
        )
        loaded = base_image.load_manifest(base_image.manifest_path_for(self.image))
        self.assertEqual(loaded.handshake_version, manifest.handshake_version)

    def test_supply_chain_note_describes_cve_refresh_and_rebuild(self) -> None:
        manifest = self.write_valid_manifest()
        note = manifest.supply_chain
        self.assertGreater(len(note), 80)
        self.assertIn("CVE", note)
        self.assertIn("rebuild", note.lower())
        self.assertIn("tool", note.lower())


class ManifestRejectionTests(BaseImageTestCase):
    def test_sha256_mismatch_is_rejected(self) -> None:
        self.write_valid_manifest()
        self.flip_image_byte()
        result = base_image.verify_image(self.image)
        self.assertFalse(result.ok)
        self.assertEqual(result.code, "checksum_mismatch")
        self.assertIn("image checksum mismatch", result.reason)
        self.assertIsNotNone(result.manifest)
        self.assertEqual(result.manifest.tool_version, TOOL_VERSION)

    def test_stale_manifest_from_previous_tool_version_is_rejected(self) -> None:
        self.write_valid_manifest()
        result = base_image.verify_image(
            self.image, expected_tool_version=NEW_TOOL_VERSION
        )
        self.assertFalse(result.ok)
        self.assertEqual(result.code, "version_mismatch")
        self.assertIn(NEW_TOOL_VERSION, result.reason)
        self.assertIn(TOOL_VERSION, result.reason)

    def test_guest_handshake_of_stale_image_is_rejected(self) -> None:
        manifest = self.write_valid_manifest()
        self.assertFalse(
            base_image.guest_handshake_matches(
                manifest.handshake_version, NEW_TOOL_VERSION
            )
        )
        self.assertTrue(
            base_image.guest_handshake_matches(manifest.handshake_version, TOOL_VERSION)
        )

    def test_missing_image_is_rejected(self) -> None:
        self.image.unlink()
        result = base_image.verify_image(self.image)
        self.assertFalse(result.ok)
        self.assertEqual(result.code, "missing_image")

    def test_missing_manifest_is_rejected(self) -> None:
        result = base_image.verify_image(self.image)
        self.assertFalse(result.ok)
        self.assertEqual(result.code, "missing_manifest")

    def test_broken_manifest_json_is_rejected(self) -> None:
        base_image.manifest_path_for(self.image).write_text(
            "{not-json", encoding="utf-8"
        )
        result = base_image.verify_image(self.image)
        self.assertFalse(result.ok)
        self.assertEqual(result.code, "invalid_manifest")

    def test_unknown_manifest_schema_is_rejected(self) -> None:
        self.write_valid_manifest()
        path = base_image.manifest_path_for(self.image)
        payload = json.loads(path.read_text(encoding="utf-8"))
        payload["schema_version"] = base_image.MANIFEST_SCHEMA_VERSION + 1
        path.write_text(json.dumps(payload), encoding="utf-8")
        result = base_image.verify_image(self.image)
        self.assertFalse(result.ok)
        self.assertEqual(result.code, "invalid_manifest")

    def test_valid_image_passes_with_expected_version(self) -> None:
        manifest = self.write_valid_manifest()
        result = base_image.verify_image(
            self.image, expected_tool_version=TOOL_VERSION
        )
        self.assertTrue(result.ok, result.reason)
        self.assertEqual(result.code, "ok")
        self.assertEqual(result.sha256, manifest.sha256)
        self.assertEqual(result.tool_version, TOOL_VERSION)
        self.assertIn("sha256", result.to_dict())


class GuestBootstrapScriptTests(unittest.TestCase):
    def test_built_image_configures_dhcp_for_the_virtio_interface(self) -> None:
        script = SCRIPT.read_text(encoding="utf-8")
        self.assertIn("Name=en*", script)
        self.assertIn("DHCP=ipv4", script)
        self.assertIn("systemctl enable systemd-networkd.service", script)
        self.assertIn('INCLUDE_WORKTREE="${IGOREK_ISOLATION_INCLUDE_WORKTREE:-0}"', script)
        self.assertIn('diff --binary "$REPO_COMMIT" -- factory/ pyproject.toml uv.lock', script)
        self.assertIn("systemd-networkd.service.d/igorek-egress.conf", script)
        self.assertIn("After=igorek-egress-guard.service", script)

    def test_built_image_starts_runner_only_after_mount_and_egress_guard(self) -> None:
        script = SCRIPT.read_text(encoding="utf-8")
        self.assertIn("workspace /workspace virtiofs defaults,nofail", script)
        self.assertIn("Requires=workspace.mount igorek-egress-guard.service", script)
        self.assertIn("ExecStartPre=/usr/local/bin/igorek-guest-bwrap-probe", script)
        self.assertIn("Environment=IGOREK_ISOLATION_NFT_BIN=/usr/local/bin/igorek-nft", script)
        self.assertIn("setcap cap_net_admin+ep /usr/local/bin/igorek-nft", script)
        self.assertNotIn("AmbientCapabilities=CAP_NET_ADMIN", script)
        self.assertNotIn("CapabilityBoundingSet=CAP_NET_ADMIN", script)
        self.assertIn("igorek-factory guest-runner --vsock-cid 2 --vsock-port 5005", script)
        self.assertIn("multi-user.target.wants/igorek-guest-runner.service", script)
        # Repo-relative MCP commands need the source tree as the runner's cwd.
        self.assertIn("WorkingDirectory=${IGOREK_GUEST_REPO_DIR}", script)


class GuestVersionContractTests(unittest.TestCase):
    def test_expected_guest_version_is_prefixed_and_versioned(self) -> None:
        self.assertEqual(
            base_image.expected_guest_version("1.2.3"),
            f"{base_image.GUEST_HANDSHAKE_ID}/1.2.3",
        )

    def test_expected_guest_version_defaults_to_host_tool_version(self) -> None:
        resolved = base_image.expected_guest_version()
        self.assertRegex(resolved, r"^" + base_image.GUEST_HANDSHAKE_ID + r"/\S+$")

    def test_handshake_compare_tolerates_whitespace_only(self) -> None:
        expected = base_image.expected_guest_version(TOOL_VERSION)
        self.assertTrue(base_image.guest_handshake_matches(f"  {expected}\n", TOOL_VERSION))
        self.assertFalse(base_image.guest_handshake_matches(None, TOOL_VERSION))
        self.assertFalse(
            base_image.guest_handshake_matches(expected, NEW_TOOL_VERSION)
        )
        self.assertFalse(base_image.guest_handshake_matches("", TOOL_VERSION))


class ScriptVerifyOnlyTests(BaseImageTestCase):
    """The shell entrypoint must never print PASS without a verified image."""

    def run_script(
        self, *args: str, env: dict[str, str] | None = None
    ) -> subprocess.CompletedProcess:
        proc_env = os.environ.copy()
        proc_env["IGOREK_ISOLATION_PYTHON"] = sys.executable
        if env:
            proc_env.update(env)
        return subprocess.run(
            ["bash", str(SCRIPT), *args],
            capture_output=True,
            text=True,
            timeout=120,
            env=proc_env,
        )

    def combined(self, proc: subprocess.CompletedProcess) -> str:
        return proc.stdout + proc.stderr

    def test_verify_only_with_valid_image_exits_zero(self) -> None:
        manifest = self.write_valid_manifest()
        proc = self.run_script("--verify-only", "--image", str(self.image))
        self.assertEqual(proc.returncode, 0, self.combined(proc))
        self.assertIn("PASS", proc.stdout)
        self.assertIn(manifest.sha256, proc.stdout)
        self.assertIn(TOOL_VERSION, proc.stdout)

    def test_verify_only_with_flipped_byte_exits_nonzero(self) -> None:
        self.write_valid_manifest()
        self.flip_image_byte()
        proc = self.run_script("--verify-only", "--image", str(self.image))
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("image checksum mismatch", self.combined(proc))
        self.assertNotIn("PASS", self.combined(proc))

    def test_verify_only_with_missing_image_exits_nonzero(self) -> None:
        missing = self.dir / "absent.qcow2"
        proc = self.run_script("--verify-only", "--image", str(missing))
        self.assertNotEqual(proc.returncode, 0)
        self.assertNotIn("PASS", self.combined(proc))

    def test_verify_only_with_missing_manifest_exits_nonzero(self) -> None:
        proc = self.run_script("--verify-only", "--image", str(self.image))
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("manifest", self.combined(proc).lower())
        self.assertNotIn("PASS", self.combined(proc))

    def test_verify_only_rejects_stale_manifest_version(self) -> None:
        self.write_valid_manifest()
        proc = self.run_script(
            "--verify-only",
            "--image",
            str(self.image),
            "--expected-tool-version",
            NEW_TOOL_VERSION,
        )
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("version mismatch", self.combined(proc))
        self.assertNotIn("PASS", self.combined(proc))

    def test_unknown_argument_exits_usage_error_without_pass(self) -> None:
        proc = self.run_script("--not-a-flag", "--image", str(self.image))
        self.assertNotEqual(proc.returncode, 0)
        self.assertNotIn("PASS", self.combined(proc))

    def test_dry_run_prints_no_success_marker(self) -> None:
        proc = self.run_script(
            "--dry-run", "--image", str(self.dir / "build-target.qcow2")
        )
        self.assertEqual(proc.returncode, 0, self.combined(proc))
        self.assertNotIn("PASS", self.combined(proc))


class ScriptSkipWhenToolsMissingTests(BaseImageTestCase):
    """No build tooling => honest SKIP with exit 0, never a fake PASS."""

    def test_build_path_skips_when_host_tools_are_missing(self) -> None:
        tools = self.dir / "tools"
        tools.mkdir()
        dirname = shutil.which("dirname")
        bash = shutil.which("bash")
        self.assertIsNotNone(dirname)
        self.assertIsNotNone(bash)
        (tools / "dirname").symlink_to(dirname)
        proc = subprocess.run(
            [
                bash,
                str(SCRIPT),
                "--image",
                str(self.dir / "build-target.qcow2"),
            ],
            capture_output=True,
            text=True,
            timeout=120,
            env={**os.environ, "PATH": str(tools), "IGOREK_ISOLATION_PYTHON": sys.executable},
        )
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("SKIP:", proc.stdout)
        self.assertNotIn("PASS", proc.stdout + proc.stderr)


if __name__ == "__main__":
    unittest.main()
