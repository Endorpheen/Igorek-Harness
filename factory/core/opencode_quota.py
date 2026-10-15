"""OpenCode Go subscription quota shared by the Web UI and mobile bridge.

The OpenCode key remains on the host. Both clients obtain quota through the
process environment or Igorek Vault using the same broker as the worker/gateway;
they never receive the key. Return only normalized rolling/weekly/monthly windows
(percent/status/resetsAt), excluding unrelated upstream fields and credentials.

A shared 60-second cache serves the same subscription to the Web UI and phone
with one upstream request.
"""

from __future__ import annotations

import json
import os
import threading
import time
import urllib.error
import urllib.request
from datetime import UTC, datetime
from typing import Any

# Proxy quota address and parameters.
OPENCODE_USAGE_URL = "https://opencode.ai/zen/go/v1/usage"
# Without a custom User-Agent, Cloudflare returns a 403 error before validating the key.
OPENCODE_USER_AGENT = "IgorekFactory local-web"
OPENCODE_MOBILE_USER_AGENT = "IgorekFactory mobile-bridge"
OPENCODE_USAGE_TIMEOUT_S = 8.0
OPENCODE_USAGE_CACHE_S = 60.0
OPENCODE_KEY_TTL_S = 600.0
OPENCODE_SERVICES = ("opencode-go", "opencode")
OPENCODE_USAGE_WINDOWS = ("rolling", "weekly", "monthly")
CACHE_LOCK = threading.Lock()
USAGE_CACHE: dict[str, Any] = {"at": 0.0, "payload": None}
KEY_CACHE: dict[str, Any] = {"at": 0.0, "key": ""}


class OpenCodeUsageError(Exception):
    """Typed proxy quota error: the error code is sent in JSON, not in a 500 status code."""

    def __init__(self, code: str, status: int, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.status = status
        self.message = message


def opencode_api_key() -> str:
    """The OpenCode Go API key is fully hosted ('' — unavailable).

    Execution order is as follows: first, the already materialized process environment (gateway stores ``OPENCODE_API_KEY`` from Vault at startup), then the same broker via ``build_services().broker.get_ephemeral_env``, which both worker and gateway use to fetch secrets. The value is not logged or returned in API responses.
    """
    for name in ("OPENCODE_API_KEY", "CODEX_OPENCODE_API_KEY"):
        value = str(os.environ.get(name) or "").strip()
        if value:
            return value
    now = time.monotonic()
    with CACHE_LOCK:
        cached = str(KEY_CACHE.get("key") or "")
        if cached and now - float(KEY_CACHE.get("at") or 0.0) < OPENCODE_KEY_TTL_S:
            return cached
    try:
        from .vault.runtime import build_services as build_vault_services
        services = build_vault_services(prompt=False)
    except Exception:
        return ""
    agent_id = str(os.environ.get("FACTORY_VAULT_AGENT") or "").strip() or "cc-backend"
    try:
        ttl = int(str(os.environ.get("FACTORY_VAULT_TTL_SECONDS") or "900").strip() or "900")
    except ValueError:
        ttl = 900
    for service in OPENCODE_SERVICES:
        try:
            decision = services.broker.get_ephemeral_env(service=service, agent_id=agent_id, ttl=ttl)
        except Exception:
            continue
        if not getattr(decision, "allowed", False):
            continue
        env = getattr(decision, "env", None) or {}
        value = str(env.get("OPENCODE_API_KEY") or env.get("CODEX_OPENCODE_API_KEY") or "").strip()
        if value:
            with CACHE_LOCK:
                KEY_CACHE.update({"at": now, "key": value})
            return value
    return ""


def fetch_opencode_usage(key: str, *, user_agent: str = OPENCODE_USER_AGENT) -> dict[str, Any]:
    """OpenCode Go quota GET request: normalized response ``{"usage": {...}}`` or error.

    The upstream response is trimmed to rolling/weekly/monthly (percent/status/resetsAt) to avoid exposing unnecessary fields and the key to the client.
    """
    request = urllib.request.Request(
        OPENCODE_USAGE_URL,
        headers={"Authorization": f"Bearer {key}", "Accept": "application/json", "User-Agent": user_agent},
    )
    try:
        with urllib.request.urlopen(request, timeout=OPENCODE_USAGE_TIMEOUT_S) as response:
            raw = response.read()
    except urllib.error.HTTPError as exc:
        if exc.code == 401:
            raise OpenCodeUsageError("key_rejected", 502, "Сервис квоты отклонил ключ OpenCode") from exc
        if exc.code == 403:
            raise OpenCodeUsageError("subscription_required", 502, "Нужна подписка OpenCode Go") from exc
        if exc.code == 429:
            raise OpenCodeUsageError("rate_limited", 502, "Сервис квоты ограничил запросы") from exc
        raise OpenCodeUsageError("upstream", 502, f"Сервис квоты ответил ошибкой {exc.code}") from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        reason = getattr(exc, "reason", None) or exc
        timed_out = isinstance(exc, TimeoutError) or "timed out" in str(reason).lower()
        if timed_out:
            raise OpenCodeUsageError("timeout", 504, "Сервис квоты не ответил вовремя") from exc
        raise OpenCodeUsageError("network", 502, "Не удалось связаться с сервисом квоты") from exc
    try:
        payload = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise OpenCodeUsageError("invalid_response", 502, "Сервис квоты вернул некорректный JSON") from exc
    usage = payload.get("usage") if isinstance(payload, dict) else None
    windows: dict[str, Any] = {}
    for name in OPENCODE_USAGE_WINDOWS:
        window = usage.get(name) if isinstance(usage, dict) else None
        if not isinstance(window, dict):
            raise OpenCodeUsageError("invalid_response", 502, "Сервис квоты вернул неожиданный ответ")
        percent = window.get("percent")
        if isinstance(percent, bool) or not isinstance(percent, (int, float)):
            raise OpenCodeUsageError("invalid_response", 502, "Сервис квоты вернул неожиданный ответ")
        windows[name] = {
            "percent": percent,
            "status": str(window.get("status") or ""),
            "resetsAt": str(window.get("resetsAt") or ""),
        }
    return {"usage": windows}


def cached_usage_payload() -> dict[str, Any] | None:
    """Fresh quota cache (or `None`), shared between the web panel and mobile bridge."""
    now = time.monotonic()
    with CACHE_LOCK:
        cached = USAGE_CACHE.get("payload")
        fresh = cached is not None and now - float(USAGE_CACHE.get("at") or 0.0) < OPENCODE_USAGE_CACHE_S
    return cached if fresh else None


def store_usage_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Saves the normalized quota into the shared cache and returns it to the client."""
    result = {"usage": payload["usage"], "fetched_at": datetime.now(UTC).isoformat()}
    with CACHE_LOCK:
        USAGE_CACHE.update({"at": time.monotonic(), "payload": result})
    return result


def usage_payload(*, force: bool = False, user_agent: str = OPENCODE_USER_AGENT) -> dict[str, Any]:
    """Normalized quota with a shared cache of 60 seconds; errors are ``OpenCodeUsageError``.

    The cache and key are resolved in the same order for any transport: the mobile bridge
    and web panel both look at the same subscription and do not duplicate upstream requests.
    """
    if not force:
        cached = cached_usage_payload()
        if cached is not None:
            return cached
    key = opencode_api_key()
    if not key:
        raise OpenCodeUsageError("no_key", 503, "Ключ OpenCode не найден на этом компьютере")
    return store_usage_payload(fetch_opencode_usage(key, user_agent=user_agent))
