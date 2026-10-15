"""Resolve the single host workspace that may be attached to a session VM.

Security invariant: exactly ONE host directory is exported to the guest (plus
explicitly opt-in extras, which are empty by default). Everything else is
refused with a structured :class:`IsolationError` carrying a machine-readable
``reason``.

Stable refusal reasons (``IsolationError.code`` is the part before the first
``:``):

``invalid_cwd_empty`` / ``invalid_cwd_relative`` / ``invalid_cwd_missing`` /
``invalid_cwd_not_a_directory``
    The requested ``cwd`` cannot be a workspace; the offending value is in
    ``IsolationError.detail``.
``sensitive_path_denied:<display>``
    The realpath is a protected host location (``$HOME`` itself, ``~/.ssh``,
    ``~/.config``, ``~/.igorek-agent-factory``, ``/``, ``/etc``, ``/var``, ...).
``data_root_denied:<path>``
    The realpath is inside the runtime data root (database, vault, sessions).
``symlink_escape:<link>``
    A symlink inside the exported tree points outside of it *into a denied
    secret location* (``$HOME`` itself, ``~/.ssh``, ``~/.config``,
    ``~/.igorek-agent-factory``, ... or the data root). An ordinary escape
    (a virtualenv interpreter under ``/usr``, a tool index under ``$HOME``) is
    allowed: the guest resolves such a link inside its own namespace and cannot
    reach the host path, so only the credential boundary is enforced here.
``hardlink_escape:<link>``
    A regular file in the workspace has additional hard links outside the
    scanned tree and outside the explicitly counted uv-managed package roots.
    The guest would see the same inode through virtiofs, so the export is refused.
``workspace_scan_limit_exceeded:<root>``
    The tree is too large to prove filesystem containment; fail closed.
``invalid_extra_guest_path:<path>`` / ``extra_export_duplicate:<path>``
    An opt-in extra export is malformed or collides with the main workspace.
``guest_agent_uid_root_forbidden``
    The policy asks for a rootful guest agent; the guest agent must run as a
    non-root uid that is mapped to the host user (todo 32).

The one-live-export-per-realpath rule lives in
:mod:`factory.core.isolation.export_guard` (stable reason ``workspace_busy``);
this module only resolves the single allowed directory.

Known limitation: the realpath and filesystem entries are checked before the
export is created, so concurrent rename, symlink, or hardlink changes between
resolution and virtiofsd start are not covered here. The caller creates the
export from the returned ``host_path``.
"""

from __future__ import annotations

import os
import stat
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final, TypedDict

from ..runtime import get_data_root
from .errors import IsolationError
from .host_escape import assert_export_clean
from .self_update import assert_export_excludes_host_roots

GUEST_WORKSPACE_PATH: Final[str] = "/workspace"
VIRTIOFS_SANDBOX_MODE: Final[str] = "namespace"
DEFAULT_GUEST_AGENT_UID: Final[int] = 1000
DEFAULT_GUEST_AGENT_GID: Final[int] = 1000
# Fail-closed bound for the containment scan, including packaged desktop builds
# and Android toolchains. Every exported entry is still checked.
MAX_SCAN_ENTRIES: Final[int] = 1_000_000

# ($HOME-relative root, deny the whole subtree). $HOME itself is exact-only.
HOME_DENIED_PATHS: Final[tuple[tuple[str, bool], ...]] = (
    ("", False),
    (".ssh", True),
    (".config", True),
    (".igorek-agent-factory", True),
    (".gnupg", True),
    (".aws", True),
    (".docker", True),
    (".kube", True),
    (".netrc", False),
)
# (absolute root, deny the whole subtree). "/" is exact-only: every path is
# technically "inside" it, but workspaces under /tmp, /home, /srv stay allowed.
SYSTEM_DENIED_PATHS: Final[tuple[tuple[str, bool], ...]] = (
    ("/", False),
    ("/etc", True),
    ("/var", True),
    ("/usr", True),
    ("/boot", True),
    ("/proc", True),
    ("/sys", True),
    ("/dev", True),
    ("/run", True),
    ("/root", True),
)


@dataclass(frozen=True, slots=True)
class ExtraExport:
    """An explicitly opt-in additional export (e.g. an Obsidian vault).

    Nothing is exported beyond the task workspace unless a caller constructs one
    of these; the default policy carries no extras.
    """

    cwd: str
    guest_path: str
    readonly: bool = True


class ExtraExportSpec(TypedDict):
    host_path: str
    guest_path: str
    readonly: bool


