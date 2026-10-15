from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

from .vault.logging_redaction import redact_text, redact_value


SUPPORTED_SOURCES = ("claude", "opencode", "codex")
MAX_MESSAGE_CHARS = 50_000


def _epoch_seconds(value: Any) -> int:
    if isinstance(value, (int, float)):
        number = float(value)
        if number > 10_000_000_000:
            number /= 1000
        return max(0, int(number))
    if isinstance(value, str) and value:
        try:
            return max(0, int(datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()))
        except ValueError:
            return 0
    return 0


def _text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        rendered = value
    else:
        try:
            rendered = json.dumps(redact_value(value), ensure_ascii=False, indent=2)
        except (TypeError, ValueError):
            rendered = str(value)
    return redact_text(rendered).strip()


def _first_nonempty(*values: Any) -> str:
    for value in values:
        rendered = str(value or "").strip()
        if rendered:
            return rendered
    return ""


def _read_json_lines(path: Path) -> Iterable[dict[str, Any]]:
    try:
        with path.open("r", encoding="utf-8", errors="replace") as handle:
            for raw in handle:
                if not raw.strip():
                    continue
                try:
                    value = json.loads(raw)
                except (json.JSONDecodeError, ValueError):
                    continue
                if isinstance(value, dict):
                    yield value
    except OSError:
        return


def _read_only_db(path: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True, timeout=2)
    connection.row_factory = sqlite3.Row
    return connection


