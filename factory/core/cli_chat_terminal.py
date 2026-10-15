"""Terminal title configuration and notification helpers for rich chat."""
from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .status_line import get_factory_settings_path


@dataclass(frozen=True)
class TerminalTitleConfig:
    items: tuple[str, ...] = ()


TERMINAL_TITLE_ITEM_LABELS: dict[str, str] = {
    "app-name": "App name",
    "project-name": "Project name",
    "current-dir": "Current directory",
    "thread-title": "Current task title",
    "git-branch": "Git branch",
    "context-remaining": "Context remaining",
    "context-used": "Context used",
    "factory-version": "Factory version",
    "session-id": "Session id",
    "model": "Model",
}

TERMINAL_TITLE_ITEM_DESCRIPTIONS: dict[str, str] = {
    "app-name": "Igorek app name",
    "project-name": "git project root name, or current directory name",
    "current-dir": "current working directory",
    "thread-title": "current user task summary",
    "git-branch": "current branch, omitted when unavailable",
    "context-remaining": "remaining context tokens, omitted when unknown",
    "context-used": "current context usage, omitted when unknown",
    "factory-version": "installed factory version",
    "session-id": "current chat session id",
    "model": "active model display name",
}

# Codex defaults to activity + project. Python does not yet have live title animation,
# so the setup preview starts with the supported stable equivalent.
DEFAULT_TERMINAL_TITLE_ITEMS: tuple[str, ...] = ("app-name", "project-name")


def _sanitize_terminal_title(text: str, *, max_chars: int = 90) -> str:
    clean = re.sub(r"\s+", " ", str(text or ""))
    clean = re.sub(r"[\x00-\x1f\x7f\u200b-\u200f\u202a-\u202e\u2060-\u206f\ufeff]", "", clean)
    clean = re.sub(r"\s+", " ", clean).strip()
    if len(clean) > max_chars:
        clean = clean[: max_chars - 1].rstrip() + "…"
    return clean


def _task_summary(user_text: str) -> str:
    first = next((line.strip() for line in str(user_text or "").splitlines() if line.strip()), "")
    return _sanitize_terminal_title(first, max_chars=48)


def _task_terminal_title(user_text: str) -> str:
    title = _task_summary(user_text)
    return f"Igorek · {title}" if title else "Igorek"


def _valid_terminal_title_items(items: Any) -> tuple[str, ...]:
    if not isinstance(items, list):
        return ()
    valid = set(TERMINAL_TITLE_ITEM_LABELS)
    result: list[str] = []
    for raw in items:
        item = str(raw or "").strip()
        if item in valid and item not in result:
            result.append(item)
    return tuple(result)


def load_terminal_title_config(settings_path: Path | None = None) -> TerminalTitleConfig:
    path = settings_path or get_factory_settings_path()
    if not path.exists():
        return TerminalTitleConfig()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return TerminalTitleConfig()
    if not isinstance(data, dict):
        return TerminalTitleConfig()
    raw = data.get("terminalTitle")
    if not isinstance(raw, dict):
        return TerminalTitleConfig()
    return TerminalTitleConfig(items=_valid_terminal_title_items(raw.get("items")))


def save_terminal_title_items(
    items: list[str] | tuple[str, ...],
    *,
    settings_path: Path | None = None,
) -> TerminalTitleConfig:
    path = settings_path or get_factory_settings_path()
    data: dict[str, Any] = {}
    if path.exists():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                data = loaded
        except (OSError, json.JSONDecodeError):
            data = {}
    selected = _valid_terminal_title_items(list(items))
    data["terminalTitle"] = {"items": list(selected)}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return TerminalTitleConfig(items=selected)


def terminal_title_item_catalog() -> list[dict[str, str]]:
    return [
        {
            "id": item,
            "label": TERMINAL_TITLE_ITEM_LABELS[item],
            "description": TERMINAL_TITLE_ITEM_DESCRIPTIONS.get(item, ""),
        }
        for item in TERMINAL_TITLE_ITEM_LABELS
    ]


def _short_path(path: str) -> str:
    if not path:
        return ""
    text = str(path)
    home = str(Path.home())
    if text == home:
        return "~"
    if text.startswith(home + "/"):
        return "~" + text[len(home):]
    return text


def _compact_tokens(value: Any) -> str:
    try:
        number = int(value)
    except (TypeError, ValueError):
        return ""
    if number >= 1_000_000:
        return f"{number / 1_000_000:.1f}M"
    if number >= 1_000:
        return f"{number / 1_000:.1f}k"
    return str(number)


def render_terminal_title(
    status_input: dict[str, Any],
    items: tuple[str, ...],
    *,
    user_text: str = "",
) -> str:
    if not items:
        return _task_terminal_title(user_text)
    model = status_input.get("model") if isinstance(status_input.get("model"), dict) else {}
    workspace = status_input.get("workspace") if isinstance(status_input.get("workspace"), dict) else {}
    git = status_input.get("git") if isinstance(status_input.get("git"), dict) else {}
    context = status_input.get("context_window") if isinstance(status_input.get("context_window"), dict) else {}
    current_dir = str(workspace.get("current_dir") or status_input.get("cwd") or "")
    project_dir = str(workspace.get("project_dir") or current_dir)
    try:
        project_name = Path(project_dir).name or Path(current_dir).name
    except Exception:
        project_name = ""
    used = context.get("current_context_tokens")
    window = context.get("context_window_size")
    try:
        remaining = max(0, int(window) - int(used))
    except (TypeError, ValueError):
        remaining = None
    branch = str(git.get("branch") or "")
    if branch and git.get("is_clean") is False:
        branch += "*"
    values = {
        "app-name": "Igorek",
        "project-name": project_name,
        "current-dir": _short_path(current_dir),
        "thread-title": _task_summary(user_text),
        "git-branch": branch,
        "factory-version": str(status_input.get("version") or ""),
        "session-id": str(status_input.get("session_id") or ""),
        "model": str(model.get("display_name") or model.get("id") or ""),
    }
    if used is not None:
        values["context-used"] = f"ctx {_compact_tokens(used)} used"
    if remaining is not None:
        values["context-remaining"] = f"{_compact_tokens(remaining)} left"
    rendered = [values.get(item, "") for item in items]
    title = " | ".join(part for part in rendered if part)
    return _sanitize_terminal_title(title, max_chars=120)


def _set_terminal_title(title: str) -> None:
    if not sys.stdout.isatty():
        return
    clean = _sanitize_terminal_title(title, max_chars=120)
    if not clean:
        return
    try:
        sys.stdout.write(f"\033]0;{clean}\033\\")
        sys.stdout.flush()
    except OSError:
        pass


def _notify_turn_complete() -> None:
    if not sys.stdout.isatty():
        return
    try:
        sys.stdout.write("\a")
        sys.stdout.flush()
    except OSError:
        pass


__all__ = [
    "DEFAULT_TERMINAL_TITLE_ITEMS",
    "TerminalTitleConfig",
    "_notify_turn_complete",
    "_sanitize_terminal_title",
    "_set_terminal_title",
    "_task_terminal_title",
    "load_terminal_title_config",
    "render_terminal_title",
    "save_terminal_title_items",
    "terminal_title_item_catalog",
]
