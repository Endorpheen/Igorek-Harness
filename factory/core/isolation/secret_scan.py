"""Secret scanning for the isolation boundary (plan todo 18).

Three questions this module answers deterministically:

* does a base-image recipe or manifest contain a secret (a secret-named
  assignment, a secret-named key, or a token-shaped value)?
* does a guest environment contain a provider token (by key name, by an
  explicit known value, or by token shape)?
* how is a value masked before it can reach a log?

Findings never carry the secret itself - only its location and a stable reason
code - so a scan result can be logged, traced or asserted without leaking.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final

__all__ = [
    "GUEST_ENV_LEAK_REASON",
    "SECRET_KEY_NAMES",
    "SECRET_KEY_RE",
    "SECRET_VALUE_PATTERNS",
    "GuestEnvLeakError",
    "SecretFinding",
    "assert_guest_env_clean",
    "manifest_secret_keys",
    "mask_secret",
    "scan_base_image_sources",
    "scan_guest_env_for_secrets",
    "scan_text_for_secrets",
]

#: Stable reason code for a guest-bound env that carries a provider secret.
GUEST_ENV_LEAK_REASON: Final[str] = "guest_env_secret_leak"

#: Every environment name Igorek Vault can materialize (mirrors
#: ``factory.core.vault.broker.SERVICE_ENV_MAP``; a regression test pins the two
#: together) plus the Wally session variables ``wally_auth`` reads.
SECRET_KEY_NAMES: frozenset[str] = frozenset(
    {
        "WALLY_ACCESS_TOKEN",
        "WALLY_REFRESH_TOKEN",
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_AUTH_TOKEN",
        "OPENROUTER_API_KEY",
        "CODEX_OPENROUTER_API_KEY",
        "FACTORY_OPENROUTER_API_KEY",
        "OPENCODE_API_KEY",
        "CODEX_OPENCODE_API_KEY",
        "MINIMAX_API_KEY",
        "GLM_API_KEY",
        "TAVILY_API_KEY",
        "XSEARCH_BEARER_TOKEN",
        "FAL_KEY",
        "FAL_API_KEY",
        "GITHUB_TOKEN",
        "GH_TOKEN",
        "FACTORY_TELEGRAM_BOT_TOKEN",
        "FACTORY_API_KEY",
        "GOOGLE_OAUTH_ACCESS_TOKEN",
        "SUPABASE_ACCESS_TOKEN",
        "COINGECKO_API_KEY",
        "FISH_API_KEY",
        "OBSIDIAN_VAULT_PATH",
        "IGOREK_VAULT_SECRET",
    }
)

SECRET_KEY_RE = re.compile(
    r"(?:^|_)(?:API_?KEY|ACCESS_TOKEN|REFRESH_TOKEN|AUTH_TOKEN|BEARER_TOKEN"
    r"|TOKEN|SECRET|PASSWORD|PASSWD|CREDENTIALS?|PRIVATE_KEY)(?:$|_)",
    re.IGNORECASE,
)

#: ``(pattern name, compiled regex)`` for values that look like real secrets.
SECRET_VALUE_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("openai_sk", re.compile(r"\bsk-[A-Za-z0-9_-]{16,}")),
    (
        "github_pat",
        re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,})"),
    ),
    ("slack_token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}")),
    ("google_api_key", re.compile(r"\bAIza[0-9A-Za-z_-]{30,}")),
    ("aws_access_key", re.compile(r"\bAKIA[0-9A-Z]{16}\b")),
    ("private_key_block", re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----")),
    ("bearer_token", re.compile(r"\bBearer\s+[A-Za-z0-9._~+/=-]{20,}")),
)

_ASSIGNMENT_RE = re.compile(r"^\s*(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)$")


class GuestEnvLeakError(RuntimeError):
    """A guest environment carries a provider secret; ``code`` is stable."""

    def __init__(self, keys: Sequence[str]) -> None:
        self.keys = tuple(str(key) for key in keys)
        self.reason = GUEST_ENV_LEAK_REASON
        super().__init__(f"{self.reason}: keys={','.join(self.keys)}")

    @property
    def code(self) -> str:
        return self.reason


@dataclass(frozen=True, slots=True)
class SecretFinding:
    """One scan hit: where it was found and why, never the secret itself."""

    source: str
    reason: str
    location: str = ""

    def to_dict(self) -> dict[str, str]:
        payload = {"source": self.source, "reason": self.reason}
        if self.location:
            payload["location"] = self.location
        return payload


def mask_secret(value: object) -> str:
    """Mask a secret for logs; an empty value stays empty."""
    return "***" if str(value or "").strip() else ""


def scan_text_for_secrets(text: str, *, source: str = "text") -> tuple[SecretFinding, ...]:
    """Find secret-named assignments and token-shaped values in text."""
    findings: list[SecretFinding] = []
    for number, line in enumerate(str(text or "").splitlines(), start=1):
        match = _ASSIGNMENT_RE.match(line)
        if match is not None:
            name, raw_value = match.group(1), match.group(2).strip().strip("'\"")
            if raw_value and SECRET_KEY_RE.search(name):
                findings.append(
                    SecretFinding(source, f"secret_assignment:{name}", f"line {number}")
                )
        for pattern_name, pattern in SECRET_VALUE_PATTERNS:
            if pattern.search(line):
                findings.append(
                    SecretFinding(source, f"secret_value:{pattern_name}", f"line {number}")
                )
    return tuple(findings)


def manifest_secret_keys(payload: Mapping[str, object]) -> tuple[str, ...]:
    """Secret-named keys anywhere in a manifest-shaped mapping."""
    found: list[str] = []

    def walk(value: object) -> None:
        if isinstance(value, Mapping):
            for key, item in value.items():
                name = str(key)
                if SECRET_KEY_RE.search(name):
                    found.append(name)
                walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)

    walk(payload)
    return tuple(found)


def scan_base_image_sources(
    *,
    recipe_path: str | Path | None = None,
    manifest: Mapping[str, object] | None = None,
) -> tuple[SecretFinding, ...]:
    """Scan the base-image recipe and manifest for secrets."""
    findings: list[SecretFinding] = []
    if recipe_path is not None:
        path = Path(recipe_path)
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            findings.append(SecretFinding(str(path), "recipe_unreadable"))
        else:
            findings.extend(scan_text_for_secrets(text, source=path.name))
    if manifest is not None:
        findings.extend(
            SecretFinding("manifest", f"secret_key:{name}", name)
            for name in manifest_secret_keys(manifest)
        )
        findings.extend(
            scan_text_for_secrets(
                json.dumps(manifest, ensure_ascii=False, default=str), source="manifest"
            )
        )
    return tuple(findings)


def scan_guest_env_for_secrets(
    env: Mapping[str, object], *, secret_values: Iterable[str] = ()
) -> tuple[str, ...]:
    """Keys of a guest env that hold a provider secret (by name, value or shape)."""
    known_values = {str(value) for value in secret_values if str(value)}
    leaked: list[str] = []
    for key, value in env.items():
        name = str(key)
        text = str(value) if value is not None else ""
        named = SECRET_KEY_RE.search(name) is not None
        valued = bool(text) and (
            text in known_values
            or any(pattern.search(text) for _name, pattern in SECRET_VALUE_PATTERNS)
        )
        if (named or valued) and name not in leaked:
            leaked.append(name)
    return tuple(leaked)


def assert_guest_env_clean(
    env: Mapping[str, object], *, secret_values: Iterable[str] = ()
) -> None:
    """Raise :class:`GuestEnvLeakError` when the env carries a provider secret."""
    leaked = scan_guest_env_for_secrets(env, secret_values=secret_values)
    if leaked:
        raise GuestEnvLeakError(leaked)
