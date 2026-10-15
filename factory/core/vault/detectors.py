from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass


@dataclass(frozen=True)
class DetectorPattern:
    service: str
    credential_type: str
    pattern: re.Pattern[str]
    recommendation: str


DETECTORS: list[DetectorPattern] = [
    DetectorPattern(
        service="openai",
        credential_type="api_key",
        pattern=re.compile(r"\bsk-[A-Za-z0-9]{20,}\b"),
        recommendation="Import this OpenAI key into Igorek Vault and remove plaintext copies.",
    ),
    DetectorPattern(
        service="anthropic",
        credential_type="api_key",
        pattern=re.compile(r"\bsk-ant-[A-Za-z0-9\-_]{20,}\b"),
        recommendation="Move this Anthropic key into Igorek Vault and remove plaintext copies.",
    ),
    DetectorPattern(
        service="github",
        credential_type="personal_access_token",
        pattern=re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}\b"),
        recommendation="Move this GitHub token into Igorek Vault and remove plaintext copies.",
    ),
    DetectorPattern(
        service="google",
        credential_type="oauth_access_token",
        pattern=re.compile(r"\bya29\.[A-Za-z0-9._-]{20,}\b"),
        recommendation="Move this Google OAuth token into Igorek Vault and stop storing it in plaintext.",
    ),
    DetectorPattern(
        service="generic",
        credential_type="bearer_token",
        pattern=re.compile(r"(?i)\bbearer\s+([A-Za-z0-9._\-]{20,})"),
        recommendation="Review and import this bearer token into Igorek Vault if it is active.",
    ),
    DetectorPattern(
        service="telegram",
        credential_type="bot_token",
        pattern=re.compile(r"\b\d{6,}:[A-Za-z0-9_-]{20,}\b"),
        recommendation="Move this Telegram bot token into Igorek Vault and remove plaintext copies.",
    ),
]

ENV_NAME_HINTS = {
    "OPENAI_API_KEY": ("openai", "api_key"),
    "ANTHROPIC_API_KEY": ("anthropic", "api_key"),
    "GITHUB_TOKEN": ("github", "personal_access_token"),
    "GH_TOKEN": ("github", "personal_access_token"),
    "GOOGLE_OAUTH_ACCESS_TOKEN": ("google", "oauth_access_token"),
    "MINIMAX_API_KEY": ("minimax", "api_key"),
    "GLM_API_KEY": ("glm", "api_key"),
    "OPENROUTER_API_KEY": ("openrouter", "api_key"),
    "CODEX_OPENROUTER_API_KEY": ("openrouter", "api_key"),
    "FACTORY_OPENROUTER_API_KEY": ("openrouter", "api_key"),
    "TAVILY_API_KEY": ("tavily", "api_key"),
    "FACTORY_TELEGRAM_BOT_TOKEN": ("telegram", "bot_token"),
    "FACTORY_API_KEY": ("factory_api", "api_key"),
    "FAL_KEY": ("fal", "api_key"),
    "FAL_API_KEY": ("fal", "api_key"),
    "XSEARCH_BEARER_TOKEN": ("xsearch", "bearer_token"),
    "COINGECKO_API_KEY": ("coingecko", "api_key"),
    "OPENCODE_API_KEY": ("opencode-go", "api_key"),
    "CODEX_OPENCODE_API_KEY": ("opencode-go", "api_key"),
    "OPENCODE_KEY": ("opencode", "api_key"),
}


def detect_matches(text: str) -> list[tuple[DetectorPattern, str]]:
    findings: list[tuple[DetectorPattern, str]] = []
    for detector in DETECTORS:
        for match in detector.pattern.finditer(text):
            secret = match.group(1) if match.lastindex else match.group(0)
            findings.append((detector, secret))
    return findings


def guess_from_env_name(name: str) -> tuple[str, str] | None:
    return ENV_NAME_HINTS.get(name.strip().upper())


def fingerprint_secret(secret: str) -> str:
    digest = hashlib.sha256(secret.encode("utf-8")).hexdigest()
    return digest[:16]
