"""libvirt QEMU/KVM domain lifecycle for one isolated agent session.

One domain per session: a disposable qcow2 overlay over the frozen base image,
one virtiofs export of the single allowed workspace, one auto-CID vsock device,
the per-session CPU/memory cgroup caps and the overlay disk cap — and nothing
else (no host devices, no host sockets, no graphics). ``network=True`` adds
exactly one restricted interface: a QEMU user-mode device with no bridge and no
``hostfwd`` (todo 16); the interface is absent from the default spec, so the
pre-network domain shape is preserved. The libvirt connection and the command
runner are injected seams; libvirt is never imported at module level, so the
lifecycle is unit-testable without a hypervisor.

The virtiofs device carries the guest->host ``<idmap>`` (todo 32): the guest
agent runs as a non-root uid/gid that libvirt maps to the host user (virtiofsd
``--uid-map``/``--gid-map``), so a guest write through the export lands on the
host owned by the host user and a rootful guest cannot create root-owned host
files. A spec that asks for a rootful guest agent is refused before any XML is
built.

libvirt accepts only ``accessmode='passthrough'`` for ``<driver
type='virtiofs'/>``, so the attribute is deliberately omitted from the XML.
The enforced control is ``virtiofsd --sandbox=namespace`` over exactly one
export (CVE-2026-47243 / CVE-2026-44210 is the failure mode this forbids).

# allow: SIZE_OK — one cohesive lifecycle (domain XML + define/start/stop/
# teardown + overlay); splitting it would separate halves of one invariant.
"""

from __future__ import annotations

import hashlib
import importlib
import logging
import os
import re
import shutil
import stat
import subprocess
import time
import xml.etree.ElementTree as ET
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from factory.core.runtime import get_data_root

from .config import DEFAULT_SESSION_DISK_GB, DEFAULT_SESSION_MEMORY_MB
from .errors import IsolationError
from .host_escape import assert_domain_xml_clean
from .limits import (
    CPU_PERIOD_US,
    GIB_BYTES,
    assert_overlay_cap,
    cpu_quota_us,
    memory_hard_limit_mib,
    qcow2_virtual_size,
)
from .network import domain_interface_xml
from .workspace import DEFAULT_GUEST_AGENT_GID, DEFAULT_GUEST_AGENT_UID

logger = logging.getLogger(__name__)

DEFAULT_LIBVIRT_URI = "qemu:///session"
DEFAULT_VIRTIOFSD_PATH = "/usr/libexec/virtiofsd"
OVERLAY_DIR_ENV = "IGOREK_ISOLATION_OVERLAY_DIR"

_QEMU_IMG = "qemu-img"
_KVM_DEVICE = Path("/dev/kvm")
_SLUG_UNSAFE = re.compile(r"[^A-Za-z0-9_-]+")
_WORKSPACE_MOUNT_TAG = "workspace"
_UNIX_SOCKET_TABLE = Path("/proc/net/unix")


def _unix_socket_path_is_live(socket_path: Path) -> bool:
    """Return whether the kernel still has a Unix socket bound to this path.

    Reading the kernel socket table avoids probing a live virtiofsd endpoint
    with a fake client connection, which could interfere with its protocol.
    """
    try:
        entries = _UNIX_SOCKET_TABLE.read_text(encoding="utf-8").splitlines()[1:]
    except OSError as exc:
        raise IsolationError(
            "virtiofs_socket_state_unavailable", f"{_UNIX_SOCKET_TABLE}: {exc}"
        ) from exc
    target = str(socket_path)
    for entry in entries:
        fields = entry.split(maxsplit=7)
        if len(fields) == 8 and fields[7] == target:
            return True
    return False


