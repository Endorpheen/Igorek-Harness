"""Isolation feature flag and host-capability preflight for per-session KVM VMs.

Host-side only. Resolves the ``IGOREK_ISOLATION_*`` environment into an
immutable :class:`IsolationConfig` and answers whether this host can actually
run a microVM via :func:`check_host_capabilities`.

Fail-closed rule: a requested ``kvm`` mode never silently degrades to ``off``.
When anything required is missing, ``IsolationConfig.enabled`` is ``False`` and
``IsolationConfig.reason`` names the structural cause. Only an explicit ``off``
(or an absent/unknown flag, which resolves to ``off`` with a warning) yields an
inactive config and no preflight is performed.
"""

from __future__ import annotations

import importlib
import logging
import os
import shutil
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

from ..runtime import get_data_root
from .limits import memory_hard_limit_mib

logger = logging.getLogger(__name__)

MODE_OFF = "off"
MODE_KVM = "kvm"
VALID_MODES: frozenset[str] = frozenset({MODE_OFF, MODE_KVM})

ENV_MODE = "IGOREK_ISOLATION_MODE"
ENV_BASE_IMAGE = "IGOREK_ISOLATION_BASE_IMAGE"
ENV_OVERLAY_DIR = "IGOREK_ISOLATION_OVERLAY_DIR"
ENV_LIBVIRT_URI = "IGOREK_ISOLATION_LIBVIRT_URI"
ENV_VSOCK_PORT = "IGOREK_ISOLATION_VSOCK_PORT"
ENV_IDLE_TTL_S = "IGOREK_ISOLATION_IDLE_TTL_S"
ENV_MAX_VMS = "IGOREK_ISOLATION_MAX_VMS"
ENV_SESSION_VCPUS = "IGOREK_ISOLATION_SESSION_VCPUS"
ENV_SESSION_MEMORY_MB = "IGOREK_ISOLATION_SESSION_MEMORY_MB"
ENV_SESSION_DISK_GB = "IGOREK_ISOLATION_SESSION_DISK_GB"
ENV_HOST_MEMORY_MB = "IGOREK_ISOLATION_HOST_MEMORY_MB"
ENV_HOST_DISK_GB = "IGOREK_ISOLATION_HOST_DISK_GB"
ENV_ADMISSION_WAIT_S = "IGOREK_ISOLATION_ADMISSION_WAIT_S"

DEFAULT_LIBVIRT_URI = "qemu:///session"
# AF_VSOCK ports below 1024 are privileged, so the default stays well above.
DEFAULT_VSOCK_PORT = 5005
# Keep one VM warm for a session across long gaps between mobile messages.
# Operators may still choose a shorter resource policy explicitly.
DEFAULT_IDLE_TTL_S = 86400
DEFAULT_MAX_VMS = 4
DEFAULT_SESSION_VCPUS = 2
# 2048 MiB is below the practical floor for Android/Gradle + JVM in the guest
# and coincided with frozen-guest turns; 6144 leaves working-set headroom.
DEFAULT_SESSION_MEMORY_MB = 6144
DEFAULT_SESSION_DISK_GB = 8
# How long a session waits for capacity before the wait is reported as a timed
# out queue entry (plan todo 33): long enough for a sibling VM to finish a turn.
DEFAULT_ADMISSION_WAIT_S = 600

MIN_VSOCK_PORT = 1024
MAX_VSOCK_PORT = 65535

KVM_DEVICE_PATH = Path("/dev/kvm")
VIRTIOFSD_FALLBACK_PATH = Path("/usr/libexec/virtiofsd")


@dataclass(frozen=True, slots=True)
class _IntSetting:
    env: str
    default: int
    minimum: int
    maximum: int | None = None


_INT_SETTINGS: tuple[_IntSetting, ...] = (
    _IntSetting(ENV_VSOCK_PORT, DEFAULT_VSOCK_PORT, MIN_VSOCK_PORT, MAX_VSOCK_PORT),
    _IntSetting(ENV_IDLE_TTL_S, DEFAULT_IDLE_TTL_S, 1),
    _IntSetting(ENV_MAX_VMS, DEFAULT_MAX_VMS, 1),
    _IntSetting(ENV_SESSION_VCPUS, DEFAULT_SESSION_VCPUS, 1),
    _IntSetting(ENV_SESSION_MEMORY_MB, DEFAULT_SESSION_MEMORY_MB, 128),
    _IntSetting(ENV_SESSION_DISK_GB, DEFAULT_SESSION_DISK_GB, 1),
    # 0 means "derive the aggregate admission budget from MAX_VMS x per-session
    # limits"; any positive value must keep at least one VM admissible.
    _IntSetting(ENV_HOST_MEMORY_MB, 0, 0),
    _IntSetting(ENV_HOST_DISK_GB, 0, 0),
    _IntSetting(ENV_ADMISSION_WAIT_S, DEFAULT_ADMISSION_WAIT_S, 0),
)


