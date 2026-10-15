"""Typed boundary for the in-guest task payload (plan todo 7; todo 30 extends it).

The host sends one ``task`` frame per turn; :meth:`GuestTask.from_wire` is the
single place that turns that untyped JSON object into a typed value the runner
can pass to the cc-backend loop. The versioned schema, the payload versioning and
the ``task_metadata`` rehydration live in :mod:`factory.core.isolation.history`
(todo 30), so the host builder and this parser share one contract. This module
also owns the JSON narrowing helpers shared by the outbound event/result modules.

Fail-closed rules:

* the version is validated by :func:`history.parse_payload_version`: an absent
  field means the legacy version, and an unparsable or unsupported version
  refuses the task instead of guessing (``payload_version_unsupported``);
* a v2 payload must carry ``approval_policy`` (host-resolved approvals only) and
  the ``task_metadata`` section; a missing section is refused, never defaulted;
* ``workdir``/``artifact_workdir`` must resolve under a guest root
  (``/workspace``/``/tmp``), so a payload can never aim the loop at a host path;
* every required field must be present and typed; a missing/wrong-typed field
  raises :class:`GuestTaskError` with a stable reason code.

Prompt assembly is deliberately NOT repeated here: the host already baked
skills/foundation into ``system_prompt``. The payload's ``skills_text`` and
``foundation_context`` are carried typed, and ``task_metadata`` (todo 30) carries
exactly the executor metadata the guest consumes: ``must_write_skills``,
``approved_tool_calls``, ``approved_session_rules``, ``forced_tool_call``,
``approval_context_messages`` and the read-only policy lists
``restrict_tools_to`` / ``block_tool_names`` (plan mode reaches the guest here).
The optional ``mcp_servers``/``skill_assets``
sections (todo 31) are carried typed too: the runner applies the MCP placements
and materializes the skill assets before the loop starts, and a malformed
section is refused by the payload contract, never defaulted. The optional
``egress`` section (todo 40a) is the host-rendered per-session ruleset the
runner applies before the loop starts; without it the guest keeps the boot-time
default-deny baseline. The guest holds no durable history: this typed task is an
ephemeral rehydration of the host's payload.
"""

from __future__ import annotations

import posixpath
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any, Final

from .channel import JsonValue, ChannelError
from .task_transfer import unpack_task
from .session_egress import SessionEgress
from .git_gateway_contract import GitGatewayRoute
from .extensions import McpServerPlacement, SkillAsset
from .history import (
    LEGACY_PAYLOAD_VERSION,
    PAYLOAD_VERSION,
    SUPPORTED_PAYLOAD_VERSIONS,
    PayloadError,
    RehydratedPayload,
    RehydratedTaskMetadata,
    legacy_resume_metadata as approval_resume_metadata,
    rehydrate_payload,
)

__all__ = [
    "DEFAULT_GUEST_ARTIFACT_DIR",
    "DEFAULT_GUEST_ROOTS",
    "LEGACY_PAYLOAD_VERSION",
    "PAYLOAD_VERSION",
    "SUPPORTED_PAYLOAD_VERSIONS",
    "GuestTask",
    "GuestTaskError",
    "LogFn",
    "UserInputProvider",
    "approval_resume_metadata",
    "narrow_int_or_zero",
    "narrow_json_object",
    "narrow_json_value",
]

DEFAULT_GUEST_ROOTS: Final[tuple[str, ...]] = ("/workspace", "/tmp")
# Guest-side artifact dir (traces, checkpoints); mirrors
# ``manager.GUEST_ARTIFACT_DIR`` (a regression test pins the two together).
DEFAULT_GUEST_ARTIFACT_DIR: Final[str] = "/tmp/igorek-artifacts"

LogFn = Callable[[str], None]
#: Guest-side user-input seam (todo 10): form payload in, answers out, ``None``
#: when the person cancelled the form.
UserInputProvider = Callable[[dict[str, Any]], dict[str, Any] | None]