def cleanup_orphaned_virtiofs_socket(socket_path: str | Path) -> bool:
    """Remove a filesystem Unix socket only when the kernel has no endpoint.

    Symlinks and non-sockets are never removed. The inode is rechecked before
    unlinking so a concurrently replaced path cannot be deleted by mistake.
    """
    path = Path(socket_path)
    try:
        initial = path.lstat()
    except FileNotFoundError:
        return False
    except OSError as exc:
        raise IsolationError("virtiofs_socket_state_unavailable", f"{path}: {exc}") from exc
    if not stat.S_ISSOCK(initial.st_mode) or _unix_socket_path_is_live(path):
        return False
    try:
        current = path.lstat()
        if (current.st_dev, current.st_ino) != (initial.st_dev, initial.st_ino):
            raise IsolationError("virtiofs_socket_changed", str(path))
        path.unlink()
        logger.info("isolation: removed orphaned virtiofs socket %s", path)
        return True
    except IsolationError:
        raise
    except OSError as exc:
        raise IsolationError("virtiofs_socket_cleanup_failed", f"{path}: {exc}") from exc


@dataclass(frozen=True, slots=True)
class CommandResult:
    returncode: int
    stdout: str = ""
    stderr: str = ""


class CommandRunner(Protocol):
    """Seam for host commands (``qemu-img``); faked in unit tests."""

    def __call__(self, argv: Sequence[str]) -> CommandResult: ...


class LibvirtDomainHandle(Protocol):
    """Minimal libvirt domain surface used by the lifecycle."""

    def name(self) -> str: ...
    def create(self) -> int: ...
    def shutdown(self) -> int: ...
    def destroy(self) -> int: ...
    def undefine(self) -> int: ...
    def isActive(self) -> int: ...


class LibvirtConnection(Protocol):
    """Minimal libvirt connection surface used by the lifecycle."""

    def defineXML(self, xml: str) -> LibvirtDomainHandle: ...
    def lookupByName(self, name: str) -> LibvirtDomainHandle: ...
    def close(self) -> None: ...


class SubprocessRunner:
    """Default runner: argv only, no shell."""

    def __call__(self, argv: Sequence[str]) -> CommandResult:
        completed = subprocess.run(
            list(argv), capture_output=True, text=True, errors="replace", check=False
        )
        return CommandResult(completed.returncode, completed.stdout, completed.stderr)


@dataclass(frozen=True, slots=True)
class DomainSpec:
    """Everything one session domain is allowed to be built from."""

    session_id: str
    base_image: Path
    workspace_host_path: Path
    overlay_dir: Path
    vcpus: int = 2
    memory_mib: int = DEFAULT_SESSION_MEMORY_MB
    disk_gib: int = DEFAULT_SESSION_DISK_GB
    """Hard cap on the overlay's qcow2 virtual size (todo 24)."""
    machine: str = "q35"
    virtiofsd_path: str = DEFAULT_VIRTIOFSD_PATH
    virtiofs_queue: int = 1024
    network: bool = False
    """Attach exactly one restricted QEMU user-mode interface (plan todo 16)."""
    guest_agent_uid: int = DEFAULT_GUEST_AGENT_UID
    """Non-root guest uid the in-guest agent runs as (todo 32)."""
    host_uid: int = field(default_factory=lambda: os.getuid())
    """Host uid every guest-agent write through the export must land as."""
    guest_agent_gid: int = DEFAULT_GUEST_AGENT_GID
    host_gid: int = field(default_factory=lambda: os.getgid())
    virtiofs_socket: Path | None = None


def _session_slug(session_id: str) -> str:
    digest = hashlib.sha256(str(session_id).encode("utf-8")).hexdigest()[:8]
    slug = _SLUG_UNSAFE.sub("-", str(session_id)).strip("-")[:48].strip("-")
    return f"{slug}-{digest}" if slug else f"session-{digest}"


def domain_name(spec: DomainSpec) -> str:
    """Deterministic, libvirt-safe domain name derived from the session id."""
    return f"igorek-session-{_session_slug(spec.session_id)}"


def overlay_path(spec: DomainSpec) -> Path:
    """Overlay path, structurally guaranteed to stay inside ``spec.overlay_dir``."""
    return overlay_for_session(spec.session_id, spec.overlay_dir)


