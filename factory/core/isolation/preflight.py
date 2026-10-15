"""Fail-closed KVM preflight and the guest/host version handshake (todo 34).

:func:`preflight` is the start-time gate in front of every ``kvm`` session VM. It
re-checks, on the real host, the two conditions that would otherwise let a
requested ``kvm`` mode silently turn into a non-isolated run:

1. host capabilities - KVM device, libvirt, ``virtiofsd`` and ``qemu-img``
   (:func:`factory.core.isolation.config.check_host_capabilities`);
2. base-image identity and version - the image is verified against its manifest
   and the recorded tool version must equal the host expectation
   (:func:`factory.core.isolation.base_image.verify_image`).

Any unmet condition yields ``ok=False`` with a ``reason`` prefixed by
``isolation_unavailable:``; there is deliberately no fallback path. Mode ``off``
short-circuits before any probe or file hashing, so an ``off`` runtime stays
side-effect free.

The boot-handshake counterpart lives here too: the guest reports
``igorek-isolation-guest/<tool_version>``, :func:`verify_guest_version` answers
whether the host accepts it, and :func:`enforce_guest_version` refuses a mismatch
by raising :class:`PreflightError`.

:class:`PreflightError` subclasses the package-wide
:class:`factory.core.isolation.errors.IsolationError`, so ``except
IsolationError`` catches a handshake refusal like any other isolation failure and
the refusal exposes ``reason``/``detail``/``code``.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Protocol

from . import base_image
from .base_image import VerifyResult
from .config import (
    MODE_KVM,
    MODE_OFF,
    CapabilityProbe,
    HostCapabilities,
    IsolationConfig,
    check_host_capabilities,
)
from .errors import IsolationError

__all__ = [
    "REASON_MODE_OFF",
    "REASON_OK",
    "REASON_UNAVAILABLE_PREFIX",
    "ImageVerifier",
    "PreflightError",
    "PreflightResult",
    "preflight",
    "expected_guest_version",
    "verify_guest_version",
    "enforce_guest_version",
]

REASON_MODE_OFF = "mode_off"
REASON_OK = "ok"
REASON_UNAVAILABLE_PREFIX = "isolation_unavailable:"


class PreflightError(IsolationError):
    """Typed preflight refusal with a stable ``reason`` and a human ``detail``.

    Subclasses :class:`IsolationError`, so handlers written against the shared
    package error (or any of its module aliases) also catch handshake refusals;
    ``reason``/``detail``/``code`` come from the shared base.
    """


class ImageVerifier(Protocol):
    """Seam for base-image verification; the production default is ``verify_image``."""

    def __call__(
        self,
        image_path: str | os.PathLike[str],
        *,
        manifest_path: str | os.PathLike[str] | None = None,
        expected_tool_version: str | None = None,
    ) -> VerifyResult: ...


@dataclass(frozen=True, slots=True)
class PreflightResult:
    """Structured outcome of :func:`preflight`."""

    ok: bool
    mode: str
    reason: str
    capabilities: HostCapabilities | None = None


def _refused(
    mode: str,
    detail: str,
    capabilities: HostCapabilities | None = None,
) -> PreflightResult:
    return PreflightResult(
        ok=False,
        mode=mode,
        reason=f"{REASON_UNAVAILABLE_PREFIX}{detail}",
        capabilities=capabilities,
    )


def _expected_tool_version(tool_version: str | None) -> str:
    if tool_version is not None and tool_version.strip():
        return tool_version.strip()
    return base_image.resolve_tool_version()


def preflight(
    config: IsolationConfig,
    *,
    capability_probe: CapabilityProbe = check_host_capabilities,
    image_verifier: ImageVerifier = base_image.verify_image,
    tool_version: str | None = None,
) -> PreflightResult:
    """Decide whether this host may start a session VM for ``config``.

    The keyword-only arguments are injection seams for tests and for callers
    that already resolved a tool version; production callers pass only
    ``config``.
    """
    if config.mode == MODE_OFF:
        return PreflightResult(ok=True, mode=MODE_OFF, reason=REASON_MODE_OFF)
    if config.mode != MODE_KVM:
        return _refused(config.mode, f"unknown_mode:{config.mode}")
    if not config.enabled:
        # ``from_env`` already refused (base_image_missing / invalid_config:* /
        # capability_unavailable:*): a disabled config must never preflight ok.
        return _refused(MODE_KVM, config.reason or "config_disabled")
    if config.base_image is None:
        return _refused(MODE_KVM, "base_image_missing")

    capabilities = capability_probe(config.libvirt_uri)
    if not capabilities.ok:
        return _refused(MODE_KVM, capabilities.reason or "capability_unknown", capabilities)

    try:
        image = image_verifier(
            config.base_image,
            expected_tool_version=_expected_tool_version(tool_version),
        )
    except OSError as exc:
        return _refused(MODE_KVM, f"image_unreadable:{exc}", capabilities)
    if not image.ok:
        return _refused(MODE_KVM, image.code, capabilities)
    if image.manifest is not None and image.manifest.vsock_port != config.vsock_port:
        return _refused(MODE_KVM, "guest_channel_mismatch", capabilities)
    return PreflightResult(ok=True, mode=MODE_KVM, reason=REASON_OK, capabilities=capabilities)


def expected_guest_version(tool_version: str | None = None) -> str:
    """Version string the boot handshake requires the guest to report."""

    return base_image.expected_guest_version(tool_version)


def verify_guest_version(reported: str | None, tool_version: str | None = None) -> bool:
    """True only when the guest-reported handshake equals the host expectation."""

    return base_image.guest_handshake_matches(reported, tool_version)


def enforce_guest_version(reported: str | None, tool_version: str | None = None) -> str:
    """Return the accepted handshake string or raise :class:`PreflightError`.

    The refusal is an :class:`IsolationError`, catchable through the shared
    package type.
    """

    if isinstance(reported, str) and verify_guest_version(reported, tool_version):
        return reported.strip()
    raise PreflightError(
        "guest_version_mismatch",
        f"guest reported {reported!r}; host expects {expected_guest_version(tool_version)!r}",
    )
