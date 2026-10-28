from __future__ import annotations

import os
import re
import shutil
from pathlib import Path
from typing import Any

OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
OPENROUTER_API_KEY_ENV = "FACTORY_OPENROUTER_API_KEY"
CODEX_OPENROUTER_API_KEY_ENV = "OPENROUTER_API_KEY"

OPENCODE_GO_BASE_URL = "https://opencode.ai/zen/go/v1"
OPENCODE_GO_API_KEY_ENV = "OPENCODE_API_KEY"

_ENV_PATTERN = re.compile(r"\$\{([^}]+)\}")

_PROVIDER_ALIASES = {
    "openai": "openai-codex",
    "openai-codex": "openai-codex",
    "openrouter": "openrouter",
    "opencode": "opencode-go",
    "opencode-go": "opencode-go",
    "lmstudio": "lmstudio-remote",
    "lmstudio-remote": "lmstudio-remote",
}

_USER_PROVIDER_NAMES = {
    "openai-codex": "openai",
    "openrouter": "openrouter",
    "opencode-go": "OpenCode Go",
    "lmstudio-remote": "lmstudio",
    "wally": "RunAnywhere (Wally)",
}

_MODEL_SUGGESTIONS = {
    "openai-codex": [
        "gpt-5.4",
        "gpt-5.4-mini",
        "gpt-5.3-codex",
        "gpt-5.2-codex",
        "gpt-5.2",
        "gpt-5.1-codex-max",
        "gpt-5.1-codex-mini",
    ],
    "openrouter": [
        "nvidia/nemotron-3-super-120b-a12b:free",
        "openrouter/free",
        "qwen/qwen3.6-plus-preview:free",
    ],
    "lmstudio-remote": [
        "qwen35-4b",
    ],
    "opencode-go": [
        "deepseek-v4-pro",
        "deepseek-v4.1-flash",
        "kimi-k2.6",
        "kimi-k2.5",
        "minimax-m2.7",
        "minimax-m2.5",
        "mimo-v2.5-pro",
        "mimo-v2.5",
        "mimo-v2-pro",
        "mimo-v2-omni",
        "glm-5.1",
        "glm-5",
        "qwen3.6-plus",
        "qwen3.5-plus",
    ],
}

_LEGACY_NODE_BIN = Path.home() / ".nvm/versions/node/v22.21.1/bin/node"


def _resolve_node_bin() -> str:
    override = str(os.environ.get("FACTORY_NODE_BIN") or "").strip()
    if override:
        candidate = Path(override).expanduser()
        if candidate.exists():
            return str(candidate.resolve())

    resolved = shutil.which("node")
    if resolved:
        return resolved

    if _LEGACY_NODE_BIN.exists():
        return str(_LEGACY_NODE_BIN.resolve())

    nvm_root = Path.home() / ".nvm" / "versions" / "node"
    candidates = sorted(nvm_root.glob("*/bin/node"))
    if candidates:
        return str(candidates[-1].resolve())

    return ""


def normalize_codex_provider(raw_provider: str | None) -> str:
    normalized = str(raw_provider or "").strip().lower()
    if not normalized:
        return "openai-codex"
    return _PROVIDER_ALIASES.get(normalized, normalized)


def user_facing_codex_provider(raw_provider: str | None) -> str:
    provider = normalize_codex_provider(raw_provider)
    return _USER_PROVIDER_NAMES.get(provider, provider)


def suggested_models_for_provider(raw_provider: str | None) -> list[str]:
    provider = normalize_codex_provider(raw_provider)
    if provider == "opencode-go":
        from .opencode_go_catalog import list_opencode_go_models
        return [item.id for item in list_opencode_go_models()]
    if provider == "wally":
        from .wally_auth import list_wally_models
        return list_wally_models()
    return list(_MODEL_SUGGESTIONS.get(provider, []))


def model_catalog_for_provider(raw_provider: str | None) -> list[dict[str, str | None]]:
    provider = normalize_codex_provider(raw_provider)
    if provider == "opencode-go":
        from .opencode_go_catalog import list_opencode_go_models
        return [{"id": item.id, "transport": item.transport} for item in list_opencode_go_models()]
    if provider == "wally":
        # All RunAnywhere models are routed through a single OpenAI-compatible endpoint.
        return [{"id": model_id, "transport": "openai"} for model_id in suggested_models_for_provider(provider)]
    return [{"id": item, "transport": None} for item in suggested_models_for_provider(provider)]


def recommended_model_for_provider(raw_provider: str | None, *, api_format: str | None = None) -> str | None:
    provider = normalize_codex_provider(raw_provider)
    if provider == "opencode-go":
        from .opencode_go_catalog import recommended_opencode_go_model
        return recommended_opencode_go_model(api_format)
    models = suggested_models_for_provider(provider)
    if provider == "wally" and "glm-5.3-flash" in models:
        return "glm-5.3-flash"
    return models[0] if models else None


def model_transport_for_provider(raw_provider: str | None, model_id: str) -> str | None:
    provider = normalize_codex_provider(raw_provider)
    if provider == "opencode-go":
        from .opencode_go_catalog import opencode_go_model_transport
        return opencode_go_model_transport(model_id)
    if provider == "wally":
        return "openai"
    return None


def model_is_compatible_with_profile(raw_provider: str | None, model_id: str, *, api_format: str | None) -> bool:
    provider = normalize_codex_provider(raw_provider)
    if provider != "opencode-go":
        return True
    from .opencode_go_catalog import normalize_transport
    transport = model_transport_for_provider(provider, model_id)
    normalized = normalize_transport(api_format)
    return bool(transport and (normalized == "auto" or transport == normalized))


