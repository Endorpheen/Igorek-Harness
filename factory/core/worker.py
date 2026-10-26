#!/usr/bin/env python3
"""
Agent Factory Worker
Takes tasks from queue and executes them with claude
"""

import json
import sys
import time
import uuid
import sqlite3
import argparse
import os
import subprocess
import shutil
import re
import threading
import urllib.error
import urllib.request
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

try:
    from .prompt_assembly import (
        build_cc_backend_system_prompt,
        build_codex_backend_input,
        build_isolation_approval_recovery_context,
    )
    from .agent_foundation import foundation_prompt_context
    from .cc_backend_tools import get_tool_schemas
    from .web_tools_runtime import scoped_web_tools

    # Quick MCP check
    _all = get_tool_schemas()
    _fal = [t.get("name", "") for t in _all if "fal" in t.get("name", "").lower()]
    print(f"[worker] MCP CHECK: tools={len(_all)}, fal={_fal}", flush=True)
    from .codex_provider import expand_profile_env, resolve_codex_launch_settings
    from .codex_event_parser import (
        extract_codex_final_text,
        extract_codex_final_text_after_tool_results,
        extract_codex_tool_events,
        extract_codex_tool_result_context,
        extract_codex_usage,
    )
    from .runtime import (
        build_backend_command,
        get_data_root,
        get_db_path,
        get_profiles_dir,
        get_profile_config,
        get_workspace_root,
        profile_backend_name,
        profile_filesystem_mode,
        profile_model_name,
        resolve_local_model_id,
    )
    from . import security_levels, security_settings
    from .schema import ensure_schema, initialize_database, open_db
    from .mcp_config import (
        get_profile_mcp_servers,
        list_mcp_capabilities_for_profile,
        list_mcp_servers,
        materialize_profile_mcp_config,
        sync_codex_mcp_for_profile,
    )
    from .model_routing import build_escalation_handoff, resolve_escalation_route
    from .openrouter_pricing import estimate_cost_for_openrouter_model
    from .security_runtime import SecurityRuntime
    from .trace_taxonomy import CcTraceEvent, trace_event
    from .isolation.config import (
        ENV_MODE as ISOLATION_MODE_ENV,
        MODE_KVM as ISOLATION_MODE_KVM,
    )
    from .isolation.errors import IsolationError
    from .isolation.workspace import GUEST_WORKSPACE_PATH
    from .vault.broker import SERVICE_ENV_MAP
    from .vault.logging_redaction import redact_mapping
    from .vault.runtime import build_services as build_vault_services
    from .skills_manager import skills_manager as _skills_manager

    skills_manager = _skills_manager
    SKILLS_AVAILABLE = True
except ImportError:  # pragma: no cover - direct script fallback
    from prompt_assembly import (
        build_cc_backend_system_prompt,
        build_codex_backend_input,
        build_isolation_approval_recovery_context,
    )
    from agent_foundation import foundation_prompt_context
    from cc_backend_tools import get_tool_schemas
    from web_tools_runtime import scoped_web_tools
    from codex_provider import expand_profile_env, resolve_codex_launch_settings
    from codex_event_parser import (
        extract_codex_final_text,
        extract_codex_final_text_after_tool_results,
        extract_codex_tool_events,
        extract_codex_tool_result_context,
        extract_codex_usage,
    )
    from runtime import (
        build_backend_command,
        get_data_root,
        get_db_path,
        get_profiles_dir,
        get_profile_config,
        get_workspace_root,
        profile_backend_name,
        profile_filesystem_mode,
        profile_model_name,
        resolve_local_model_id,
    )
    import security_levels
    import security_settings
    from schema import ensure_schema, initialize_database, open_db
    from mcp_config import (
        get_profile_mcp_servers,
        list_mcp_capabilities_for_profile,
        list_mcp_servers,
        materialize_profile_mcp_config,
        sync_codex_mcp_for_profile,
    )
    from model_routing import build_escalation_handoff, resolve_escalation_route
    from openrouter_pricing import estimate_cost_for_openrouter_model
    from security_runtime import SecurityRuntime
    from trace_taxonomy import CcTraceEvent, trace_event
    from isolation.config import (
        ENV_MODE as ISOLATION_MODE_ENV,
        MODE_KVM as ISOLATION_MODE_KVM,
    )
    from isolation.errors import IsolationError
    from isolation.workspace import GUEST_WORKSPACE_PATH
    from vault.broker import SERVICE_ENV_MAP
    from vault.logging_redaction import redact_mapping
    from vault.runtime import build_services as build_vault_services

    try:
        from skills_manager import skills_manager as _skills_manager

        skills_manager = _skills_manager
        SKILLS_AVAILABLE = True
    except ImportError:
        SKILLS_AVAILABLE = False
        print("[Worker] Skills manager not available")

# Add current directory to path for local modules
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

if TYPE_CHECKING:  # pragma: no cover - typing only, never imported at runtime
    from .isolation.host_op_runtime import HostOpApprovalRuntime
    from .isolation.manager import IsolationManager


def _format_mcp_names(names: list[str] | None) -> str:
    items = [str(item).strip() for item in (names or []) if str(item).strip()]
    return ", ".join(items) if items else "-"


def _format_mcp_errors(errors: list[dict[str, str]] | None) -> str:
    parts: list[str] = []
    for item in errors or []:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        error = str(item.get("error") or "").strip()
        if name and error:
            parts.append(f"{name}:{error}")
        elif name:
            parts.append(name)
        elif error:
            parts.append(error)
    return ", ".join(parts) if parts else "-"


def _resolve_level_runtime(
    config: dict,
    task_metadata: dict | None,
) -> dict[str, str | None]:
    """Resolve the server-side access level into runtime fields.

    A pure function with no persistence side effects. Obtain the level from
    `security_settings.resolve_security_level` (session > global > baseline),
    with the profile's `runtime.profile_filesystem_mode` as the baseline.

    Obtain `session_id` only from server-owned `task_metadata["session"]`, never
    model-supplied tool arguments. Intentionally ignore
    `task_metadata["security_level"]` and record that fact in trace, preventing a
    model from raising its access level through metadata.

    Return `{filesystem_mode, guard_mode, security_mode}`. Preserve the
    `user-read` baseline: `filesystem_mode='user-read'`, with guard/security unset.
    """
    metadata = task_metadata if isinstance(task_metadata, dict) else {}
    if "security_level" in metadata:
        print(
            f"[worker] ignoring task_metadata.security_level="
            f"{metadata.get('security_level')!r}"
        )
    when_no_level = profile_filesystem_mode(config)
    resolved = security_settings.resolve_security_level(
        session_id=metadata.get("session"),
        when_no_level=when_no_level,
    )
    if resolved.get("level") == "user-read":
        return {
            "filesystem_mode": "user-read",
            "guard_mode": None,
            "security_mode": None,
        }
    runtime = security_levels.level_to_runtime(resolved.get("level"))
    return {
        "filesystem_mode": runtime["filesystem_mode"],
        "guard_mode": runtime["guard_mode"],
        "security_mode": runtime["security_mode"],
    }


def _vault_reason_is_missing_credential(reason: object) -> bool:
    return "credential not found in vault" in str(reason or "").strip().lower()


def _existing_env_for_vault_service(
    service: str,
    environ: dict[str, str] | None = None,
) -> dict[str, str]:
    source = os.environ if environ is None else environ
    env_template = SERVICE_ENV_MAP.get(service, {})
    existing: dict[str, str] = {}
    for env_name in env_template:
        value = source.get(env_name)
        if value:
            existing[str(env_name)] = str(value)
    return existing


def _estimate_openrouter_task_cost(
    *,
    provider: str | None,
    model: str | None,
    usage: dict | None,
) -> float:
    if str(provider or "").strip().lower() != "openrouter":
        return 0.0
    if not isinstance(usage, dict):
        return 0.0
    try:
        return estimate_cost_for_openrouter_model(str(model or ""), usage)
    except Exception:
        return 0.0


def _codex_tool_event_payloads(line: str) -> list[dict[str, Any]]:
    payloads: list[dict[str, Any]] = []
    for event in extract_codex_tool_events(line):
        if event.event_type == "function_call":
            payloads.append(
                {
                    "kind": CcTraceEvent.TOOL_REQUEST_BATCH,
                    "source": "codex_json",
                    "round": "codex",
                    "tools": [
                        {
                            "id": event.call_id,
                            "name": event.name or "function_call",
                            "input": event.arguments or {},
                        }
                    ],
                }
            )
        elif event.event_type == "function_call_output":
            payloads.append(
                {
                    "kind": CcTraceEvent.TOOL_RESULT,
                    "source": "codex_json",
                    "tool_use_id": event.call_id,
                    "raw_result_preview": event.summary,
                    "duration_ms": None,
                    "output_chars": event.output_chars,
                    "exit_code": event.exit_code,
                }
            )
    return payloads


def _append_codex_trace_event(trace_path: Path, event: dict[str, Any]) -> None:
    try:
        trace_path.parent.mkdir(parents=True, exist_ok=True)
        with trace_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, ensure_ascii=False) + "\n")
    except OSError:
        return


def _trace_path_for_task(task_id: str) -> Path:
    return Path(get_workspace_root()) / f"temp_execution_{task_id[:8]}" / "cc_backend_trace.jsonl"


def _codex_rollout_files(codex_home: str | None) -> list[Path]:
    if not codex_home:
        return []
    root = Path(codex_home).expanduser()
    if not root.exists():
        return []
    try:
        return sorted(root.glob("sessions/**/rollout-*.jsonl"), key=lambda path: path.stat().st_mtime)
    except OSError:
        return []


def _start_codex_rollout_trace_monitor(
    *,
    codex_home: str | None,
    trace_path: Path,
    started_at: float,
    seen_payloads: set[str],
    seen_lock: threading.Lock,
) -> tuple[threading.Event, threading.Thread] | None:
    if not codex_home:
        return None

    stop_event = threading.Event()

    def _append_payload_once(payload: dict[str, Any]) -> None:
        key = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        with seen_lock:
            if key in seen_payloads:
                return
            seen_payloads.add(key)
        _append_codex_trace_event(trace_path, payload)

    def _monitor() -> None:
        offsets: dict[Path, int] = {}
        while not stop_event.is_set():
            files = _codex_rollout_files(codex_home)
            for path in files:
                try:
                    if path.stat().st_mtime + 2 < started_at:
                        continue
                    with path.open("r", encoding="utf-8") as handle:
                        handle.seek(offsets.get(path, 0))
                        for line in handle:
                            for payload in _codex_tool_event_payloads(line):
                                _append_payload_once(payload)
                        offsets[path] = handle.tell()
                except OSError:
                    continue
            stop_event.wait(0.2)

        for path in _codex_rollout_files(codex_home):
            try:
                if path.stat().st_mtime + 2 < started_at:
                    continue
                with path.open("r", encoding="utf-8") as handle:
                    handle.seek(offsets.get(path, 0))
                    for line in handle:
                        for payload in _codex_tool_event_payloads(line):
                            _append_payload_once(payload)
                    offsets[path] = handle.tell()
            except OSError:
                continue

    thread = threading.Thread(target=_monitor, daemon=True)
    thread.start()
    return stop_event, thread


def _recent_codex_rollout_text(
    *,
    codex_home: str | None,
    started_at: float,
) -> str:
    lines: list[str] = []
    for path in _codex_rollout_files(codex_home):
        try:
            if path.stat().st_mtime + 2 < started_at:
                continue
            lines.append(path.read_text(encoding="utf-8"))
        except OSError:
            continue
    return "\n".join(lines)


def _combine_usage(first: dict[str, int], second: dict[str, int]) -> dict[str, int]:
    if not first:
        return dict(second)
    if not second:
        return dict(first)
    result = dict(first)
    for key in (
        "prompt",
        "completion",
        "input_tokens",
        "cached_input_tokens",
        "output_tokens",
        "reasoning_output_tokens",
        "total_tokens",
    ):
        result[key] = int(first.get(key, 0) or 0) + int(second.get(key, 0) or 0)
    if first.get("model_context_window") or second.get("model_context_window"):
        result["model_context_window"] = int(
            second.get("model_context_window")
            or first.get("model_context_window")
            or 0
        )
    return result


def _build_codex_synthesis_input(
    *,
    task_text: str,
    tool_context: str,
) -> str:
    return "\n\n".join(
        [
            "Всегда отвечай на русском языке.",
            "Инструменты уже выполнены. Не вызывай новые инструменты и не читай файлы повторно.",
            "Сформулируй финальный ответ пользователю только по результатам ниже.",
            "Если данных недостаточно, коротко скажи, чего не хватает.",
            "## Исходный запрос пользователя",
            task_text.strip() or "-",
            "## Результаты инструментов",
            tool_context.strip() or "-",
        ]
    )


def _without_tool(tool_schemas: list[dict], tool_name: str) -> list[dict]:
    return [
        schema
        for schema in tool_schemas
        if str(schema.get("name") or "").strip() != tool_name
    ]


def _task_supports_interactive_input(task_metadata: dict) -> bool:
    return str(task_metadata.get("source") or "").strip() in {
        "cli_chat_rich",
        "cli_chat_tui",
        "mobile_bridge",
    }


def _only_tools(tool_schemas: list[dict], allowed_tool_names: set[str]) -> list[dict]:
    return [
        schema
        for schema in tool_schemas
        if str(schema.get("name") or "").strip() in allowed_tool_names
    ]


