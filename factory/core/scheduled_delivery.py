"""Explicit, durable results for desktop scheduled runs."""

import json
import os
import uuid
from pathlib import Path

from .cli_tasks import get_task
from .runtime import get_data_root
from .mobile_bridge.files import MAX_DOWNLOAD_BYTES, collect_files, open_file_safely


def result_path(task_id):
    if str(uuid.UUID(task_id)) != task_id:
        raise ValueError("Некорректная задача расписания")
    return Path(get_data_root()) / "schedule-results" / (task_id + ".json")


def report_result(task_id, fields):
    task = get_task(task_id)
    metadata = (task or {}).get("metadata") or {}
    if not metadata.get("schedule_run", {}).get("source_session"):
        raise ValueError("Этот инструмент доступен только внутри фонового расписания")
    if not isinstance(fields, dict) or set(fields) - {"disposition", "text", "files"}:
        raise ValueError("Некорректные поля результата")
    disposition = fields.get("disposition")
    text, files = fields.get("text", ""), fields.get("files", [])
    if disposition not in {"deliver", "skip"} or not isinstance(text, str) or len(text) > 32000:
        raise ValueError("Выбери deliver или skip и текст до 32000 символов")
    if not isinstance(files, list) or len(files) > 10 or any(not isinstance(item, str) for item in files):
        raise ValueError("Можно приложить до 10 файлов")
    if disposition == "skip" and (text.strip() or files):
        raise ValueError("skip не содержит текста или файлов")
    if disposition == "deliver" and not (text.strip() or files):
        raise ValueError("Для deliver нужен текст или файл")
    base = Path(metadata["cwd"]).resolve()
    checked = []
    for raw in dict.fromkeys(files):
        path = Path(raw)
        if not path.is_absolute() or not path.is_relative_to(base):
            raise ValueError("Файлы должны находиться внутри рабочей папки расписания")
        if not path.resolve().is_relative_to(base):
            raise ValueError("Файл находится вне рабочей папки")
        if path.is_file() and path.stat().st_size > MAX_DOWNLOAD_BYTES:
            raise ValueError("Файл превышает лимит 500 МиБ: " + path.name)
        found = collect_files(base, "", [raw], task_id, visible_base=True)
        if len(found) != 1:
            raise ValueError("Файл недоступен: " + path.name)
        info, candidate = found[0]
        if not candidate.is_relative_to(base):
            raise ValueError("Файл находится вне рабочей папки")
        with open_file_safely(candidate, info):
            pass
        checked.append({"path": str(candidate), "info": info})
    result = {"disposition": disposition, "text": text.strip(), "files": checked}
    target = result_path(task_id)
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = target.with_suffix("." + uuid.uuid4().hex + ".tmp")
    temporary.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    os.replace(temporary, target)
    return {"status": "recorded", "disposition": disposition, "files": len(checked)}


def deliver_result(service, task, source_session, *, quiet=False):
    from .cli_chat import _session_path, _session_write_lock, _append_entry_unlocked
    from .session_history import read_history

    task_id = task["task_id"]
    path = result_path(task_id)
    if task["status"] != "ok":
        return "failed"
    if path.exists():
        result = json.loads(path.read_text(encoding="utf-8"))
    elif quiet:
        return "missing_result"
    else:
        result = {"disposition": "deliver", "text": str(task.get("output_text") or ""), "files": []}
    if result["disposition"] == "skip":
        return "skipped"
    text = result["text"]
    files = result["files"]
    if not text and not files:
        return "skipped"
    service.get_session(source_session)
    journal = _session_path(source_session)
    message_id = "schedule-" + task_id
    with service._session_lock:
        with _session_write_lock(journal):
            existing = next((entry for entry in read_history(journal) if entry.get("message_id") == message_id), None)
            if existing is None:
                for item in files:
                    with open_file_safely(Path(item["path"]), item["info"]):
                        pass
                _append_entry_unlocked(journal, role="assistant", text=text, task_id=task_id, status="ok", extra={
                    "message_id": message_id, "files": [item["info"] for item in files],
                    "output_files": [item["path"] for item in files],
                    "workspace_task_id": task_id,
                })
            else:
                text = existing["text"]
                files = [{"info": info} for info in existing.get("files", [])]
        payload = {"message_id": message_id, "text": text, "files": [item["info"] for item in files]}
        # A reserved negative source line gives restart-safe event idempotency.
        seq = service.store.append_event(kind="schedule.delivered", payload=payload,
            session_id=source_session, task_id=task_id, src_line=-2)
        if seq is not None:
            service._publish_event(kind="schedule.delivered", payload=payload,
                session_id=source_session, task_id=task_id)
    return "delivered"
