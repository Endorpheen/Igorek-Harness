#!/usr/bin/env python3
"""Unified CLI entrypoint for Agent Factory."""

from __future__ import annotations

import argparse
import importlib
import json
import os
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

# Dispatch read-only probes before legacy imports initialize the database.
if sys.argv[1:2] == ["doctor"] and "--read-only" in sys.argv[2:]:
    from core.readiness import check_host_readiness, format_readiness

    probe_parser = argparse.ArgumentParser(prog="igorek-factory doctor")
    probe_parser.add_argument("--read-only", action="store_true")
    probe_parser.add_argument("--data-dir")
    probe_parser.add_argument("--json", action="store_true")
    probe_args = probe_parser.parse_args(sys.argv[2:])
    probe_report = check_host_readiness(probe_args.data_dir)
    print(json.dumps(probe_report, ensure_ascii=False, indent=2) if probe_args.json else format_readiness(probe_report))
    sys.exit(1 if probe_report["status"] == "needs_action" else 0)

from core.cli_bootstrap import doctor_cli_environment, init_cli_environment
from core.readiness import check_host_readiness, format_readiness
from core.cli_boltbook import heartbeat_check as boltbook_heartbeat_check
from core.cli_boltbook import heartbeat_tick as boltbook_heartbeat_tick
from core.cli_boltbook import drafts as boltbook_drafts
from core.cli_boltbook import generate_drafts as boltbook_generate_drafts
from core.cli_boltbook import generate_post as boltbook_generate_post
from core.cli_boltbook import inbox as boltbook_inbox
from core.cli_boltbook import feed as boltbook_feed
from core.cli_boltbook import me as boltbook_me
from core.cli_boltbook import comments as boltbook_comments
from core.cli_boltbook import next_draft as boltbook_next_draft
from core.cli_boltbook import next_reaction as boltbook_next_reaction
from core.cli_boltbook import posts as boltbook_posts
from core.cli_boltbook import print_json as boltbook_print_json
from core.cli_boltbook import reactions as boltbook_reactions
from core.cli_boltbook import review_inbox as boltbook_review_inbox
from core.cli_boltbook import approve as boltbook_approve_draft
from core.cli_boltbook import approve_post as boltbook_approve_post
from core.cli_boltbook import actions as boltbook_actions
from core.cli_boltbook import post_analysis as boltbook_post_analysis
from core.cli_boltbook import post_drafts as boltbook_post_drafts
from core.cli_boltbook import persona_list as boltbook_persona_list
from core.cli_boltbook import persona_set as boltbook_persona_set
from core.cli_boltbook import persona_show as boltbook_persona_show
from core.cli_boltbook import search as boltbook_search
from core.cli_boltbook import send_post as boltbook_send_post
from core.cli_boltbook import send as boltbook_send_draft
from core.cli_boltbook import show_draft as boltbook_show_draft
from core.cli_boltbook import show_post_draft as boltbook_show_post_draft
from core.cli_boltbook import status as boltbook_status
from core.cli_boltbook import subscribe_submolt as boltbook_subscribe_submolt
from core.cli_boltbook import submolts as boltbook_submolts
from core.cli_boltbook import update_draft as boltbook_update_draft
from core.cli_boltbook import update_post_draft as boltbook_update_post_draft
from core.cli_chat import generate_session_name
from core.cli_skills import (
    activate_local_skills,
    init_skill,
    list_skills,
    set_skill_enabled,
    skills_usage_summary,
)
from core.cli_run import run_gateway_stack, run_local_stack
from core.telegram_bridge import run_telegram_bridge
from core.schema import initialize_database, open_db
from core.cli_tasks import ask_task, create_task, get_task, list_tasks, wait_task
from core.vault import cli_support as vault_cli
from core.mcp_config import (
    add_http_server,
    add_stdio_server,
    disable_mcp_server_for_profile,
    enable_mcp_server_for_profile,
    get_mcp_registry_path,
    get_profile_mcp_servers,
    list_mcp_servers,
    remove_mcp_server,
    sync_codex_mcp_for_profile,
)
from core.openrouter_model_selector import update_openrouter_profile_model
from core.runtime import DEFAULT_CLI_HOME, get_db_path


RUN_EXAMPLES = """Примеры:
  igorek-factory run --profile codex
  igorek-factory run --profile codex --port 8181
  igorek-factory run --profile codex-alt --data-dir ~/.igorek-agent-factory

Для расширенных сценариев смотри:
  igorek-factory launch-help
"""


RUNTIME_ENV_HELP = """
Runtime env-переменные

1. Пути и layout
   FACTORY_DATA_DIR                 Корневой data dir runtime.
   IGOREK_FACTORY_HOME              Алиас для data dir.
   FACTORY_PROFILES_DIR             Явный путь до папки профилей.
   FACTORY_RUNTIME_LAYOUT           Принудительный layout runtime.

2. Бинарники backend'ов
   FACTORY_CODEX_BIN                Путь до codex CLI.
   FACTORY_CLAUDE_BIN               Путь до claude CLI.

3. API и логирование
   FACTORY_API_KEY                  API key для HTTP API.
   FACTORY_CORS_ORIGINS             CORS origins через запятую.
   FACTORY_LOG_FORMAT               Формат логов.
   FACTORY_LOG_LEVEL                Уровень логов.

4. Watchdog
   FACTORY_WATCHDOG_INTERVAL_S      Интервал watchdog.
   FACTORY_STUCK_TIMEOUT_S          Порог зависшей задачи.

5. Boltbook heartbeat
   FACTORY_BOLTBOOK_HEARTBEAT_ENABLED
   FACTORY_BOLTBOOK_HEARTBEAT_LOOP_INTERVAL_S
   FACTORY_BOLTBOOK_HEARTBEAT_INTERVAL_S
   FACTORY_BOLTBOOK_SKILL_REFRESH_INTERVAL_S
   FACTORY_BOLTBOOK_HEARTBEAT_FEED_LIMIT
   FACTORY_BOLTBOOK_HEARTBEAT_FEED_SORT

6. Boltbook autonomous comments
   FACTORY_BOLTBOOK_AUTONOMOUS_COMMENTS_ENABLED
   FACTORY_BOLTBOOK_AUTONOMOUS_PROFILE
   FACTORY_BOLTBOOK_AUTONOMOUS_MIN_SCORE
   FACTORY_BOLTBOOK_AUTONOMOUS_TIMEOUT_S
   FACTORY_BOLTBOOK_AUTONOMOUS_COMMENT_COOLDOWN_S
   FACTORY_BOLTBOOK_AUTONOMOUS_MAX_COMMENTS_PER_HOUR
   FACTORY_BOLTBOOK_AUTONOMOUS_MAX_COMMENTS_PER_DAY
   FACTORY_BOLTBOOK_AUTONOMOUS_OWN_POST_MAX_COMMENTS_PER_HOUR

7. Boltbook autonomous posts
   FACTORY_BOLTBOOK_AUTONOMOUS_POSTS_ENABLED
   FACTORY_BOLTBOOK_AUTONOMOUS_POST_PROFILE
   FACTORY_BOLTBOOK_AUTONOMOUS_POST_SUBMOLT
   FACTORY_BOLTBOOK_AUTONOMOUS_POST_TIMEOUT_S
   FACTORY_BOLTBOOK_AUTONOMOUS_POST_COOLDOWN_S
    FACTORY_BOLTBOOK_AUTONOMOUS_POST_WINDOW_S
    FACTORY_BOLTBOOK_AUTONOMOUS_POST_MAX_PER_WINDOW
    FACTORY_BOLTBOOK_AUTONOMOUS_POLICY

8. Launcher script overrides
   FACTORY_PORT                     Порт для scripts/run_mini_pc_codex_boltbook.sh

8.5 Gateway preset
   `config/gateway.default.env`    Встроенный preset для `igorek-factory gateway`.
   `.env.gateway`                  Локальный override-файл поверх gateway preset.
   `igorek-factory gateway`        Стандартный запуск runtime + worker + telegram + boltbook.
   FACTORY_WORKER_PROFILES         Дополнительные профили для воркеров через запятую,
                                   например `cc-wally,cc-glm`. Активный профиль рантайма
                                   обслуживается всегда; воркер берёт задачи только своего
                                   профиля, поэтому список включает параллельные очереди.
                                   Значения off/0/no/false/disabled/none выключают список.
                                   Неизвестный профиль пропускается с сообщением в лог.

8.6 Session embeddings runtime
   FACTORY_SESSION_EMBEDDINGS_ENABLED        Режим автозапуска embeddings runtime: auto|true|false.
   FACTORY_SESSION_EMBEDDINGS_MODEL_PATH     Путь до локальной GGUF embeddings-модели.
   FACTORY_SESSION_EMBEDDINGS_MODEL          Логическое имя модели для `/v1/embeddings`.
   FACTORY_SESSION_EMBEDDINGS_HOST           Host embeddings runtime.
   FACTORY_SESSION_EMBEDDINGS_PORT           Port embeddings runtime.
   FACTORY_SESSION_EMBEDDINGS_LLAMASERVER_BIN Путь до llama-server.
   FACTORY_SESSION_EMBEDDINGS_CTX_SIZE       Context size embeddings runtime.
   FACTORY_SESSION_EMBEDDINGS_GPU_LAYERS     GPU layers для embeddings runtime.
   FACTORY_SESSION_EMBEDDINGS_EXTRA_ARGS     Дополнительные args для llama-server embeddings runtime.
   IGOREK_SESSION_EMBEDDINGS_BASE_URL        Base URL локального embeddings endpoint для SessionSearch.
   IGOREK_SESSION_EMBEDDINGS_MODEL           Имя embedding-модели для SessionSearch semantic retrieval.

8.7 Igorek Vault
   FACTORY_VAULT_ENABLED            Включить brokered env и отключить ранний load локальных plaintext env для cc-backend.
   FACTORY_VAULT_SERVICES           Сервисы через запятую: tavily,fal,xsearch,telegram,openrouter...
   FACTORY_VAULT_AGENT              Policy agent id, обычно cc-backend для локальной модели.
   FACTORY_VAULT_TTL_SECONDS        TTL brokered env.
   IGOREK_VAULT_HOME                Runtime home vault, по умолчанию data-dir/vault.
   IGOREK_VAULT_PASSPHRASE          Passphrase для локального encrypted vault.
   IGOREK_VAULT_PASSPHRASE_FILE     Owner-only файл с passphrase.

9. Telegram bridge
   FACTORY_TELEGRAM_BOT_TOKEN       Telegram bot token.
   FACTORY_TELEGRAM_ENABLED         Автозапуск Telegram bridge внутри `igorek-factory run`.
   FACTORY_TELEGRAM_ALLOWED_USER_IDS Белый список Telegram user_id через запятую.
   FACTORY_TELEGRAM_ALLOWED_CHAT_IDS Необязательный белый список chat_id через запятую.
   FACTORY_TELEGRAM_PROFILE         Профиль для Telegram bridge.
   FACTORY_TELEGRAM_WORKDIR         Рабочая папка app-server session.
   FACTORY_TELEGRAM_POLL_TIMEOUT_S  Timeout long-poll getUpdates.
   FACTORY_TELEGRAM_AUTO_APPROVE    Авто-approve app-server approvals в Telegram.
   FACTORY_TELEGRAM_AUTO_APPROVE_SCOPE Scope авто-approve: turn или session.
   FACTORY_TELEGRAM_RICH_MESSAGES  Включить отправку Bot API Rich Messages.

Примеры:
  FACTORY_DATA_DIR=~/.igorek-agent-factory igorek-factory run --profile codex --port 8181
  FACTORY_CODEX_BIN=/usr/local/bin/codex igorek-factory init --backend codex
  FACTORY_BOLTBOOK_AUTONOMOUS_POLICY=conservative ./scripts/run_mini_pc_codex_boltbook.sh
  FACTORY_TELEGRAM_BOT_TOKEN=123:abc FACTORY_TELEGRAM_ALLOWED_USER_IDS=111111 igorek-factory telegram --profile codex
"""