def expand_profile_env(raw_env: dict[str, Any] | None) -> dict[str, str]:
    env_map = raw_env if isinstance(raw_env, dict) else {}
    resolved: dict[str, str] = {}
    for key, value in env_map.items():
        name = str(key or "").strip()
        if not name:
            continue
        text = str(value or "")
        expanded = _ENV_PATTERN.sub(lambda match: os.environ.get(match.group(1), ""), text)
        resolved[name] = expanded
    return resolved


def resolve_codex_launch_settings(
    profile_config: dict[str, Any],
    *,
    provider_override: str | None = None,
    model_override: str | None = None,
) -> dict[str, Any]:
    if str(profile_config.get("backend") or "").strip() != "codex":
        raise ValueError("resolve_codex_launch_settings supports only backend=codex")

    profile_provider = str(profile_config.get("provider") or "openai-codex").strip()
    provider = normalize_codex_provider(provider_override or profile_provider)
    model = str(model_override or profile_config.get("model") or "").strip()
    reasoning_effort = str(profile_config.get("reasoning_effort") or "system").strip() or "system"
    codex_profile = str(profile_config.get("codex_profile") or "").strip()
    args = [str(item) for item in profile_config.get("args") or [] if str(item).strip()]
    env = expand_profile_env(profile_config.get("env"))

    if provider == "openrouter":
        api_key = str(env.get(CODEX_OPENROUTER_API_KEY_ENV) or "").strip()
        if not api_key:
            fallback_api_key = os.environ.get(OPENROUTER_API_KEY_ENV, "").strip()
            if fallback_api_key:
                env[CODEX_OPENROUTER_API_KEY_ENV] = fallback_api_key
        node_bin = _resolve_node_bin()
        if node_bin:
            node_dir = str(Path(node_bin).resolve().parent)
            current_path = str(env.get("PATH") or os.environ.get("PATH") or "").strip()
            path_parts = [part for part in current_path.split(os.pathsep) if part]
            if node_dir not in path_parts:
                env["PATH"] = os.pathsep.join([node_dir, *path_parts]) if path_parts else node_dir
        codex_home = str(env.get("CODEX_HOME") or "").strip()
        if not codex_home:
            data_root = Path(
                os.environ.get("FACTORY_DATA_DIR")
                or os.environ.get("IGOREK_FACTORY_HOME")
                or (Path.home() / ".igorek-agent-factory")
            )
            codex_home_path = data_root / "codex_homes" / "openrouter"
            codex_home_path.mkdir(parents=True, exist_ok=True)
            env["CODEX_HOME"] = str(codex_home_path)
    elif provider == "opencode-go":
        api_key = str(env.get(OPENCODE_GO_API_KEY_ENV) or "").strip()
        if not api_key:
            fallback_api_key = os.environ.get(OPENCODE_GO_API_KEY_ENV, "").strip()
            if fallback_api_key:
                env[OPENCODE_GO_API_KEY_ENV] = fallback_api_key
        base_url = str(profile_config.get("base_url") or "").strip() or OPENCODE_GO_BASE_URL
        if not model:
            model = "gpt-5.3-codex"
        node_bin = _resolve_node_bin()
        if node_bin:
            node_dir = str(Path(node_bin).resolve().parent)
            current_path = str(env.get("PATH") or os.environ.get("PATH") or "").strip()
            path_parts = [part for part in current_path.split(os.pathsep) if part]
            if node_dir not in path_parts:
                env["PATH"] = os.pathsep.join([node_dir, *path_parts]) if path_parts else node_dir
        codex_home = str(env.get("CODEX_HOME") or "").strip()
        if not codex_home:
            data_root = Path(
                os.environ.get("FACTORY_DATA_DIR")
                or os.environ.get("IGOREK_FACTORY_HOME")
                or (Path.home() / ".igorek-agent-factory")
            )
            codex_home_path = data_root / "codex_homes" / "opencode-go"
            codex_home_path.mkdir(parents=True, exist_ok=True)
            env["CODEX_HOME"] = str(codex_home_path)
    elif provider == "openai-codex":
        env.pop("OPENAI_BASE_URL", None)
        env.pop("OPENAI_API_KEY", None)
        env.pop(CODEX_OPENROUTER_API_KEY_ENV, None)

    config_overrides: list[str] = []
    if provider == "openrouter":
        base_url = str(profile_config.get("base_url") or "").strip() or OPENROUTER_BASE_URL
        config_overrides.extend(
            [
                'model_provider="openrouter"',
                'model_providers.openrouter.name="openrouter"',
                f'model_providers.openrouter.base_url="{base_url}"',
                f'model_providers.openrouter.env_key="{CODEX_OPENROUTER_API_KEY_ENV}"',
            ]
        )
    elif provider == "opencode-go":
        base_url = str(profile_config.get("base_url") or "").strip() or OPENCODE_GO_BASE_URL
        config_overrides.extend(
            [
                'model_provider="opencode-go"',
                'model_providers.opencode-go.name="opencode-go"',
                f'model_providers.opencode-go.base_url="{base_url}"',
                f'model_providers.opencode-go.env_key="{OPENCODE_GO_API_KEY_ENV}"',
            ]
        )

    return {
        "provider": provider,
        "provider_display": user_facing_codex_provider(provider),
        "model": model,
        "reasoning_effort": reasoning_effort,
        "codex_profile": codex_profile,
        "args": args,
        "env": env,
        "config_overrides": config_overrides,
    }
