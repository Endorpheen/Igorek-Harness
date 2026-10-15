"""MCP / Vault / skill-asset placement across the VM boundary (plan todo 31).

**Placement decision (the default).** MCP servers run **in the guest**: the host
resolves every configured server (env-var config ``MCP_SERVERS`` +
``MCP_{NAME}_*`` and the profile-enabled entries of the MCP registry), narrows it
into a :class:`McpServerPlacement` and carries the program, the argv vector and
the server's *configured env* over the versioned task payload
(:mod:`factory.core.isolation.history`). The guest starts the same stdio servers
with that env and registers their tools, so the ``tools/list`` handshake and the
tool dispatch happen inside the VM.

**Vault materialization stays host-side.** ``worker._materialize_vault_env``
runs on the host as today (inside its ``_temporary_env`` window, so the
ephemeral values are visible while this module collects); only the values a
server actually needs travel - in that server's own ``env`` section, never as a
global guest env. The whole data dir never crosses.

**Servers that still need the host-side broker (todo 19).** A server whose
credential must not enter the guest is marked ``host_brokered`` (env-var
``MCP_{NAME}_HOST_BROKERED=true`` or registry ``"host_brokered": true``). Its
spec travels without any env value and the guest reports a visible
``isolation.mcp_brokered_pending`` status instead of starting it. The
credential-bearing servers of ``mcp_bridge`` - ``tavily``, ``xsearch`` and
``fal_image`` - are the ones intended for that broker path; until it lands they
run in-guest with their ephemeral Vault env (the default path above, residual
risk recorded per todo 18), and a credential that is missing entirely is
surfaced as ``isolation.mcp_unavailable`` - never a silent skip.

**Skill assets are a bounded read-only export.** The host mirrors the
task-local ``<artifact_workdir>/.skill_runtime/<skill>`` tree that the skills
manager already materialized for this task (the tree the model was told about in
``skills_text``) - nothing else, never the data dir. Asset bytes travel
urlsafe-base64 encoded (a transport encoding: the guest writes them back
byte-for-byte) and the ``skills_text`` runtime root is rewritten host->guest so
every path the model reads points at ``<artifact_workdir>/skills/<skill>`` inside
the guest. Per-file and total byte budgets bound the payload; an asset that
cannot be exported produces ``isolation.skill_asset_unavailable``.

**Never a silent drop.** Every capability this module cannot place produces a
:class:`CapabilityStatus` with a taxonomy-shaped ``isolation.*`` kind. The host
writes its statuses into the authoritative trace file (the mobile pipeline
normalizes them like any other event); the guest emits its statuses as inert
``event`` frames whose ``record`` envelope carries the status, so the payload
policy can never turn a status into a dispatch failure.

Fail-closed rules: a malformed extension section refuses the whole payload
(:class:`ExtensionError` -> ``PayloadError``), a guest never guesses a placement,
and the guest always clears the ambient ``MCP_SERVERS`` before the loop starts -
in kvm mode the payload is the only MCP configuration, so a guest-side env file
can never start a server with the wrong (host-only) credentials.

# allow: SIZE_OK - one placement contract in one file: the wire schema, the host
# collection, the guest application and the visible-status vocabulary are the
# same decision seen from both sides; splitting them would let the host and the
# guest drift, which is exactly what "never a silent drop" forbids.
"""

from __future__ import annotations

import base64
import logging
import os
from itertools import islice
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path, PurePosixPath
from typing import Any, Final, assert_never

from ..cc_backend_trace import append_cc_backend_trace
from ..trace_taxonomy import CcTraceEvent
from ..artifact_files import open_regular_file, parent_handle
from .channel import (
    DEFAULT_VSOCK_PORT,
    ChannelConfig,
    JsonValue,
    MessageKind,
    payload_policy_violation,
)

logger = logging.getLogger(__name__)

