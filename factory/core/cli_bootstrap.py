from __future__ import annotations

import getpass
import json
import os
import secrets
import shutil
import socket
import sqlite3
from pathlib import Path
from typing import Any

try:
    import readline  # noqa: F401 — includes arrows, history, and Home/End keys in input()
except ImportError:
    pass

from .agent_foundation import bootstrap_complete, ensure_foundation_files, get_foundation_paths
from .mcp_config import ensure_mcp_registry, get_mcp_registry_path, get_profile_mcp_servers, list_mcp_servers
from .runtime import (
    DEFAULT_CLI_HOME,
    DEFAULT_CLAUDE_MODEL,
    DEFAULT_CODEX_MODEL,
    DEFAULT_CODEX_REASONING_EFFORT,
    PACKAGE_ROOT,
    get_claude_settings_path,
    get_profile_config,
    get_profiles_dir,
    get_runtime_layout,
    profile_backend_name,
    profile_model_name,
    profile_reasoning_effort,
    resolve_claude_bin,
    resolve_codex_bin,
)
from .schema import TASK_COLUMNS, EVENT_COLUMNS, initialize_database, db_tables_check, open_db

DEFAULT_PROVIDER = "z.ai"
LMSTUDIO_PROVIDER = "lmstudio-anthropic"
CC_BACKEND_PROVIDER = "cc-backend"
DEFAULT_BACKEND = "claude"
SUPPORTED_BACKENDS = {"claude", "codex", "cc-backend"}
SUPPORTED_CLAUDE_PROVIDERS = {DEFAULT_PROVIDER, LMSTUDIO_PROVIDER}
DEFAULT_CC_BACKEND_BASE_URL = "http://127.0.0.1:8001"
DEFAULT_CC_BACKEND_MODEL = "gemma-4-E4B-it-Q4_K_S.gguf"
DEFAULT_LMSTUDIO_MODEL = "qwen35-4b"
SUPPORTED_CODEX_REASONING_EFFORTS = ("system", "low", "medium", "high", "xhigh")
SUPPORTED_GPT54_REASONING_EFFORTS = ("system", "none", "low", "medium", "high", "xhigh")


def _default_data_root() -> Path:
    override = os.environ.get("IGOREK_FACTORY_HOME", "").strip()
    if override:
        return Path(override).expanduser()
    return DEFAULT_CLI_HOME


DEFAULT_SPINNER_TIPS = [
    "Igorek AI Studio: сначала тесты, потом геройство.",
    "Проверь docker compose и логи, прежде чем орать на мир.",
    "UV > pip. Если ставишь зависимости, делай это нормально.",
    "Если что-то сломалось: tail -n 200 логов и думай.",
    "Не забудь: Игорёк сам себя не сделает! Пока ещё нет.",
]


def _prompt(message: str, default: str | None = None, secret: bool = False) -> str:
    suffix = f" [{default}]" if default else ""
    prompt = f"{message}{suffix}: "
    try:
        if secret:
            value = getpass.getpass(prompt)
        else:
            value = input(prompt).strip()
    except EOFError as exc:
        raise ValueError("Инициализация прервана: ввод закончился раньше времени.") from exc
    except KeyboardInterrupt as exc:
        raise KeyboardInterrupt("Инициализация прервана пользователем.") from exc
    if value:
        return value
    return default or ""


def _prompt_choice(message: str, options: list[tuple[str, str]], default: str) -> str:
    indexed = {str(index): value for index, (value, _) in enumerate(options, start=1)}
    labels = [f"{index}. {label}" for index, (_, label) in enumerate(options, start=1)]
    print(message)
    for line in labels:
        print(f"  {line}")
    try:
        raw = input(f"Выбор [{default}]: ").strip().lower()
    except EOFError as exc:
        raise ValueError("Инициализация прервана: ввод закончился раньше времени.") from exc
    except KeyboardInterrupt as exc:
        raise KeyboardInterrupt("Инициализация прервана пользователем.") from exc
    if not raw:
        return default
    if raw in indexed:
        return indexed[raw]
    values = {value for value, _ in options}
    if raw in values:
        return raw
    raise ValueError(f"Неизвестный выбор: {raw}")


def _prompt_confirm(message: str, default: bool = False) -> bool:
    hint = "Y/n" if default else "y/N"
    try:
        raw = input(f"{message} [{hint}]: ").strip().lower()
    except EOFError as exc:
        raise ValueError("Инициализация прервана: ввод закончился раньше времени.") from exc
    except KeyboardInterrupt as exc:
        raise KeyboardInterrupt("Инициализация прервана пользователем.") from exc
    if not raw:
        return default
    if raw in {"y", "yes", "д", "да"}:
        return True
    if raw in {"n", "no", "н", "нет"}:
        return False
    raise ValueError(f"Неизвестный выбор: {raw}")


def _resolve_backend_selection(
    *,
    backend: str | None,
    provider: str | None,
    interactive: bool,
) -> tuple[str, str]:
    selected_backend = str(backend or "").strip().lower()
    if not selected_backend:
        if not interactive:
            raise ValueError(
                "Не указан backend. Повтори команду с --backend codex, --backend claude или --backend cc-backend."
            )
        selected_backend = _prompt_choice(
            "Какой backend хочешь использовать? 🙂",
            [
                ("cc-backend", "cc-backend (локальная модель через llama.cpp)"),
                ("codex", "codex"),
                ("claude", "claude"),
            ],
            default="cc-backend",
        )
    if selected_backend not in SUPPORTED_BACKENDS:
        raise ValueError(f"Сейчас поддерживаются backend: {', '.join(sorted(SUPPORTED_BACKENDS))}")

    selected_provider = str(provider or "").strip().lower()
    if selected_backend == "claude":
        if not selected_provider:
            if not interactive:
                selected_provider = DEFAULT_PROVIDER
            else:
                selected_provider = _prompt_choice(
                    "Какой Claude provider хочешь использовать? 🙂",
                    [
                        (DEFAULT_PROVIDER, "z.ai"),
                        (LMSTUDIO_PROVIDER, "lmstudio-anthropic (LM Studio)"),
                    ],
                    default=DEFAULT_PROVIDER,
                )
        if selected_provider not in SUPPORTED_CLAUDE_PROVIDERS:
            raise ValueError(
                f"Для backend claude сейчас поддерживаются provider: {', '.join(sorted(SUPPORTED_CLAUDE_PROVIDERS))}"
            )
        return selected_backend, selected_provider

    if selected_backend == "cc-backend":
        return selected_backend, CC_BACKEND_PROVIDER

    return selected_backend, "openai-codex"