def _filter_by_toolsets(tool_schemas: list[dict], enabled_toolset_names: list[str]) -> list[dict]:
    """Filter tool schemas to only include tools from the specified toolsets."""
    if not enabled_toolset_names:
        return tool_schemas
    from .toolsets import resolve_toolset
    from .tool_registry import registry

    normalized_toolsets = [
        str(ts_name).strip()
        for ts_name in enabled_toolset_names
        if str(ts_name).strip()
    ]
    if any(ts_name in {"all", "*"} for ts_name in normalized_toolsets):
        return tool_schemas

    tool_to_toolset = registry.get_tool_to_toolset_map()
    allowed: set[str] = set()
    for ts_name in normalized_toolsets:
        allowed.update(resolve_toolset(ts_name))
        allowed.update(
            tool_name
            for tool_name, toolset in tool_to_toolset.items()
            if toolset == ts_name
        )
    if not allowed:
        return tool_schemas
    return _only_tools(tool_schemas, allowed)


def _reload_enabled_mcp_servers_for_cc_backend(profile: str) -> list[dict]:
    """Hot reload profile-enabled MCP servers after cc-backend vault env exists."""
    enabled = get_profile_mcp_servers(profile)
    if not enabled:
        return []
    registry_payload = list_mcp_servers()
    results: list[dict] = []
    try:
        from .mcp_bridge import reload_mcp_server_from_registry, desktop_mcp_entries
    except ImportError:  # pragma: no cover - direct script fallback
        from mcp_bridge import reload_mcp_server_from_registry, desktop_mcp_entries

    registry_payload.update(desktop_mcp_entries())

    for server_name in enabled:
        entry = registry_payload.get(server_name)
        if not isinstance(entry, dict):
            results.append(
                {
                    "status": "missing",
                    "server_name": server_name,
                    "tools": [],
                }
            )
            continue
        result = reload_mcp_server_from_registry(server_name, entry)
        results.append(result)
    return results


def _default_cc_backend_toolsets(config: dict) -> list[str]:
    """Return provider defaults when the profile does not pin toolsets."""
    explicit = config.get("enabled_toolsets")
    if isinstance(explicit, list) and explicit:
        return [str(item).strip() for item in explicit if str(item).strip()]
    provider = str(config.get("provider") or "").strip().lower()
    if provider == "openrouter":
        return ["file", "terminal", "search", "web", "session", "skills", "mcp", "interactive"]
    return []


def _cc_backend_prompt_style(config: dict) -> str:
    explicit = str(config.get("prompt_style") or "").strip().lower()
    if explicit:
        return explicit
    provider = str(config.get("provider") or "").strip().lower()
    if provider == "openrouter":
        return "compact"
    return "full"


def _stable_prompt_prefix(config: dict) -> bool:
    """Whether the profile keeps a byte-stable system prompt for KV-cache reuse.

    Explicit ``stable_prompt_prefix`` wins; otherwise auto-enabled for local
    endpoints (``provider: local`` or a localhost base_url). When on, per-turn
    dynamic content is kept out of the system prompt so llama-server can reuse
    its KV cache instead of reprocessing the whole prompt.
    """
    from .runtime import profile_stable_prompt_prefix

    return profile_stable_prompt_prefix(config)


def _isolation_kvm_requested() -> bool:
    """True only for an explicit ``IGOREK_ISOLATION_MODE=kvm`` request.

    The raw env is read here instead of building the full ``IsolationConfig``:
    the off path stays side-effect free, while a requested but unusable ``kvm``
    mode fails closed later in the isolation manager (never degraded to off).
    """
    return (
        str(os.environ.get(ISOLATION_MODE_ENV, "")).strip().lower()
        == ISOLATION_MODE_KVM
    )


def _isolation_prompt_workdir(host_workdir: str, isolation_active: bool) -> str:
    """Workspace path the system prompt must name for this turn.

    A kvm session sees only the single virtiofs export at ``/workspace``; the
    host path is meaningless inside the guest and the channel payload policy
    rejects it, so the prompt is built against the guest path instead.
    """
    if not isolation_active:
        return host_workdir
    return GUEST_WORKSPACE_PATH


def _isolation_failure_error(result: dict[str, Any]) -> str:
    """Clear error text for a contained isolation failure.

    The manager's terminal failures carry ``error`` (a full sentence) and
    ``failure_reason`` (a machine token); the task table needs one of them in
    ``error_message``, never an empty string.
    """
    for key in ("error", "failure_details"):
        value = result.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    reason = str(result.get("failure_reason") or "").strip()
    return f"isolation_failed: {reason}" if reason else "isolation_failed"


def _isolation_failure_result(result: dict[str, Any], *, task_id: str) -> dict[str, Any]:
    """Worker-shaped terminal failure for a contained isolation failure.

    The generic executor normalizer keeps only status/output/usage (it drops
    ``error``), so a kvm failure would reach ``update_task`` with an empty
    ``error_message``. This is the same ``{status, error, usage}`` shape the
    ``IsolationError`` branch returns, used only for ``status == "fail"``: a
    failure can never be turned into a success here.
    """
    usage = result.get("usage")
    # The manager uses `fail` as its executor-compatible shape for an
    # acknowledged host cancel. Preserve that distinction at the task boundary
    # so mobile clients render an intentional stop as stopped, not as a red
    # thread error. Other isolation failures remain failures.
    status = (
        "cancelled"
        if str(result.get("failure_reason") or "") == "canceled"
        else "fail"
    )
    return {
        "task_id": task_id,
        "status": status,
        "error": _isolation_failure_error(result),
        "failure_reason": str(result.get("failure_reason") or ""),
        **({'diagnostic': result['diagnostic']} if isinstance(result.get('diagnostic'), dict) else {}),
        "usage": usage
        if isinstance(usage, dict)
        else {"prompt": 0, "completion": 0, "lat_ms_p50": 0},
    }


def _consume_host_op_resolutions(task_id: str, task_metadata: dict) -> str:
    """Run a resumed task's approved host ops once; ``''`` when nothing is due.

    A non-empty answer is a stable reason the runtime must not proceed with the
    turn: an approved host op it cannot execute has to surface, never be dropped.
    """
    if not (
        task_metadata.get("host_op_resolutions")
        or task_metadata.get("host_op_session_grants")
    ):
        return ""
    from .isolation.host_op_resume import consume_pending_host_op_resolutions

    try:
        consumption = consume_pending_host_op_resolutions(
            task_id=task_id, runtime=_get_host_op_runtime()
        )
    except (OSError, sqlite3.Error) as exc:
        print(f"[worker] host-op resolution failed: {exc}")
        return "host_op_resolution_failed"
    if consumption.refused:
        print(f"[worker] host-op resolution refused: {consumption.refused_reason}")
        return consumption.refused_reason
    if consumption.metadata:
        task_metadata.clear()
        task_metadata.update(consumption.metadata)
    if consumption.executed:
        print(f"[worker] host-op resolutions executed: {len(consumption.executed)}")
    return ""


_HOST_OP_RUNTIME: "HostOpApprovalRuntime | None" = None
_HOST_OP_RUNTIME_RESOLVED = False


def _get_host_op_runtime() -> "HostOpApprovalRuntime | None":
    """Process-wide host-op runtime; ``None`` when this host cannot build one.

    One instance is shared by the isolation manager's guest-host-op intake and
    by the resume consumer, so a session grant consumed from task metadata is
    visible to the intake that decides whether a later request parks.
    """
    global _HOST_OP_RUNTIME, _HOST_OP_RUNTIME_RESOLVED
    if not _HOST_OP_RUNTIME_RESOLVED:
        from .isolation.host_op_runtime import build_host_op_runtime

        _HOST_OP_RUNTIME = build_host_op_runtime()
        _HOST_OP_RUNTIME_RESOLVED = True
    return _HOST_OP_RUNTIME


_ISOLATION_MANAGER: "IsolationManager | None" = None


def _get_isolation_manager() -> "IsolationManager":
    """Process-wide isolation manager, built on first use (``kvm`` mode only)."""
    global _ISOLATION_MANAGER
    if _ISOLATION_MANAGER is None:
        from .isolation.manager import IsolationManager, ManagerSeams

        _ISOLATION_MANAGER = IsolationManager.from_env(
            seams=ManagerSeams(host_ops=_get_host_op_runtime())
        )
    return _ISOLATION_MANAGER


def _isolation_cancel_check(task_id: str) -> Callable[[], bool]:
    """Host stop signal for one isolated dispatch.

    Mobile/TUI stop retains the transition to ``cancelling`` through
    ``cli_tasks.cancel_task``. The manager polls this seam while awaiting the
    guest and sends ``cancel`` over its channel. The manager owns acknowledgment
    and teardown behavior; do not reimplement it here.

    Reuse ``task_should_stop`` from the off-mode executor. A missing task row
    also means stop: a deleted task must not keep a guest dispatch alive.
    """
    def requested() -> bool:
        try:
            from .tool_runtime_context import is_interrupt_requested
        except ImportError:
            from tool_runtime_context import is_interrupt_requested
        if is_interrupt_requested():
            print(
                f"[worker] isolation.cancel_requested task_id={task_id or '(untracked)'} "
                "source=runtime_interrupt",
                flush=True,
            )
            return True
        if not task_id:
            return False
        try:
            from .cc_backend_executor import task_should_stop
        except ImportError:
            from cc_backend_executor import task_should_stop
        if not task_should_stop(task_id):
            return False
        try:
            from .cli_tasks import get_task
        except ImportError:
            from cli_tasks import get_task
        try:
            task = get_task(task_id)
        except Exception:
            task = None
            source = "task_stop_predicate_db_error_or_row_missing"
        else:
            source = (
                "task_row_missing"
                if task is None
                else "task_status_cancelling"
                if task.get("status") == "cancelling"
                else "task_stop_predicate_changed"
            )
        print(
            f"[worker] isolation.cancel_requested task_id={task_id} source={source}",
            flush=True,
        )
        return True

    return requested


# Resolve runtime paths. Installed uv-tool uses ~/.igorek-agent-factory, repo checkout keeps repo-local paths.
FACTORY_DIR = Path(get_data_root())
DB_PATH = get_db_path()
WORKSPACE_ROOT = get_workspace_root()
PROFILES_PATH = get_profiles_dir()
initialize_database(DB_PATH)


def _effective_cc_backend_model(
    *,
    provider: str,
    configured_model: str,
    task_metadata: dict,
    base_url: str,
) -> str:
    requested_model = str(task_metadata.get("model_override") or configured_model or "").strip()
    if provider in {"opencode", "opencode-go", "openrouter", "minimax", "glm", "wally"}:
        return requested_model
    return _resolve_cc_backend_model(base_url, requested_model)


def _resolve_cc_backend_api_format(config: dict, model: str) -> str:
    configured = str(config.get("api_format") or "").strip().lower() or "anthropic"
    provider = str(config.get("provider") or "").strip().lower()
    if provider == "wally":
        # RunAnywhere (Wally) only provides an OpenAI-compatible transport.
        return "openai"
    if provider in {"opencode", "opencode-go", "minimax", "glm"}:
        try:
            from .opencode_go_catalog import opencode_go_model_transport
        except ImportError:  # pragma: no cover - direct script fallback
            from opencode_go_catalog import opencode_go_model_transport
        return opencode_go_model_transport(model) or configured
    return configured


_OPENCODE_SESSION_ID = (
    os.environ.get("FACTORY_OPENCODE_SESSION", "").strip() or f"factory-{uuid.uuid4().hex}"
)


def _resolve_cc_backend_auth_headers(config: dict, *, api_format: str | None = None, environment=None) -> dict[str, str] | None:
    environment = os.environ if environment is None else environment
    effective_format = str(api_format or config.get("api_format") or "").strip().lower()
    provider = str(config.get("provider") or "").strip().lower()
    if provider == "wally":
        # The Wally token is fetched from the live session of RunAnywhere (via vault env or credentials.json)
        # and renewed via Wally’s refresh mechanism; logging is not performed.
        try:
            from .wally_auth import WallyAuthError, get_wally_access_token
        except ImportError:  # pragma: no cover - direct script fallback
            from wally_auth import WallyAuthError, get_wally_access_token

        try:
            token = get_wally_access_token()
        except WallyAuthError as exc:
            raise RuntimeError(f"Wally authentication failed: {exc}") from None
        return {"Authorization": f"Bearer {token}"}
    if effective_format != "openai" and provider not in {"opencode", "opencode-go", "minimax", "glm"}:
        return None
    key = ""
    if provider == "openrouter":
        key = (
            environment.get("OPENROUTER_API_KEY", "").strip()
            or environment.get("CODEX_OPENROUTER_API_KEY", "").strip()
            or environment.get("FACTORY_OPENROUTER_API_KEY", "").strip()
        )
    elif provider == "minimax":
        key = (
            environment.get("ANTHROPIC_AUTH_TOKEN", "").strip()
            or environment.get("MINIMAX_API_KEY", "").strip()
        )
    elif provider == "glm":
        key = (
            environment.get("ANTHROPIC_AUTH_TOKEN", "").strip()
            or environment.get("GLM_API_KEY", "").strip()
        )
    else:
        key = environment.get("OPENCODE_API_KEY", "").strip()
    if key:
        headers = (
            {"x-api-key": key}
            if effective_format == "anthropic"
            else {"Authorization": f"Bearer {key}"}
        )
        if provider in {"opencode", "opencode-go"}:
            headers.update(
                {
                    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36",
                    "Origin": "https://opencode.ai",
                    "x-opencode-session": _OPENCODE_SESSION_ID,
                }
            )
        return headers
    return None