__all__ = [
    "GUEST_SKILLS_SUBDIR",
    "MAX_SKILL_ASSET_FILES",
    "MAX_SKILL_ASSET_FILE_BYTES",
    "MAX_SKILL_ASSET_TOTAL_BYTES",
    "MCP_CREDENTIAL_EPHEMERAL_ENV",
    "MCP_CREDENTIAL_HOST_BROKER",
    "MCP_CREDENTIAL_NONE",
    "MCP_PLACEMENT_HOST_BROKERED",
    "MCP_PLACEMENT_IN_GUEST",
    "McpCredentialSource",
    "McpPlacement",
    "McpSources",
    "REASON_BUDGET_EXCEEDED",
    "REASON_COMMAND_MISSING",
    "REASON_CREDENTIAL_MISSING",
    "REASON_FILE_TOO_LARGE",
    "REASON_HOST_BROKER_PENDING",
    "REASON_NOT_REGISTERED",
    "REASON_PATH_DENIED",
    "REASON_PAYLOAD_DENIED",
    "REASON_READ_FAILED",
    "REASON_START_FAILED",
    "REASON_UNSUPPORTED_TYPE",
    "REASON_WRITE_FAILED",
    "SKILL_ASSET_ENCODING_BASE64",
    "SKILL_RUNTIME_SUBDIR",
    "CapabilityStatus",
    "ExtensionError",
    "McpServerPlacement",
    "SkillAsset",
    "TaskExtensions",
    "apply_guest_mcp_servers",
    "collect_task_extensions",
    "emit_capability_statuses",
    "guest_skills_root",
    "materialize_skill_assets",
    "mcp_servers_from_wire",
    "rewrite_skill_runtime_paths",
    "skill_assets_from_wire",
]

#: Where the host skills manager materializes the task-local skill tree.
SKILL_RUNTIME_SUBDIR: Final[str] = ".skill_runtime"
#: Guest sub-directory (under the artifact workdir) the assets are written to.
GUEST_SKILLS_SUBDIR: Final[str] = "skills"
#: The only asset transport encoding (urlsafe base64: byte-exact and policy-safe).
SKILL_ASSET_ENCODING_BASE64: Final[str] = "base64"

MAX_SKILL_ASSET_FILE_BYTES: Final[int] = 256 * 1024
MAX_SKILL_ASSET_TOTAL_BYTES: Final[int] = 1024 * 1024
MAX_SKILL_ASSET_FILES: Final[int] = 128

class McpPlacement(StrEnum):
    """Where one MCP server runs: in the guest, or behind the host broker."""

    IN_GUEST = "in_guest"
    HOST_BROKERED = "host_brokered"


class McpCredentialSource(StrEnum):
    """Where the server's credential comes from (never logged)."""

    EPHEMERAL_ENV = "ephemeral_env"
    HOST_BROKER = "host_broker"
    NONE = "none"


MCP_PLACEMENT_IN_GUEST: Final[McpPlacement] = McpPlacement.IN_GUEST
MCP_PLACEMENT_HOST_BROKERED: Final[McpPlacement] = McpPlacement.HOST_BROKERED
MCP_CREDENTIAL_EPHEMERAL_ENV: Final[McpCredentialSource] = McpCredentialSource.EPHEMERAL_ENV
MCP_CREDENTIAL_HOST_BROKER: Final[McpCredentialSource] = McpCredentialSource.HOST_BROKER
MCP_CREDENTIAL_NONE: Final[McpCredentialSource] = McpCredentialSource.NONE

REASON_CREDENTIAL_MISSING: Final[str] = "credential_missing"
REASON_COMMAND_MISSING: Final[str] = "command_missing"
REASON_UNSUPPORTED_TYPE: Final[str] = "unsupported_type"
REASON_NOT_REGISTERED: Final[str] = "not_registered"
REASON_PAYLOAD_DENIED: Final[str] = "payload_denied"
REASON_START_FAILED: Final[str] = "start_failed"
REASON_HOST_BROKER_PENDING: Final[str] = "host_broker_pending"
REASON_PATH_DENIED: Final[str] = "path_denied"
REASON_WRITE_FAILED: Final[str] = "write_failed"
REASON_READ_FAILED: Final[str] = "read_failed"
REASON_FILE_TOO_LARGE: Final[str] = "file_too_large"
REASON_BUDGET_EXCEEDED: Final[str] = "budget_exceeded"

