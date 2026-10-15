"""Session compaction planning and anchored memory handovers."""

from __future__ import annotations

import json
import os
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .trace_taxonomy import CcTraceEvent


REASON_PENDING_APPROVAL = "pending_approval"
REASON_UNRESOLVED_FAILED_TOOL_RESULT = "unresolved_failed_tool_result"
REASON_UNRESOLVED_POST_TURN_GUARD = "unresolved_post_turn_guard"
REASON_NO_COMPACTABLE_MESSAGES = "no_compactable_messages"
REASON_CONTEXT_PRESSURE = "context_pressure"
REASON_PROMPT_TOO_LONG = "prompt_too_long"
SUMMARY_SCHEMA_VERSION = 1

DEFAULT_DRY_RUN_THRESHOLD = 0.72
DEFAULT_MANUAL_THRESHOLD = 0.82
DEFAULT_REACTIVE_THRESHOLD = 0.9
IMAGE_TOKEN_ESTIMATE_MIN = 256
IMAGE_TOKEN_ESTIMATE_MAX = 8_192


@dataclass(frozen=True)
class CompactionDryRunPlan:
    """A conservative compaction plan that does not mutate session history."""

    blocked: bool
    reason: str = ""
    summarize_message_indexes: list[int] = field(default_factory=list)
    preserve_message_indexes: list[int] = field(default_factory=list)
    preserve_trace_indexes: list[int] = field(default_factory=list)
    preserved_tool_result_ids: list[str] = field(default_factory=list)
    preserved_approval_ids: list[str] = field(default_factory=list)
    boundary_preview: dict[str, Any] = field(default_factory=dict)
    summary_shape: dict[str, Any] = field(default_factory=dict)

    @property
    def can_compact(self) -> bool:
        return not self.blocked and bool(self.summarize_message_indexes)


@dataclass(frozen=True)
class CompactionArtifact:
    compaction_id: str
    summary_path: Path
    boundary_event: dict[str, Any]
    summary: dict[str, Any]


@dataclass(frozen=True)
class CompactionPressureDecision:
    action: str
    reason: str = ""
    context_ratio: float = 0.0
    estimated_context_tokens: int = 0
    usable_context_window: int = 0
    reserved_output_tokens: int = 0

    @property
    def should_check(self) -> bool:
        return self.action in {"dry_run", "manual", "reactive"}


def estimate_context_tokens(
    *,
    system_prompt: str,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None = None,
) -> int:
    """Estimate request context tokens without a tokenizer dependency."""

    return max(
        1,
        _estimate_jsonish_tokens(system_prompt)
        + _estimate_jsonish_tokens(messages)
        + _estimate_jsonish_tokens(tools or []),
    )