class WorkspaceExportSpec(TypedDict):
    """The one virtiofs export the session VM receives.

    ``uid_map``/``gid_map`` are the policy view (``{host id: guest agent id}``)
    that the generated domain XML carries as the ``<idmap>`` of its virtiofs
    device, so everything the guest writes through the export is owned by the
    host user on the host (todo 32).
    """

    host_path: str
    guest_path: str
    readonly: bool
    sandbox: str
    uid_map: dict[int, int]
    gid_map: dict[int, int]
    extra_exports: list[ExtraExportSpec]


@dataclass(frozen=True, slots=True)
class WorkspacePolicy:
    """Host facts the resolver must know: what is home, what is the data root.

    ``host_gid`` defaults to the current process gid; a caller that builds the
    policy by hand still gets the real host group, never a silent 0.
    """

    home: Path
    data_root: Path
    host_uid: int | None
    guest_agent_uid: int = DEFAULT_GUEST_AGENT_UID
    host_gid: int | None = field(default_factory=lambda: os.getgid() if os.name != 'nt' else None)
    guest_agent_gid: int = DEFAULT_GUEST_AGENT_GID
    extra_exports: tuple[ExtraExport, ...] = ()

    @classmethod
    def from_environment(cls) -> WorkspacePolicy:
        return cls(
            home=Path.home(),
            data_root=Path(get_data_root()),
            host_uid=os.getuid() if os.name != 'nt' else None,
            host_gid=os.getgid() if os.name != 'nt' else None,
        )


def _is_within_root(path: str, root: str) -> bool:
    if path == root:
        return True
    if root == os.sep:
        return False
    return path.startswith(root.rstrip(os.sep) + os.sep)


def _display(path: str, home: str) -> str:
    if path == home:
        return "~"
    if _is_within_root(path, home):
        return "~/" + os.path.relpath(path, home)
    return path


def _sensitive_reason(real: str, policy: WorkspacePolicy) -> str | None:
    for root, subtree in SYSTEM_DENIED_PATHS:
        root_real = os.path.realpath(root)
        if real == root_real or (subtree and _is_within_root(real, root_real)):
            return f"sensitive_path_denied:{root}"
    home = os.path.realpath(str(policy.home))
    data_root = os.path.realpath(str(policy.data_root))
    if real == data_root or _is_within_root(real, data_root):
        return f"data_root_denied:{_display(real, home)}"
    for relative, subtree in HOME_DENIED_PATHS:
        root_real = os.path.join(home, relative) if relative else home
        if real == root_real or (subtree and _is_within_root(real, root_real)):
            return f"sensitive_path_denied:{_display(root_real, home)}"
    return None


def _secret_target_reason(target: str, policy: WorkspacePolicy) -> str | None:
    """The denied secret location a symlink target lands in, or ``None``."""
    data_root = os.path.realpath(str(policy.data_root))
    if target == data_root or _is_within_root(target, data_root):
        return "data_root"
    home = os.path.realpath(str(policy.home))
    for relative, subtree in HOME_DENIED_PATHS:
        root_real = os.path.join(home, relative) if relative else home
        if target == root_real or (subtree and _is_within_root(target, root_real)):
            return relative or "~"
    return None


def _escaping_entry(root: str, policy: WorkspacePolicy) -> tuple[str, str] | None:
    """Find a secret symlink escape or a file hard-linked outside ``root``.

    An escape to an ordinary host path is inert for the guest (a virtiofs symlink
    is resolved in the guest namespace), so it is allowed; only a target inside
    a credential location ($HOME itself, ``~/.ssh``/``.config``/..., the data
    root) is refused. Regular files are grouped by device/inode; a link count
    greater than the number of names found in this tree means at least one
    hard link is outside the export. Unreadable directories are skipped: the
    guest (same host user) cannot enter them either, so they leak nothing.
    """
    scanned = 0
    stack = [root]
    inode_entries: dict[tuple[int, int], tuple[int, int, str]] = {}
    while stack:
        current = stack.pop()
        try:
            entries = list(os.scandir(current))
        except OSError:
            continue
        for entry in entries:
            scanned += 1
            if scanned > MAX_SCAN_ENTRIES:
                raise IsolationError(f"workspace_scan_limit_exceeded:{root}")
            try:
                is_link = entry.is_symlink()
                is_dir = not is_link and entry.is_dir(follow_symlinks=False)
            except OSError:
                continue
            if is_link:
                target = os.path.realpath(entry.path)
                if not _is_within_root(target, root) and _secret_target_reason(
                    target, policy
                ) is not None:
                    return ("symlink_escape", entry.path)
            elif is_dir:
                stack.append(entry.path)
            else:
                try:
                    info = entry.stat(follow_symlinks=False)
                except OSError:
                    continue
                if not stat.S_ISREG(info.st_mode):
                    continue
                inode = (info.st_dev, info.st_ino)
                previous = inode_entries.get(inode)
                if previous is None:
                    inode_entries[inode] = (1, info.st_nlink, entry.path)
                else:
                    inode_entries[inode] = (
                        previous[0] + 1,
                        max(previous[1], info.st_nlink),
                        previous[2],
                    )
    for inode, (internal_links, link_count, first_path) in inode_entries.items():
        accounted_links = internal_links
        if link_count > accounted_links:
            return ("hardlink_escape", first_path)
    return None