def _run_module_main(module_name: str, argv: list[str]) -> int:
    module = importlib.import_module(module_name)
    main_fn = getattr(module, "main", None)
    if not callable(main_fn):
        raise RuntimeError(f"Module {module_name} does not expose callable main()")

    old_argv = sys.argv
    try:
        sys.argv = [module_name, *argv]
        result = main_fn()
        return int(result) if isinstance(result, int) else 0
    finally:
        sys.argv = old_argv


def _print_json(payload: dict) -> None:
    print(json.dumps(payload, indent=2, ensure_ascii=False))


def _print_init_followup(payload: dict) -> None:
    summary = str(payload.get("human_summary") or "").strip()
    if not summary:
        return
    print()
    print(summary)


def _print_doctor_followup(payload: dict) -> None:
    status = str(payload.get("status") or "unknown").strip().lower()
    ok = status == "ok"
    runtime_layout = str(payload.get("runtime_layout") or "-")
    data_root = str(payload.get("data_root") or "-")
    profiles_dir = str(payload.get("profiles_dir") or "-")
    profiles = payload.get("profiles") or {}
    profile_items: list[str] = []
    if isinstance(profiles, dict) and profiles:
        for name in sorted(profiles.keys()):
            meta = profiles.get(name) or {}
            backend = str(meta.get("backend") or "?") if isinstance(meta, dict) else "?"
            profile_items.append(f"{name} [{backend}]")
    profile_names = ", ".join(profile_items) if profile_items else "-"
    mcp_servers = payload.get("mcp_servers") or []
    mcp_text = ", ".join(str(name) for name in mcp_servers) if mcp_servers else "нет"

    issues: list[str] = []
    if not payload.get("db_exists"):
        issues.append("не найдена база данных")
    if not payload.get("profiles_dir_exists"):
        issues.append("не найдена папка профилей")
    if not profiles:
        issues.append("не найдены профили")
    if payload.get("claude_settings_required") and not payload.get(
        "claude_settings_exists"
    ):
        issues.append("не найден ~/.claude/settings.json для Claude-профилей")
    used_backends = {
        str(meta.get("backend") or "").strip()
        for meta in (profiles.values() if isinstance(profiles, dict) else [])
        if isinstance(meta, dict)
    }
    if "claude" in used_backends and not payload.get("claude_available"):
        issues.append("CLI claude недоступен")
    if "codex" in used_backends and not payload.get("codex_available"):
        issues.append("CLI codex недоступен")

    header = (
        "Проверка окружения пройдена. ✅"
        if ok
        else "Проверка окружения выявила проблемы. ⚠️"
    )
    print()
    print(header)
    print(f"- Runtime layout: {runtime_layout}")
    print(f"- Data dir: {data_root}")
    print(f"- Profiles dir: {profiles_dir}")
    print("- Backend'ов поддерживается 2: codex и claude.")
    print(
        "- Профиль — это сохранённая конфигурация, поэтому профилей может быть несколько."
    )
    print(f"- Профили: {profile_names}")
    print(
        f"- Backends: codex={'ok' if payload.get('codex_available') else 'missing'}, "
        f"claude={'ok' if payload.get('claude_available') else 'missing'}"
    )
    print(f"- MCP servers: {mcp_text}")
    if issues:
        print("- Проблемы: " + "; ".join(issues))
    else:
        print("- Критичных проблем не обнаружено.")


def _print_launch_help() -> None:
    script_path = str(
        Path(__file__).resolve().parent / "scripts" / "run_mini_pc_codex_boltbook.sh"
    )
    print(
        "\n"
        "Сценарии запуска runtime\n"
        "\n"
        "1. Базовый запуск\n"
        "   igorek-factory run --profile codex\n"
        "\n"
        "2. Запуск на другом порту\n"
        "   igorek-factory run --profile codex --port 8181\n"
        "\n"
        "3. Запуск с конкретным профилем\n"
        "   igorek-factory run --profile codex-alt\n"
        "\n"
        "4. Запуск с другим data dir\n"
        "   igorek-factory run --profile codex --data-dir ~/.igorek-agent-factory\n"
        "\n"
        "5. Advanced запуск через env\n"
        "   FACTORY_DATA_DIR=~/.igorek-agent-factory \\\n"
        "   FACTORY_BOLTBOOK_AUTONOMOUS_POLICY=conservative \\\n"
        "   python3 factory_api.py --port 8181\n"
        "\n"
        "   Полный список runtime env-переменных:\n"
        "   igorek-factory env-help\n"
        "\n"
        "6. Готовый mini-PC launcher script из checkout репозитория\n"
        f"   {script_path}\n"
        "\n"
        "   Пример:\n"
        "   FACTORY_BOLTBOOK_AUTONOMOUS_POLICY=conservative ./scripts/run_mini_pc_codex_boltbook.sh\n"
        "\n"
        "Когда что использовать:\n"
        "- `igorek-factory run ...` — обычный локальный запуск API + worker.\n"
        "- env + `factory_api.py` — когда нужен полный контроль над runtime-переменными.\n"
        "- `run_mini_pc_codex_boltbook.sh` — готовый сценарий для mini-PC и Boltbook autonomous mode, если ты работаешь из checkout репозитория.\n"
        "\n"
        "Подсказки:\n"
        "- Этот script существует только в checkout репозитория, а не как отдельная установленная системная команда.\n"
        "- Для первого запуска используй путь из `igorek-factory init`.\n"
        "- Для проверки окружения используй `igorek-factory doctor`.\n"
        "- Для чата после запуска runtime открывайте `igorek-factory chat --profile <profile>`.\n"
    )