@dataclass(frozen=True, slots=True)
class HostCapabilities:
    """Structured result of the host preflight.

    ``reason`` is the first unmet requirement in a deterministic order
    (kvm -> libvirt -> virtiofsd -> qemu-img), or ``None`` when ``ok``.
    """

    kvm_present: bool
    libvirt_reachable: bool
    virtiofsd_present: bool
    qemu_img_present: bool
    reason: str | None = None

    @property
    def ok(self) -> bool:
        return (
            self.kvm_present
            and self.libvirt_reachable
            and self.virtiofsd_present
            and self.qemu_img_present
        )


CapabilityProbe = Callable[[str], HostCapabilities]


@dataclass(frozen=True, slots=True)
class IsolationConfig:
    """Resolved isolation settings for the host runtime.

    ``enabled`` is ``True`` only for a fully satisfied ``kvm`` request. In every
    other case ``reason`` explains why (``mode_off``, ``unknown_mode:*``,
    ``base_image_missing``, ``invalid_config:<ENV>``,
    ``capability_unavailable:<detail>``).

    ``host_memory_mb``/``host_disk_gb`` are the aggregate admission budget
    (plan todo 33); ``0`` means "derive from ``max_vms`` and the per-session
    limits". ``admission_wait_s`` bounds how long a session may queue for
    capacity before its wait is reported as timed out.
    """

    mode: str
    enabled: bool
    reason: str | None
    base_image: Path | None
    overlay_dir: Path
    libvirt_uri: str
    vsock_port: int
    idle_ttl_s: int
    max_vms: int
    session_vcpus: int
    session_memory_mb: int
    session_disk_gb: int
    host_memory_mb: int = 0
    host_disk_gb: int = 0
    admission_wait_s: int = DEFAULT_ADMISSION_WAIT_S

    @classmethod
    def from_env(
        cls,
        env: Mapping[str, str] | None = None,
        *,
        data_root: Path | None = None,
        capability_probe: CapabilityProbe | None = None,
    ) -> IsolationConfig:
        source = os.environ if env is None else env
        root = data_root if data_root is not None else get_data_root()

        mode, mode_reason = _resolve_mode(source)
        base_image = _resolve_base_image(source)
        overlay_dir = _resolve_overlay_dir(source, root)
        libvirt_uri = _resolve_libvirt_uri(source)
        values, invalid_settings = _parse_int_settings(source)
        for name in invalid_settings:
            logger.warning(
                "Invalid %s=%r; keeping the default value", name, source.get(name)
            )

        def build(enabled: bool, reason: str | None) -> IsolationConfig:
            return cls(
                mode=mode,
                enabled=enabled,
                reason=reason,
                base_image=base_image,
                overlay_dir=overlay_dir,
                libvirt_uri=libvirt_uri,
                vsock_port=values[ENV_VSOCK_PORT],
                idle_ttl_s=values[ENV_IDLE_TTL_S],
                max_vms=values[ENV_MAX_VMS],
                session_vcpus=values[ENV_SESSION_VCPUS],
                session_memory_mb=values[ENV_SESSION_MEMORY_MB],
                session_disk_gb=values[ENV_SESSION_DISK_GB],
                host_memory_mb=values[ENV_HOST_MEMORY_MB],
                host_disk_gb=values[ENV_HOST_DISK_GB],
                admission_wait_s=values[ENV_ADMISSION_WAIT_S],
            )

        if mode == MODE_OFF:
            return build(False, mode_reason)
        if base_image is None or not base_image.is_file():
            return build(False, "base_image_missing")
        if invalid_settings:
            return build(False, f"invalid_config:{invalid_settings[0]}")
        quota_error = _admission_quota_error(values)
        if quota_error is not None:
            return build(False, f"invalid_config:{quota_error}")

        probe = capability_probe if capability_probe is not None else check_host_capabilities
        capabilities = probe(libvirt_uri)
        if not capabilities.ok:
            return build(False, f"capability_unavailable:{capabilities.reason or 'unknown'}")
        return build(True, None)