_REASON_MCP_INVALID: Final[str] = "payload_mcp_servers_invalid"
_REASON_ASSETS_INVALID: Final[str] = "payload_skill_assets_invalid"

McpStarter = Callable[["McpServerPlacement"], Mapping[str, Any]]
StatusWriter = Callable[[str, dict[str, Any]], None]


class ExtensionError(Exception):
    """Malformed extension section on the wire; ``reason`` is a stable code."""

    def __init__(self, reason: str, detail: str = "") -> None:
        super().__init__(reason)
        self.reason = str(reason)
        self.detail = str(detail)

    def __str__(self) -> str:
        return f"{self.reason}:{self.detail}" if self.detail else self.reason


@dataclass(frozen=True, slots=True)
class CapabilityStatus:
    """One capability that is not fully available; the subject names it."""

    kind: str
    subject: str
    reason: str
    detail: str = ""

    def to_trace_record(self) -> dict[str, Any]:
        """The taxonomy-shaped record the host trace / guest event carries."""
        record: dict[str, Any] = {
            "kind": self.kind,
            "subject": self.subject,
            "reason": self.reason,
        }
        if self.detail:
            record["detail"] = self.detail
        return record


@dataclass(frozen=True, slots=True)
class McpServerPlacement:
    """One MCP stdio server as it crosses the boundary (argv, never a shell line)."""

    name: str
    program: str
    args: tuple[str, ...] = ()
    env: tuple[tuple[str, str], ...] = ()
    placement: McpPlacement = MCP_PLACEMENT_IN_GUEST
    credential_source: McpCredentialSource = MCP_CREDENTIAL_NONE

    def env_map(self) -> dict[str, str]:
        return {key: value for key, value in self.env}

    def to_wire(self) -> dict[str, JsonValue]:
        wire: dict[str, JsonValue] = {
            "name": self.name,
            "program": self.program,
            "placement": str(self.placement),
            "credential_source": str(self.credential_source),
        }
        if self.args:
            wire["args"] = list(self.args)
        if self.env:
            wire["env"] = self.env_map()
        return wire

    @classmethod
    def from_wire(cls, value: JsonValue) -> McpServerPlacement:
        if not isinstance(value, dict):
            raise ExtensionError(_REASON_MCP_INVALID, "entry is not an object")
        return cls(
            name=_required_text(value, "name", _REASON_MCP_INVALID),
            program=_required_text(value, "program", _REASON_MCP_INVALID),
            args=_text_tuple(value.get("args"), "args"),
            env=_text_pairs(value.get("env")),
            placement=_placement_choice(value.get("placement")),
            credential_source=_credential_choice(value.get("credential_source")),
        )


@dataclass(frozen=True, slots=True)
class SkillAsset:
    """One skill asset file; ``content`` is urlsafe base64 of the exact bytes."""

    skill_id: str
    path: str
    content: str
    encoding: str = SKILL_ASSET_ENCODING_BASE64

    def to_wire(self) -> dict[str, JsonValue]:
        return {
            "skill_id": self.skill_id,
            "path": self.path,
            "content": self.content,
            "encoding": self.encoding,
        }

    @classmethod
    def from_wire(cls, value: JsonValue) -> SkillAsset:
        if not isinstance(value, dict):
            raise ExtensionError(_REASON_ASSETS_INVALID, "entry is not an object")
        return cls(
            skill_id=_required_text(value, "skill_id", _REASON_ASSETS_INVALID),
            path=_required_text(value, "path", _REASON_ASSETS_INVALID),
            content=_required_text(value, "content", _REASON_ASSETS_INVALID),
            encoding=_asset_encoding_choice(value.get("encoding")),
        )


@dataclass(frozen=True, slots=True)
class McpSources:
    """The host's MCP configuration sources, all injectable for tests.

    ``env`` is the host process environment (inside the worker's ephemeral Vault
    window, so the materialized credentials are visible); ``registry`` is the
    MCP registry payload and ``profile_servers`` the names enabled for the task's
    profile. The manager reads the real ones; tests pass their own.
    """

    env: Mapping[str, str] = field(default_factory=dict)
    registry: Mapping[str, JsonValue] = field(default_factory=dict)
    profile_servers: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class TaskExtensions:
    """Everything todo 31 adds to one dispatch: placements, assets, statuses."""

    mcp_servers: tuple[McpServerPlacement, ...] = ()
    skill_assets: tuple[SkillAsset, ...] = ()
    statuses: tuple[CapabilityStatus, ...] = ()


