"""Manifest for the frozen guest base image: build, verify, boot handshake.

The KVM guest base image is a build artifact, so it is identified by content,
not by path: every image next to which a manifest file lives can be verified
byte-for-byte before a single VM is started. ``base.qcow2.manifest.json``
records the image ``sha256``, the tool/repo version it was built from, the
build date and an explicit supply-chain note (kernel/rootfs CVE refresh
cadence and the rebuild trigger on a host tool version change).

Verification is deliberately available without any virtualization host: it is
pure file hashing plus JSON validation, which is why it is unit-tested here
and called by ``factory/scripts/build_isolation_image.sh --verify-only``.

Boot handshake contract (consumed by the KVM preflight in a later todo): the
guest reports the string returned by :func:`expected_guest_version` and the
host compares it with :func:`guest_handshake_matches`. A mismatch means the
image predates the current host tool version and must be rebuilt — the same
condition ``verify_image(expected_tool_version=...)`` refuses.

No secrets are ever part of the manifest: it contains no credentials, no host
paths and no data-directory content.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from dataclasses import asdict, dataclass, fields
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Final

from ..runtime import get_data_root
from .guest_bwrap import (
    BWRAP_APPARMOR_PROFILE,
    USERNS_POLICY,
    USERNS_POLICY_MARKER,
)

__all__ = [
    "MANIFEST_SCHEMA_VERSION",
    "MANIFEST_SUFFIX",
    "DEFAULT_IMAGE_NAME",
    "GUEST_HANDSHAKE_ID",
    "SUPPLY_CHAIN_NOTE",
    "ManifestError",
    "ImageBuildInfo",
    "BaseImageManifest",
    "VerifyResult",
    "compute_sha256",
    "manifest_path_for",
    "default_image_path",
    "resolve_tool_version",
    "build_manifest",
    "write_manifest",
    "load_manifest",
    "verify_image",
    "expected_guest_version",
    "guest_handshake_matches",
]

MANIFEST_SCHEMA_VERSION: Final = 1
MANIFEST_SUFFIX: Final = ".manifest.json"
DEFAULT_IMAGE_NAME: Final = "base.qcow2"
GUEST_HANDSHAKE_ID: Final = "igorek-isolation-guest"
HASH_CHUNK_BYTES: Final = 1024 * 1024

SUPPLY_CHAIN_NOTE: Final = (
    "Supply-chain policy for the frozen guest base image. Kernel and rootfs "
    "CVEs are cleared only by rebuilding, never by patching the image in "
    "place: the recipe is re-run (virt-builder/debootstrap against the pinned "
    "Debian suite plus the current Debian security archive) at least monthly "
    "and within 7 days of any Debian security advisory that covers the pinned "
    "suite or the guest kernel package. Every rebuild bumps the recorded build "
    "date and recomputes sha256. A rebuild is mandatory whenever the host tool "
    "version changes: pass the new value to build_isolation_image.sh "
    "--tool-version, rebuild, and let the boot handshake compare "
    "igorek-isolation-guest/<tool_version>. An image whose manifest tool "
    "version differs from the host expectation is rejected by "
    "verify_image(expected_tool_version=...) and must not be booted."
)

_REQUIRED_NON_EMPTY_FIELDS: Final = (
    "image_name",
    "built_at",
    "tool_version",
    "distro",
    "kernel",
    "builder",
    "guest_user",
    "handshake_version",
    "supply_chain",
    "userns_policy",
    "apparmor_profile",
)


class ManifestError(ValueError):
    """Typed manifest failure; ``code``/``detail`` are stable and loggable."""

    def __init__(self, code: str, detail: str = "") -> None:
        self.code = code
        self.detail = detail
        message = f"{code}: {detail}" if detail else code
        super().__init__(message)


@dataclass(frozen=True, slots=True)
class ImageBuildInfo:
    """Inputs recorded in the manifest when the recipe finishes an image."""

    tool_version: str
    repo_commit: str = ""
    distro: str = "debian-13-minimal"
    kernel: str = "linux-image-cloud-amd64"
    builder: str = "virt-builder"
    guest_user: str = "agent"
    userns_policy: str = USERNS_POLICY
    apparmor_profile: str = BWRAP_APPARMOR_PROFILE


@dataclass(frozen=True, slots=True)
class BaseImageManifest:
    """Content identity + provenance of one built ``base.qcow2``."""

    schema_version: int
    image_name: str
    sha256: str
    size_bytes: int
    built_at: str
    tool_version: str
    repo_commit: str
    distro: str
    kernel: str
    builder: str
    guest_user: str
    handshake_version: str
    supply_chain: str
    userns_policy: str
    apparmor_profile: str
    vsock_port: int = 5005

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "BaseImageManifest":
        if not isinstance(payload, dict):
            raise ManifestError("invalid_manifest", "manifest is not a JSON object")
        schema = payload.get("schema_version")
        if schema != MANIFEST_SCHEMA_VERSION:
            raise ManifestError(
                "invalid_manifest",
                f"unsupported schema_version {schema!r} "
                f"(expected {MANIFEST_SCHEMA_VERSION})",
            )
        values: dict[str, Any] = {}
        for field in fields(cls):
            if field.name == "vsock_port" and field.name not in payload:
                values[field.name] = 5005
                continue
            if field.name not in payload:
                raise ManifestError(
                    "invalid_manifest", f"missing field {field.name!r}"
                )
            values[field.name] = payload[field.name]
        port = values["vsock_port"]
        if type(port) is not int or not 1024 <= port <= 65535:
            raise ManifestError("invalid_manifest", "invalid vsock_port")
        size_bytes = values["size_bytes"]
        if isinstance(size_bytes, bool) or not isinstance(size_bytes, int):
            raise ManifestError("invalid_manifest", "size_bytes must be an integer")
        if size_bytes < 0:
            raise ManifestError("invalid_manifest", "size_bytes must be >= 0")
        for name, value in values.items():
            if name in {"schema_version", "size_bytes", "vsock_port"}:
                continue
            if not isinstance(value, str):
                raise ManifestError("invalid_manifest", f"{name} must be a string")
        sha256 = values["sha256"]
        if len(sha256) != 64 or any(ch not in "0123456789abcdef" for ch in sha256):
            raise ManifestError("invalid_manifest", "sha256 must be 64 hex chars")
        for name in _REQUIRED_NON_EMPTY_FIELDS:
            if not values[name].strip():
                raise ManifestError("invalid_manifest", f"{name} must not be empty")
        if USERNS_POLICY_MARKER not in values["userns_policy"]:
            raise ManifestError(
                "invalid_manifest",
                f"userns_policy must contain {USERNS_POLICY_MARKER!r}, "
                f"got {values['userns_policy']!r}",
            )
        if values["apparmor_profile"] != BWRAP_APPARMOR_PROFILE:
            raise ManifestError(
                "invalid_manifest",
                f"apparmor_profile must be {BWRAP_APPARMOR_PROFILE!r}, "
                f"got {values['apparmor_profile']!r}",
            )
        return cls(**values)


@dataclass(frozen=True, slots=True)
class VerifyResult:
    """Structured outcome of :func:`verify_image`."""

    ok: bool
    code: str
    reason: str
    image_path: str
    manifest: BaseImageManifest | None = None
    sha256: str = ""
    tool_version: str = ""
    built_at: str = ""
    userns_policy: str = ""
    apparmor_profile: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "code": self.code,
            "reason": self.reason,
            "image_path": self.image_path,
            "sha256": self.sha256,
            "tool_version": self.tool_version,
            "built_at": self.built_at,
            "userns_policy": self.userns_policy,
            "apparmor_profile": self.apparmor_profile,
            "manifest": self.manifest.to_dict() if self.manifest else None,
        }


def compute_sha256(image_path: str | os.PathLike[str]) -> str:
    """Streaming sha256 of the image file (constant memory)."""

    digest = hashlib.sha256()
    with open(image_path, "rb") as handle:
        while True:
            chunk = handle.read(HASH_CHUNK_BYTES)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def manifest_path_for(image_path: str | os.PathLike[str]) -> Path:
    """``<image>.manifest.json`` — the manifest always lives next to the image."""

    return Path(str(image_path) + MANIFEST_SUFFIX)


def default_image_path() -> Path:
    """Default image location: ``<data_root>/isolation/images/base.qcow2``."""

    override = os.environ.get("IGOREK_ISOLATION_BASE_IMAGE", "").strip()
    if override:
        return Path(override).expanduser()
    return get_data_root() / "isolation" / "images" / DEFAULT_IMAGE_NAME


def resolve_tool_version() -> str:
    """Installed tool version, falling back to the repo's pyproject and a stub."""

    try:
        from importlib.metadata import PackageNotFoundError, version

        try:
            return version("igorek-agent-factory")
        except PackageNotFoundError:
            pass
    except Exception:  # pragma: no cover - importlib.metadata always present
        pass
    pyproject = Path(__file__).resolve().parents[3] / "pyproject.toml"
    try:
        for line in pyproject.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if stripped.startswith("version = "):
                return stripped.split("=", 1)[1].strip().strip('"').strip("'")
    except OSError:
        pass
    return "0.0.0-unknown"


