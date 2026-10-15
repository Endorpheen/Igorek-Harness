from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
from pathlib import Path
from typing import Any

import yaml

from .skills_guard import format_scan_report, scan_skill, should_allow_install
from .skills_manager import SkillsManager, _default_skills_path, _default_skills_dirs, skills_manager
from .tool_registry import registry


MAX_SKILL_VIEW_CHARS = 120_000
EXCLUDED_SKILL_DIRS = {".git", ".github", ".hub", "__pycache__"}
MAX_SKILL_NAME_LENGTH = 64
MAX_SKILL_DESCRIPTION_LENGTH = 1024
MAX_SKILL_CONTENT_CHARS = 100_000
MAX_SKILL_FILE_BYTES = 1_048_576
VALID_SKILL_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]*$")
ALLOWED_SKILL_FILE_DIRS = {"references", "templates", "scripts", "assets"}
MAIN_SKILL_FILE_ALIASES = {"instructions.md", "skill.md", "skill"}


def _resolve_skills_dirs() -> list[str]:
    """Resolve the full list of skills directories (read-write + read-only)."""
    settings = skills_manager.load_settings()

    # 1. Check DB for skills_dirs (JSON array)
    configured_dirs = str(settings.get("skills_dirs") or "").strip()
    if configured_dirs:
        try:
            dirs = json.loads(configured_dirs)
            if isinstance(dirs, list) and dirs:
                return [str(d).strip() for d in dirs if str(d).strip()]
        except (json.JSONDecodeError, TypeError):
            pass

    # 2. Check DB for legacy skills_path (single string)
    configured = str(settings.get("skills_path") or "").strip()
    if configured:
        return [configured]

    # 3. Fall back to module-level defaults
    return _default_skills_dirs()


def _resolve_writable_root() -> Path:
    """Return the first (writable) skills directory as a Path."""
    dirs = _resolve_skills_dirs()
    root = dirs[0] if dirs else _default_skills_dirs()[0]
    return Path(root).expanduser().resolve()


def _resolve_skills_root() -> Path:
    """Backward-compat alias for _resolve_writable_root()."""
    return _resolve_writable_root()


def _load_manager() -> tuple[SkillsManager, Path]:
    dirs = _resolve_skills_dirs()
    writable = Path(dirs[0]).expanduser().resolve() if dirs else Path(_default_skills_dirs()[0]).expanduser().resolve()
    manager = SkillsManager(skills_dirs=dirs)
    manager.load_skills()
    return manager, writable


def _json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2)


def _parse_frontmatter(raw_content: str) -> tuple[dict[str, Any], str]:
    if not raw_content.startswith("---\n"):
        return {}, raw_content
    marker = "\n---\n"
    end = raw_content.find(marker, 4)
    if end == -1:
        return {}, raw_content
    frontmatter_raw = raw_content[4:end]
    body = raw_content[end + len(marker):]
    try:
        import yaml

        parsed = yaml.safe_load(frontmatter_raw) or {}
    except Exception:
        parsed = {}
    return (parsed if isinstance(parsed, dict) else {}), body


def _category_description(category_dir: Path) -> str:
    desc_file = category_dir / "DESCRIPTION.md"
    if not desc_file.is_file():
        return ""
    try:
        raw = desc_file.read_text(encoding="utf-8")
    except Exception:
        return ""
    frontmatter, body = _parse_frontmatter(raw)
    description = str(frontmatter.get("description") or "").strip()
    if description:
        return description[:1024]
    for line in body.splitlines():
        cleaned = line.strip()
        if cleaned and not cleaned.startswith("#"):
            return cleaned[:1024]
    return ""