def virtiofs_socket_for_session(session_id: str) -> Path:
    """Dedicated transport socket under the configured isolation overlay directory.

    Uses the same env-configurable root as the overlays (``IGOREK_ISOLATION_OVERLAY_DIR``)
    so the socket can live outside an encrypted/FUSE data root that the QEMU user
    (``libvirt-qemu``) cannot traverse.
    """
    return resolve_overlay_dir() / f"{_session_slug(session_id)}.sock"


def overlay_for_session(session_id: str, overlay_dir: str | Path) -> Path:
    """Overlay path of one session id inside ``overlay_dir``.

    The reaper/startup recovery needs the path without a full :class:`DomainSpec`
    (a leftover belongs to no live VM); the escape check is the same structural
    guarantee as :func:`overlay_path`.
    """
    directory = Path(overlay_dir)
    candidate = directory / f"{_session_slug(session_id)}.qcow2"
    if not candidate.resolve().is_relative_to(directory.resolve()):
        raise IsolationError("overlay_path_escape", f"{candidate} escapes {directory}")
    return candidate


def resolve_overlay_dir() -> Path:
    """Configured overlay root: env override or ``<data_root>/isolation/overlays``."""
    override = os.environ.get(OVERLAY_DIR_ENV, "").strip()
    if override:
        return Path(override).expanduser()
    return get_data_root() / "isolation" / "overlays"


def validate_spec(spec: DomainSpec) -> None:
    """Fail closed on malformed input before any side effect (never a libvirt call)."""
    if not isinstance(spec.session_id, str) or not spec.session_id.strip():
        raise IsolationError("invalid_session_id", "session_id must be a non-empty string")
    base_image = Path(spec.base_image)
    if not base_image.is_file():
        raise IsolationError("base_image_missing", f"base image not found: {base_image}")
    workspace = Path(spec.workspace_host_path)
    if not workspace.is_absolute():
        raise IsolationError("invalid_workspace", f"workspace must be absolute: {workspace}")
    if not workspace.is_dir():
        raise IsolationError("workspace_missing", f"workspace directory not found: {workspace}")
    if not isinstance(spec.vcpus, int) or spec.vcpus < 1:
        raise IsolationError("invalid_limits", f"vcpus must be an int >= 1, got {spec.vcpus!r}")
    if not isinstance(spec.memory_mib, int) or spec.memory_mib < 128:
        raise IsolationError(
            "invalid_limits", f"memory_mib must be an int >= 128, got {spec.memory_mib!r}"
        )
    if not isinstance(spec.disk_gib, int) or spec.disk_gib < 1:
        raise IsolationError(
            "invalid_limits", f"disk_gib must be an int >= 1, got {spec.disk_gib!r}"
        )
    for name, value in (
        ("host_uid", spec.host_uid),
        ("guest_agent_uid", spec.guest_agent_uid),
        ("host_gid", spec.host_gid),
        ("guest_agent_gid", spec.guest_agent_gid),
    ):
        if not isinstance(value, int) or value < 0:
            raise IsolationError("invalid_idmap", f"{name} must be an int >= 0, got {value!r}")
    if spec.virtiofs_socket is not None:
        socket_path = Path(spec.virtiofs_socket)
        if not socket_path.is_absolute() or socket_path.name != f"{_session_slug(spec.session_id)}.sock":
            raise IsolationError("invalid_virtiofs_socket", str(socket_path))
        if spec.host_uid != os.getuid() or spec.host_gid != os.getgid():
            raise IsolationError("invalid_idmap", "external virtiofsd must map to its host user")
    if spec.guest_agent_uid == 0:
        raise IsolationError(
            "guest_agent_uid_root_forbidden",
            "the guest agent must run as a non-root uid mapped to the host user",
        )


