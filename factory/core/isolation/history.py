"""Versioned guest task payload: the host owns history, the guest rehydrates (todo 30).

The durable conversation record is host-side only: the session JSONL (written by
``mobile_bridge/service.py`` and ``cli_chat.py``), the task DB, the trace file
and the checkpoints. The guest holds no history of its own - it receives ONE
versioned task payload per turn and rehydrates an ephemeral view from it. This
module is the single owner of that payload schema, so the host builder
(``manager``) and the guest parser (``guest_task``) cannot drift:

* :func:`build_wire_task_payload` - the host assembly, the only place the wire
  keys are written (``HostTaskInput`` is the typed input);
* :func:`rehydrate_payload` - the guest rehydration entry point: it validates the
  version, refuses anything it cannot interpret and returns the typed
  ``task_metadata`` the executor consumes.

Which entries rehydrate the guest (the complete list; the surrounding task fields
are parsed by :class:`~factory.core.isolation.guest_task.GuestTask`):

* session messages - ``history_messages`` (the host-selected tail of the session
  JSONL; the selection rule stays host-side, the guest only replays it);
* ``system_prompt`` - assembled on the host (skills/foundation baked in);
* ``skills_text`` and ``foundation_context`` - carried typed for the same seam;
* limits - ``max_tokens`` and ``timeout_s``;
* approval policy - :data:`APPROVAL_POLICY` (``host_resolved``): the guest never
  resolves its own approvals, it forwards blocked calls to the host relay;
* ``task_metadata`` - the executor-needed rehydration subset: ``must_write_skills``,
  ``approved_tool_calls``, ``approved_session_rules``, ``forced_tool_call``,
  ``approval_context_messages``, ``model_context_window``, and the read-only
  policy lists ``restrict_tools_to`` / ``block_tool_names`` (plan mode must reach
  the guest executor, otherwise the guest would not enforce it);
* optional capability sections (todo 31, emitted only when non-empty):
  ``mcp_servers`` (the host-resolved stdio placements that run in the guest) and
  ``skill_assets`` (the bounded read-only export of the task-local skill tree);
* the optional ``egress`` section (todo 40a): the host-rendered per-session
  nftables ruleset plus the pinned-address map the guest applies before the loop
  starts. Additive like the capability sections: an absent section leaves the
  guest on the boot-time default-deny baseline, never on an unrestricted path.

Payload versions:

* ``0`` - legacy: no ``payload_version`` field (the pre-todo-30 manager);
* ``1`` - explicit version, resume metadata in ``approval_resume`` only;
* ``2`` - current: adds ``approval_policy`` and the ``task_metadata`` section.
  ``approval_resume`` is still emitted as the v1-compatible view of the SAME
  typed metadata, so a guest that only understands v1 keeps working. The todo-31
  capability sections are optional and additive: an empty section is absent, so
  a payload without MCP servers/assets is byte-identical to the todo-30 shape.

Fail-closed rules: an unknown version raises
:data:`REASON_VERSION_UNSUPPORTED` (``payload_version_unsupported``) and a v2
payload missing ``task_metadata`` or ``approval_policy`` is refused - the guest
never guesses, never partially applies a payload, and never keeps durable
conversation history. A malformed ``mcp_servers``/``skill_assets`` section is
refused the same way (``payload_mcp_servers_invalid`` / ``payload_skill_assets_invalid``).
"""

# allow: SIZE_OK - one schema in one file: versioning, rehydration and assembly
# are the same contract, and splitting them would let the host builder and the
# guest parser drift (exactly what todo 30 forbids). Most lines are the
# docstrings that define which entries rehydrate the guest.


from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Final

from .channel import JsonValue
from .session_egress import EgressWireError, SessionEgress
from .git_gateway_contract import GitGatewayRoute
from .apk_delivery import validate_endpoint
from .extensions import (
    ExtensionError,
    McpServerPlacement,
    SkillAsset,
    mcp_servers_from_wire,
    skill_assets_from_wire,
)

