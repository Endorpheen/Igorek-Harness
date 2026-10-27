from __future__ import annotations

import json
import os
import re
import sys
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

from .platform_support import exclusive_lock
from .runtime import DEFAULT_CLI_HOME, get_chat_sessions_dir


@contextmanager
def _session_write_lock(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor = exclusive_lock(path.with_name(f".{path.name}.lock"), blocking=True)
    try:
        yield
    finally:
        os.close(descriptor)


def _sanitize_session_name(session: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9._-]+", "_", session.strip())
    return cleaned or "default"


def _session_path(session: str) -> Path:
    sessions_dir = Path(get_chat_sessions_dir())
    sessions_dir.mkdir(parents=True, exist_ok=True)
    return sessions_dir / f"{_sanitize_session_name(session)}.jsonl"


def generate_session_name(prefix: str = "chat") -> str:
    ts = time.strftime("%Y%m%d-%H%M%S")
    return _sanitize_session_name(f"{prefix}-{ts}-{uuid.uuid4().hex[:6]}")


def format_resume_command(
    session: str,
    profile: str = "zai",
    data_dir: str | None = None,
) -> str:
    base_command = "igorek-factory chat"
    parts = [base_command]
    if data_dir:
        normalized = str(Path(data_dir).expanduser())
        if normalized != str(DEFAULT_CLI_HOME):
            parts.extend(["--data-dir", normalized])
    parts.extend(["--resume", session])
    if profile != "zai":
        parts.extend(["--profile", profile])
    return " ".join(parts)


def _print_resume_hint(
    session: str, profile: str, data_dir: str | None
) -> None:
    command = format_resume_command(
        session, profile=profile, data_dir=data_dir
    )
    if sys.stdout.isatty():
        print(f"Resume: \033[31m{command}\033[0m")
    else:
        print(f"Resume: {command}")


def _load_entries(path: Path) -> list[dict]:
    if not path.exists():
        return []

    entries: list[dict] = []
    for raw in path.read_bytes().splitlines():
        if not raw.strip():
            continue
        try:
            entry = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            continue
        if isinstance(entry, dict) and entry.get("role") in {"user", "assistant"}:
            entries.append(entry)
    return entries


def _read_session_meta(path: Path) -> dict | None:
    """Read metadata header from a session JSONL file.

    Returns the meta dict if the first non-empty line is a type=meta entry,
    otherwise None. Old session files without metadata return None.
    """
    if not path.exists():
        return None
    try:
        with path.open("rb") as fh:
            for raw in fh:
                raw = raw.strip()
                if not raw:
                    continue
                entry = json.loads(raw.decode("utf-8"))
                if isinstance(entry, dict) and entry.get("type") == "meta":
                    return entry
                return None
    except (UnicodeDecodeError, json.JSONDecodeError, OSError):
        return None


def _write_session_meta(
    path: Path,
    *,
    session_id: str,
    profile: str = "",
    model: str = "",
    provider: str = "",
    cwd: str = "",
    git_branch: str = "",
) -> None:
    """Write a metadata header as the first line of a new session file.

    Only writes if the file is empty or does not exist. If the file already
    has content, the metadata is not inserted (backward compatibility).
    """
    meta = {
        "type": "meta",
        "session_id": session_id,
        "created_at": int(time.time()),
        "profile": profile,
        "model": model,
        "provider": provider,
        "cwd": cwd,
        "git_branch": git_branch,
    }
    with _session_write_lock(path):
        if path.exists() and path.stat().st_size > 0:
            return
        with path.open("w", encoding="utf-8") as fh:
            fh.write(json.dumps(meta, ensure_ascii=False) + "\n")


def update_session_meta(path: Path, updates: dict) -> dict:
    """Merge ``updates`` into the session meta header, preserving entries.

    The meta header is the first line of the session JSONL. Old sessions created
    before meta existed get a fresh header inserted at the top. Writes go through
    a temp file + atomic replace so a partially written file never occurs.
    """
    with _session_write_lock(path):
        lines: list[str] = []
        if path.exists():
            lines = path.read_text(encoding="utf-8").splitlines()

        meta: dict | None = None
        rest_start = 0
        if lines:
            try:
                first = json.loads(lines[0])
            except json.JSONDecodeError:
                first = None
            if isinstance(first, dict) and first.get("type") == "meta":
                meta = first
                rest_start = 1
        if meta is None:
            meta = {
                "type": "meta",
                "session_id": path.stem,
                "created_at": int(time.time()),
                "profile": "",
                "model": "",
                "provider": "",
                "cwd": "",
                "git_branch": "",
            }

        for key, value in updates.items():
            if value is None:
                meta.pop(key, None)
            else:
                meta[key] = value

        content = json.dumps(meta, ensure_ascii=False) + "\n"
        rest = [line for line in lines[rest_start:]]
        if rest:
            content += "\n".join(rest) + "\n"
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(content, encoding="utf-8")
        os.replace(temporary, path)
        return meta


def _first_user_message(path: Path) -> str:
    """Return the text of the first user entry for preview, or empty string."""
    if not path.exists():
        return ""
    try:
        with path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(entry, dict) and entry.get("role") == "user":
                    return str(entry.get("text", ""))[:200]
    except OSError:
        pass
    return ""


def _session_entry_count(path: Path) -> int:
    """Count user+assistant entries without loading full text."""
    if not path.exists():
        return 0
    count = 0
    try:
        with path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(entry, dict) and entry.get("role") in {"user", "assistant"}:
                    count += 1
    except OSError:
        pass
    return count


def _last_entry_ts(path: Path) -> int:
    """Return the timestamp of the last entry, or 0."""
    if not path.exists():
        return 0
    ts = 0
    try:
        with path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(entry, dict) and "ts" in entry:
                    ts = entry["ts"]
    except OSError:
        pass
    return ts


def list_sessions() -> list[dict]:
    """Scan chat_sessions/ directory and return session metadata for picker.

    Each dict contains: session_id, path, created_at, updated_at, profile,
    model, provider, cwd, git_branch, first_user_message, message_count.
    Returns list sorted by updated_at descending (most recent first).
    """
    sessions_dir = Path(get_chat_sessions_dir())
    if not sessions_dir.exists():
        return []

    results: list[dict] = []
    for jsonl_path in sessions_dir.glob("*.jsonl"):
        session_id = jsonl_path.stem
        meta = _read_session_meta(jsonl_path) or {}
        first_user_message = _first_user_message(jsonl_path)
        results.append({
            "session_id": session_id,
            "path": str(jsonl_path),
            "created_at": meta.get("created_at", 0),
            "updated_at": _last_entry_ts(jsonl_path) or meta.get("created_at", 0),
            "profile": meta.get("profile", ""),
            "model": meta.get("model", ""),
            "provider": meta.get("provider", ""),
            "cwd": meta.get("cwd", ""),
            "git_branch": meta.get("git_branch", ""),
            "title": str(meta.get("title") or "") or first_user_message,
            "archived": bool(meta.get("archived", False)),
            "pinned": meta.get("pinned") is True,
            "first_user_message": first_user_message,
            "message_count": _session_entry_count(jsonl_path),
        })

    results.sort(key=lambda s: s["updated_at"], reverse=True)
    return results


def _append_entry(
    path: Path,
    role: str,
    text: str,
    task_id: str | None = None,
    status: str | None = None,
    extra: dict | None = None,
) -> None:
    with _session_write_lock(path):
        _append_entry_unlocked(path, role, text, task_id, status, extra)


def _append_entry_unlocked(
    path: Path,
    role: str,
    text: str,
    task_id: str | None = None,
    status: str | None = None,
    extra: dict | None = None,
) -> None:
    payload = {
        "role": role,
        "text": text,
        "ts": int(time.time()),
    }
    if task_id:
        payload["task_id"] = task_id
    if status:
        payload["status"] = status
    if extra:
        payload.update(extra)

    with path.open("a+", encoding="utf-8") as fh:
        fh.buffer.seek(0, os.SEEK_END)
        if fh.buffer.tell():
            fh.buffer.seek(-1, os.SEEK_END)
            if fh.buffer.read(1) != b"\n":
                fh.write("\n")
        fh.write(json.dumps(payload, ensure_ascii=False) + "\n")


def _clear_session(path: Path) -> None:
    with _session_write_lock(path):
        path.write_text("", encoding="utf-8")


def fork_session(source_path: Path) -> str | None:
    """Fork a session: copy its JSONL to a new file with a generated name.

    Returns the new session name, or None if source does not exist or is empty.
    """
    if not source_path.exists() or source_path.stat().st_size == 0:
        return None

    new_name = generate_session_name(prefix="fork")
    new_path = _session_path(new_name)

    content = source_path.read_text(encoding="utf-8")
    new_path.parent.mkdir(parents=True, exist_ok=True)
    new_path.write_text(content, encoding="utf-8")

    # Update metadata header in forked file with new session_id
    meta = _read_session_meta(new_path)
    if meta:
        lines = new_path.read_text(encoding="utf-8").splitlines(keepends=True)
        meta["session_id"] = new_name
        meta["pinned"] = False
        meta["created_at"] = int(time.time())
        lines[0] = json.dumps(meta, ensure_ascii=False) + "\n"
        new_path.write_text("".join(lines), encoding="utf-8")

    return new_name


def archive_session_thread(
    path: Path, *, archive_name: str | None = None
) -> Path | None:
    if not path.exists():
        return None

    with _session_write_lock(path):
        content = path.read_text(encoding="utf-8")
        if not content.strip():
            path.write_text("", encoding="utf-8")
            return None

        stem = path.stem
        ts = time.strftime("%Y%m%d-%H%M%S")
        suffix = uuid.uuid4().hex[:6]
        candidate_name = archive_name or f"{stem}--thread-{ts}-{suffix}"
        archive_path = path.with_name(f"{_sanitize_session_name(candidate_name)}.jsonl")
        while archive_path.exists():
            suffix = uuid.uuid4().hex[:6]
            archive_path = path.with_name(
                f"{_sanitize_session_name(candidate_name)}-{suffix}.jsonl"
            )

        path.replace(archive_path)
        path.write_text("", encoding="utf-8")
        return archive_path


def _history_tail(entries: list[dict], max_messages: int, max_chars: int | None) -> list[dict]:
    selected: list[dict] = []
    total_chars = 0

    for entry in reversed(entries):
        text = str(entry.get("text", ""))
        entry_chars = len(text) + len(str(entry.get("role", ""))) + 8
        if selected and len(selected) >= max_messages:
            break
        if selected and max_chars is not None and total_chars + entry_chars > max_chars:
            break
        selected.append(entry)
        total_chars += entry_chars

    return list(reversed(selected))


def _build_prompt(
    history: list[dict], user_text: str, foundation_context: str = ""
) -> str:
    sections: list[str] = []

    if foundation_context.strip():
        sections.append(foundation_context.strip())

    if history:
        history_lines = []
        for entry in history:
            role = "user" if entry.get("role") == "user" else "assistant"
            history_lines.append(f"{role}: {entry.get('text', '').strip()}")
        sections.append("История текущей сессии:\n" + "\n".join(history_lines))

    sections.append("Последнее сообщение пользователя:\n" + user_text)

    return (
        "Ты продолжаешь локальный CLI-диалог с пользователем. "
        "Используй shared foundation memory и историю ниже как контекст, а затем ответь на последнее сообщение.\n\n"
        + "\n\n".join(sections)
    )