def expected_guest_version(tool_version: str | None = None) -> str:
    """Version string the guest must report for the boot handshake to pass."""

    resolved = tool_version.strip() if isinstance(tool_version, str) and tool_version.strip() else resolve_tool_version()
    return f"{GUEST_HANDSHAKE_ID}/{resolved}"


def guest_handshake_matches(
    reported: str | None, tool_version: str | None = None
) -> bool:
    """True when the guest-reported version equals the host expectation."""

    if not isinstance(reported, str):
        return False
    return reported.strip() == expected_guest_version(tool_version)


def build_manifest(
    image_path: str | os.PathLike[str],
    info: ImageBuildInfo,
    *,
    built_at: str | None = None,
) -> BaseImageManifest:
    """Hash an existing image and produce its manifest (does not write it)."""

    path = Path(image_path)
    if not path.is_file():
        raise ManifestError("missing_image", f"base image missing: {path}")
    if not info.tool_version.strip():
        raise ManifestError("invalid_manifest", "tool_version must not be empty")
    timestamp = built_at or datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return BaseImageManifest(
        schema_version=MANIFEST_SCHEMA_VERSION,
        image_name=path.name,
        sha256=compute_sha256(path),
        size_bytes=path.stat().st_size,
        built_at=timestamp,
        tool_version=info.tool_version.strip(),
        repo_commit=info.repo_commit.strip(),
        distro=info.distro.strip(),
        kernel=info.kernel.strip(),
        builder=info.builder.strip(),
        guest_user=info.guest_user.strip(),
        handshake_version=expected_guest_version(info.tool_version),
        supply_chain=SUPPLY_CHAIN_NOTE,
        userns_policy=info.userns_policy.strip(),
        apparmor_profile=info.apparmor_profile.strip(),
    )