def _admission_quota_error(values: Mapping[str, int]) -> str | None:
    """The env name of an aggregate budget that cannot admit a single VM."""
    if 0 < values[ENV_HOST_MEMORY_MB] < memory_hard_limit_mib(values[ENV_SESSION_MEMORY_MB]):
        return ENV_HOST_MEMORY_MB
    if 0 < values[ENV_HOST_DISK_GB] < values[ENV_SESSION_DISK_GB]:
        return ENV_HOST_DISK_GB
    return None


def _resolve_mode(source: Mapping[str, str]) -> tuple[str, str]:
    raw = source.get(ENV_MODE)
    if raw is None:
        return MODE_OFF, "mode_off"
    candidate = str(raw).strip().lower()
    if candidate in VALID_MODES:
        return candidate, "mode_off"
    logger.warning("Unknown %s=%r; isolation stays off", ENV_MODE, raw)
    return MODE_OFF, f"unknown_mode:{candidate or '<empty>'}"


def _resolve_base_image(source: Mapping[str, str]) -> Path | None:
    raw = source.get(ENV_BASE_IMAGE)
    if raw is None:
        return None
    text = str(raw).strip()
    if not text:
        return None
    return Path(text).expanduser()


def _resolve_overlay_dir(source: Mapping[str, str], root: Path) -> Path:
    raw = source.get(ENV_OVERLAY_DIR)
    text = str(raw).strip() if raw is not None else ""
    if text:
        return Path(text).expanduser()
    return root / "isolation" / "overlays"


def _resolve_libvirt_uri(source: Mapping[str, str]) -> str:
    raw = source.get(ENV_LIBVIRT_URI)
    text = str(raw).strip() if raw is not None else ""
    return text or DEFAULT_LIBVIRT_URI


def _parse_int_settings(source: Mapping[str, str]) -> tuple[dict[str, int], list[str]]:
    values: dict[str, int] = {}
    invalid: list[str] = []
    for setting in _INT_SETTINGS:
        raw = source.get(setting.env)
        if raw is None or not str(raw).strip():
            values[setting.env] = setting.default
            continue
        try:
            parsed = int(str(raw).strip(), 10)
        except ValueError:
            parsed = setting.default
            invalid.append(setting.env)
        else:
            too_small = parsed < setting.minimum
            too_large = setting.maximum is not None and parsed > setting.maximum
            if too_small or too_large:
                invalid.append(setting.env)
                parsed = setting.default
        values[setting.env] = parsed
    return values, invalid


def check_host_capabilities(libvirt_uri: str = DEFAULT_LIBVIRT_URI) -> HostCapabilities:
    """Probe the host for everything a per-session KVM microVM needs."""
    kvm_present = _kvm_device_available()
    libvirt_reachable = _libvirt_reachable(libvirt_uri)
    virtiofsd_present = _virtiofsd_available()
    qemu_img_present = _qemu_img_available()

    reason: str | None = None
    if not kvm_present:
        reason = "kvm_missing"
    elif not libvirt_reachable:
        reason = "libvirt_unreachable"
    elif not virtiofsd_present:
        reason = "virtiofsd_missing"
    elif not qemu_img_present:
        reason = "qemu_img_missing"

    return HostCapabilities(
        kvm_present=kvm_present,
        libvirt_reachable=libvirt_reachable,
        virtiofsd_present=virtiofsd_present,
        qemu_img_present=qemu_img_present,
        reason=reason,
    )


def _kvm_device_available() -> bool:
    return KVM_DEVICE_PATH.exists() and os.access(KVM_DEVICE_PATH, os.R_OK | os.W_OK)


def _binary_available(name: str) -> bool:
    return shutil.which(name) is not None


def _virtiofsd_available() -> bool:
    return _binary_available("virtiofsd") or VIRTIOFSD_FALLBACK_PATH.exists()


def _qemu_img_available() -> bool:
    return _binary_available("qemu-img")


def _libvirt_reachable(libvirt_uri: str) -> bool:
    """Best-effort connectivity probe; libvirt bindings are optional."""
    try:
        libvirt = importlib.import_module("libvirt")
    except ImportError:
        return False
    try:
        connect = getattr(libvirt, "open", None)
        if connect is None:
            return False
        connection = connect(libvirt_uri)
    except Exception:  # optional external library: any failure means "unreachable"
        return False
    if connection is None:
        return False
    close = getattr(connection, "close", None)
    if close is not None:
        try:
            close()
        except Exception:  # closing a probe connection must never fail the probe
            logger.debug("Failed to close the libvirt probe connection", exc_info=True)
    return True
