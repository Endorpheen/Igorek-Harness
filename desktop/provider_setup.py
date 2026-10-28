"""Persist standard cc-backend profile drafts beside their encrypted API key."""
import ipaddress
import re
from urllib.parse import urlsplit
from factory.core.vault.lifecycle import is_loopback_provider

def profile_config(name: str, model: str, base_url: str, api_format: str):
    if not re.fullmatch(r"[a-z][a-z0-9-]{0,39}", name):
        raise ValueError("invalid profile name")
    if not model.strip() or len(model) > 256 or any(ord(char) < 32 for char in model):
        raise ValueError("invalid model")
    if api_format not in {"openai", "anthropic", "openai-responses"}:
        raise ValueError("invalid API format")
    if api_format == "openai-responses" and base_url.rstrip("/") != "https://api.openai.com/v1":
        raise ValueError("ChatGPT requires the official Responses endpoint")
    url = urlsplit(base_url)
    if (not url.hostname or url.username is not None or url.password is not None or url.query or url.fragment
            or any(char.isspace() or ord(char) < 32 for char in base_url) or (url.port is not None and not 1 <= url.port <= 65535)):
        raise ValueError("invalid URL")
    if url.scheme != "https":
        try:
            loopback = ipaddress.ip_address(url.hostname).is_loopback
        except ValueError:
            loopback = url.hostname == "localhost"
        if url.scheme != "http" or not loopback:
            raise ValueError("HTTPS required for remote providers")
    return {"name": name, "backend": "cc-backend", "provider": "custom", "model": model.strip(),
            "base_url": base_url.rstrip("/"), "api_format": api_format, "filesystem_mode": "workspace",
            "checkpoints": {"enabled": True, "max_snapshots": 50}, "env": {}}


class ProviderSetup:
    def __init__(self, management):
        self.management = management

    def list_profiles(self):
        return self.management.list_provider_profiles()

    def check(self, credential_id):
        from provider_probe import check_connection
        profile = next((p for p in self.list_profiles() if p['credential_id'] == credential_id), None)
        if profile and profile['config']['api_format'] == 'openai-responses':
            token = self.management.chatgpt_access_token(credential_id, profile['config'])
            return self.management.check_provider_profile(credential_id,
                lambda config, _secret: check_connection(config, token))
        return self.management.check_provider_profile(credential_id, check_connection)

    def update(self, credential_id, name, model, base_url, api_format, api_key, *, before_update=None):
        config = profile_config(name, model, base_url, api_format)
        return self.management.update_provider_profile(credential_id, config, api_key, before_update=before_update)

    def save(self, name, model, base_url, api_format, api_key):
        config = profile_config(name, model, base_url, api_format)
        if api_format == 'openai-responses':
            raise ValueError('Use Sign in with ChatGPT')
        if (not api_key.strip() and not is_loopback_provider(config)) or any(ord(char) < 32 for char in api_key):
            raise ValueError("invalid API key")
        record = self.management.add_credential("desktop-profile:" + name, api_key.strip(), "api_key",
            imported_from="desktop-provider-setup", secret_metadata={"desktop_profile": config})
        return {"credential_id": record.id, "config": config, "status": "unverified"}


def desktop_openai_voice_key(lifecycle):
    try:
        for profile in lifecycle.list_provider_profiles():
            config = profile["config"]
            if profile["status"] != "verified" or config.get("base_url") != "https://api.openai.com/v1" or config.get("api_format") != "openai":
                continue
            headers = lifecycle.provider_runtime_headers(profile["credential_id"], config, "openai")
            return headers["Authorization"].removeprefix("Bearer ")
    except Exception:
        return ""
    return ""


PRESETS = {
    "opencode-go": ("https://opencode.ai/zen/go/v1", "deepseek-v4.1-flash"),
    "opencode-zen": ("https://opencode.ai/zen/v1", "big-pickle"),
    "zai-coding-plan": ("https://api.z.ai/api/coding/paas/v4", "glm-5.3-flash"),
    "openrouter": ("https://openrouter.ai/api/v1", ""),
}


def catalog_json(url, key=""):
    import json
    import urllib.request
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *args, **kwargs):
            return None
    headers = {"User-Agent": "Mozilla/5.0 IgorekDesktop/model-catalog"}
    if key:
        headers["Authorization"] = "Bearer " + key
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.build_opener(NoRedirect).open(request, timeout=12) as response:
        raw = response.read(8 * 1024 * 1024 + 1)
    if len(raw) > 8 * 1024 * 1024:
        raise ValueError("Catalog too large")
    return json.loads(raw)


