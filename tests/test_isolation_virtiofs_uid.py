#!/usr/bin/env python3
"""todo 32 (kvm-session-isolation): virtiofs uid/gid mapping + one export per workspace.

Two invariants are locked here:

1. **Ownership.** The in-guest agent runs as a non-root uid; the generated domain
   XML carries the ``<idmap>`` that maps the guest agent uid/gid to the host user,
   and the virtiofs export spec carries the same mapping. The proof is
   kernel-enforced: the guest write is replayed inside a user namespace whose
   ``uid_map`` is exactly the one the XML carries (inside = guest id, outside =
   host id) and the assertion is on the real ``st_uid``/``st_gid`` of the host
   file - never on the word "mapped". A rootful guest write is shown to land as
   the host user as well, never as a root-owned host file.

2. **One export per workspace realpath.** At most one live VM may export a given
   workspace realpath; a second claim - another session in this process or a live
   claim from another process - is refused with ``workspace_busy``, and a
   released claim becomes reusable.

The user-namespace ownership tests need an unprivileged user namespace; when the
kernel forbids it they skip loudly (``SKIP: ...``) - never a fake pass.
"""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
import sys
import tempfile
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path

REPO_ROOT = Path(
    os.environ.get("IGOREK_FACTORY_REPO", Path(__file__).resolve().parents[1])
)
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from factory.core.isolation.channel import SessionMap  # noqa: E402
from factory.core.isolation.config import (  # noqa: E402
    MODE_KVM,
    HostCapabilities,
    IsolationConfig,
)
from factory.core.isolation.domain import (  # noqa: E402
    CommandResult,
    DomainSpec,
    IsolationError,
    build_domain_xml,
)
from factory.core.isolation.export_guard import WorkspaceExportGuard  # noqa: E402
from factory.core.isolation.manager import (  # noqa: E402
    IsolationManager,
    ManagerSeams,
    SessionSpec,
)
from factory.core.isolation.preflight import REASON_OK, PreflightResult  # noqa: E402
from factory.core.isolation.workspace import (  # noqa: E402
    DEFAULT_GUEST_AGENT_GID,
    DEFAULT_GUEST_AGENT_UID,
    GUEST_WORKSPACE_PATH,
    VIRTIOFS_SANDBOX_MODE,
    WorkspacePolicy,
    resolve_workspace_export,
)

FAKE_CID = 42
_UID_IS_ROOT = os.getuid() == 0

_CHILD_HOLD_SCRIPT = """
import os, sys, time
sys.path.insert(0, sys.argv[1])
from pathlib import Path
from factory.core.isolation.export_guard import WorkspaceExportGuard
from factory.core.isolation.workspace import WorkspacePolicy
root = Path(sys.argv[2])
guard = WorkspaceExportGuard(
    WorkspacePolicy(home=root / "home", data_root=root / "data", host_uid=os.getuid())
)
guard.acquire(sys.argv[3], sys.argv[4])
print("held", flush=True)
time.sleep(120)
"""

_GUEST_WRITER_SCRIPT = """
import os, sys
path = sys.argv[1]
data = sys.stdin.buffer.read()
fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o644)
with os.fdopen(fd, "wb") as handle:
    handle.write(data)
"""