__all__ = [
    "APPROVAL_POLICY",
    "LEGACY_PAYLOAD_VERSION",
    "PAYLOAD_VERSION",
    "REASON_EGRESS_INVALID",
    "SUPPORTED_PAYLOAD_VERSIONS",
    "HostTaskInput",
    "PayloadError",
    "RehydratedPayload",
    "RehydratedTaskMetadata",
    "build_wire_task_payload",
    "legacy_resume_metadata",
    "parse_payload_version",
    "rehydrate_payload",
    "rehydrate_task_metadata",
]

#: The wire shape this guest understands and the host builder emits.
PAYLOAD_VERSION: Final[int] = 2
#: Pre-todo-30 payloads carried no ``payload_version`` field at all.
LEGACY_PAYLOAD_VERSION: Final[int] = 0
#: The first explicitly versioned payload (resume metadata in ``approval_resume``).
RESUME_PAYLOAD_VERSION: Final[int] = 1
SUPPORTED_PAYLOAD_VERSIONS: Final[frozenset[int]] = frozenset(
    {LEGACY_PAYLOAD_VERSION, RESUME_PAYLOAD_VERSION, PAYLOAD_VERSION}
)

#: The only supported approval policy: the host resolves every blocked call
#: (plan todo 9); a payload claiming otherwise is refused.
APPROVAL_POLICY: Final[str] = "host_resolved"

REASON_VERSION_UNSUPPORTED: Final[str] = "payload_version_unsupported"
REASON_VERSION_INVALID: Final[str] = "payload_version_invalid"
REASON_METADATA_MISSING: Final[str] = "payload_metadata_missing"
REASON_METADATA_INVALID: Final[str] = "payload_metadata_invalid"
REASON_POLICY_MISSING: Final[str] = "approval_policy_missing"
REASON_POLICY_UNSUPPORTED: Final[str] = "approval_policy_unsupported"
REASON_EGRESS_INVALID: Final[str] = "payload_egress_invalid"


class PayloadError(Exception):
    """Malformed or unsupported payload section; ``reason`` is a stable code."""

    def __init__(self, reason: str, detail: str = "") -> None:
        super().__init__(reason)
        self.reason = str(reason)
        self.detail = str(detail)

    def __str__(self) -> str:
        return f"{self.reason}:{self.detail}" if self.detail else self.reason


