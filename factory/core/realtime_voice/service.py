"""Real-time voice facade for the mobile bridge.

Binds configuration, Vault/OpenAI access, and call registry. All methods are synchronous: the bridge calls them from the request handler.
"""

from __future__ import annotations

from typing import Any

from . import config as rt_config
from . import credentials
from .registry import VoiceCallRegistry, VoiceRateLimiter


class VoiceUnavailable(Exception):
    def __init__(self, code: str, status: int, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.status = status
        self.message = message


class VoiceService:
    def __init__(
        self,
        *,
        registry: VoiceCallRegistry | None = None,
        limiter: VoiceRateLimiter | None = None,
        key_provider=None,
    ) -> None:
        self.registry = registry or VoiceCallRegistry()
        self.limiter = limiter or VoiceRateLimiter()
        self.key_provider = key_provider or (lambda: credentials.openai_api_key())

    def public_config(self) -> dict[str, Any]:
        cfg = rt_config.public_config()
        if not cfg["enabled"]:
            return {**cfg, "reason": "disabled"}
        if not self.key_provider():
            return {**cfg, "enabled": False, "reason": "no_key"}
        return {**cfg, "reason": ""}

    def start_session(self, session_id: str, tier: str | None = None) -> dict[str, Any]:
        if not rt_config.enabled():
            raise VoiceUnavailable("disabled", 503, "Голосовой режим выключен")
        key = self.key_provider()
        if not key:
            raise VoiceUnavailable("no_key", 503, "Ключ OpenAI Realtime не найден в Vault")
        if not self.limiter.allow(session_id):
            raise VoiceUnavailable("rate_limited", 429, "Слишком частое создание голосовых сессий")
        payload = rt_config.build_session_payload(session_id=session_id, tier=tier)
        minted = credentials.mint_client_secret(
            session_payload=payload, ttl_s=rt_config.ttl_seconds(), key=key
        )
        output = payload.get("audio", {}).get("output", {}) if isinstance(payload.get("audio"), dict) else {}
        call = self.registry.create(
            session_id=session_id,
            ephemeral=str(minted["value"]),
            expires_at=float(minted.get("expires_at") or 0.0),
            model=str(payload.get("model") or ""),
            voice=str(output.get("voice") or ""),
        )
        return {
            "call_id": call.call_id,
            "expires_at": int(float(minted.get("expires_at") or 0.0)),
            "model": call.model,
            "voice": call.voice,
        }

    def relay_sdp(self, call_id: str, sdp: str) -> dict[str, str]:
        call = self.registry.get(call_id)
        if call is None:
            raise VoiceUnavailable("call_not_found", 404, "Голосовая сессия не найдена или истекла")
        result = credentials.create_call(sdp=sdp, ephemeral=call.ephemeral)
        call.provider_call_id = result.get("call_id", "")
        return {"sdp": result["sdp"]}

    def hangup(self, call_id: str) -> dict[str, bool]:
        call = self.registry.pop(call_id)
        if call is not None:
            key = self.key_provider()
            credentials.hangup(provider_call_id=call.provider_call_id, key=key)
        return {"ok": True}
