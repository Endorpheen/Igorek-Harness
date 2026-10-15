"""TDD suite for host-owned history and the versioned guest payload (plan todo 30).

The host session JSONL/DB is the only durable conversation record; the guest
receives one versioned payload per turn and rehydrates an ephemeral view from it
(:mod:`factory.core.isolation.history`). This suite pins that contract:

* round-trip at the declared version - the shared builder's wire payload
  rehydrates into the same typed task the in-guest executor receives, with
  ``history_messages``, the system prompt, ``skills_text``, ``foundation_context``,
  the limits and the approval policy all intact;
* stale state - an unknown payload version is refused with
  ``payload_version_unsupported`` and never partially applied; a v1 payload keeps
  its legacy resume view; a v2 payload that lets the guest self-approve is
  refused;
* misleading success - exact key sets, exact metadata dicts and exact message
  sequences are asserted, never truthiness;
* off-vs-kvm resume parity - for the same host fixture the message sequence the
  executor builds in kvm mode equals the off-mode golden (the same
  ``_build_initial_messages`` rule the off-mode worker uses);
* dirty worktree - every write stays under ``tmp_path``.

No VM, no libvirt and no vsock are needed: the manager's wire builder is called
on a stub ``SessionVm`` and the guest half runs in-process.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from factory.core.cc_backend_executor import (
    _build_initial_messages,
    _consume_approved_tool_call,
    _tool_denied_by_policy,
)
from factory.core.cli_chat import _history_tail
from factory.core.isolation import guest_task as guest_task_module
from factory.core.isolation import history as history_module
from factory.core.isolation import manager as manager_module
from factory.core.isolation.channel import JsonValue, PeerCid
from factory.core.isolation.config import IsolationConfig
from factory.core.isolation.domain import DomainSpec, SessionDomain
from factory.core.isolation.guest_task import (
    DEFAULT_GUEST_ROOTS,
    GuestTask,
    GuestTaskError,
)
from factory.core.isolation.history import (
    PAYLOAD_VERSION,
    HostTaskInput,
    PayloadError,
    RehydratedTaskMetadata,
    build_wire_task_payload,
    parse_payload_version,
)
from factory.core.isolation.artifact_map import guest_artifact_dir
from factory.core.isolation.manager import (
    GUEST_ARTIFACT_DIR,
    IsolationManager,
    SessionVm,
)
from factory.core.isolation.workspace import GUEST_WORKSPACE_PATH

SYSTEM_PROMPT = "You are Igorek; this prompt is assembled on the host."
SKILLS_TEXT = "skill: chart-builder (must write an HTML chart)"
FOUNDATION_CONTEXT = "foundation memory"
USER_MESSAGE = "построй график"
TRAILING_CONTEXT = "trailing dynamic context"
HOST_ROOT = "/home/example/demo"
GUEST_ROOT = GUEST_WORKSPACE_PATH
TOOL_USE_ID = "toolu-1"

HISTORY_MESSAGES: tuple[dict[str, str], ...] = (
    {"role": "user", "content": "первый вопрос"},
    {"role": "assistant", "content": "первый ответ"},
)

# The host session JSONL fixture: exactly what the durable record holds (the
# guest never appends to it).
SESSION_ENTRIES: list[dict[str, Any]] = [
    {"role": "user", "text": "первый вопрос", "ts": 1},
    {"role": "assistant", "text": "первый ответ", "ts": 2},
    {"role": "user", "text": "второй вопрос", "ts": 3},
    {"role": "assistant", "text": "второй ответ", "ts": 4},
]

GUEST_SIGNATURE = (
    'Write:{"content":"x","file_path":"/workspace/report.md"}'
)
HOST_SIGNATURE = (
    f'Write:{{"content":"x","file_path":"{HOST_ROOT}/report.md"}}'
)
FORCED_TOOL_CALL: dict[str, JsonValue] = {
    "name": "Write",
    "input": {"file_path": f"{GUEST_ROOT}/report.md", "content": "x"},
    "tool_use_id": TOOL_USE_ID,
    "source": "mobile_approval",
    "reason": "write outside the workspace",
    "approval_context_messages": [
        {"role": "assistant", "content": [{"type": "text", "text": "ctx"}]}
    ],
}
SESSION_RULE: dict[str, JsonValue] = {
    "rule_type": "file_path",
    "tool": "Write",
    "file_path": f"{GUEST_ROOT}/report.md",
}
APPROVAL_CONTEXT: tuple[dict[str, JsonValue], ...] = (
    {"role": "user", "content": [{"type": "text", "text": "top ctx"}]},
)


def _guest_metadata() -> RehydratedTaskMetadata:
    """The already host->guest mapped metadata the host embeds for the guest."""
    return RehydratedTaskMetadata(
        must_write_skills=("chart-builder",),
        approved_tool_calls=(GUEST_SIGNATURE,),
        approved_session_rules=(SESSION_RULE,),
        forced_tool_call=FORCED_TOOL_CALL,
        approval_context_messages=APPROVAL_CONTEXT,
    )


def _host_input(**overrides: Any) -> HostTaskInput:
    values: dict[str, Any] = {
        "task_id": "task-1",
        "profile": "cc-wally",
        "model": "glm-5.3-flash",
        "base_url": "https://inference.runanywhere.ai/v1",
        "api_format": "openai",
        "system_prompt": SYSTEM_PROMPT,
        "user_message": USER_MESSAGE,
        "skills_text": SKILLS_TEXT,
        "foundation_context": FOUNDATION_CONTEXT,
        "filesystem_mode": "workspace",
        "guard_mode": "on",
        "max_tokens": 512,
        "timeout_s": 30,
        "workdir": GUEST_ROOT,
        "artifact_workdir": GUEST_ARTIFACT_DIR,
        "trailing_context_text": TRAILING_CONTEXT,
        "current_session": "sess-1",
        "source_platform": "android",
        "source_chat_id": "42",
        "input_files": ("/tmp/upload.png",),
        "enabled_tools": ({"name": "Read"},),
        "history_messages": HISTORY_MESSAGES,
        "checkpoint_config": {"enabled": True},
        "task_metadata": _guest_metadata(),
    }
    values.update(overrides)
    return HostTaskInput(**values)


def _round_trip(wire: dict[str, JsonValue]) -> GuestTask:
    """Cross the wire exactly as the channel does, then rehydrate in the guest."""
    return GuestTask.from_wire(
        json.loads(json.dumps(wire, ensure_ascii=False)),
        guest_roots=DEFAULT_GUEST_ROOTS,
    )


def _no_log(_message: str) -> None:
    return None


def _off_mode_history_messages(entries: list[dict[str, Any]]) -> list[dict[str, str]]:
    """The off-mode host rule for which session entries replay (chat_session.py)."""
    tail = _history_tail(entries, max_messages=30, max_chars=50_000)
    return [
        {
            "role": "user" if str(entry.get("role")) == "user" else "assistant",
            "content": str(entry.get("text") or ""),
        }
        for entry in tail
        if str(entry.get("text") or "").strip()
    ]


def _dispatch_payload(**overrides: Any) -> dict[str, Any]:
    """The worker dispatch payload the manager receives (host paths inside)."""
    payload: dict[str, Any] = {
        "task_id": "task-1",
        "profile": "cc-test",
        "model": "test-model",
        "base_url": "http://127.0.0.1:8001",
        "api_format": "anthropic",
        "system_prompt": SYSTEM_PROMPT,
        "user_message": USER_MESSAGE,
        "skills_text": SKILLS_TEXT,
        "foundation_context": FOUNDATION_CONTEXT,
        "filesystem_mode": "workspace",
        "guard_mode": "on",
        "max_tokens": 4000,
        "timeout_s": 1200,
        "trailing_context_text": TRAILING_CONTEXT,
        "current_session": "sess-1",
        "source_platform": "android",
        "source_chat_id": "42",
        "input_files": [],
        "enabled_tools": [{"name": "Read"}],
        "history_messages": list(HISTORY_MESSAGES),
        "checkpoint_config": {"enabled": True},
        "metadata": {
            "must_write_skills": ["chart-builder"],
            "approved_tool_calls": [HOST_SIGNATURE],
            "approved_session_rules": [
                {
                    "rule_type": "file_path",
                    "tool": "Write",
                    "file_path": f"{HOST_ROOT}/report.md",
                }
            ],
            "forced_tool_call": {
                **FORCED_TOOL_CALL,
                "input": {"file_path": f"{HOST_ROOT}/report.md", "content": "x"},
            },
            "approval_context_messages": list(APPROVAL_CONTEXT),
        },
    }
    payload.update(overrides)
    return payload


def _stub_vm(workspace: Path) -> SessionVm:
    """A SessionVm whose export is the only part the wire builder reads."""
    return SessionVm(
        session_id="sess-1",
        spec=DomainSpec(
            session_id="sess-1",
            base_image=workspace / "base.qcow2",
            workspace_host_path=workspace,
            overlay_dir=workspace / "overlays",
        ),
        domain=SessionDomain(SimpleNamespace()),
        export={"host_path": str(workspace), "guest_path": GUEST_ROOT},
        peer_cid=PeerCid(11),
    )


def _manager(tmp_path: Path) -> IsolationManager:
    config = IsolationConfig.from_env({}, data_root=tmp_path)
    return IsolationManager(config)


# ── round-trip at the declared version ───────────────────────────────────────


def test_payload_round_trips_at_the_declared_version() -> None:
    # Given: one host task payload built by the single shared builder
    wire = build_wire_task_payload(_host_input())

    # When: it crosses the wire (JSON) and the guest rehydrates it
    task = _round_trip(wire)

    # Then: the declared version and every host-owned rehydration entry survive
    assert wire["payload_version"] == PAYLOAD_VERSION == 2
    assert wire["approval_policy"] == "host_resolved"
    assert task.payload_version == PAYLOAD_VERSION
    assert task.system_prompt == SYSTEM_PROMPT
    assert task.skills_text == SKILLS_TEXT
    assert task.foundation_context == FOUNDATION_CONTEXT
    assert task.max_tokens == 512
    assert task.timeout_s == 30
    assert task.user_message == USER_MESSAGE
    assert list(task.history_messages) == list(HISTORY_MESSAGES)
    assert task.workdir == GUEST_ROOT
    assert task.artifact_workdir == GUEST_ARTIFACT_DIR
    kwargs = task.executor_kwargs(_no_log)
    assert kwargs["system_prompt"] == SYSTEM_PROMPT
    assert kwargs["user_message"] == USER_MESSAGE
    assert kwargs["history_messages"] == list(HISTORY_MESSAGES)
    assert kwargs["trailing_context_text"] == TRAILING_CONTEXT
    assert kwargs["input_files"] == ["/tmp/upload.png"]
    assert kwargs["max_tokens"] == 512
    assert kwargs["timeout_s"] == 30
    # Then: the durable host state stays host-side - the ephemeral view carries
    # no host workspace path at all
    assert HOST_ROOT not in json.dumps(wire, ensure_ascii=False)


def test_wire_schema_is_pinned_exactly() -> None:
    # Given: a full payload and a minimal one (no resume, no optional entries)
    full = build_wire_task_payload(_host_input())
    minimal = build_wire_task_payload(
        _host_input(
            trailing_context_text="",
            current_session="",
            source_platform="",
            source_chat_id="",
            input_files=(),
            enabled_tools=(),
            history_messages=(),
            checkpoint_config=None,
            task_metadata=RehydratedTaskMetadata(),
        )
    )

    # Then: the key set is a contract, not an accident
    assert set(full) == {
        "payload_version",
        "approval_policy",
        "task_metadata",
        "task_id",
        "profile",
        "model",
        "base_url",
        "api_format",
        "system_prompt",
        "user_message",
        "skills_text",
        "foundation_context",
        "filesystem_mode",
        "guard_mode",
        "max_tokens",
        "timeout_s",
        "workdir",
        "artifact_workdir",
        "trailing_context_text",
        "current_session",
        "source_platform",
        "source_chat_id",
        "input_files",
        "enabled_tools",
        "history_messages",
        "checkpoint_config",
        "approval_resume",
    }
    assert set(minimal) == {
        "payload_version",
        "approval_policy",
        "task_metadata",
        "task_id",
        "profile",
        "model",
        "base_url",
        "api_format",
        "system_prompt",
        "user_message",
        "skills_text",
        "foundation_context",
        "filesystem_mode",
        "guard_mode",
        "max_tokens",
        "timeout_s",
        "workdir",
        "artifact_workdir",
        "trailing_context_text",
    }
    assert minimal["task_metadata"] == {}


# ── stale state: versions ────────────────────────────────────────────────────


def test_unknown_payload_version_is_refused_without_guessing() -> None:
    # Given: a payload from a version the guest has no migrator for
    future = PAYLOAD_VERSION + 1
    with pytest.raises(PayloadError) as refusal:
        parse_payload_version({"payload_version": future})

    # Then: the stable reason names the condition and the raw version
    assert refusal.value.reason == "payload_version_unsupported"
    assert refusal.value.detail == str(future)

    # And: the guest wire path refuses the same payload before any field is read
    wire = build_wire_task_payload(_host_input())
    wire["payload_version"] = future
    with pytest.raises(GuestTaskError) as guest_refusal:
        GuestTask.from_wire(wire, guest_roots=DEFAULT_GUEST_ROOTS)
    assert guest_refusal.value.reason == f"payload_version_unsupported:{future}"

    # And: a non-integer version is malformed, never coerced
    with pytest.raises(PayloadError) as invalid:
        parse_payload_version({"payload_version": "2"})
    assert invalid.value.reason == "payload_version_invalid"


def test_supported_versions_are_the_declared_ones() -> None:
    # Given/When/Then: legacy and v1 payloads still parse; only the declared set does
    assert parse_payload_version({}) == 0
    assert parse_payload_version({"payload_version": 1}) == 1
    assert parse_payload_version({"payload_version": PAYLOAD_VERSION}) == PAYLOAD_VERSION


def test_v1_payload_keeps_the_legacy_resume_view() -> None:
    # Given: a v1 payload (the committed wire shape before todo 30)
    wire = build_wire_task_payload(_host_input())
    legacy = {
        key: value
        for key, value in wire.items()
        if key not in {"payload_version", "approval_policy", "task_metadata"}
    }
    legacy["payload_version"] = 1
    legacy["approval_resume"] = dict(wire["approval_resume"])

    # When: the guest rehydrates it
    task = _round_trip(legacy)

    # Then: the legacy resume metadata reaches the executor unchanged
    assert task.payload_version == 1
    assert task.executor_kwargs(_no_log)["metadata"] == {
        "forced_tool_call": FORCED_TOOL_CALL,
        "approved_tool_calls": [GUEST_SIGNATURE],
        "approved_session_rules": [SESSION_RULE],
    }


def test_v2_payload_without_task_metadata_is_refused() -> None:
    # Given: a v2 payload missing the canonical rehydration section
    wire = build_wire_task_payload(_host_input())
    del wire["task_metadata"]

    # When/Then: the guest refuses instead of guessing an empty history
    with pytest.raises(GuestTaskError) as refusal:
        GuestTask.from_wire(wire, guest_roots=DEFAULT_GUEST_ROOTS)
    assert refusal.value.reason == "payload_metadata_missing:task_metadata"


def test_payload_that_lets_the_guest_self_approve_is_refused() -> None:
    # Given: a payload claiming the guest may resolve its own approvals
    wire = build_wire_task_payload(_host_input())
    wire["approval_policy"] = "guest_may_approve"

    # When/Then: fail closed - the host owns approvals (plan todo 9)
    with pytest.raises(GuestTaskError) as refusal:
        GuestTask.from_wire(wire, guest_roots=DEFAULT_GUEST_ROOTS)
    assert refusal.value.reason == "approval_policy_unsupported:guest_may_approve"


# ── metadata rehydration ─────────────────────────────────────────────────────


def test_task_metadata_fields_survive_the_round_trip() -> None:
    # Given: a payload whose task_metadata carries the executor-needed subset
    wire = build_wire_task_payload(_host_input())

    # Then: the wire section is the exact typed value, not a superset
    assert wire["task_metadata"] == {
        "must_write_skills": ["chart-builder"],
        "approved_tool_calls": [GUEST_SIGNATURE],
        "approved_session_rules": [SESSION_RULE],
        "forced_tool_call": FORCED_TOOL_CALL,
        "approval_context_messages": list(APPROVAL_CONTEXT),
    }

    # When: the guest rehydrates it
    metadata = _round_trip(wire).executor_kwargs(_no_log)["metadata"]

    # Then: every field reaches the executor with the same key and value
    assert metadata == {
        "forced_tool_call": FORCED_TOOL_CALL,
        "approved_tool_calls": [GUEST_SIGNATURE],
        "approved_session_rules": [SESSION_RULE],
        "must_write_skills": ["chart-builder"],
        "approval_context_messages": list(APPROVAL_CONTEXT),
    }


def test_model_context_window_survives_the_round_trip() -> None:
    # Given: a payload whose task_metadata carries the host-resolved context window
    wire = build_wire_task_payload(
        _host_input(task_metadata=RehydratedTaskMetadata(model_context_window=128_000))
    )

    # Then: the wire carries it exactly and the guest executor receives the same value
    assert wire["task_metadata"]["model_context_window"] == 128_000
    metadata = _round_trip(wire).executor_kwargs(_no_log)["metadata"]
    assert metadata["model_context_window"] == 128_000


def test_invalid_model_context_window_is_refused_without_guessing() -> None:
    # Given: a wire section whose context window is not a positive integer
    wire = build_wire_task_payload(_host_input())
    wire["task_metadata"] = {**wire["task_metadata"], "model_context_window": -1}

    # Then: rehydration refuses the payload instead of coercing the value
    with pytest.raises(GuestTaskError, match="payload_metadata_invalid:model_context_window"):
        _round_trip(wire)


def test_resumed_approved_call_is_consumed_by_the_guest_executor() -> None:
    # Given: a resumed task whose rehydrated metadata reaches the executor
    metadata = _round_trip(build_wire_task_payload(_host_input())).executor_kwargs(_no_log)[
        "metadata"
    ]

    # When/Then: the approved signature the guest recomputes from guest paths
    # matches, so the forced call runs exactly once instead of re-prompting
    assert (
        _consume_approved_tool_call(
            metadata=metadata,
            tool_name="Write",
            tool_input={"file_path": f"{GUEST_ROOT}/report.md", "content": "x"},
            tool_use_id=TOOL_USE_ID,
        )
        is True
    )
    assert metadata["approved_tool_calls"] == []


# ── off-vs-kvm resume parity (golden) ────────────────────────────────────────


def test_resume_message_sequence_matches_off_mode_golden() -> None:
    # Given: one host session fixture and its off-mode replay rule
    history_messages = _off_mode_history_messages(SESSION_ENTRIES)
    assert len(history_messages) == 4  # the golden is not vacuous
    off_sequence = _build_initial_messages(
        history_messages=history_messages,
        trailing_context_text=TRAILING_CONTEXT,
        user_message=USER_MESSAGE,
        input_files=None,
    )
    assert [message["role"] for message in off_sequence] == [
        "user",
        "assistant",
        "user",
        "assistant",
        "user",
    ]

    # When: the same fixture is dispatched through the versioned kvm payload
    wire = build_wire_task_payload(
        _host_input(history_messages=tuple(history_messages), input_files=())
    )
    kwargs = _round_trip(wire).executor_kwargs(_no_log)
    kvm_sequence = _build_initial_messages(
        history_messages=kwargs.get("history_messages"),
        trailing_context_text=kwargs.get("trailing_context_text", ""),
        user_message=kwargs["user_message"],
        input_files=kwargs.get("input_files"),
    )

    # Then: the message sequence is identical to the off-mode golden
    assert kvm_sequence == off_sequence
    # And: the rehydration inputs themselves are equal, not just the result
    assert kwargs["history_messages"] == history_messages
    assert kwargs["user_message"] == USER_MESSAGE
    assert kwargs["trailing_context_text"] == TRAILING_CONTEXT


def test_manager_assembles_the_wire_only_through_the_shared_builder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Given: a manager whose shared builder is observed
    captured: list[HostTaskInput] = []
    original = history_module.build_wire_task_payload

    def spy(task: HostTaskInput) -> dict[str, JsonValue]:
        captured.append(task)
        return original(task)

    monkeypatch.setattr(manager_module, "build_wire_task_payload", spy)
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    manager = _manager(tmp_path)
    vm = _stub_vm(workspace)

    # When: the manager builds the wire frame for one dispatch
    wire = manager._wire_task_payload(vm, _dispatch_payload())

    # Then: the assembly happened in exactly one place, with a typed input
    assert len(captured) == 1
    assert isinstance(captured[0], HostTaskInput)
    assert wire == original(captured[0])
    assert captured[0].workdir == GUEST_ROOT
    assert captured[0].artifact_workdir == guest_artifact_dir(GUEST_ROOT, "task-1")
    assert captured[0].skills_text == SKILLS_TEXT
    assert captured[0].foundation_context == FOUNDATION_CONTEXT
    assert captured[0].max_tokens == 4000
    assert captured[0].timeout_s == 1200


def test_manager_maps_host_paths_into_the_rehydrated_task_metadata(
    tmp_path: Path,
) -> None:
    # Given: host metadata stored by the mobile approval flow (host paths) and a
    # VM exporting exactly that workspace root
    manager = _manager(tmp_path)
    vm = _stub_vm(Path(HOST_ROOT))

    # When: the manager builds the wire frame
    wire = manager._wire_task_payload(vm, _dispatch_payload())
    section = dict(wire["task_metadata"])

    # Then: every host path is mapped to its guest path before it crosses
    assert dict(section["forced_tool_call"])["input"]["file_path"] == (
        f"{GUEST_ROOT}/report.md"
    )
    assert list(section["approved_tool_calls"]) == [GUEST_SIGNATURE]
    assert list(section["approved_session_rules"]) == [SESSION_RULE]
    assert list(section["must_write_skills"]) == ["chart-builder"]
    assert HOST_ROOT not in json.dumps(wire, ensure_ascii=False)

    # And: the guest consumes the mapped approval (the forced call runs)
    guest_metadata = _round_trip(wire).executor_kwargs(_no_log)["metadata"]
    assert (
        _consume_approved_tool_call(
            metadata=guest_metadata,
            tool_name="Write",
            tool_input={"file_path": f"{GUEST_ROOT}/report.md", "content": "x"},
            tool_use_id=TOOL_USE_ID,
        )
        is True
    )


# ── the guest holds no durable conversation history ──────────────────────────

HOST_STATE_MODULES = frozenset(
    {
        "mobile_bridge",
        "cli_chat",
        "chat_session",
        "session_recall_store",
        "runtime",
        "worker",
        "cc_backend_executor",
        "manager",
        "domain",
        "workspace",
        "approval",
        "relay",
        "user_input",
        "preflight",
        "base_image",
        "config",
        "sqlite3",
        "subprocess",
    }
)


def _imported_names(module: Any) -> list[tuple[int, str]]:
    tree = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
    names: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.extend((0, alias.name) for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            names.append((node.level, node.module or ""))
            names.extend((node.level, alias.name) for alias in node.names)
    return names


def test_guest_history_modules_import_no_host_state() -> None:
    # Given/When/Then: the guest payload modules cannot reach the durable host
    # session store, the DB or the host runtime - rehydration is payload-only
    for module in (history_module, guest_task_module):
        for level, name in _imported_names(module):
            leaf = name.split(".")[-1]
            if level > 0:
                assert leaf not in HOST_STATE_MODULES, f"{module.__name__} imports .{name}"
            else:
                assert name.split(".")[0] not in HOST_STATE_MODULES, (
                    f"{module.__name__} imports {name}"
                )


def test_manager_maps_attachment_paths_and_text_to_the_exported_guest_root(tmp_path: Path) -> None:
    attachment = tmp_path / ".igorek_uploads" / "session-a" / "image.png"
    attachment.parent.mkdir(parents=True)
    attachment.write_bytes(b"image")
    host_path = str(attachment.resolve())
    payload = _dispatch_payload(
        input_files=[host_path],
        user_message=f"Посмотри файл: {host_path}",
        trailing_context_text=f"Файл: {host_path}",
        history_messages=[{"role": "user", "content": f"Вложение: {host_path}"}],
    )
    manager = _manager(tmp_path)
    wire = manager._wire_task_payload(_stub_vm(tmp_path), payload)

    guest_path = f"{GUEST_ROOT}/.igorek_uploads/session-a/image.png"
    assert wire["input_files"] == [guest_path]
    assert wire["user_message"] == f"Посмотри файл: {guest_path}"
    assert wire["trailing_context_text"] == f"Файл: {guest_path}"
    assert wire["history_messages"] == [{"role": "user", "content": f"Вложение: {guest_path}"}]
    assert host_path not in json.dumps(wire)
    guest = _round_trip(wire)
    assert guest.executor_kwargs(_no_log)["input_files"] == wire["input_files"]


def test_manager_refuses_attachment_outside_the_exported_workspace(tmp_path: Path) -> None:
    outside = tmp_path.parent / "outside.png"
    outside.write_bytes(b"image")
    manager = _manager(tmp_path)

    with pytest.raises(manager_module.IsolationError, match="input_file_outside_workspace"):
        manager._wire_task_payload(
            _stub_vm(tmp_path), _dispatch_payload(input_files=[str(outside.resolve())])
        )


# ── plan mode reaches the guest executor (read-only enforcement) ─────────────

PLAN_MODE_ALLOWED = ("Read", "Grep", "Glob", "update_plan")
PLAN_MODE_DENIED = ("Write", "Edit", "MultiEdit", "Bash")


def test_plan_mode_restriction_reaches_the_guest_executor() -> None:
    # Given: a plan-mode task whose host metadata carries the read-only policy
    wire = build_wire_task_payload(
        _host_input(
            task_metadata=RehydratedTaskMetadata(
                restrict_tools_to=PLAN_MODE_ALLOWED,
            )
        )
    )

    # Then: the policy crosses the wire as the exact typed list
    assert wire["task_metadata"]["restrict_tools_to"] == list(PLAN_MODE_ALLOWED)

    # When: the guest rehydrates it and the executor reads the metadata
    metadata = _round_trip(wire).executor_kwargs(_no_log)["metadata"]

    # Then: the guest executor denies every mutating tool and allows read-only
    assert metadata["restrict_tools_to"] == list(PLAN_MODE_ALLOWED)
    for allowed in PLAN_MODE_ALLOWED:
        assert not _tool_denied_by_policy(allowed, metadata), allowed
    for denied in PLAN_MODE_DENIED:
        assert _tool_denied_by_policy(denied, metadata), denied


def test_manager_maps_plan_mode_policy_into_the_guest_payload(tmp_path: Path) -> None:
    # Given: the worker's plan-mode metadata (restrict_tools_to + must_write popped)
    payload = _dispatch_payload(
        metadata={
            "plan_mode": True,
            "restrict_tools_to": list(PLAN_MODE_ALLOWED),
        }
    )
    manager = _manager(tmp_path)

    # When: the manager builds the wire frame for the guest
    wire = manager._wire_task_payload(_stub_vm(Path(HOST_ROOT)), payload)

    # Then: the restriction is present in the rehydrated section, not dropped
    assert wire["task_metadata"]["restrict_tools_to"] == list(PLAN_MODE_ALLOWED)
    metadata = _round_trip(wire).executor_kwargs(_no_log)["metadata"]
    for denied in PLAN_MODE_DENIED:
        assert _tool_denied_by_policy(denied, metadata), denied
    for allowed in PLAN_MODE_ALLOWED:
        assert not _tool_denied_by_policy(allowed, metadata), allowed


def test_block_tool_names_reaches_the_guest_executor() -> None:
    # Given: a task that blocks one tool by name (the other policy list)
    wire = build_wire_task_payload(
        _host_input(
            task_metadata=RehydratedTaskMetadata(block_tool_names=("delegate_task",))
        )
    )

    # When: the guest rehydrates it
    metadata = _round_trip(wire).executor_kwargs(_no_log)["metadata"]

    # Then: the blocked name is denied and everything else stays allowed
    assert metadata["block_tool_names"] == ["delegate_task"]
    assert _tool_denied_by_policy("delegate_task", metadata)
    assert not _tool_denied_by_policy("Read", metadata)


def test_normal_task_metadata_is_byte_stable_without_policy_lists() -> None:
    # Given: a normal (execute) task with no read-only policy
    wire = build_wire_task_payload(_host_input())

    # Then: the policy keys are absent, so the wire shape is unchanged
    assert "restrict_tools_to" not in wire["task_metadata"]
    assert "block_tool_names" not in wire["task_metadata"]
    metadata = _round_trip(wire).executor_kwargs(_no_log)["metadata"]
    assert "restrict_tools_to" not in metadata
    assert "block_tool_names" not in metadata
    # And: a normal task still runs every tool
    for tool in ("Read", "Write", "Edit", "Bash"):
        assert not _tool_denied_by_policy(tool, metadata), tool


def test_manager_wire_has_no_host_only_keys() -> None:
    # Given: a dispatch payload whose host-only values must never cross
    workspace = Path("/tmp") / "unused"
    payload = _dispatch_payload()
    payload["log_fn"] = _no_log
    payload["request_headers"] = {"Authorization": "Bearer secret"}
    payload["pending_steer_provider"] = _no_log

    # When: the manager builds the wire frame
    manager = IsolationManager(IsolationConfig.from_env({}, data_root=Path("/tmp")))
    wire = manager._wire_task_payload(_stub_vm(workspace), payload)

    # Then: only the declared contract keys are present
    for host_only in ("log_fn", "request_headers", "pending_steer_provider", "metadata"):
        assert host_only not in wire
    assert "Bearer secret" not in json.dumps(wire, ensure_ascii=False)


def test_manager_publishes_large_history_in_the_existing_workspace_export(tmp_path: Path) -> None:
    history = [{"role": "assistant", "content": "evidence " + "x" * 8_000_000, "history_id": "external:1"}]
    wire = _manager(tmp_path)._wire_task_payload(_stub_vm(tmp_path), _dispatch_payload(history_messages=history, metadata={}))
    assert "history_messages" not in wire
    assert len(json.dumps(wire).encode()) < 4096
    reference = wire["task_payload_file"]
    guest_path = reference["path"]
    host_path = tmp_path / Path(guest_path).relative_to(GUEST_ROOT)
    stored = json.loads(host_path.read_text())
    assert stored["history_messages"] == history
    assert stored["task_id"] == wire["task_id"]
    assert reference["size"] == host_path.stat().st_size
