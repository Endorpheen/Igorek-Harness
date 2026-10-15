from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path

from .runtime import get_data_root, get_db_path
from .schema import open_db
from .skills_manager import SkillsManager, _default_skills_dirs, skills_manager


def default_skills_runtime_dir(data_dir: str | None = None) -> Path:
    root = Path(data_dir).expanduser() if data_dir else Path(get_data_root())
    return root / "skills"


def normalize_skill_name(name: str) -> str:
    value = re.sub(r"[^a-z0-9-]+", "-", name.strip().lower())
    value = re.sub(r"-{2,}", "-", value).strip("-")
    if not value:
        raise ValueError("Skill name must contain latin letters, digits, or hyphen")
    if len(value) > 63:
        raise ValueError("Skill name must be shorter than 64 characters")
    return value


def _skill_template(skill_name: str, description: str, triggers: list[str], backends: list[str]) -> str:
    trigger_lines = "\n".join(f"  - {item}" for item in triggers) if triggers else "  - add trigger here"
    backend_lines = "\n".join(f"  - {item}" for item in backends) if backends else "  - claude"
    desc = description.strip() or "Когда и зачем использовать этот skill."
    return (
        "---\n"
        f"name: {skill_name}\n"
        f"description: {desc}\n"
        "triggers:\n"
        f"{trigger_lines}\n"
        "backends:\n"
        f"{backend_lines}\n"
        "---\n\n"
        f"# {skill_name}\n\n"
        "## Purpose\n\n"
        "Коротко опиши, что должен делать этот skill и в каких случаях его применять.\n\n"
        "## Workflow\n\n"
        "1. Опиши первый шаг.\n"
        "2. Опиши второй шаг.\n"
        "3. Добавь проверки или ограничения.\n\n"
        "## Notes\n\n"
        "- Укажи важные ограничения.\n"
        "- Сошлись на files/scripts/references, если они есть.\n"
    )


def init_skill(
    name: str,
    *,
    data_dir: str | None = None,
    path: str | None = None,
    description: str = "",
    triggers: list[str] | None = None,
    backends: list[str] | None = None,
    with_scripts: bool = False,
    with_references: bool = False,
    with_assets: bool = False,
    force: bool = False,
) -> dict:
    skill_name = normalize_skill_name(name)
    skills_root = Path(path).expanduser() if path else default_skills_runtime_dir(data_dir)
    skill_dir = skills_root / skill_name
    skill_file = skill_dir / "SKILL.md"

    if skill_file.exists() and not force:
        raise FileExistsError(f"Skill already exists: {skill_file}")

    skill_dir.mkdir(parents=True, exist_ok=True)
    if with_scripts:
        (skill_dir / "scripts").mkdir(exist_ok=True)
    if with_references:
        (skill_dir / "references").mkdir(exist_ok=True)
    if with_assets:
        (skill_dir / "assets").mkdir(exist_ok=True)

    skill_file.write_text(
        _skill_template(
            skill_name=skill_name,
            description=description,
            triggers=triggers or [],
            backends=backends or [],
        ),
        encoding="utf-8",
    )

    return {
        "status": "ok",
        "skill_name": skill_name,
        "skills_root": str(skills_root),
        "skill_dir": str(skill_dir),
        "skill_file": str(skill_file),
        "created": {
            "scripts": with_scripts,
            "references": with_references,
            "assets": with_assets,
        },
    }


def list_skills(*, data_dir: str | None = None, path: str | None = None) -> dict:
    skills_root = Path(path).expanduser() if path else default_skills_runtime_dir(data_dir)
    manager = SkillsManager(str(skills_root))
    loaded = manager.load_skills()
    return {
        "status": "ok" if loaded else "empty",
        "skills_root": str(skills_root),
        "loaded": loaded,
        "count": len(manager.all_skills),
        "active_count": manager.get_active_count(),
        "skills": manager.get_metadata(),
    }