def _print_runtime_env_help() -> None:
    print(RUNTIME_ENV_HELP.strip())


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="igorek-factory",
        description="Agent Factory CLI wrapper (api/worker/init/task/run/ask/chat + bootstrap/memory foundation)",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("api", help="Run Factory API server")
    boltbook_parser = subparsers.add_parser(
        "boltbook", help="Read-only Boltbook connector"
    )
    web_parser = subparsers.add_parser("web", help="Run the local HTTPS agent web interface")
    web_parser.add_argument("--data-dir", help="CLI data directory (default ~/.igorek-agent-factory)")
    web_parser.add_argument("--port", type=int, default=5177, help="HTTPS bind port")
    web_parser.add_argument("--bridge-url", default="http://127.0.0.1:47821", help="Loopback mobile bridge URL")
    web_parser.add_argument("--tls-cert", default="", help="TLS certificate (PEM)")
    web_parser.add_argument("--tls-key", default="", help="TLS private key (PEM)")
    subparsers.add_parser("worker", help="Run worker process")
    subparsers.add_parser(
        "guest-runner",
        help="Run the in-guest cc-backend runner (inside a session VM)",
    )
    subparsers.add_parser(
        "guest-terminal",
        help="Run the in-guest interactive terminal service (inside a session VM)",
    )
    subparsers.add_parser("launch-help", help="Show practical runtime launch scenarios")
    subparsers.add_parser(
        "env-help", help="Show runtime env variables and launch overrides"
    )

    auth_parser = subparsers.add_parser("auth", help="Manage provider authentication")
    auth_subparsers = auth_parser.add_subparsers(dest="auth_command", required=True)
    auth_login = auth_subparsers.add_parser("login", help="Store API key for a provider")
    auth_login.add_argument("provider", help="Provider name (e.g. opencode)")
    auth_login.add_argument("--key", help="API key (prompts if not provided)")

    init_parser = subparsers.add_parser(
        "init", help="Bootstrap CLI environment for local/Termux use"
    )
    init_parser.add_argument("--backend", help="Backend runtime (cc-backend, claude or codex)")
    init_parser.add_argument(
        "--provider",
        help="Provider (claude: z.ai or lmstudio-anthropic; codex: openai-codex)",
    )
    init_parser.add_argument("--api-key", help="Provider API key or auth token")
    init_parser.add_argument(
        "--base-url", help="Custom API base URL (used for provider=lmstudio-anthropic)"
    )
    init_parser.add_argument("--model", help="Backend model override")
    init_parser.add_argument(
        "--reasoning-effort",
        help="Codex reasoning effort: system, none, low, medium, high, xhigh",
    )
    init_parser.add_argument(
        "--dangerous-mode",
        action="store_true",
        help="Enable super dangerous Codex execution mode",
    )
    init_parser.add_argument(
        "--profile-name",
        help="Write backend config into this profile name instead of defaults",
    )
    init_parser.add_argument(
        "--data-dir", help="CLI data directory (default ~/.igorek-agent-factory)"
    )
    init_parser.add_argument(
        "--env-file",
        help="Env file for optional Telegram/runtime variables (default: existing .env/.env.local)",
    )
    init_parser.add_argument(
        "--force", action="store_true", help="Overwrite local generated config files"
    )
    init_parser.add_argument(
        "--json",
        action="store_true",
        help="Print raw JSON result even in interactive mode",
    )
    init_parser.add_argument(
        "--non-interactive",
        action="store_true",
        help="Disable prompts and require all needed args",
    )
    init_parser.add_argument(
        "--setup-telegram",
        action="store_true",
        default=None,
        help="Also configure Telegram bridge env during init",
    )
    init_parser.add_argument(
        "--telegram-bot-token", help="Telegram bot token for optional Telegram setup"
    )
    init_parser.add_argument(
        "--telegram-allowed-user-ids",
        help="Comma-separated Telegram user_id whitelist for optional Telegram setup",
    )
    init_parser.add_argument(
        "--telegram-profile", help="Profile used by Telegram bridge"
    )
    init_parser.add_argument(
        "--telegram-auto-approve",
        action="store_true",
        default=None,
        help="Enable Telegram bridge auto-approve",
    )
    init_parser.add_argument(
        "--telegram-auto-approve-scope",
        choices=["turn", "session"],
        help="Telegram auto-approve scope",
    )
    init_parser.add_argument(
        "--enabled-toolsets",
        help="Comma-separated toolsets: file,terminal,search,web,session (default: all)",
    )
    init_parser.add_argument(
        "--tool-display",
        choices=["verbose", "compact", "minimal"],
        help="TUI display style (default: compact)",
    )

    doctor_parser = subparsers.add_parser("doctor", help="Validate CLI environment")
    doctor_parser.add_argument("--read-only", action="store_true", help="Check host without creating or changing files")
    doctor_parser.add_argument(
        "--data-dir", help="CLI data directory (default ~/.igorek-agent-factory)"
    )
    doctor_parser.add_argument(
        "--json",
        action="store_true",
        help="Print raw JSON result even in interactive mode",
    )

    openrouter_parser = subparsers.add_parser(
        "openrouter", help="Manage OpenRouter runtime helpers"
    )
    openrouter_subparsers = openrouter_parser.add_subparsers(
        dest="openrouter_command", required=True
    )
    openrouter_update = openrouter_subparsers.add_parser(
        "update-free-model",
        help="Select a recent free OpenRouter model and update a profile",
    )
    openrouter_update.add_argument("--profile", default="cc-openrouter")
    openrouter_update.add_argument("--data-dir", help="CLI data directory override")
    openrouter_update.add_argument(
        "--profiles-dir", help="Explicit profiles directory override"
    )
    openrouter_update.add_argument(
        "--min-context",
        type=int,
        default=65536,
        help="Minimum context length for candidates",
    )
    openrouter_update.add_argument(
        "--max-age-days",
        type=int,
        default=31,
        help="Reject models older than this many days; use 0 to disable",
    )
    openrouter_update.add_argument(
        "--allow-no-tools",
        action="store_true",
        help="Allow models without OpenAI tools support",
    )
    openrouter_update.add_argument(
        "--exclude-model",
        action="append",
        default=[],
        help="Model id to skip during selection; repeatable",
    )
    openrouter_update.add_argument(
        "--cached",
        action="store_true",
        help="Use cached OpenRouter models if available",
    )
    openrouter_update.add_argument(
        "--dry-run",
        action="store_true",
        help="Print selection without editing the profile",
    )
    openrouter_update.add_argument(
        "--limit", type=int, default=10, help="Number of ranked candidates to show"
    )

    wally_parser = subparsers.add_parser(
        "wally", help="Inspect the RunAnywhere (Wally) cloud account and models"
    )
    wally_subparsers = wally_parser.add_subparsers(
        dest="wally_command", required=True
    )
    wally_status = wally_subparsers.add_parser(
        "status", help="Show Wally account, plan and credit balance"
    )
    wally_status.add_argument(
        "--days", type=int, default=7, help="Usage window in days (default 7)"
    )
    wally_status.add_argument(
        "--json", action="store_true", help="Print raw JSON"
    )
    wally_models = wally_subparsers.add_parser(
        "models", help="List models served by the Wally cloud gateway"
    )
    wally_models.add_argument(
        "--catalog",
        action="store_true",
        help="Show the pricing catalog instead of plain model ids",
    )
    wally_models.add_argument(
        "--json", action="store_true", help="Print raw JSON"
    )

    run_parser = subparsers.add_parser(
        "run",
        help="Run API + worker together for local CLI use",
        epilog=RUN_EXAMPLES,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    run_parser.add_argument(
        "--data-dir", help="CLI data directory (default ~/.igorek-agent-factory)"
    )
    run_parser.add_argument("--host", default="127.0.0.1", help="API bind host")
    run_parser.add_argument("--port", type=int, default=8180, help="API bind port")
    run_parser.add_argument("--profile", default="zai", help="Worker profile")
    run_parser.add_argument(
        "--poll-interval", type=int, default=2, help="Worker poll interval"
    )

    gateway_parser = subparsers.add_parser(
        "gateway",
        help="Run the default gateway preset: runtime + worker + telegram + boltbook",
    )
    gateway_parser.add_argument(
        "--data-dir", help="CLI data directory (default ~/.igorek-agent-factory)"
    )
    gateway_parser.add_argument("--host", default="127.0.0.1", help="API bind host")
    gateway_parser.add_argument("--port", type=int, default=8181, help="API bind port")
    gateway_parser.add_argument(
        "--profile", default="codex", help="Default gateway profile"
    )
    gateway_parser.add_argument(
        "--poll-interval", type=int, default=2, help="Worker poll interval"
    )
    gateway_parser.add_argument(
        "--env-file",
        help="Gateway env file override (default merge order: .env, then .env.gateway, then .env.local)",
    )

    mobile_parser = subparsers.add_parser(
        "mobile",
        help="Remote-control bridge for the Android client (serve/pair/devices/revoke)",
    )
    mobile_parser.add_argument(
        "--data-dir", help="CLI data directory (default ~/.igorek-agent-factory)"
    )
    mobile_subparsers = mobile_parser.add_subparsers(
        dest="mobile_command", required=True
    )

    mobile_serve = mobile_subparsers.add_parser(
        "serve", help="Run the mobile bridge HTTP/SSE server"
    )
    mobile_serve.add_argument(
        "--host",
        default="127.0.0.1",
        help="Bind host (127.0.0.1 = local only; use the address the phone reaches)",
    )
    mobile_serve.add_argument("--port", type=int, default=47821, help="Bind port")
    mobile_serve.add_argument(
        "--profile",
        default="",
        help="Default profile for sessions that have none",
    )
    mobile_serve.add_argument(
        "--workdir", default="", help="Default working directory for new tasks"
    )
    mobile_serve.add_argument(
        "--tls-cert", default="", help="TLS certificate (PEM) to serve HTTPS"
    )
    mobile_serve.add_argument("--tls-key", default="", help="TLS private key (PEM)")
    mobile_serve.add_argument(
        "--poll-interval",
        type=float,
        default=1.5,
        help="Trace polling interval in seconds",
    )

    mobile_pair = mobile_subparsers.add_parser(
        "pair", help="Issue a one-time pairing code for a new phone"
    )
    mobile_pair.add_argument(
        "--ttl", type=int, default=300, help="Pairing code lifetime in seconds"
    )

    mobile_subparsers.add_parser("devices", help="List paired devices")

    mobile_revoke = mobile_subparsers.add_parser(
        "revoke", help="Revoke a device by id"
    )
    mobile_revoke.add_argument("device_id", help="Device id from `mobile devices`")

    telegram_parser = subparsers.add_parser(
        "telegram", help="Run Telegram bridge on top of runtime app-server API"
    )
    telegram_parser.add_argument(
        "--data-dir", help="CLI data directory (default ~/.igorek-agent-factory)"
    )
    telegram_parser.add_argument(
        "--profile",
        default=os.environ.get("FACTORY_TELEGRAM_PROFILE", "codex"),
        help="Target codex profile",
    )
    telegram_parser.add_argument(
        "--token",
        default=os.environ.get("FACTORY_TELEGRAM_BOT_TOKEN", ""),
        help="Telegram bot token",
    )
    telegram_parser.add_argument(
        "--runtime-api-base-url",
        default=os.environ.get("FACTORY_API_BASE_URL", "http://127.0.0.1:8181"),
        help="Runtime API base URL",
    )
    telegram_parser.add_argument(
        "--runtime-api-key",
        default=os.environ.get("FACTORY_API_KEY", ""),
        help="Runtime API key for app-server endpoints",
    )
    telegram_parser.add_argument(
        "--allowed-user-ids",
        default=os.environ.get("FACTORY_TELEGRAM_ALLOWED_USER_IDS", ""),
        help="Comma-separated Telegram user_id whitelist",
    )
    telegram_parser.add_argument(
        "--allowed-chat-ids",
        default=os.environ.get("FACTORY_TELEGRAM_ALLOWED_CHAT_IDS", ""),
        help="Optional comma-separated chat_id whitelist",
    )
    telegram_parser.add_argument(
        "--workdir",
        default=os.environ.get("FACTORY_TELEGRAM_WORKDIR", ""),
        help="Working directory for Codex app-server sessions",
    )
    telegram_parser.add_argument(
        "--poll-timeout",
        type=int,
        default=int(os.environ.get("FACTORY_TELEGRAM_POLL_TIMEOUT_S", "30")),
        help="Telegram getUpdates long-poll timeout in seconds",
    )
    telegram_parser.add_argument(
        "--auto-approve",
        action="store_true",
        default=os.environ.get("FACTORY_TELEGRAM_AUTO_APPROVE", "").strip().lower()
        in {"1", "true", "yes", "on"},
        help="Automatically approve app-server requests for Telegram chats",
    )
    telegram_parser.add_argument(
        "--auto-approve-scope",
        default=os.environ.get("FACTORY_TELEGRAM_AUTO_APPROVE_SCOPE", "session"),
        help="Auto-approve scope: turn or session",
    )

    ask_parser = subparsers.add_parser(
        "ask", help="Create task, wait, and print final result"
    )
    ask_parser.add_argument("text", help="Task text")
    ask_parser.add_argument(
        "--data-dir", help="CLI data directory (default ~/.igorek-agent-factory)"
    )
    ask_parser.add_argument("--profile", default="zai", help="Target profile")
    ask_parser.add_argument(
        "--json",
        action="store_true",
        help="Print full task object instead of plain answer",
    )
    ask_parser.add_argument(
        "--timeout", type=int, default=1200, help="Wait timeout in seconds"
    )
    ask_parser.add_argument(
        "--interval", type=float, default=2.0, help="Poll interval in seconds"
    )

    chat_parser = subparsers.add_parser("chat", help="Interactive local chat loop")
    chat_parser.add_argument(
        "--data-dir", help="CLI data directory (default ~/.igorek-agent-factory)"
    )
    chat_parser.add_argument("--profile", default="zai", help="Target profile")
    chat_parser.add_argument(
        "--session", help="Start a new session with explicit id/name"
    )
    chat_parser.add_argument("--resume", nargs="?", const="__picker__", default=None,
                            help="Resume an existing session by id, or show picker if no id given")
    chat_parser.add_argument("--fork", nargs="?", const="__picker__", default=None,
                            help="Fork from an existing session, or show picker if no id given")
    chat_parser.add_argument(
        "--timeout", type=int, default=1200, help="Wait timeout per message in seconds"
    )
    chat_parser.add_argument(
        "--interval", type=float, default=2.0, help="Poll interval in seconds"
    )
    chat_parser.add_argument(
        "--debug", action="store_true", help="Print session/task debug info"
    )

    mcp_parser = subparsers.add_parser(
        "mcp", help="Manage MCP server registry and profile bindings"
    )
    mcp_parser.add_argument("--data-dir", help="CLI data directory override")
    mcp_subparsers = mcp_parser.add_subparsers(dest="mcp_command", required=True)

    mcp_list = mcp_subparsers.add_parser("list", help="List MCP servers")
    mcp_list.add_argument(
        "--profile", help="Also show enabled MCP servers for this profile"
    )

    mcp_add_stdio = mcp_subparsers.add_parser("add-stdio", help="Add stdio MCP server")
    mcp_add_stdio.add_argument("name", help="Server name")
    mcp_add_stdio.add_argument(
        "stdio_command", nargs=argparse.REMAINDER, help="Command after --"
    )
    mcp_add_stdio.add_argument(
        "--env", action="append", default=[], help="KEY=VALUE env vars"
    )
    mcp_add_stdio.add_argument(
        "--description", default="", help="Human-readable capability description"
    )
    mcp_add_stdio.add_argument(
        "--capability", action="append", default=[], help="Capability hint (repeatable)"
    )
    mcp_add_stdio.add_argument(
        "--tool-hint", action="append", default=[], help="Expected MCP tool name (repeatable)"
    )
    mcp_add_stdio.add_argument(
        "--disabled", action="store_true", help="Store server disabled"
    )

    mcp_add_http = mcp_subparsers.add_parser("add-http", help="Add HTTP MCP server")
    mcp_add_http.add_argument("name", help="Server name")
    mcp_add_http.add_argument("url", help="HTTP MCP endpoint")
    mcp_add_http.add_argument(
        "--header", action="append", default=[], help="Header as KEY=VALUE"
    )
    mcp_add_http.add_argument(
        "--bearer-token-env-var", help="Env var name holding bearer token"
    )
    mcp_add_http.add_argument(
        "--description", default="", help="Human-readable capability description"
    )
    mcp_add_http.add_argument(
        "--capability", action="append", default=[], help="Capability hint (repeatable)"
    )
    mcp_add_http.add_argument(
        "--tool-hint", action="append", default=[], help="Expected MCP tool name (repeatable)"
    )
    mcp_add_http.add_argument(
        "--disabled", action="store_true", help="Store server disabled"
    )

    mcp_remove = mcp_subparsers.add_parser("remove", help="Remove MCP server")
    mcp_remove.add_argument("name", help="Server name")

    mcp_enable = mcp_subparsers.add_parser(
        "enable", help="Enable MCP server for a profile"
    )
    mcp_enable.add_argument("profile", help="Profile name")
    mcp_enable.add_argument("name", help="Server name")

    mcp_disable = mcp_subparsers.add_parser(
        "disable", help="Disable MCP server for a profile"
    )
    mcp_disable.add_argument("profile", help="Profile name")
    mcp_disable.add_argument("name", help="Server name")

    mcp_sync_codex = mcp_subparsers.add_parser(
        "sync-codex",
        help="Sync enabled MCP servers for codex profile into codex MCP registry",
    )
    mcp_sync_codex.add_argument("profile", help="Profile name (backend=codex)")
    mcp_sync_codex.add_argument(
        "--timeout-s", type=int, default=30, help="Timeout per codex mcp add call"
    )
    mcp_sync_codex.add_argument(
        "--no-progress", action="store_true", help="Disable progress lines"
    )

    mcp_telemetry = mcp_subparsers.add_parser(
        "telemetry", help="Show MCP telemetry events from local DB"
    )
    mcp_telemetry.add_argument(
        "--tail", type=int, default=20, help="How many recent events to show"
    )
    mcp_telemetry.add_argument("--profile", help="Filter by profile")
    mcp_telemetry.add_argument(
        "--event-type",
        choices=["mcp_tool_call", "mcp_signal", "mcp_error"],
        help="Filter by event type",
    )

    vault_parser = subparsers.add_parser(
        "vault", help="Manage Igorek Vault credentials and brokered access"
    )
    vault_parser.add_argument("--data-dir", help="CLI data directory override")
    vault_subparsers = vault_parser.add_subparsers(dest="vault_command", required=True)

    vault_subparsers.add_parser("status", help="Show vault runtime status")

    vault_scan = vault_subparsers.add_parser("scan", help="Scan paths for plaintext secrets")
    vault_scan.add_argument("--path", action="append", default=[], help="Path to scan; repeatable")

    vault_import = vault_subparsers.add_parser("import", help="Import credentials into vault")
    vault_import.add_argument("--from-env", help="Import known env vars from .env-style file")
    vault_import.add_argument("--from-json", help="Import detected secrets from JSON object")
    vault_import.add_argument(
        "--redact-source",
        action="store_true",
        help="Comment out imported .env lines after successful import",
    )
    vault_import.add_argument(
        "--replace-existing",
        action="store_true",
        help="Replace existing service+alias records",
    )

    vault_add = vault_subparsers.add_parser("add", help="Add one credential")
    vault_add.add_argument("service", help="Service id, e.g. openrouter, tavily, github")
    vault_add.add_argument("--alias", default="default", help="Credential alias")
    vault_add.add_argument("--credential-type", default="api_key", help="Credential type")
    vault_add.add_argument("--secret", help="Secret value; prompts when omitted")
    vault_add.add_argument("--replace-existing", action="store_true")

    vault_subparsers.add_parser("list", help="List credential metadata")

    vault_show = vault_subparsers.add_parser("show-metadata", help="Show credential metadata")
    vault_show.add_argument("service_or_id")

    vault_rotate = vault_subparsers.add_parser("rotate", help="Rotate an existing credential")
    vault_rotate.add_argument("service_or_id")
    vault_rotate.add_argument("--secret", help="New secret; prompts when omitted")

    vault_delete = vault_subparsers.add_parser("delete", help="Delete a credential")
    vault_delete.add_argument("service_or_id")
    vault_delete.add_argument("--yes", action="store_true", help="Confirm deletion")

    vault_verify = vault_subparsers.add_parser("verify", help="Verify credentials")
    vault_verify.add_argument("--service", help="Service to verify")
    vault_verify.add_argument("--all", action="store_true", help="Verify all credentials")

    vault_broker = vault_subparsers.add_parser("broker", help="Broker operations")
    vault_broker_subparsers = vault_broker.add_subparsers(
        dest="vault_broker_command", required=True
    )
    vault_broker_env = vault_broker_subparsers.add_parser(
        "env", help="Materialize ephemeral env for one service"
    )
    vault_broker_env.add_argument("service")
    vault_broker_env.add_argument("--agent", required=True)
    vault_broker_env.add_argument("--ttl", type=int, default=900)

    vault_broker_list = vault_broker_subparsers.add_parser(
        "list", help="List policy-visible credential metadata for an agent"
    )
    vault_broker_list.add_argument("--agent", required=True)

    vault_audit = vault_subparsers.add_parser("audit", help="Show recent vault access logs")
    vault_audit.add_argument("--limit", type=int, default=100)

    vault_skill = vault_subparsers.add_parser(
        "generate-skill", help="Generate vault access skill contract"
    )
    vault_skill.add_argument("--agent", help="Agent id")
    vault_skill.add_argument("--all-agents", action="store_true")

    task_parser = subparsers.add_parser("task", help="Manage tasks without raw curl")
    task_parser.add_argument("--data-dir", help="CLI data directory override")
    task_subparsers = task_parser.add_subparsers(dest="task_command", required=True)

    task_create = task_subparsers.add_parser("create", help="Create task")
    task_create.add_argument("text", help="Task text")
    task_create.add_argument("--profile", default="zai", help="Target profile")
    task_create.add_argument(
        "--wait", action="store_true", help="Wait for completion and print final task"
    )
    task_create.add_argument(
        "--timeout", type=int, default=600, help="Wait timeout in seconds"
    )
    task_create.add_argument(
        "--interval", type=float, default=2.0, help="Poll interval in seconds"
    )

    task_list = task_subparsers.add_parser("list", help="List tasks")
    task_list.add_argument("--limit", type=int, default=20, help="Max tasks to show")

    task_show = task_subparsers.add_parser("show", help="Show one task")
    task_show.add_argument("task_id", help="Task ID")

    task_wait = task_subparsers.add_parser("wait", help="Wait until task finishes")
    task_wait.add_argument("task_id", help="Task ID")
    task_wait.add_argument(
        "--timeout", type=int, default=600, help="Wait timeout in seconds"
    )
    task_wait.add_argument(
        "--interval", type=float, default=2.0, help="Poll interval in seconds"
    )

    skills_parser = subparsers.add_parser("skills", help="Manage local skills runtime")
    skills_subparsers = skills_parser.add_subparsers(
        dest="skills_command", required=True
    )

    skills_init = skills_subparsers.add_parser(
        "init", help="Create a package-style skill scaffold"
    )
    skills_init.add_argument("name", help="Skill name (lowercase-hyphen preferred)")
    skills_init.add_argument("--data-dir", help="CLI data directory override")
    skills_init.add_argument(
        "--description", default="", help="Frontmatter description"
    )
    skills_init.add_argument(
        "--trigger", action="append", default=[], help="Trigger phrase (repeatable)"
    )
    skills_init.add_argument(
        "--backend",
        action="append",
        default=[],
        help="Backend filter, e.g. claude (repeatable)",
    )
    skills_init.add_argument("--path", help="Override skills root directory")
    skills_init.add_argument(
        "--with-scripts", action="store_true", help="Create scripts/ directory"
    )
    skills_init.add_argument(
        "--with-references", action="store_true", help="Create references/ directory"
    )
    skills_init.add_argument(
        "--with-assets", action="store_true", help="Create assets/ directory"
    )
    skills_init.add_argument(
        "--force", action="store_true", help="Overwrite existing SKILL.md"
    )

    skills_list = skills_subparsers.add_parser(
        "list", help="List skills from the local runtime directory"
    )
    skills_list.add_argument("--data-dir", help="CLI data directory override")
    skills_list.add_argument("--path", help="Override skills root directory")

    skills_activate_local = skills_subparsers.add_parser(
        "activate-local", help="Switch skills runtime to data-dir/skills"
    )
    skills_activate_local.add_argument("--data-dir", help="CLI data directory override")

    skills_enable = skills_subparsers.add_parser(
        "enable", help="Enable one skill by skill_id"
    )
    skills_enable.add_argument("skill_id", help="Skill id, e.g. dev-handoff")
    skills_enable.add_argument("--data-dir", help="CLI data directory override")
    skills_enable.add_argument("--path", help="Override skills root directory")

    skills_disable = skills_subparsers.add_parser(
        "disable", help="Disable one skill by skill_id"
    )
    skills_disable.add_argument("skill_id", help="Skill id, e.g. dev-handoff")
    skills_disable.add_argument("--data-dir", help="CLI data directory override")
    skills_disable.add_argument("--path", help="Override skills root directory")

    skills_usage = skills_subparsers.add_parser(
        "usage", help="Show skills usage summary from DB"
    )
    skills_usage.add_argument(
        "--limit", type=int, default=20, help="Max skills to show"
    )
    skills_usage.add_argument("--data-dir", help="CLI data directory override")
    skills_usage.add_argument("--path", help="Override skills root directory")

    boltbook_subparsers = boltbook_parser.add_subparsers(
        dest="boltbook_command", required=True
    )

    boltbook_subparsers.add_parser("status", help="Check Boltbook claim status")
    boltbook_subparsers.add_parser("me", help="Fetch Boltbook profile")

    boltbook_feed_parser = boltbook_subparsers.add_parser(
        "feed", help="Fetch personal Boltbook feed"
    )
    boltbook_feed_parser.add_argument(
        "--sort", default="new", choices=["new", "hot", "top"]
    )
    boltbook_feed_parser.add_argument("--limit", type=int, default=15)

    boltbook_posts_parser = boltbook_subparsers.add_parser(
        "posts", help="Fetch Boltbook posts"
    )
    boltbook_posts_parser.add_argument(
        "--sort", default="new", choices=["new", "hot", "top"]
    )
    boltbook_posts_parser.add_argument("--limit", type=int, default=15)
    boltbook_posts_parser.add_argument("--submolt", help="Filter posts by submolt")

    boltbook_comments_parser = boltbook_subparsers.add_parser(
        "comments", help="Fetch comments for one Boltbook post"
    )
    boltbook_comments_parser.add_argument("post_id", help="Post id, e.g. 336")
    boltbook_comments_parser.add_argument(
        "--sort", default="new", choices=["new", "hot", "top"]
    )
    boltbook_comments_parser.add_argument("--limit", type=int, default=100)

    boltbook_actions_parser = boltbook_subparsers.add_parser(
        "actions", help="Show logged autonomous Boltbook actions"
    )
    boltbook_actions_parser.add_argument(
        "--limit", type=int, default=20, help="Max logged actions to show"
    )
    boltbook_actions_parser.add_argument(
        "--source", help="Filter by source, e.g. heartbeat"
    )
    boltbook_actions_parser.add_argument("--action-type", help="Filter by action type")
    boltbook_actions_parser.add_argument("--status", help="Filter by action status")

    boltbook_post_analysis_parser = boltbook_subparsers.add_parser(
        "post-analysis", help="Summarize autonomous Boltbook behavior for a time window"
    )
    boltbook_post_analysis_parser.add_argument(
        "--hours", type=int, default=24, help="Lookback window in hours"
    )
    boltbook_post_analysis_parser.add_argument(
        "--sent-limit", type=int, default=20, help="How many sent comments to include"
    )

    boltbook_subparsers.add_parser("persona-list", help="List Boltbook persona presets")
    boltbook_subparsers.add_parser(
        "persona-show", help="Show active Boltbook persona config"
    )
    boltbook_persona_set_parser = boltbook_subparsers.add_parser(
        "persona-set", help="Set Boltbook persona mode and keys"
    )
    boltbook_persona_set_parser.add_argument(
        "--mode",
        choices=["fixed", "round_robin_daily", "round_robin_hourly"],
        help="Persona mode",
    )
    boltbook_persona_set_parser.add_argument("--fixed", help="Fixed persona key")
    boltbook_persona_set_parser.add_argument(
        "--rotation", nargs="+", help="Persona keys used for round robin"
    )

    boltbook_generate_post_parser = boltbook_subparsers.add_parser(
        "generate-post-draft", help="Generate one local Boltbook post draft"
    )
    boltbook_generate_post_parser.add_argument(
        "--profile",
        default="codex",
        help="Worker profile used for LLM post draft generation",
    )
    boltbook_generate_post_parser.add_argument(
        "--submolt", required=True, help="Target submolt, e.g. general"
    )
    boltbook_generate_post_parser.add_argument(
        "--brief", required=True, help="Short brief for the first post"
    )
    boltbook_generate_post_parser.add_argument(
        "--timeout",
        type=int,
        default=600,
        help="Wait timeout for post draft generation",
    )

    boltbook_post_drafts_parser = boltbook_subparsers.add_parser(
        "post-drafts", help="Show local Boltbook post drafts"
    )
    boltbook_post_drafts_parser.add_argument(
        "--status",
        choices=["generated", "approved", "sent"],
        help="Filter post draft status",
    )
    boltbook_post_drafts_parser.add_argument(
        "--limit", type=int, default=20, help="Max post drafts to show"
    )

    boltbook_show_post_draft_parser = boltbook_subparsers.add_parser(
        "show-post-draft", help="Show one local Boltbook post draft"
    )
    boltbook_show_post_draft_parser.add_argument(
        "draft_id", help="Post draft id, e.g. post-draft:1"
    )

    boltbook_edit_post_draft_parser = boltbook_subparsers.add_parser(
        "edit-post-draft", help="Edit one local Boltbook post draft"
    )
    boltbook_edit_post_draft_parser.add_argument(
        "draft_id", help="Post draft id, e.g. post-draft:1"
    )
    boltbook_edit_post_draft_parser.add_argument(
        "--title", help="Replace post draft title"
    )
    boltbook_edit_post_draft_parser.add_argument(
        "--content", help="Replace post draft content"
    )

    boltbook_approve_post_parser = boltbook_subparsers.add_parser(
        "approve-post-draft", help="Approve one local Boltbook post draft for sending"
    )
    boltbook_approve_post_parser.add_argument(
        "draft_id", help="Post draft id, e.g. post-draft:1"
    )

    boltbook_send_post_parser = boltbook_subparsers.add_parser(
        "send-post-draft", help="Send one approved local Boltbook post draft"
    )
    boltbook_send_post_parser.add_argument(
        "draft_id", help="Post draft id, e.g. post-draft:1"
    )

    boltbook_submolts_parser = boltbook_subparsers.add_parser(
        "submolts", help="Fetch Boltbook submolts"
    )
    boltbook_submolts_parser.add_argument(
        "--sort", default="new", choices=["new", "hot", "top"]
    )
    boltbook_submolts_parser.add_argument("--limit", type=int, default=20)

    boltbook_subscribe_parser = boltbook_subparsers.add_parser(
        "subscribe", help="Subscribe agent to a Boltbook submolt"
    )
    boltbook_subscribe_parser.add_argument("submolt", help="Submolt name, e.g. general")

    boltbook_search_parser = boltbook_subparsers.add_parser(
        "search", help="Search Boltbook"
    )
    boltbook_search_parser.add_argument("query", help="Search query")
    boltbook_search_parser.add_argument(
        "--type", default="all", choices=["all", "posts", "comments"]
    )
    boltbook_search_parser.add_argument("--limit", type=int, default=10)
    boltbook_search_parser.add_argument("--author", help="Filter by author")
    boltbook_search_parser.add_argument("--submolt", help="Filter by submolt")

    boltbook_hb_parser = boltbook_subparsers.add_parser(
        "heartbeat-check", help="Run a safe read-only heartbeat check"
    )
    boltbook_hb_parser.add_argument(
        "--feed-sort", default="new", choices=["new", "hot", "top"]
    )
    boltbook_hb_parser.add_argument("--feed-limit", type=int, default=15)

    boltbook_tick_parser = boltbook_subparsers.add_parser(
        "heartbeat-tick", help="Run heartbeat only when due"
    )
    boltbook_tick_parser.add_argument(
        "--feed-sort", default="new", choices=["new", "hot", "top"]
    )
    boltbook_tick_parser.add_argument("--feed-limit", type=int, default=15)
    boltbook_tick_parser.add_argument(
        "--interval", type=int, default=20 * 60, help="Min seconds between checks"
    )
    boltbook_tick_parser.add_argument(
        "--skill-refresh-interval",
        type=int,
        default=24 * 60 * 60,
        help="Min seconds between daily skill refresh markers",
    )
    boltbook_tick_parser.add_argument(
        "--force", action="store_true", help="Run even if heartbeat is not due yet"
    )

    boltbook_inbox_parser = boltbook_subparsers.add_parser(
        "inbox", help="Show locally captured Boltbook candidates"
    )
    boltbook_inbox_parser.add_argument(
        "--status",
        choices=["new", "reviewed", "ignored", "draft"],
        help="Filter local candidate status",
    )
    boltbook_inbox_parser.add_argument(
        "--limit", type=int, default=20, help="Max local candidates to show"
    )

    boltbook_review_parser = boltbook_subparsers.add_parser(
        "review-inbox", help="Score local Boltbook inbox candidates"
    )
    boltbook_review_parser.add_argument(
        "--limit", type=int, default=20, help="Max reviewed candidates to return"
    )

    boltbook_reactions_parser = boltbook_subparsers.add_parser(
        "reactions", help="Show candidates worthy of reaction"
    )
    boltbook_reactions_parser.add_argument(
        "--limit", type=int, default=20, help="Max actionable candidates to show"
    )
    boltbook_reactions_parser.add_argument(
        "--min-score", type=int, default=5, help="Minimum local decision score"
    )

    boltbook_next_reaction_parser = boltbook_subparsers.add_parser(
        "next-reaction", help="Show the next actionable Boltbook reaction candidate"
    )
    boltbook_next_reaction_parser.add_argument(
        "--min-score", type=int, default=5, help="Minimum local decision score"
    )

    boltbook_generate_parser = boltbook_subparsers.add_parser(
        "generate-drafts",
        help="Generate local comment drafts from actionable candidates",
    )
    boltbook_generate_parser.add_argument(
        "--profile",
        default="codex",
        help="Worker profile used for LLM draft generation",
    )
    boltbook_generate_parser.add_argument(
        "--limit", type=int, default=20, help="Max returned candidates"
    )
    boltbook_generate_parser.add_argument(
        "--min-score", type=int, default=5, help="Minimum local decision score"
    )
    boltbook_generate_parser.add_argument(
        "--force", action="store_true", help="Regenerate existing drafts"
    )
    boltbook_generate_parser.add_argument(
        "--timeout",
        type=int,
        default=600,
        help="Wait timeout per draft task in seconds",
    )

    boltbook_drafts_parser = boltbook_subparsers.add_parser(
        "drafts", help="Show local Boltbook drafts"
    )
    boltbook_drafts_parser.add_argument(
        "--status",
        choices=["generated", "approved", "sent"],
        help="Filter draft status",
    )
    boltbook_drafts_parser.add_argument(
        "--limit", type=int, default=20, help="Max drafts to show"
    )

    boltbook_subparsers.add_parser(
        "next-draft", help="Show the next pending local Boltbook draft"
    )

    boltbook_show_draft_parser = boltbook_subparsers.add_parser(
        "show-draft", help="Show one draft in compact form"
    )
    boltbook_show_draft_parser.add_argument(
        "candidate_id", help="Candidate id, e.g. post:336"
    )

    boltbook_edit_draft_parser = boltbook_subparsers.add_parser(
        "edit-draft", help="Replace draft text before sending"
    )
    boltbook_edit_draft_parser.add_argument(
        "candidate_id", help="Candidate id, e.g. post:336"
    )
    boltbook_edit_draft_parser.add_argument(
        "--text", required=True, help="New draft text"
    )

    boltbook_approve_parser = boltbook_subparsers.add_parser(
        "approve-draft", help="Approve one local draft for sending"
    )
    boltbook_approve_parser.add_argument(
        "candidate_id", help="Candidate id, e.g. post:336"
    )

    boltbook_send_parser = boltbook_subparsers.add_parser(
        "send-draft", help="Send one approved draft to Boltbook"
    )
    boltbook_send_parser.add_argument(
        "candidate_id", help="Candidate id, e.g. post:336"
    )

    return parser


def main() -> int:
    parser = _build_parser()
    args, rest = parser.parse_known_args()

    command_to_module: dict[str, str] = {
        "api": "factory.factory_api",
        "worker": "factory.core.worker",
        "guest-runner": "factory.core.isolation.guest_runner",
        "guest-terminal": "factory.core.isolation.guest_terminal",
    }

    if args.command in command_to_module:
        return _run_module_main(command_to_module[args.command], rest)

    if args.command in {
        "init",
        "doctor",
        "task",
        "run",
        "gateway",
        "ask",
        "chat",
        "mcp",
        "openrouter",
        "vault",
        "skills",
        "boltbook",
        "telegram",
    }:
        os.environ["IGOREK_FACTORY_HOME"] = getattr(
            args, "data_dir", None
        ) or os.environ.get("IGOREK_FACTORY_HOME", str(DEFAULT_CLI_HOME))

    if args.command == "init":
        try:
            result = init_cli_environment(
                data_dir=args.data_dir,
                provider=args.provider,
                api_key=args.api_key,
                model=args.model,
                reasoning_effort=args.reasoning_effort,
                dangerous_mode=(True if args.dangerous_mode else None),
                backend=args.backend,
                base_url=args.base_url,
                profile_name=args.profile_name,
                force=args.force,
                interactive=not args.non_interactive,
                env_file=args.env_file,
                setup_telegram=args.setup_telegram,
                telegram_bot_token=args.telegram_bot_token,
                telegram_allowed_user_ids=args.telegram_allowed_user_ids,
                telegram_profile=args.telegram_profile,
                telegram_auto_approve=args.telegram_auto_approve,
                telegram_auto_approve_scope=args.telegram_auto_approve_scope,
                enabled_toolsets=(
                    [t.strip() for t in args.enabled_toolsets.split(",") if t.strip()]
                    if args.enabled_toolsets
                    else None
                ),
                tool_display=args.tool_display,
            )
        except (ValueError, RuntimeError) as exc:
            print(str(exc), file=sys.stderr)
            return 2
        except KeyboardInterrupt as exc:
            print(str(exc) or "Инициализация прервана пользователем.", file=sys.stderr)
            return 130
        if args.json or not sys.stdout.isatty():
            _print_json(result)
        if sys.stdout.isatty() and not args.json:
            _print_init_followup(result)
        return 0

    if args.command == "doctor":
        if args.read_only:
            result = check_host_readiness(data_dir=args.data_dir)
            if args.json:
                _print_json(result)
            else:
                print(format_readiness(result))
            return 1 if result["status"] == "needs_action" else 0
        result = doctor_cli_environment(data_dir=args.data_dir)
        if args.json or not sys.stdout.isatty():
            _print_json(result)
        if sys.stdout.isatty() and not args.json:
            _print_doctor_followup(result)
        return 0

    if args.command == "openrouter":
        if args.openrouter_command == "update-free-model":
            result = update_openrouter_profile_model(
                profile=args.profile,
                profiles_dir=args.profiles_dir,
                refresh=not args.cached,
                dry_run=args.dry_run,
                require_tools=not args.allow_no_tools,
                min_context=args.min_context,
                max_age_days=(args.max_age_days if args.max_age_days > 0 else None),
                exclude_models=set(args.exclude_model or []),
                limit=args.limit,
            )
            _print_json(result)
            return 0 if result.get("status") == "ok" else 2

    if args.command == "wally":
        from .core.wally_auth import (
            WallyAuthError,
            list_wally_models,
            wally_account_info,
            wally_account_summary_lines,
            wally_model_catalog,
        )

        try:
            if args.wally_command == "status":
                result = wally_account_info(days=args.days)
                if args.json or not sys.stdout.isatty():
                    _print_json(result)
                else:
                    for line in wally_account_summary_lines(days=args.days):
                        print(line)
                return 0
            if args.wally_command == "models":
                if args.catalog:
                    catalog = wally_model_catalog()
                    if args.json or not sys.stdout.isatty():
                        _print_json({"models": catalog})
                    else:
                        for item in catalog:
                            print(
                                f"{item['id']}\t{item['display_name']}\t"
                                f"in ${item.get('input_per_mtok')}/Mtok\t"
                                f"out ${item.get('output_per_mtok')}/Mtok"
                            )
                else:
                    models = list_wally_models()
                    if args.json or not sys.stdout.isatty():
                        _print_json({"models": models})
                    else:
                        for model_id in models:
                            print(model_id)
                return 0
        except WallyAuthError as exc:
            print(f"Wally: {exc}", file=sys.stderr)
            return 2

    if args.command == "launch-help":
        _print_launch_help()
        return 0

    if args.command == "env-help":
        _print_runtime_env_help()
        return 0

    if args.command == "auth":
        if args.auth_command == "login":
            key = args.key
            if not key:
                import getpass
                key = getpass.getpass(f"API key for {args.provider}: ")
            if not key or not key.strip():
                print("Error: API key is required", file=sys.stderr)
                return 2
            try:
                result = vault_cli.add(
                    service=args.provider.strip().lower(),
                    alias=args.provider.strip(),
                    credential_type="api_key",
                    secret=key.strip(),
                    replace_existing=True,
                )
                if result.get("status") == "ok":
                    cred = result.get("credential", {})
                    service = cred.get("service", args.provider)
                    print(f"API key stored in vault for service '{service}'.")
                    print(f"Key saved to encrypted vault at: ~/.igorek-agent-factory/vault/")
                    print(f"Verify connection: igorek-factory auth verify {service}")
                    print(f"Use profile:   igorek-factory chat --profile codex-{service}")
                else:
                    print(f"Error: {result.get('error', 'unknown')}", file=sys.stderr)
                    return 2
            except Exception as exc:
                print(f"Error: {exc}", file=sys.stderr)
                return 2
            return 0

    if args.command == "vault":
        try:
            if args.vault_command == "status":
                _print_json(vault_cli.status())
                return 0
            if args.vault_command == "scan":
                _print_json(vault_cli.scan(args.path or None))
                return 0
            if args.vault_command == "import":
                _print_json(
                    vault_cli.import_credentials(
                        from_env=args.from_env,
                        from_json=args.from_json,
                        redact_source=args.redact_source,
                        replace_existing=args.replace_existing,
                    )
                )
                return 0
            if args.vault_command == "add":
                _print_json(
                    vault_cli.add(
                        service=args.service,
                        alias=args.alias,
                        credential_type=args.credential_type,
                        secret=args.secret,
                        replace_existing=args.replace_existing,
                    )
                )
                return 0
            if args.vault_command == "list":
                _print_json(vault_cli.list_credentials())
                return 0
            if args.vault_command == "show-metadata":
                _print_json(vault_cli.show_metadata(args.service_or_id))
                return 0
            if args.vault_command == "rotate":
                _print_json(vault_cli.rotate(args.service_or_id, args.secret))
                return 0
            if args.vault_command == "delete":
                _print_json(vault_cli.delete(args.service_or_id, args.yes))
                return 0
            if args.vault_command == "verify":
                _print_json(vault_cli.verify(args.service, args.all))
                return 0
            if args.vault_command == "broker":
                if args.vault_broker_command == "env":
                    _print_json(vault_cli.broker_env(args.service, args.agent, args.ttl))
                    return 0
                if args.vault_broker_command == "list":
                    _print_json(vault_cli.broker_list(args.agent))
                    return 0
            if args.vault_command == "audit":
                _print_json(vault_cli.audit(args.limit))
                return 0
            if args.vault_command == "generate-skill":
                _print_json(vault_cli.generate_skill(args.agent, args.all_agents))
                return 0
        except Exception as exc:
            print(str(exc), file=sys.stderr)
            return 2

    if args.command == "mcp":

        def parse_pairs(items: list[str]) -> dict[str, str]:
            result: dict[str, str] = {}
            for item in items:
                if "=" not in item:
                    raise ValueError(f"Expected KEY=VALUE, got: {item}")
                key, value = item.split("=", 1)
                result[key] = value
            return result

        def parse_stdio_remainder(
            items: list[str],
        ) -> tuple[list[str], list[str], str, list[str], list[str], bool]:
            command: list[str] = []
            env_items = list(args.env)
            description = str(args.description or "")
            capabilities = list(args.capability)
            tools_hint = list(args.tool_hint)
            disabled = bool(args.disabled)
            index = 0
            while index < len(items):
                item = items[index]
                if item == "--":
                    command = items[index + 1 :]
                    break
                if item == "--env" and index + 1 < len(items):
                    env_items.append(items[index + 1])
                    index += 2
                    continue
                if item == "--description" and index + 1 < len(items):
                    description = items[index + 1]
                    index += 2
                    continue
                if item == "--capability" and index + 1 < len(items):
                    capabilities.append(items[index + 1])
                    index += 2
                    continue
                if item == "--tool-hint" and index + 1 < len(items):
                    tools_hint.append(items[index + 1])
                    index += 2
                    continue
                if item == "--disabled":
                    disabled = True
                    index += 1
                    continue
                command = items[index:]
                break
            return command, env_items, description, capabilities, tools_hint, disabled

        def load_mcp_telemetry(
            *, tail: int, profile: str | None, event_type: str | None
        ) -> dict:
            db_path = initialize_database(get_db_path())
            clauses = ["event_type LIKE 'mcp_%'"]
            params: list[object] = []
            if profile:
                clauses.append("profile = ?")
                params.append(profile)
            if event_type:
                clauses.append("event_type = ?")
                params.append(event_type)
            where_sql = " AND ".join(clauses)
            limit = max(1, int(tail))

            query = f"""
                SELECT event_id, task_id, event_type, profile, model, details, created_at,
                       datetime(created_at, 'unixepoch') AS created_at_iso
                FROM events
                WHERE {where_sql}
                ORDER BY created_at DESC
                LIMIT ?
            """
            params.append(limit)

            items: list[dict] = []
            with open_db(db_path) as conn:
                cur = conn.cursor()
                cur.execute(query, tuple(params))
                rows = cur.fetchall()
            for row in rows:
                details_raw = row[5]
                details_payload: object = details_raw
                if isinstance(details_raw, str):
                    try:
                        details_payload = json.loads(details_raw)
                    except json.JSONDecodeError:
                        details_payload = details_raw
                items.append(
                    {
                        "event_id": row[0],
                        "task_id": row[1],
                        "event_type": row[2],
                        "profile": row[3],
                        "model": row[4],
                        "details": details_payload,
                        "created_at": row[6],
                        "created_at_iso": row[7],
                    }
                )
            return {
                "status": "ok",
                "count": len(items),
                "tail": limit,
                "items": items,
            }

        if args.mcp_command == "list":
            payload = {
                "registry": str(get_mcp_registry_path(args.data_dir)),
                "servers": list_mcp_servers(args.data_dir),
            }
            if args.profile:
                payload["profile"] = args.profile
                payload["enabled_for_profile"] = get_profile_mcp_servers(
                    args.profile, args.data_dir
                )
            _print_json(payload)
            return 0
        if args.mcp_command == "add-stdio":
            (
                command,
                env_items,
                description,
                capabilities,
                tools_hint,
                disabled,
            ) = parse_stdio_remainder(list(args.stdio_command))
            if not command:
                parser.error("mcp add-stdio requires a command after --")
            payload = add_stdio_server(
                args.name,
                command,
                env=parse_pairs(env_items),
                disabled=disabled,
                data_dir=args.data_dir,
                description=description,
                capabilities=capabilities,
                tools_hint=tools_hint,
            )
            _print_json(
                {
                    "status": "ok",
                    "name": args.name,
                    "server": payload,
                    "registry": str(get_mcp_registry_path(args.data_dir)),
                }
            )
            return 0
        if args.mcp_command == "add-http":
            payload = add_http_server(
                args.name,
                args.url,
                headers=parse_pairs(args.header),
                bearer_token_env_var=args.bearer_token_env_var,
                disabled=args.disabled,
                data_dir=args.data_dir,
                description=args.description,
                capabilities=args.capability,
                tools_hint=args.tool_hint,
            )
            _print_json(
                {
                    "status": "ok",
                    "name": args.name,
                    "server": payload,
                    "registry": str(get_mcp_registry_path(args.data_dir)),
                }
            )
            return 0
        if args.mcp_command == "remove":
            removed = remove_mcp_server(args.name, args.data_dir)
            _print_json({"status": "ok" if removed else "missing", "name": args.name})
            return 0
        if args.mcp_command == "enable":
            enabled = enable_mcp_server_for_profile(
                args.profile, args.name, args.data_dir
            )
            _print_json(
                {"status": "ok", "profile": args.profile, "mcp_servers": enabled}
            )
            return 0
        if args.mcp_command == "disable":
            enabled = disable_mcp_server_for_profile(
                args.profile, args.name, args.data_dir
            )
            _print_json(
                {"status": "ok", "profile": args.profile, "mcp_servers": enabled}
            )
            return 0
        if args.mcp_command == "sync-codex":
            progress = (
                None
                if args.no_progress
                else (lambda message: print(f"[mcp] {message}"))
            )
            _print_json(
                sync_codex_mcp_for_profile(
                    args.profile,
                    args.data_dir,
                    timeout_s=args.timeout_s,
                    progress_cb=progress,
                )
            )
            return 0
        if args.mcp_command == "telemetry":
            _print_json(
                load_mcp_telemetry(
                    tail=args.tail, profile=args.profile, event_type=args.event_type
                )
            )
            return 0

    if args.command == "run":
        return run_local_stack(
            api_host=args.host,
            api_port=args.port,
            profile=args.profile,
            poll_interval=args.poll_interval,
        )

    if args.command == "mobile":
        from .core.mobile_bridge.cli import run_mobile_command

        return run_mobile_command(args)

    if args.command == "web":
        from .web_ui.server import run_server

        return run_server(args)

    if args.command == "gateway":
        return run_gateway_stack(
            api_host=args.host,
            api_port=args.port,
            profile=args.profile,
            poll_interval=args.poll_interval,
            env_file=args.env_file,
            data_dir=args.data_dir,
        )

    if args.command == "telegram":
        allowed_user_ids = {
            item.strip()
            for item in str(args.allowed_user_ids or "").split(",")
            if item.strip()
        }
        allowed_chat_ids = {
            item.strip()
            for item in str(args.allowed_chat_ids or "").split(",")
            if item.strip()
        }
        return run_telegram_bridge(
            token=args.token,
            profile=args.profile,
            runtime_api_base_url=args.runtime_api_base_url,
            runtime_api_key=args.runtime_api_key,
            allowed_user_ids=allowed_user_ids,
            allowed_chat_ids=allowed_chat_ids,
            workdir=(args.workdir or None),
            poll_timeout_s=args.poll_timeout,
            auto_approve=args.auto_approve,
            auto_approve_scope=args.auto_approve_scope,
        )

    if args.command == "ask":
        task = ask_task(
            text=args.text,
            profile=args.profile,
            interval_s=args.interval,
            timeout_s=args.timeout,
        )
        if args.json:
            _print_json(task)
            return 0

        if task["status"] != "ok":
            message = (
                task.get("error_message") or task.get("result_text") or "Task failed"
            )
            print(message)
            return 1

        print((task.get("result_text") or task.get("output_text") or "").rstrip())
        return 0

    if args.command == "chat":
        if rest:
            parser.error("unrecognized arguments: " + " ".join(rest))
        if args.session and args.resume:
            parser.error("Use only one of --session or --resume for chat")
        if args.session and getattr(args, 'fork', None):
            parser.error("Use only one of --session or --fork for chat")

        # Determine session name, possibly via interactive picker
        session_name = None
        fork_source = None
        if getattr(args, 'fork', None):
            from core.cli_chat import list_sessions as _list_sessions
            from core.tui.resume_picker import run_resume_picker, SessionPickerAction, SessionSelection
            sessions = _list_sessions()
            if not sessions:
                print("No sessions found to fork from.")
                return 1
            sel, chosen = run_resume_picker(sessions, action=SessionPickerAction.FORK)
            if sel == SessionSelection.EXIT:
                return 0
            if sel == SessionSelection.START_FRESH or chosen is None:
                session_name = generate_session_name()
            else:
                fork_source = chosen["session_id"]
                session_name = generate_session_name()
        elif args.resume == "__picker__":
            from core.cli_chat import list_sessions as _list_sessions
            from core.tui.resume_picker import run_resume_picker, SessionPickerAction, SessionSelection
            sessions = _list_sessions()
            if not sessions:
                session_name = generate_session_name()
            else:
                sel, chosen = run_resume_picker(sessions, action=SessionPickerAction.RESUME)
                if sel == SessionSelection.EXIT:
                    return 0
                if sel == SessionSelection.START_FRESH or chosen is None:
                    session_name = generate_session_name()
                else:
                    session_name = chosen["session_id"]
                    # CWD prompt if session cwd differs
                    if chosen.get("cwd") and chosen["cwd"] != str(Path.cwd()):
                        from core.tui.cwd_prompt import run_cwd_prompt, CwdPromptAction, CwdPromptOutcomeKind, CwdSelection
                        outcome = run_cwd_prompt(
                            action=CwdPromptAction.Resume,
                            session_cwd=chosen["cwd"],
                            current_cwd=str(Path.cwd()),
                        )
                        if outcome.kind == CwdPromptOutcomeKind.Exit:
                            return 0
                        if outcome.selection == CwdSelection.Session:
                            session_cwd = Path(chosen["cwd"])
                            if session_cwd.is_dir():
                                os.chdir(session_cwd)
                            else:
                                print(f"Session directory not found, staying in {Path.cwd()}: {session_cwd}")
        elif args.resume:
            session_name = args.resume
        elif args.session:
            session_name = args.session
        else:
            session_name = generate_session_name()

        # If forking, copy source session to new name
        if fork_source:
            from core.cli_chat import _session_path, fork_session
            source_path = _session_path(fork_source)
            new_name = fork_session(source_path)
            if new_name:
                session_name = new_name
        from core.cli_chat_rich import run_rich_chat

        return run_rich_chat(
            profile=args.profile,
            interval_s=args.interval,
            timeout_s=args.timeout,
            session=session_name,
            debug=args.debug,
            data_dir=args.data_dir,
        )

    if args.command == "task":
        if args.task_command == "create":
            created = create_task(text=args.text, profile=args.profile)
            if args.wait:
                _print_json(
                    wait_task(
                        created["task_id"],
                        interval_s=args.interval,
                        timeout_s=args.timeout,
                    )
                )
            else:
                _print_json(created)
            return 0
        if args.task_command == "list":
            _print_json(list_tasks(limit=args.limit))
            return 0
        if args.task_command == "show":
            task = get_task(args.task_id)
            if task is None:
                print(json.dumps({"error": "Task not found"}, ensure_ascii=False))
                return 1
            _print_json(task)
            return 0
        if args.task_command == "wait":
            _print_json(
                wait_task(
                    args.task_id, interval_s=args.interval, timeout_s=args.timeout
                )
            )
            return 0

    if args.command == "skills":
        if args.skills_command == "init":
            _print_json(
                init_skill(
                    name=args.name,
                    data_dir=args.data_dir,
                    path=args.path,
                    description=args.description,
                    triggers=args.trigger,
                    backends=args.backend,
                    with_scripts=args.with_scripts,
                    with_references=args.with_references,
                    with_assets=args.with_assets,
                    force=args.force,
                )
            )
            return 0
        if args.skills_command == "list":
            _print_json(list_skills(data_dir=args.data_dir, path=args.path))
            return 0
        if args.skills_command == "activate-local":
            _print_json(activate_local_skills(data_dir=args.data_dir))
            return 0
        if args.skills_command == "enable":
            _print_json(
                set_skill_enabled(
                    args.skill_id, enabled=True, data_dir=args.data_dir, path=args.path
                )
            )
            return 0
        if args.skills_command == "disable":
            _print_json(
                set_skill_enabled(
                    args.skill_id, enabled=False, data_dir=args.data_dir, path=args.path
                )
            )
            return 0
        if args.skills_command == "usage":
            _print_json(
                skills_usage_summary(
                    limit=args.limit, data_dir=args.data_dir, path=args.path
                )
            )
            return 0

    if args.command == "boltbook":
        if args.boltbook_command == "status":
            boltbook_print_json(boltbook_status())
            return 0
        if args.boltbook_command == "me":
            boltbook_print_json(boltbook_me())
            return 0
        if args.boltbook_command == "feed":
            boltbook_print_json(boltbook_feed(sort=args.sort, limit=args.limit))
            return 0
        if args.boltbook_command == "posts":
            boltbook_print_json(
                boltbook_posts(sort=args.sort, limit=args.limit, submolt=args.submolt)
            )
            return 0
        if args.boltbook_command == "comments":
            boltbook_print_json(
                boltbook_comments(
                    post_id=args.post_id, sort=args.sort, limit=args.limit
                )
            )
            return 0
        if args.boltbook_command == "actions":
            boltbook_print_json(
                boltbook_actions(
                    limit=args.limit,
                    source=args.source,
                    action_type=args.action_type,
                    status=args.status,
                )
            )
            return 0
        if args.boltbook_command == "post-analysis":
            boltbook_print_json(
                boltbook_post_analysis(
                    hours=args.hours,
                    sent_limit=args.sent_limit,
                )
            )
            return 0
        if args.boltbook_command == "persona-list":
            boltbook_print_json(boltbook_persona_list())
            return 0
        if args.boltbook_command == "persona-show":
            boltbook_print_json(boltbook_persona_show())
            return 0
        if args.boltbook_command == "persona-set":
            boltbook_print_json(
                boltbook_persona_set(
                    mode=args.mode,
                    fixed=args.fixed,
                    rotation=args.rotation,
                )
            )
            return 0
        if args.boltbook_command == "generate-post-draft":
            boltbook_print_json(
                boltbook_generate_post(
                    profile=args.profile,
                    submolt=args.submolt,
                    brief=args.brief,
                    timeout_s=args.timeout,
                )
            )
            return 0
        if args.boltbook_command == "post-drafts":
            boltbook_print_json(
                boltbook_post_drafts(status=args.status, limit=args.limit)
            )
            return 0
        if args.boltbook_command == "show-post-draft":
            boltbook_print_json(boltbook_show_post_draft(args.draft_id))
            return 0
        if args.boltbook_command == "edit-post-draft":
            boltbook_print_json(
                boltbook_update_post_draft(
                    args.draft_id,
                    title=args.title,
                    content=args.content,
                )
            )
            return 0
        if args.boltbook_command == "approve-post-draft":
            boltbook_print_json(boltbook_approve_post(args.draft_id))
            return 0
        if args.boltbook_command == "send-post-draft":
            boltbook_print_json(boltbook_send_post(args.draft_id))
            return 0
        if args.boltbook_command == "submolts":
            boltbook_print_json(boltbook_submolts(sort=args.sort, limit=args.limit))
            return 0
        if args.boltbook_command == "subscribe":
            boltbook_print_json(boltbook_subscribe_submolt(args.submolt))
            return 0
        if args.boltbook_command == "search":
            boltbook_print_json(
                boltbook_search(
                    query=args.query,
                    result_type=args.type,
                    limit=args.limit,
                    author=args.author,
                    submolt=args.submolt,
                )
            )
            return 0
        if args.boltbook_command == "heartbeat-check":
            boltbook_print_json(
                boltbook_heartbeat_check(
                    feed_limit=args.feed_limit, feed_sort=args.feed_sort
                )
            )
            return 0
        if args.boltbook_command == "heartbeat-tick":
            boltbook_print_json(
                boltbook_heartbeat_tick(
                    feed_limit=args.feed_limit,
                    feed_sort=args.feed_sort,
                    interval_s=args.interval,
                    skill_refresh_interval_s=args.skill_refresh_interval,
                    force=args.force,
                )
            )
            return 0
        if args.boltbook_command == "inbox":
            boltbook_print_json(boltbook_inbox(status=args.status, limit=args.limit))
            return 0
        if args.boltbook_command == "review-inbox":
            boltbook_print_json(boltbook_review_inbox(limit=args.limit))
            return 0
        if args.boltbook_command == "reactions":
            boltbook_print_json(
                boltbook_reactions(limit=args.limit, min_score=args.min_score)
            )
            return 0
        if args.boltbook_command == "next-reaction":
            boltbook_print_json(boltbook_next_reaction(min_score=args.min_score))
            return 0
        if args.boltbook_command == "generate-drafts":
            boltbook_print_json(
                boltbook_generate_drafts(
                    profile=args.profile,
                    limit=args.limit,
                    min_score=args.min_score,
                    force=args.force,
                    timeout_s=args.timeout,
                )
            )
            return 0
        if args.boltbook_command == "drafts":
            boltbook_print_json(boltbook_drafts(status=args.status, limit=args.limit))
            return 0
        if args.boltbook_command == "next-draft":
            boltbook_print_json(boltbook_next_draft())
            return 0
        if args.boltbook_command == "show-draft":
            boltbook_print_json(boltbook_show_draft(args.candidate_id))
            return 0
        if args.boltbook_command == "edit-draft":
            boltbook_print_json(boltbook_update_draft(args.candidate_id, args.text))
            return 0
        if args.boltbook_command == "approve-draft":
            boltbook_print_json(boltbook_approve_draft(args.candidate_id))
            return 0
        if args.boltbook_command == "send-draft":
            boltbook_print_json(boltbook_send_draft(args.candidate_id))
            return 0

    parser.error(f"Unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