def _json_object(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return raw
    if not isinstance(raw, str) or not raw:
        return {}
    try:
        value = json.loads(raw)
    except (json.JSONDecodeError, ValueError):
        return {}
    return value if isinstance(value, dict) else {}


@dataclass(frozen=True)
class ExternalSessionRecord:
    source: str
    session_id: str
    title: str
    cwd: str
    created_at: int
    updated_at: int
    model: str = ""
    agent: str = ""
    message_count: int = 0
    native_resume: bool = False
    locator: str = ""

    def public(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "session_id": self.session_id,
            "title": self.title[:240],
            "cwd": self.cwd,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
            "model": self.model,
            "agent": self.agent,
            "message_count": self.message_count,
            "native_resume": self.native_resume,
        }


class ExternalSessionCatalog:
    def __init__(
        self,
        *,
        claude_root: str | Path | None = None,
        codex_root: str | Path | None = None,
        opencode_db: str | Path | None = None,
        cache_ttl_s: float = 15.0,
    ) -> None:
        home = Path.home()
        self.claude_root = Path(
            claude_root
            or os.environ.get("FACTORY_EXTERNAL_CLAUDE_HOME")
            or os.environ.get("CLAUDE_CONFIG_DIR")
            or home / ".claude"
        ).expanduser()
        self.codex_root = Path(
            codex_root
            or os.environ.get("FACTORY_EXTERNAL_CODEX_HOME")
            or os.environ.get("CODEX_HOME")
            or home / ".codex"
        ).expanduser()
        self.opencode_db = Path(
            opencode_db
            or os.environ.get("FACTORY_EXTERNAL_OPENCODE_DB")
            or home / ".local" / "share" / "opencode" / "opencode.db"
        ).expanduser()
        self.cache_ttl_s = max(0.0, float(cache_ttl_s))
        self._cache_at = 0.0
        self._records: dict[tuple[str, str], ExternalSessionRecord] = {}
        self._lock = threading.RLock()

    def list_sessions(
        self,
        *,
        source: str = "",
        query: str = "",
        limit: int = 250,
        refresh: bool = False,
    ) -> dict[str, Any]:
        normalized_source = source.strip().lower()
        if normalized_source and normalized_source not in SUPPORTED_SOURCES:
            raise ValueError(f"unsupported external session source: {source}")
        records = self._scan(refresh=refresh)
        if normalized_source:
            records = [item for item in records if item.source == normalized_source]
        needle = query.strip().casefold()
        if needle:
            records = [
                item
                for item in records
                if needle
                in "\n".join((item.title, item.cwd, item.model, item.agent, item.session_id)).casefold()
            ]
        counts = {name: 0 for name in SUPPORTED_SOURCES}
        for item in self._records.values():
            counts[item.source] += 1
        bounded_limit = min(max(1, int(limit)), 1000)
        return {
            "sessions": [item.public() for item in records[:bounded_limit]],
            "counts": counts,
            "total": len(records),
            "scanned_at": int(self._cache_at),
        }

    def get_session(
        self,
        source: str,
        session_id: str,
        *,
        offset: int = 0,
        limit: int = 400,
    ) -> dict[str, Any]:
        record = self._get_record(source, session_id)
        messages = self._messages(record)
        start = max(0, int(offset))
        page_size = min(max(1, int(limit)), 1000)
        page = messages[start : start + page_size]
        next_offset = start + len(page)
        payload = record.public()
        payload.update(
            {
                "messages": page,
                "total_messages": len(messages),
                "next_offset": next_offset if next_offset < len(messages) else None,
            }
        )
        return payload

    def import_messages(
        self,
        source: str,
        session_id: str,
        *,
        max_messages: int = 200,
        max_chars: int = 200_000,
    ) -> tuple[ExternalSessionRecord, list[dict[str, Any]], int]:
        record = self._get_record(source, session_id)
        # Import is a durable history copy; model context is budgeted separately.
        messages = self._messages(record)
        for index, item in enumerate(messages):
            item["history_id"] = f"external:{record.source}:{record.session_id}:{index}"
            item["order"] = index
        return record, messages, 0

    def _scan(self, *, refresh: bool = False) -> list[ExternalSessionRecord]:
        with self._lock:
            now = time.time()
            if not refresh and self._records and now - self._cache_at <= self.cache_ttl_s:
                return sorted(self._records.values(), key=lambda item: item.updated_at, reverse=True)
            records: list[ExternalSessionRecord] = []
            records.extend(self._scan_claude())
            records.extend(self._scan_codex())
            records.extend(self._scan_opencode())
            self._records = {(item.source, item.session_id): item for item in records}
            self._cache_at = now
            return sorted(self._records.values(), key=lambda item: item.updated_at, reverse=True)

    def _get_record(self, source: str, session_id: str) -> ExternalSessionRecord:
        key = (source.strip().lower(), session_id.strip())
        self._scan()
        record = self._records.get(key)
        if record is None:
            self._scan(refresh=True)
            record = self._records.get(key)
        if record is None:
            raise KeyError(f"external session not found: {key[0]}:{key[1]}")
        return record

    def _scan_claude(self) -> list[ExternalSessionRecord]:
        projects = self.claude_root / "projects"
        if not projects.is_dir():
            return []
        records: dict[str, ExternalSessionRecord] = {}
        for index_path in projects.glob("*/sessions-index.json"):
            try:
                payload = json.loads(index_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            entries = payload.get("entries") if isinstance(payload, dict) else None
            if not isinstance(entries, list):
                continue
            for raw in entries:
                if not isinstance(raw, dict) or raw.get("isSidechain"):
                    continue
                session_id = str(raw.get("sessionId") or "").strip()
                path = Path(str(raw.get("fullPath") or ""))
                if not session_id or not self._allowed_claude_path(path):
                    continue
                records[session_id] = ExternalSessionRecord(
                    source="claude",
                    session_id=session_id,
                    title=_first_nonempty(raw.get("firstPrompt"), f"Claude {session_id[:8]}"),
                    cwd=str(raw.get("projectPath") or ""),
                    created_at=_epoch_seconds(raw.get("created")),
                    updated_at=_epoch_seconds(raw.get("modified") or raw.get("fileMtime")),
                    message_count=max(0, int(raw.get("messageCount") or 0)),
                    native_resume=True,
                    locator=str(path),
                )
        for path in projects.glob("*/*.jsonl"):
            if path.parent.name == "subagents" or path.stem in records:
                continue
            metadata = self._claude_file_metadata(path)
            if metadata is not None:
                records[metadata.session_id] = metadata
        return list(records.values())

    def _allowed_claude_path(self, path: Path) -> bool:
        try:
            path.resolve().relative_to((self.claude_root / "projects").resolve())
            return path.is_file() and path.suffix == ".jsonl" and "subagents" not in path.parts
        except (OSError, ValueError):
            return False

    def _claude_file_metadata(self, path: Path) -> ExternalSessionRecord | None:
        if not self._allowed_claude_path(path):
            return None
        session_id = path.stem
        cwd = ""
        first_prompt = ""
        created_at = 0
        message_count = 0
        for item in _read_json_lines(path):
            if item.get("isSidechain"):
                continue
            cwd = _first_nonempty(cwd, item.get("cwd"))
            session_id = _first_nonempty(item.get("sessionId"), session_id)
            timestamp = _epoch_seconds(item.get("timestamp"))
            if timestamp and not created_at:
                created_at = timestamp
            if item.get("type") in {"user", "assistant"}:
                message_count += 1
            if not first_prompt and item.get("type") == "user":
                first_prompt = self._claude_user_text(item)
        try:
            updated_at = int(path.stat().st_mtime)
        except OSError:
            updated_at = created_at
        return ExternalSessionRecord(
            source="claude",
            session_id=session_id,
            title=_first_nonempty(first_prompt, f"Claude {session_id[:8]}"),
            cwd=cwd,
            created_at=created_at,
            updated_at=updated_at,
            message_count=message_count,
            native_resume=True,
            locator=str(path),
        )

    def _scan_codex(self) -> list[ExternalSessionRecord]:
        state_db = self.codex_root / "state_5.sqlite"
        if state_db.is_file():
            try:
                with _read_only_db(state_db) as connection:
                    rows = connection.execute(
                        "SELECT id, name, title, cwd, created_at, updated_at, created_at_ms, "
                        "updated_at_ms, model, agent_nickname, rollout_path, preview, "
                        "first_user_message FROM threads WHERE preview <> '' OR title <> ''"
                    ).fetchall()
                message_counts: dict[str, int] = {}
                history_db = self.codex_root / "thread_history_1.sqlite"
                if history_db.is_file():
                    try:
                        with _read_only_db(history_db) as connection:
                            message_counts = {
                                str(row["thread_id"]): int(row["message_count"] or 0)
                                for row in connection.execute(
                                    "SELECT thread_id, COUNT(*) AS message_count FROM thread_items "
                                    "WHERE item_type IN ('userMessage', 'agentMessage') GROUP BY thread_id"
                                ).fetchall()
                            }
                    except (sqlite3.Error, OSError):
                        message_counts = {}
                return [
                    ExternalSessionRecord(
                        source="codex",
                        session_id=str(row["id"]),
                        title=_first_nonempty(
                            row["name"],
                            row["title"],
                            row["preview"],
                            row["first_user_message"],
                            f"Codex {str(row['id'])[:8]}",
                        ),
                        cwd=str(row["cwd"] or ""),
                        created_at=_epoch_seconds(row["created_at_ms"] or row["created_at"]),
                        updated_at=_epoch_seconds(row["updated_at_ms"] or row["updated_at"]),
                        model=str(row["model"] or ""),
                        agent=str(row["agent_nickname"] or ""),
                        message_count=message_counts.get(str(row["id"]), 0),
                        native_resume=True,
                        locator=str(row["rollout_path"] or ""),
                    )
                    for row in rows
                ]
            except (sqlite3.Error, OSError, KeyError):
                pass
        return self._scan_codex_rollouts()

    def _scan_codex_rollouts(self) -> list[ExternalSessionRecord]:
        sessions = self.codex_root / "sessions"
        if not sessions.is_dir():
            return []
        records: list[ExternalSessionRecord] = []
        for path in sessions.rglob("rollout-*.jsonl"):
            first = next(iter(_read_json_lines(path)), {})
            payload = first.get("payload") if isinstance(first.get("payload"), dict) else {}
            if first.get("type") != "session_meta" or not payload:
                continue
            session_id = str(payload.get("id") or payload.get("session_id") or path.stem).strip()
            try:
                stat = path.stat()
            except OSError:
                continue
            records.append(
                ExternalSessionRecord(
                    source="codex",
                    session_id=session_id,
                    title=f"Codex {session_id[:8]}",
                    cwd=str(payload.get("cwd") or ""),
                    created_at=_epoch_seconds(payload.get("timestamp") or first.get("timestamp")),
                    updated_at=int(stat.st_mtime),
                    model=str(payload.get("model") or ""),
                    native_resume=True,
                    locator=str(path),
                )
            )
        return records

    def _scan_opencode(self) -> list[ExternalSessionRecord]:
        if not self.opencode_db.is_file():
            return []
        try:
            with _read_only_db(self.opencode_db) as connection:
                rows = connection.execute(
                    "SELECT s.id, s.title, s.directory, s.time_created, s.time_updated, "
                    "s.model, s.agent, COUNT(m.id) AS message_count "
                    "FROM session s LEFT JOIN message m ON m.session_id = s.id "
                    "GROUP BY s.id ORDER BY s.time_updated DESC"
                ).fetchall()
        except (sqlite3.Error, OSError):
            return []
        records: list[ExternalSessionRecord] = []
        for row in rows:
            model = _json_object(row["model"])
            model_name = "/".join(
                item for item in (str(model.get("providerID") or ""), str(model.get("id") or "")) if item
            )
            session_id = str(row["id"] or "")
            if not session_id:
                continue
            records.append(
                ExternalSessionRecord(
                    source="opencode",
                    session_id=session_id,
                    title=_first_nonempty(row["title"], f"OpenCode {session_id[:8]}"),
                    cwd=str(row["directory"] or ""),
                    created_at=_epoch_seconds(row["time_created"]),
                    updated_at=_epoch_seconds(row["time_updated"]),
                    model=model_name,
                    agent=str(row["agent"] or ""),
                    message_count=max(0, int(row["message_count"] or 0)),
                    native_resume=True,
                )
            )
        return records

    def _messages(self, record: ExternalSessionRecord) -> list[dict[str, Any]]:
        if record.source == "claude":
            return self._claude_messages(record)
        if record.source == "codex":
            return self._codex_messages(record)
        if record.source == "opencode":
            return self._opencode_messages(record)
        return []

    @staticmethod
    def _message(role: str, text: Any, ts: Any = 0, kind: str = "text") -> dict[str, Any] | None:
        content = _text(text)
        if not content:
            return None
        return {"role": role, "text": content, "ts": _epoch_seconds(ts), "kind": kind}

    def _claude_user_text(self, item: dict[str, Any]) -> str:
        message = item.get("message") if isinstance(item.get("message"), dict) else {}
        content = message.get("content")
        if isinstance(content, str):
            return _text(content)
        if not isinstance(content, list):
            return ""
        return "\n\n".join(
            _text(part.get("text"))
            for part in content
            if isinstance(part, dict) and part.get("type") == "text" and _text(part.get("text"))
        )

    def _claude_messages(self, record: ExternalSessionRecord) -> list[dict[str, Any]]:
        path = Path(record.locator)
        if not self._allowed_claude_path(path):
            return []
        result: list[dict[str, Any]] = []
        for item in _read_json_lines(path):
            if item.get("isSidechain"):
                continue
            entry_type = item.get("type")
            message = item.get("message") if isinstance(item.get("message"), dict) else {}
            content = message.get("content")
            timestamp = item.get("timestamp")
            if entry_type == "user" and isinstance(content, str):
                rendered = self._message("user", content, timestamp)
                if rendered:
                    result.append(rendered)
                continue
            if not isinstance(content, list):
                continue
            for part in content:
                if not isinstance(part, dict):
                    continue
                part_type = str(part.get("type") or "")
                rendered: dict[str, Any] | None = None
                if part_type == "text":
                    rendered = self._message(
                        "assistant" if entry_type == "assistant" else "user",
                        part.get("text"),
                        timestamp,
                    )
                elif part_type == "thinking":
                    rendered = self._message("reasoning", part.get("thinking"), timestamp, "reasoning")
                elif part_type == "tool_use":
                    rendered = self._message(
                        "tool",
                        f"{part.get('name') or 'tool'}\n{_text(part.get('input'))}",
                        timestamp,
                        "tool_call",
                    )
                elif part_type == "tool_result":
                    rendered = self._message("tool", part.get("content"), timestamp, "tool_result")
                if rendered:
                    if part_type in {"tool_use", "tool_result"}:
                        rendered.update({
                            "tool_use_id": str(part.get("id") or part.get("tool_use_id") or ""),
                            "tool_name": str(part.get("name") or ""),
                            "tool_input": redact_value(part.get("input") or {}),
                            "tool_output": _text(part.get("content")) if part_type == "tool_result" else "",
                            "tool_status": "error" if part.get("is_error") else "done",
                        })
                    result.append(rendered)
        return result

    def _codex_messages(self, record: ExternalSessionRecord) -> list[dict[str, Any]]:
        history_db = self.codex_root / "thread_history_1.sqlite"
        if history_db.is_file():
            try:
                with _read_only_db(history_db) as connection:
                    rows = connection.execute(
                        "SELECT item_json, created_at_ms FROM thread_items "
                        "WHERE thread_id = ? ORDER BY rollout_ordinal",
                        (record.session_id,),
                    ).fetchall()
                if rows:
                    return self._codex_projected_messages(rows)
            except (sqlite3.Error, OSError):
                pass
        return self._codex_rollout_messages(record)

    def _codex_projected_messages(self, rows: Iterable[sqlite3.Row]) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for row in rows:
            item = _json_object(row["item_json"])
            item_type = str(item.get("type") or "")
            timestamp = row["created_at_ms"]
            rendered: dict[str, Any] | None = None
            if item_type == "userMessage":
                content = item.get("content") if isinstance(item.get("content"), list) else []
                rendered = self._message(
                    "user",
                    "\n\n".join(
                        _text(part.get("text"))
                        for part in content
                        if isinstance(part, dict) and part.get("type") == "text"
                    ),
                    timestamp,
                )
            elif item_type == "agentMessage":
                rendered = self._message("assistant", item.get("text"), timestamp)
            elif item_type == "reasoning":
                rendered = self._message(
                    "reasoning", item.get("summary") or item.get("content"), timestamp, "reasoning"
                )
            elif item_type == "commandExecution":
                output = _first_nonempty(item.get("aggregatedOutput"), item.get("status"))
                rendered = self._message(
                    "tool", f"$ {item.get('command') or ''}\n{output}", timestamp, "tool_result"
                )
            elif item_type == "mcpToolCall":
                label = "/".join(
                    part for part in (str(item.get("server") or ""), str(item.get("tool") or "")) if part
                )
                body = item.get("result") or item.get("error") or item.get("status")
                rendered = self._message("tool", f"{label}\n{_text(body)}", timestamp, "tool_result")
            elif item_type == "webSearch":
                rendered = self._message("tool", item.get("query"), timestamp, "web_search")
            if rendered:
                if rendered["role"] == "tool":
                    rendered.update({
                        "tool_use_id": str(item.get("id") or ""),
                        "tool_name": "Bash" if item_type == "commandExecution" else str(item.get("tool") or item_type),
                        "tool_input": redact_value(item.get("arguments") or ({"command": item.get("command")} if item_type == "commandExecution" else {"query": item.get("query")} if item_type == "webSearch" else {})),
                        "tool_output": _text(item.get("aggregatedOutput") or item.get("result") or item.get("error") or item.get("status")),
                        "tool_status": "error" if item.get("error") or item.get("exitCode", 0) not in (0, None) else "done",
                    })
                result.append(rendered)
        return result

    def _allowed_codex_rollout(self, path: Path) -> bool:
        try:
            path.resolve().relative_to((self.codex_root / "sessions").resolve())
            return path.is_file() and path.suffix in {".jsonl", ".json"}
        except (OSError, ValueError):
            return False

    def _codex_rollout_messages(self, record: ExternalSessionRecord) -> list[dict[str, Any]]:
        path = Path(record.locator)
        if not self._allowed_codex_rollout(path):
            return []
        result: list[dict[str, Any]] = []
        for item in _read_json_lines(path):
            if item.get("type") != "response_item":
                continue
            payload = item.get("payload") if isinstance(item.get("payload"), dict) else {}
            payload_type = payload.get("type")
            timestamp = item.get("timestamp")
            if payload_type == "message" and payload.get("role") in {"user", "assistant"}:
                content = payload.get("content") if isinstance(payload.get("content"), list) else []
                text = "\n\n".join(
                    _text(part.get("text"))
                    for part in content
                    if isinstance(part, dict) and part.get("type") in {"input_text", "output_text"}
                )
                rendered = self._message(str(payload.get("role")), text, timestamp)
            elif payload_type in {"function_call", "custom_tool_call"}:
                rendered = self._message(
                    "tool",
                    f"{payload.get('name') or payload.get('tool') or 'tool'}\n"
                    f"{_text(payload.get('arguments') or payload.get('input'))}",
                    timestamp,
                    "tool_call",
                )
            elif payload_type in {"function_call_output", "custom_tool_call_output"}:
                rendered = self._message(
                    "tool", payload.get("output") or payload.get("result"), timestamp, "tool_result"
                )
            else:
                rendered = None
            if rendered:
                if rendered["role"] == "tool":
                    arguments = payload.get("arguments") or payload.get("input") or {}
                    if isinstance(arguments, str):
                        try:
                            arguments = json.loads(arguments)
                        except ValueError:
                            arguments = {"raw": arguments}
                    rendered.update({
                        "tool_use_id": str(payload.get("call_id") or payload.get("id") or ""),
                        "tool_name": str(payload.get("name") or payload.get("tool") or ""),
                        "tool_input": redact_value(arguments),
                        "tool_output": _text(payload.get("output") or payload.get("result")) if rendered["kind"] == "tool_result" else "",
                        "tool_status": "done",
                    })
                result.append(rendered)
        return result

    def _opencode_messages(self, record: ExternalSessionRecord) -> list[dict[str, Any]]:
        if not self.opencode_db.is_file():
            return []
        try:
            with _read_only_db(self.opencode_db) as connection:
                rows = connection.execute(
                    "SELECT m.data AS message_data, p.data AS part_data, "
                    "COALESCE(p.time_created, m.time_created, 0) AS created_at "
                    "FROM message m JOIN part p ON p.message_id = m.id "
                    "WHERE m.session_id = ? ORDER BY m.time_created, p.time_created, p.rowid",
                    (record.session_id,),
                ).fetchall()
        except (sqlite3.Error, OSError):
            return []
        result: list[dict[str, Any]] = []
        for row in rows:
            message = _json_object(row["message_data"])
            part = _json_object(row["part_data"])
            role = str(message.get("role") or "")
            part_type = str(part.get("type") or "")
            timestamp = row["created_at"]
            rendered: dict[str, Any] | None = None
            if part_type == "text" and role in {"user", "assistant"}:
                rendered = self._message(role, part.get("text"), timestamp)
            elif part_type == "reasoning":
                rendered = self._message("reasoning", part.get("text"), timestamp, "reasoning")
            elif part_type == "tool":
                state = part.get("state") if isinstance(part.get("state"), dict) else {}
                body = state.get("output") or state.get("input") or state.get("status")
                rendered = self._message(
                    "tool", f"{part.get('tool') or 'tool'}\n{_text(body)}", timestamp, "tool_result"
                )
            elif part_type == "file":
                rendered = self._message(
                    role if role in {"user", "assistant"} else "tool",
                    part.get("filename") or part.get("url"),
                    timestamp,
                    "attachment",
                )
            if rendered:
                if part_type == "tool":
                    rendered.update({
                        "tool_use_id": str(part.get("callID") or part.get("id") or ""),
                        "tool_name": str(part.get("tool") or "tool"),
                        "tool_input": redact_value(state.get("input") or {}),
                        "tool_output": _text(state.get("output") or state.get("error")),
                        "tool_status": "error" if state.get("status") == "error" else "done",
                    })
                result.append(rendered)
        return result