def _estimate_jsonish_tokens(value: Any) -> int:
    if isinstance(value, str):
        return max(1, len(value) // 4)
    if isinstance(value, (int, float, bool)) or value is None:
        return max(1, len(json.dumps(value, ensure_ascii=False)) // 4)
    if isinstance(value, list):
        return 1 + sum(_estimate_jsonish_tokens(item) for item in value)
    if isinstance(value, dict):
        image_tokens = _estimate_image_block_tokens(value)
        if image_tokens is not None:
            return image_tokens
        total = 1
        for key, item in value.items():
            total += _estimate_jsonish_tokens(str(key))
            total += _estimate_jsonish_tokens(item)
        return total
    return max(1, len(str(value)) // 4)


def _estimate_image_block_tokens(block: dict[str, Any]) -> int | None:
    if str(block.get("type") or "") != "image":
        return None
    source = block.get("source")
    if not isinstance(source, dict):
        return IMAGE_TOKEN_ESTIMATE_MIN
    if str(source.get("type") or "") != "base64":
        return IMAGE_TOKEN_ESTIMATE_MIN
    data = source.get("data")
    if not isinstance(data, str):
        return IMAGE_TOKEN_ESTIMATE_MIN
    # Base64 is transport encoding, not text context. Estimate the image payload
    # by decoded byte size instead of counting every base64 character as tokens.
    approx_bytes = (len(data.strip()) * 3) // 4
    estimated = max(IMAGE_TOKEN_ESTIMATE_MIN, approx_bytes // 1024)
    return min(IMAGE_TOKEN_ESTIMATE_MAX, estimated)


def build_compaction_dry_run_plan(
    *,
    messages: list[dict[str, Any]],
    trace_events: list[dict[str, Any]] | None = None,
    session_budget_snapshot: dict[str, Any] | None = None,
    keep_tail_messages: int = 2,
    trigger: str = "manual",
    keep_tail_tokens: int | None = None,
) -> CompactionDryRunPlan:
    """Build a non-mutating plan for session compaction.

    The planner is intentionally conservative: unresolved approvals, failed
    tool evidence and active post-turn guard retries block compaction instead
    of being summarized away.
    """

    events = list(trace_events or [])
    budget = dict(session_budget_snapshot or {})
    message_count = len(messages)
    preserve_message_indexes: set[int] = set()
    preserve_trace_indexes: set[int] = set()

    if keep_tail_messages > 0:
        start = max(0, message_count - keep_tail_messages)
        preserve_message_indexes.update(range(start, message_count))

    if keep_tail_tokens is not None:
        preserve_message_indexes.clear()
        user_indexes = [
            index for index, message in enumerate(messages)
            if message.get("role") == "user"
            and not any(block.get("type") == "tool_result" for block in _iter_content_blocks(message))
        ]
        start = user_indexes[-1] if user_indexes else max(0, message_count - 1)
        used = estimate_context_tokens(system_prompt="", messages=messages[start:], tools=[])
        for previous in reversed(user_indexes[:-1]):
            turn_size = estimate_context_tokens(system_prompt="", messages=messages[previous:start], tools=[])
            if used + turn_size > keep_tail_tokens:
                break
            start = previous
            used += turn_size
        preserve_message_indexes.update(range(start, message_count))

    tool_use_message_by_id: dict[str, int] = {}
    tool_result_message_by_id: dict[str, int] = {}
    for index, message in enumerate(messages):
        for block in _iter_content_blocks(message):
            block_type = str(block.get("type") or "")
            if block_type == "tool_use":
                tool_id = _first_text(block, "id", "tool_use_id")
                if tool_id:
                    tool_use_message_by_id[tool_id] = index
            elif block_type == "tool_result":
                tool_id = _first_text(block, "tool_use_id", "id")
                if tool_id:
                    tool_result_message_by_id[tool_id] = index

    _preserve_complete_tool_pairs(
        preserve_message_indexes,
        tool_use_message_by_id,
        tool_result_message_by_id,
    )

    preserved_tool_result_ids: set[str] = set()
    preserved_approval_ids: set[str] = set()
    block_reason = ""

    last_fact_check_index = _last_index(
        events,
        {
            CcTraceEvent.FACT_CHECK_PASSED,
            CcTraceEvent.FACT_CHECK_CORRECTION_FORCED,
        },
    )

    for index, event in enumerate(events):
        kind = str(event.get("kind") or "")
        if kind == CcTraceEvent.TOOL_APPROVAL_REQUIRED and _is_pending_approval(event):
            preserve_trace_indexes.add(index)
            approval_id = _approval_id(event)
            if approval_id:
                preserved_approval_ids.add(approval_id)
            block_reason = block_reason or REASON_PENDING_APPROVAL
        elif kind == CcTraceEvent.POST_TURN_RETRY_REQUIRED:
            preserve_trace_indexes.add(index)
            block_reason = block_reason or REASON_UNRESOLVED_POST_TURN_GUARD
        elif kind == CcTraceEvent.POST_TURN_PENDING_WRITE_DETECTED:
            preserve_trace_indexes.add(index)
            block_reason = block_reason or REASON_UNRESOLVED_POST_TURN_GUARD
        elif kind == CcTraceEvent.TOOL_RESULT:
            tool_id = _tool_result_id(event)
            if tool_id and _is_failed_tool_result(event):
                preserve_trace_indexes.add(index)
                preserved_tool_result_ids.add(tool_id)
                if last_fact_check_index < index:
                    block_reason = block_reason or REASON_UNRESOLVED_FAILED_TOOL_RESULT
            elif tool_id and index > last_fact_check_index:
                preserved_tool_result_ids.add(tool_id)

    for tool_id in preserved_tool_result_ids:
        _preserve_tool_pair_by_id(
            preserve_message_indexes,
            tool_id,
            tool_use_message_by_id,
            tool_result_message_by_id,
        )

    if block_reason:
        return _plan(
            blocked=True,
            reason=block_reason,
            messages=messages,
            preserve_message_indexes=preserve_message_indexes,
            preserve_trace_indexes=preserve_trace_indexes,
            preserved_tool_result_ids=preserved_tool_result_ids,
            preserved_approval_ids=preserved_approval_ids,
            trigger=trigger,
            budget=budget,
        )

    summarize_indexes = [
        index for index in range(message_count) if index not in preserve_message_indexes
    ]
    if not summarize_indexes:
        return _plan(
            blocked=True,
            reason=REASON_NO_COMPACTABLE_MESSAGES,
            messages=messages,
            preserve_message_indexes=preserve_message_indexes,
            preserve_trace_indexes=preserve_trace_indexes,
            preserved_tool_result_ids=preserved_tool_result_ids,
            preserved_approval_ids=preserved_approval_ids,
            trigger=trigger,
            budget=budget,
        )

    return _plan(
        blocked=False,
        reason="",
        messages=messages,
        preserve_message_indexes=preserve_message_indexes,
        preserve_trace_indexes=preserve_trace_indexes,
        preserved_tool_result_ids=preserved_tool_result_ids,
        preserved_approval_ids=preserved_approval_ids,
        trigger=trigger,
        budget=budget,
        summarize_message_indexes=summarize_indexes,
    )


def evaluate_compaction_pressure(
    *,
    estimated_context_tokens: int,
    context_window_tokens: int,
    reserved_output_tokens: int = 2048,
    prompt_too_long: bool = False,
    dry_run_threshold: float = DEFAULT_DRY_RUN_THRESHOLD,
    manual_threshold: float = DEFAULT_MANUAL_THRESHOLD,
    reactive_threshold: float = DEFAULT_REACTIVE_THRESHOLD,
) -> CompactionPressureDecision:
    """Classify context pressure without triggering compaction.

    The thresholds are deliberately conservative for local models: keep a
    reserve for the next answer/tool result, dry-run before the window is hot,
    and make automatic/reactive behavior a late path.
    """

    usable_window = max(1, int(context_window_tokens) - max(0, int(reserved_output_tokens)))
    used_tokens = max(0, int(estimated_context_tokens))
    ratio = min(1.0, used_tokens / usable_window)
    if prompt_too_long:
        return CompactionPressureDecision(
            action="reactive",
            reason=REASON_PROMPT_TOO_LONG,
            context_ratio=ratio,
            estimated_context_tokens=used_tokens,
            usable_context_window=usable_window,
            reserved_output_tokens=max(0, int(reserved_output_tokens)),
        )
    if ratio >= reactive_threshold:
        action = "reactive"
    elif ratio >= manual_threshold:
        action = "manual"
    elif ratio >= dry_run_threshold:
        action = "dry_run"
    else:
        action = "continue"
    return CompactionPressureDecision(
        action=action,
        reason=REASON_CONTEXT_PRESSURE if action != "continue" else "",
        context_ratio=ratio,
        estimated_context_tokens=used_tokens,
        usable_context_window=usable_window,
        reserved_output_tokens=max(0, int(reserved_output_tokens)),
    )


def write_manual_compaction_artifact(
    *,
    plan: CompactionDryRunPlan,
    artifact_dir: str | Path,
    compaction_id: str,
    created_at: int | None = None,
    summary_text: str = "",
    covered_history_ids: list[str] | None = None,
) -> CompactionArtifact:
    """Write a structured summary artifact for a manual compaction attempt.

    This is intentionally not a context mutation API.  Callers get a boundary
    event and artifact path, but applying the compacted context is a separate
    future step.
    """

    if not plan.can_compact:
        raise ValueError(f"Cannot write compaction artifact for blocked plan: {plan.reason}")

    target_dir = Path(artifact_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    summary_path = target_dir / f"{compaction_id}.json"
    timestamp = int(created_at if created_at is not None else time.time())
    summary = {
        **plan.summary_shape,
        "schema_version": SUMMARY_SCHEMA_VERSION,
        "compaction_id": compaction_id,
        "boundary_id": compaction_id,
        "created_at": timestamp,
        "summary_path": str(summary_path),
        "summary_text": summary_text,
        "dry_run": False,
        "covered_history_ids": list(covered_history_ids or []),
    }
    summary_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    boundary = {
        **plan.boundary_preview,
        "kind": CcTraceEvent.COMPACTION_BOUNDARY,
        "dry_run": False,
        "compaction_id": compaction_id,
        "boundary_id": compaction_id,
        "summary_path": str(summary_path),
    }
    return CompactionArtifact(
        compaction_id=compaction_id,
        summary_path=summary_path,
        boundary_event=boundary,
        summary=summary,
    )


def build_compacted_messages(
    *,
    plan: CompactionDryRunPlan,
    artifact: CompactionArtifact,
    messages: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Build compacted context as boundary summary plus preserved tail."""

    if not plan.can_compact:
        raise ValueError(f"Cannot compact blocked plan: {plan.reason}")
    preserved = [
        messages[index]
        for index in plan.preserve_message_indexes
        if 0 <= index < len(messages)
    ]
    boundary_message = {
        "role": "user",
        "content": [
            {
                "type": "text",
                "text": (
                    "<session-compaction-boundary>\n"
                    f"Compaction id: {artifact.compaction_id}\n"
                    f"Summary artifact: {artifact.summary_path}\n"
                    f"Session memory:\n{artifact.summary.get('summary_text', '')}\n"
                    f"Summarized message indexes: {plan.summarize_message_indexes}\n"
                    f"Preserved tool results: {plan.preserved_tool_result_ids}\n"
                    f"Preserved approvals: {plan.preserved_approval_ids}\n"
                    "Continue from the preserved recent context below. "
                    "Do not assume omitted details unless they are present in the summary artifact, "
                    "tool results, or preserved messages.\n"
                    "</session-compaction-boundary>"
                ),
            }
        ],
    }
    if preserved and preserved[0].get("role") == "user":
        first = dict(preserved[0])
        boundary_content = boundary_message["content"]
        first_content = first.get("content")
        if isinstance(first_content, list):
            first["content"] = [*boundary_content, *first_content]
        else:
            first["content"] = [
                *boundary_content,
                {"type": "text", "text": str(first_content or "")},
            ]
        return [first, *preserved[1:]]
    return [boundary_message, *preserved]

def _plan(
    *,
    blocked: bool,
    reason: str,
    messages: list[dict[str, Any]],
    preserve_message_indexes: set[int],
    preserve_trace_indexes: set[int],
    preserved_tool_result_ids: set[str],
    preserved_approval_ids: set[str],
    trigger: str,
    budget: dict[str, Any],
    summarize_message_indexes: list[int] | None = None,
) -> CompactionDryRunPlan:
    preserved_messages = sorted(preserve_message_indexes)
    summarized_messages = list(summarize_message_indexes or [])
    post_message_count = len(preserved_messages) + (1 if summarized_messages else 0)
    boundary = {
        "kind": CcTraceEvent.COMPACTION_CHECK,
        "trigger": trigger,
        "dry_run": True,
        "blocked": blocked,
        "reason": reason,
        "pre_message_count": len(messages),
        "post_message_count": post_message_count,
        "preserved_tool_result_ids": sorted(preserved_tool_result_ids),
        "preserved_approval_ids": sorted(preserved_approval_ids),
        "session_budget_snapshot": budget,
    }
    summary = {
        "schema_version": SUMMARY_SCHEMA_VERSION,
        "trigger": trigger,
        "dry_run": True,
        "blocked": blocked,
        "reason": reason,
        "preserved_tool_results": sorted(preserved_tool_result_ids),
        "approvals": sorted(preserved_approval_ids),
        "budget": budget,
        "next_action": "manual_compaction" if not blocked else "resolve_blocker",
    }
    return CompactionDryRunPlan(
        blocked=blocked,
        reason=reason,
        summarize_message_indexes=summarized_messages,
        preserve_message_indexes=preserved_messages,
        preserve_trace_indexes=sorted(preserve_trace_indexes),
        preserved_tool_result_ids=sorted(preserved_tool_result_ids),
        preserved_approval_ids=sorted(preserved_approval_ids),
        boundary_preview=boundary,
        summary_shape=summary,
    )


def _iter_content_blocks(message: dict[str, Any]) -> list[dict[str, Any]]:
    content = message.get("content")
    if isinstance(content, list):
        return [item for item in content if isinstance(item, dict)]
    return []


def _first_text(mapping: dict[str, Any], *keys: str) -> str:
    for key in keys:
        value = mapping.get(key)
        if value is not None:
            return str(value)
    return ""


def _last_index(events: list[dict[str, Any]], kinds: set[str]) -> int:
    for index in range(len(events) - 1, -1, -1):
        if str(events[index].get("kind") or "") in kinds:
            return index
    return -1


def _is_pending_approval(event: dict[str, Any]) -> bool:
    status = str(
        event.get("approval_status")
        or event.get("status")
        or event.get("decision")
        or "pending"
    ).lower()
    return status not in {"approved", "rejected", "denied", "resolved", "allowed"}


def _approval_id(event: dict[str, Any]) -> str:
    return _first_text(event, "approval_id", "request_id", "security_approval_id")


def _tool_result_id(event: dict[str, Any]) -> str:
    return _first_text(event, "tool_use_id", "id", "request_id")


def _is_failed_tool_result(event: dict[str, Any]) -> bool:
    if event.get("is_error") is True or event.get("ok") is False:
        return True
    status = str(event.get("status") or "").lower()
    if status in {"error", "failed", "failure"}:
        return True
    exit_code = event.get("exit_code")
    return isinstance(exit_code, int) and exit_code != 0


def _preserve_complete_tool_pairs(
    preserve_message_indexes: set[int],
    tool_use_message_by_id: dict[str, int],
    tool_result_message_by_id: dict[str, int],
) -> None:
    for tool_id, result_index in tool_result_message_by_id.items():
        if result_index not in preserve_message_indexes:
            continue
        _preserve_tool_pair_by_id(
            preserve_message_indexes,
            tool_id,
            tool_use_message_by_id,
            tool_result_message_by_id,
        )


def _preserve_tool_pair_by_id(
    preserve_message_indexes: set[int],
    tool_id: str,
    tool_use_message_by_id: dict[str, int],
    tool_result_message_by_id: dict[str, int],
) -> None:
    if tool_id in tool_use_message_by_id:
        preserve_message_indexes.add(tool_use_message_by_id[tool_id])
    if tool_id in tool_result_message_by_id:
        preserve_message_indexes.add(tool_result_message_by_id[tool_id])


def archive_summary_history(messages: list[dict[str, Any]], path: Path, *, history_ids: list[str] | None = None) -> None:
    """Persist complete evidence before producing any bounded summary input."""
    from .vault.logging_redaction import redact_value

    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".history-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            for index, message in enumerate(messages):
                record = {"archive_line": index + 1, "message": redact_value(message)}
                if history_ids and index < len(history_ids):
                    record["history_id"] = history_ids[index]
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def compact_summary_messages(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Follow OpenCode serialization: preserve prose/calls and bound tool output."""
    def tool_output(value: Any) -> str:
        text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        return text if len(text) <= 2000 else text[:2000] + "\n[truncated; full result retained in archive]"

    result = []
    for index, message in enumerate(messages):
        role, content = message.get("role"), message.get("content", "")
        item = {"role": role, "archive_line": index + 1}
        if isinstance(content, str):
            imported = None
            for prefix in ("[Historical tool evidence]\n", "[Historical tool evidence; already executed]\n"):
                if content.startswith(prefix):
                    try:
                        imported = json.loads(content[len(prefix):])
                    except ValueError:
                        pass
                    break
            if isinstance(imported, dict):
                status = imported.get("tool_status", imported.get("status"))
                output = imported.get("tool_output", imported.get("output", imported.get("text", "")))
                item.update({
                    "kind": imported.get("kind", "tool"),
                    "tool_name": imported.get("tool_name", imported.get("name")),
                    "tool_use_id": imported.get("tool_use_id", imported.get("id")),
                    "status": status,
                    "input": imported.get("tool_input", imported.get("input", {})),
                    "output": output if status in {"error", "failed", "failure"} else tool_output(output),
                })
            else:
                item["content"] = content
        elif isinstance(content, list):
            blocks = []
            for block in content:
                if not isinstance(block, dict):
                    continue
                kind = block.get("type")
                if kind == "tool_result":
                    blocks.append({**block, "content": block.get("content", "") if block.get("is_error") is True else tool_output(block.get("content", ""))})
                elif kind == "image":
                    blocks.append({"type": kind, "media_type": (block.get("source") or {}).get("media_type"), "content": "[image retained in archive]"})
                else:
                    blocks.append(dict(block))
            item["content"] = blocks
        else:
            item["content"] = content
        result.append(item)
    return result


def summarize_history(messages: list[dict[str, Any]], *, request, input_budget_tokens: int, progress=None, archive_path: str = "", continuation_goal: str = "") -> str:
    """Summarize bounded chunks without silently dropping old evidence."""
    from .prompt_assembly import build_session_compaction_prompt, SESSION_SUMMARY_HEADINGS

    # Include wire tool calls/results, not just assistant prose.
    def summary_value(value):
        if isinstance(value, dict):
            if value.get("type") == "image":
                return {"type": "image", "media_type": (value.get("source") or {}).get("media_type"), "content": "[image preserved in full session history]"}
            return {key: summary_value(item) for key, item in value.items()}
        if isinstance(value, list):
            return [summary_value(item) for item in value]
        return value
    source = compact_summary_messages(messages) if archive_path else messages
    serialized = [json.dumps(summary_value(message), ensure_ascii=False) for message in source]
    chunk_chars = max(512, (input_budget_tokens - 7000) * 2)
    pieces = []
    buffer = ""
    for text in serialized:
        for offset in range(0, len(text), chunk_chars):
            piece = text[offset:offset + chunk_chars]
            if buffer and len(buffer) + len(piece) + 2 > chunk_chars:
                pieces.append(buffer)
                buffer = ""
            buffer += piece + "\n"
    if buffer:
        pieces.append(buffer)
    summary = ""
    queue = [(piece, 0) for piece in pieces]
    index = 0
    while index < len(queue):
        piece, depth = queue[index]
        repair_reason = ""
        candidate = ""
        for attempt in range(2):
            prompt = build_session_compaction_prompt(previous_summary=summary, conversation=piece, repair_reason=repair_reason, archive_path=archive_path, continuation_goal=continuation_goal)
            if estimate_context_tokens(system_prompt="", messages=[{"role": "user", "content": prompt}], tools=[]) > input_budget_tokens:
                repair_reason = "summary_input_budget_exceeded"
                break
            try:
                candidate = request(prompt)
            except ValueError as exc:
                if str(exc) != "summary_output_truncated":
                    raise
                repair_reason = str(exc)
                break
            if not isinstance(candidate, str) or not candidate.strip():
                repair_reason = "summary_empty"
            else:
                positions = [candidate.find(heading) for heading in SESSION_SUMMARY_HEADINGS]
                if any(position < 0 for position in positions) or positions != sorted(positions):
                    repair_reason = "summary_structure_invalid"
                elif len(candidate) > 24000:
                    repair_reason = "summary_output_too_large"
                else:
                    repair_reason = ""
                    break
        if repair_reason:
            if repair_reason in {"summary_output_truncated", "summary_input_budget_exceeded"} and depth < 4 and len(piece) > 4096:
                middle = len(piece) // 2
                queue[index:index + 1] = [(piece[:middle], depth + 1), (piece[middle:], depth + 1)]
                continue
            raise ValueError(repair_reason)
        summary = candidate.strip()
        index += 1
        if progress:
            progress(summary, index, len(queue))
    if not summary:
        raise ValueError("summary_empty")
    if archive_path:
        summary += "\n\nПодробная история до сжатия: `" + archive_path + "`. Формат JSONL; archive_line — номер строки, message — полная запись.\n"
    return summary


def read_compaction_guards(artifact_dir: str) -> list[dict[str, Any]]:
    """Retain unresolved guards until a structural allowed verdict clears them."""
    path = Path(artifact_dir) / "cc_backend_trace.jsonl"
    events = []
    if not path.exists():
        return events
    for line in path.read_text(encoding="utf-8").splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if not isinstance(event, dict):
            continue
        if event.get("kind") == CcTraceEvent.TOOL_RESULT and _tool_result_id(event):
            events = [item for item in events if not (
                item.get("kind") == CcTraceEvent.TOOL_APPROVAL_REQUIRED
                and _tool_result_id(item) == _tool_result_id(event)
            )]
        if event.get("kind") == CcTraceEvent.POST_TURN_VERDICT and event.get("action") == "completion_allowed":
            events = [item for item in events if item.get("kind") not in {CcTraceEvent.POST_TURN_RETRY_REQUIRED, CcTraceEvent.POST_TURN_PENDING_WRITE_DETECTED}]
        if event.get("kind") in {
            CcTraceEvent.TOOL_APPROVAL_REQUIRED, CcTraceEvent.POST_TURN_RETRY_REQUIRED,
            CcTraceEvent.POST_TURN_PENDING_WRITE_DETECTED, CcTraceEvent.TOOL_RESULT,
            CcTraceEvent.FACT_CHECK_PASSED, CcTraceEvent.FACT_CHECK_CORRECTION_FORCED,
        }:
            events.append(event)
    return events
