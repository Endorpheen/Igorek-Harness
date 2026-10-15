"""Per-session resource limits: the configured budget, the cgroup contract and
the containment of a guest that breaches one (plan todo 24, Wave 4).

The plan's research §8 row 10 (Firecracker jailer ``--resource-limit``, cgroups)
maps onto this stack as three structural limits, none of which depends on guest
cooperation:

* **CPU** - ``<cputune><period>/<quota>`` in the domain XML. libvirt renders it
  into the per-domain cgroup (``cpu.max``), so the guest can use at most
  ``vcpus * period`` microseconds of CPU per period no matter what it runs;
* **memory** - ``<memtune><hard_limit>`` (the cgroup ``memory.max``) is the
  configured guest memory plus a fixed QEMU overhead allowance, so the kernel
  caps the whole domain, not just what the guest agrees to;
* **disk** - the overlay's qcow2 virtual size is the configured cap, so a
  disk-fill inside the guest ends in an I/O error/ENOSPC at the cap while the
  host file stays bounded (see :func:`assert_overlay_cap`).

A guest that still reports a breach (``enospc``, ``oom``, a fork/FD exhaustion)
is classified here by an exact machine token - never by prose matching - and the
manager tears the VM down and reports the breach through the task result and the
host trace (:func:`limit_failure_result`, :func:`emit_limit_breach`).

The token has two halves and this module owns both. Inside the guest,
:func:`limit_token_for_error` maps a real executor failure onto one of the
documented tokens from the exception's own structure (``OSError.errno``, a
``MemoryError``), and the runner reports that token as an ``error`` frame; on the
host, :func:`classify_limit_breach` turns the token back into the breached class.
No message text is ever matched on either half, so the containment path is
reachable from a real ENOSPC/OOM instead of only from synthetic tokens.

Process (``pids``) and file-descriptor (``fd``) exhaustion are contained by the
same path: the guest cannot create host processes or host file descriptors at
all (the domain XML device set is closed and fixed), and a guest that exhausts
its own budget is stopped and reported instead of being retried forever.
"""

from __future__ import annotations

import errno
import logging
import struct
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

from ..cc_backend_trace import append_cc_backend_trace
from ..trace_taxonomy import CcTraceEvent
from .errors import IsolationError

logger = logging.getLogger(__name__)

__all__ = [
    "CPU_PERIOD_US",
    "GIB_BYTES",
    "LIMIT_DISK",
    "LIMIT_FD",
    "LIMIT_MEMORY",
    "LIMIT_PIDS",
    "MEMORY_OVERHEAD_MIB",
    "LimitBreach",
    "assert_overlay_cap",
    "classify_limit_breach",
    "cpu_quota_us",
    "emit_limit_breach",
    "limit_failure_result",
    "limit_token_for_error",
    "memory_hard_limit_mib",
    "qcow2_virtual_size",
]

GIB_BYTES: Final[int] = 1024**3
# One scheduling period per vCPU's worth of quota: quota/period is the number of
# host cores the domain may consume in total.
CPU_PERIOD_US: Final[int] = 100_000
# QEMU's own device threads/allocations need headroom above the guest memory; a
# hard limit equal to the guest budget would OOM-kill the VMM instead of the
# guest. This mirrors libvirt's documented "hard_limit above guest memory" rule.
MEMORY_OVERHEAD_MIB: Final[int] = 256

LIMIT_DISK: Final[str] = "disk"
LIMIT_MEMORY: Final[str] = "memory"
LIMIT_PIDS: Final[str] = "pids"
LIMIT_FD: Final[str] = "fd"

# Exact guest error tokens (never substrings, never prose) -> breached class.
_LIMIT_REASONS: Final[dict[str, str]] = {
    "enospc": LIMIT_DISK,
    "no_space_left": LIMIT_DISK,
    "disk_full": LIMIT_DISK,
    "disk_quota_exceeded": LIMIT_DISK,
    "overlay_full": LIMIT_DISK,
    "oom": LIMIT_MEMORY,
    "out_of_memory": LIMIT_MEMORY,
    "cannot_allocate_memory": LIMIT_MEMORY,
    "memory_limit": LIMIT_MEMORY,
    "pids_limit": LIMIT_PIDS,
    "fork_failed": LIMIT_PIDS,
    "cannot_fork": LIMIT_PIDS,
    "thread_creation_failed": LIMIT_PIDS,
    "too_many_open_files": LIMIT_FD,
    "emfile": LIMIT_FD,
    "fd_limit": LIMIT_FD,
    "open_files_limit": LIMIT_FD,
}

_BREACH_DETAILS: Final[dict[str, str]] = {
    LIMIT_DISK: "the guest filled its disk up to the overlay cap",
    LIMIT_MEMORY: "the guest exceeded its memory budget",
    LIMIT_PIDS: "the guest exhausted its process budget",
    LIMIT_FD: "the guest exhausted its open-file budget",
}

_QCOW2_MAGIC: Final[bytes] = b"QFI\xfb"
_QCOW2_VERSIONS: Final[frozenset[int]] = frozenset({2, 3})
# qcow2 header: magic(4) version(4) backing_offset(8) backing_size(4)
# cluster_bits(4) size(8) ... - the virtual size is the u64 at offset 24.
_QCOW2_SIZE_OFFSET: Final[int] = 24
_QCOW2_HEADER_LEN: Final[int] = 104