def _binary_is_available(binary: str) -> bool:
    if os.path.sep in binary:
        return Path(binary).exists()
    return bool(shutil.which(binary))


def _find_available_port(
    host: str = "127.0.0.1",
    start_port: int = 8180,
    max_attempts: int = 10,
) -> tuple[int, bool, bool]:
    for port in range(start_port, start_port + max_attempts):
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
                sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                sock.bind((host, port))
        except PermissionError:
            return start_port, True, False
        except OSError:
            continue
        return port, port == start_port, True
    return start_port, False, True


def _backend_install_hint(backend: str) -> str:
    if backend == "codex":
        return (
            "Codex CLI не найден.\n"
            "Сначала установи его, например так:\n"
            "  npm install -g @openai/codex\n"
            "Потом заново запусти:\n"
            "  igorek-factory init"
        )
    return (
        "Claude CLI не найден.\n"
        "Сначала установи его, например так:\n"
        "  npm install -g @anthropic-ai/claude-code\n"
        "Потом заново запусти:\n"
        "  igorek-factory init"
    )


def _preflight_backend_or_raise(backend: str) -> dict[str, str]:
    if backend == "cc-backend":
        return {"backend": backend, "binary": ""}
    binary = resolve_codex_bin() if backend == "codex" else resolve_claude_bin()
    if not _binary_is_available(binary):
        raise RuntimeError(_backend_install_hint(backend))
    return {
        "backend": backend,
        "binary": binary,
    }


def _codex_reasoning_options(model: str) -> tuple[str, ...]:
    normalized = str(model or "").strip().lower()
    if normalized.startswith("gpt-5.4"):
        return SUPPORTED_GPT54_REASONING_EFFORTS
    return SUPPORTED_CODEX_REASONING_EFFORTS


def _validate_codex_reasoning_effort(model: str, reasoning_effort: str | None) -> str:
    selected = str(reasoning_effort or DEFAULT_CODEX_REASONING_EFFORT).strip().lower() or DEFAULT_CODEX_REASONING_EFFORT
    allowed = _codex_reasoning_options(model)
    if selected not in allowed:
        raise ValueError(
            f"Для модели {model} сейчас поддерживаются reasoning effort: {', '.join(allowed)}."
        )
    return selected


def _resolve_codex_reasoning_effort(
    *,
    model: str,
    reasoning_effort: str | None,
    interactive: bool,
) -> str:
    if reasoning_effort:
        return _validate_codex_reasoning_effort(model, reasoning_effort)
    if not interactive:
        return DEFAULT_CODEX_REASONING_EFFORT

    allowed = _codex_reasoning_options(model)
    labels = [(value, "system default" if value == "system" else value) for value in allowed]
    selected = _prompt_choice(
        "Какой reasoning effort использовать? 🙂",
        list(labels),
        default=DEFAULT_CODEX_REASONING_EFFORT,
    )
    return _validate_codex_reasoning_effort(model, selected)


def _resolve_codex_dangerous_mode(
    *,
    dangerous_mode: bool | None,
    interactive: bool,
) -> bool:
    if dangerous_mode is not None:
        return bool(dangerous_mode)
    if not interactive:
        return False
    selected = _prompt_choice(
        "Какой режим выполнения Codex использовать? 🙂",
        [
            ("safe", "безопасный"),
            ("danger", "супер опасный"),
        ],
        default="safe",
    )
    return selected == "danger"