@dataclass(frozen=True, slots=True)
class RehydratedTaskMetadata:
    """The executor-relevant ``task_metadata`` subset, typed on both sides.

    The host builds it from the task metadata (paths already mapped host->guest);
    the guest parses it back from the wire. :meth:`to_dict` is the exact
    ``metadata`` mapping the cc-backend executor consumes, so a rehydrated call
    matches the off-mode one.
    """

    must_write_skills: tuple[str, ...] = ()
    approved_tool_calls: tuple[str, ...] = ()
    approved_session_rules: tuple[JsonValue, ...] = ()
    forced_tool_call: JsonValue = None
    approval_context_messages: tuple[JsonValue, ...] = ()
    model_context_window: int | None = None
    restrict_tools_to: tuple[str, ...] = ()
    block_tool_names: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        """The executor ``metadata`` dict; empty fields are omitted (off-mode parity)."""
        metadata: dict[str, Any] = dict(self.resume_dict())
        if self.model_context_window:
            metadata["model_context_window"] = int(self.model_context_window)
        if self.must_write_skills:
            metadata["must_write_skills"] = list(self.must_write_skills)
        if self.restrict_tools_to:
            metadata["restrict_tools_to"] = list(self.restrict_tools_to)
        if self.block_tool_names:
            metadata["block_tool_names"] = list(self.block_tool_names)
        if self.approval_context_messages:
            metadata["approval_context_messages"] = [
                dict(item)
                for item in self.approval_context_messages
                if isinstance(item, dict)
            ]
        return metadata

    def resume_dict(self) -> dict[str, JsonValue]:
        """The v1 ``approval_resume`` view: the three legacy keys only."""
        resume: dict[str, JsonValue] = {}
        forced = self.forced_tool_call
        if isinstance(forced, dict) and forced:
            resume["forced_tool_call"] = dict(forced)
        if self.approved_tool_calls:
            resume["approved_tool_calls"] = [
                str(item) for item in self.approved_tool_calls
            ]
        if self.approved_session_rules:
            resume["approved_session_rules"] = [
                dict(rule)
                for rule in self.approved_session_rules
                if isinstance(rule, dict)
            ]
        return resume

    @classmethod
    def from_wire_section(cls, value: JsonValue) -> RehydratedTaskMetadata:
        """Parse a v2 ``task_metadata`` section; a wrong shape is refused."""
        if not isinstance(value, dict):
            raise PayloadError(REASON_METADATA_INVALID, "task_metadata")
        return cls(
            must_write_skills=_text_tuple(value.get("must_write_skills"), "must_write_skills"),
            approved_tool_calls=_text_tuple(
                value.get("approved_tool_calls"), "approved_tool_calls"
            ),
            approved_session_rules=_dict_tuple(
                value.get("approved_session_rules"), "approved_session_rules"
            ),
            forced_tool_call=_forced_tool_call(value.get("forced_tool_call")),
            approval_context_messages=_dict_tuple(
                value.get("approval_context_messages"), "approval_context_messages"
            ),
            model_context_window=_positive_int(
                value.get("model_context_window"), "model_context_window"
            ),
            restrict_tools_to=_text_tuple(
                value.get("restrict_tools_to"), "restrict_tools_to"
            ),
            block_tool_names=_text_tuple(
                value.get("block_tool_names"), "block_tool_names"
            ),
        )

    @classmethod
    def from_legacy_resume(cls, value: JsonValue) -> RehydratedTaskMetadata:
        """Parse the v0/v1 ``approval_resume`` view (todo-9 guest behavior kept)."""
        if not isinstance(value, dict):
            raise PayloadError(REASON_METADATA_INVALID, "approval_resume")
        forced = value.get("forced_tool_call")
        calls = value.get("approved_tool_calls")
        rules = value.get("approved_session_rules")
        return cls(
            forced_tool_call=dict(forced) if isinstance(forced, dict) and forced else None,
            approved_tool_calls=(
                tuple(str(item) for item in calls) if isinstance(calls, list) else ()
            ),
            approved_session_rules=(
                tuple(dict(rule) for rule in rules if isinstance(rule, dict))
                if isinstance(rules, list)
                else ()
            ),
        )


@dataclass(frozen=True, slots=True)
class RehydratedPayload:
    """One version-checked payload: the version, the policy and the metadata."""

    payload_version: int
    approval_policy: str
    task_metadata: RehydratedTaskMetadata
    mcp_servers: tuple[McpServerPlacement, ...] = ()
    skill_assets: tuple[SkillAsset, ...] = ()
    egress: SessionEgress | None = None
    git_gateway: GitGatewayRoute | None = None
    apk_delivery_url: str = ""


def parse_payload_version(payload: Mapping[str, JsonValue]) -> int:
    """The payload version, or a refusal; an absent field means the legacy version."""
    raw = payload.get("payload_version")
    if raw is None:
        return LEGACY_PAYLOAD_VERSION
    if isinstance(raw, bool) or not isinstance(raw, int):
        raise PayloadError(REASON_VERSION_INVALID)
    if raw not in SUPPORTED_PAYLOAD_VERSIONS:
        raise PayloadError(REASON_VERSION_UNSUPPORTED, str(raw))
    return raw