def cpu_quota_us(vcpus: int) -> int:
    """The cgroup CPU quota (microseconds per period) for ``vcpus`` cores."""
    return CPU_PERIOD_US * vcpus


def memory_hard_limit_mib(memory_mib: int) -> int:
    """The cgroup memory cap: the guest budget plus the VMM overhead allowance."""
    return memory_mib + MEMORY_OVERHEAD_MIB


@dataclass(frozen=True, slots=True)
class LimitBreach:
    """One classified over-limit event reported by the guest."""

    kind: str
    reason: str
    detail: str

    def to_trace_record(self, *, subject: str) -> dict[str, Any]:
        """The taxonomy-shaped host trace record for this breach."""
        return {
            "kind": CcTraceEvent.ISOLATION_LIMIT_EXCEEDED,
            "subject": subject,
            "reason": self.reason,
            "limit": self.kind,
            "detail": self.detail,
        }


def classify_limit_breach(reason: str) -> LimitBreach | None:
    """Classify an exact guest error token; ``None`` when it is not a limit."""
    token = reason.strip()
    kind = _LIMIT_REASONS.get(token)
    if kind is None:
        return None
    return LimitBreach(kind=kind, reason=token, detail=_BREACH_DETAILS[kind])


# Real exhaustion failures -> one of the exact tokens above. Structural: the
# exception type and ``OSError.errno`` decide, never the message text.
_ERRNO_LIMIT_TOKENS: Final[dict[int, str]] = {
    errno.ENOSPC: "enospc",
    errno.EDQUOT: "disk_quota_exceeded",
    errno.ENOMEM: "cannot_allocate_memory",
    errno.EMFILE: "emfile",
    errno.ENFILE: "fd_limit",
    # fork()/pthread_create() report EAGAIN - subprocess.Popen surfaces it as
    # BlockingIOError - when the process/thread budget is exhausted.
    errno.EAGAIN: "fork_failed",
}


def limit_token_for_error(error: object) -> str | None:
    """The documented limit token for a real executor failure, or ``None``.

    Called where the failure is observed (the guest runner's executor catch), so
    the token comes from the exception's own structure: ``MemoryError`` is the
    memory budget, and ``OSError.errno`` is what the kernel returned. The result
    is always a key of :data:`_LIMIT_REASONS`, so the host's
    :func:`classify_limit_breach` maps it to the documented class and contains
    the VM. A failure with no exhaustion structure yields ``None`` and keeps the
    ordinary ``executor_error`` path.
    """
    if isinstance(error, MemoryError):
        return "oom"
    if isinstance(error, OSError) and error.errno is not None:
        return _ERRNO_LIMIT_TOKENS.get(error.errno)
    return None


def qcow2_virtual_size(path: Path) -> int | None:
    """The qcow2 virtual size in bytes, or ``None`` when ``path`` is not qcow2.

    Reading the header is the only check that observes what the guest will
    actually see: the virtual size, not the sparse host file size.
    """
    try:
        with Path(path).open("rb") as handle:
            header = handle.read(_QCOW2_HEADER_LEN)
    except OSError:
        return None
    if len(header) < _QCOW2_SIZE_OFFSET + 8 or header[:4] != _QCOW2_MAGIC:
        return None
    version = struct.unpack(">I", header[4:8])[0]
    if version not in _QCOW2_VERSIONS:
        return None
    return struct.unpack(">Q", header[_QCOW2_SIZE_OFFSET : _QCOW2_SIZE_OFFSET + 8])[0]


def assert_overlay_cap(overlay: Path, *, disk_gib: int) -> int | None:
    """Refuse an overlay whose on-disk virtual size is not the configured cap.

    Returns the observed size, or ``None`` when the file carries no qcow2 header
    (nothing to assert about a non-image). A real ``qemu-img create`` always
    produces a qcow2 image, so the production path is always checked; the
    refusal is fail-closed and discards the overlay.
    """
    observed = qcow2_virtual_size(overlay)
    if observed is None:
        logger.debug("isolation: %s carries no qcow2 header; disk cap unverified", overlay)
        return None
    cap_bytes = disk_gib * GIB_BYTES
    if observed != cap_bytes:
        raise IsolationError(
            "overlay_size_mismatch",
            f"{overlay}: qcow2 virtual size {observed} != configured cap {cap_bytes}",
        )
    return observed


def limit_failure_result(breach: LimitBreach, *, session_id: str) -> dict[str, Any]:
    """The terminal executor-shaped failure for a contained limit breach."""
    detail = (
        f"session {session_id} hit its {breach.kind} limit ({breach.reason}); "
        "the VM was destroyed and the host is unaffected"
    )
    return {
        "status": "fail",
        "output_text": "",
        "usage": {},
        "tool_calls_count": 0,
        "failure_reason": f"limit_exceeded:{breach.kind}",
        "failure_details": detail,
        "error": detail,
    }


def emit_limit_breach(
    artifact_workdir: str,
    breach: LimitBreach,
    *,
    subject: str,
    writer: Callable[[str, dict[str, Any]], None] | None = None,
) -> bool:
    """Append the breach to the authoritative host trace; never fails the task."""
    write = writer if writer is not None else append_cc_backend_trace
    try:
        write(artifact_workdir, breach.to_trace_record(subject=subject))
    except OSError as exc:  # a trace concern must never mask the containment
        logger.warning("isolation limits: cannot append the breach record: %s", exc)
        return False
    return True