def _load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _resolve_env_file_path(data_root: Path, explicit_path: str | None = None) -> Path:
    if explicit_path:
        return Path(explicit_path).expanduser()
    candidates = [
        PACKAGE_ROOT / ".env",
        PACKAGE_ROOT / ".env.local",
        data_root / ".env",
        data_root / ".env.local",
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    if PACKAGE_ROOT.exists():
        return PACKAGE_ROOT / ".env"
    return data_root / ".env"


def _read_env_file(path: Path) -> tuple[list[str], dict[str, str]]:
    if not path.exists():
        return [], {}
    lines = path.read_text(encoding="utf-8").splitlines()
    values: dict[str, str] = {}
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip()
    return lines, values


def _write_env_file(path: Path, lines: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = "\n".join(lines).rstrip() + "\n"
    path.write_text(payload, encoding="utf-8")


def _upsert_env_values(path: Path, updates: dict[str, str]) -> None:
    existing_lines, _ = _read_env_file(path)
    lines = list(existing_lines)
    updated_keys: set[str] = set()
    for index, line in enumerate(lines):
        if "=" not in line or line.lstrip().startswith("#"):
            continue
        key, _ = line.split("=", 1)
        normalized_key = key.strip()
        if normalized_key in updates:
            lines[index] = f"{normalized_key}={updates[normalized_key]}"
            updated_keys.add(normalized_key)
    if lines and lines[-1].strip():
        lines.append("")
    for key, value in updates.items():
        if key in updated_keys:
            continue
        lines.append(f"{key}={value}")
    _write_env_file(path, lines)


def _env_keep_or_prompt(
    *,
    key: str,
    label: str,
    existing: str | None,
    interactive: bool,
    provided: str | None = None,
    default: str | None = None,
    secret: bool = False,
) -> str | None:
    explicit = None if provided is None else str(provided).strip()
    if explicit:
        return explicit
    normalized_existing = str(existing or "").strip()
    if normalized_existing:
        if not interactive:
            return normalized_existing
        keep = _prompt_confirm(f"{label} уже настроен в {key}. Оставить текущее значение? 🙂", default=True)
        if keep:
            return normalized_existing
    if not interactive:
        return default
    prompted = _prompt(label, default=default or "", secret=secret).strip()
    return prompted or default


def _resolve_bool_choice(
    *,
    label: str,
    interactive: bool,
    provided: bool | None,
    existing: str | None,
    default: bool,
) -> bool:
    if provided is not None:
        return bool(provided)
    normalized_existing = str(existing or "").strip().lower()
    if normalized_existing in {"1", "true", "yes", "on"}:
        if not interactive:
            return True
        keep = _prompt_confirm(f"{label} уже включён. Оставить как есть? 🙂", default=True)
        if keep:
            return True
    if normalized_existing in {"0", "false", "no", "off"}:
        if not interactive:
            return False
        keep = _prompt_confirm(f"{label} уже выключен. Оставить как есть? 🙂", default=True)
        if keep:
            return False
    if not interactive:
        return default
    return _prompt_confirm(label, default=default)


# ---------------------------------------------------------------------------
# Feature: Component checklist, tool selection, display style, review
# ---------------------------------------------------------------------------

def _prompt_checklist(
    message: str,
    items: list[tuple[str, str, bool]],
) -> list[str]:
    """Show numbered multi-select checklist. Return selected keys.

    Each item is ``(key, label, default_checked)``.
    User types comma-separated numbers to toggle items from their defaults.
    Empty input = accept all defaults.
    """
    state = {key: default for key, _label, default in items}
    print(message)
    for idx, (key, label, default) in enumerate(items, start=1):
        marker = "[x]" if default else "[ ]"
        print(f"  {marker} {idx}. {label}")
    try:
        raw = input("Номера через запятую для переключения [Enter — принять]: ").strip()
    except EOFError as exc:
        raise ValueError("Инициализация прервана: ввод закончился раньше времени.") from exc
    except KeyboardInterrupt as exc:
        raise KeyboardInterrupt("Инициализация прервана пользователем.") from exc
    if raw:
        tokens = [t.strip() for t in raw.split(",") if t.strip()]
        for token in tokens:
            if not token.isdigit():
                continue
            idx = int(token) - 1
            if 0 <= idx < len(items):
                key = items[idx][0]
                state[key] = not state[key]
    return [key for key, _label, _default in items if state[key]]


def _resolve_component_selection(
    *,
    interactive: bool,
    setup_telegram: bool | None,
    toolsets_provided: list[str] | None,
    display_provided: str | None,
) -> dict[str, bool]:
    """Interactive checklist for optional init components.

    Returns dict with setup_telegram, configure_toolsets, configure_display.
    """
    if not interactive:
        return {
            "setup_telegram": bool(setup_telegram),
            "configure_toolsets": False,
            "configure_display": False,
        }
    telegram_default = bool(setup_telegram)
    toolsets_default = toolsets_provided is not None
    display_default = display_provided is not None
    selected = _prompt_checklist(
        "Какие дополнительные компоненты настроить?",
        [
            ("telegram", "Telegram bridge", telegram_default),
            ("toolsets", "Выбор инструментов", toolsets_default),
            ("display", "Стиль отображения в TUI", display_default),
        ],
    )
    return {
        "setup_telegram": "telegram" in selected,
        "configure_toolsets": "toolsets" in selected,
        "configure_display": "display" in selected,
    }


def _resolve_toolsets(
    *,
    interactive: bool,
    provided: list[str] | None,
    previous: list[str] | None = None,
) -> list[str]:
    """Resolve which toolsets are enabled.

    Returns list of toolset names.  Empty list means all enabled (backward compat).
    ``previous`` is used when re-entering the checklist after review — preserves toggles.
    """
    if provided is not None:
        return [t.strip() for t in provided if t.strip()]
    if not interactive:
        return []
    # If we have previous selections, use them as defaults; otherwise use hardcoded
    all_toolsets = ["file", "terminal", "search", "web", "session", "skills"]
    if previous:
        prev_set = set(previous)
    else:
        prev_set = {"file", "terminal", "search", "session"}
    selected = _prompt_checklist(
        "Какие группы инструментов разрешить?",
        [
            ("file", "Чтение/запись файлов (Read, Write, Edit)", "file" in prev_set),
            ("terminal", "Выполнение команд (Bash)", "terminal" in prev_set),
            ("search", "Поиск файлов и контента (Glob, Grep)", "search" in prev_set),
            ("web", "Веб-поиск (web_search, web_extract)", "web" in prev_set),
            ("session", "Поиск по сессиям (SessionSearch)", "session" in prev_set),
        ],
    )
    return selected


_DISPLAY_VERBOSE = "verbose"
_DISPLAY_COMPACT = "compact"
_DISPLAY_MINIMAL = "minimal"
_DISPLAY_OPTIONS = {"verbose", "compact", "minimal"}


def _resolve_display_style(
    *,
    interactive: bool,
    provided: str | None,
) -> str:
    """Resolve tool_display style: verbose, compact, or minimal."""
    if provided and provided.strip().lower() in _DISPLAY_OPTIONS:
        return provided.strip().lower()
    if not interactive:
        return ""
    return _prompt_choice(
        "Стиль отображения в TUI? 🎨",
        [
            ("verbose", "подробный — показывать размышления модели"),
            ("compact", "компактный — сворачивать размышления"),
            ("minimal", "минимальный — убирать размышления, меньше текста"),
        ],
        default=_DISPLAY_COMPACT,
    )


def _review_and_confirm(
    *,
    backend: str,
    provider: str,
    model: str,
    reasoning_effort: str | None,
    dangerous_mode: bool,
    enabled_toolsets: list[str],
    display_style: str,
    telegram_enabled: bool,
    interactive: bool,
) -> bool:
    """Print formatted summary and ask for confirmation.

    Returns True to proceed, False to abort.
    Non-interactive always returns True.
    """
    if not interactive:
        return True
    toolset_line = ", ".join(enabled_toolsets) if enabled_toolsets else "все"
    reasoning_line = ""
    if reasoning_effort:
        reasoning_line = f"  Reasoning:      {reasoning_effort}\n"
    dangerous_line = ""
    if backend == "codex":
        dangerous_line = f"  Dangerous mode: {'on' if dangerous_mode else 'off'}\n"
    display_line = f"  Display:        {display_style or 'compact (default)'}\n"
    telegram_line = f"  Telegram:       {'on' if telegram_enabled else 'off'}\n"

    print()
    print("=== Сводка настроек ===")
    print(f"  Backend:        {backend}")
    print(f"  Provider:       {provider}")
    print(f"  Model:          {model}")
    print(reasoning_line, end="")
    print(dangerous_line, end="")
    print(f"  Toolsets:       {toolset_line}")
    print(display_line, end="")
    print(telegram_line, end="")
    print("========================")
    action = _prompt_choice(
        "Что дальше?",
        [
            ("write", "Записать настройки"),
            ("edit", "Вернуться к настройке компонентов"),
            ("abort", "Отменить инициализацию"),
        ],
        default="write",
    )
    if action == "write":
        return True
    if action == "abort":
        raise ValueError("Инициализация отменена пользователем.")
    return False


def _configure_telegram_env(
    *,
    data_root: Path,
    profile_names: list[str],
    interactive: bool,
    force: bool,
    env_file: str | None,
    setup_telegram: bool | None,
    telegram_bot_token: str | None,
    telegram_allowed_user_ids: str | None,
    telegram_profile: str | None,
    telegram_auto_approve: bool | None,
    telegram_auto_approve_scope: str | None,
) -> dict[str, Any] | None:
    env_path = _resolve_env_file_path(data_root, env_file)
    _, existing = _read_env_file(env_path)

    should_setup = bool(setup_telegram)
    if setup_telegram is None and interactive:
        should_setup = _prompt_confirm("Настроить Telegram bridge для runtime? 🙂", default=False)
    if not should_setup:
        return None

    primary_profile = profile_names[0] if profile_names else "codex"
    bot_token = _env_keep_or_prompt(
        key="FACTORY_TELEGRAM_BOT_TOKEN",
        label="Telegram bot token 🔑",
        existing=existing.get("FACTORY_TELEGRAM_BOT_TOKEN"),
        interactive=interactive,
        provided=telegram_bot_token,
        secret=True,
    )
    allowed_user_ids = _env_keep_or_prompt(
        key="FACTORY_TELEGRAM_ALLOWED_USER_IDS",
        label="Telegram user_id whitelist через запятую 👤",
        existing=existing.get("FACTORY_TELEGRAM_ALLOWED_USER_IDS"),
        interactive=interactive,
        provided=telegram_allowed_user_ids,
    )
    selected_profile = _env_keep_or_prompt(
        key="FACTORY_TELEGRAM_PROFILE",
        label="Профиль для Telegram bridge 🤖",
        existing=existing.get("FACTORY_TELEGRAM_PROFILE"),
        interactive=interactive,
        provided=telegram_profile,
        default=primary_profile,
    )
    selected_workdir = _env_keep_or_prompt(
        key="FACTORY_TELEGRAM_WORKDIR",
        label="Рабочая папка Telegram bridge 📁",
        existing=existing.get("FACTORY_TELEGRAM_WORKDIR"),
        interactive=interactive,
        default=str(PACKAGE_ROOT),
    )
    auto_approve = _resolve_bool_choice(
        label="Включить auto-approve для Telegram bridge? 🙂",
        interactive=interactive,
        provided=telegram_auto_approve,
        existing=existing.get("FACTORY_TELEGRAM_AUTO_APPROVE"),
        default=True,
    )
    auto_approve_scope = str(
        telegram_auto_approve_scope
        or existing.get("FACTORY_TELEGRAM_AUTO_APPROVE_SCOPE")
        or "session"
    ).strip().lower() or "session"
    if auto_approve_scope not in {"turn", "session"}:
        auto_approve_scope = "session"
    if interactive and telegram_auto_approve_scope is None and auto_approve:
        auto_approve_scope = _prompt_choice(
            "Какой scope использовать для Telegram auto-approve? 🙂",
            [
                ("session", "до конца session"),
                ("turn", "только на один turn"),
            ],
            default=auto_approve_scope,
        )

    api_key = _env_keep_or_prompt(
        key="FACTORY_API_KEY",
        label="FACTORY_API_KEY для runtime API 🔐",
        existing=existing.get("FACTORY_API_KEY"),
        interactive=interactive,
        default=secrets.token_hex(32) if not existing.get("FACTORY_API_KEY") else None,
        secret=True,
    )

    if not str(bot_token or "").strip():
        raise ValueError("Для Telegram setup нужен bot token.")
    if not str(allowed_user_ids or "").strip():
        raise ValueError("Для Telegram setup нужен хотя бы один Telegram user_id в whitelist.")
    if not str(api_key or "").strip():
        raise ValueError("Для Telegram setup нужен FACTORY_API_KEY.")

    updates = {
        "FACTORY_API_KEY": str(api_key).strip(),
        "FACTORY_TELEGRAM_ENABLED": "true",
        "FACTORY_TELEGRAM_BOT_TOKEN": str(bot_token).strip(),
        "FACTORY_TELEGRAM_ALLOWED_USER_IDS": str(allowed_user_ids).strip(),
        "FACTORY_TELEGRAM_PROFILE": str(selected_profile or primary_profile).strip() or primary_profile,
        "FACTORY_TELEGRAM_WORKDIR": str(selected_workdir or PACKAGE_ROOT).strip() or str(PACKAGE_ROOT),
        "FACTORY_TELEGRAM_POLL_TIMEOUT_S": str(existing.get("FACTORY_TELEGRAM_POLL_TIMEOUT_S") or "30").strip() or "30",
        "FACTORY_TELEGRAM_AUTO_APPROVE": "true" if auto_approve else "false",
        "FACTORY_TELEGRAM_AUTO_APPROVE_SCOPE": auto_approve_scope,
    }
    if force or existing.get("FACTORY_TELEGRAM_ALLOWED_CHAT_IDS") is not None:
        updates["FACTORY_TELEGRAM_ALLOWED_CHAT_IDS"] = str(existing.get("FACTORY_TELEGRAM_ALLOWED_CHAT_IDS") or "").strip()
    _upsert_env_values(env_path, updates)
    return {
        "env_file": str(env_path),
        "enabled": True,
        "profile": updates["FACTORY_TELEGRAM_PROFILE"],
        "auto_approve": auto_approve,
        "auto_approve_scope": auto_approve_scope,
        "allowed_user_ids": updates["FACTORY_TELEGRAM_ALLOWED_USER_IDS"],
    }


def _settings_payload(api_key: str, model: str) -> dict:
    return {
        "env": {
            "ANTHROPIC_BASE_URL": "https://api.z.ai/api/anthropic",
            "ANTHROPIC_AUTH_TOKEN": api_key,
            "API_TIMEOUT_MS": "3000000",
            "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": 1,
            "ANTHROPIC_MODEL": model,
            "ANTHROPIC_SMALL_FAST_MODEL": model,
            "ANTHROPIC_DEFAULT_SONNET_MODEL": model,
            "ANTHROPIC_DEFAULT_OPUS_MODEL": model,
            "ANTHROPIC_DEFAULT_HAIKU_MODEL": model,
        },
        "spinnerTipsOverride": {
            "excludeDefault": False,
            "tips": DEFAULT_SPINNER_TIPS,
        },
    }


def _lmstudio_profile_env(model: str, base_url: str, api_key: str | None = None) -> dict[str, str]:
    token = (api_key or "lmstudio").strip() or "lmstudio"
    normalized_base_url = base_url.strip().rstrip("/")
    return {
        "ANTHROPIC_BASE_URL": normalized_base_url,
        "ANTHROPIC_AUTH_TOKEN": token,
        "API_TIMEOUT_MS": "3000000",
        "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
        "ANTHROPIC_MODEL": model,
        "ANTHROPIC_SMALL_FAST_MODEL": model,
        "ANTHROPIC_DEFAULT_SONNET_MODEL": model,
        "ANTHROPIC_DEFAULT_OPUS_MODEL": model,
        "ANTHROPIC_DEFAULT_HAIKU_MODEL": model,
    }


def _write_claude_settings(api_key: str, model: str, force: bool) -> Path:
    settings_path = get_claude_settings_path()
    settings = _load_json(settings_path)
    generated = _settings_payload(api_key, model)

    if settings and not force:
        env = settings.get("env", {})
        env.update(generated["env"])
        settings["env"] = env
        if "spinnerTipsOverride" not in settings:
            settings["spinnerTipsOverride"] = generated["spinnerTipsOverride"]
    else:
        settings = generated

    _write_json(settings_path, settings)
    return settings_path


def _profile_payload(
    backend: str,
    provider: str,
    model: str,
    *,
    base_url: str | None = None,
    api_key: str | None = None,
    reasoning_effort: str | None = None,
    dangerous_mode: bool | None = None,
    enabled_toolsets: list[str] | None = None,
    display_style: str | None = None,
) -> dict:
    if backend == "claude":
        env: dict[str, str] = {}
        if provider == LMSTUDIO_PROVIDER:
            if not base_url:
                raise ValueError("Для provider lmstudio-anthropic обязателен --base-url")
            env = _lmstudio_profile_env(model=model, base_url=base_url, api_key=api_key)
        payload: dict[str, Any] = {
            "backend": "claude",
            "provider": provider,
            "model": model,
            "env": env,
        }
    elif backend == "codex":
        payload = {
            "backend": "codex",
            "provider": "openai-codex",
            "model": model,
            "env": {},
            "args": [],
        }
        selected_effort = _validate_codex_reasoning_effort(model, reasoning_effort)
        if selected_effort != DEFAULT_CODEX_REASONING_EFFORT:
            payload["reasoning_effort"] = selected_effort
        if dangerous_mode:
            payload["dangerously_bypass_approvals_and_sandbox"] = True
    elif backend == "cc-backend":
        payload = {
            "backend": "cc-backend",
            "model": model,
            "base_url": base_url or DEFAULT_CC_BACKEND_BASE_URL,
            "filesystem_mode": "user-read",
        }
    else:
        raise ValueError(f"Unsupported backend: {backend}")

    if enabled_toolsets:
        payload["enabled_toolsets"] = list(enabled_toolsets)
    if display_style:
        payload["display"] = {"tool_display": display_style}

    return payload


def _default_profile_names(backend: str, provider: str, profile_name: str | None) -> list[str]:
    if profile_name:
        return [profile_name]
    if backend == "claude" and provider == DEFAULT_PROVIDER:
        return ["zai", "minimax"]
    if backend == "claude" and provider == LMSTUDIO_PROVIDER:
        return ["lmstudio-claude"]
    if backend == "codex":
        return ["codex"]
    if backend == "cc-backend":
        return ["cc-local"]
    return [backend]


def _resolve_profile_name(
    *,
    backend: str,
    provider: str,
    profile_name: str | None,
    interactive: bool,
) -> str | None:
    if profile_name:
        return profile_name.strip()
    defaults = _default_profile_names(backend, provider, None)
    default_name = defaults[0] if defaults else backend
    if not interactive:
        return None
    return _prompt("Задайте имя профиля", default_name).strip() or default_name


def _write_profiles(
    profiles_dir: Path,
    backend: str,
    provider: str,
    model: str,
    base_url: str | None,
    api_key: str | None,
    reasoning_effort: str | None,
    dangerous_mode: bool | None,
    profile_name: str | None,
    force: bool,
    interactive: bool,
    enabled_toolsets: list[str] | None = None,
    display_style: str | None = None,
) -> tuple[list[Path], list[str], list[str]]:
    profiles_dir.mkdir(parents=True, exist_ok=True)
    written: list[Path] = []
    skipped: list[str] = []
    profile_names = _default_profile_names(backend, provider, profile_name)
    payload = _profile_payload(
        backend=backend,
        provider=provider,
        model=model,
        base_url=base_url,
        api_key=api_key,
        reasoning_effort=reasoning_effort,
        dangerous_mode=dangerous_mode,
        enabled_toolsets=enabled_toolsets,
        display_style=display_style,
    )
    for name in profile_names:
        profile_path = profiles_dir / f"{name}.json"
        if profile_path.exists() and not force:
            existing = _load_json(profile_path)
            if existing == payload:
                skipped.append(name)
                continue
            if interactive:
                overwrite = _prompt_confirm(
                    f"Профиль {name} уже существует. Перезаписать его? 🙂",
                    default=True,
                )
                if not overwrite:
                    skipped.append(name)
                    continue
            else:
                skipped.append(name)
                continue
        _write_json(profile_path, payload)
        written.append(profile_path)
    return written, profile_names, skipped


def _profile_summary(profiles_dir: Path) -> dict[str, dict]:
    summary: dict[str, dict] = {}
    if not profiles_dir.exists():
        return summary
    for profile_path in sorted(profiles_dir.glob("*.json")):
        try:
            config = get_profile_config(profile_path.stem, profiles_dir=profiles_dir)
        except Exception as exc:
            summary[profile_path.stem] = {"error": str(exc)}
            continue
        summary[profile_path.stem] = {
            "backend": profile_backend_name(config),
            "provider": str(config.get("provider", "")),
            "model": profile_model_name(config),
            "reasoning_effort": profile_reasoning_effort(config),
            "dangerous_mode": bool(config.get("dangerously_bypass_approvals_and_sandbox")),
            "mcp_servers": get_profile_mcp_servers(profile_path.stem, data_dir=str(profiles_dir.parent)),
        }
    return summary


def _build_init_next_steps(
    *,
    data_root: Path,
    backend: str,
    provider: str,
    profile_names: list[str],
) -> dict[str, Any]:
    primary_profile = profile_names[0] if profile_names else ("codex" if backend == "codex" else "zai")
    selected_port, default_port_free, port_probe_ok = _find_available_port()
    run_command = f"igorek-factory run --profile {primary_profile} --port {selected_port}"
    chat_tui_command = f"igorek-factory chat --profile {primary_profile} --tui"
    chat_command = f"igorek-factory chat --profile {primary_profile}"
    doctor_command = "igorek-factory doctor"

    if str(data_root) != str(DEFAULT_CLI_HOME):
        data_arg = f" --data-dir {data_root}"
        run_command += data_arg
        chat_tui_command += data_arg
        chat_command += data_arg
        doctor_command += data_arg

    notes: list[str] = []
    if backend == "codex":
        notes.append("Для работы backend codex должен быть установлен и доступен в PATH.")
    if backend == "claude" and provider == LMSTUDIO_PROVIDER:
        notes.append("Перед запуском убедись, что LM Studio уже поднят и доступен по указанному base URL.")
    if backend == "claude" and provider == DEFAULT_PROVIDER:
        notes.append("Глобальный ~/.claude/settings.json уже настроен для Claude-профилей.")
    if not port_probe_ok:
        notes.append("Не удалось проверить, свободен ли порт 8180. Если он занят, укажи другой через --port.")
    elif not default_port_free:
        notes.append(
            f"Порт 8180 занят, поэтому для запуска в примере выбран свободный порт {selected_port}."
        )

    return {
        "primary_profile": primary_profile,
        "selected_port": selected_port,
        "port_probe_ok": port_probe_ok,
        "doctor_command": doctor_command,
        "run_command": run_command,
        "chat_tui_command": chat_tui_command,
        "chat_command": chat_command,
        "notes": notes,
    }


def _build_init_human_summary(
    *,
    data_root: Path,
    backend: str,
    provider: str,
    model: str,
    reasoning_effort: str,
    dangerous_mode: bool,
    profile_names: list[str],
    written_profiles: list[str],
    skipped_profiles: list[str],
    foundation: dict[str, str],
    next_steps: dict[str, Any],
    telegram_setup: dict[str, Any] | None = None,
) -> str:
    profile_text = ", ".join(profile_names) if profile_names else "-"
    memory_path = foundation.get("memory_index") or str(data_root / "MEMORY.md")
    bootstrap_path = foundation.get("bootstrap") or str(data_root / "BOOTSTRAP.md")
    note_lines = [f"- {line}" for line in next_steps.get("notes", []) if str(line).strip()]
    if written_profiles:
        note_lines.append(f"- Обновлены профили: {', '.join(written_profiles)}.")
    if skipped_profiles:
        note_lines.append(
            f"- Без изменений оставлены профили: {', '.join(skipped_profiles)}. "
            "Если нужно принудительно переписать их без вопросов, используй --force."
        )
    notes_block = "\n".join(note_lines) if note_lines else "- Дополнительных условий для старта не обнаружено."
    telegram_block = ""
    if telegram_setup:
        telegram_block = (
            f"- Telegram env: {telegram_setup.get('env_file')}\n"
            f"- Telegram profile: {telegram_setup.get('profile')}\n"
            f"- Telegram auto-approve: {'on' if telegram_setup.get('auto_approve') else 'off'}"
            f" ({telegram_setup.get('auto_approve_scope')})\n"
        )
    reasoning_line = ""
    if backend == "codex":
        reasoning_label = "system default" if reasoning_effort == DEFAULT_CODEX_REASONING_EFFORT else reasoning_effort
        reasoning_line = f"- Reasoning effort: {reasoning_label}\n"
    dangerous_line = ""
    if backend == "codex":
        dangerous_line = f"- Execution mode: {'super dangerous' if dangerous_mode else 'safe'}\n"

    return (
        "Инициализация завершена. ✅\n"
        f"- Backend: {backend}\n"
        f"- Provider: {provider}\n"
        f"- Model: {model}\n"
        f"{reasoning_line}"
        f"{dangerous_line}"
        f"- Profiles: {profile_text}\n"
        f"- Runtime data dir: {data_root}\n"
        f"- Memory index: {memory_path}\n"
        f"- Bootstrap file: {bootstrap_path}\n"
        f"{telegram_block}"
        "\n"
        "Что делать дальше:\n"
        f"1. Проверить окружение: {next_steps['doctor_command']}\n"
        f"2. Запустить runtime: {next_steps['run_command']}\n"
        f"3. В другом терминале открыть TUI-чат: {next_steps['chat_tui_command']}\n"
        f"4. Если нужен обычный чат без TUI: {next_steps['chat_command']}\n"
        "\n"
        "Примечания:\n"
        f"{notes_block}"
    )


def init_cli_environment(
    data_dir: str | None = None,
    provider: str | None = None,
    api_key: str | None = None,
    model: str | None = None,
    reasoning_effort: str | None = None,
    dangerous_mode: bool | None = None,
    backend: str | None = None,
    base_url: str | None = None,
    profile_name: str | None = None,
    force: bool = False,
    interactive: bool = True,
    env_file: str | None = None,
    setup_telegram: bool | None = None,
    telegram_bot_token: str | None = None,
    telegram_allowed_user_ids: str | None = None,
    telegram_profile: str | None = None,
    telegram_auto_approve: bool | None = None,
    telegram_auto_approve_scope: str | None = None,
    enabled_toolsets: list[str] | None = None,
    tool_display: str | None = None,
) -> dict:
    backend, provider = _resolve_backend_selection(
        backend=backend,
        provider=provider,
        interactive=interactive,
    )
    preflight = _preflight_backend_or_raise(backend)
    profile_name = _resolve_profile_name(
        backend=backend,
        provider=provider,
        profile_name=profile_name,
        interactive=interactive,
    )

    data_root = Path(data_dir).expanduser() if data_dir else _default_data_root()
    profiles_dir = data_root / "profiles"
    workspace_dir = data_root / "workspace"
    db_path = data_root / "database" / "factory.db"

    settings_path: Path | None = None
    if backend == "claude":
        if provider == DEFAULT_PROVIDER:
            if interactive:
                model = _prompt("Модель z.ai 🤖", model or DEFAULT_CLAUDE_MODEL)
                api_key = api_key or _prompt("API key z.ai 🔑", secret=True)
            else:
                model = model or DEFAULT_CLAUDE_MODEL
            if not api_key:
                raise ValueError("API key обязателен для init backend=claude provider=z.ai")
            settings_path = _write_claude_settings(api_key=api_key, model=model, force=force)
        else:
            if interactive:
                model = _prompt("Модель LM Studio 🤖", model or DEFAULT_LMSTUDIO_MODEL)
                base_url = _prompt("Anthropic-compatible base URL 🌐", base_url or "http://127.0.0.1:1234")
                api_key = _prompt("Auth token для LM Studio 🔑", api_key or "lmstudio")
            else:
                model = model or DEFAULT_LMSTUDIO_MODEL
            if not base_url:
                raise ValueError("Для init backend=claude provider=lmstudio-anthropic обязателен --base-url")
    elif backend == "cc-backend":
        if interactive:
            model = _prompt(
                "Имя модели — GGUF файл, который загружен в llama-server "
                f"(например {DEFAULT_CC_BACKEND_MODEL}) 🤖",
                model or DEFAULT_CC_BACKEND_MODEL,
            )
            base_url = _prompt(
                "Адрес llama-server — куда Igorek шлёт запросы "
                "(обычно http://127.0.0.1:8001 если llama-server на этой машине) 🌐",
                base_url or DEFAULT_CC_BACKEND_BASE_URL,
            )
        else:
            model = model or DEFAULT_CC_BACKEND_MODEL
            base_url = base_url or DEFAULT_CC_BACKEND_BASE_URL
    else:
        if interactive:
            model = _prompt("Модель Codex 🤖", model or DEFAULT_CODEX_MODEL)
        else:
            model = model or DEFAULT_CODEX_MODEL
        reasoning_effort = _resolve_codex_reasoning_effort(
            model=str(model),
            reasoning_effort=reasoning_effort,
            interactive=interactive,
        )
        dangerous_mode = _resolve_codex_dangerous_mode(
            dangerous_mode=dangerous_mode,
            interactive=interactive,
        )

    # --- Component checklist loop: review → back to checklist on rejection ---
    resolved_toolsets: list[str] = []
    resolved_display: str = ""
    component_choices: dict[str, bool] = {}
    while True:
        component_choices = _resolve_component_selection(
            interactive=interactive,
            setup_telegram=setup_telegram,
            toolsets_provided=enabled_toolsets,
            display_provided=tool_display,
        )

        # --- Toolset selection (Feature 3) ---
        prev_toolsets = resolved_toolsets
        resolved_toolsets = []
        if component_choices.get("configure_toolsets"):
            resolved_toolsets = _resolve_toolsets(
                interactive=interactive,
                provided=enabled_toolsets,
                previous=prev_toolsets,
            )
        elif enabled_toolsets:
            resolved_toolsets = [t.strip() for t in enabled_toolsets if t.strip()]

        # --- Display style (Feature 4) ---
        resolved_display = ""
        if component_choices.get("configure_display"):
            resolved_display = _resolve_display_style(
                interactive=interactive,
                provided=tool_display,
            )
        elif tool_display:
            resolved_display = tool_display.strip().lower()

        # --- Review step (Feature 2) ---
        if not interactive:
            break
        confirmed = _review_and_confirm(
            backend=backend,
            provider=provider,
            model=model,
            reasoning_effort=reasoning_effort if backend == "codex" else None,
            dangerous_mode=bool(dangerous_mode),
            enabled_toolsets=resolved_toolsets,
            display_style=resolved_display,
            telegram_enabled=component_choices.get("setup_telegram", False),
            interactive=interactive,
        )
        if confirmed:
            break
        # Not confirmed — loop back to checklist
        print("Возвращаемся к настройке компонентов.\n")

    written_profiles, profile_names, skipped_profiles = _write_profiles(
        profiles_dir=profiles_dir,
        backend=backend,
        provider=provider,
        model=model,
        base_url=base_url,
        api_key=api_key,
        reasoning_effort=reasoning_effort,
        dangerous_mode=dangerous_mode,
        profile_name=profile_name,
        force=force,
        interactive=interactive,
        enabled_toolsets=resolved_toolsets or None,
        display_style=resolved_display or None,
    )
    initialize_database(str(db_path))
    workspace_dir.mkdir(parents=True, exist_ok=True)
    foundation = ensure_foundation_files(data_dir=str(data_root), force=force)
    mcp_registry = ensure_mcp_registry(str(data_root))

    # --- Telegram (guarded by component checklist) ---
    telegram_setup = None
    if component_choices.get("setup_telegram"):
        telegram_setup = _configure_telegram_env(
            data_root=data_root,
            profile_names=profile_names,
            interactive=interactive,
            force=force,
            env_file=env_file,
            setup_telegram=setup_telegram,
            telegram_bot_token=telegram_bot_token,
            telegram_allowed_user_ids=telegram_allowed_user_ids,
            telegram_profile=telegram_profile,
            telegram_auto_approve=telegram_auto_approve,
            telegram_auto_approve_scope=telegram_auto_approve_scope,
        )

    profiles_present = _profile_summary(profiles_dir)
    effective_model = str(model)
    effective_reasoning_effort = str(reasoning_effort or DEFAULT_CODEX_REASONING_EFFORT)
    effective_dangerous_mode = bool(dangerous_mode)
    primary_profile = profile_names[0] if profile_names else ""
    primary_meta = profiles_present.get(primary_profile) if primary_profile else None
    if isinstance(primary_meta, dict) and primary_meta.get("model"):
        effective_model = str(primary_meta["model"])
    if isinstance(primary_meta, dict) and primary_meta.get("reasoning_effort"):
        effective_reasoning_effort = str(primary_meta["reasoning_effort"])
    if isinstance(primary_meta, dict):
        effective_dangerous_mode = bool(primary_meta.get("dangerous_mode"))

    result = {
        "status": "ok",
        "data_root": str(data_root),
        "db_path": str(db_path),
        "workspace_dir": str(workspace_dir),
        "profiles_dir": str(profiles_dir),
        "profiles_written": [str(path) for path in written_profiles],
        "profiles_skipped": skipped_profiles,
        "profiles_present": profiles_present,
        "foundation": foundation,
        "mcp_registry": str(mcp_registry),
        "mcp_servers": list_mcp_servers(str(data_root)),
        "backend": backend,
        "provider": provider,
        "model": effective_model,
        "requested_model": model,
        "reasoning_effort": effective_reasoning_effort,
        "requested_reasoning_effort": reasoning_effort or DEFAULT_CODEX_REASONING_EFFORT,
        "dangerous_mode": effective_dangerous_mode,
        "requested_dangerous_mode": bool(dangerous_mode),
        "profile_names": profile_names,
        "preflight": preflight,
    }
    if resolved_toolsets:
        result["enabled_toolsets"] = resolved_toolsets
    if resolved_display:
        result["tool_display"] = resolved_display
    if telegram_setup is not None:
        result["telegram_setup"] = telegram_setup
    next_steps = _build_init_next_steps(
        data_root=data_root,
        backend=backend,
        provider=provider,
        profile_names=profile_names,
    )
    result["next_steps"] = next_steps
    result["human_summary"] = _build_init_human_summary(
        data_root=data_root,
        backend=backend,
        provider=provider,
        model=effective_model,
        reasoning_effort=effective_reasoning_effort,
        dangerous_mode=effective_dangerous_mode,
        profile_names=profile_names,
        written_profiles=[path.stem for path in written_profiles],
        skipped_profiles=skipped_profiles,
        foundation=foundation,
        next_steps=next_steps,
        telegram_setup=telegram_setup,
    )
    if settings_path is not None:
        result["claude_settings"] = str(settings_path)
    return result


def doctor_cli_environment(data_dir: str | None = None) -> dict:
    data_root = Path(data_dir).expanduser() if data_dir else _default_data_root()
    db_path = data_root / "database" / "factory.db"
    profiles_dir = Path(get_profiles_dir()) if data_dir is None else data_root / "profiles"
    settings_path = get_claude_settings_path()
    foundation_paths = get_foundation_paths(str(data_root))
    ensure_foundation_files(data_dir=str(data_root), force=False)

    claude_bin = resolve_claude_bin()
    codex_bin = resolve_codex_bin()
    profile_summary = _profile_summary(profiles_dir)
    mcp_registry_path = ensure_mcp_registry(str(data_root))
    mcp_servers = list_mcp_servers(str(data_root))
    used_backends = {meta.get("backend") for meta in profile_summary.values() if isinstance(meta, dict)}
    claude_profiles = [meta for meta in profile_summary.values() if isinstance(meta, dict) and meta.get("backend") == "claude"]
    claude_settings_required = any(meta.get("provider") == DEFAULT_PROVIDER for meta in claude_profiles)

    checks = {
        "runtime_layout": get_runtime_layout(),
        "data_root": str(data_root),
        "profiles_dir": str(profiles_dir),
        "data_root_exists": data_root.exists(),
        "db_exists": db_path.exists(),
        "profiles_dir_exists": profiles_dir.exists(),
        "zai_profile_exists": (profiles_dir / "zai.json").exists(),
        "minimax_profile_exists": (profiles_dir / "minimax.json").exists(),
        "codex_profile_exists": (profiles_dir / "codex.json").exists(),
        "claude_settings_exists": settings_path.exists(),
        "claude_settings_required": claude_settings_required,
        "bootstrap_exists": foundation_paths.bootstrap.exists(),
        "identity_exists": foundation_paths.identity.exists(),
        "user_exists": foundation_paths.user.exists(),
        "soul_exists": foundation_paths.soul.exists(),
        "memory_index_exists": foundation_paths.memory_index.exists(),
        "daily_memory_exists": foundation_paths.memory_dir.exists() and any(foundation_paths.memory_dir.glob("*.md")),
        "bootstrap_complete": bootstrap_complete(str(data_root)),
        "claude_bin": claude_bin,
        "claude_available": (Path(claude_bin).exists() if os.path.sep in claude_bin else bool(shutil.which(claude_bin))),
        "codex_bin": codex_bin,
        "codex_available": (Path(codex_bin).exists() if os.path.sep in codex_bin else bool(shutil.which(codex_bin))),
        "profiles": profile_summary,
        "mcp_registry": str(mcp_registry_path),
        "mcp_server_count": len(mcp_servers),
        "mcp_servers": sorted(mcp_servers.keys()),
        "db_tables": {},
        "db_columns": {},
    }

    if db_path.exists():
        with open_db(db_path) as conn:
            checks["db_tables"] = db_tables_check(conn)
            cursor = conn.cursor()
            cursor.execute("PRAGMA table_info(tasks)")
            columns = {row[1] for row in cursor.fetchall()}
            checks["db_columns"] = {
                "tasks_required_columns": sorted(name for name, _ in TASK_COLUMNS if name in columns),
                "events_required_columns": [name for name, _ in EVENT_COLUMNS],
            }

    backend_ok = True
    if "claude" in used_backends:
        backend_ok = backend_ok and checks["claude_available"] and (not checks["claude_settings_required"] or checks["claude_settings_exists"])
    if "codex" in used_backends:
        backend_ok = backend_ok and checks["codex_available"]

    checks["status"] = "ok" if all([
        checks["db_exists"],
        bool(profile_summary),
        backend_ok,
    ]) else "error"
    return checks