# ── host side: collection ────────────────────────────────────────────────────


def collect_task_extensions(
    host_artifact_workdir: str, *, mcp: McpSources | None = None,
    skill_sources: Mapping[str, str] | None = None,
) -> TaskExtensions:
    """Resolve the host's MCP servers and skill assets for one dispatch.

    Pure with respect to host state: the MCP sources are injected by the caller
    (the manager reads the real ones), so tests never touch the machine's
    configuration.
    """
    sources = mcp if mcp is not None else McpSources()
    host_env: Mapping[str, str] = sources.env
    registry = sources.registry
    statuses: list[CapabilityStatus] = []
    specs: dict[str, McpServerPlacement] = {}
    order: list[str] = []

    def place(name: str, spec: McpServerPlacement | None, status: CapabilityStatus | None) -> None:
        if status is not None:
            statuses.append(status)
            return
        if spec is None:
            return
        if name not in specs:
            order.append(name)
        specs[name] = spec

    for name in _split_names(host_env.get("MCP_SERVERS", "")):
        place(name, *_env_var_spec(name, host_env))
    # Profile-enabled registry entries override an env-var server of the same
    # name: that is the off-mode reload order (worker reloads them last).
    for name in sources.profile_servers:
        place(str(name).strip(), *_registry_spec(str(name).strip(), registry, host_env))

    policy = ChannelConfig(port=DEFAULT_VSOCK_PORT)
    placements: list[McpServerPlacement] = []
    for name in order:
        spec = specs[name]
        violation = payload_policy_violation(
            MessageKind.TASK, {"mcp_servers": [spec.to_wire()]}, policy
        )
        if violation is not None:
            statuses.append(
                CapabilityStatus(
                    CcTraceEvent.ISOLATION_MCP_UNAVAILABLE,
                    name,
                    REASON_PAYLOAD_DENIED,
                    detail=f"reason={violation}",
                )
            )
            continue
        placements.append(spec)

    assets, asset_statuses = collect_skill_assets(host_artifact_workdir, skill_sources=skill_sources)
    statuses.extend(asset_statuses)
    return TaskExtensions(
        mcp_servers=tuple(placements),
        skill_assets=assets,
        statuses=tuple(statuses),
    )