def _read_project_instructions(workdir: str) -> str:
    """Read AGENTS.md (and CLAUDE.md only if AGENTS.md is missing) for cc-backend."""
    parts: list[str] = []
    root = Path(workdir)
    agents_path = root / "AGENTS.md"
    claude_path = root / "CLAUDE.md"

    primary = agents_path if agents_path.is_file() else claude_path
    if primary.is_file():
        try:
            content = primary.read_text(encoding="utf-8").strip()
            if content:
                header = (
                    f"# Project Configuration ({primary.name}) — already loaded into your context, "
                    "do NOT re-read with tools"
                )
                parts.append(f"{header}\n\n{content}")
        except Exception:
            pass

    return "\n\n".join(parts)


def _extract_raw_user_message(task_text: str) -> str:
    """Extract the raw user message from the factory's wrapper text.

    The TUI wraps user messages with memory/context/history. The server's
    intent router works best with the raw user message. This function tries
    to extract just the user's actual request.
    """
    # Pattern: "Last user message:\n<actual message>"
    marker = "Последнее сообщение пользователя:"
    idx = task_text.rfind(marker)
    if idx >= 0:
        extracted = task_text[idx + len(marker) :].strip()
        if extracted:
            return extracted

    # Pattern: "User task:\n<actual message>"
    marker2 = "Задача пользователя:"
    idx2 = task_text.rfind(marker2)
    if idx2 >= 0:
        extracted = task_text[idx2 + len(marker2) :].strip()
        if extracted:
            return extracted

    # Fallback: if the text looks like a wrapped message (starts with the boilerplate),
    # take the last non-empty paragraph
    if "Ты продолжаешь локальный CLI-диалог" in task_text:
        parts = [p.strip() for p in task_text.split("\n\n") if p.strip()]
        if len(parts) > 1:
            return parts[-1]

    # No wrapper detected — return as-is
    return task_text


def _resolve_cc_backend_model(base_url: str, fallback: str) -> str:
    """Read the served model using the same endpoint normalization as the UI."""
    return resolve_local_model_id(base_url, str(fallback or "").strip())


def _foundation_context_block(data_dir: str | None = None) -> str:
    try:
        return foundation_prompt_context(data_dir).strip()
    except Exception:
        return ""


def _prepend_foundation_context(task_text: str, foundation_context: str) -> str:
    foundation = (foundation_context or "").strip()
    if not foundation:
        return task_text
    if (
        foundation in task_text
        or "Ниже твоя долговременная foundation-память" in task_text
    ):
        return task_text
    return foundation + "\n\n" + task_text


def _truthy(value: object) -> bool:
    return str(value or "").strip().lower() in {"1", "true", "yes", "on"}


def _string_list(value: object) -> list[str]:
    if isinstance(value, str):
        raw_items = value.split(",")
    elif isinstance(value, (list, tuple, set)):
        raw_items = list(value)
    else:
        raw_items = []
    return [str(item).strip() for item in raw_items if str(item).strip()]


@contextmanager
def _temporary_env(updates: dict[str, str]):
    if not updates:
        yield
        return
    previous = {key: os.environ.get(key) for key in updates}
    os.environ.update({str(key): str(value) for key, value in updates.items()})
    try:
        yield
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


