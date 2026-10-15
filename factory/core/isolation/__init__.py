"""Isolation components for KVM per-session microVMs.

Most modules are trusted host-side components imported by the host runtime
(gateway, worker, isolation manager). The guest-side exception is
:mod:`factory.core.isolation.guest_runner` together with ``guest_task`` /
``guest_trace`` / ``guest_result``: those run inside the session VM only and are
deliberately kept free of host-side imports. The guest image build recipe lives
in ``factory/scripts/build_isolation_image.sh``; its manifest logic is in
:mod:`factory.core.isolation.base_image`.
"""

from __future__ import annotations

from .config import (
    DEFAULT_LIBVIRT_URI,
    DEFAULT_VSOCK_PORT,
    HostCapabilities,
    IsolationConfig,
    check_host_capabilities,
)
from .errors import IsolationError

__all__ = [
    "DEFAULT_LIBVIRT_URI",
    "DEFAULT_VSOCK_PORT",
    "HostCapabilities",
    "IsolationConfig",
    "IsolationError",
    "check_host_capabilities",
]
