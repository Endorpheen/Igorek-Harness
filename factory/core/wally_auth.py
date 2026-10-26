"""RunAnywhere / Wally cloud inference: session, refresh and account helpers.

The integration uses the following service contracts, checked against a working
Wally installation:

* OpenAI-compatible inference: ``{console_url}/v1/chat/completions`` and ``/v1/models``;
* Device-flow authorization with CLI session data in ``credentials.json``:
  ``access_token``, ``refresh_token``, ``console_url``, ``expires_at`` and ``email``;
* Refresh: ``POST {console_url}/auth/cli/refresh`` with ``{"refresh_token": ...}``;
* Account/usage: ``GET /v1/me`` and ``GET /v1/cli/usage?days=N``;
* Models/prices: ``GET /v1/models`` and ``GET /v1/models/catalog``.

Do not log credentials or expose them to clients. Return a token only to the
internal Authorization-header path; account summaries exclude secret values.
"""

from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

WALLY_DEFAULT_CONSOLE_URL = "https://inference.runanywhere.ai"

# Use the standard local configuration or an explicit credential path.
WALLY_DEFAULT_CREDENTIALS_PATH = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "wally/credentials.json"

WALLY_CREDENTIALS_ENV = "FACTORY_WALLY_CREDENTIALS"
WALLY_CONSOLE_URL_ENV = "WALLY_CONSOLE_URL"
WALLY_ACCESS_TOKEN_ENV = "WALLY_ACCESS_TOKEN"
WALLY_REFRESH_TOKEN_ENV = "WALLY_REFRESH_TOKEN"
WALLY_EMAIL_ENV = "WALLY_EMAIL"
WALLY_EXPIRES_AT_ENV = "WALLY_EXPIRES_AT"

WALLY_HTTP_TIMEOUT_S = 30
# Pre-extend the session to avoid a 401 error during the agent cycle.
WALLY_REFRESH_SKEW_S = 300
_SESSION_CACHE_TTL_S = 30

_FALLBACK_MODELS = ("glm-5.3-flash", "gemma-4", "qwen3.8-27b")


class WallyAuthError(RuntimeError):
    """The Wally session is unavailable or cannot be renewed."""


@dataclass(frozen=True)
class WallySession:
    access_token: str
    refresh_token: str
    console_url: str
    expires_at: int | None
    email: str
    plan: str
    source: str

    def is_expired(self, *, skew_s: int = WALLY_REFRESH_SKEW_S) -> bool:
        if not self.expires_at:
            return False
        return int(time.time()) >= int(self.expires_at) - int(skew_s)


_CACHE: dict[str, Any] = {"session": None, "fetched_at": 0.0}


def credentials_path() -> Path:
    override = str(os.environ.get(WALLY_CREDENTIALS_ENV) or "").strip()
    if override:
        return Path(override).expanduser()
    return WALLY_DEFAULT_CREDENTIALS_PATH


def console_url(session: WallySession | None = None) -> str:
    if session is not None and session.console_url:
        return session.console_url.rstrip("/")
    env_url = str(os.environ.get(WALLY_CONSOLE_URL_ENV) or "").strip()
    if env_url:
        return env_url.rstrip("/")
    return WALLY_DEFAULT_CONSOLE_URL


def api_base_url() -> str:
    """OpenAI-compatible base URL used by the cc-backend profile."""
    return f"{console_url()}/v1"


def _coerce_expires_at(raw: Any) -> int | None:
    if raw is None or raw == "":
        return None
    if isinstance(raw, (int, float)):
        try:
            value = int(raw)
        except (ValueError, OverflowError):
            return None
        return value if value > 0 else None
    text = str(raw).strip()
    if not text:
        return None
    if text.isdigit():
        value = int(text)
        return value if value > 0 else None
    try:
        from datetime import datetime, timezone

        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return int(parsed.timestamp())
    except ValueError:
        return None


def _session_from_payload(payload: dict[str, Any], *, source: str, fallback: WallySession | None = None) -> WallySession | None:
    access = str(payload.get("access_token") or payload.get("accessToken") or "").strip()
    if not access and fallback is not None:
        access = fallback.access_token
    if not access:
        return None
    refresh = str(payload.get("refresh_token") or payload.get("refreshToken") or "").strip()
    if not refresh and fallback is not None:
        refresh = fallback.refresh_token
    url = str(payload.get("console_url") or payload.get("consoleUrl") or "").strip()
    if not url:
        url = fallback.console_url if fallback is not None else console_url()
    expires_at = _coerce_expires_at(payload.get("expires_at") or payload.get("expiresAt"))
    if expires_at is None:
        expires_in = payload.get("expires_in") or payload.get("expiresIn")
        if isinstance(expires_in, (int, float, str)) and str(expires_in).strip().isdigit():
            expires_at = int(time.time()) + int(expires_in)
        elif fallback is not None:
            expires_at = fallback.expires_at
    email = str(payload.get("email") or "").strip() or (fallback.email if fallback is not None else "")
    plan = str(payload.get("plan") or "").strip() or (fallback.plan if fallback is not None else "")
    return WallySession(
        access_token=access,
        refresh_token=refresh,
        console_url=url or WALLY_DEFAULT_CONSOLE_URL,
        expires_at=expires_at,
        email=email,
        plan=plan,
        source=source,
    )