def _skill_metadata(skill) -> dict[str, Any]:
    return {
        "skill_id": skill.skill_id,
        "name": skill.name,
        "description": skill.description,
        "category": skill.category or None,
        "source_file": skill.source_file,
        "source_dir": skill.source_dir,
        "enabled": bool(skill.enabled),
        "backend_filter": skill.backend_filter,
        "resource_dirs": skill.resource_dirs(),
        "supporting_files": [
            item for item in skill.bundled_files(limit=64) if not item.startswith("env/")
        ],
    }


def _find_skill(manager: SkillsManager, identifier: str):
    raw = str(identifier or "").strip().strip("/")
    if not raw:
        return None
    normalized = raw.lower()
    for skill in manager.all_skills.values():
        candidates = {
            skill.skill_id.lower(),
            skill.name.lower(),
            skill.source_file.lower(),
            str(Path(skill.source_file).with_suffix("")).lower(),
        }
        if normalized in candidates:
            return skill
    return None


def _safe_skill_file(skill_dir: Path, file_path: str) -> Path:
    raw = str(file_path or "").strip()
    if not raw:
        raise ValueError("file_path is required")
    requested = Path(raw)
    if requested.is_absolute() or ".." in requested.parts:
        raise ValueError("Path traversal is not allowed. Use a relative path inside the skill.")
    target = (skill_dir / requested).resolve()
    skill_root = skill_dir.resolve()
    try:
        target.relative_to(skill_root)
    except ValueError as exc:
        raise ValueError("Path escapes skill directory boundary.") from exc
    if not target.is_file():
        raise FileNotFoundError(f"Skill file not found: {raw}")
    return target


def _read_limited_text(path: Path) -> str:
    content = path.read_text(encoding="utf-8", errors="replace")
    if len(content) > MAX_SKILL_VIEW_CHARS:
        return (
            content[:MAX_SKILL_VIEW_CHARS]
            + f"\n\n[truncated: {len(content) - MAX_SKILL_VIEW_CHARS} chars omitted]"
        )
    return content


def _validate_skill_name(name: str) -> str:
    if not name:
        return "Skill name is required."
    if len(name) > MAX_SKILL_NAME_LENGTH:
        return f"Skill name exceeds {MAX_SKILL_NAME_LENGTH} characters."
    if not VALID_SKILL_NAME_RE.match(name):
        return (
            f"Invalid skill name '{name}'. Use lowercase letters, numbers, "
            "hyphens, dots, and underscores. Must start with a letter or digit."
        )
    return ""


def _validate_skill_category(category: str) -> str:
    if not category:
        return ""
    if "/" in category or "\\" in category:
        return "Category must be a single directory segment."
    return _validate_skill_name(category).replace("Skill name", "Category")


def _validate_skill_content_size(content: str, label: str = "SKILL.md") -> str:
    if len(content) > MAX_SKILL_CONTENT_CHARS:
        return (
            f"{label} content is {len(content):,} characters "
            f"(limit: {MAX_SKILL_CONTENT_CHARS:,}). Split large material into supporting files."
        )
    return ""


def _validate_skill_frontmatter(content: str) -> str:
    if not content.strip():
        return "Content cannot be empty."
    if not content.startswith("---\n"):
        return "SKILL.md must start with YAML frontmatter."
    marker = "\n---\n"
    end = content.find(marker, 4)
    if end == -1:
        return "SKILL.md frontmatter is not closed."
    frontmatter_raw = content[4:end]
    body = content[end + len(marker):].strip()
    try:
        parsed = yaml.safe_load(frontmatter_raw)
    except yaml.YAMLError as exc:
        return f"YAML frontmatter parse error: {exc}"
    if not isinstance(parsed, dict):
        return "Frontmatter must be a YAML mapping."
    if "name" not in parsed:
        return "Frontmatter must include 'name'."
    if "description" not in parsed:
        return "Frontmatter must include 'description'."
    if len(str(parsed["description"])) > MAX_SKILL_DESCRIPTION_LENGTH:
        return f"Description exceeds {MAX_SKILL_DESCRIPTION_LENGTH} characters."
    if not body:
        return "SKILL.md must have content after frontmatter."
    return ""


