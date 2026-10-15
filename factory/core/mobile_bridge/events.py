"""Normalize cc-backend trace records into mobile-client events.

The trace is structured JSONL using `factory/core/trace_taxonomy.py`. The mobile
layer does not scrape human-readable output: it normalizes frames into the
durable `mobile_events` journal with monotonic `seq`. After a disconnect,
`after_seq` retrieves only missed events.

Text deltas are not journaled individually: `model.round_response.text_full`
contains their complete result, avoiding one durable row per delta. Reasoning
deltas have no equivalent complete trace record, so they are joined into one
`model.reasoning` fragment. Live SSE receives both through live hooks.
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Callable

from ..agent_foundation import extract_memory_write_blocks
from ..isolation.artifact_map import read_artifact_route
from ..runtime import get_db_path, get_workspace_root
from ..schema import open_db
from ..trace_taxonomy import CcTraceEvent
from ..vault.logging_redaction import redact_text

def _with_memory_note(text: str) -> str:
    cleaned, _writes = extract_memory_write_blocks(text)
    return cleaned

_MAX_TEXT_CHARS = 20_000
_MAX_PREVIEW_CHARS = 4_000
_MAX_REASONING_CHARS = 8_000

_ACTIVE_STATUSES = ("queued", "processing", "waiting_approval", "cancelling", "retry")

_FINAL_STATUSES = ("ok", "fail", "dead_letter", "cancelled", "timeout")

# State of queued/processing/retry intentionally omitted: they introduce noise and arrive after trace events.
_NOTEWORTHY_STATUSES = ("waiting_approval", "cancelling", *_FINAL_STATUSES)

_DEFAULT_RECENT_WINDOW_S = 86_400

# Subagent log lines (``subagents.jsonl``) share the same ``mobile_events`` table with trace tasks,
# with a unique index of (task_id, src_line).
# Thus, subagent line numbering starts with an offset: deduplication remains
# idempotent, but file numbers do not overlap.
_SUBAGENT_SRC_LINE_OFFSET = 5_000_000

# Subagent file event → mobile log kind. Raw file and normalized log are different layers, like trace and bridge events.
_SUBAGENT_EVENT_KINDS = {
    "start": "subagent.start",
    "progress": "subagent.progress",
    "finish": "subagent.finish",
}

LiveHook = Callable[[dict[str, Any]], None]


def normalize_subagent_event(
    raw: dict[str, Any],
    *,
    task_id: str = "",
    session_id: str | None = None,
) -> dict[str, Any] | None:
    """Normalizes the `subagents.jsonl` line into a mobile event entry."""
    kind = _SUBAGENT_EVENT_KINDS.get(str(raw.get("event") or ""))
    if kind is None:
        return None
    raw_ts = raw.get("ts")
    ts = int(raw_ts) if isinstance(raw_ts, (int, float)) else None
    discovered = str(raw.get("session_id") or "").strip()
    payload: dict[str, Any] = {
        "task_id": task_id,
        "subagent_id": str(raw.get("subagent_id") or ""),
        "parent_task_id": str(raw.get("parent_task_id") or ""),
    }
    if discovered:
        payload["session_id"] = discovered
    elif session_id:
        payload["session_id"] = session_id
    for key, value in raw.items():
        if key in {"event", "ts", "session_id", "subagent_id", "parent_task_id"}:
            continue
        if isinstance(value, str):
            payload[key] = _clip(value, _MAX_PREVIEW_CHARS)
        elif isinstance(value, (dict, list, int, float, bool)) or value is None:
            payload[key] = value
    if not payload["subagent_id"]:
        return None
    return _frame(kind, ts, payload)


def trace_path_for_task(task_id: str, workspace_root: str | Path | None = None) -> Path:
    root = Path(workspace_root) if workspace_root is not None else Path(get_workspace_root())
    return root / f"temp_execution_{task_id[:8]}" / "cc_backend_trace.jsonl"


def classify_tool_status(preview: str) -> str:
    """Structural classification of the tool’s result (as in the TUI trace editor)."""
    try:
        payload = json.loads(str(preview or ""))
    except (ValueError, TypeError):
        payload = None
    if isinstance(payload, dict) and payload.get("error"):
        return "error"
    lowered = str(preview or "").lower()
    if lowered.startswith("error") or "command timed out" in lowered:
        return "error"
    if "(exit code:" in lowered:
        return "exit"
    return "done"


def _clip(value: Any, limit: int) -> str:
    text = value if isinstance(value, str) else str(value or "")
    if len(text) <= limit:
        return text
    return text[:limit] + "…"


def _frame(kind: str, ts: int | None, payload: dict[str, Any]) -> dict[str, Any]:
    return {"kind": kind, "ts": ts, "payload": payload}


def _stored_output_path(artifact_workdir: Path | None, tool_use_id: str) -> str | None:
    if artifact_workdir is None or not tool_use_id:
        return None
    relative = Path(".tool_results") / f"{tool_use_id}.txt"
    return str(relative) if (artifact_workdir / relative).is_file() else None


def normalize_trace_event(
    event: dict[str, Any],
    *,
    task_id: str = "",
    session_id: str | None = None,
    artifact_workdir: Path | None = None,
) -> dict[str, Any] | None:
    kind = str(event.get("kind") or "")
    if not kind:
        return None
    raw_ts = event.get("ts")
    ts = int(raw_ts) if isinstance(raw_ts, (int, float)) else None

    if kind in {CcTraceEvent.COMPACTION_CHECK, CcTraceEvent.COMPACTION_STARTED,
                CcTraceEvent.COMPACTION_PROGRESS, CcTraceEvent.COMPACTION_COMPLETED,
                CcTraceEvent.COMPACTION_SKIPPED, CcTraceEvent.COMPACTION_FAILED,
                CcTraceEvent.COMPACTION_BOUNDARY}:
        return _frame(kind, ts, {key: value for key, value in event.items() if key not in {"kind", "ts"}})

    if kind == CcTraceEvent.TASK_START:
        return _frame(
            "task.start",
            ts,
            {
                "task_id": task_id,
                "model": str(event.get("model") or ""),
                "model_context_window": event.get("model_context_window"),
                "filesystem_mode": str(event.get("filesystem_mode") or ""),
                "session_id": str(event.get("current_session") or "") or session_id or "",
                "parent_task_id": event.get("parent_task_id"),
                "delegate_depth": int(event.get("delegate_depth") or 0),
            },
        )

    if kind == CcTraceEvent.TASK_FINISH:
        blocked = event.get("approval_blocked_tools")
        return _frame(
            "task.finish",
            ts,
            {
                "task_id": task_id,
                "status": str(event.get("status") or ""),
                "reason": str(event.get("reason") or ""),
                "rounds": int(event.get("rounds") or 0),
                "tool_calls_count": int(event.get("tool_calls_count") or 0),
                "output_preview": _clip(event.get("output_preview"), _MAX_PREVIEW_CHARS),
                "approval_blocked_count": len(blocked) if isinstance(blocked, list) else 0,
            },
        )

    if kind == CcTraceEvent.MODEL_ROUND_RESPONSE:
        return _frame(
            "model.round_response",
            ts,
            {
                "round": int(event.get("round") or 0),
                "text": _clip(
                    _with_memory_note(event.get("text_full") or event.get("text_preview") or ""),
                    _MAX_TEXT_CHARS,
                ),
                "usage": event.get("usage") if isinstance(event.get("usage"), dict) else {},
                "latency_ms": int(event.get("latency_ms") or 0),
                "http_error": _clip(event.get("http_error"), 500),
                "content_types": event.get("content_types") if isinstance(event.get("content_types"), list) else [],
            },
        )

    if kind == CcTraceEvent.MODEL_TEXT_DELTA:
        return _frame(
            "model.text_delta",
            ts,
            {"round": int(event.get("round") or 0), "text": str(event.get("text") or "")},
        )

    if kind == CcTraceEvent.MODEL_REASONING_DELTA:
        return _frame(
            "model.reasoning_delta",
            ts,
            {"round": int(event.get("round") or 0), "text": str(event.get("text") or "")},
        )

    if kind == CcTraceEvent.TOOL_REQUEST_BATCH:
        tools = event.get("tools") if isinstance(event.get("tools"), list) else []
        normalized_tools = [
            {
                "id": str(item.get("id") or ""),
                "name": str(item.get("name") or "tool"),
                "input": item.get("input") if isinstance(item.get("input"), dict) else {},
            }
            for item in tools
            if isinstance(item, dict)
        ]
        return _frame(
            "tool.request_batch",
            ts,
            {"round": int(event.get("round") or 0), "tools": normalized_tools},
        )

    if kind == CcTraceEvent.TOOL_RESULT:
        preview = str(event.get("raw_result_preview") or event.get("result_for_model_preview") or "")
        return _frame(
            "tool.result",
            ts,
            {
                "round": int(event.get("round") or 0),
                "tool_use_id": str(event.get("tool_use_id") or ""),
                "tool_name": str(event.get("tool_name") or ""),
                "tool_input": event.get("tool_input") if isinstance(event.get("tool_input"), dict) else {},
                "result_preview": _clip(preview, _MAX_PREVIEW_CHARS),
                "context_output": redact_text(str(event.get("context_output") or preview)),
                "result_len": int(event.get("raw_result_len") or len(preview)),
                "duration_ms": int(event.get("duration_ms") or 0),
                "status": classify_tool_status(preview),
                "stored_path": _stored_output_path(
                    artifact_workdir, str(event.get("tool_use_id") or "")
                ),
            },
        )

    if kind == CcTraceEvent.TOOL_APPROVAL_REQUIRED:
        return _frame(
            "approval.required",
            ts,
            {
                "request_id": str(event.get("tool_use_id") or ""),
                "tool": str(event.get("tool") or ""),
                "input": event.get("input") if isinstance(event.get("input"), dict) else {},
                "target": _clip(event.get("target"), 16_000 if event.get('tool') in {'calendar_create_event','calendar_move_event'} else 1_000),
                "signature": str(event.get("signature") or ""),
                "session_rule": event.get("session_rule") if isinstance(event.get("session_rule"), dict) else None,
                "reason": _clip(event.get("reason"), 1_000),
                "round": int(event.get("round") or 0),
            },
        )

    payload = {key: (_clip(value, 1_000) if isinstance(value, str) else value) for key, value in event.items() if key != "kind"}
    return _frame(kind, ts, payload)


class TraceJournaler:
    """Appends task trace tails and aggregates normalized events into the log."""

    def __init__(
        self,
        store: Any,
        *,
        workspace_root: str | Path | None = None,
        active_task_provider: Callable[[], list[dict[str, Any]]] | None = None,
        recent_window_s: int = _DEFAULT_RECENT_WINDOW_S,
    ) -> None:
        self.store = store
        self._workspace_root = Path(workspace_root) if workspace_root is not None else None
        self._provider = active_task_provider or self._db_tasks
        self._recent_window_s = max(60, int(recent_window_s))
        self._cursors: dict[str, dict[str, int]] = {}
        self._subagent_cursors: dict[str, dict[str, int]] = {}
        self._sessions: dict[str, str | None] = {}
        self._reasoning: dict[str, list[str]] = {}
        self._statuses: dict[str, str] = {}
        self._finished_tasks: set[str] = set()
        self._live_hooks: list[LiveHook] = []

    def add_live_hook(self, hook: LiveHook) -> None:
        self._live_hooks.append(hook)

    def _root(self) -> Path:
        return self._workspace_root or Path(get_workspace_root())

    def _db_tasks(self) -> list[dict[str, Any]]:
        threshold = int(time.time()) - self._recent_window_s
        placeholders = ", ".join(["?"] * len(_ACTIVE_STATUSES))
        query = (
            "SELECT task_id, status, metadata, created_at, error_message FROM tasks "
            f"WHERE status IN ({placeholders}) OR created_at >= ? ORDER BY created_at ASC"
        )
        with open_db(get_db_path()) as conn:
            rows = conn.execute(query, (*_ACTIVE_STATUSES, threshold)).fetchall()
        tasks: list[dict[str, Any]] = []
        for row in rows:
            metadata: dict[str, Any] = {}
            if row[2]:
                try:
                    loaded = json.loads(row[2])
                    if isinstance(loaded, dict):
                        metadata = loaded
                except json.JSONDecodeError:
                    metadata = {}
            tasks.append(
                {
                    "task_id": row[0],
                    "status": row[1],
                    "metadata": metadata,
                    "created_at": int(row[3] or 0),
                    "error_message": str(row[4] or ""),
                }
            )
        return tasks

    def _emit_live(self, frame: dict[str, Any], *, session_id: str | None, task_id: str, seq: int | None = None) -> None:
        event = {"kind": frame["kind"], "ts": frame.get("ts"), "session_id": session_id, "task_id": task_id, "payload": frame["payload"]}
        if seq is not None:
            event["seq"] = seq
        for hook in list(self._live_hooks):
            try:
                hook(event)
            except Exception:
                continue

    def _buffer_reasoning(self, task_id: str, text: str) -> None:
        if not text:
            return
        self._reasoning.setdefault(task_id, []).append(text)

    def _flush_reasoning(self, task_id: str, *, session_id: str | None, reasoning_key: str | None = None) -> int:
        parts = self._reasoning.pop(reasoning_key or task_id, None)
        if not parts:
            return 0
        text = _clip("".join(parts), _MAX_TEXT_CHARS)
        if not text.strip():
            return 0
        seq = self.store.append_event(
            kind="model.reasoning",
            payload={"text": text},
            session_id=session_id,
            task_id=task_id,
        )
        self._emit_live({"kind": "model.reasoning", "ts": None, "payload": {"text": text}}, session_id=session_id, task_id=task_id, seq=seq)
        return 1

    def _handle_raw(self, raw: dict[str, Any], *, task_id: str, line_no: int, artifact_workdir: Path) -> int:
        kind = str(raw.get("kind") or "")
        session_id = self._sessions.get(task_id)
        agent_id = str(raw.get("agent_session_id") or "")
        child = bool(raw.get("subagent_id") and agent_id)
        reasoning_key = task_id + ":" + agent_id if child else task_id

        if kind == CcTraceEvent.TASK_START:
            discovered = str(raw.get("current_session") or "").strip()
            if discovered:
                session_id = discovered
                self._sessions[task_id] = discovered

        if child:
            session_id = agent_id

        frame = normalize_trace_event(
            raw,
            task_id=task_id,
            session_id=session_id,
            artifact_workdir=artifact_workdir,
        )
        if frame is None:
            return 0

        if child:
            frame["payload"].update({key: raw.get(key) for key in (
                "agent_session_id", "parent_session_id", "root_session_id", "subagent_id"
            )})
            if kind == CcTraceEvent.TASK_START:
                frame["payload"]["user_message"] = str(raw.get("user_message") or "")

        if child and kind in {CcTraceEvent.MODEL_REASONING_DELTA, CcTraceEvent.MODEL_TEXT_DELTA}:
            seq = self.store.append_event(kind=frame["kind"], payload=frame["payload"],
                session_id=session_id, task_id=task_id, ts=frame.get("ts"), src_line=line_no)
            if seq is not None:
                self._emit_live(frame, session_id=session_id, task_id=task_id, seq=seq)
            return int(seq is not None)

        if kind == CcTraceEvent.MODEL_REASONING_DELTA:
            self._buffer_reasoning(reasoning_key, str(frame["payload"].get("text") or ""))
            self._emit_live(frame, session_id=session_id, task_id=task_id)
            buffered = "".join(self._reasoning.get(reasoning_key, []))
            if len(buffered) >= _MAX_REASONING_CHARS:
                return self._flush_reasoning(task_id, session_id=session_id, reasoning_key=reasoning_key)
            return 0

        if kind == CcTraceEvent.MODEL_TEXT_DELTA:
            flushed = self._flush_reasoning(task_id, session_id=session_id, reasoning_key=reasoning_key)
            self._emit_live(frame, session_id=session_id, task_id=task_id)
            return flushed

        flushed = self._flush_reasoning(task_id, session_id=session_id, reasoning_key=reasoning_key)
        seq = self.store.append_event(
            kind=frame["kind"],
            payload=frame["payload"],
            session_id=session_id,
            task_id=task_id,
            ts=frame.get("ts"),
            src_line=line_no,
        )
        if kind == CcTraceEvent.TASK_FINISH and not child:
            self._finished_tasks.add(task_id)
        if seq is not None:
            self._emit_live(frame, session_id=session_id, task_id=task_id, seq=seq)
        return flushed + (1 if seq is not None else 0)

    def _poll_task(self, task: dict[str, Any]) -> int:
        task_id = str(task.get("task_id") or "")
        if not task_id:
            return 0
        path = trace_path_for_task(task_id, self._root())

        if path.exists():
            cursor = self._cursors.get(task_id)
            if cursor is None:
                metadata = task.get("metadata") if isinstance(task.get("metadata"), dict) else {}
                self._sessions.setdefault(task_id, str(metadata.get("session") or "").strip() or None)
                cursor = {
                    "lines_seen": 0,
                    "offset": 0,
                    "processed_lines": self.store.last_trace_line(
                        task_id, below=_SUBAGENT_SRC_LINE_OFFSET
                    ),
                }
                self._cursors[task_id] = cursor

            appended = 0
            with path.open("r", encoding="utf-8") as handle:
                handle.seek(int(cursor["offset"]))
                while True:
                    position = handle.tell()
                    line = handle.readline()
                    if not line:
                        break
                    if not line.endswith("\n"):
                        handle.seek(position)
                        break
                    cursor["lines_seen"] += 1
                    cursor["offset"] = handle.tell()
                    if cursor["lines_seen"] <= int(cursor["processed_lines"]):
                        continue
                    try:
                        raw = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if not isinstance(raw, dict):
                        continue
                    appended += self._handle_raw(
                        raw,
                        task_id=task_id,
                        line_no=cursor["lines_seen"],
                        artifact_workdir=path.parent,
                    )
        else:
            appended = 0

        metadata = task.get("metadata") if isinstance(task.get("metadata"), dict) else {}
        self._sessions.setdefault(task_id, str(metadata.get("session") or "").strip() or None)
        appended += self._poll_subagents(task_id, artifact_workdir=path.parent)
        return appended

    def _poll_subagents(self, task_id: str, *, artifact_workdir: Path) -> int:
        """Transfers lines from `subagents.jsonl` to the mobile log.

        The parent job may run inside a guest KVM virtual machine.
        The KVM dispatcher writes a route file next to the host trace, which points to
        the host mirror of the guest artifact directory within the exported workspace; there, the log appears via virtiofs. Without a route file
        (off-mode, legacy tasks), the historical trace directory is read.
        Events are addressed to a specific session, from which they are retrieved by SSE and the subagent list API.
        """
        journal_dir = read_artifact_route(artifact_workdir) or artifact_workdir
        path = journal_dir / "subagents.jsonl"
        if not path.exists():
            return 0
        cursor = self._subagent_cursors.get(task_id)
        if cursor is None:
            cursor = {"lines_seen": 0, "offset": 0}
            self._subagent_cursors[task_id] = cursor

        session_id = self._sessions.get(task_id)
        metadata_session = session_id
        appended = 0
        with path.open("r", encoding="utf-8") as handle:
            handle.seek(int(cursor["offset"]))
            while True:
                position = handle.tell()
                line = handle.readline()
                if not line:
                    break
                if not line.endswith("\n"):
                    # Incomplete line—process may have crashed mid-write; we'll return to it after a newline appears.
                    handle.seek(position)
                    break
                cursor["lines_seen"] += 1
                cursor["offset"] = handle.tell()
                try:
                    raw = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(raw, dict):
                    continue
                frame = normalize_subagent_event(
                    raw, task_id=task_id, session_id=session_id
                )
                if frame is None:
                    continue
                payload = frame["payload"]
                event_session = str(payload.get("session_id") or "").strip() or metadata_session
                if event_session:
                    self._sessions[task_id] = event_session
                src_line = _SUBAGENT_SRC_LINE_OFFSET + cursor["lines_seen"]
                seq = self.store.append_event(
                    kind=frame["kind"],
                    payload=payload,
                    session_id=event_session,
                    task_id=task_id,
                    ts=frame.get("ts"),
                    src_line=src_line,
                )
                if seq is None:
                    continue
                self._emit_live(
                    frame, session_id=event_session, task_id=task_id, seq=seq
                )
                appended += 1
        return appended

    def _emit_status_changes(self, tasks: list[dict[str, Any]]) -> int:
        appended = 0
        for task in tasks:
            task_id = str(task.get("task_id") or "")
            status = str(task.get("status") or "")
            if not task_id or not status:
                continue
            metadata = task.get("metadata") if isinstance(task.get("metadata"), dict) else {}
            session_id = self._sessions.get(task_id) or str(metadata.get("session") or "").strip() or None
            self._sessions[task_id] = session_id
            previous = self._statuses.get(task_id)
            self._statuses[task_id] = status
            first_success = previous is None and status == "ok" and not self.store.event_exists(
                kind="task.status", session_id=session_id, task_id=task_id,
                payload_match=("$.status", "ok"),
            )
            if (previous is not None or first_success) and previous != status and status in _NOTEWORTHY_STATUSES:
                # Persisted success is distinct from an executor finish (which may requeue).
                if status == "ok" or status not in _FINAL_STATUSES or task_id not in self._finished_tasks:
                    payload = {"task_id": task_id, "status": status, "previous": previous}
                    seq = self.store.append_event(
                        kind="task.status",
                        payload=payload,
                        session_id=session_id,
                        task_id=task_id,
                    )
                    if seq is not None:
                        self._emit_live(
                            {"kind": "task.status", "ts": None, "payload": payload},
                            session_id=session_id,
                            task_id=task_id,
                            seq=seq,
                        )
                        appended += 1

            error_message = str(task.get("error_message") or "").strip()
            if status in {"fail", "dead_letter", "timeout"} and error_message:
                clipped_error = _clip(error_message, _MAX_PREVIEW_CHARS)
                if not self.store.event_exists(
                    kind="task.error",
                    session_id=session_id,
                    task_id=task_id,
                    payload_match=("$.error_message", clipped_error),
                ):
                    payload = {
                        "task_id": task_id,
                        "error_message": clipped_error,
                    }
                    seq = self.store.append_event(
                        kind="task.error",
                        payload=payload,
                        session_id=session_id,
                        task_id=task_id,
                    )
                    if seq is not None:
                        self._emit_live(
                            {"kind": "task.error", "ts": None, "payload": payload},
                            session_id=session_id,
                            task_id=task_id,
                            seq=seq,
                        )
                        appended += 1
        return appended

    def poll(self) -> int:
        tasks = self._provider()
        appended = 0
        for task in tasks:
            task_id = str(task.get("task_id") or "")
            appended += self._poll_task(task)
            if task_id in self._reasoning and str(task.get("status") or "") in _FINAL_STATUSES:
                appended += self._flush_reasoning(task_id, session_id=self._sessions.get(task_id))
        appended += self._emit_status_changes(tasks)
        return appended