def _session_from_env() -> WallySession | None:
    access = str(os.environ.get(WALLY_ACCESS_TOKEN_ENV) or "").strip()
    if not access:
        return None
    return WallySession(
        access_token=access,
        refresh_token=str(os.environ.get(WALLY_REFRESH_TOKEN_ENV) or "").strip(),
        console_url=console_url(),
        expires_at=_coerce_expires_at(os.environ.get(WALLY_EXPIRES_AT_ENV)),
        email=str(os.environ.get(WALLY_EMAIL_ENV) or "").strip(),
        plan="",
        source="env",
    )


def _session_from_file() -> WallySession | None:
    path = credentials_path()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    if not isinstance(raw, dict):
        return None
    return _session_from_payload(raw, source=str(path))


def load_wally_session(*, use_cache: bool = True) -> WallySession | None:
    """Return the current Wally session (vault env first, then the Wally install)."""
    now = time.time()
    cached = _CACHE.get("session")
    if use_cache and cached is not None and now - float(_CACHE.get("fetched_at") or 0) < _SESSION_CACHE_TTL_S:
        return cached  # type: ignore[return-value]

    session = _session_from_env()
    if session is not None and session.is_expired() and not session.refresh_token:
        # Environment session without a refresh token does not renew—we retrieve the live session from
        # the Wally package (which contains a refresh_token).
        file_session = _session_from_file()
        if file_session is not None:
            session = file_session
    if session is None:
        session = _session_from_file()
    _CACHE["session"] = session
    _CACHE["fetched_at"] = now
    return session


def _store_session_to_file(session: WallySession) -> bool:
    """Best-effort persist of a refreshed session back into credentials.json."""
    path = credentials_path()
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raw = {}
    except (OSError, json.JSONDecodeError):
        raw = {}
    raw["access_token"] = session.access_token
    if session.refresh_token:
        raw["refresh_token"] = session.refresh_token
    raw["console_url"] = session.console_url
    if session.expires_at:
        raw["expires_at"] = int(session.expires_at)
    if session.email:
        raw["email"] = session.email
    if session.plan:
        raw["plan"] = session.plan
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = path.with_suffix(path.suffix + ".tmp")
        tmp_path.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")
        os.chmod(tmp_path, 0o600)
        tmp_path.replace(path)
    except OSError:
        return False
    return True


def _http_json(
    url: str,
    *,
    method: str = "GET",
    payload: dict[str, Any] | None = None,
    token: str | None = None,
    timeout_s: int = WALLY_HTTP_TIMEOUT_S,
) -> dict[str, Any]:
    headers = {"Accept": "application/json"}
    data = None
    if payload is not None:
        headers["Content-Type"] = "application/json"
        data = json.dumps(payload).encode("utf-8")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout_s) as response:
            body = response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:300]
        raise WallyAuthError(f"HTTP {exc.code} from {url}: {detail}") from None
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        raise WallyAuthError(f"network failure for {url}: {type(exc).__name__}") from None
    try:
        parsed = json.loads(body)
    except json.JSONDecodeError:
        raise WallyAuthError(f"non-JSON response from {url}") from None
    if not isinstance(parsed, dict):
        raise WallyAuthError(f"unexpected payload shape from {url}")
    return parsed


def refresh_wally_session(session: WallySession) -> WallySession:
    """Rotate the access token using the stored refresh token."""
    if not session.refresh_token:
        raise WallyAuthError("Wally session has no refresh token; re-login via Wally CLI required")
    payload = _http_json(
        f"{console_url(session)}/auth/cli/refresh",
        method="POST",
        payload={"refresh_token": session.refresh_token},
    )
    refreshed = _session_from_payload(payload, source=f"{session.source}:refresh", fallback=session)
    if refreshed is None or not refreshed.access_token:
        raise WallyAuthError("Wally refresh response did not contain an access token")
    _store_session_to_file(refreshed)
    _CACHE["session"] = refreshed
    _CACHE["fetched_at"] = time.time()
    return refreshed


def get_wally_session(*, allow_refresh: bool = True) -> WallySession:
    session = load_wally_session()
    if session is None:
        raise WallyAuthError(
            "Wally session not found: set WALLY_ACCESS_TOKEN or provide "
            f"{WALLY_CREDENTIALS_ENV}/{WALLY_DEFAULT_CREDENTIALS_PATH}"
        )
    if allow_refresh and session.is_expired():
        session = refresh_wally_session(session)
    return session


def get_wally_access_token(*, allow_refresh: bool = True) -> str:
    """Return a valid access token, transparently refreshing it when expired."""
    return get_wally_session(allow_refresh=allow_refresh).access_token