def write_manifest(
    manifest: BaseImageManifest,
    image_path: str | os.PathLike[str],
) -> Path:
    """Write ``<image>.manifest.json`` atomically; return the written path."""

    target = manifest_path_for(image_path)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(manifest.to_dict(), indent=2, sort_keys=True) + "\n"
    fd, tmp_name = tempfile.mkstemp(
        prefix=target.name + ".", suffix=".tmp", dir=str(target.parent)
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(payload)
        os.replace(tmp_name, target)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise
    return target


def load_manifest(path: str | os.PathLike[str]) -> BaseImageManifest:
    """Parse and fully validate a manifest file."""

    manifest_path = Path(path)
    try:
        raw = manifest_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ManifestError("invalid_manifest", f"cannot read {manifest_path}: {exc}") from exc
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ManifestError("invalid_manifest", f"{manifest_path}: {exc}") from exc
    return BaseImageManifest.from_dict(payload)


def verify_image(
    image_path: str | os.PathLike[str],
    *,
    manifest_path: str | os.PathLike[str] | None = None,
    expected_tool_version: str | None = None,
) -> VerifyResult:
    """Refuse a missing/corrupted/stale image; never returns ok for one."""

    path = Path(image_path)
    if not path.is_file():
        return VerifyResult(
            ok=False,
            code="missing_image",
            reason=f"base image missing: {path}",
            image_path=str(path),
        )
    manifest_file = (
        Path(manifest_path) if manifest_path is not None else manifest_path_for(path)
    )
    if not manifest_file.is_file():
        return VerifyResult(
            ok=False,
            code="missing_manifest",
            reason=f"image manifest missing: {manifest_file}",
            image_path=str(path),
        )
    try:
        manifest = load_manifest(manifest_file)
    except ManifestError as exc:
        return VerifyResult(
            ok=False,
            code="invalid_manifest",
            reason=f"image manifest invalid: {exc}",
            image_path=str(path),
        )
    actual_sha = compute_sha256(path)
    if actual_sha != manifest.sha256:
        return VerifyResult(
            ok=False,
            code="checksum_mismatch",
            reason=(
                "image checksum mismatch: "
                f"actual={actual_sha} expected={manifest.sha256}"
            ),
            image_path=str(path),
            manifest=manifest,
        )
    if expected_tool_version is not None and manifest.tool_version != expected_tool_version:
        return VerifyResult(
            ok=False,
            code="version_mismatch",
            reason=(
                "tool version mismatch: "
                f"host expects {expected_tool_version}, "
                f"image was built with {manifest.tool_version}"
            ),
            image_path=str(path),
            manifest=manifest,
        )
    return VerifyResult(
        ok=True,
        code="ok",
        reason=(
            f"verified base image {path} sha256={actual_sha} "
            f"tool_version={manifest.tool_version} built_at={manifest.built_at} "
            f"userns_policy={manifest.userns_policy} "
            f"apparmor_profile={manifest.apparmor_profile}"
        ),
        image_path=str(path),
        manifest=manifest,
        sha256=actual_sha,
        tool_version=manifest.tool_version,
        built_at=manifest.built_at,
        userns_policy=manifest.userns_policy,
        apparmor_profile=manifest.apparmor_profile,
    )