def rehydrate_task_metadata(
    payload: Mapping[str, JsonValue], *, version: int
) -> RehydratedTaskMetadata:
    """The executor metadata subset: the v2 section, or the v1 resume view."""
    if version >= PAYLOAD_VERSION:
        section = payload.get("task_metadata")
        if section is None:
            raise PayloadError(REASON_METADATA_MISSING, "task_metadata")
        return RehydratedTaskMetadata.from_wire_section(section)
    resume = payload.get("approval_resume")
    if resume is None:
        return RehydratedTaskMetadata()
    return RehydratedTaskMetadata.from_legacy_resume(resume)


def rehydrate_payload(payload: Mapping[str, JsonValue]) -> RehydratedPayload:
    """Validate one task payload and rehydrate the host-owned entries (the seam)."""
    version = parse_payload_version(payload)
    return RehydratedPayload(
        payload_version=version,
        approval_policy=_rehydrate_approval_policy(payload, version=version),
        task_metadata=rehydrate_task_metadata(payload, version=version),
        mcp_servers=_rehydrate_mcp_servers(payload),
        skill_assets=_rehydrate_skill_assets(payload),
        egress=_rehydrate_egress(payload),
        git_gateway=_rehydrate_git_gateway(payload),
        apk_delivery_url=_rehydrate_apk_delivery(payload),
    )


def _rehydrate_apk_delivery(payload):
    if "apk_delivery_url" not in payload:
        return ""
    try:
        return validate_endpoint(payload["apk_delivery_url"])
    except (ValueError, TypeError) as exc:
        raise PayloadError("payload_apk_delivery_invalid", "apk_delivery_url") from exc


def _rehydrate_git_gateway(payload):
    value = payload.get("git_gateway")
    if value is None:
        return None
    try:
        return GitGatewayRoute.from_wire(value)
    except (ValueError, TypeError) as exc:
        raise PayloadError("payload_git_gateway_invalid", "git_gateway") from exc


def _rehydrate_egress(payload: Mapping[str, JsonValue]) -> SessionEgress | None:
    value = payload.get("egress")
    if value is None:
        return None
    try:
        return SessionEgress.from_wire(value)
    except EgressWireError as exc:
        raise PayloadError(REASON_EGRESS_INVALID, str(exc)) from exc


def _rehydrate_mcp_servers(payload: Mapping[str, JsonValue]) -> tuple[McpServerPlacement, ...]:
    try:
        return mcp_servers_from_wire(payload.get("mcp_servers"))
    except ExtensionError as exc:
        raise PayloadError(exc.reason, exc.detail) from exc


def _rehydrate_skill_assets(payload: Mapping[str, JsonValue]) -> tuple[SkillAsset, ...]:
    try:
        return skill_assets_from_wire(payload.get("skill_assets"))
    except ExtensionError as exc:
        raise PayloadError(exc.reason, exc.detail) from exc


def legacy_resume_metadata(value: JsonValue) -> dict[str, Any]:
    """Backward-compatible helper: ``{}`` for anything that is not a resume view."""
    if not isinstance(value, dict):
        return {}
    return RehydratedTaskMetadata.from_legacy_resume(value).to_dict()


def _rehydrate_approval_policy(
    payload: Mapping[str, JsonValue], *, version: int
) -> str:
    if version < PAYLOAD_VERSION:
        return APPROVAL_POLICY
    value = payload.get("approval_policy")
    if value is None:
        raise PayloadError(REASON_POLICY_MISSING, "approval_policy")
    if value != APPROVAL_POLICY:
        raise PayloadError(REASON_POLICY_UNSUPPORTED, str(value))
    return APPROVAL_POLICY


