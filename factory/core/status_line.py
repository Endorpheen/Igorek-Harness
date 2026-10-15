from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .runtime import get_data_root, get_db_path
from .schema import open_db


ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


@dataclass(frozen=True)
class StatusLineConfig:
    type: str = "builtin"
    command: str = ""
    padding: int = 0
    items: tuple[str, ...] = ()


STATUS_LINE_ITEM_LABELS: dict[str, str] = {
    "profile": "Profile",
    "model": "Model",
    "git-branch": "Git branch",
    "context-used": "Context used",
    "context-remaining": "Context remaining",
    "cost": "Session cost",
    "current-dir": "Current directory",
    "project-dir": "Project directory",
    "factory-version": "Factory version",
    "session-id": "Session id",
}

DEFAULT_STATUS_LINE_ITEMS: tuple[str, ...] = (
    "profile",
    "model",
    "git-branch",
    "context-used",
    "cost",
    "current-dir",
)

STATUS_LINE_ITEM_DESCRIPTIONS: dict[str, str] = {
    "profile": "active profile name",
    "model": "model display name",
    "git-branch": "current branch, with * when dirty",
    "context-used": "current context usage and percentage",
    "context-remaining": "remaining context tokens",
    "cost": "session cost when known",
    "current-dir": "current working directory",
    "project-dir": "git project root or cwd",
    "factory-version": "installed factory version",
    "session-id": "current chat session id",
}


def get_factory_settings_path() -> Path:
    return get_data_root() / "settings.json"


def _valid_status_line_items(items: Any) -> tuple[str, ...]:
    if not isinstance(items, list):
        return ()
    valid = set(STATUS_LINE_ITEM_LABELS)
    result: list[str] = []
    for raw in items:
        item = str(raw or "").strip()
        if item in valid and item not in result:
            result.append(item)
    return tuple(result)


def load_status_line_config(settings_path: Path | None = None) -> StatusLineConfig:
    path = settings_path or get_factory_settings_path()
    if not path.exists():
        return StatusLineConfig()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return StatusLineConfig()
    if not isinstance(data, dict):
        return StatusLineConfig()
    raw = data.get("statusLine")
    if not isinstance(raw, dict):
        return StatusLineConfig()
    line_type = str(raw.get("type") or "").strip()
    command = str(raw.get("command") or "").strip()
    try:
        padding = max(0, int(raw.get("padding") or 0))
    except Exception:
        padding = 0
    items = _valid_status_line_items(raw.get("items"))
    if line_type == "command" and command:
        return StatusLineConfig(type="command", command=command, padding=padding)
    return StatusLineConfig(items=items)


