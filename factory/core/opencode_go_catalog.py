"""Cached OpenCode Go model catalog from the official API endpoint."""
from __future__ import annotations

import json
import os
import time
import threading
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlsplit

OPENCODE_GO_MODELS_URL = os.environ.get(
    "FACTORY_OPENCODE_GO_MODELS_URL",
    "https://opencode.ai/zen/go/v1/models",
)
OPENCODE_GO_MODELS_TTL = int(os.environ.get("FACTORY_OPENCODE_GO_MODELS_TTL", "60"))
MODELS_DEV_URL = "https://models.dev/api.json"
_GO_API_ROUTES = {
    ("@ai-sdk/openai-compatible", "https://opencode.ai/inference/go/openai/v1"): "openai",
    ("@ai-sdk/openai", "https://opencode.ai/inference/go/openai/v1"): "opencode-responses",
    ("@ai-sdk/anthropic", "https://opencode.ai/inference/go/anthropic/v1"): "anthropic",
    ("@ai-sdk/google", "https://opencode.ai/inference/go/google/v1beta"): "google",
}
_routes_lock = threading.Lock()
_routes_cached: dict[str, tuple[str, str, str]] = {}
_routes_expire = 0.0


def is_opencode_go_url(value: str) -> bool:
    try:
        url = urlsplit(value)
        return (url.scheme == 'https' and url.hostname == 'opencode.ai' and url.port in (None, 443)
                and url.username is None and url.password is None and not url.query and not url.fragment
                and url.path.rstrip('/') == '/zen/go/v1')
    except (ValueError, TypeError):
        return False


def parse_opencode_go_routes(payload: object) -> dict[str, tuple[str, str, str]]:
    if not isinstance(payload, dict):
        return {}
    provider = payload.get("opencode-go")
    if not isinstance(provider, dict) or not isinstance(provider.get("models"), dict):
        return {}
    result = {}
    for model_id, model in provider["models"].items():
        if not isinstance(model_id, str) or not model_id or len(model_id) > 256 or any(ord(c) < 33 for c in model_id) or not isinstance(model, dict):
            continue
        info = model.get("provider") if isinstance(model.get("provider"), dict) else {}
        package = info.get("npm", provider.get("npm"))
        url = info.get("api", provider.get("api"))
        if not isinstance(package, str) or not isinstance(url, str):
            continue
        transport = _GO_API_ROUTES.get((package, url))
        if transport:
            name = model.get("name")
            result[model_id] = (transport, url, name[:256] if isinstance(name, str) and name else model_id)
    return result


def fetch_opencode_go_routes() -> dict[str, tuple[str, str, str]]:
    try:
        request = urllib.request.Request(MODELS_DEV_URL, headers={"Accept": "application/json", "User-Agent": "IgorekFactory/1.0"})
        with urllib.request.urlopen(request, timeout=10) as response:
            return parse_opencode_go_routes(json.loads(response.read(32 * 1024 * 1024)))
    except (OSError, ValueError, TypeError):
        return {}


def get_opencode_go_routes(*, force: bool = False) -> dict[str, tuple[str, str, str]]:
    global _routes_cached, _routes_expire
    with _routes_lock:
        if force or time.monotonic() >= _routes_expire:
            fetched = fetch_opencode_go_routes()
            if fetched:
                _routes_cached = fetched
                _routes_expire = time.monotonic() + OPENCODE_GO_MODELS_TTL
            else:
                _routes_expire = time.monotonic() + 10
        return dict(_routes_cached)


def canonical_opencode_go_model(model: str) -> str:
    """Migrate the confirmed provider ID rename without changing model families."""
    return {'deepseek-v4-flash': 'deepseek-v4.1-flash'}.get(model, model)


def active_opencode_go_routes() -> dict[str, tuple[str, str, str]]:
    active = [item.id for item in list_opencode_go_models()]
    if not active:
        return {}
    routes = get_opencode_go_routes()
    if any(model not in routes for model in active):
        routes = get_opencode_go_routes(force=True)
    return {model: routes[model] for model in active if model in routes}

# Transport assignments come from the official OpenCode Go endpoint table.
# The /models API currently returns IDs only, so unknown new IDs remain visible
# but are never auto-recommended until their transport is known.
_MODEL_TRANSPORTS = {
    "deepseek-v4.1-flash": "openai",
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

_RECOMMENDATION_PRIORITY = (
    "deepseek-v4-pro",
    "deepseek-v4.1-flash",
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
        model_ids = fetched
        if fetched:
            _write_cache(cache_path, fetched)
    return [OpenCodeGoModel(item, _MODEL_TRANSPORTS.get(item)) for item in model_ids
            if canonical_opencode_go_model(item) == item]


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