def _atomic_write_text(file_path: Path, content: str) -> None:
    file_path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_path = tempfile.mkstemp(
        dir=str(file_path.parent),
        prefix=f".{file_path.name}.tmp.",
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
        os.replace(temp_path, file_path)
    except Exception:
        try:
            os.unlink(temp_path)
        except OSError:
            pass
        raise


def _scan_skill_change(scan_path: Path) -> str:
    result = scan_skill(scan_path, source="agent-created")
    allowed, reason = should_allow_install(result, force=False)
    if result.verdict == "dangerous" or allowed is False:
        return f"Security scan blocked this skill ({reason}):\n{format_scan_report(result)}"
    return ""


def _managed_skill_dir(skills_root: Path, name: str, category: str = "") -> Path:
    if category:
        return skills_root / category / name
    return skills_root / name


def _safe_managed_file(skill_dir: Path, file_path: str) -> Path:
    raw = str(file_path or "").strip()
    if not raw:
        raise ValueError("file_path is required")
    requested = Path(raw)
    if requested.is_absolute() or ".." in requested.parts:
        raise ValueError("Path traversal is not allowed.")
    if not requested.parts or requested.parts[0] not in ALLOWED_SKILL_FILE_DIRS:
        allowed = ", ".join(sorted(ALLOWED_SKILL_FILE_DIRS))
        raise ValueError(
            f"File must be under one of: {allowed}. "
            f"Для файлов вне skill (заметки, документы) используй Bash или Write, не skill_manage."
        )
    if len(requested.parts) < 2:
        raise ValueError("Provide a file path, not just a directory.")
    target = (skill_dir / requested).resolve()
    skill_root = skill_dir.resolve()
    try:
        target.relative_to(skill_root)
    except ValueError as exc:
        raise ValueError("Path escapes skill directory boundary.") from exc
    return target


def _replace_content(
    content: str,
    old_string: str,
    new_string: str,
    replace_all: bool,
) -> tuple[str, int, str]:
    if old_string not in content:
        preview = content[:500] + ("..." if len(content) > 500 else "")
        return content, 0, f"old_string not found. File preview:\n{preview}"
    count = content.count(old_string)
    if count > 1 and not replace_all:
        return content, count, "old_string is not unique. Set replace_all=true or include more context."
    return content.replace(old_string, new_string, -1 if replace_all else 1), count, ""


def _skills_categories(args: dict[str, Any], **_: Any) -> str:
    manager, skills_root = _load_manager()
    categories: dict[str, dict[str, Any]] = {}
    uncategorized = 0
    for skill in manager.all_skills.values():
        if not skill.enabled:
            continue
        if skill.category:
            entry = categories.setdefault(
                skill.category,
                {
                    "name": skill.category,
                    "skill_count": 0,
                    "description": "",
                },
            )
            entry["skill_count"] += 1
        else:
            uncategorized += 1
    for name, entry in categories.items():
        entry["description"] = _category_description(skills_root / name)
    result = sorted(categories.values(), key=lambda item: item["name"])
    if uncategorized:
        result.append(
            {
                "name": "uncategorized",
                "skill_count": uncategorized,
                "description": "",
            }
        )
    return _json(
        {
            "success": True,
            "skills_root": str(skills_root),
            "count": len(result),
            "categories": result,
            "hint": "Use skills_list with a category to inspect available skills.",
        }
    )


def _skills_list(args: dict[str, Any], **_: Any) -> str:
    manager, skills_root = _load_manager()
    category = str(args.get("category") or "").strip()
    backend = str(args.get("backend") or "").strip()
    include_disabled = bool(args.get("include_disabled", False))
    known_categories = {
        skill.category
        for skill in manager.all_skills.values()
        if skill.category and (include_disabled or skill.enabled)
    }
    has_uncategorized = any(
        not skill.category and (include_disabled or skill.enabled)
        for skill in manager.all_skills.values()
    )
    category_warning = ""
    if category and category != "uncategorized" and category not in known_categories:
        category_warning = (
            f"Unknown category '{category}'. Returned unfiltered skills instead. "
            "Use skills_categories for valid category names."
        )
        category = ""
    if category == "uncategorized" and not has_uncategorized:
        category_warning = "No uncategorized skills are available."
    skills = []
    for skill in manager.all_skills.values():
        if not include_disabled and not skill.enabled:
            continue
        if category and category != "uncategorized" and skill.category != category:
            continue
        if category == "uncategorized" and skill.category:
            continue
        if backend and not skill.supports_backend(backend):
            continue
        skills.append(_skill_metadata(skill))
    skills.sort(key=lambda item: ((item.get("category") or ""), item["name"].lower()))
    return _json(
        {
            "success": True,
            "skills_root": str(skills_root),
            "count": len(skills),
            "skills": skills,
            "applied_category": category or None,
            "category_warning": category_warning or None,
            "hint": "Use skill_view(name) for full instructions or skill_view(name, file_path) for a supporting file.",
        }
    )


def _skill_view(args: dict[str, Any], **_: Any) -> str:
    manager, skills_root = _load_manager()
    name = str(args.get("name") or "").strip()
    file_path = str(args.get("file_path") or "").strip()
    skill = _find_skill(manager, name)
    if skill is None:
        available = [
            item["name"]
            for item in sorted(
                (_skill_metadata(skill) for skill in manager.all_skills.values()),
                key=lambda item: item["name"].lower(),
            )[:20]
        ]
        return _json(
            {
                "success": False,
                "error": f"Skill not found: {name}",
                "available_skills": available,
            }
        )
    if not skill.package_dir and file_path:
        return _json(
            {
                "success": False,
                "error": "Supporting files are only available for package-style skills.",
                "skill": _skill_metadata(skill),
            }
        )
    try:
        if file_path.lower() in MAIN_SKILL_FILE_ALIASES:
            file_path = ""
        if file_path:
            target = _safe_skill_file(Path(skill.package_dir), file_path)
            content = _read_limited_text(target)
            viewed_path = str(target.relative_to(Path(skill.package_dir).resolve()))
            return _json(
                {
                    "success": True,
                    "skill": _skill_metadata(skill),
                    "file_path": viewed_path,
                    "content": content,
                    "truncated": "[truncated:" in content,
                }
            )
        raw_content = Path(skill.body_path).read_text(encoding="utf-8", errors="replace")
        frontmatter, body = _parse_frontmatter(raw_content)
        content = body.strip() or skill.load_content()
        if len(content) > MAX_SKILL_VIEW_CHARS:
            content = (
                content[:MAX_SKILL_VIEW_CHARS]
                + f"\n\n[truncated: {len(content) - MAX_SKILL_VIEW_CHARS} chars omitted]"
            )
        return _json(
            {
                "success": True,
                "skill": _skill_metadata(skill),
                "frontmatter": frontmatter,
                "content": content,
                "truncated": "[truncated:" in content,
            }
        )
    except Exception as exc:
        return _json(
            {
                "success": False,
                "error": str(exc),
                "skill": _skill_metadata(skill),
            }
        )


def _skill_scan(args: dict[str, Any], **_: Any) -> str:
    manager, _skills_root = _load_manager()
    name = str(args.get("name") or "").strip()
    source = str(args.get("source") or "community").strip() or "community"
    skill = _find_skill(manager, name)
    if skill is None:
        available = [
            item["name"]
            for item in sorted(
                (_skill_metadata(skill) for skill in manager.all_skills.values()),
                key=lambda item: item["name"].lower(),
            )[:20]
        ]
        return _json(
            {
                "success": False,
                "error": f"Skill not found: {name}",
                "available_skills": available,
            }
        )
    scan_path = Path(skill.package_dir) if skill.package_dir else Path(skill.body_path)
    result = scan_skill(scan_path, source=source)
    allowed, reason = should_allow_install(result, force=False)
    return _json(
        {
            "success": True,
            "skill": _skill_metadata(skill),
            "scan_path": str(scan_path),
            "result": result.to_dict(),
            "decision": {
                "allowed": allowed,
                "reason": reason,
            },
            "report": format_scan_report(result),
        }
    )


def _skill_manage(args: dict[str, Any], **_: Any) -> str:
    manager, skills_root = _load_manager()
    action = str(args.get("action") or "").strip()
    name = str(args.get("name") or "").strip()
    category = str(args.get("category") or "").strip()
    content = args.get("content")
    file_path = str(args.get("file_path") or "").strip()
    file_content = args.get("file_content")
    old_string = args.get("old_string")
    new_string = args.get("new_string")
    replace_all = bool(args.get("replace_all", False))

    if action in {"create"}:
        err = _validate_skill_name(name) or _validate_skill_category(category)
        if err:
            return _json({"success": False, "error": err})
    elif action in {"edit", "patch", "delete", "write_file", "remove_file"}:
        if not name:
            return _json({"success": False, "error": "name is required."})
    else:
        return _json(
            {
                "success": False,
                "error": (
                    "Unknown action. Use: create, edit, patch, delete, "
                    "write_file, remove_file."
                ),
            }
        )

    try:
        if action == "create":
            if not isinstance(content, str) or not content.strip():
                return _json({"success": False, "error": "content is required for create."})
            err = _validate_skill_frontmatter(content) or _validate_skill_content_size(content)
            if err:
                return _json({"success": False, "error": err})
            if _find_skill(manager, name):
                return _json({"success": False, "error": f"Skill already exists: {name}"})
            skill_dir = _managed_skill_dir(skills_root, name, category)
            skill_md = skill_dir / "SKILL.md"
            _atomic_write_text(skill_md, content)
            scan_error = _scan_skill_change(skill_dir)
            if scan_error:
                shutil.rmtree(skill_dir, ignore_errors=True)
                return _json({"success": False, "error": scan_error})
            return _json(
                {
                    "success": True,
                    "message": f"Skill '{name}' created.",
                    "path": str(skill_dir),
                    "skill_md": str(skill_md),
                    "category": category or None,
                }
            )

        skill = _find_skill(manager, name)
        if skill is None:
            return _json({"success": False, "error": f"Skill not found: {name}"})

        # Read-only guard: skills from external dirs cannot be mutated
        if skill.source_dir:
            writable = str(skills_root)
            if os.path.abspath(skill.source_dir) != os.path.abspath(writable):
                return _json({
                    "success": False,
                    "error": (
                        f"Skill '{name}' is in a read-only external directory: {skill.source_dir}. "
                        f"Only skills in the writable directory ({writable}) can be modified."
                    ),
                })

        skill_path = Path(skill.package_dir) if skill.package_dir else Path(skill.body_path)
        skill_dir = Path(skill.package_dir) if skill.package_dir else Path(skill.body_path).parent
        skill_md = skill_dir / "SKILL.md" if skill.package_dir else Path(skill.body_path)

        if action == "edit":
            if not isinstance(content, str) or not content.strip():
                return _json({"success": False, "error": "content is required for edit."})
            err = _validate_skill_frontmatter(content) or _validate_skill_content_size(content)
            if err:
                return _json({"success": False, "error": err})
            original = skill_md.read_text(encoding="utf-8") if skill_md.exists() else ""
            _atomic_write_text(skill_md, content)
            scan_error = _scan_skill_change(skill_path)
            if scan_error:
                _atomic_write_text(skill_md, original)
                return _json({"success": False, "error": scan_error})
            return _json({"success": True, "message": f"Skill '{name}' updated.", "path": str(skill_md)})

        if action == "patch":
            if not isinstance(old_string, str) or not old_string:
                return _json({"success": False, "error": "old_string is required for patch."})
            if not isinstance(new_string, str):
                return _json({"success": False, "error": "new_string is required for patch."})
            target = _safe_managed_file(skill_dir, file_path) if file_path else skill_md
            if not target.exists():
                return _json({"success": False, "error": f"File not found: {target}"})
            original = target.read_text(encoding="utf-8")
            updated, match_count, match_error = _replace_content(
                original,
                old_string,
                new_string,
                replace_all,
            )
            if match_error:
                return _json({"success": False, "error": match_error, "match_count": match_count})
            err = _validate_skill_content_size(updated, label=str(target.relative_to(skill_dir)))
            if err:
                return _json({"success": False, "error": err})
            if target == skill_md:
                err = _validate_skill_frontmatter(updated)
                if err:
                    return _json({"success": False, "error": f"Patch would break SKILL.md: {err}"})
            _atomic_write_text(target, updated)
            scan_error = _scan_skill_change(skill_path)
            if scan_error:
                _atomic_write_text(target, original)
                return _json({"success": False, "error": scan_error})
            return _json(
                {
                    "success": True,
                    "message": f"Patched '{target.relative_to(skill_dir)}' in skill '{name}'.",
                    "match_count": match_count,
                }
            )

        if action == "write_file":
            if not skill.package_dir:
                return _json(
                    {
                        "success": False,
                        "error": "Supporting files require package-style skills.",
                    }
                )
            if not isinstance(file_content, str):
                return _json({"success": False, "error": "file_content is required for write_file."})
            content_bytes = len(file_content.encode("utf-8"))
            if content_bytes > MAX_SKILL_FILE_BYTES:
                return _json(
                    {
                        "success": False,
                        "error": f"file_content exceeds {MAX_SKILL_FILE_BYTES} bytes.",
                    }
                )
            err = _validate_skill_content_size(file_content, label=file_path)
            if err:
                return _json({"success": False, "error": err})
            target = _safe_managed_file(skill_dir, file_path)
            original = target.read_text(encoding="utf-8") if target.exists() else None
            _atomic_write_text(target, file_content)
            scan_error = _scan_skill_change(skill_path)
            if scan_error:
                if original is None:
                    target.unlink(missing_ok=True)
                else:
                    _atomic_write_text(target, original)
                return _json({"success": False, "error": scan_error})
            return _json({"success": True, "message": f"File '{file_path}' written.", "path": str(target)})

        if action == "remove_file":
            if not skill.package_dir:
                return _json(
                    {
                        "success": False,
                        "error": "Supporting files require package-style skills.",
                    }
                )
            target = _safe_managed_file(skill_dir, file_path)
            if not target.exists():
                return _json({"success": False, "error": f"File not found: {file_path}"})
            original = target.read_text(encoding="utf-8")
            target.unlink()
            scan_error = _scan_skill_change(skill_path)
            if scan_error:
                _atomic_write_text(target, original)
                return _json({"success": False, "error": scan_error})
            return _json({"success": True, "message": f"File '{file_path}' removed."})

        if action == "delete":
            if skill.package_dir:
                shutil.rmtree(skill_dir)
            else:
                skill_md.unlink()
            return _json({"success": True, "message": f"Skill '{name}' deleted."})

    except Exception as exc:
        return _json({"success": False, "error": f"{type(exc).__name__}: {exc}"})

    return _json({"success": False, "error": f"Unhandled action: {action}"})


SKILLS_CATEGORIES_SCHEMA: dict[str, Any] = {
    "name": "skills_categories",
    "description": "List skill categories with counts and descriptions.",
    "input_schema": {
        "type": "object",
        "properties": {},
        "required": [],
    },
}

SKILLS_LIST_SCHEMA: dict[str, Any] = {
    "name": "skills_list",
    "description": "List installed skills as compact metadata. Use before skill_view.",
    "input_schema": {
        "type": "object",
        "properties": {
            "category": {
                "type": "string",
                "description": "Optional category filter, or uncategorized.",
            },
            "backend": {
                "type": "string",
                "description": "Optional backend filter such as cc-backend, claude, or codex.",
            },
            "include_disabled": {
                "type": "boolean",
                "description": "Include disabled skills in the listing.",
            },
        },
        "required": [],
    },
}

SKILL_VIEW_SCHEMA: dict[str, Any] = {
    "name": "skill_view",
    "description": (
        "View a skill's full instructions or one supporting file. "
        "Use this only after skills_list identifies a relevant skill."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "Skill name, skill_id, or source path from skills_list.",
            },
            "file_path": {
                "type": "string",
                "description": "Optional relative path inside the skill directory.",
            },
        },
        "required": ["name"],
    },
}