def _userns_write_available() -> bool:
    """True when an unprivileged user namespace can run a real write."""
    if shutil.which("unshare") is None:
        return False
    try:
        result = subprocess.run(
            ["unshare", "--user", "--map-user=0", "--map-group=0", "--", "true"],
            capture_output=True,
            timeout=30,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0


_USERNS_WRITE_AVAILABLE = _userns_write_available()


def _single_id_pair(mapping: dict[int, int]) -> tuple[int, int]:
    ((outside, inside),) = mapping.items()
    return outside, inside


def simulate_guest_write(
    export: dict,
    relative_path: str,
    content: bytes = b"guest-write",
    *,
    guest_uid: int | None = None,
    guest_gid: int | None = None,
) -> Path:
    """Write through ``export`` exactly as the guest idmap does.

    The write runs inside a user namespace whose uid_map/gid_map mirror the
    ``<idmap>`` the domain XML carries: ``inside = guest id`` maps to
    ``outside = host id``. Returns the host path of the created file so the
    caller can assert real ownership.
    """
    host_uid, mapped_guest_uid = _single_id_pair(export["uid_map"])
    host_gid, mapped_guest_gid = _single_id_pair(export["gid_map"])
    inside_uid = mapped_guest_uid if guest_uid is None else guest_uid
    inside_gid = mapped_guest_gid if guest_gid is None else guest_gid
    target = Path(export["host_path"]) / relative_path
    argv = [
        "unshare",
        "--user",
        f"--map-user={inside_uid}",
        f"--map-group={inside_gid}",
        "--",
        sys.executable,
        "-c",
        _GUEST_WRITER_SCRIPT,
        str(target),
    ]
    result = subprocess.run(argv, input=content, capture_output=True, timeout=60, check=False)
    if result.returncode != 0:
        raise AssertionError(
            f"guest write simulation failed (uid={inside_uid}, host={host_uid}): "
            f"{result.stderr.decode('utf-8', 'replace').strip()}"
        )
    return target


def _idmap_of(xml: str) -> tuple[tuple[int, int, int], tuple[int, int, int]]:
    root = ET.fromstring(xml)
    filesystem = root.find("./devices/filesystem")
    assert filesystem is not None
    idmap = filesystem.find("idmap")
    assert idmap is not None, "the domain XML carries no <idmap>"
    uid = idmap.find("uid")
    gid = idmap.find("gid")
    assert uid is not None and gid is not None
    return (
        (int(uid.get("start", "-1")), int(uid.get("target", "-1")), int(uid.get("count", "0"))),
        (int(gid.get("start", "-1")), int(gid.get("target", "-1")), int(gid.get("count", "0"))),
    )


class IdMapXmlTest(unittest.TestCase):
    """The generated domain XML carries the guest->host uid/gid mapping."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="igorek-idmap-xml-")
        self.root = Path(self._tmp.name)
        self.workspace = self.root / "workspace"
        self.workspace.mkdir()
        self.base_image = self.root / "base.qcow2"
        self.base_image.write_bytes(b"qcow2-placeholder")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _spec(self, **overrides: object) -> DomainSpec:
        values: dict[str, object] = {
            "session_id": "sess-idmap",
            "base_image": self.base_image,
            "workspace_host_path": self.workspace,
            "overlay_dir": self.root / "overlays",
            "guest_agent_uid": 1000,
            "host_uid": 4242,
            "guest_agent_gid": 1000,
            "host_gid": 4243,
        }
        values.update(overrides)
        return DomainSpec(**values)  # type: ignore[arg-type]

    def test_domain_xml_carries_the_guest_to_host_idmap(self) -> None:
        xml = build_domain_xml(self._spec())

        uid, gid = _idmap_of(xml)
        self.assertEqual(uid, (1000, 4242, 1))
        self.assertEqual(gid, (1000, 4243, 1))

    def test_idmap_sits_on_the_single_virtiofs_filesystem(self) -> None:
        root = ET.fromstring(build_domain_xml(self._spec()))
        filesystems = root.findall("./devices/filesystem")
        idmaps = root.findall(".//idmap")

        self.assertEqual(len(filesystems), 1)
        self.assertEqual(len(idmaps), 1)
        self.assertIs(filesystems[0].find("idmap"), idmaps[0])
        source = filesystems[0].find("source")
        assert source is not None
        self.assertEqual(source.get("dir"), str(self.workspace))
        binary = filesystems[0].find("binary")
        assert binary is not None
        self.assertNotIn("accessmode", ET.tostring(filesystems[0], encoding="unicode"))

    def test_domain_xml_refuses_a_rootful_guest_agent(self) -> None:
        with self.assertRaises(IsolationError) as caught:
            build_domain_xml(self._spec(guest_agent_uid=0))

        self.assertEqual(caught.exception.code, "guest_agent_uid_root_forbidden")

    def test_domain_xml_refuses_negative_ids(self) -> None:
        for field in ("host_uid", "guest_agent_uid", "host_gid", "guest_agent_gid"):
            with self.subTest(field=field):
                with self.assertRaises(IsolationError) as caught:
                    build_domain_xml(self._spec(**{field: -1}))
                self.assertEqual(caught.exception.code, "invalid_idmap")

    def test_default_domain_spec_maps_the_guest_to_the_real_host_user(self) -> None:
        spec = DomainSpec(
            session_id="sess-default",
            base_image=self.base_image,
            workspace_host_path=self.workspace,
            overlay_dir=self.root / "overlays",
        )
        uid, gid = _idmap_of(build_domain_xml(spec))

        self.assertEqual(uid, (DEFAULT_GUEST_AGENT_UID, os.getuid(), 1))
        self.assertEqual(gid, (DEFAULT_GUEST_AGENT_GID, os.getgid(), 1))
        self.assertNotEqual(uid[0], 0, "the guest agent must never run as root")


class ExportSpecIdMapTest(unittest.TestCase):
    """The single virtiofs export spec carries the same mapping the XML does."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="igorek-idmap-export-")
        self.root = Path(self._tmp.name)
        self.workspace = self.root / "workspace"
        self.workspace.mkdir()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _policy(self, **overrides: object) -> WorkspacePolicy:
        values: dict[str, object] = {
            "home": self.root / "home",
            "data_root": self.root / "data",
            "host_uid": 4242,
            "guest_agent_uid": 1000,
            "host_gid": 4243,
            "guest_agent_gid": 1000,
        }
        values.update(overrides)
        return WorkspacePolicy(**values)  # type: ignore[arg-type]

    def test_export_spec_maps_guest_uid_and_gid_to_the_host_user(self) -> None:
        spec = resolve_workspace_export(self.workspace, policy=self._policy())

        self.assertEqual(spec["uid_map"], {4242: 1000})
        self.assertEqual(spec["gid_map"], {4243: 1000})
        self.assertEqual(spec["sandbox"], VIRTIOFS_SANDBOX_MODE)
        self.assertEqual(spec["guest_path"], GUEST_WORKSPACE_PATH)
        self.assertEqual(spec["host_path"], str(self.workspace.resolve()))

    def test_export_spec_refuses_a_rootful_guest_agent(self) -> None:
        with self.assertRaises(IsolationError) as caught:
            resolve_workspace_export(
                self.workspace, policy=self._policy(guest_agent_uid=0)
            )

        self.assertEqual(caught.exception.code, "guest_agent_uid_root_forbidden")

    def test_policy_defaults_the_host_gid_to_the_real_process_gid(self) -> None:
        policy = WorkspacePolicy(
            home=self.root / "home", data_root=self.root / "data", host_uid=os.getuid()
        )
        spec = resolve_workspace_export(self.workspace, policy=policy)

        self.assertEqual(spec["gid_map"], {os.getgid(): DEFAULT_GUEST_AGENT_GID})


@unittest.skipUnless(
    _USERNS_WRITE_AVAILABLE and not _UID_IS_ROOT,
    "SKIP: unprivileged user namespaces unavailable (virtiofsd namespace sandbox "
    "cannot work either); ownership is not proven without a real uid_map",
)
class GuestWriteOwnershipTest(unittest.TestCase):
    """A guest write through the export is owned by the host user on the host."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="igorek-guest-write-")
        self.root = Path(self._tmp.name)
        self.workspace = self.root / "workspace"
        self.workspace.mkdir()
        self.policy = WorkspacePolicy(
            home=self.root / "home",
            data_root=self.root / "data",
            host_uid=os.getuid(),
            guest_agent_uid=DEFAULT_GUEST_AGENT_UID,
            host_gid=os.getgid(),
            guest_agent_gid=DEFAULT_GUEST_AGENT_GID,
        )
        self.export = resolve_workspace_export(self.workspace, policy=self.policy)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _spec(self) -> DomainSpec:
        return DomainSpec(
            session_id="sess-ownership",
            base_image=self.root / "base.qcow2",
            workspace_host_path=Path(self.export["host_path"]),
            overlay_dir=self.root / "overlays",
            guest_agent_uid=self.policy.guest_agent_uid,
            host_uid=self.policy.host_uid,
            guest_agent_gid=self.policy.guest_agent_gid,
            host_gid=self.policy.host_gid,
        )

    def test_simulated_guest_write_is_owned_by_the_host_user(self) -> None:
        self._spec().base_image.write_bytes(b"qcow2-placeholder")
        (guest_uid, host_uid, uid_count), (guest_gid, host_gid, gid_count) = _idmap_of(
            build_domain_xml(self._spec())
        )
        self.assertEqual((host_uid, host_gid), (os.getuid(), os.getgid()))
        self.assertEqual((uid_count, gid_count), (1, 1))

        path = simulate_guest_write(
            self.export,
            "guest.txt",
            b"hello host",
            guest_uid=guest_uid,
            guest_gid=guest_gid,
        )

        info = path.stat()
        self.assertEqual(info.st_uid, host_uid)
        self.assertEqual(info.st_gid, host_gid)
        self.assertEqual(path.read_bytes(), b"hello host")
        self.assertEqual(stat.S_IMODE(info.st_mode) & 0o600, 0o600)

    def test_rootful_guest_write_never_creates_a_root_owned_host_file(self) -> None:
        path = simulate_guest_write(
            self.export, "root-write.txt", b"guest root", guest_uid=0, guest_gid=0
        )

        info = path.stat()
        self.assertEqual(info.st_uid, os.getuid())
        self.assertNotEqual(info.st_uid, 0, "a rootful guest must never own host files")
        self.assertEqual(path.read_bytes(), b"guest root")


class WorkspaceExportGuardTest(unittest.TestCase):
    """At most one live export per workspace realpath, across processes."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="igorek-export-guard-")
        self.root = Path(self._tmp.name)
        self.workspace = self.root / "workspace"
        self.workspace.mkdir()
        self.other_workspace = self.root / "other"
        self.other_workspace.mkdir()
        self.policy = WorkspacePolicy(
            home=self.root / "home", data_root=self.root / "data", host_uid=os.getuid()
        )
        self.realpath = str(self.workspace.resolve())

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_second_session_on_the_same_realpath_is_refused_workspace_busy(self) -> None:
        first = WorkspaceExportGuard(self.policy)
        second = WorkspaceExportGuard(self.policy)
        first.acquire(self.workspace, "sess-a")

        with self.assertRaises(IsolationError) as caught:
            second.acquire(self.workspace, "sess-b")

        self.assertEqual(caught.exception.code, "workspace_busy")
        self.assertIn(self.realpath, caught.exception.detail)
        self.assertIn("sess-a", caught.exception.detail)
        self.assertEqual(first.held, {"sess-a": self.realpath})

    def test_same_session_reacquire_is_idempotent(self) -> None:
        guard = WorkspaceExportGuard(self.policy)
        guard.acquire(self.workspace, "sess-a")
        guard.acquire(self.workspace, "sess-a")

        self.assertEqual(guard.held, {"sess-a": self.realpath})

    def test_released_claim_becomes_reusable(self) -> None:
        first = WorkspaceExportGuard(self.policy)
        second = WorkspaceExportGuard(self.policy)
        first.acquire(self.workspace, "sess-a")
        first.release("sess-a")

        second.acquire(self.workspace, "sess-b")
        self.assertEqual(second.held, {"sess-b": self.realpath})

    def test_symlinked_paths_share_one_realpath_key(self) -> None:
        link = self.root / "workspace-link"
        link.symlink_to(self.workspace)
        first = WorkspaceExportGuard(self.policy)
        second = WorkspaceExportGuard(self.policy)
        first.acquire(self.workspace, "sess-a")

        with self.assertRaises(IsolationError) as caught:
            second.acquire(link, "sess-b")

        self.assertEqual(caught.exception.code, "workspace_busy")
        self.assertIn(self.realpath, caught.exception.detail)

    def test_different_workspaces_do_not_conflict(self) -> None:
        first = WorkspaceExportGuard(self.policy)
        second = WorkspaceExportGuard(self.policy)
        first.acquire(self.workspace, "sess-a")
        second.acquire(self.other_workspace, "sess-b")

        self.assertEqual(first.held, {"sess-a": str(self.workspace.resolve())})
        self.assertEqual(second.held, {"sess-b": str(self.other_workspace.resolve())})

    def test_release_of_an_unknown_session_is_a_noop(self) -> None:
        guard = WorkspaceExportGuard(self.policy)
        guard.release("never-claimed")

        self.assertEqual(guard.held, {})

    def test_empty_session_id_is_refused(self) -> None:
        guard = WorkspaceExportGuard(self.policy)
        with self.assertRaises(IsolationError) as caught:
            guard.acquire(self.workspace, "   ")

        self.assertEqual(caught.exception.code, "invalid_session_id")

    def test_live_claim_from_another_process_is_refused(self) -> None:
        child = subprocess.Popen(
            [
                sys.executable,
                "-c",
                _CHILD_HOLD_SCRIPT,
                str(REPO_ROOT),
                str(self.root),
                str(self.workspace),
                "child-session",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        try:
            assert child.stdout is not None
            self.assertEqual(child.stdout.readline().strip(), "held")
            with self.assertRaises(IsolationError) as caught:
                WorkspaceExportGuard(self.policy).acquire(self.workspace, "parent-session")
            self.assertEqual(caught.exception.code, "workspace_busy")
            self.assertIn("child-session", caught.exception.detail)
        finally:
            child.kill()
            child.wait(timeout=30)

    def test_claim_is_released_when_the_holder_process_dies(self) -> None:
        child = subprocess.Popen(
            [
                sys.executable,
                "-c",
                _CHILD_HOLD_SCRIPT,
                str(REPO_ROOT),
                str(self.root),
                str(self.workspace),
                "child-session",
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        assert child.stdout is not None
        self.assertEqual(child.stdout.readline().strip(), "held")
        child.kill()
        child.wait(timeout=30)

        guard = WorkspaceExportGuard(self.policy)
        guard.acquire(self.workspace, "parent-session")
        self.assertEqual(guard.held, {"parent-session": self.realpath})


class _FakeDomainHandle:
    def __init__(self, name: str, cid: int | None = FAKE_CID) -> None:
        self._name = name
        self._cid = cid
        self.active = False
        self.create_calls = 0
        self.undefine_calls = 0
        self.destroy_calls = 0
        self.fail_create = False

    def name(self) -> str:
        return self._name

    def create(self) -> int:
        if self.fail_create:
            raise RuntimeError("create refused")
        self.create_calls += 1
        self.active = True
        return 0

    def destroy(self) -> int:
        self.destroy_calls += 1
        self.active = False
        return 0

    def shutdown(self) -> int:
        return 0

    def undefine(self) -> int:
        self.undefine_calls += 1
        return 0

    def isActive(self) -> int:
        return 1 if self.active else 0

    def XMLDesc(self, flags: int = 0) -> str:
        if self._cid is None:
            return "<domain><devices><vsock model='virtio'><cid auto='yes'/></vsock></devices></domain>"
        return (
            "<domain><devices><vsock model='virtio'>"
            f"<cid auto='yes' address='{self._cid}'/>"
            "</vsock></devices></domain>"
        )


class _FakeConnection:
    def __init__(self, *, fail_create: bool = False) -> None:
        self.fail_create = fail_create
        self.defined: dict[str, _FakeDomainHandle] = {}

    def defineXML(self, xml: str) -> _FakeDomainHandle:
        name = ET.fromstring(xml).findtext("name") or ""
        handle = _FakeDomainHandle(name)
        handle.fail_create = self.fail_create
        self.defined[name] = handle
        return handle

    def lookupByName(self, name: str) -> _FakeDomainHandle:
        return self.defined[name]

    def close(self) -> None:
        pass


class ManagerExportGuardTest(unittest.TestCase):
    """The manager holds one workspace claim per live VM and releases it."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="igorek-manager-guard-")
        self.root = Path(self._tmp.name)
        self.workspace = self.root / "workspace"
        self.workspace.mkdir()
        self.policy = WorkspacePolicy(
            home=self.root / "home", data_root=self.root / "data", host_uid=os.getuid()
        )
        self.preflight_calls: list[IsolationConfig] = []

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def _config(self) -> IsolationConfig:
        base = self.root / "base.qcow2"
        base.write_bytes(b"qcow2-placeholder")
        config = IsolationConfig.from_env(
            {
                "IGOREK_ISOLATION_MODE": MODE_KVM,
                "IGOREK_ISOLATION_BASE_IMAGE": str(base),
                "IGOREK_ISOLATION_OVERLAY_DIR": str(self.root / "overlays"),
                "IGOREK_ISOLATION_VSOCK_PORT": "5005",
            },
            data_root=self.root,
            capability_probe=lambda uri: HostCapabilities(True, True, True, True),
        )
        assert config.enabled, config.reason
        return config

    def _manager(self, *, fail_create: bool = False) -> IsolationManager:
        connection = _FakeConnection(fail_create=fail_create)

        def tracking_preflight(cfg: IsolationConfig) -> PreflightResult:
            self.preflight_calls.append(cfg)
            return PreflightResult(ok=True, mode=cfg.mode, reason=REASON_OK)

        seams = ManagerSeams(
            connect=lambda uri: connection,
            runner=lambda argv: CommandResult(0),
            channel=None,
            session_map=SessionMap(),
            workspace_policy=self.policy,
            preflight_fn=tracking_preflight,
        )
        return IsolationManager(self._config(), seams=seams)

    def _session(self, session_id: str) -> SessionSpec:
        return SessionSpec(session_id=session_id, workspace_cwd=str(self.workspace))

    def test_second_session_takes_over_an_idle_workspace_holder(self) -> None:
        manager = self._manager()
        manager.ensure_vm(self._session("sess-a"))

        vm = manager.ensure_vm(self._session("sess-b"))

        self.assertEqual("sess-b", vm.session_id)
        self.assertNotIn("sess-a", manager._vms)

    def test_second_session_is_refused_while_the_holder_is_in_flight(self) -> None:
        manager = self._manager()
        manager.ensure_vm(self._session("sess-a"))
        manager._lifecycle.begin_dispatch("sess-a")

        with self.assertRaises(IsolationError) as caught:
            manager.ensure_vm(self._session("sess-b"))

        self.assertEqual(caught.exception.code, "workspace_busy")
        self.assertIn("sess-a", caught.exception.detail)
        self.assertEqual(len(self.preflight_calls), 1, "refused before any host side effect")

    def test_stopping_the_first_session_frees_the_workspace(self) -> None:
        manager = self._manager()
        manager.ensure_vm(self._session("sess-a"))
        manager.stop("sess-a")

        vm = manager.ensure_vm(self._session("sess-b"))

        self.assertEqual(vm.session_id, "sess-b")

    def test_failed_start_releases_the_workspace_claim(self) -> None:
        manager = self._manager(fail_create=True)
        with self.assertRaises(IsolationError) as caught:
            manager.ensure_vm(self._session("sess-a"))
        self.assertEqual(caught.exception.code, "domain_start_failed")

        manager.stop("sess-a")  # no VM is tracked; must not raise
        vm = self._manager().ensure_vm(self._session("sess-b"))

        self.assertEqual(vm.session_id, "sess-b")

    def test_off_mode_creates_no_export_lock_state(self) -> None:
        off = IsolationManager(
            IsolationConfig.from_env({}, data_root=self.root),
            seams=ManagerSeams(workspace_policy=self.policy),
        )
        with self.assertRaises(IsolationError) as caught:
            off.ensure_vm(self._session("sess-a"))

        self.assertEqual(caught.exception.code, "isolation_not_enabled")
        self.assertFalse((self.root / "data" / "isolation").exists())


if __name__ == "__main__":
    unittest.main()