def build_domain_xml(spec: DomainSpec) -> str:
    """Return the deterministic libvirt domain XML for ``spec``."""
    validate_spec(spec)
    overlay = overlay_path(spec)
    root = ET.Element("domain", {"type": "kvm"})
    ET.SubElement(root, "name").text = domain_name(spec)
    ET.SubElement(root, "memory", {"unit": "MiB"}).text = str(spec.memory_mib)
    ET.SubElement(root, "currentMemory", {"unit": "MiB"}).text = str(spec.memory_mib)
    # Kernel-enforced caps (todo 24): libvirt renders <memtune> into the domain
    # cgroup's memory.max and <cputune> into cpu.max, so the budget holds even
    # when the guest ignores it. hard_limit carries the VMM overhead headroom;
    # soft_limit is the configured guest memory itself.
    memtune = ET.SubElement(root, "memtune")
    ET.SubElement(memtune, "hard_limit", {"unit": "MiB"}).text = str(
        memory_hard_limit_mib(spec.memory_mib)
    )
    ET.SubElement(memtune, "soft_limit", {"unit": "MiB"}).text = str(spec.memory_mib)
    ET.SubElement(root, "vcpu", {"placement": "static"}).text = str(spec.vcpus)
    cputune = ET.SubElement(root, "cputune")
    ET.SubElement(cputune, "period").text = str(CPU_PERIOD_US)
    ET.SubElement(cputune, "quota").text = str(cpu_quota_us(spec.vcpus))
    os_element = ET.SubElement(root, "os")
    ET.SubElement(os_element, "type", {"arch": "x86_64", "machine": spec.machine}).text = "hvm"
    ET.SubElement(os_element, "boot", {"dev": "hd"})
    features = ET.SubElement(root, "features")
    ET.SubElement(features, "acpi")
    ET.SubElement(root, "cpu", {"mode": "host-passthrough"})
    ET.SubElement(root, "clock", {"offset": "utc"})
    ET.SubElement(root, "on_poweroff").text = "destroy"
    ET.SubElement(root, "on_reboot").text = "destroy"
    ET.SubElement(root, "on_crash").text = "destroy"
    memory_backing = ET.SubElement(root, "memoryBacking")
    ET.SubElement(memory_backing, "source", {"type": "memfd"})
    ET.SubElement(memory_backing, "access", {"mode": "shared"})
    devices = ET.SubElement(root, "devices")
    disk = ET.SubElement(devices, "disk", {"type": "file", "device": "disk"})
    ET.SubElement(disk, "driver", {"name": "qemu", "type": "qcow2"})
    ET.SubElement(disk, "source", {"file": str(overlay)})
    backing = ET.SubElement(disk, "backingStore", {"type": "file"})
    ET.SubElement(backing, "format", {"type": "qcow2"})
    base_source = ET.SubElement(backing, "source", {"file": str(spec.base_image)})
    # Keep the shared base owner stable; retain normal DAC handling for the overlay.
    ET.SubElement(base_source, "seclabel", {"model": "dac", "relabel": "no"})
    ET.SubElement(backing, "backingStore")
    ET.SubElement(disk, "target", {"dev": "vda", "bus": "virtio"})
    filesystem = ET.SubElement(devices, "filesystem", {"type": "mount"})
    ET.SubElement(
        filesystem, "driver", {"type": "virtiofs", "queue": str(spec.virtiofs_queue)}
    )
    if spec.virtiofs_socket is None:
        binary = ET.SubElement(filesystem, "binary", {"path": spec.virtiofsd_path})
        ET.SubElement(binary, "sandbox", {"mode": "namespace"})
    # Security invariant (todo 32): virtiofsd must translate the non-root guest
    # agent id to the host user, so the host - never a rootful guest - owns
    # everything written through the export.
    if spec.virtiofs_socket is None:
        idmap = ET.SubElement(filesystem, "idmap")
        ET.SubElement(
            idmap,
            "uid",
            {"start": str(spec.guest_agent_uid), "target": str(spec.host_uid), "count": "1"},
        )
        ET.SubElement(
            idmap,
            "gid",
            {"start": str(spec.guest_agent_gid), "target": str(spec.host_gid), "count": "1"},
        )
    if spec.virtiofs_socket is None:
        ET.SubElement(filesystem, "source", {"dir": str(spec.workspace_host_path)})
    else:
        ET.SubElement(filesystem, "source", {"socket": str(spec.virtiofs_socket)})
    ET.SubElement(filesystem, "target", {"dir": _WORKSPACE_MOUNT_TAG})
    vsock = ET.SubElement(devices, "vsock", {"model": "virtio"})
    ET.SubElement(vsock, "cid", {"auto": "yes"})
    if spec.network:
        devices.append(ET.fromstring(domain_interface_xml()))
    ET.indent(root, space="  ")
    xml = ET.tostring(root, encoding="unicode")
    # Structural closure: only the selected workspace and its exact virtiofs
    # transport socket are allowed; guest control sockets remain forbidden.
    assert_domain_xml_clean(
        xml,
        allowed_export_paths=(str(spec.workspace_host_path),),
        allowed_virtiofs_socket_paths=(str(spec.virtiofs_socket),) if spec.virtiofs_socket else (),
    )
    return xml