def collect_skill_assets(
    host_artifact_workdir: str,
    *, skill_sources: Mapping[str, str] | None = None,
) -> tuple[tuple[SkillAsset, ...], tuple[CapabilityStatus, ...]]:
    """Mirror the task-local materialized skill tree, bounded by the budgets.

    Only ``<artifact_workdir>/.skill_runtime/<skill>`` is read (the tree the
    skills manager materialized for this task); the rest of the data dir is
    never touched.
    """
    runtime_root = Path(host_artifact_workdir).expanduser().resolve() / SKILL_RUNTIME_SUBDIR
    if not runtime_root.is_dir():
        return (), ()
    assets: list[SkillAsset] = []
    statuses: list[CapabilityStatus] = []
    total_bytes = 0
    try:
        with parent_handle(runtime_root / '.entry') as (directory, _):
            with os.scandir(directory) as entries:
                skill_dirs = [runtime_root / entry.name for entry in islice(entries, MAX_SKILL_ASSET_FILES + 1)]
        if len(skill_dirs) > MAX_SKILL_ASSET_FILES:
            raise ValueError('skill_tree_budget_exceeded')
    except (OSError, ValueError) as exc:
        return (), (CapabilityStatus(CcTraceEvent.ISOLATION_SKILL_ASSET_UNAVAILABLE,
                                     'skills', REASON_READ_FAILED, detail=type(exc).__name__),)
    for skill_dir in sorted(skill_dirs, key=lambda item: item.name):
        if not skill_dir.is_dir():
            continue
        roots = [skill_dir.absolute()]
        if (skill_sources or {}).get(skill_dir.name):
            roots.append(Path(skill_sources[skill_dir.name]).expanduser().resolve())
        try:
            if skill_dir.is_symlink():
                raise PermissionError('skill_root_link_rejected')
            files = _walk_files(skill_dir, roots=roots)
        except (OSError, ValueError, RuntimeError) as exc:
            statuses.append(CapabilityStatus(CcTraceEvent.ISOLATION_SKILL_ASSET_UNAVAILABLE,
                                              skill_dir.name, REASON_READ_FAILED, detail=type(exc).__name__))
            continue
        for file_path in files:
            relative = file_path.relative_to(skill_dir).as_posix()
            subject = f"{skill_dir.name}/{relative}"
            if len(assets) >= MAX_SKILL_ASSET_FILES:
                statuses.append(
                    CapabilityStatus(
                        CcTraceEvent.ISOLATION_SKILL_ASSET_UNAVAILABLE,
                        subject,
                        REASON_BUDGET_EXCEEDED,
                        detail=f"max_files={MAX_SKILL_ASSET_FILES}",
                    )
                )
                continue
            try:
                canonical = file_path.resolve(strict=True)
                if (not any(canonical.is_relative_to(root) for root in roots)
                        or any(canonical.is_relative_to(root / 'env') for root in roots)):
                    raise PermissionError('skill_source_escape')
                with open_regular_file(canonical) as stream:
                    data = stream.read(MAX_SKILL_ASSET_FILE_BYTES + 1)
            except (OSError, ValueError, RuntimeError) as exc:
                statuses.append(
                    CapabilityStatus(
                        CcTraceEvent.ISOLATION_SKILL_ASSET_UNAVAILABLE,
                        subject,
                        REASON_READ_FAILED,
                        detail=f"{type(exc).__name__}",
                    )
                )
                continue
            if len(data) > MAX_SKILL_ASSET_FILE_BYTES:
                statuses.append(
                    CapabilityStatus(
                        CcTraceEvent.ISOLATION_SKILL_ASSET_UNAVAILABLE,
                        subject,
                        REASON_FILE_TOO_LARGE,
                        detail=f"bytes={len(data)}",
                    )
                )
                continue
            if total_bytes + len(data) > MAX_SKILL_ASSET_TOTAL_BYTES:
                statuses.append(
                    CapabilityStatus(
                        CcTraceEvent.ISOLATION_SKILL_ASSET_UNAVAILABLE,
                        subject,
                        REASON_BUDGET_EXCEEDED,
                        detail=f"total_bytes>{MAX_SKILL_ASSET_TOTAL_BYTES}",
                    )
                )
                continue
            total_bytes += len(data)
            assets.append(
                SkillAsset(
                    skill_id=skill_dir.name,
                    path=relative,
                    content=base64.urlsafe_b64encode(data).decode("ascii"),
                )
            )
    return tuple(assets), tuple(statuses)


def rewrite_skill_runtime_paths(
    skills_text: str, host_artifact_workdir: str, *, guest_artifact_workdir: str
) -> str:
    """Rewrite the host skill runtime root in ``skills_text`` to its guest path."""
    host_root = str(Path(host_artifact_workdir) / SKILL_RUNTIME_SUBDIR)
    if not host_root.strip("/") or host_root not in skills_text:
        return skills_text
    return skills_text.replace(host_root, guest_skills_root(guest_artifact_workdir))


def guest_skills_root(artifact_workdir: str) -> str:
    """Where the guest writes skill assets (under its own artifact root)."""
    return str(PurePosixPath(artifact_workdir) / GUEST_SKILLS_SUBDIR)


def emit_capability_statuses(
    artifact_workdir: str,
    statuses: Sequence[CapabilityStatus],
    *,
    writer: StatusWriter | None = None,
) -> int:
    """Append host-side statuses to the authoritative trace; returns how many."""
    if not statuses:
        return 0
    write = writer if writer is not None else append_cc_backend_trace
    written = 0
    for status in statuses:
        try:
            write(artifact_workdir, status.to_trace_record())
        except OSError as exc:  # a trace concern must never fail the task itself
            logger.warning("isolation extensions: cannot append a status record: %s", exc)
            continue
        written += 1
    return written


