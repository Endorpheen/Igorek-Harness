"""Cached OpenCode Go model catalog from the official API endpoint."""
from __future__ import annotations

import json
import os
import time
import urllib.request
from dataclasses import dataclass
from pathlib import Path

OPENCODE_GO_MODELS_URL = os.environ.get(
    "FACTORY_OPENCODE_GO_MODELS_URL",
    "https://opencode.ai/zen/go/v1/models",
)
OPENCODE_GO_MODELS_TTL = int(os.environ.get("FACTORY_OPENCODE_GO_MODELS_TTL", "3600"))

# Transport assignments come from the official OpenCode Go endpoint table.
# The /models API currently returns IDs only, so unknown new IDs remain visible
# but are never auto-recommended until their transport is known.
_MODEL_TRANSPORTS = {
    "glm-5.1": "openai",
    "glm-5": "openai",
    "kimi-k2.5": "openai",
    "kimi-k2.6": "openai",
    "deepseek-v4-pro": "openai",
    "deepseek-v4-flash": "openai",
    "mimo-v2.5": "openai",
    "mimo-v2.5-pro": "openai",
    "minimax-m3": "anthropic",
    "minimax-m2.7": "anthropic",
    "minimax-m2.5": "anthropic",
    "MiniMax-M3": "anthropic",
    "MiniMax-M2.7": "anthropic",
    "MiniMax-M2.5": "anthropic",
    "qwen3.7-max": "anthropic",
    "qwen3.6-plus": "anthropic",
    "qwen3.5-plus": "anthropic",
}

_FALLBACK_MODEL_IDS = (
    "minimax-m3",
    "minimax-m2.7",
    "minimax-m2.5",
    "MiniMax-M3",
    "MiniMax-M2.7",
    "MiniMax-M2.5",
    "kimi-k2.6",
    "kimi-k2.5",
    "glm-5.1",
    "glm-5",
    "deepseek-v4-pro",
    "deepseek-v4-flash",
    "qwen3.7-max",
    "qwen3.6-plus",
    "qwen3.5-plus",
    "mimo-v2-pro",
    "mimo-v2-omni",
    "mimo-v2.5-pro",
    "mimo-v2.5",
    "hy3-preview",
)

_RECOMMENDATION_PRIORITY = (
    "deepseek-v4-pro",
    "deepseek-v4-flash",
    "kimi-k2.6",
    "mimo-v2.5-pro",
    "glm-5.1",
    "glm-5",
    "kimi-k2.5",
    "mimo-v2.5",
    "qwen3.7-max",
    "qwen3.6-plus",
    "qwen3.5-plus",
    "minimax-m3",
    "minimax-m2.7",
    "minimax-m2.5",
)


@dataclass(frozen=True)
class OpenCodeGoModel:
    id: str
    transport: str | None = None

    def supports(self, api_format: str | None) -> bool:
        normalized = normalize_transport(api_format)
        return bool(self.transport and (normalized == "auto" or self.transport == normalized))


def normalize_transport(api_format: str | None) -> str:
    normalized = str(api_format or "").strip().lower()
    if normalized == "auto":
        return "auto"
    return "openai" if normalized == "openai" else "anthropic"


def _cache_path() -> Path:
    data_root = (
        os.environ.get("FACTORY_DATA_DIR")
        or os.environ.get("IGOREK_FACTORY_HOME")
        or os.path.expanduser("~/.igorek-agent-factory")
    )
    return Path(data_root) / "cache" / "opencode_go_models.json"


def _parse_model_ids(payload: object) -> list[str]:
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
        return []
    result: list[str] = []
    for item in payload["data"]:
        model_id = str(item.get("id") or "").strip() if isinstance(item, dict) else ""
        if model_id and model_id not in result:
            result.append(model_id)
    return result


def _fetch_model_ids() -> list[str]:
    try:
        request = urllib.request.Request(
            OPENCODE_GO_MODELS_URL,
            headers={"Accept": "application/json", "User-Agent": "IgorekFactory/1.0"},
        )
        with urllib.request.urlopen(request, timeout=10) as response:
            return _parse_model_ids(json.loads(response.read().decode("utf-8")))
    except Exception:
        return []


def _read_cache(path: Path) -> list[str]:
    try:
        return _parse_model_ids(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, json.JSONDecodeError):
        return []


def _write_cache(path: Path, model_ids: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {"object": "list", "data": [{"id": item, "object": "model"} for item in model_ids]}
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def list_opencode_go_models() -> list[OpenCodeGoModel]:
    cache_path = _cache_path()
    cached = _read_cache(cache_path)
    is_stale = not cache_path.exists() or (time.time() - cache_path.stat().st_mtime) > OPENCODE_GO_MODELS_TTL
    model_ids = cached
    if is_stale:
        fetched = _fetch_model_ids()
        if fetched:
            model_ids = fetched
            _write_cache(cache_path, fetched)
    if not model_ids:
        model_ids = list(_FALLBACK_MODEL_IDS)
    return [OpenCodeGoModel(item, _MODEL_TRANSPORTS.get(item)) for item in model_ids]


def compatible_opencode_go_models(api_format: str | None) -> list[OpenCodeGoModel]:
    return [item for item in list_opencode_go_models() if item.supports(api_format)]


def recommended_opencode_go_model(api_format: str | None) -> str | None:
    compatible = {item.id for item in compatible_opencode_go_models(api_format)}
    return next((item for item in _RECOMMENDATION_PRIORITY if item in compatible), None)


def opencode_go_model_transport(model_id: str) -> str | None:
    normalized = str(model_id or "").strip()
    if not normalized:
        return None
    if normalized in _MODEL_TRANSPORTS:
        return _MODEL_TRANSPORTS[normalized]
    for item in list_opencode_go_models():
        if item.id == normalized:
            return item.transport
    return None