def open_libvirt_connection(uri: str = DEFAULT_LIBVIRT_URI) -> LibvirtConnection:
    """Open a real libvirt connection; fail closed when libvirt is unavailable."""
    try:
        libvirt = importlib.import_module("libvirt")
    except ImportError as exc:
        raise IsolationError(
            "libvirt_unavailable", "python libvirt bindings are not installed"
        ) from exc
    try:
        connection = libvirt.open(uri)
    except Exception as exc:  # libvirt boundary
        raise IsolationError("libvirt_unavailable", f"cannot open {uri}: {exc}") from exc
    if connection is None:
        raise IsolationError("libvirt_unavailable", f"libvirt.open({uri!r}) returned None")
    return connection


def kvm_unavailable_reason(
    *,
    uri: str = DEFAULT_LIBVIRT_URI,
    virtiofsd_path: str = DEFAULT_VIRTIOFSD_PATH,
) -> str | None:
    """Return why the real-KVM smoke cannot run, or ``None`` when it can."""
    if not _KVM_DEVICE.exists():
        return f"missing {_KVM_DEVICE}"
    if not Path(virtiofsd_path).exists():
        return f"missing {virtiofsd_path}"
    if shutil.which(_QEMU_IMG) is None:
        return f"missing {_QEMU_IMG}"
    try:
        connection = open_libvirt_connection(uri)
    except IsolationError as exc:
        return str(exc)
    try:
        connection.close()
    except Exception:  # libvirt boundary
        pass
    return None


def probe_kvm_available(
    *,
    uri: str = DEFAULT_LIBVIRT_URI,
    virtiofsd_path: str = DEFAULT_VIRTIOFSD_PATH,
) -> bool:
    """True only when a real KVM/libvirt host is usable for the smoke test."""
    return kvm_unavailable_reason(uri=uri, virtiofsd_path=virtiofsd_path) is None