# ── guest side: application ──────────────────────────────────────────────────


def materialize_skill_assets(
    assets: Sequence[SkillAsset], artifact_workdir: str
) -> tuple[CapabilityStatus, ...]:
    """Write the exported assets byte-for-byte under the guest skills root."""
    root = Path(artifact_workdir) / GUEST_SKILLS_SUBDIR
    statuses: list[CapabilityStatus] = []
    for asset in assets:
        subject = f"{asset.skill_id}/{asset.path}"
        target = _safe_asset_target(root, asset)
        if target is None:
            statuses.append(
                CapabilityStatus(
                    CcTraceEvent.ISOLATION_SKILL_ASSET_UNAVAILABLE,
                    subject,
                    REASON_PATH_DENIED,
                )
            )
            continue
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(_decode_asset(asset))
        except (OSError, ValueError) as exc:
            statuses.append(
                CapabilityStatus(
                    CcTraceEvent.ISOLATION_SKILL_ASSET_UNAVAILABLE,
                    subject,
                    REASON_WRITE_FAILED,
                    detail=f"{type(exc).__name__}",
                )
            )
    return tuple(statuses)


def apply_guest_mcp_servers(
    servers: Sequence[McpServerPlacement], *, starter: McpStarter | None = None
) -> tuple[CapabilityStatus, ...]:
    """Start the in-guest servers and report what could not be placed.

    The ambient ``MCP_SERVERS`` is cleared first: in kvm mode the task payload is
    the only MCP configuration, so a guest-side env file can never start a
    server with credentials the host did not intend to place.
    """
    os.environ["MCP_SERVERS"] = ""
    start = starter if starter is not None else _start_guest_server
    statuses: list[CapabilityStatus] = []
    for server in servers:
        match server.placement:
            case McpPlacement.HOST_BROKERED:
                statuses.append(
                    CapabilityStatus(
                        CcTraceEvent.ISOLATION_MCP_BROKERED_PENDING,
                        server.name,
                        REASON_HOST_BROKER_PENDING,
                    )
                )
            case McpPlacement.IN_GUEST:
                statuses.extend(_start_and_report(server, start))
            case _ as unreachable:
                assert_never(unreachable)
    return tuple(statuses)


def _start_and_report(
    server: McpServerPlacement, start: McpStarter
) -> tuple[CapabilityStatus, ...]:
    result = start(server)
    state = result.get("status") if isinstance(result, Mapping) else None
    if state == "ok":
        return ()
    detail = "invalid_starter_result"
    if isinstance(result, Mapping):
        detail = str(result.get("error") or state or "unknown")
    return (
        CapabilityStatus(
            CcTraceEvent.ISOLATION_MCP_UNAVAILABLE,
            server.name,
            REASON_START_FAILED,
            detail=f"error={detail}",
        ),
    )


def _start_guest_server(server: McpServerPlacement) -> Mapping[str, Any]:
    """Start one host-placed stdio server through the existing MCP bridge."""
    from ..mcp_bridge import reload_mcp_server_from_registry

    return reload_mcp_server_from_registry(
        server.name,
        {
            "type": "stdio",
            "command": server.program,
            "args": list(server.args),
            "env": server.env_map(),
        },
    )


# ── wire parsing (used by the todo-30 payload contract) ──────────────────────


def mcp_servers_from_wire(value: JsonValue) -> tuple[McpServerPlacement, ...]:
    """Parse the optional ``mcp_servers`` section; a wrong shape is refused."""
    if value is None:
        return ()
    if not isinstance(value, list):
        raise ExtensionError(_REASON_MCP_INVALID, "not a list")
    return tuple(McpServerPlacement.from_wire(item) for item in value)


def skill_assets_from_wire(value: JsonValue) -> tuple[SkillAsset, ...]:
    """Parse the optional ``skill_assets`` section; a wrong shape is refused."""
    if value is None:
        return ()
    if not isinstance(value, list):
        raise ExtensionError(_REASON_ASSETS_INVALID, "not a list")
    return tuple(SkillAsset.from_wire(item) for item in value)