class Worker:
    def __init__(self, profile: str, poll_interval: int = 2, *, auth_headers_provider=None, isolation_manager=None):
        self.profile = profile
        self.auth_headers_provider = auth_headers_provider
        self.isolation_manager = isolation_manager
        self.poll_interval = poll_interval
        self.pid = os.getpid()
        self.tasks_processed = 0
        self.last_cleanup_time = time.time()
        self.cleanup_interval = 300  # Run cleanup every 300 seconds (5 minutes)
        # Heartbeat for live tasks: while execute_task is running, the worker periodically updates tasks.updated_at.
        # Without it, a long-running (but fully live) task would appear stuck after cleanup_interval, since updated_at is only set at task creation and claim.
        # The value must be strictly less than timeout_seconds in cleanup_stuck_tasks, otherwise the heartbeat won’t have time to complete.
        self.heartbeat_interval = 30
        self.security_runtime = SecurityRuntime()

        # Load profile configuration
        self.config = get_profile_config(profile)
        self.backend = profile_backend_name(self.config)
        self.model = profile_model_name(self.config)
        self.codex_settings = (
            resolve_codex_launch_settings(self.config)
            if self.backend == "codex"
            else None
        )
        self.mcp_config_path = materialize_profile_mcp_config(self.profile)
        self.codex_mcp_sync_status = None
        self.enabled_mcp_servers = get_profile_mcp_servers(self.profile)
        if self.backend == "codex":
            try:
                print(
                    f"[{self.profile}] MCP enabled: {_format_mcp_names(self.enabled_mcp_servers)}"
                )
                self.codex_mcp_sync_status = sync_codex_mcp_for_profile(
                    self.profile,
                    codex_env=self.codex_settings.get("env")
                    if isinstance(self.codex_settings, dict)
                    else None,
                )
                print(
                    f"[{self.profile}] MCP sync (codex): "
                    f"status={self.codex_mcp_sync_status.get('status')} | "
                    f"added={_format_mcp_names(self.codex_mcp_sync_status.get('added', []))} | "
                    f"existing={_format_mcp_names(self.codex_mcp_sync_status.get('skipped_existing', []))} | "
                    f"errors={_format_mcp_errors(self.codex_mcp_sync_status.get('errors', []))}"
                )
            except Exception as exc:
                print(f"[{self.profile}] MCP sync (codex) error: {exc}")

        # Ensure workspace exists
        os.makedirs(WORKSPACE_ROOT, exist_ok=True)

        # Ensure database schema exists before any component uses it
        os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
        with open_db(DB_PATH) as conn:
            self._ensure_db_schema(conn)

        # Load skills if available
        if SKILLS_AVAILABLE:
            skills_manager.load_settings()
            if skills_manager.skills_dirs and any(
                os.path.isdir(d) for d in skills_manager.skills_dirs
            ):
                skills_manager.load_skills()
                print(f"[{self.profile}] Loaded {len(skills_manager.skills)} skills from {len(skills_manager.skills_dirs)} dirs")
            else:
                print(f"[{self.profile}] Skills directories not configured or not found")

    def _vault_request_config(self, task_metadata: dict | None = None) -> dict:
        metadata = task_metadata if isinstance(task_metadata, dict) else {}
        profile_vault = self.config.get("vault")
        profile_vault_cfg = profile_vault if isinstance(profile_vault, dict) else {}
        metadata_vault = metadata.get("vault")
        metadata_vault_cfg = metadata_vault if isinstance(metadata_vault, dict) else {}

        services = []
        services.extend(_string_list(os.environ.get("FACTORY_VAULT_SERVICES")))
        services.extend(_string_list(self.config.get("vault_services")))
        services.extend(_string_list(profile_vault_cfg.get("services")))
        services.extend(_string_list(metadata.get("vault_services")))
        services.extend(_string_list(metadata_vault_cfg.get("services")))
        services = list(dict.fromkeys(services))

        enabled = (
            _truthy(os.environ.get("FACTORY_VAULT_ENABLED"))
            or _truthy(profile_vault_cfg.get("enabled"))
            or _truthy(metadata_vault_cfg.get("enabled"))
            or bool(services)
        )
        if not enabled or not services:
            return {"enabled": False, "services": []}

        agent_id = (
            str(metadata_vault_cfg.get("agent") or metadata.get("vault_agent") or "").strip()
            or str(profile_vault_cfg.get("agent") or self.config.get("vault_agent") or "").strip()
            or str(os.environ.get("FACTORY_VAULT_AGENT") or "").strip()
            or self.backend
            or self.profile
        )
        try:
            ttl = int(
                metadata_vault_cfg.get("ttl_seconds")
                or metadata.get("vault_ttl_seconds")
                or profile_vault_cfg.get("ttl_seconds")
                or self.config.get("vault_ttl_seconds")
                or os.environ.get("FACTORY_VAULT_TTL_SECONDS")
                or 900
            )
        except Exception:
            ttl = 900
        return {
            "enabled": True,
            "services": services,
            "agent": agent_id,
            "ttl_seconds": ttl,
        }

    def _materialize_vault_env(
        self,
        *,
        task_id: str | None,
        task_metadata: dict | None = None,
    ) -> dict[str, str]:
        request = self._vault_request_config(task_metadata)
        if not request.get("enabled"):
            return {}

        if getattr(self, "auth_headers_provider", None) is not None:
            raise PermissionError("Additional vault services are not configured for this desktop runtime")

        services = _string_list(request.get("services"))
        agent_id = str(request.get("agent") or self.backend or self.profile).strip()
        ttl_seconds = int(request.get("ttl_seconds") or 900)
        merged_env: dict[str, str] = {}
        decisions: list[dict[str, object]] = []

        vault_services = build_vault_services(prompt=False)
        for service in services:
            decision = vault_services.broker.get_ephemeral_env(
                service=service,
                agent_id=agent_id,
                ttl=ttl_seconds,
            )
            decision_summary = {
                "service": service,
                "agent": agent_id,
                "allowed": decision.allowed,
                "reason": decision.reason,
                "ttl_seconds": decision.ttl_seconds,
                "env_names": sorted(decision.env.keys()),
            }
            decisions.append(decision_summary)
            if not decision.allowed:
                existing_env = _existing_env_for_vault_service(service)
                if _vault_reason_is_missing_credential(decision.reason):
                    if existing_env:
                        decision_summary["fallback"] = "existing_env"
                        decision_summary["env_names"] = sorted(existing_env.keys())
                        merged_env.update(existing_env)
                        self.security_runtime.store.log_event(
                            event_type="vault_env_existing_env_reused",
                            task_id=task_id,
                            decision="allow",
                            target=service,
                            details=decision_summary,
                        )
                    else:
                        decision_summary["fallback"] = "missing_optional_skipped"
                        self.security_runtime.store.log_event(
                            event_type="vault_env_missing_optional_skipped",
                            task_id=task_id,
                            decision="allow",
                            target=service,
                            details=decision_summary,
                        )
                    continue
                self.security_runtime.store.log_event(
                    event_type="vault_env_denied",
                    task_id=task_id,
                    decision="deny",
                    target=service,
                    details=decision_summary,
                )
                raise PermissionError(
                    f"Igorek Vault denied service '{service}' for agent '{agent_id}': {decision.reason}"
                )
            merged_env.update({str(key): str(value) for key, value in decision.env.items()})

        self.security_runtime.store.log_event(
            event_type="vault_env_materialized",
            task_id=task_id,
            decision="allow",
            target=",".join(services),
            details={
                "agent": agent_id,
                "services": services,
                "env_names": sorted(merged_env.keys()),
                "decisions": decisions,
            },
        )
        print(
            f"[{self.profile}] Igorek Vault env materialized: "
            f"agent={agent_id} services={','.join(services)} env={','.join(sorted(merged_env.keys()))}",
            flush=True,
        )
        return merged_env

    def copy_created_files_to_workspace(
        self, parent_id: str, task_id: str, workdir: str
    ):
        """# Copies created files from the temporary directory to workspace/results"""
        try:
            try:
                from .workspace_manager import WorkspaceManager
            except ImportError:
                from workspace_manager import WorkspaceManager
            ws_manager = WorkspaceManager()
            workspace_path = ws_manager.get_workspace_path(parent_id)

            if not workspace_path:
                print(f"[{self.profile}] Не найден workspace для {parent_id}")
                return

            # Create a results folder in the workspace
            results_dir = os.path.join(workspace_path, "results")
            os.makedirs(results_dir, exist_ok=True)

            # Create a subfolder for a specific subtask
            task_folder = os.path.join(results_dir, f"{self.profile}_{task_id[:8]}")
            os.makedirs(task_folder, exist_ok=True)

            # Find all files in the working directory, excluding system files
            exclude_files = {
                "input.json",
                "result.json",
                "MEMORY.md",
                "USER.md",
                "IDENTITY.md",
                "SOUL.md",
                "BOOTSTRAP.md",
            }
            copied_files = []

            for item in os.listdir(workdir):
                item_path = os.path.join(workdir, item)
                if os.path.isfile(item_path) and item not in exclude_files:
                    dest_path = os.path.join(task_folder, item)
                    shutil.copy2(item_path, dest_path)
                    copied_files.append(item)
                    print(f"[{self.profile}] Файл скопирован: {item} -> {dest_path}")

            if copied_files:
                # Creating metadata for copied files
                metadata = {
                    "task_id": task_id,
                    "profile": self.profile,
                    "copied_at": datetime.now().isoformat(),
                    "files": copied_files,
                    "source_dir": workdir,
                    "target_dir": task_folder,
                }

                metadata_file = os.path.join(task_folder, "copy_metadata.json")
                with open(metadata_file, "w", encoding="utf-8") as f:
                    json.dump(metadata, f, indent=2, ensure_ascii=False)

                print(
                    f"[{self.profile}] Скопировано {len(copied_files)} файлов в workspace/results"
                )

        except Exception as e:
            print(f"[{self.profile}] Ошибка копирования файлов: {e}")

    def claim_task(self, conn) -> dict | None:
        """Atomically claim next task for this profile"""
        cur = conn.cursor()

        # Try to claim a task with advisory lock
        cur.execute(
            """
            SELECT task_id FROM tasks
            WHERE status = 'queued'
              AND profile = ?
              AND retry_count < max_retries
            ORDER BY created_at
            LIMIT 1
        """,
            (self.profile,),
        )

        row = cur.fetchone()
        if not row:
            return None

        task_id = row[0]

        # Attempt to atomically update the task
        cur.execute(
            """
            UPDATE tasks
            SET status = 'processing',
                started_at = strftime('%s', 'now'),
                updated_at = strftime('%s', 'now')
            WHERE task_id = ?
              AND status = 'queued'
        """,
            (task_id,),
        )

        if cur.rowcount == 0:
            # Someone else claimed it
            return None

        conn.commit()

        # Fetch full task
        return self.get_task(conn, task_id)

    def get_task(self, conn, task_id: str) -> dict:
        """Fetch full task by ID"""
        cur = conn.cursor()
        cur.execute("SELECT * FROM tasks WHERE task_id = ?", (task_id,))
        row = cur.fetchone()
        columns = [desc[0] for desc in cur.description]

        task = dict(zip(columns, row))

        # Parse JSON fields
        for field in [
            "next_profiles",
            "propose_next",
            "capabilities",
            "input_files",
            "output_files",
            "metadata",
        ]:
            if task.get(field):
                try:
                    task[field] = json.loads(task[field])
                except (json.JSONDecodeError, TypeError):
                    pass

        return task

    def execute_task(self, task: dict) -> dict:
        """Execute task with configured backend."""
        task_id = task["task_id"]

        task_metadata = (
            task.get("metadata") if isinstance(task.get("metadata"), dict) else {}
        )

        host_op_refusal = _consume_host_op_resolutions(task_id, task_metadata)
        if host_op_refusal:
            return {
                "task_id": task_id,
                "status": "fail",
                "error": f"host_op_resolution_refused: {host_op_refusal}",
                "usage": {"prompt": 0, "completion": 0, "lat_ms_p50": 0},
            }

        cwd_override = str(task_metadata.get("cwd") or "").strip()
        use_cwd = bool(cwd_override and os.path.isdir(cwd_override))
        runtime_workdir = f"{WORKSPACE_ROOT}/temp_execution_{task_id[:8]}"

        if task["parent_id"] and not use_cwd:
            parent_temp_workdir = (
                f"{WORKSPACE_ROOT}/temp_execution_{str(task['parent_id'])[:8]}"
            )
            if os.path.isdir(parent_temp_workdir):
                workdir = parent_temp_workdir
            else:
                try:
                    try:
                        from .workspace_manager import WorkspaceManager
                    except ImportError:
                        from workspace_manager import WorkspaceManager
                    ws_manager = WorkspaceManager()
                    workdir = ws_manager.get_workspace_path(task["parent_id"])
                except ImportError:
                    parent_workdir = f"{WORKSPACE_ROOT}/{task['parent_id']}"
                    workdir = parent_workdir
        elif use_cwd:
            workdir = os.path.realpath(cwd_override)
        else:
            print(
                f"[{self.profile}] Main task {task_id[:8]}... should not create separate workspace"
            )
            workdir = runtime_workdir
        os.makedirs(runtime_workdir, exist_ok=True)
        if not os.path.isdir(workdir):
            os.makedirs(workdir, exist_ok=True)

        task_text = task.get("input_text", "")
        foundation_context = _foundation_context_block(get_data_root())
        if task_metadata.get('desktop_ui') and self.backend != 'cc-backend':
            from .prompt_assembly import desktop_navigation_prompt
            foundation_context += '\n\n' + desktop_navigation_prompt()
        task_text_with_foundation = _prepend_foundation_context(
            task_text, foundation_context
        )

        skills_text = ""
        skills_env: dict[str, str] = {}
        if SKILLS_AVAILABLE:
            if skills_manager.needs_reload():
                print(f"[{self.profile}] 🔥 Hot-reload: skills changed, reloading...")
                skills_manager.load_settings()
                skills_manager.load_skills()
                print(
                    f"[{self.profile}] 🔥 Hot-reload: loaded {len(skills_manager.skills)} skills"
                )

            if skills_manager.is_loaded:
                skills_text, skills_env = skills_manager.build_skill_runtime_context(
                    task_text,
                    backend=self.backend,
                    workdir=runtime_workdir,
                )

        input_data = {
            "task_id": task_id,
            "stage": task["stage"],
            "capabilities": task.get("capabilities", []),
            "input": {
                "text": f"IMPORTANT: Please respond in Russian language only.\n\n{skills_text}\n\n{task_text_with_foundation}",
                "files": task.get("input_files", []),
            },
            "limits": {
                "max_tokens": task.get("limits_max_tokens", 4000),
                "timeout_s": task.get("limits_timeout_s", 1200),
                "budget_usd": task.get("limits_budget_usd", 0.02),
            },
            "profile": self.profile,
            "instruction": "Всегда отвечай на русском языке. Никакого английского в ответах.\n\n🔒 ВАЖНЫЕ ОГРАНИЧЕНИЯ БЕЗОПАСНОСТИ:\n- Разрешено сохранять файлы ТОЛЬКО в текущую рабочую директорию: {workdir}\n- Запрещено сохранять в /tmp, /root, /etc, /var и любые директории вне workdir\n- Запрещено запускать браузеры, редакторы или внешние приложения\n- Запрещено выполнять команды вне workdir\n- Запрещено изменять системные файлы\n- Работай ТОЛЬКО с файлами в указанной рабочей директории\n- Создавай файлы ТОЛЬКО в workdir, не выходи за его пределы\n- Никогда не создавай в рабочей директории файлы MEMORY.md, USER.md, IDENTITY.md, SOUL.md или BOOTSTRAP.md; память и foundation обновляются отдельным системным механизмом".format(
                workdir=workdir
            ),
        }

        input_file = f"{runtime_workdir}/input.json"
        with open(input_file, "w") as f:
            json.dump(input_data, f, indent=2)

        env = os.environ.copy()
        env.update(expand_profile_env(self.config.get("env")))
        env.update(skills_env)

        backend = self.backend
        effective_provider = str(self.config.get("provider", ""))
        effective_model = self.model
        if backend == "claude":
            vault_env = self._materialize_vault_env(
                task_id=task_id,
                task_metadata=task_metadata,
            )
            env.update(vault_env)
            self.mcp_config_path = materialize_profile_mcp_config(self.profile)
            command = build_backend_command(
                self.config,
                workdir=workdir,
                mcp_config_path=str(self.mcp_config_path)
                if self.mcp_config_path
                else None,
            )
            backend_input = json.dumps(input_data)
            enabled_mcp_servers = get_profile_mcp_servers(self.profile)
        elif backend == "codex":
            provider_override = (
                str(task_metadata.get("provider_override") or "").strip() or None
            )
            model_override = (
                str(task_metadata.get("model_override") or "").strip() or None
            )
            codex_settings = dict(
                resolve_codex_launch_settings(
                    self.config,
                    provider_override=provider_override,
                    model_override=model_override,
                )
            )
            env.update(
                codex_settings.get("env")
                if isinstance(codex_settings.get("env"), dict)
                else {}
            )
            vault_env = self._materialize_vault_env(
                task_id=task_id,
                task_metadata=task_metadata,
            )
            env.update(vault_env)
            effective_provider = str(
                codex_settings.get("provider") or effective_provider
            )
            effective_model = str(codex_settings.get("model") or effective_model)
            try:
                codex_mcp_env = dict(
                    codex_settings.get("env")
                    if isinstance(codex_settings.get("env"), dict)
                    else {}
                )
                codex_mcp_env.update(vault_env)
                sync_status = sync_codex_mcp_for_profile(
                    self.profile,
                    codex_env=codex_mcp_env,
                )
                if sync_status.get("status") in {"partial", "error"}:
                    print(
                        f"[{self.profile}] MCP sync (codex) task warning: {sync_status}"
                    )
            except Exception as exc:
                print(f"[{self.profile}] MCP sync (codex) task warning: {exc}")
            command_profile = dict(self.config)
            if provider_override:
                command_profile["provider"] = provider_override
            if model_override:
                command_profile["model"] = model_override
            command = build_backend_command(command_profile, workdir=workdir)
            if "--json" not in command:
                try:
                    exec_index = command.index("exec")
                    command.insert(exec_index + 1, "--json")
                except ValueError:
                    command.append("--json")
            enabled_mcp_servers = get_profile_mcp_servers(self.profile)
            input_files = task.get("input_files", []) or []
            capabilities = task.get("capabilities", []) or []
            backend_input = build_codex_backend_input(
                workdir=workdir,
                profile=self.profile,
                stage=str(task.get("stage", "main")),
                task_text=task_text,
                capabilities=capabilities,
                input_files=input_files,
                skills_text=skills_text.strip(),
                foundation_context=(
                    foundation_context.strip()
                    if foundation_context.strip()
                    and "Ниже твоя долговременная foundation-память" not in task_text
                    else ""
                ),
            )
        elif backend == "cc-backend":
            try:
                return self._execute_cc_backend(
                    task,
                    workdir,
                    runtime_workdir,
                    task_text,
                    skills_text,
                    foundation_context=foundation_context,
                    start_time=None,
                )
            except Exception as exc:
                return {
                    "task_id": task_id,
                    "status": "fail",
                    "error": str(exc),
                    "usage": {"prompt": 0, "completion": 0, "lat_ms_p50": 0},
                }
        else:
            return {
                "task_id": task_id,
                "status": "fail",
                "error": f"Unsupported backend: {backend}",
                "usage": {"prompt": 0, "completion": 0, "lat_ms_p50": 0},
            }

        codex_trace_path = _trace_path_for_task(task_id)
        codex_trace_seen: set[str] = set()
        codex_trace_seen_lock = threading.Lock()

        def _codex_stdout_line_callback(line: str) -> None:
            if backend != "codex":
                return
            for payload in _codex_tool_event_payloads(line):
                key = json.dumps(payload, ensure_ascii=False, sort_keys=True)
                with codex_trace_seen_lock:
                    if key in codex_trace_seen:
                        continue
                    codex_trace_seen.add(key)
                _append_codex_trace_event(codex_trace_path, payload)

        start_time = time.time()
        codex_rollout_monitor = (
            _start_codex_rollout_trace_monitor(
                codex_home=env.get("CODEX_HOME"),
                trace_path=codex_trace_path,
                started_at=start_time,
                seen_payloads=codex_trace_seen,
                seen_lock=codex_trace_seen_lock,
            )
            if backend == "codex"
            else None
        )
        try:
            security_runtime_mode = _resolve_level_runtime(
                self.config, task_metadata
            )["security_mode"]
            proc, security_session_id = self.security_runtime.execute_command(
                command=command,
                input_payload=backend_input,
                env=env,
                cwd=workdir,
                timeout_s=task.get("limits_timeout_s", 1200),
                task_id=task_id,
                profile=self.profile,
                backend=backend,
                model=effective_model,
                provider=effective_provider,
                task_metadata=task.get("metadata")
                if isinstance(task.get("metadata"), dict)
                else None,
                stdout_line_callback=_codex_stdout_line_callback
                if backend == "codex"
                else None,
                security_mode=security_runtime_mode,
            )
            if codex_rollout_monitor is not None:
                stop_event, monitor_thread = codex_rollout_monitor
                stop_event.set()
                monitor_thread.join(timeout=2)

            lat_ms = int((time.time() - start_time) * 1000)

            print(f"[{self.profile}] Backend={backend} returncode: {proc.returncode}")
            print(
                f"[{self.profile}] Backend stdout length: {len(proc.stdout) if proc.stdout else 0}"
            )
            print(
                f"[{self.profile}] Backend stderr: {proc.stderr[:200] if proc.stderr else '(empty)'}"
            )

            mcp_telemetry: list[dict] = []
            if backend == "codex":
                mcp_telemetry = self._collect_codex_mcp_telemetry(
                    task_id=task_id,
                    stdout_text=proc.stdout or "",
                    stderr_text=proc.stderr or "",
                    enabled_servers=enabled_mcp_servers,
                )
            elif backend == "claude":
                mcp_telemetry = self._collect_claude_mcp_telemetry(
                    task_id=task_id,
                    stdout_text=proc.stdout or "",
                    stderr_text=proc.stderr or "",
                    enabled_servers=enabled_mcp_servers,
                )

            if mcp_telemetry:
                print(
                    f"[{self.profile}] MCP telemetry captured: {len(mcp_telemetry)} event(s)"
                )
                for item in mcp_telemetry[:10]:
                    print(
                        f"[{self.profile}] MCP event: "
                        f"kind={item.get('kind')} source={item.get('source_event_type')} "
                        f"summary={item.get('summary')}"
                    )

            result_file = f"{runtime_workdir}/result.json"
            if backend == "claude" and os.path.exists(result_file):
                with open(result_file) as f:
                    result = json.load(f)
                print(f"[{self.profile}] Using result.json")
            elif backend == "claude":
                parsed_output = self._extract_claude_final_text(proc.stdout or "")
                output_text = parsed_output if parsed_output else (proc.stdout or "")
                result = {
                    "task_id": task_id,
                    "status": "ok" if proc.returncode == 0 else "fail",
                    "output": {"text": output_text, "stderr": proc.stderr},
                    "usage": {"prompt": 0, "completion": 0, "lat_ms_p50": lat_ms},
                    "security": {
                        "runtime_mode": self.security_runtime.mode,
                        "session_id": security_session_id,
                    },
                }
                if mcp_telemetry:
                    result["mcp_telemetry"] = mcp_telemetry
                print(
                    f"[{self.profile}] Using claude stream-json fallback, status: {result['status']}"
                )
            elif backend == "codex":
                rollout_text = _recent_codex_rollout_text(
                    codex_home=env.get("CODEX_HOME"),
                    started_at=start_time,
                )
                parse_text = rollout_text or (proc.stdout or "")
                parsed_output = extract_codex_final_text_after_tool_results(parse_text)
                parsed_usage = extract_codex_usage(proc.stdout or "")
                codex_tool_events = extract_codex_tool_events(proc.stdout or "")
                if not codex_tool_events:
                    codex_tool_events = extract_codex_tool_events(rollout_text)
                missing_post_tool_final = bool(codex_tool_events) and not parsed_output
                synthesis_usage: dict[str, int] = {}
                synthesis_output = ""
                synthesis_error = ""
                if missing_post_tool_final:
                    tool_context = extract_codex_tool_result_context(rollout_text)
                    if tool_context:
                        synthesis_input = _build_codex_synthesis_input(
                            task_text=task_text,
                            tool_context=tool_context,
                        )
                        synthesis_started_at = time.time()
                        try:
                            synthesis_proc, _synthesis_security_session_id = (
                                self.security_runtime.execute_command(
                                    command=command,
                                    input_payload=synthesis_input,
                                    env=env,
                                    cwd=workdir,
                                    timeout_s=min(
                                        int(task.get("limits_timeout_s", 1200) or 1200),
                                        180,
                                    ),
                                    task_id=task_id,
                                    profile=self.profile,
                                    backend=backend,
                                    model=effective_model,
                                    provider=effective_provider,
                                    task_metadata=task.get("metadata")
                                    if isinstance(task.get("metadata"), dict)
                                    else None,
                                )
                            )
                            synthesis_usage = extract_codex_usage(
                                synthesis_proc.stdout or ""
                            )
                            synthesis_tool_events = extract_codex_tool_events(
                                synthesis_proc.stdout or ""
                            )
                            if synthesis_tool_events:
                                synthesis_output = (
                                    extract_codex_final_text_after_tool_results(
                                        synthesis_proc.stdout or ""
                                    )
                                )
                            else:
                                synthesis_output = extract_codex_final_text(
                                    synthesis_proc.stdout or ""
                                )
                            synthesis_error = synthesis_proc.stderr or ""
                            if synthesis_proc.returncode != 0 and not synthesis_error:
                                synthesis_error = (
                                    f"synthesis returncode={synthesis_proc.returncode}"
                                )
                            print(
                                f"[{self.profile}] Codex synthesis retry completed "
                                f"in {int((time.time() - synthesis_started_at) * 1000)}ms, "
                                f"output={bool(synthesis_output)}"
                            )
                        except Exception as exc:
                            synthesis_error = str(exc)
                    if synthesis_output:
                        parsed_output = synthesis_output
                        parsed_usage = _combine_usage(parsed_usage, synthesis_usage)
                        missing_post_tool_final = False
                output_text = parsed_output
                if not output_text and not missing_post_tool_final:
                    output_text = extract_codex_final_text(proc.stdout or "")
                if not output_text and not missing_post_tool_final:
                    output_text = proc.stdout or ""
                status = "ok" if proc.returncode == 0 else "fail"
                if missing_post_tool_final:
                    status = "fail"
                    output_text = (
                        "Codex backend did not produce a final assistant response "
                        "after tool results."
                    )
                    if synthesis_error:
                        output_text = f"{output_text} Synthesis retry failed: {synthesis_error}"
                result = {
                    "task_id": task_id,
                    "status": status,
                    "output": {"text": output_text, "stderr": proc.stderr},
                    "usage": {
                        "prompt": parsed_usage.get("prompt", 0),
                        "completion": parsed_usage.get("completion", 0),
                        "lat_ms_p50": lat_ms,
                    },
                    "security": {
                        "runtime_mode": self.security_runtime.mode,
                        "session_id": security_session_id,
                    },
                }
                if missing_post_tool_final:
                    result["error"] = output_text
                elif synthesis_output:
                    result["codex_synthesis_retry"] = True
                if parsed_usage:
                    result["usage_ext"] = parsed_usage
                    cost_usd = _estimate_openrouter_task_cost(
                        provider=str(self.config.get("provider") or ""),
                        model=self.model,
                        usage=parsed_usage,
                    )
                    if cost_usd > 0:
                        result["cost_usd"] = cost_usd
                if mcp_telemetry:
                    result["mcp_telemetry"] = mcp_telemetry
                if codex_tool_events:
                    result["codex_tool_events"] = [
                        {
                            "event_type": event.event_type,
                            "call_id": event.call_id,
                            "name": event.name,
                            "arguments": event.arguments,
                            "output_preview": event.output_preview,
                            "output_chars": event.output_chars,
                            "exit_code": event.exit_code,
                            "summary": event.summary,
                        }
                        for event in codex_tool_events
                    ]
                    print(
                        f"[{self.profile}] Codex tool events captured: "
                        f"{len(codex_tool_events)} event(s)"
                    )
                print(
                    f"[{self.profile}] Using codex json fallback, status: {result['status']}"
                )
            else:
                output_text = proc.stdout

                result = {
                    "task_id": task_id,
                    "status": "ok" if proc.returncode == 0 else "fail",
                    "output": {"text": output_text, "stderr": proc.stderr},
                    "usage": {"prompt": 0, "completion": 0, "lat_ms_p50": lat_ms},
                    "security": {
                        "runtime_mode": self.security_runtime.mode,
                        "session_id": security_session_id,
                    },
                }
                print(
                    f"[{self.profile}] Using stdout fallback, status: {result['status']}"
                )

            if result.get("status") == "ok":
                try:
                    try:
                        from .workspace_manager import WorkspaceManager
                    except ImportError:
                        from workspace_manager import WorkspaceManager
                    ws_manager = WorkspaceManager()

                    workspace_id = task["parent_id"] if task["parent_id"] else task_id

                    if task["parent_id"]:
                        ws_manager.add_subtask_result(
                            task["parent_id"],
                            task_id,
                            self.profile,
                            task["stage"],
                            result,
                        )

                    if not use_cwd:
                        self.copy_created_files_to_workspace(workspace_id, task_id, workdir)
                except Exception as e:
                    print(f"[{self.profile}] Ошибка сохранения в workspace: {e}")

            return result

        except subprocess.TimeoutExpired:
            return {
                "task_id": task_id,
                "status": "fail",
                "error": f"Timeout after {task.get('limits_timeout_s', 1200)}s",
                "usage": {
                    "prompt": 0,
                    "completion": 0,
                    "lat_ms_p50": int((time.time() - start_time) * 1000),
                },
            }
        except Exception as e:
            return {
                "task_id": task_id,
                "status": "fail",
                "error": str(e),
                "usage": {
                    "prompt": 0,
                    "completion": 0,
                    "lat_ms_p50": int((time.time() - start_time) * 1000),
                },
            }
        finally:
            if codex_rollout_monitor is not None:
                stop_event, monitor_thread = codex_rollout_monitor
                stop_event.set()
                monitor_thread.join(timeout=2)

    def update_task(self, conn, task_id: str, result: dict):
        """Update task with execution result"""
        from .cc_backend_executor import resync_approval_signatures

        cur = conn.cursor()
        stored_result = redact_mapping(result) if isinstance(result, dict) else {}

        # Determine status
        status = stored_result.get("status", "fail")
        if status not in ["ok", "fail", "retry", "waiting_approval", "cancelled"]:
            status = "ok" if stored_result.get("output") else "fail"
        if status == 'fail':
            from .diagnostics import CODES, observation
            diagnostic = stored_result.get('diagnostic')
            code = diagnostic.get('code') if isinstance(diagnostic, dict) else None
            if not isinstance(code, str) or code not in CODES:
                reason = stored_result.get('failure_reason')
                reason = reason.strip() if isinstance(reason, str) else ''
                detail = next((value.strip() for key in ('error', 'failure_details')
                               if isinstance(value := stored_result.get(key), str) and value.strip()), '')
                stored_result['diagnostic'] = observation(
                    'TASK.EXECUTION_FAILED' if reason or detail else 'ERROR.UNKNOWN',
                    component='worker', operation='task', phase='execution',
                    endpoint_role='runtime_to_provider', owner='task:'+task_id, task_id=task_id,
                    profile=str(getattr(self, 'profile', '') or ''), model=str(getattr(self, 'model', '') or ''),
                    reason_code=reason, reason_detail=detail,
                )

        # Prepare update
        output_payload = stored_result.get("output")
        output_payload = output_payload if isinstance(output_payload, dict) else {}
        output_text = str(output_payload.get("text", stored_result.get("output_text", "")))
        usage_payload = stored_result.get("usage")
        usage_payload = usage_payload if isinstance(usage_payload, dict) else {}
        update_data = {
            "status": status,
            "output_text": output_text,
            "output_files": json.dumps(output_payload.get("files", [])),
            "usage_prompt": usage_payload.get("prompt", 0),
            "usage_completion": usage_payload.get("completion", 0),
            "usage_lat_ms_p50": usage_payload.get("lat_ms_p50", 0),
            "usage_lat_ms_p95": usage_payload.get("lat_ms_p95", 0),
            "cost_usd": stored_result.get("cost_usd", 0),
            "propose_next": json.dumps(stored_result.get("propose_next", [])),
            "approval_blocked_tools": json.dumps(
                resync_approval_signatures(stored_result.get("approval_blocked_tools", []))
            ),
            # A paused task is incomplete and does not hold SQLite during user selection.
            "completed_at": None if status == "waiting_approval" else int(time.time()),
        }

        if status == "fail":
            update_data["error_message"] = stored_result.get("error", "")

        usage_ext = stored_result.get("usage_ext")
        codex_tool_events = stored_result.get("codex_tool_events")
        failure_reason = str(stored_result.get("failure_reason") or "")
        infra_failure = False
        if status == "fail" and failure_reason:
            from .isolation.failure_reasons import INFRA_FAILURE_REASONS

            infra_failure = failure_reason in INFRA_FAILURE_REASONS
        if (
            (isinstance(usage_ext, dict) and usage_ext)
            or (isinstance(codex_tool_events, list) and codex_tool_events)
            or infra_failure
            or isinstance(stored_result.get('diagnostic'), dict)
        ):
            cur.execute("SELECT metadata FROM tasks WHERE task_id = ?", (task_id,))
            row = cur.fetchone()
            metadata_payload: dict = {}
            if row and row[0]:
                try:
                    loaded = json.loads(row[0])
                    if isinstance(loaded, dict):
                        metadata_payload = loaded
                except json.JSONDecodeError:
                    metadata_payload = {}
            if isinstance(usage_ext, dict) and usage_ext:
                metadata_payload["usage_ext"] = usage_ext
            if isinstance(codex_tool_events, list) and codex_tool_events:
                metadata_payload["codex_tool_events"] = codex_tool_events
            if infra_failure:
                metadata_payload["infra_failure"] = True
                metadata_payload["failure_reason"] = failure_reason
            if isinstance(stored_result.get('diagnostic'), dict):
                from .diagnostics import sanitize_observation
                metadata_payload['diagnostic'] = sanitize_observation(stored_result['diagnostic'])
                if failure_reason:
                    metadata_payload['diagnostic_failure_reason'] = failure_reason
            update_data["metadata"] = json.dumps(metadata_payload, ensure_ascii=False)

        # Update query
        fields = ", ".join([f"{k} = ?" for k in update_data.keys()])
        values = list(update_data.values()) + [task_id]

        cur.execute(
            f"""
            UPDATE tasks
            SET {fields}
            WHERE task_id = ?
        """,
            values,
        )

        # Log event
        event_id = str(uuid.uuid4())
        cur.execute(
            """
            INSERT INTO events (event_id, task_id, event_type, profile, pid,
                              base_url, model, details, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
            (
                event_id,
                task_id,
                f"task_{status}",
                self.profile,
                self.pid,
                self.config.get("env", {}).get("ANTHROPIC_BASE_URL", "")
                if self.backend == "claude"
                else "",
                self.model,
                json.dumps(stored_result, ensure_ascii=False),
                int(time.time()),
            ),
        )

        mcp_rows = stored_result.get("mcp_telemetry")
        if isinstance(mcp_rows, list):
            for row in mcp_rows[:100]:
                if not isinstance(row, dict):
                    continue
                kind = str(row.get("kind") or "mcp_signal")
                source_event_type = str(row.get("source_event_type") or "")
                summary = str(row.get("summary") or "").strip()
                details_payload = {
                    "kind": kind,
                    "source_event_type": source_event_type,
                    "summary": summary,
                    "event": row.get("event"),
                }
                details_payload = redact_mapping(details_payload)
                cur.execute(
                    """
                    INSERT INTO events (event_id, task_id, event_type, profile, pid,
                                      base_url, model, details, created_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                    (
                        str(uuid.uuid4()),
                        task_id,
                        kind,
                        self.profile,
                        self.pid,
                        self.config.get("env", {}).get("ANTHROPIC_BASE_URL", "")
                        if self.backend == "claude"
                        else "",
                        self.model,
                        json.dumps(details_payload, ensure_ascii=False),
                        int(time.time()),
                    ),
                )

        conn.commit()

    @staticmethod
    def _extract_json_events(stdout_text: str, stderr_text: str) -> list[dict]:
        events: list[dict] = []
        for raw in (stdout_text, stderr_text):
            if not raw:
                continue
            for line in raw.splitlines():
                line = line.strip()
                if not line.startswith("{") or not line.endswith("}"):
                    continue
                try:
                    payload = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(payload, dict) and payload.get("type"):
                    events.append(payload)
        return events

    def _cc_backend_request_headers(self, api_format, session_id=None):
        provider = getattr(self, "auth_headers_provider", None)
        if provider is not None:
            headers = provider(self.config, api_format)
        else:
            headers = _resolve_cc_backend_auth_headers(self.config, api_format=api_format)
        from urllib.parse import urlsplit
        endpoint = urlsplit(str(self.config.get("base_url") or ""))
        if session_id and endpoint.hostname == "opencode.ai" and endpoint.path.rstrip("/") == "/zen/go/v1":
            headers = dict(headers or {})
            headers["x-opencode-session"] = str(session_id)
            headers["User-Agent"] = "IgorekDesktop/agent"
        return headers

    def _execute_cc_backend(
        self,
        task: dict,
        workdir: str,
        runtime_workdir: str,
        task_text: str,
        skills_text: str,
        foundation_context: str = "",
        start_time: float | None = None,
    ) -> dict:
        """Execute task via CC backend with full tool-use loop."""
        try:
            from .cc_backend_executor import execute_cc_backend_task
        except ImportError:
            from cc_backend_executor import execute_cc_backend_task

        task_id = task["task_id"]
        isolation_active = getattr(self, "isolation_manager", None) is not None or _isolation_kvm_requested()
        task_metadata = (
            task.get("metadata") if isinstance(task.get("metadata"), dict) else {}
        )
        base_url = str(self.config.get("base_url", "http://127.0.0.1:8001")).rstrip("/")
        provider = str(self.config.get("provider") or "").strip().lower()
        # For remote providers, we directly take the model from the profile/session override,
        # _resolve_cc_backend_model only for local CC backend.
        model = str(task_metadata.get('model_override') or self.model).strip() if self.config.get('api_format') == 'openai-responses' else _effective_cc_backend_model(
            provider=provider,
            configured_model=self.model,
            task_metadata=task_metadata,
            base_url=base_url,
        )
        api_format = _resolve_cc_backend_api_format(self.config, model)
        level_runtime = _resolve_level_runtime(self.config, task_metadata)
        filesystem_mode = level_runtime["filesystem_mode"]
        guard_mode = level_runtime["guard_mode"]
        if task_metadata.get('demo_workspace'):
            filesystem_mode = 'workspace'
            foundation_context = ''
            skills_text = ''
        checkpoint_config = self.config.get(
            "checkpoints", {"enabled": True, "max_snapshots": 50}
        )
        max_tokens = task.get("limits_max_tokens", 4000)
        timeout_s = task.get("limits_timeout_s", 1200)
        input_files = task.get("input_files") or []
        vault_env = self._materialize_vault_env(
            task_id=task_id,
            task_metadata=task_metadata,
        )

        with _temporary_env(vault_env), scoped_web_tools(getattr(self, "web_tools_provider", None)):
            # Extract raw user message from factory wrapper
            raw_message = _extract_raw_user_message(task_text)

            structured_history = task_metadata.get("structured_session_history") is True
            if structured_history:
                raw_message = str(task_metadata.get("session_user_message") or task_metadata.get("original_message_text") or raw_message)
            user_message = raw_message
            mcp_reload_results = _reload_enabled_mcp_servers_for_cc_backend(
                self.profile
            )
            if mcp_reload_results:
                summary = ", ".join(
                    f"{item.get('server_name')}:{item.get('status')}:{len(item.get('tools') or [])}"
                    for item in mcp_reload_results
                    if isinstance(item, dict)
                )
                print(f"[{self.profile}] MCP hot reload before tools: {summary}")
            tool_schemas = get_tool_schemas()

            # Profile-driven toolset filtering
            profile_toolsets = _default_cc_backend_toolsets(self.config)
            if profile_toolsets:
                tool_schemas = _filter_by_toolsets(tool_schemas, profile_toolsets)

            stable_prefix = _stable_prompt_prefix(self.config)
            interactive_input_enabled = _task_supports_interactive_input(task_metadata)
            # Plan mode (web UI «Plan» / Android optional flag): prompt layer in
            # prompt_assembly plus a structural read-only toolset below.
            plan_mode = task_metadata.get("plan_mode") is True
            if not stable_prefix and not interactive_input_enabled:
                tool_schemas = _without_tool(tool_schemas, "request_user_input")

            explicit_skill_names: list[str] = []
            if SKILLS_AVAILABLE and skills_manager.is_loaded and not task_metadata.get('opencode_result_for'):
                explicit_skills = skills_manager.find_explicitly_mentioned_skills(
                    raw_message,
                    backend=self.backend,
                )
                explicit_skill_names = [skill.name for skill in explicit_skills]
                if explicit_skill_names:
                    if stable_prefix:
                        # Keep the wire toolset byte-stable for KV-cache reuse;
                        # enforce the same restrictions at execution time.
                        if any(
                            name == "igorek-self-inspection"
                            for name in explicit_skill_names
                        ):
                            task_metadata["restrict_tools_to"] = [
                                "Read",
                                "Grep",
                                "Glob",
                            ]
                        else:
                            task_metadata["block_tool_names"] = ["delegate_task"]
                    else:
                        tool_schemas = _without_tool(tool_schemas, "delegate_task")
                        print(
                            f"[{self.profile}] delegate_task disabled for explicit skill request: "
                            f"{', '.join(explicit_skill_names)}"
                        )
                        if any(name == "igorek-self-inspection" for name in explicit_skill_names):
                            tool_schemas = _only_tools(tool_schemas, {"Read", "Grep", "Glob"})
                            print(
                                f"[{self.profile}] read-only tools enforced for igorek-self-inspection"
                            )
                    must_write_skills = [
                        skill.name
                        for skill in explicit_skills
                        if getattr(skill, "must_write", False)
                    ]
                    if must_write_skills:
                        task_metadata["must_write_skills"] = must_write_skills

            if plan_mode:
                task_metadata["restrict_tools_to"] = [
                    "Read",
                    "Grep",
                    "Glob",
                    "update_plan",
                ]
                task_metadata.pop("must_write_skills", None)
                print(
                    f"[{self.profile}] plan mode: read-only toolset enforced (metadata plan_mode=true)"
                )

            available_tool_names = {
                str(schema.get("name") or "").strip()
                for schema in tool_schemas
                if str(schema.get("name") or "").strip()
            }

            if task_metadata.get('demo_workspace'):
                from .mobile_bridge.demo import SAFE_TOOLS
                allowed_demo_tools = set(task_metadata.get('restrict_tools_to') or SAFE_TOOLS)
                tool_schemas = _only_tools(tool_schemas, allowed_demo_tools & SAFE_TOOLS)
                available_tool_names = {str(schema.get('name')) for schema in tool_schemas}

            # Put context/history into system prompt, raw message as user content
            mcp_tool_names = [
                s.get("name", "")
                for s in tool_schemas
                if s.get("name", "")
                and s.get("name", "")
                not in {
                    "Write",
                    "Read",
                    "Edit",
                    "Bash",
                    "Glob",
                    "Grep",
                    "SessionSearch",
                    "web_search",
                    "web_extract",
                    "request_user_input",
                    "suggest_enable_mcp",
                }
            ]
            disabled_mcp_capabilities = list_mcp_capabilities_for_profile(
                self.profile,
                enabled=False,
            )
            trailing_context_text = ""
            prompt_workdir = _isolation_prompt_workdir(workdir, isolation_active)
            if stable_prefix:
                # Session-stable prefix: keep foundation memory in the static
                # system prompt (it changes rarely) and drop per-message selected
                # skills so the prefix stays append-only across turns. Skills
                # remain discoverable through skills_list/skill_view tools.
                system_prompt = build_cc_backend_system_prompt(
                    workdir=prompt_workdir,
                    filesystem_mode=filesystem_mode,
                    model_name=model,
                    foundation_context=foundation_context.strip(),
                    skills_text="",
                    wrapped_task_context="",
                    web_tools_enabled="web_search" in available_tool_names,
                    web_extract_enabled="web_extract" in available_tool_names,
                    mcp_tool_names=mcp_tool_names or None,
                    disabled_mcp_capabilities=disabled_mcp_capabilities or None,
                    interactive_input_enabled=interactive_input_enabled,
                    prompt_style=_cc_backend_prompt_style(self.config),
                    source_platform=str(task_metadata.get("platform") or "").strip() or None,
                    plan_mode=plan_mode,
                )
            else:
                system_prompt = build_cc_backend_system_prompt(
                    workdir=prompt_workdir,
                    filesystem_mode=filesystem_mode,
                    model_name=model,
                    foundation_context=foundation_context.strip(),
                    skills_text=skills_text.strip(),
                    wrapped_task_context=task_text if raw_message != task_text and not structured_history else "",
                    web_tools_enabled="web_search" in available_tool_names,
                    web_extract_enabled="web_extract" in available_tool_names,
                    mcp_tool_names=mcp_tool_names or None,
                    disabled_mcp_capabilities=disabled_mcp_capabilities or None,
                    interactive_input_enabled=interactive_input_enabled,
                    prompt_style=_cc_backend_prompt_style(self.config),
                    source_platform=str(task_metadata.get("platform") or "").strip() or None,
                    plan_mode=plan_mode,
                )

            project_instructions = _read_project_instructions(workdir)
            if task_metadata.get('desktop_ui') or task_metadata.get('demo_workspace'):
                from .prompt_assembly import desktop_navigation_prompt
                system_prompt += '\n\n' + desktop_navigation_prompt()
            if project_instructions:
                system_prompt += "\n\n" + project_instructions
                print(f"[{self.profile}] Project instructions added: AGENTS.md/CLAUDE.md ({len(project_instructions)} chars)")

            if start_time is None:
                start_time = time.time()

            def log_fn(msg: str) -> None:
                print(f"[{self.profile}] {msg}")

            # Match desktop chat by delivering new mobile session messages to
            # the active turn without allocating another task ID.
            pending_steer_provider = None
            priority_steer_provider = None
            approvals_enabled_provider = None
            if str(task_metadata.get("source") or "") == "mobile_bridge" and task_id:
                try:
                    from .mobile_approval_settings import mobile_approvals_enabled
                    from .steer_queue import make_priority_steer_provider, make_steer_provider

                    pending_steer_provider = make_steer_provider(task_id)
                    priority_steer_provider = make_priority_steer_provider(task_id)
                    approvals_enabled_provider = mobile_approvals_enabled
                except Exception as exc:
                    log_fn(f"Mobile runtime controls are unavailable: {exc}")

            vault_writer = getattr(self, 'vault_value_writer', None)
            if vault_writer is None:
                tool_schemas = [tool for tool in tool_schemas if tool.get('name') != 'VaultStore']
            executor_kwargs: dict[str, Any] = {
                "base_url": base_url,
                "model": model,
                "system_prompt": system_prompt,
                "user_message": user_message,
                "input_files": input_files if isinstance(input_files, list) else [],
                "workdir": workdir,
                "artifact_workdir": runtime_workdir,
                "filesystem_mode": filesystem_mode,
                "guard_mode": guard_mode,
                "checkpoint_config": checkpoint_config
                if isinstance(checkpoint_config, dict)
                else None,
                "current_session": str(task_metadata.get("session") or "").strip() or None,
                "source_platform": str(task_metadata.get("platform") or "").strip() or None,
                "source_chat_id": str(task_metadata.get("chat_id") or "").strip() or None,
                "max_tokens": max_tokens,
                "timeout_s": timeout_s,
                "log_fn": log_fn,
                "enabled_tools": tool_schemas,
                "metadata": task_metadata,
                "history_messages": (
                    task_metadata.get("history_messages")
                    if (stable_prefix or structured_history)
                    and isinstance(task_metadata.get("history_messages"), list)
                    else None
                ),
                "trailing_context_text": trailing_context_text,
                "api_format": api_format,
                "request_headers": self._cc_backend_request_headers(api_format, task_metadata.get("session")),
                "task_id": task_id,
                "profile": self.profile,
                "pending_steer_provider": pending_steer_provider,
                "priority_steer_provider": priority_steer_provider,
                "approvals_enabled_provider": approvals_enabled_provider,
            }
            if api_format == "openai":
                from .runtime import profile_llama_cpp_reasoning_format

                reasoning_format = profile_llama_cpp_reasoning_format(self.config)
                if reasoning_format is not None:
                    executor_kwargs["reasoning_format"] = reasoning_format
            if vault_writer is not None:
                executor_kwargs['vault_store_provider'] = lambda args: vault_writer.save(args,
                    workdir=workdir, task_id=task_id, session_id=str(task_metadata.get('session') or ''), profile=self.profile)
            if api_format == 'openai-responses' and not isolation_active:
                executor_kwargs['request_headers_provider'] = lambda: self._cc_backend_request_headers(api_format, task_metadata.get('session'))
            if isolation_active:
                isolated_metadata = dict(executor_kwargs["metadata"])
                try:
                    from .cc_backend_executor import resolve_host_context_window

                    isolated_metadata["model_context_window"] = (
                        resolve_host_context_window(
                            base_url, model, api_format=api_format
                        )
                    )
                except Exception as exc:  # host resolution boundary: guest uses its fallback
                    log_fn(f"[isolation] context window resolution failed: {exc}")
                try:
                    result = self._execute_cc_backend_isolated(
                        task=task,
                        workdir=workdir,
                        payload={
                            **executor_kwargs,
                            "metadata": isolated_metadata,
                            "skills_text": skills_text,
                            "foundation_context": foundation_context,
                        },
                    )
                except IsolationError as exc:
                    return {
                        "task_id": task_id,
                        "status": "fail",
                        "error": f"isolation_unavailable: {exc}",
                        "usage": {"prompt": 0, "completion": 0, "lat_ms_p50": 0},
                    }
                if str(result.get("status") or "") == "fail":
                    return _isolation_failure_result(result, task_id=task_id)
            else:
                result = execute_cc_backend_task(**executor_kwargs)
        escalation_route = resolve_escalation_route(
            failure_reason=str(result.get("failure_reason") or ""),
            config=self.config,
        )
        if (
            isolation_active
            and result.get("status") == "fail"
            and result.get("failure_reason") != "approval_required"
        ):
            log_fn("[isolation] host-side escalation stays disabled for kvm sessions")
        if (
            result.get("status") == "fail"
            and result.get("failure_reason") != "approval_required"
            and not isolation_active
            and escalation_route is not None
            and not task_metadata.get('demo_workspace')
        ):
            log_fn(
                "Smart routing escalation: "
                f"reason={escalation_route.reason} provider={escalation_route.provider} "
                f"model={escalation_route.model}"
            )
            escalated = self._execute_codex_escalation(
                task=task,
                workdir=workdir,
                route=escalation_route,
                handoff_text=build_escalation_handoff(
                    user_message=user_message,
                    failure_reason=escalation_route.reason,
                    failure_details=str(
                        result.get("failure_details") or result.get("output_text") or ""
                    ),
                    primary_model=model,
                ),
                skills_text=skills_text,
                foundation_context=foundation_context,
                timeout_s=timeout_s,
                start_time=start_time,
            )
            if escalated.get("status") == "ok":
                output_payload = escalated.get("output")
                if isinstance(output_payload, dict):
                    text = str(output_payload.get("text") or "").strip()
                    if text:
                        output_payload["text"] = (
                            "Локальная модель упёрлась в ошибку вызова инструмента. "
                            f"Переключился на OpenRouter ({escalation_route.model}) и продолжил.\n\n"
                            f"{text}"
                        )
                return escalated
            result["escalation"] = {
                "status": escalated.get("status"),
                "provider": escalation_route.provider,
                "model": escalation_route.model,
                "reason": escalation_route.reason,
                "error": escalated.get("error")
                or escalated.get("output", {}).get("stderr", ""),
            }

        cc_usage = result.get("usage") if isinstance(result.get("usage"), dict) else {}
        usage_ext: dict[str, Any] = {}
        for k in (
            "total_tokens",
            "model_context_window",
            "current_usage",
            "input_tokens",
            "output_tokens",
            "cache_creation_input_tokens",
            "cache_read_input_tokens",
        ):
            if k in cc_usage:
                usage_ext[k] = cc_usage[k]
        if not usage_ext.get("input_tokens"):
            usage_ext["input_tokens"] = cc_usage.get("prompt", 0)
        if not usage_ext.get("output_tokens"):
            usage_ext["output_tokens"] = cc_usage.get("completion", 0)
        cost_usd = _estimate_openrouter_task_cost(
            provider=str(self.config.get("provider") or ""),
            model=model,
            usage=usage_ext or cc_usage,
        )

        return {
            "task_id": task_id,
            "status": result["status"],
            "output": {"text": result["output_text"]},
            **({'error': str(result.get('failure_details') or result.get('error') or result.get('output_text') or ''),
                'failure_reason': str(result.get('failure_reason') or ''),
                **({'diagnostic': result['diagnostic']} if isinstance(result.get('diagnostic'),dict) else {})}
               if result.get('status')=='fail' else {}),
            "usage": cc_usage,
            "usage_ext": usage_ext if usage_ext else None,
            "cost_usd": cost_usd,
            "approval_blocked_tools": result.get("approval_blocked_tools", []),
        }

    def _execute_cc_backend_isolated(
        self,
        *,
        task: dict,
        workdir: str,
        payload: dict[str, Any],
    ) -> dict:
        """Dispatch one turn to the session VM and recover one lost approval park.

        The caller normalizes a terminal ``fail`` into the worker result. When
        the guest reports ``waiting_approval`` without a deliverable approval
        record, retry once with recovery context so the model can inspect the
        workspace and continue safely.
        """
        manager = getattr(self, "isolation_manager", None)
        if manager is None:
            manager = _get_isolation_manager()
        cancel_check = _isolation_cancel_check(str(task.get("task_id") or ""))
        result = manager.run_task(
            task=task,
            workdir=workdir,
            payload=payload,
            cancel_check=cancel_check,
        )
        print(
            f"[{self.profile}] isolation dispatch done: status={result.get('status')}"
        )
        if (
            result.get("status") != "fail"
            or result.get("failure_reason") != "approval_request_missing"
            or cancel_check()
        ):
            return result

        recovery_payload = dict(payload)
        recovery_metadata = payload.get("metadata")
        if isinstance(recovery_metadata, dict):
            recovery_metadata = dict(recovery_metadata)
            recovery_metadata.pop("forced_tool_call", None)
            recovery_metadata.pop("approved_tool_calls", None)
            recovery_payload["metadata"] = recovery_metadata
        recovery_note = build_isolation_approval_recovery_context()
        recovery_payload["system_prompt"] = (
            f"{str(payload.get('system_prompt') or '').rstrip()}\n\n{recovery_note}"
        ).strip()
        recovery_task_id = str(task.get("task_id") or payload.get("task_id") or "").strip()
        if recovery_task_id:
            _append_codex_trace_event(
                _trace_path_for_task(recovery_task_id),
                trace_event(
                    CcTraceEvent.ISOLATION_RECOVERY_STARTED,
                    ts=int(time.time()),
                    attempt=1,
                    max_attempts=1,
                    reason="approval_request_missing",
                ),
            )
        print(
            f"[{self.profile}] approval record missing; retrying once with recovery context"
        )
        recovered = manager.run_task(
            task=task,
            workdir=workdir,
            payload=recovery_payload,
            cancel_check=cancel_check,
        )
        previous_usage = result.get("usage")
        recovered_usage = recovered.get("usage")
        if isinstance(previous_usage, dict) and isinstance(recovered_usage, dict):
            combined_usage = dict(previous_usage)
            combined_usage.update(recovered_usage)
            for key in ("prompt", "completion"):
                before = previous_usage.get(key)
                after = recovered_usage.get(key)
                if (
                    isinstance(before, (int, float))
                    and not isinstance(before, bool)
                    and isinstance(after, (int, float))
                    and not isinstance(after, bool)
                ):
                    combined_usage[key] = before + after
            recovered["usage"] = combined_usage
        if (
            recovered.get("status") == "fail"
            and recovered.get("failure_reason") == "approval_request_missing"
        ):
            recovered["failure_details"] = (
                "После одной попытки автоматического восстановления guest снова вернул "
                "waiting_approval без записи заблокированного действия. "
                "Автоматическое продолжение остановлено."
            )
            recovered["output_text"] = (
                "Не удалось восстановить запрос подтверждения после одной попытки."
            )
        if recovery_task_id:
            _append_codex_trace_event(
                _trace_path_for_task(recovery_task_id),
                trace_event(
                    CcTraceEvent.ISOLATION_RECOVERY_FINISHED,
                    ts=int(time.time()),
                    attempt=1,
                    status=str(recovered.get("status") or ""),
                    failure_reason=str(recovered.get("failure_reason") or ""),
                    error_message=str(
                        recovered.get("failure_details") or recovered.get("error") or ""
                    ),
                ),
            )
        print(
            f"[{self.profile}] isolation recovery dispatch done: "
            f"status={recovered.get('status')}"
        )
        return recovered

    def _execute_codex_escalation(
        self,
        *,
        task: dict,
        workdir: str,
        route,
        handoff_text: str,
        skills_text: str,
        foundation_context: str,
        timeout_s: int,
        start_time: float | None,
    ) -> dict:
        task_id = task["task_id"]
        started_at = start_time if start_time is not None else time.time()

        def log_fn(msg: str) -> None:
            print(f"[{self.profile}] {msg}")

        command_profile = {
            "backend": "codex",
            "provider": route.provider,
            "model": route.model,
            "reasoning_effort": "high",
            "env": self.config.get("env")
            if isinstance(self.config.get("env"), dict)
            else {},
            "args": [],
        }
        codex_settings = dict(resolve_codex_launch_settings(command_profile))
        env = os.environ.copy()
        env.update(
            codex_settings.get("env")
            if isinstance(codex_settings.get("env"), dict)
            else {}
        )
        vault_env = self._materialize_vault_env(
            task_id=task_id,
            task_metadata=task.get("metadata")
            if isinstance(task.get("metadata"), dict)
            else None,
        )
        env.update(vault_env)
        backend_input = build_codex_backend_input(
            workdir=workdir,
            profile=self.profile,
            stage=str(task.get("stage", "main")),
            task_text=handoff_text,
            capabilities=task.get("capabilities", []) or [],
            input_files=task.get("input_files", []) or [],
            skills_text=skills_text.strip(),
            foundation_context=foundation_context.strip(),
        )
        command = build_backend_command(command_profile, workdir=workdir)
        if "--json" not in command:
            try:
                exec_index = command.index("exec")
                command.insert(exec_index + 1, "--json")
            except ValueError:
                command.append("--json")

        log_fn(
            f"[cc-backend] Escalation starting: {route.provider}/{route.model}, "
            f"timeout={timeout_s}s"
        )

        # Run security_runtime.execute_command in a background thread so we
        # can emit periodic progress logs while the subprocess works.
        _result_box: list = [None]   # [proc, session_id] on success
        _exc_box: list = [None]      # exception on failure
        codex_trace_path = _trace_path_for_task(task_id)

        def _codex_stdout_line_callback(line: str) -> None:
            for payload in _codex_tool_event_payloads(line):
                _append_codex_trace_event(codex_trace_path, payload)

        def _run_subprocess():
            try:
                proc, sid = self.security_runtime.execute_command(
                    command=command,
                    input_payload=backend_input,
                    env=env,
                    cwd=workdir,
                    timeout_s=timeout_s,
                    task_id=task_id,
                    profile=self.profile,
                    backend="codex",
                    model=route.model,
                    provider=route.provider,
                    task_metadata={
                        "smart_routing_escalation": True,
                        "escalation_reason": route.reason,
                        "primary_backend": self.backend,
                        "primary_model": self.model,
                    },
                    stdout_line_callback=_codex_stdout_line_callback,
                )
                _result_box[0] = (proc, sid)
            except Exception as exc:
                _exc_box[0] = exc

        worker_thread = threading.Thread(target=_run_subprocess, daemon=True)
        worker_thread.start()

        progress_interval = 15  # seconds between progress logs
        elapsed = 0
        while worker_thread.is_alive():
            worker_thread.join(timeout=progress_interval)
            if worker_thread.is_alive():
                elapsed += progress_interval
                log_fn(
                    f"[cc-backend] Escalation still running... ({elapsed}s elapsed, "
                    f"model={route.model})"
                )
            else:
                break

        total_elapsed = int(time.time() - started_at)

        if _exc_box[0] is not None:
            exc = _exc_box[0]
            if isinstance(exc, subprocess.TimeoutExpired):
                log_fn(
                    f"[cc-backend] Escalation timed out after {timeout_s}s"
                )
                return {
                    "task_id": task_id,
                    "status": "fail",
                    "error": f"Escalation timeout after {timeout_s}s",
                    "usage": {
                        "prompt": 0,
                        "completion": 0,
                        "lat_ms_p50": int(total_elapsed * 1000),
                    },
                }
            log_fn(f"[cc-backend] Escalation failed: {exc}")
            return {
                "task_id": task_id,
                "status": "fail",
                "error": f"Escalation failed: {exc}",
                "usage": {
                    "prompt": 0,
                    "completion": 0,
                    "lat_ms_p50": int(total_elapsed * 1000),
                },
            }

        proc, security_session_id = _result_box[0]
        log_fn(
            f"[cc-backend] Escalation subprocess finished: "
            f"rc={proc.returncode}, {total_elapsed}s elapsed"
        )

        lat_ms = int((time.time() - started_at) * 1000)
        parsed_output = extract_codex_final_text(proc.stdout or "")
        parsed_usage = extract_codex_usage(proc.stdout or "")
        codex_tool_events = extract_codex_tool_events(proc.stdout or "")
        output_text = parsed_output if parsed_output else (proc.stdout or "")
        result = {
            "task_id": task_id,
            "status": "ok" if proc.returncode == 0 else "fail",
            "output": {"text": output_text, "stderr": proc.stderr},
            "usage": {
                "prompt": parsed_usage.get("prompt", 0),
                "completion": parsed_usage.get("completion", 0),
                "lat_ms_p50": lat_ms,
            },
            "security": {
                "runtime_mode": self.security_runtime.mode,
                "session_id": security_session_id,
            },
            "smart_routing": {
                "escalated": True,
                "reason": route.reason,
                "provider": route.provider,
                "model": route.model,
            },
        }
        if parsed_usage:
            result["usage_ext"] = parsed_usage
            cost_usd = _estimate_openrouter_task_cost(
                provider=route.provider,
                model=route.model,
                usage=parsed_usage,
            )
            if cost_usd > 0:
                result["cost_usd"] = cost_usd
        if codex_tool_events:
            result["codex_tool_events"] = [
                {
                    "event_type": event.event_type,
                    "call_id": event.call_id,
                    "name": event.name,
                    "arguments": event.arguments,
                    "output_preview": event.output_preview,
                    "output_chars": event.output_chars,
                    "exit_code": event.exit_code,
                    "summary": event.summary,
                }
                for event in codex_tool_events
            ]
        return result

    @staticmethod
    def _extract_claude_final_text(stdout_text: str) -> str:
        final_text = ""
        events = Worker._extract_json_events(stdout_text, "")
        for payload in events:
            payload_type = str(payload.get("type") or "")
            if payload_type == "result":
                result_text = payload.get("result")
                if isinstance(result_text, str) and result_text.strip():
                    final_text = result_text.strip()
            elif payload_type == "assistant":
                message = payload.get("message")
                if not isinstance(message, dict):
                    continue
                content = message.get("content")
                if not isinstance(content, list):
                    continue
                texts: list[str] = []
                for item in content:
                    if not isinstance(item, dict):
                        continue
                    if str(item.get("type") or "") != "text":
                        continue
                    text = item.get("text")
                    if isinstance(text, str) and text.strip():
                        texts.append(text.strip())
                if texts:
                    final_text = "\n".join(texts)
        return final_text

    def _collect_codex_mcp_telemetry(
        self,
        *,
        task_id: str,
        stdout_text: str,
        stderr_text: str,
        enabled_servers: list[str],
    ) -> list[dict]:
        events = self._extract_json_events(stdout_text, stderr_text)
        if not events:
            return []

        telemetry: list[dict] = []
        server_tokens = [name.lower() for name in enabled_servers if str(name).strip()]
        for event in events:
            event_type = str(event.get("type") or "").strip()
            if not event_type:
                continue
            event_dump = json.dumps(event, ensure_ascii=False).lower()
            has_mcp_hint = "mcp" in event_dump or any(
                token and token in event_dump for token in server_tokens
            )
            if not has_mcp_hint:
                continue

            lower_type = event_type.lower()
            if "error" in lower_type or "fail" in lower_type:
                kind = "mcp_error"
            elif "tool" in lower_type or "call" in lower_type:
                kind = "mcp_tool_call"
            else:
                kind = "mcp_signal"

            summary_parts = [event_type]
            message = event.get("message")
            if isinstance(message, str) and message.strip():
                compact = re.sub(r"\s+", " ", message).strip()
                summary_parts.append(compact[:180])
            summary = " | ".join(summary_parts)

            telemetry.append(
                {
                    "task_id": task_id,
                    "kind": kind,
                    "source_event_type": event_type,
                    "summary": summary,
                    "event": event,
                }
            )

        return telemetry

    def _collect_claude_mcp_telemetry(
        self,
        *,
        task_id: str,
        stdout_text: str,
        stderr_text: str,
        enabled_servers: list[str],
    ) -> list[dict]:
        events = self._extract_json_events(stdout_text, stderr_text)
        if not events:
            return []

        telemetry: list[dict] = []
        server_tokens = [name.lower() for name in enabled_servers if str(name).strip()]
        for event in events:
            event_type = str(event.get("type") or "").strip()
            if not event_type:
                continue
            event_dump = json.dumps(event, ensure_ascii=False).lower()

            has_tool_hint = "tool_use" in event_dump or "mcp" in event_dump
            has_server_hint = any(
                token and token in event_dump for token in server_tokens
            )
            if not (has_tool_hint or has_server_hint):
                continue

            kind = "mcp_signal"
            is_result_error = bool(event.get("is_error") is True)
            if "error" in event_type.lower() or is_result_error:
                kind = "mcp_error"
            elif "tool_use" in event_dump or "parent_tool_use_id" in event_dump:
                kind = "mcp_tool_call"

            summary = event_type
            if has_server_hint:
                matched = [token for token in server_tokens if token in event_dump]
                if matched:
                    summary = f"{summary} | servers={','.join(matched[:3])}"

            telemetry.append(
                {
                    "task_id": task_id,
                    "kind": kind,
                    "source_event_type": event_type,
                    "summary": summary,
                    "event": event,
                }
            )
        return telemetry

    def route_next(self, conn, task: dict, result: dict):
        """Create next tasks based on result"""
        propose_next = result.get("propose_next") or []
        task_next = task.get("next_profiles") or []

        # Merge proposed and predefined next steps
        next_profiles = list(set(propose_next + task_next))

        if not next_profiles:
            return

        # Create child task for each next profile
        for profile in next_profiles:
            child_id = str(uuid.uuid4())
            cur = conn.cursor()
            cur.execute(
                """
                INSERT INTO tasks (
                    task_id, parent_id, stage, status, profile,
                    input_text, input_files, limits_max_tokens,
                    limits_timeout_s, limits_budget_usd, created_at, updated_at
                ) VALUES (?, ?, ?, 'queued', ?, ?, ?, ?, ?, ?, ?, ?)
            """,
                (
                    child_id,
                    task["task_id"],
                    task["stage"],
                    profile,
                    result.get("output", {}).get("text", ""),
                    json.dumps(result.get("output", {}).get("files", [])),
                    task.get("limits_max_tokens", 4000),
                    task.get("limits_timeout_s", 1200),
                    task.get("limits_budget_usd", 0.02),
                    int(time.time()),
                    int(time.time()),
                ),
            )

            conn.commit()

    def _schema_file_path(self) -> str:
        return DB_PATH

    def _ensure_db_schema(self, conn: sqlite3.Connection):
        """Ensure required tables exist, create/apply schema if missing."""
        ensure_schema(conn)

    def cleanup_stuck_tasks(self, timeout_seconds=300):
        """Finalize stale tasks for this worker's profile without deleting rows.

        Structural invariants:

        1. Own profile only. ``claim_task`` filters by `profile`, so cleanup must
           use the same scope. Another profile's live worker may own its task.
        2. Preserve the row. Deleting it while a worker still runs hides it from
           ``TraceJournaler._db_tasks``, removes client cards and prevents normal
           cancellation. Publish terminal state through ``task.status``.
        3. ``_touch_task`` refreshes ``updated_at`` while ``execute_task`` is alive.
           Clean up only tasks whose process stopped providing that heartbeat.
        4. Cover both transitional statuses:
           * stale `processing` becomes `dead_letter`;
           * stale `cancelling` becomes `fail` with `cancelled by user`.

        ``claim_task`` does not select `cancelling`. Without cleanup, a dead
        executor leaves that row active forever. The mobile bridge's
        ``ACTIVE_STATUSES`` then keeps showing a stopping session and steers new
        messages into a task that can no longer consume them.
        """
        try:
            with open_db(DB_PATH) as conn:
                self._ensure_db_schema(conn)
                cursor = conn.cursor()

                # Find stuck tasks of your profile in transit statuses.
                cursor.execute(
                    """
                    SELECT task_id, profile, status, input_text, created_at
                    FROM tasks
                    WHERE status IN ('processing', 'cancelling')
                      AND profile = ?
                      AND updated_at < strftime('%s', 'now') - ?
                    """,
                    (self.profile, timeout_seconds),
                )

                stuck_tasks = cursor.fetchall()

                if not stuck_tasks:
                    return

                print(f"🧹[{self.profile}] Найдено {len(stuck_tasks)} зависших задач:")

                for task_id, profile, status, input_text, created_at in stuck_tasks:
                    # Show task details
                    text_preview = (
                        input_text[:100] + "..."
                        if len(input_text) > 100
                        else input_text
                    )
                    print(f"  ⏱️  {task_id[:8]}... [{profile}/{status}] {text_preview}")

                    if str(status) == "cancelling":
                        # Cancellation requested by the user, but the executor does not provide
                        # signs of life: finalize the row so that the client can unlock and the session
                        # stops being considered active.
                        cursor.execute(
                            """
                            UPDATE tasks
                            SET status = 'fail',
                                error_message = 'cancelled by user',
                                completed_at = strftime('%s', 'now'),
                                updated_at = strftime('%s', 'now')
                            WHERE task_id = ?
                              AND status = 'cancelling'
                            """,
                            (task_id,),
                        )
                    else:
                        # Marks as dead letter, saves the record.
                        cursor.execute(
                            """
                            UPDATE tasks
                            SET status = 'dead_letter',
                                error_message = ?,
                                completed_at = strftime('%s', 'now'),
                                updated_at = strftime('%s', 'now')
                            WHERE task_id = ?
                              AND status = 'processing'
                            """,
                            (
                                "worker_cleanup_stuck_task: no heartbeat for "
                                f"{timeout_seconds}s (profile {self.profile})",
                                task_id,
                            ),
                        )

                conn.commit()
                print(
                    f"✅[{self.profile}] {len(stuck_tasks)} зависших задач финализировано"
                )

        except Exception as e:
            print(f"❌[{self.profile}] Ошибка очистки зависших задач: {e}")

    def _touch_task(self, task_id: str) -> bool:
        """Heartbeat: refresh updated_at for a live task in a transitional status.

        Cover both states in which execution is still in progress:
        * `processing`: ordinary work;
        * `cancelling`: Stop was requested and the executor is cooperatively
          finishing its round. Cleanup must not finalize it before the executor
          can persist its terminal state.

        Return False if the row is missing or already terminal. That is the
        structural signal that execution should stop.
        """
        try:
            with open_db(DB_PATH) as conn:
                cursor = conn.cursor()
                cursor.execute(
                    """
                    UPDATE tasks
                    SET updated_at = strftime('%s', 'now')
                    WHERE task_id = ?
                      AND status IN ('processing', 'cancelling')
                    """,
                    (task_id,),
                )
                conn.commit()
                return cursor.rowcount > 0
        except Exception:
            # Heartbeat should not fail to execute the task: write failure
            # handled silently; next attempt will occur after an interval.
            return True

    def _task_heartbeat_loop(self, task_id: str, stop_event: threading.Event) -> None:
        """# Background heartbeat loop, runs exactly as long as the task is active"""
        while not stop_event.wait(self.heartbeat_interval):
            self._touch_task(task_id)

    def _execute_task_with_heartbeat(self, conn, task: dict) -> dict:
        """# Executes the task while maintaining its `updated_at` timestamp (heartbeat).

        Execution may exceed `cleanup_interval`: long model rounds, waiting for sudo password via phone, heavy Bash commands. As long as the task is actively running, `updated_at` must be updated; otherwise, `cleanup_stuck_tasks` will classify the live process as stuck.
        """
        task_id = str(task.get("task_id") or "")
        stop_event = threading.Event()
        heartbeat = threading.Thread(
            target=self._task_heartbeat_loop,
            args=(task_id, stop_event),
            name=f"heartbeat-{task_id[:8]}",
            daemon=True,
        )
        heartbeat.start()
        try:
            return self.execute_task(task)
        finally:
            stop_event.set()
            heartbeat.join(timeout=1.0)

    def run(self, *, stop_event=None, execution_lock=None):
        from .worker_presence import WorkerPresence
        with WorkerPresence(self.profile, getattr(self, "config", {}), ready=False) as presence:
            self._presence = presence
            try:
                return self._run(stop_event=stop_event, execution_lock=execution_lock)
            finally:
                self._presence = None

    def _run(self, *, stop_event=None, execution_lock=None):
        """Main worker loop"""
        print(f"[{self.profile}] Worker started (PID: {self.pid})", flush=True)
        if self.backend == "cc-backend" and (getattr(self, "isolation_manager", None) is not None or os.environ.get("IGOREK_ISOLATION_MODE") == "kvm"):
            from .isolation.terminal_owner import TerminalOwnerServer
            manager = getattr(self, "isolation_manager", None)
            self._terminal_owner = TerminalOwnerServer(self.profile, manager if manager is not None else _get_isolation_manager())

        # Initialize database (create file if missing, and always ensure schema exists)
        os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
        with open_db(DB_PATH) as conn:
            self._ensure_db_schema(conn)

        stop_event = stop_event or threading.Event()
        execution_lock = execution_lock or threading.Lock()
        if getattr(self, "_presence", None) is not None:
            self._presence.mark_ready()
        while not stop_event.is_set():
            try:
                with execution_lock:
                    if stop_event.is_set():
                        break
                    # open_db() guarantees close() on every path, including exceptions.
                    # The previous bare sqlite3.connect() leaked the connection whenever
                    # claim/execute/update raised, eventually exhausting all FDs (EMFILE).
                    cleanup_due = False
                    executed_task = False
                    with open_db(DB_PATH) as conn:
                        conn.row_factory = sqlite3.Row
                        self._ensure_db_schema(conn)

                        # Check if it's time to cleanup stuck tasks
                        current_time = time.time()
                        cleanup_due = (
                            current_time - self.last_cleanup_time >= self.cleanup_interval
                        )
                        if cleanup_due:
                            executed_task = True  # Skip the idle sleep below
                        else:
                            # Try to claim and execute a task
                            task = self.claim_task(conn)

                            if task:
                                task_id = task["task_id"]
                                print(f"[{self.profile}] Executing task {task_id}...", flush=True)

                                result = self._execute_task_with_heartbeat(conn, task)
                                self.update_task(conn, task_id, result)
                                self.route_next(conn, task, result)

                                self.tasks_processed += 1
                                executed_task = True

                    # Connection is closed here: sleeps and cleanup never hold it.
                    if cleanup_due:
                        print(
                            f"🧹[{self.profile}] Запуск автоматической очистки зависших задач...",
                            flush=True,
                        )
                        self.cleanup_stuck_tasks(timeout_seconds=300)
                        self.last_cleanup_time = current_time
                if executed_task:
                    stop_event.wait(0.5)  # Small pause between tasks
                else:
                    stop_event.wait(self.poll_interval)

            except KeyboardInterrupt:
                print(f"\n[{self.profile}] Shutting down...", flush=True)
                try:
                    from .mcp_bridge import shutdown_mcp_servers

                    shutdown_mcp_servers()
                except Exception:
                    pass
                break
            except Exception as e:
                print(f"[{self.profile}] Error: {e}", flush=True)
                stop_event.wait(self.poll_interval)


def main():
    parser = argparse.ArgumentParser(description="Agent Factory Worker")
    parser.add_argument(
        "--profile",
        required=True,
        help="Worker profile name from the configured profiles directory",
    )
    parser.add_argument(
        "--poll-interval", type=int, default=2, help="Polling interval in seconds"
    )
    args = parser.parse_args()

    worker = Worker(args.profile, args.poll_interval)
    worker.run()


if __name__ == "__main__":
    main()
