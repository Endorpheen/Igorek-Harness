from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

try:
    from .codex_provider import OPENROUTER_API_KEY_ENV, suggested_models_for_provider
except ImportError:  # pragma: no cover - direct script fallback
    from codex_provider import OPENROUTER_API_KEY_ENV, suggested_models_for_provider


ESCALATABLE_FAILURE_REASONS = frozenset(
    {
        "invalid_salvaged_tool_call",
        "raw_textual_tool_artifact",
        "tool_plan_json_parse_failed_repeated",
        "invalid_tool_use_repeated",
        "workflow_no_progress_after_retry",
        "local_generation_empty_or_malformed_repeated",
        "max_tool_rounds_reached",
        "synthesis_empty_after_stuck",
    }
)

TERMINAL_FAILURE_REASONS = frozenset(
    {
        "http_error",
        "policy_denial",
        "approval_rejected",
        "missing_api_key",
        "external_tool_error",
        "security_denial",
    }
)


@dataclass(frozen=True)
class ModelRoute:
    provider: str
    model: str
    reason: str
    enabled: bool = True


def normalize_failure_reason(reason: str | None) -> str:
    return str(reason or "").strip()


def is_terminal_failure_reason(reason: str | None) -> bool:
    return normalize_failure_reason(reason) in TERMINAL_FAILURE_REASONS


def is_escalatable_failure_reason(reason: str | None) -> bool:
    normalized = normalize_failure_reason(reason)
    return bool(normalized) and normalized in ESCALATABLE_FAILURE_REASONS


def _truthy(value: Any, default: bool = False) -> bool:
    if value is None:
        return default
    text = str(value).strip().lower()
    if not text:
        return default
    return text in {"1", "true", "yes", "on"}


def _config_section(config: dict[str, Any] | None) -> dict[str, Any]:
    if not isinstance(config, dict):
        return {}
    section = config.get("smart_model_routing")
    return section if isinstance(section, dict) else {}


def smart_routing_enabled(config: dict[str, Any] | None) -> bool:
    section = _config_section(config)
    return _truthy(
        section.get("enabled"),
        default=_truthy(os.environ.get("FACTORY_SMART_ROUTING_ENABLED"), False),
    )


def resolve_escalation_route(
    *,
    failure_reason: str | None,
    config: dict[str, Any] | None = None,
) -> ModelRoute | None:
    reason = normalize_failure_reason(failure_reason)
    if not reason or is_terminal_failure_reason(reason):
        return None
    if not is_escalatable_failure_reason(reason):
        return None
    if not smart_routing_enabled(config):
        return None

    section = _config_section(config)
    strong_model = section.get("strong_model")
    route_cfg = strong_model if isinstance(strong_model, dict) else {}
    provider = str(
        route_cfg.get("provider")
        or section.get("escalation_provider")
        or os.environ.get("FACTORY_SMART_ROUTING_STRONG_PROVIDER")
        or "openrouter"
    ).strip().lower()
    model = str(
        route_cfg.get("model")
        or section.get("escalation_model")
        or os.environ.get("FACTORY_SMART_ROUTING_STRONG_MODEL")
        or "qwen/qwen3.6-plus"
    ).strip()
    if not model and provider == "openrouter":
        suggestions = suggested_models_for_provider(provider)
        model = suggestions[0] if suggestions else ""
    if provider != "openrouter" or not model:
        return None
    api_key = os.environ.get(OPENROUTER_API_KEY_ENV, "").strip()
    if not api_key:
        return None
    return ModelRoute(provider=provider, model=model, reason=reason)


def build_escalation_handoff(
    *,
    user_message: str,
    failure_reason: str,
    failure_details: str,
    primary_model: str,
) -> str:
    return (
        "Локальная модель не смогла завершить задачу из-за структурного сбоя runtime.\n"
        "Продолжи исходную задачу через сильную модель. Не рассказывай пользователю внутренние детали "
        "маршрутизации, если это не нужно для результата.\n\n"
        f"Primary model: {primary_model}\n"
        f"Escalation reason: {failure_reason}\n"
        f"Failure details:\n{failure_details[:1200]}\n\n"
        "Исходный запрос пользователя:\n"
        f"{user_message}"
    )