# ── internals ────────────────────────────────────────────────────────────────


def _env_var_spec(
    name: str, env: Mapping[str, str]
) -> tuple[McpServerPlacement | None, CapabilityStatus | None]:
    upper = name.upper().replace("-", "_")
    program = str(env.get(f"MCP_{upper}_COMMAND") or "").strip()
    if not program:
        return None, CapabilityStatus(
            CcTraceEvent.ISOLATION_MCP_UNAVAILABLE, name, REASON_COMMAND_MISSING
        )
    args = tuple(str(env.get(f"MCP_{upper}_ARGS") or "").split())
    prefix = f"MCP_{upper}_ENV_"
    configured = {
        key[len(prefix) :]: str(value)
        for key, value in env.items()
        if key.startswith(prefix)
    }
    return _placement(
        name,
        program,
        args,
        configured,
        _truthy(env.get(f"MCP_{upper}_HOST_BROKERED")),
        env,
    )


def _registry_spec(
    name: str, registry: Mapping[str, JsonValue], env: Mapping[str, str]
) -> tuple[McpServerPlacement | None, CapabilityStatus | None]:
    entry = registry.get(name)
    if not isinstance(entry, Mapping):
        return None, CapabilityStatus(
            CcTraceEvent.ISOLATION_MCP_UNAVAILABLE, name, REASON_NOT_REGISTERED
        )
    if entry.get("disabled") is True:
        # Off-mode parity: a globally disabled server stays off, by config.
        return None, None
    if str(entry.get("type") or "").strip().lower() != "stdio":
        return None, CapabilityStatus(
            CcTraceEvent.ISOLATION_MCP_UNAVAILABLE, name, REASON_UNSUPPORTED_TYPE
        )
    program = str(entry.get("command") or "").strip()
    if not program:
        return None, CapabilityStatus(
            CcTraceEvent.ISOLATION_MCP_UNAVAILABLE, name, REASON_COMMAND_MISSING
        )
    raw_args = entry.get("args") if isinstance(entry.get("args"), list) else []
    args = tuple(str(item) for item in raw_args)
    raw_env = entry.get("env") if isinstance(entry.get("env"), Mapping) else {}
    configured = {str(key): str(value) for key, value in raw_env.items()}
    upper = name.upper().replace("-", "_")
    brokered = entry.get("host_brokered") is True or _truthy(
        env.get(f"MCP_{upper}_HOST_BROKERED")
    )
    return _placement(name, program, args, configured, brokered, env)


def _placement(
    name: str,
    program: str,
    args: tuple[str, ...],
    configured_env: Mapping[str, str],
    brokered: bool,
    host_env: Mapping[str, str],
) -> tuple[McpServerPlacement | None, CapabilityStatus | None]:
    if brokered:
        return (
            McpServerPlacement(
                name=name,
                program=program,
                args=args,
                placement=McpPlacement.HOST_BROKERED,
                credential_source=McpCredentialSource.HOST_BROKER,
            ),
            None,
        )
    required = _credential_keys(name)
    credentials = {
        key: str(host_env[key]) for key in required if str(host_env.get(key, "")).strip()
    }
    if required and not credentials:
        return None, CapabilityStatus(
            CcTraceEvent.ISOLATION_MCP_UNAVAILABLE,
            name,
            REASON_CREDENTIAL_MISSING,
            detail=", ".join(required),
        )
    merged = {**configured_env, **credentials}
    return (
        McpServerPlacement(
            name=name,
            program=program,
            args=args,
            env=tuple(sorted(merged.items())),
            credential_source=(
                McpCredentialSource.EPHEMERAL_ENV
                if credentials
                else McpCredentialSource.NONE
            ),
        ),
        None,
    )


def _credential_keys(name: str) -> tuple[str, ...]:
    """The credential keys ``mcp_bridge`` requires for this server (public seam)."""
    from ..mcp_bridge import mcp_server_env_requirements

    return mcp_server_env_requirements(name)


def _split_names(raw: str) -> list[str]:
    return [item.strip() for item in str(raw or "").split(",") if item.strip()]


