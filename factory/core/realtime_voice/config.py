"""Configure the realtime voice interface.

OpenAI Realtime handles speech recognition and reads replies from the selected
Igorek agent. It has no tools and VAD does not initiate responses
(`create_response: false`); speech begins when the application supplies text.

Environment settings select the model, voice and tier without code changes.
"""

from __future__ import annotations

import os
from typing import Any

REALTIME_MODEL_FULL = "gpt-realtime-2.1"
REALTIME_MODEL_MINI = "gpt-realtime-2.1-mini"
REALTIME_TIERS: dict[str, str] = {"full": REALTIME_MODEL_FULL, "mini": REALTIME_MODEL_MINI}
DEFAULT_TIER = "mini"
DEFAULT_VOICE = "cedar"
DEFAULT_TRANSCRIPTION_MODEL = "gpt-4o-mini-transcribe"
DEFAULT_TTL_S = 600
MIN_TTL_S = 60
MAX_TTL_S = 7200
DEFAULT_BASE_URL = "https://api.openai.com"
DEFAULT_AGENT = "igorek"

_TRUTHY = {"1", "true", "yes", "on", "enabled"}
_FALSY = {"0", "false", "no", "off", "disabled", "none"}

DEFAULT_INSTRUCTIONS = (
    "Ты — только голосовой интерфейс Игорька, то есть озвучка. Твоя единственная "
    "задача — дословно читать вслух текст, который приходит тебе в сообщениях, и "
    "больше ничего. Ты НЕ решаешь задачи, НЕ отвечаешь по существу, НЕ задаёшь "
    "вопросов, НЕ добавляешь ничего от себя и НЕ вызываешь инструменты. Речь "
    "пользователя распознаётся отдельно и передаётся агенту Игорька; его ответы "
    "придут тебе как сообщения — читай их ровно как есть, без добавлений и без "
    "ответов. Если сообщение пустое или не содержит текста для чтения, молчи. "
    "Не упоминай имена инструментов и внутреннее устройство системы."
)


def _env(name: str) -> str:
    return str(os.environ.get(name) or "").strip()


def enabled() -> bool:
    value = _env("IGOREK_REALTIME_ENABLED").lower()
    if value in _FALSY:
        return False
    if value in _TRUTHY:
        return True
    # Enabled by default; actual availability still depends on the key.
    return True


def normalize_tier(value: str | None) -> str:
    candidate = str(value or "").strip().lower()
    return candidate if candidate in REALTIME_TIERS else DEFAULT_TIER


def default_tier() -> str:
    return normalize_tier(_env("IGOREK_REALTIME_TIER"))


def model_for_tier(tier: str | None) -> str:
    return REALTIME_TIERS[normalize_tier(tier)]


def resolve_model(tier: str | None) -> str:
    override = _env("IGOREK_REALTIME_MODEL")
    return override or model_for_tier(tier)


def voice() -> str:
    return _env("IGOREK_REALTIME_VOICE") or DEFAULT_VOICE


def transcription_model() -> str:
    return _env("IGOREK_REALTIME_TRANSCRIPTION_MODEL") or DEFAULT_TRANSCRIPTION_MODEL


def instructions() -> str:
    return _env("IGOREK_REALTIME_INSTRUCTIONS") or DEFAULT_INSTRUCTIONS


def ttl_seconds() -> int:
    try:
        value = int(_env("IGOREK_REALTIME_TTL_S") or DEFAULT_TTL_S)
    except ValueError:
        value = DEFAULT_TTL_S
    return max(MIN_TTL_S, min(MAX_TTL_S, value))


def base_url() -> str:
    return (_env("IGOREK_REALTIME_BASE_URL") or DEFAULT_BASE_URL).rstrip("/")


def agent_id() -> str:
    return _env("IGOREK_REALTIME_AGENT") or DEFAULT_AGENT


def public_config() -> dict[str, Any]:
    tier = default_tier()
    return {
        "enabled": enabled(),
        "tier": tier,
        "model": resolve_model(tier),
        "voice": voice(),
        "tiers": list(REALTIME_TIERS.keys()),
    }


def build_session_payload(*, session_id: str, tier: str | None = None) -> dict[str, Any]:
    """Create an OpenAI Realtime session object for client credentials.

    Without tools and with `create_response: false`: the model does not respond on its own and does not
    initiate any action. It only follows our `response.create` directive, reading the provided text literally.
    """
    chosen = normalize_tier(tier) if tier else default_tier()
    del session_id  # Session context is unnecessary: the model only speaks aloud.
    return {
        "type": "realtime",
        "model": resolve_model(chosen),
        "instructions": instructions(),
        "output_modalities": ["audio"],
        "audio": {
            "input": {
                "transcription": {"model": transcription_model()},
                "turn_detection": {
                    "type": "semantic_vad",
                    "create_response": False,
                    "interrupt_response": True,
                },
            },
            "output": {"voice": voice()},
        },
    }