def activate_local_skills(*, data_dir: str | None = None) -> dict:
    settings = skills_manager.load_settings()
    auto_load = settings.get("skills_auto_load", "true") == "true"
    smart_selection = settings.get("skills_smart_selection", "true") == "true"
    skills_root = default_skills_runtime_dir(data_dir)
    skills_root.mkdir(parents=True, exist_ok=True)
    skills_manager.save_settings(str(skills_root), auto_load=auto_load, smart_selection=smart_selection)
    skills_manager.load_settings()
    return {
        "status": "ok",
        "skills_root": str(skills_root),
        "message": "Local skills runtime activated",
    }


def _resolve_skills_root(data_dir: str | None = None, path: str | None = None) -> Path:
    if path:
        return Path(path).expanduser()
    if data_dir:
        return default_skills_runtime_dir(data_dir)
    settings = skills_manager.load_settings()
    configured = str(settings.get("skills_path") or "").strip()
    if configured:
        return Path(configured).expanduser()
    return default_skills_runtime_dir(None)


def _resolve_skills_dirs(data_dir: str | None = None, path: str | None = None) -> list[str]:
    if path:
        return [str(Path(path).expanduser())]
    if data_dir:
        return [str(default_skills_runtime_dir(data_dir))]
    settings = skills_manager.load_settings()
    configured_dirs = str(settings.get("skills_dirs") or "").strip()
    if configured_dirs:
        try:
            dirs = json.loads(configured_dirs)
            if isinstance(dirs, list) and dirs:
                return [str(d).strip() for d in dirs if str(d).strip()]
        except (json.JSONDecodeError, TypeError):
            pass
    configured = str(settings.get("skills_path") or "").strip()
    if configured:
        return [configured]
    return _default_skills_dirs()


def _load_manager(data_dir: str | None = None, path: str | None = None) -> tuple[SkillsManager, Path]:
    dirs = _resolve_skills_dirs(data_dir=data_dir, path=path)
    writable = Path(dirs[0]).expanduser() if dirs else _resolve_skills_root(data_dir=data_dir, path=path)
    manager = SkillsManager(skills_dirs=dirs)
    manager.load_skills()
    return manager, writable


def set_skill_enabled(skill_id: str, *, enabled: bool, data_dir: str | None = None, path: str | None = None) -> dict:
    manager, skills_root = _load_manager(data_dir=data_dir, path=path)
    ok, message = manager.set_skill_enabled(skill_id=skill_id, enabled=enabled)
    if not ok:
        raise ValueError(message)
    skill = manager.all_skills.get(skill_id)
    return {
        "status": "ok",
        "skill_id": skill_id,
        "enabled": bool(enabled),
        "message": message,
        "skills_root": str(skills_root),
        "skill": skill.to_metadata_dict() if skill else None,
    }


def skills_usage_summary(*, data_dir: str | None = None, path: str | None = None, limit: int = 20) -> dict:
    manager, skills_root = _load_manager(data_dir=data_dir, path=path)
    db_path = get_db_path()
    limit = max(1, int(limit))

    rows: list[tuple] = []
    with open_db(db_path) as conn:
        cur = conn.cursor()
        cur.execute(
            """
            SELECT skill_id, selected_count, last_selected_at, updated_at
            FROM skills_usage
            ORDER BY selected_count DESC, COALESCE(last_selected_at, 0) DESC
            LIMIT ?
            """,
            (limit,),
        )
        rows = cur.fetchall()

    items: list[dict] = []
    for skill_id, selected_count, last_selected_at, updated_at in rows:
        skill = manager.all_skills.get(skill_id)
        items.append(
            {
                "skill_id": skill_id,
                "name": skill.name if skill else skill_id,
                "enabled": bool(skill.enabled) if skill else None,
                "selected_count": int(selected_count or 0),
                "last_selected_at": int(last_selected_at or 0),
                "updated_at": int(updated_at or 0),
            }
        )

    return {
        "status": "ok",
        "skills_root": str(skills_root),
        "count": len(items),
        "items": items,
    }


def print_json(payload: dict) -> None:
    print(json.dumps(payload, indent=2, ensure_ascii=False))