def _truthy(value: object) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _walk_files(root: Path, *, roots: Sequence[Path]) -> list[Path]:
    """Walk only authorized skill sources, with finite entry and cycle budgets."""
    files: list[Path] = []
    stack = [(root, frozenset())]
    inspected = 0
    while stack:
        path, ancestors = stack.pop()
        canonical = path.resolve(strict=True)
        if (not any(canonical.is_relative_to(base) for base in roots)
                or any(canonical.is_relative_to(base / 'env') for base in roots)):
            raise PermissionError('skill_source_escape')
        if canonical.is_dir():
            if canonical in ancestors or len(ancestors) >= 64:
                raise ValueError('skill_source_cycle')
            with parent_handle(canonical / '.entry') as (directory, _):
                with os.scandir(directory) as entries:
                    children = []
                    for entry in entries:
                        inspected += 1
                        if inspected > MAX_SKILL_ASSET_FILES * 4:
                            raise ValueError('skill_tree_budget_exceeded')
                        children.append(path / entry.name)
            stack.extend((child, ancestors | {canonical}) for child in reversed(sorted(children)))
        else:
            files.append(path)
    return sorted(files, key=lambda item: item.as_posix())


def _safe_asset_target(root: Path, asset: SkillAsset) -> Path | None:
    """The guest path for one asset, or ``None`` when it would escape the root."""
    skill_id = asset.skill_id.strip()
    if (
        not skill_id
        or "/" in skill_id
        or "\\" in skill_id
        or skill_id in {".", ".."}
    ):
        return None
    relative = asset.path.strip()
    if not relative or relative.startswith("/") or "\\" in relative:
        return None
    parts = PurePosixPath(relative).parts
    if not parts or any(part in {"", ".", ".."} for part in parts):
        return None
    return root.joinpath(skill_id, *parts)


def _decode_asset(asset: SkillAsset) -> bytes:
    if asset.encoding != SKILL_ASSET_ENCODING_BASE64:
        raise ValueError(f"unsupported asset encoding {asset.encoding!r}")
    return base64.urlsafe_b64decode(asset.content.encode("ascii"))


def _required_text(payload: Mapping[str, JsonValue], key: str, reason: str) -> str:
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise ExtensionError(reason, key)
    return value


def _text_tuple(value: JsonValue, key: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ExtensionError(_REASON_MCP_INVALID, key)
    return tuple(str(item) for item in value)


def _text_pairs(value: JsonValue) -> tuple[tuple[str, str], ...]:
    if value is None:
        return ()
    if not isinstance(value, dict):
        raise ExtensionError(_REASON_MCP_INVALID, "env")
    pairs: list[tuple[str, str]] = []
    for key, item in value.items():
        if not isinstance(item, str):
            raise ExtensionError(_REASON_MCP_INVALID, f"env.{key}")
        pairs.append((str(key), item))
    return tuple(pairs)


def _placement_choice(value: JsonValue) -> McpPlacement:
    if value is None:
        return McpPlacement.IN_GUEST
    if not isinstance(value, str):
        raise ExtensionError(_REASON_MCP_INVALID, f"placement={value!r}")
    try:
        return McpPlacement(value)
    except ValueError as exc:
        raise ExtensionError(_REASON_MCP_INVALID, f"placement={value!r}") from exc


def _credential_choice(value: JsonValue) -> McpCredentialSource:
    if value is None:
        return McpCredentialSource.NONE
    if not isinstance(value, str):
        raise ExtensionError(_REASON_MCP_INVALID, f"credential_source={value!r}")
    try:
        return McpCredentialSource(value)
    except ValueError as exc:
        raise ExtensionError(_REASON_MCP_INVALID, f"credential_source={value!r}") from exc


def _asset_encoding_choice(value: JsonValue) -> str:
    if value is None:
        return SKILL_ASSET_ENCODING_BASE64
    if value != SKILL_ASSET_ENCODING_BASE64:
        raise ExtensionError(_REASON_ASSETS_INVALID, f"encoding={value!r}")
    return SKILL_ASSET_ENCODING_BASE64