def _text_tuple(value: JsonValue, key: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise PayloadError(REASON_METADATA_INVALID, key)
    if not all(isinstance(item, str) for item in value):
        raise PayloadError(REASON_METADATA_INVALID, key)
    return tuple(str(item) for item in value)


def _dict_tuple(value: JsonValue, key: str) -> tuple[JsonValue, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise PayloadError(REASON_METADATA_INVALID, key)
    if not all(isinstance(item, dict) for item in value):
        raise PayloadError(REASON_METADATA_INVALID, key)
    return tuple(value)


def _forced_tool_call(value: JsonValue) -> JsonValue:
    if value is None:
        return None
    if not isinstance(value, dict):
        raise PayloadError(REASON_METADATA_INVALID, "forced_tool_call")
    return dict(value)


def _positive_int(value: JsonValue, key: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        raise PayloadError(REASON_METADATA_INVALID, key)
    return int(value)


@dataclass(frozen=True, slots=True)
class HostTaskInput:
    """Everything one outbound task frame is assembled from.

    Path-bearing fields are the guest paths (the single workspace export);
    ``task_metadata`` carries values already mapped host->guest by the caller.
    """

    task_id: str
    profile: str
    model: str
    base_url: str
    api_format: str
    system_prompt: str
    user_message: str
    skills_text: str
    foundation_context: str
    filesystem_mode: str
    guard_mode: str
    max_tokens: int
    timeout_s: int
    workdir: str
    artifact_workdir: str
    trailing_context_text: str = ""
    current_session: str = ""
    source_platform: str = ""
    source_chat_id: str = ""
    input_files: tuple[JsonValue, ...] = ()
    enabled_tools: tuple[JsonValue, ...] = ()
    history_messages: tuple[JsonValue, ...] = ()
    checkpoint_config: JsonValue = None
    task_metadata: RehydratedTaskMetadata = field(default_factory=RehydratedTaskMetadata)
    mcp_servers: tuple[McpServerPlacement, ...] = ()
    skill_assets: tuple[SkillAsset, ...] = ()
    egress: SessionEgress | None = None
    git_gateway: GitGatewayRoute | None = None
    apk_delivery_url: str = ""


def build_wire_task_payload(task: HostTaskInput) -> dict[str, JsonValue]:
    """Assemble the one versioned task frame (the only place the schema is written)."""
    wire: dict[str, JsonValue] = {
        "payload_version": PAYLOAD_VERSION,
        "approval_policy": APPROVAL_POLICY,
        "task_metadata": task.task_metadata.to_dict(),
        "task_id": task.task_id,
        "profile": task.profile,
        "model": task.model,
        "base_url": task.base_url,
        "api_format": task.api_format,
        "system_prompt": task.system_prompt,
        "user_message": task.user_message,
        "skills_text": task.skills_text,
        "foundation_context": task.foundation_context,
        "filesystem_mode": task.filesystem_mode,
        "guard_mode": task.guard_mode,
        "max_tokens": task.max_tokens,
        "timeout_s": task.timeout_s,
        "workdir": task.workdir,
        "artifact_workdir": task.artifact_workdir,
        "trailing_context_text": task.trailing_context_text,
    }
    for key, text in (
        ("current_session", task.current_session),
        ("source_platform", task.source_platform),
        ("source_chat_id", task.source_chat_id),
    ):
        if text.strip():
            wire[key] = text
    for key, items in (
        ("input_files", task.input_files),
        ("enabled_tools", task.enabled_tools),
        ("history_messages", task.history_messages),
    ):
        if items:
            wire[key] = list(items)
    checkpoint = task.checkpoint_config
    if isinstance(checkpoint, dict) and checkpoint:
        wire["checkpoint_config"] = checkpoint
    if task.mcp_servers:
        wire["mcp_servers"] = [server.to_wire() for server in task.mcp_servers]
    if task.skill_assets:
        wire["skill_assets"] = [asset.to_wire() for asset in task.skill_assets]
    if task.apk_delivery_url:
        wire["apk_delivery_url"] = task.apk_delivery_url
    if task.git_gateway is not None:
        wire["git_gateway"] = task.git_gateway.to_wire()
    if task.egress is not None:
        wire["egress"] = task.egress.to_wire()
    resume = task.task_metadata.resume_dict()
    if resume:
        wire["approval_resume"] = resume
    return wire