def save_status_line_items(
    items: list[str] | tuple[str, ...],
    *,
    settings_path: Path | None = None,
) -> StatusLineConfig:
    path = settings_path or get_factory_settings_path()
    data: dict[str, Any] = {}
    if path.exists():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                data = loaded
        except (OSError, json.JSONDecodeError):
            data = {}
    selected = _valid_status_line_items(list(items))
    data["statusLine"] = {
        "type": "builtin",
        "items": list(selected),
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return StatusLineConfig(items=selected)


def _short_path(path: str, *, home: Path | None = None) -> str:
    if not path:
        return "-"
    home = home or Path.home()
    try:
        resolved = str(Path(path).resolve())
    except Exception:
        resolved = path
    home_text = str(home)
    if resolved == home_text:
        return "~"
    if resolved.startswith(home_text + os.sep):
        return "~" + resolved[len(home_text):]
    return resolved


def _run_git(args: list[str], cwd: str) -> str:
    from .readonly_git import read_git
    try:
        current = Path(cwd).resolve()
        root = next((path for path in (current, *current.parents)
                     if (path / '.git').exists()), current)
        return read_git(root, args, timeout=2).decode('utf-8', errors='replace').strip()
    except (OSError, ValueError, subprocess.TimeoutExpired):
        return ""


def git_status(cwd: str) -> dict[str, Any]:
    branch = _run_git(["rev-parse", "--abbrev-ref", "HEAD"], cwd)
    commit = _run_git(["rev-parse", "--short", "HEAD"], cwd)
    root = _run_git(["rev-parse", "--show-toplevel"], cwd)
    porcelain = _run_git(["status", "--porcelain"], cwd)
    return {
        "branch": branch,
        "commit": commit,
        "root": root,
        "is_clean": porcelain == "" if branch or commit or root else None,
    }


def _safe_int(value: Any) -> int | None:
    try:
        if value is None:
            return None
        return int(value)
    except Exception:
        return None


def _compact_tokens(value: int | None) -> str:
    if value is None:
        return "n/a"
    if value >= 1_000_000:
        return f"{value / 1_000_000:.1f}M"
    if value >= 1_000:
        return f"{value / 1_000:.1f}k"
    return str(value)


def status_line_item_catalog() -> list[dict[str, str]]:
    return [
        {
            "id": item,
            "label": STATUS_LINE_ITEM_LABELS[item],
            "description": STATUS_LINE_ITEM_DESCRIPTIONS.get(item, ""),
        }
        for item in STATUS_LINE_ITEM_LABELS
    ]


def _ctx_color(used_pct: int | float) -> str:
    if used_pct < 60:
        return "\033[2m"
    if used_pct < 80:
        return "\033[33m"
    if used_pct <= 92:
        return "\033[38;5;214m"
    return "\033[31m"


_ANSI_RESET = "\033[0m"
_STATUS_SEPARATOR = f" \033[2m·{_ANSI_RESET} "
_STATUS_ITEM_COLORS: dict[str, str] = {
    "profile": "\033[38;5;222m",
    "model": "\033[38;5;222m",
    "git-branch": "\033[38;5;111m",
    "context-used": "\033[38;5;214m",
    "context-remaining": "\033[38;5;214m",
    "cost": "\033[38;5;205m",
    "current-dir": "\033[38;5;114m",
    "project-dir": "\033[38;5;114m",
    "factory-version": "\033[38;5;176m",
    "session-id": "\033[38;5;176m",
}


def _style_status_item(item: str, text: str, *, used_pct: int | float | None = None) -> str:
    if not text:
        return ""
    color = (
        _ctx_color(used_pct)
        if item == "context-used" and used_pct is not None
        else _STATUS_ITEM_COLORS.get(item)
    )
    if not color:
        return text
    return f"{color}{text}{_ANSI_RESET}"


def _join_status_items(items: list[tuple[str, str]], *, used_pct: int | float | None = None) -> str:
    rendered = [
        _style_status_item(item, text, used_pct=used_pct)
        for item, text in items
        if text
    ]
    return _STATUS_SEPARATOR.join(rendered)


def _usage_from_row(row: sqlite3.Row) -> dict[str, Any]:
    metadata = {}
    if row["metadata"]:
        try:
            loaded = json.loads(row["metadata"])
            if isinstance(loaded, dict):
                metadata = loaded
        except json.JSONDecodeError:
            metadata = {}
    usage_ext = metadata.get("usage_ext") if isinstance(metadata.get("usage_ext"), dict) else {}
    prompt_tokens = _safe_int(row["usage_prompt"]) or 0
    completion_tokens = _safe_int(row["usage_completion"]) or 0
    total_tokens = _safe_int(usage_ext.get("total_tokens"))
    current_context_tokens = _safe_int(usage_ext.get("current_usage"))
    context_window = _safe_int(usage_ext.get("model_context_window"))
    current_usage: dict[str, int] = {}
    for source_key, target_key in (
        ("input_tokens", "input_tokens"),
        ("output_tokens", "output_tokens"),
        ("cache_creation_input_tokens", "cache_creation_input_tokens"),
        ("cache_read_input_tokens", "cache_read_input_tokens"),
    ):
        value = _safe_int(usage_ext.get(source_key))
        if value is not None:
            current_usage[target_key] = value
    if not current_usage and (prompt_tokens or completion_tokens):
        current_usage = {
            "input_tokens": prompt_tokens,
            "output_tokens": completion_tokens,
        }
    if total_tokens is None and current_usage:
        total_tokens = sum(current_usage.values())
    if current_context_tokens is None:
        current_context_tokens = total_tokens
    return {
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": total_tokens,
        "current_context_tokens": current_context_tokens,
        "context_window": context_window,
        "current_usage": current_usage or None,
    }


def _latest_usage_for_task_ids(task_ids: list[str]) -> dict[str, Any]:
    if not task_ids:
        return {}
    placeholders = ", ".join(["?"] * len(task_ids))
    try:
        with open_db(get_db_path()) as conn:
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            cur.execute(
                f"""
                SELECT task_id, usage_prompt, usage_completion, metadata, completed_at, created_at
                FROM tasks
                WHERE task_id IN ({placeholders})
                ORDER BY completed_at DESC, created_at DESC
                LIMIT 1
                """,
                task_ids,
            )
            row = cur.fetchone()
    except Exception:
        return {}
    if not row:
        return {}
    return _usage_from_row(row)


def _latest_usage_for_session(session_name: str) -> dict[str, Any]:
    try:
        with open_db(get_db_path()) as conn:
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            cur.execute(
                """
                SELECT usage_prompt, usage_completion, metadata
                FROM tasks
                WHERE metadata LIKE ?
                ORDER BY completed_at DESC, created_at DESC
                LIMIT 1
                """,
                (f'%"session": "{session_name}"%',),
            )
            row = cur.fetchone()
    except Exception:
        return {}
    if not row:
        return {}
    return _usage_from_row(row)


def _session_total_tokens(task_ids: list[str]) -> int | None:
    if not task_ids:
        return None
    placeholders = ", ".join(["?"] * len(task_ids))
    try:
        with open_db(get_db_path()) as conn:
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            cur.execute(
                f"""
                SELECT usage_prompt, usage_completion, metadata
                FROM tasks
                WHERE task_id IN ({placeholders})
                """,
                task_ids,
            )
            rows = cur.fetchall()
    except Exception:
        return None
    total = 0
    for row in rows:
        metadata = {}
        if row["metadata"]:
            try:
                loaded = json.loads(row["metadata"])
                if isinstance(loaded, dict):
                    metadata = loaded
            except json.JSONDecodeError:
                metadata = {}
        usage_ext = metadata.get("usage_ext") if isinstance(metadata.get("usage_ext"), dict) else {}
        ext_total = _safe_int(usage_ext.get("total_tokens"))
        if ext_total is not None:
            total += ext_total
        else:
            total += (_safe_int(row["usage_prompt"]) or 0) + (
                _safe_int(row["usage_completion"]) or 0
            )
    return total


def _session_total_cost_usd(task_ids: list[str]) -> float:
    if not task_ids:
        return 0.0
    placeholders = ", ".join(["?"] * len(task_ids))
    try:
        with open_db(get_db_path()) as conn:
            conn.row_factory = sqlite3.Row
            cur = conn.cursor()
            cur.execute(
                f"""
                SELECT cost_usd
                FROM tasks
                WHERE task_id IN ({placeholders})
                """,
                task_ids,
            )
            rows = cur.fetchall()
    except Exception:
        return 0.0
    total = 0.0
    for row in rows:
        try:
            total += max(0.0, float(row["cost_usd"] or 0))
        except Exception:
            continue
    return total


def build_status_line_input(
    *,
    session_name: str,
    session_path: Path,
    profile: str,
    profile_config: dict[str, Any],
    model: str,
    cwd: str,
    version: str,
    task_ids: list[str] | None = None,
) -> dict[str, Any]:
    git = git_status(cwd)
    latest_usage = _latest_usage_for_task_ids(task_ids or []) or _latest_usage_for_session(session_name)
    session_tokens = _session_total_tokens(task_ids or [])
    session_cost_usd = _session_total_cost_usd(task_ids or [])
    context_window = latest_usage.get("context_window")
    current_total = latest_usage.get("current_context_tokens")
    used_percentage = None
    remaining_percentage = None
    if isinstance(context_window, int) and context_window > 0 and isinstance(current_total, int):
        used_percentage = max(0, min(100, round((current_total / context_window) * 100)))
        remaining_percentage = max(0, 100 - used_percentage)
    provider = str(profile_config.get("provider") or "").strip()
    backend = str(profile_config.get("backend") or "").strip()
    if backend == "gguf" or backend == "cc-backend":
        provider_display = "YOUR_PC"
    else:
        provider_display = provider
    return {
        "session_id": session_name,
        "session_name": session_name,
        "transcript_path": str(session_path),
        "cwd": cwd,
        "permission_mode": "ask",
        "model": {
            "id": model,
            "display_name": model,
        },
        "workspace": {
            "current_dir": cwd,
            "project_dir": git.get("root") or cwd,
            "added_dirs": [],
        },
        "version": version,
        "output_style": {
            "name": "default",
        },
        "backend": {
            "profile": profile,
            "provider": provider_display,
            "type": backend,
        },
        "git": git,
        "cost": {
            "total_cost_usd": session_cost_usd,
            "total_duration_ms": 0,
            "total_api_duration_ms": 0,
            "total_lines_added": 0,
            "total_lines_removed": 0,
        },
        "context_window": {
            "total_input_tokens": latest_usage.get("prompt_tokens") or 0,
            "total_output_tokens": latest_usage.get("completion_tokens") or 0,
            "session_total_tokens": session_tokens,
            "context_window_size": context_window,
            "current_context_tokens": current_total,
            "current_usage": latest_usage.get("current_usage"),
            "used_percentage": used_percentage,
            "remaining_percentage": remaining_percentage,
        },
        "exceeds_200k_tokens": bool(isinstance(current_total, int) and current_total > 200_000),
    }


def _clean_status_output(stdout: str) -> str:
    lines = []
    for line in stdout.strip().splitlines():
        cleaned = line.strip()
        if cleaned:
            lines.append(cleaned)
    return "\n".join(lines)


def execute_status_line_command(
    config: StatusLineConfig,
    status_input: dict[str, Any],
    *,
    timeout_s: float = 5.0,
) -> str | None:
    if config.type != "command" or not config.command:
        return None
    try:
        result = subprocess.run(
            config.command,
            input=json.dumps(status_input, ensure_ascii=False),
            capture_output=True,
            text=True,
            timeout=timeout_s,
            check=False,
            shell=True,
            executable=shutil.which("bash") or None,
            env=os.environ.copy(),
        )
    except Exception:
        return None
    if result.returncode != 0:
        return None
    output = _clean_status_output(result.stdout or "")
    return output or None


def render_builtin_status_line(status_input: dict[str, Any]) -> str:
    backend = status_input.get("backend") if isinstance(status_input.get("backend"), dict) else {}
    model = status_input.get("model") if isinstance(status_input.get("model"), dict) else {}
    workspace = (
        status_input.get("workspace")
        if isinstance(status_input.get("workspace"), dict)
        else {}
    )
    git = status_input.get("git") if isinstance(status_input.get("git"), dict) else {}
    context = (
        status_input.get("context_window")
        if isinstance(status_input.get("context_window"), dict)
        else {}
    )
    cost = status_input.get("cost") if isinstance(status_input.get("cost"), dict) else {}
    branch = str(git.get("branch") or "-")
    if git.get("is_clean") is False:
        branch += "*"
    used_pct = context.get("used_percentage")
    if not isinstance(used_pct, (int, float)):
        used_pct = None
    current_usage = context.get("current_usage")
    current_context_tokens = _safe_int(context.get("current_context_tokens"))
    if isinstance(current_usage, dict):
        last_task_total = sum((_safe_int(value) or 0) for value in current_usage.values())
        used = (
            current_context_tokens
            if isinstance(current_context_tokens, int) and current_context_tokens > 0
            else last_task_total
        )
    else:
        used = current_context_tokens
    window = _safe_int(context.get("context_window_size"))
    left = max(0, window - used) if isinstance(window, int) and isinstance(used, int) else None
    cwd = _short_path(str(workspace.get("current_dir") or status_input.get("cwd") or ""))
    items = [
        ("profile", str(backend.get("profile") or "-")),
        ("model", str(model.get("display_name") or model.get("id") or "-")),
        ("git-branch", branch),
    ]
    if used is not None and window is not None and left is not None:
        pct = round((used / window) * 100)
        items.append(
            (
                "context-used",
                f"ctx {_compact_tokens(used)}/{_compact_tokens(window)}  {pct}% used  {_compact_tokens(left)} left",
            )
        )
    elif used is not None:
        items.append(("context-used", f"session {_compact_tokens(used)} used"))
    total_cost_usd = cost.get("total_cost_usd")
    if isinstance(total_cost_usd, (int, float)) and total_cost_usd > 0:
        items.append(("cost", f"${total_cost_usd:.2f}"))
    items.append(("current-dir", cwd))
    return _join_status_items(items, used_pct=used_pct)


def render_configured_status_line(status_input: dict[str, Any], items: tuple[str, ...]) -> str:
    if not items:
        return render_builtin_status_line(status_input)
    backend = status_input.get("backend") if isinstance(status_input.get("backend"), dict) else {}
    model = status_input.get("model") if isinstance(status_input.get("model"), dict) else {}
    workspace = (
        status_input.get("workspace")
        if isinstance(status_input.get("workspace"), dict)
        else {}
    )
    git = status_input.get("git") if isinstance(status_input.get("git"), dict) else {}
    context = (
        status_input.get("context_window")
        if isinstance(status_input.get("context_window"), dict)
        else {}
    )
    cost = status_input.get("cost") if isinstance(status_input.get("cost"), dict) else {}
    branch = str(git.get("branch") or "")
    if branch and git.get("is_clean") is False:
        branch += "*"
    current_usage = context.get("current_usage")
    current_context_tokens = _safe_int(context.get("current_context_tokens"))
    if isinstance(current_usage, dict):
        last_task_total = sum((_safe_int(value) or 0) for value in current_usage.values())
        used = current_context_tokens if isinstance(current_context_tokens, int) and current_context_tokens > 0 else last_task_total
    else:
        used = current_context_tokens
    window = _safe_int(context.get("context_window_size"))
    left = max(0, window - used) if isinstance(window, int) and isinstance(used, int) else None
    used_pct = context.get("used_percentage")
    if (
        not isinstance(used_pct, (int, float))
        and isinstance(window, int)
        and isinstance(used, int)
        and window > 0
    ):
        used_pct = round((used / window) * 100)
    total_cost_usd = cost.get("total_cost_usd")
    values = {
        "profile": str(backend.get("profile") or ""),
        "model": str(model.get("display_name") or model.get("id") or ""),
        "git-branch": branch,
        "current-dir": _short_path(str(workspace.get("current_dir") or status_input.get("cwd") or "")),
        "project-dir": _short_path(str(workspace.get("project_dir") or "")),
        "factory-version": str(status_input.get("version") or ""),
        "session-id": str(status_input.get("session_id") or ""),
    }
    if used is not None and window is not None:
        pct_text = f" {round(used_pct)}% used" if isinstance(used_pct, (int, float)) else ""
        values["context-used"] = f"ctx {_compact_tokens(used)}/{_compact_tokens(window)}{pct_text}"
    elif used is not None:
        values["context-used"] = f"session {_compact_tokens(used)} used"
    if left is not None:
        values["context-remaining"] = f"{_compact_tokens(left)} left"
    if isinstance(total_cost_usd, (int, float)) and total_cost_usd > 0:
        values["cost"] = f"${total_cost_usd:.2f}"
    rendered = [(item, values.get(item, "")) for item in items]
    return _join_status_items(rendered, used_pct=used_pct)


class StatusLineRuntime:
    def __init__(
        self,
        *,
        session_name: str,
        session_path: Path,
        profile: str,
        profile_config: dict[str, Any],
        model: str,
        version: str,
        cwd: str | None = None,
    ) -> None:
        self.session_name = session_name
        self.session_path = session_path
        self.profile = profile
        self.profile_config = profile_config
        self.model = model
        self.version = version
        self.cwd = cwd or str(Path.cwd())
        self.task_ids: list[str] = []
        self.text = ""
        self.config = StatusLineConfig()
        self.settings_path = get_factory_settings_path()
        self.last_input: dict[str, Any] = {}
        self.refresh()

    def note_task(self, task_id: str | None) -> None:
        if task_id and task_id not in self.task_ids:
            self.task_ids.append(task_id)
        self.refresh()

    def refresh(self) -> str:
        self.config = load_status_line_config(self.settings_path)
        self.last_input = build_status_line_input(
            session_name=self.session_name,
            session_path=self.session_path,
            profile=self.profile,
            profile_config=self.profile_config,
            model=self.model,
            cwd=self.cwd,
            version=self.version,
            task_ids=self.task_ids,
        )
        custom = execute_status_line_command(self.config, self.last_input)
        self.text = custom or render_configured_status_line(self.last_input, self.config.items)
        return self.text

    def toolbar_text(self) -> str:
        return self.text or self.refresh()

    def plain_text(self) -> str:
        return ANSI_RE.sub("", self.toolbar_text())

    def debug_summary(self) -> dict[str, Any]:
        return {
            "settings_path": str(self.settings_path),
            "configured": self.config.type == "command" or bool(self.config.items),
            "command": self.config.command,
            "padding": self.config.padding,
            "items": list(self.config.items),
            "text": self.text,
            "input": self.last_input,
        }