SKILL_SCAN_SCHEMA: dict[str, Any] = {
    "name": "skill_scan",
    "description": (
        "Run a Hermes-style static security scan on an installed skill. "
        "Use before trusting, editing, or installing skill content."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "name": {
                "type": "string",
                "description": "Skill name, skill_id, or source path from skills_list.",
            },
            "source": {
                "type": "string",
                "description": "Trust source: official, openai/skills, community, or agent-created.",
            },
        },
        "required": ["name"],
    },
}

SKILL_MANAGE_SCHEMA: dict[str, Any] = {
    "name": "skill_manage",
    "description": (
        "Manage skills as procedural memory. Supports create, edit, patch, delete, "
        "write_file, and remove_file. Writes are atomic and blocked changes are "
        "rolled back after skill_scan. This is a single tool: pass action='create' "
        "or action='patch'; do not call pseudo-methods like skill_manage.create."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["create", "edit", "patch", "delete", "write_file", "remove_file"],
                "description": "Skill management action.",
            },
            "name": {
                "type": "string",
                "description": "Skill id/name. For create use lowercase filesystem-safe id.",
            },
            "content": {
                "type": "string",
                "description": "Full SKILL.md content for create/edit. Must start with YAML frontmatter: --- name: ... description: ... --- followed by markdown instructions.",
            },
            "category": {
                "type": "string",
                "description": "Optional package category directory for create.",
            },
            "file_path": {
                "type": "string",
                "description": "Supporting file path under references/, templates/, scripts/, or assets/.",
            },
            "file_content": {
                "type": "string",
                "description": "Content for write_file.",
            },
            "old_string": {
                "type": "string",
                "description": "Text to replace for patch.",
            },
            "new_string": {
                "type": "string",
                "description": "Replacement text for patch.",
            },
            "replace_all": {
                "type": "boolean",
                "description": "Replace all matches for patch.",
            },
        },
        "required": ["action", "name"],
    },
}


registry.register(
    name="skills_categories",
    toolset="skills",
    schema=SKILLS_CATEGORIES_SCHEMA,
    handler=_skills_categories,
    description="List skill categories.",
)

registry.register(
    name="skills_list",
    toolset="skills",
    schema=SKILLS_LIST_SCHEMA,
    handler=_skills_list,
    description="List installed skills as compact metadata.",
)

registry.register(
    name="skill_view",
    toolset="skills",
    schema=SKILL_VIEW_SCHEMA,
    handler=_skill_view,
    description="View full skill content or supporting files.",
)

registry.register(
    name="skill_scan",
    toolset="skills",
    schema=SKILL_SCAN_SCHEMA,
    handler=_skill_scan,
    description="Run a static security scan for an installed skill.",
)

registry.register(
    name="skill_manage",
    toolset="skills",
    schema=SKILL_MANAGE_SCHEMA,
    handler=_skill_manage,
    description="Create, edit, patch, delete, and manage skill files.",
)