class SessionDomain:
    """Lifecycle for exactly one session domain over injected libvirt/exec seams."""

    def __init__(
        self, connection: LibvirtConnection, runner: CommandRunner | None = None
    ) -> None:
        self._connection = connection
        self._runner: CommandRunner = runner if runner is not None else SubprocessRunner()
        self._spec: DomainSpec | None = None
        self._overlay: Path | None = None
        self._domain: LibvirtDomainHandle | None = None
        self._virtiofsd: subprocess.Popen[bytes] | None = None
        self._owned_socket = False

    @property
    def overlay(self) -> Path | None:
        """Overlay path once ``define()`` validated a spec, else ``None``."""
        return self._overlay

    def is_active(self) -> bool:
        """Whether libvirt still reports the domain running (VMM liveness).

        A domain that was never defined, or whose state cannot be read (the
        libvirt connection is gone), counts as *not* running: without live state
        the host cannot trust the VM, and the fail-safe layer contains it
        instead of waiting for a guest that may no longer exist.
        """
        domain = self._domain
        if domain is None:
            return False
        try:
            return bool(domain.isActive())
        except Exception:  # libvirt boundary: unreadable state is not "alive"
            logger.debug(
                "isolation: could not read the domain state; treating it as not running",
                exc_info=True,
            )
            return False

    def define(self, spec: DomainSpec) -> str:
        """Create the overlay and define the libvirt domain; returns the domain XML."""
        validate_spec(spec)
        overlay = overlay_path(spec)
        self._spec = spec
        if overlay.exists():
            raise IsolationError(
                "overlay_exists",
                f"stale overlay for session {spec.session_id!r}: {overlay}",
            )
        self._overlay = overlay
        try:
            overlay.parent.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise IsolationError("overlay_dir_unavailable", f"{overlay.parent}: {exc}") from exc
        self._create_overlay(spec, overlay)
        xml = build_domain_xml(spec)
        try:
            domain = self._connection.defineXML(xml)
        except Exception as exc:  # libvirt boundary
            self._discard_overlay()
            raise IsolationError("domain_define_failed", str(exc)) from exc
        if domain is None:
            self._discard_overlay()
            raise IsolationError("domain_define_failed", "libvirt returned no domain handle")
        self._domain = domain
        return xml

    def start(self) -> None:
        """Boot the defined domain."""
        try:
            if self._spec is not None and self._spec.virtiofs_socket is not None:
                self._start_external_virtiofsd(self._spec)
            self._require_domain().create()
        except IsolationError:
            self._stop_external_virtiofsd()
            raise
        except Exception as exc:  # libvirt boundary
            self._stop_external_virtiofsd()
            raise IsolationError("domain_start_failed", str(exc)) from exc

    def _start_external_virtiofsd(self, spec: DomainSpec) -> None:
        socket_path = Path(spec.virtiofs_socket)
        try:
            socket_stat = socket_path.lstat()
        except FileNotFoundError:
            socket_stat = None
        except OSError as exc:
            raise IsolationError("virtiofs_socket_state_unavailable", f"{socket_path}: {exc}") from exc
        if socket_stat is not None:
            if not stat.S_ISSOCK(socket_stat.st_mode) or not cleanup_orphaned_virtiofs_socket(socket_path):
                raise IsolationError("virtiofs_socket_exists", str(socket_path))
        socket_path.parent.mkdir(parents=True, exist_ok=True)
        argv = [
            spec.virtiofsd_path,
            f"--socket-path={socket_path}",
            f"--shared-dir={spec.workspace_host_path}",
            "--sandbox=namespace",
            f"--uid-map=:{spec.guest_agent_uid}:{spec.host_uid}:1:",
            f"--gid-map=:{spec.guest_agent_gid}:{spec.host_gid}:1:",
        ]
        try:
            self._virtiofsd = subprocess.Popen(
                argv, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL
            )
            self._owned_socket = True
            for _ in range(100):
                if socket_path.exists():
                    if not stat.S_ISSOCK(socket_path.stat().st_mode):
                        raise IsolationError("virtiofs_socket_invalid", str(socket_path))
                    result = self._runner(
                        ["setfacl", "-m", "u:libvirt-qemu:rw", str(socket_path)]
                    )
                    if result.returncode != 0:
                        raise IsolationError("virtiofs_socket_acl_failed", result.stderr.strip())
                    try:
                        os.chmod(socket_path, 0o666)
                    except OSError as exc:
                        raise IsolationError("virtiofs_socket_acl_failed", str(exc)) from exc
                    return
                if self._virtiofsd.poll() is not None:
                    raise IsolationError(
                        "virtiofsd_start_failed", f"exit {self._virtiofsd.returncode}"
                    )
                time.sleep(0.05)
            raise IsolationError("virtiofsd_start_failed", "socket did not appear")
        except (OSError, IsolationError) as exc:
            self._stop_external_virtiofsd()
            if isinstance(exc, IsolationError):
                raise
            raise IsolationError("virtiofsd_start_failed", str(exc)) from exc

    def _stop_external_virtiofsd(self) -> None:
        proc = self._virtiofsd
        self._virtiofsd = None
        if proc is not None:
            if proc.poll() is None:
                proc.terminate()
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait()
        if self._owned_socket and self._spec is not None:
            socket_path = Path(self._spec.virtiofs_socket)
            socket_path.unlink(missing_ok=True)
            Path(f"{socket_path}.pid").unlink(missing_ok=True)
            self._owned_socket = False

    def stop(self) -> None:
        """Request a graceful guest shutdown (ACPI)."""
        try:
            self._require_domain().shutdown()
        except IsolationError:
            raise
        except Exception as exc:  # libvirt boundary
            raise IsolationError("domain_stop_failed", str(exc)) from exc

    def destroy(self) -> None:
        """Power the domain off immediately."""
        try:
            self._require_domain().destroy()
        except IsolationError:
            raise
        except Exception as exc:  # libvirt boundary
            raise IsolationError("domain_destroy_failed", str(exc)) from exc

    def teardown(self) -> None:
        """Force the domain down, undefine it and delete the overlay (idempotent)."""
        errors: list[IsolationError] = []
        domain = self._domain
        if domain is not None:
            try:
                if domain.isActive():
                    self.destroy()
            except IsolationError as exc:
                errors.append(exc)
            except Exception as exc:  # libvirt boundary
                errors.append(IsolationError("domain_destroy_failed", str(exc)))
            try:
                domain.undefine()
            except Exception as exc:  # libvirt boundary
                errors.append(IsolationError("domain_undefine_failed", str(exc)))
            self._domain = None
        try:
            self._stop_external_virtiofsd()
        except OSError as exc:
            errors.append(IsolationError("virtiofsd_stop_failed", str(exc)))
        overlay = self._overlay
        if overlay is not None and overlay.exists():
            try:
                overlay.unlink()
            except OSError as exc:
                errors.append(IsolationError("overlay_remove_failed", f"{overlay}: {exc}"))
        if errors:
            raise errors[0]

    def _require_domain(self) -> LibvirtDomainHandle:
        if self._domain is None:
            raise IsolationError("domain_not_defined", "define(spec) must succeed first")
        return self._domain

    def _create_overlay(self, spec: DomainSpec, overlay: Path) -> None:
        cap_bytes = spec.disk_gib * GIB_BYTES
        argv = [
            _QEMU_IMG,
            "create",
            "-f",
            "qcow2",
            "-F",
            "qcow2",
            "-b",
            str(spec.base_image),
            str(overlay),
            str(cap_bytes),
        ]
        try:
            result = self._runner(argv)
        except OSError as exc:
            self._discard_overlay()
            raise IsolationError(
                "overlay_create_failed", f"{_QEMU_IMG} could not run: {exc}"
            ) from exc
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "").strip()
            self._discard_overlay()
            raise IsolationError(
                "overlay_create_failed", detail or f"{_QEMU_IMG} exited {result.returncode}"
            )
        self._assert_overlay_cap(spec, overlay)

    def _assert_overlay_cap(self, spec: DomainSpec, overlay: Path) -> None:
        """Refuse (and discard) an overlay whose on-disk size is not the cap."""
        cap_bytes = spec.disk_gib * GIB_BYTES
        base_bytes = qcow2_virtual_size(spec.base_image)
        if base_bytes is not None and base_bytes > cap_bytes:
            logger.warning(
                "isolation: session disk cap %s GiB is below the base image "
                "virtual size %s bytes; the guest sees a truncated disk",
                spec.disk_gib,
                base_bytes,
            )
        try:
            assert_overlay_cap(overlay, disk_gib=spec.disk_gib)
        except IsolationError:
            self._discard_overlay()
            raise

    def _discard_overlay(self) -> None:
        overlay = self._overlay
        if overlay is not None and overlay.exists():
            try:
                overlay.unlink()
            except OSError:
                pass  # best effort; the original failure is the reported one