class GuestTaskError(Exception):
    """Malformed or unsupported task payload; ``reason`` is a stable code."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = str(reason)

    def __str__(self) -> str:
        return self.reason


@dataclass(frozen=True, slots=True)
class GuestTask:
    """One validated task frame, assembled for the in-guest executor."""

    payload_version: int
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
    workdir: str
    artifact_workdir: str
    max_tokens: int
    timeout_s: int
    current_session: str
    source_platform: str
    source_chat_id: str
    trailing_context_text: str
    input_files: tuple[str, ...]
    history_messages: tuple[JsonValue, ...]
    enabled_tools: tuple[JsonValue, ...]
    checkpoint_config: JsonValue
    task_metadata: RehydratedTaskMetadata
    mcp_servers: tuple[McpServerPlacement, ...] = ()
    skill_assets: tuple[SkillAsset, ...] = ()
    egress: SessionEgress | None = None
    git_gateway: GitGatewayRoute | None = None
    apk_delivery_url: str = ""

    @classmethod
    def from_wire(
        cls, payload: Mapping[str, JsonValue], *, guest_roots: tuple[str, ...]
    ) -> GuestTask:
        """Parse one ``task`` payload; raises :class:`GuestTaskError` when unsafe."""
        try:
            if "task_payload_file" in payload:
                _require_guest_root("workdir", _required_text(payload, "workdir"), guest_roots)
                _require_guest_root("artifact_workdir", _required_text(payload, "artifact_workdir"), guest_roots)
            payload = unpack_task(payload)
        except ChannelError as exc:
            raise GuestTaskError(exc.reason) from exc
        workdir = _required_text(payload, "workdir")
        artifact_workdir = _required_text(payload, "artifact_workdir")
        _require_guest_root("workdir", workdir, guest_roots)
        _require_guest_root("artifact_workdir", artifact_workdir, guest_roots)
        rehydrated = _rehydrate_or_refuse(payload)
        return cls(
            payload_version=rehydrated.payload_version,
            task_id=_required_text(payload, "task_id"),
            profile=_required_text(payload, "profile"),
            model=_required_text(payload, "model"),
            base_url=_required_text(payload, "base_url"),
            api_format=_required_text(payload, "api_format"),
            system_prompt=_required_text(payload, "system_prompt"),
            user_message=_required_text(payload, "user_message"),
            skills_text=_required_text(payload, "skills_text"),
            foundation_context=_required_text(payload, "foundation_context"),
            filesystem_mode=_required_text(payload, "filesystem_mode"),
            guard_mode=_required_text(payload, "guard_mode"),
            workdir=workdir,
            artifact_workdir=artifact_workdir,
            max_tokens=_required_int(payload, "max_tokens"),
            timeout_s=_required_int(payload, "timeout_s"),
            current_session=_optional_text(payload, "current_session"),
            source_platform=_optional_text(payload, "source_platform"),
            source_chat_id=_optional_text(payload, "source_chat_id"),
            trailing_context_text=_optional_text(payload, "trailing_context_text"),
            input_files=tuple(_optional_text_list(payload, "input_files")),
            history_messages=tuple(_optional_object_list(payload, "history_messages")),
            enabled_tools=tuple(_optional_object_list(payload, "enabled_tools")),
            checkpoint_config=_optional_object(payload, "checkpoint_config"),
            task_metadata=rehydrated.task_metadata,
            mcp_servers=rehydrated.mcp_servers,
            skill_assets=rehydrated.skill_assets,
            egress=rehydrated.egress,
            git_gateway=rehydrated.git_gateway,
            apk_delivery_url=rehydrated.apk_delivery_url,
        )

    def executor_kwargs(
        self,
        log_fn: LogFn,
        *,
        user_input_provider: UserInputProvider | None = None,
        web_tool_provider: Callable[[str, dict[str, Any]], str] | None = None,
        session_search_provider: Callable[[dict[str, Any]], str] | None = None,
        vault_store_provider: Callable[[dict[str, Any]], str] | None = None,
        memory_forget_provider: Callable[[dict[str, Any]], str] | None = None,
        pending_steer_provider: Callable[[], list[dict[str, Any]]] | None = None,
        priority_steer_provider: Callable[[], list[dict[str, Any]]] | None = None,
        data_root: str | None = None,
    ) -> dict[str, Any]:
        """The ``execute_cc_backend_task`` call for this task (todo 30's seam).

        ``user_input_provider`` is the guest runner's user-input bridge (todo 10):
        when present the executor's ``request_user_input`` tool asks the host
        instead of the (host-only) store or a terminal. ``data_root`` is the
        durable root inside the shared workspace (todo 26): with it, the
        checkpoint shadow repos and the ``session_changes`` records are produced
        inside the export and survive a VM teardown.
        """
        kwargs: dict[str, Any] = {
            "base_url": self.base_url,
            "model": self.model,
            "api_format": self.api_format,
            "profile": self.profile,
            "task_id": self.task_id,
            "system_prompt": self.system_prompt,
            "user_message": self.user_message,
            "workdir": self.workdir,
            "artifact_workdir": self.artifact_workdir,
            "filesystem_mode": self.filesystem_mode,
            "guard_mode": self.guard_mode,
            "max_tokens": self.max_tokens,
            "timeout_s": self.timeout_s,
            "log_fn": log_fn,
            # The guest cannot approve its own blocked tools: the approval path
            # stays enabled and the host resolves it (todo 9).
            "approvals_enabled_provider": _approvals_enabled,
        }
        if self.current_session:
            kwargs["current_session"] = self.current_session
        if self.source_platform:
            kwargs["source_platform"] = self.source_platform
        if self.source_chat_id:
            kwargs["source_chat_id"] = self.source_chat_id
        if self.trailing_context_text:
            kwargs["trailing_context_text"] = self.trailing_context_text
        if self.input_files:
            kwargs["input_files"] = list(self.input_files)
        if self.history_messages:
            kwargs["history_messages"] = list(self.history_messages)
        if self.enabled_tools:
            kwargs["enabled_tools"] = list(self.enabled_tools)
        if self.checkpoint_config is not None:
            kwargs["checkpoint_config"] = self.checkpoint_config
        rehydrated_metadata = self.task_metadata.to_dict()
        if rehydrated_metadata:
            kwargs["metadata"] = rehydrated_metadata
        if user_input_provider is not None:
            kwargs["user_input_provider"] = user_input_provider
        if web_tool_provider is not None:
            kwargs["web_tool_provider"] = web_tool_provider
        if session_search_provider is not None:
            kwargs["session_search_provider"] = session_search_provider
        if vault_store_provider is not None:
            kwargs["vault_store_provider"] = vault_store_provider
        if memory_forget_provider is not None:
            kwargs["memory_forget_provider"] = memory_forget_provider
        if (
            pending_steer_provider is not None
            and self.source_platform.strip().lower() == "android"
        ):
            kwargs["pending_steer_provider"] = pending_steer_provider
            if priority_steer_provider is not None:
                kwargs["priority_steer_provider"] = priority_steer_provider
        if data_root:
            kwargs["data_root"] = data_root
        return kwargs


def _approvals_enabled() -> bool:
    return True


def _rehydrate_or_refuse(payload: Mapping[str, JsonValue]) -> RehydratedPayload:
    """Version + policy + metadata rehydration, in the wire's reason vocabulary."""
    try:
        return rehydrate_payload(payload)
    except PayloadError as exc:
        if exc.reason == "payload_version_invalid":
            raise GuestTaskError("task_invalid:payload_version") from exc
        raise GuestTaskError(str(exc)) from exc


def _require_guest_root(key: str, value: str, guest_roots: tuple[str, ...]) -> None:
    normalized = posixpath.normpath(value)
    if not any(
        normalized == root or normalized.startswith(f"{root}/") for root in guest_roots
    ):
        raise GuestTaskError(f"task_{key}_denied")


def _required_text(payload: Mapping[str, JsonValue], key: str) -> str:
    value = payload.get(key)
    if value is None:
        raise GuestTaskError(f"task_missing:{key}")
    if not isinstance(value, str):
        raise GuestTaskError(f"task_invalid:{key}")
    return value


def _optional_text(payload: Mapping[str, JsonValue], key: str) -> str:
    value = payload.get(key)
    if value is None:
        return ""
    if not isinstance(value, str):
        raise GuestTaskError(f"task_invalid:{key}")
    return value


def _required_int(payload: Mapping[str, JsonValue], key: str) -> int:
    value = payload.get(key)
    if value is None:
        raise GuestTaskError(f"task_missing:{key}")
    if isinstance(value, bool) or not isinstance(value, int):
        raise GuestTaskError(f"task_invalid:{key}")
    return value


def _optional_list(payload: Mapping[str, JsonValue], key: str) -> list[JsonValue]:
    value = payload.get(key)
    if value is None:
        return []
    if not isinstance(value, list):
        raise GuestTaskError(f"task_invalid:{key}")
    return list(value)


def _optional_text_list(payload: Mapping[str, JsonValue], key: str) -> list[str]:
    items = _optional_list(payload, key)
    if not all(isinstance(item, str) for item in items):
        raise GuestTaskError(f"task_invalid:{key}")
    return [str(item) for item in items]


def _optional_object_list(payload: Mapping[str, JsonValue], key: str) -> list[JsonValue]:
    items = _optional_list(payload, key)
    if not all(isinstance(item, dict) for item in items):
        raise GuestTaskError(f"task_invalid:{key}")
    return items


def _optional_object(payload: Mapping[str, JsonValue], key: str) -> JsonValue:
    value = payload.get(key)
    if value is None:
        return None
    if not isinstance(value, dict):
        raise GuestTaskError(f"task_invalid:{key}")
    return dict(value)


def narrow_json_value(value: Any) -> JsonValue:  # wire boundary value, narrowed here
    """Narrow one executor/trace value into the JSON tree the channel accepts."""
    match value:
        case str() | bool() | int() | float() | None:
            return value
        case list():
            return [narrow_json_value(item) for item in value]
        case dict():
            return narrow_json_object(value)
        case _ as rendered:
            return str(rendered)


def narrow_json_object(value: Mapping[str, Any]) -> dict[str, JsonValue]:
    return {str(key): narrow_json_value(item) for key, item in value.items()}


def narrow_int_or_zero(value: Any) -> int:  # wire boundary value, narrowed here
    if isinstance(value, bool) or not isinstance(value, int):
        return 0
    return value
