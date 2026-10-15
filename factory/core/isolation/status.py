"""Additive ``isolation`` value for mobile session/task status payloads (todo 12/35).

The value is a small technical indicator, never a workflow step. It answers one
honest question: *is this session/task actually running behind a VM boundary?*
Two structural inputs decide it:

* the resolved :class:`IsolationConfig` - ``"kvm"`` is claimed only for a
  *satisfied* ``kvm`` request (``enabled`` is true: the base image exists and the
  host preflight passed); a requested-but-unavailable ``kvm`` configuration is
  reported as ``"kvm-unavailable"`` instead of claiming a boundary that is not
  operational;
* the session backend - in this plan only ``cc-backend`` runs inside a VM, so
  ``claude``/``codex`` (and profiles that do not resolve to ``cc-backend``) are
  pinned to ``"none"`` and can never claim ``"kvm"``.

Resolution reads the full config (env parsing, base-image presence, host
preflight) and can therefore probe the host in ``kvm`` mode; the mobile bridge
resolves it once per service instance and caches the result, which keeps status
refreshes and session creation free of repeated preflight work. Reading the
status never constructs or touches the isolation manager, so it never boots a VM.
"""

from __future__ import annotations

from collections.abc import Mapping

from .config import MODE_KVM, IsolationConfig

__all__ = [
    "ISOLATION_BACKEND",
    "ISOLATION_KVM",
    "ISOLATION_KVM_UNAVAILABLE",
    "ISOLATION_NONE",
    "isolation_status_for_backend",
    "isolation_status_value",
    "resolve_isolation_status",
]

ISOLATION_NONE = "none"
ISOLATION_KVM = "kvm"
# Requested ``kvm`` that is not operational (missing base image, failed host
# preflight, invalid settings): truthful, never a boundary claim.
ISOLATION_KVM_UNAVAILABLE = "kvm-unavailable"
# The only backend this plan actually runs inside a VM (see ISOLATION.md, §1).
ISOLATION_BACKEND = "cc-backend"


def isolation_status_value(config: IsolationConfig) -> str:
    """Map one resolved config to its config-level status value."""
    if config.mode != MODE_KVM:
        return ISOLATION_NONE
    return ISOLATION_KVM if config.enabled else ISOLATION_KVM_UNAVAILABLE


def isolation_status_for_backend(value: str, backend: str) -> str:
    """Narrow a config-level status value to one backend's honest status.

    Only ``cc-backend`` sessions may report ``"kvm"``/``"kvm-unavailable"``; any
    other backend (``claude``, ``codex``, an unresolved profile, an empty
    backend) is pinned to ``"none"``.
    """
    if str(backend).strip() != ISOLATION_BACKEND:
        return ISOLATION_NONE
    return value


def resolve_isolation_status(env: Mapping[str, str] | None = None) -> str:
    """Resolve the config-level status value from the host isolation configuration."""
    return isolation_status_value(IsolationConfig.from_env(env))
