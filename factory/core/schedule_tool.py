"""
Schedule tools for cc-backend.
Allows the model to create, list, and remove deferred tasks (scheduled executions).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

try:
    from .scheduler import get_scheduler
    from .tool_registry import ToolEntry, registry, tool_result, tool_error
except ImportError:
    from scheduler import get_scheduler
    from tool_registry import ToolEntry, registry, tool_result, tool_error

_DEFAULT_TZ = "Europe/Moscow"
_DEFAULT_PROFILE = "cc-local"

# ── Tool schemas ──────────────────────────────────────────────────

CREATE_SCHEDULE_SCHEMA = {
    "name": "create_schedule",
    "description": (
        "Создать разовую или повторяющуюся задачу в существующем планировщике. "
        "Используй когда пользователь просит что-то сделать позже, в определённое время, "
        "или поставить напоминание. Время указывается в формате HH:MM по московскому времени."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "Краткое название задачи (одно-два слова)",
            },
            "schedule": {
                "type": "string",
                "description": "HH:MM по Москве. Для hourly используются минуты: 00:15 означает каждый час в :15.",
            },
            "payload": {
                "type": "string",
                "description": "Текст задачи — что именно нужно сделать",
            },
            "profile": {
                "type": "string",
                "description": "Профиль для выполнения (по умолчанию cc-local)",
            },
            "repeat": {"type": "string", "enum": ["once", "daily", "hourly"], "description": "Повторение; по умолчанию once."},
            "quiet": {"type": "boolean", "description": "При true доставлять только явно отобранный результат через schedule_result; пропуск без сообщения."},
        },
        "required": ["name", "schedule", "payload"],
    },
}

LIST_SCHEDULES_SCHEMA = {
    "name": "list_schedules",
    "description": (
        "Показать список всех отложенных задач пользователя. "
        "Используй когда пользователь спрашивает про свои отложенные задачи или напоминания."
    ),
    "input_schema": {
        "type": "object",
        "properties": {},
    },
}

REMOVE_SCHEDULE_SCHEMA = {
    "name": "remove_schedule",
    "description": (
        "Удалить отложенную задачу по ID. "
        "Используй когда пользователь просит отменить задачу или напоминание."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "schedule_id": {
                "type": "string",
                "description": "ID задачи для удаления",
            },
        },
        "required": ["schedule_id"],
    },
}


# ── Handlers ──────────────────────────────────────────────────────

def _handle_create_schedule(args: dict, **kwargs) -> str:
    name = str(args.get("name") or "").strip()
    schedule = str(args.get("schedule") or "").strip()
    payload = str(args.get("payload") or "").strip()
    profile = str(args.get("profile") or kwargs.get('profile') or _DEFAULT_PROFILE).strip()
    repeat = args.get("repeat", "once")
    quiet = args.get("quiet", False)
    if repeat not in {"once", "daily", "hourly"} or not isinstance(quiet, bool):
        return tool_error("repeat: once/daily/hourly; quiet: boolean")

    if not name or not schedule or not payload:
        return tool_error("name, schedule и payload обязательны")

    # Validate HH:MM format
    try:
        parts = schedule.split(":")
        if len(parts) != 2:
            raise ValueError
        hour, minute = int(parts[0]), int(parts[1])
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            raise ValueError
    except (ValueError, IndexError):
        return tool_error(f"Неверный формат времени '{schedule}'. Используй HH:MM, например '14:30'")

    # Build metadata with delivery target from source context
    metadata: dict[str, Any] = {
        "created_via": "schedule_tool",
    }
    workdir = str(kwargs.get('workdir') or '')
    if workdir and Path(workdir).is_absolute():
        metadata['cwd'] = workdir
    session = str(kwargs.get("current_session") or "").strip()
    if session:
        metadata.update(deliver_session=session, quiet=quiet)
    elif quiet:
        return tool_error("quiet требует исходного чата Игорька")
    source_platform = str(kwargs.get("source_platform") or "").strip()
    source_chat_id = str(kwargs.get("source_chat_id") or "").strip()
    if source_platform and source_chat_id:
        metadata["deliver"] = {"platform": source_platform, "chat_id": source_chat_id}

    try:
        scheduler = get_scheduler()
        schedule_id = scheduler.create_schedule(
            name=name,
            agent_id=profile,
            payload=payload,
            schedule_type=repeat,
            time_value=schedule,
            tz=_DEFAULT_TZ,
            enabled=True,
            metadata=metadata,
        )
        result = {
            "schedule_id": schedule_id,
            "name": name,
            "schedule": schedule,
            "profile": profile,
            "status": "created",
            "repeat": repeat,
            "delivery_session": session or None,
            "quiet": quiet,
            "message": f"Задача '{name}' создана: {repeat}, {schedule} (московское время).",
        }
        return tool_result(result)
    except Exception as exc:
        return tool_error(f"Ошибка создания задачи: {exc}")


def _handle_list_schedules(args: dict, **kwargs) -> str:
    try:
        scheduler = get_scheduler()
        schedules = scheduler.list_schedules(enabled_only=False)
        if not schedules:
            return tool_result({"count": 0, "message": "Нет отложенных задач."})
        items = []
        for s in schedules:
            d = s.to_dict()
            items.append({
                "schedule_id": d.get("schedule_id"),
                "name": d.get("name"),
                "schedule_type": d.get("schedule_type"),
                "time_value": d.get("time_value"),
                "enabled": d.get("enabled"),
                "next_run_at": d.get("next_run_at"),
                "last_run_at": d.get("last_run_at"),
                "run_count": d.get("run_count"),
                "last_output": (str(d.get("last_output") or "")[:200]) if d.get("last_output") else None,
            })
        return tool_result({"count": len(items), "schedules": items})
    except Exception as exc:
        return tool_error(f"Ошибка получения списка: {exc}")


def _handle_remove_schedule(args: dict, **kwargs) -> str:
    schedule_id = str(args.get("schedule_id") or "").strip()
    if not schedule_id:
        return tool_error("schedule_id обязателен")
    try:
        scheduler = get_scheduler()
        success = scheduler.delete_schedule(schedule_id)
        if success:
            return tool_result({"schedule_id": schedule_id, "status": "removed"})
        else:
            return tool_error(f"Задача {schedule_id} не найдена")
    except Exception as exc:
        return tool_error(f"Ошибка удаления: {exc}")


# ── Registration ──────────────────────────────────────────────────

def _check_schedule_available() -> bool:
    """Schedule tools are always available (uses SQLite)."""
    return True


registry.register(
    name="create_schedule",
    toolset="schedule",
    schema=CREATE_SCHEDULE_SCHEMA,
    handler=_handle_create_schedule,
    check_fn=_check_schedule_available,
    description="Create a deferred task that runs at specified time",
)

registry.register(
    name="list_schedules",
    toolset="schedule",
    schema=LIST_SCHEDULES_SCHEMA,
    handler=_handle_list_schedules,
    check_fn=_check_schedule_available,
    description="List all deferred tasks",
)

registry.register(
    name="remove_schedule",
    toolset="schedule",
    schema=REMOVE_SCHEDULE_SCHEMA,
    handler=_handle_remove_schedule,
    check_fn=_check_schedule_available,
    description="Remove a deferred task by ID",
)


def _handle_schedule_result(args: dict, **kwargs) -> str:
    from .scheduled_delivery import report_result
    try:
        return tool_result(report_result(str(kwargs.get("task_id") or ""), args))
    except (ValueError, OSError) as error:
        return tool_error(str(error))


registry.register(
    name="schedule_result", toolset="schedule",
    schema={"name": "schedule_result", "description": "Явно выбрать результат текущего фонового расписания. deliver: готовый текст и абсолютные пути скачиваемых файлов; skip: ничего не отправлять в исходный чат. Доставка только после успешного завершения задачи.",
            "input_schema": {"type": "object", "properties": {
                "disposition": {"type": "string", "enum": ["deliver", "skip"]},
                "text": {"type": "string", "maxLength": 32000},
                "files": {"type": "array", "maxItems": 10, "items": {"type": "string"}},
            }, "required": ["disposition"], "additionalProperties": False}},
    handler=_handle_schedule_result, check_fn=_check_schedule_available,
    description="Select delivery or silent skip for a scheduled run",
)