def _micros_to_usd(value: Any) -> float | None:
    try:
        micros = int(value)
    except (TypeError, ValueError):
        return None
    return round(micros / 1_000_000, 6)


def wally_account_info(*, days: int = 7, timeout_s: int = WALLY_HTTP_TIMEOUT_S) -> dict[str, Any]:
    """Anonymized Wally account summary: plan, balance, spending, and model credit limit."""
    session = get_wally_session()
    base = console_url(session)
    info: dict[str, Any] = {
        "email": session.email,
        "plan": session.plan,
        "console_url": base,
        "session_source": session.source,
        "expires_at": session.expires_at,
    }
    try:
        me = _http_json(f"{base}/v1/me", token=session.access_token, timeout_s=timeout_s)
    except WallyAuthError as exc:
        info["error"] = str(exc)
        return info
    for key in ("email", "plan", "tokens_this_month", "monthly_token_limit"):
        if key in me:
            info[key] = me[key]
    try:
        usage = _http_json(
            f"{base}/v1/cli/usage?days={max(1, int(days))}",
            token=session.access_token,
            timeout_s=timeout_s,
        )
    except WallyAuthError as exc:
        info["usage_error"] = str(exc)
        return info
    credit = usage.get("credit") if isinstance(usage.get("credit"), dict) else {}
    totals = usage.get("totals") if isinstance(usage.get("totals"), dict) else {}
    info["balance_usd"] = _micros_to_usd(credit.get("balance_micros"))
    info["granted_usd"] = _micros_to_usd(credit.get("granted_micros"))
    info["spent_usd"] = _micros_to_usd(credit.get("spent_micros"))
    info["requests"] = totals.get("requests")
    info["prompt_tokens"] = totals.get("prompt_tokens")
    info["completion_tokens"] = totals.get("completion_tokens")
    info["cached_tokens"] = totals.get("cached_tokens")
    return info


def wally_account_summary_lines(*, days: int = 7) -> list[str]:
    """Human-readable account summary for CLI/TUI output (no secrets)."""
    info = wally_account_info(days=days)
    if info.get("error"):
        return [f"Wally: сессия недоступна ({info['error']})"]
    lines = [
        f"Wally account: {info.get('email') or 'unknown'} (plan: {info.get('plan') or 'unknown'})",
        f"console: {info.get('console_url')}",
    ]
    if info.get("balance_usd") is not None:
        lines.append(
            f"balance: ${info['balance_usd']:.4f} "
            f"(granted ${info.get('granted_usd') or 0:.4f}, spent ${info.get('spent_usd') or 0:.4f})"
        )
    if info.get("requests") is not None:
        lines.append(
            f"last {days}d: {info['requests']} requests, "
            f"{info.get('prompt_tokens') or 0} in / {info.get('completion_tokens') or 0} out tokens"
        )
    if info.get("usage_error"):
        lines.append(f"usage: {info['usage_error']}")
    return lines


def _model_ids(payload: dict[str, Any]) -> list[str]:
    data = payload.get("data")
    if not isinstance(data, list):
        return []
    ids: list[str] = []
    for item in data:
        if isinstance(item, dict):
            model_id = str(item.get("id") or "").strip()
        else:
            model_id = str(item or "").strip()
        if model_id and model_id not in ids:
            ids.append(model_id)
    return ids


def list_wally_models(*, timeout_s: int = WALLY_HTTP_TIMEOUT_S) -> list[str]:
    """Model ids served by the Wally cloud gateway (falls back to the known set)."""
    try:
        session = get_wally_session()
        payload = _http_json(
            f"{console_url(session)}/v1/models",
            token=session.access_token,
            timeout_s=timeout_s,
        )
    except WallyAuthError:
        return list(_FALLBACK_MODELS)
    models = _model_ids(payload)
    return models or list(_FALLBACK_MODELS)


def wally_model_catalog(*, timeout_s: int = WALLY_HTTP_TIMEOUT_S) -> list[dict[str, Any]]:
    """Model catalog with display names and pricing (best-effort)."""
    try:
        session = get_wally_session()
        payload = _http_json(
            f"{console_url(session)}/v1/models/catalog",
            token=session.access_token,
            timeout_s=timeout_s,
        )
    except WallyAuthError:
        return []
    models = payload.get("models")
    if not isinstance(models, list):
        return []
    result: list[dict[str, Any]] = []
    for item in models:
        if not isinstance(item, dict):
            continue
        model_id = str(item.get("id") or "").strip()
        if not model_id:
            continue
        result.append(
            {
                "id": model_id,
                "display_name": str(item.get("display_name") or model_id),
                "input_per_mtok": _micros_to_usd(item.get("input_per_mtok")),
                "output_per_mtok": _micros_to_usd(item.get("output_per_mtok")),
                "cached_input_per_mtok": _micros_to_usd(item.get("cached_input_per_mtok")),
            }
        )
    return result