def _resolve_export_dir(cwd: str | os.PathLike[str], policy: WorkspacePolicy) -> str:
    raw = str(cwd).strip()
    if not raw:
        raise IsolationError("invalid_cwd_empty")
    expanded = os.path.expanduser(raw)
    if not os.path.isabs(expanded):
        raise IsolationError("invalid_cwd_relative", detail=raw)
    real = os.path.realpath(expanded)
    denied = _sensitive_reason(real, policy)
    if denied:
        raise IsolationError(denied)
    if not os.path.isdir(real):
        code = "invalid_cwd_not_a_directory" if os.path.exists(real) else "invalid_cwd_missing"
        raise IsolationError(code, detail=real)
    escaping = _escaping_entry(real, policy)
    if escaping:
        reason, path = escaping
        raise IsolationError(f"{reason}:{path}")
    return real


def resolve_workspace_cwd(
    cwd: str | os.PathLike[str],
    *,
    policy: WorkspacePolicy | None = None,
) -> str:
    """Realpath of a directory the isolation policy would accept as a workspace.

    Same admission check as :func:`resolve_workspace_export` - the deny-lists,
    the realpath resolution and the symlink/hardlink containment scan - without
    building an export spec. Callers that only need to decide *whether* a
    directory may be exported (for example an external-session import that
    inherits the source tool's ``cwd``) then refuse exactly the paths a VM start
    would refuse and raise the same ``IsolationError`` codes.

    Structural export checks (``assert_export_clean`` and
    ``assert_export_excludes_host_roots``) stay in
    :func:`resolve_workspace_export`: they do not depend on the requested path.
    """
    active = policy or WorkspacePolicy.from_environment()
    return _resolve_export_dir(cwd, active)


def resolve_workspace_export(
    cwd: str | os.PathLike[str],
    *,
    policy: WorkspacePolicy | None = None,
) -> WorkspaceExportSpec:
    """Return the virtiofs export spec for the one allowed workspace.

    ``cwd`` is the task's requested working directory; its realpath becomes the
    export root and is mapped to ``/workspace`` in the guest. Raises
    :class:`IsolationError` for every denied host resource.
    """
    from ..platform_support import require_linux
    require_linux('virtiofs_export')
    active = policy or WorkspacePolicy.from_environment()
    if active.guest_agent_uid == 0:
        raise IsolationError(
            "guest_agent_uid_root_forbidden",
            "the guest agent must run as a non-root uid mapped to the host user",
        )
    host_path = _resolve_export_dir(cwd, active)
    seen = {host_path}
    extras: list[ExtraExportSpec] = []
    for extra in active.extra_exports:
        guest_path = str(extra.guest_path).strip()
        if not guest_path.startswith("/") or guest_path == GUEST_WORKSPACE_PATH:
            raise IsolationError(f"invalid_extra_guest_path:{guest_path}")
        extra_host = _resolve_export_dir(extra.cwd, active)
        if extra_host in seen:
            raise IsolationError(
                f"extra_export_duplicate:{_display(extra_host, str(active.home))}"
            )
        seen.add(extra_host)
        extras.append(
            {
                "host_path": extra_host,
                "guest_path": guest_path,
                "readonly": bool(extra.readonly),
            }
        )
    spec: WorkspaceExportSpec = {
        "host_path": host_path,
        "guest_path": GUEST_WORKSPACE_PATH,
        "readonly": False,
        "sandbox": VIRTIOFS_SANDBOX_MODE,
        "uid_map": {active.host_uid: active.guest_agent_uid},
        "gid_map": {active.host_gid: active.guest_agent_gid},
        "extra_exports": extras,
    }
    # Structural closure (todo 15): a control path, a socket or a guest control
    # root must never become an export, whatever a caller passed in.
    assert_export_clean(spec)
    # Self-update split (todo 21): the host runtime's uv roots must never be
    # reachable from the guest, or a guest `uv tool install --force --reinstall .`
    # could write through the workspace export into the HOST environment. The
    # guest environment update stays confined to the guest's own roots.
    assert_export_excludes_host_roots(spec, home=active.home)
    return spec
