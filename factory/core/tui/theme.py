"""Bundled syntax themes for the Python TUI runtime."""
from __future__ import annotations

import json
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator

from ..status_line import get_factory_settings_path


@dataclass(frozen=True)
class ThemeSpec:
    name: str
    label: str
    description: str
    pygments_style: str
    rich_code_style: str
    code_ansi: str
    heading_ansi: str
    bullet_ansi: str
    diff_add_ansi: str
    diff_del_ansi: str
    diff_context_ansi: str = "\033[2m"


_BUNDLED_THEMES: tuple[ThemeSpec, ...] = (
    ThemeSpec(
        name="dark",
        label="Dark",
        description="Igorek dark palette with Monokai syntax highlighting",
        pygments_style="monokai",
        rich_code_style="cyan",
        code_ansi="\033[36m",
        heading_ansi="\033[33m",
        bullet_ansi="\033[32m",
        diff_add_ansi="\033[48;5;22m\033[38;5;82m",
        diff_del_ansi="\033[48;5;52m\033[38;5;196m",
    ),
    ThemeSpec(
        name="light",
        label="Light",
        description="Light-terminal palette with Friendly syntax highlighting",
        pygments_style="friendly",
        rich_code_style="blue",
        code_ansi="\033[34m",
        heading_ansi="\033[35m",
        bullet_ansi="\033[32m",
        diff_add_ansi="\033[48;5;194m\033[38;5;28m",
        diff_del_ansi="\033[48;5;224m\033[38;5;124m",
    ),
    ThemeSpec(
        name="monokai",
        label="Monokai",
        description="High-contrast Monokai palette",
        pygments_style="monokai",
        rich_code_style="magenta",
        code_ansi="\033[35m",
        heading_ansi="\033[33m",
        bullet_ansi="\033[36m",
        diff_add_ansi="\033[48;5;22m\033[38;5;118m",
        diff_del_ansi="\033[48;5;52m\033[38;5;203m",
    ),
)

DEFAULT_THEME_NAME = "dark"
_PREVIEW_THEME_NAME: str | None = None
_THEME_CACHE: dict[str, tuple[int | None, str]] = {}


def list_available_themes() -> tuple[ThemeSpec, ...]:
    return _BUNDLED_THEMES


def resolve_theme(name: str | None) -> ThemeSpec:
    normalized = str(name or "").strip().lower()
    for theme in _BUNDLED_THEMES:
        if theme.name == normalized:
            return theme
    return next(theme for theme in _BUNDLED_THEMES if theme.name == DEFAULT_THEME_NAME)


def load_theme_name(settings_path: Path | None = None) -> str:
    path = settings_path or get_factory_settings_path()
    cache_key = str(path)
    try:
        mtime_ns = path.stat().st_mtime_ns
    except OSError:
        mtime_ns = None
    cached = _THEME_CACHE.get(cache_key)
    if cached is not None and cached[0] == mtime_ns:
        return cached[1]
    if mtime_ns is None:
        name = DEFAULT_THEME_NAME
    else:
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            data = {}
        name = resolve_theme(data.get("theme") if isinstance(data, dict) else None).name
    _THEME_CACHE[cache_key] = (mtime_ns, name)
    return name


def save_theme_name(name: str | None, *, settings_path: Path | None = None) -> ThemeSpec:
    path = settings_path or get_factory_settings_path()
    data: dict = {}
    if path.exists():
        try:
            loaded = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                data = loaded
        except (OSError, json.JSONDecodeError):
            data = {}
    if name is None or not str(name).strip():
        data.pop("theme", None)
        theme = resolve_theme(DEFAULT_THEME_NAME)
    else:
        theme = resolve_theme(name)
        if theme.name != str(name).strip().lower():
            raise ValueError(f"Unknown theme: {name}")
        data["theme"] = theme.name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return theme


def current_theme(settings_path: Path | None = None) -> ThemeSpec:
    return resolve_theme(_PREVIEW_THEME_NAME or load_theme_name(settings_path))


@contextmanager
def preview_theme(name: str) -> Iterator[ThemeSpec]:
    global _PREVIEW_THEME_NAME
    previous = _PREVIEW_THEME_NAME
    theme = resolve_theme(name)
    _PREVIEW_THEME_NAME = theme.name
    try:
        yield theme
    finally:
        _PREVIEW_THEME_NAME = previous