def model_catalog(preset, api_key, credential_id, management):
    if preset not in PRESETS:
        raise ValueError("Unknown preset")
    if management.state != "unlocked":
        from factory.core.vault.lifecycle import VaultLockedError
        raise VaultLockedError()
    base, _ = PRESETS[preset]
    if credential_id and not api_key:
        profile = next((p for p in management.list_provider_profiles() if p["credential_id"] == credential_id), None)
        if not profile or profile["config"]["base_url"] != base:
            raise ValueError("Provider binding changed")
        api_key = management.get_secret(credential_id).secret
    if any(ord(c) < 32 for c in api_key):
        raise ValueError("Invalid key")
    if preset == "zai-coding-plan":
        return {"models": [{"id": m, "name": m, "api_format": "openai", "free": False}
                           for m in ("glm-5.3-flash", "glm-5.3")],
                "source": "documented", "note": "Модели из документации Z.AI Coding Plan; доступ по ключу проверяется отдельно."}
    data = catalog_json(base + "/models", api_key)
    if not isinstance(data, dict) or not isinstance(data.get("data"), list):
        raise ValueError("Invalid catalog")
    metadata = None
    if preset.startswith("opencode-"):
        metadata = catalog_json("https://models.dev/api.json").get("opencode" if preset == "opencode-zen" else preset)
        if not isinstance(metadata, dict) or not isinstance(metadata.get("models"), dict):
            raise ValueError("Invalid metadata")
    models = {}
    for item in data["data"]:
        if not isinstance(item, dict):
            continue
        identity = item.get("id")
        if not isinstance(identity, str) or not identity or len(identity) > 256 or any(ord(c) < 32 for c in identity):
            continue
        info = metadata["models"].get(identity) if metadata else item
        if not isinstance(info, dict):
            continue
        if metadata:
            npm = info.get("provider", {}).get("npm", metadata.get("npm"))
            if npm not in ("@ai-sdk/openai-compatible", "@ai-sdk/anthropic"):
                continue
            api_format = "anthropic" if npm == "@ai-sdk/anthropic" else "openai"
            cost = info.get("cost", {})
            free = cost.get("input") == 0 and cost.get("output") == 0
        else:
            if "tools" not in item.get("supported_parameters", []):
                continue
            api_format = "openai"
            cost = item.get("pricing", {})
            free = str(cost.get("prompt")) in ("0", "0.0") and str(cost.get("completion")) in ("0", "0.0")
        models[identity] = {"id": identity, "name": str(info.get("name") or identity)[:256],
                            "api_format": api_format, "free": free}
    if not models:
        raise ValueError("Empty compatible catalog")
    return {"models": sorted(models.values(), key=lambda m: (not m["free"], m["name"].lower())),
            "source": "live", "note": "Актуальный каталог сервиса. Список не подтверждает доступ по ключу; после сохранения проверьте подключение."}


def desktop_opencode_usage(management, active_profile, *, force=False):
    from factory.core.opencode_quota import OpenCodeUsageError, fetch_opencode_usage
    from datetime import UTC, datetime
    from factory.core.vault.lifecycle import VaultLockedError
    try:
        profiles = management.list_provider_profiles()
        profile = next((p for p in profiles if p["config"]["name"] == active_profile), None)
        if not profile or profile["config"]["base_url"] != PRESETS["opencode-go"][0]:
            raise OpenCodeUsageError("no_key", 503, "Для квоты запустите профиль OpenCode Go")
        headers = management.provider_runtime_headers(profile["credential_id"], profile["config"], profile["config"]["api_format"])
        key = headers.get("Authorization", "").removeprefix("Bearer ") or headers.get("x-api-key", "")
        payload = fetch_opencode_usage(key)
        return {"usage": payload["usage"], "fetched_at": datetime.now(UTC).isoformat()}
    except VaultLockedError:
        raise OpenCodeUsageError("no_key", 503, "Разблокируйте хранилище десктопа") from None


def read_legacy_profile(source):
    import json
    import os
    import stat
    from pathlib import Path
    path = Path(source)
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > 1024 * 1024:
            raise ValueError()
        raw = stream.read(1024 * 1024 + 1)
    if len(raw) > 1024 * 1024:
        raise ValueError()
    data = json.loads(raw)
    if not isinstance(data, dict) or data.get("backend") != "cc-backend":
        raise ValueError()
    config = profile_config(path.stem, data.get("model", ""), data.get("base_url", ""), data.get("api_format", "openai"))
    return {key: config[key] for key in ("name", "model", "base_url", "api_format")}


def desktop_provider_usage(management, profile, *, force=False):
    from factory.core.chatgpt_usage import UsageStore
    from factory.core.opencode_quota import OpenCodeUsageError
    from factory.core.vault.lifecycle import VaultLockedError
    try:
        item = next((p for p in management.list_provider_profiles() if p['config']['name'] == profile), None)
    except VaultLockedError:
        raise OpenCodeUsageError('vault_locked', 423, 'Разблокируйте хранилище для обновления квоты') from None
    if item is None:
        raise OpenCodeUsageError('profile_unknown', 404, 'Подключение не найдено')
    config = item['config']
    if config['api_format'] == 'openai-responses':
        from factory.core.chatgpt_usage import UsageError
        from datetime import UTC, datetime
        store = UsageStore(management.home, item['credential_id'], config)
        cached = store.load()
        try:
            connection=getattr(management,'codex_quota',None)
            if connection is None:raise UsageError('quota_login_required')
            key=connection.cache_key(profile) if hasattr(connection,'cache_key') else None
            if key is not None and cached.get('account_key')!=key:cached={'usage':None,'fetched_at':'','source':'codex_account'}
            if not force and cached['source'] in {'codex_account','codex_local'}:
                try:
                    age=(datetime.now(UTC)-datetime.fromisoformat(cached['fetched_at'])).total_seconds()
                    if 0<=age<60:return {'provider':'codex','profile':profile,**cached}
                except (ValueError,TypeError):pass
            payload = connection.usage(profile)
            store.save(payload)
            return {'provider': 'codex', 'profile': profile, **payload}
        except UsageError as error:
            if error.code in {'quota_account_mismatch','quota_profile_changed','quota_login_required','quota_local_login_required'}:
                cached={'usage':None,'fetched_at':'','source':'codex_account'}
            return {'provider': 'codex', 'profile': profile, **cached,
                    'quota_error': {'code': error.code, 'http_status': error.http_status}}

    if config['base_url'] == PRESETS['opencode-go'][0]:
        return {'provider': 'opencode', 'profile': profile, **desktop_opencode_usage(management, profile, force=force)}
    return {'provider': 'unsupported', 'profile': profile, 'usage': None}
