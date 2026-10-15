from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Any

from .agent_foundation import get_foundation_paths, write_memory_fact

MEMORY_POLICY_ENV = "IGOREK_MEMORY_POLICY"
DEFAULT_MEMORY_POLICY = "proactive"
VALID_MEMORY_POLICIES = {"off", "explicit_only", "proactive"}
MAX_MEMORY_FACT_CHARS = 500

_INVISIBLE_CHARS = {
    "\u200b",
    "\u200c",
    "\u200d",
    "\u2060",
    "\ufeff",
    "\u202a",
    "\u202b",
    "\u202c",
    "\u202d",
    "\u202e",
}

_THREAT_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (
        re.compile(r"ignore\s+(previous|all|above|prior)\s+instructions", re.I),
        "prompt_injection",
    ),
    (
        re.compile(
            r"disregard\s+(your|all|any)\s+(instructions|rules|guidelines)", re.I
        ),
        "disregard_rules",
    ),
    (re.compile(r"system\s+prompt\s+override", re.I), "system_prompt_override"),
    (re.compile(r"you\s+are\s+now\s+", re.I), "role_hijack"),
    (
        re.compile(
            r"curl\s+[^\n]*\$\{?\w*(KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL|API)", re.I
        ),
        "exfil_curl",
    ),
    (
        re.compile(
            r"wget\s+[^\n]*\$\{?\w*(KEY|TOKEN|SECRET|PASSWORD|CREDENTIAL|API)", re.I
        ),
        "exfil_wget",
    ),
    (
        re.compile(
            r"cat\s+[^\n]*(\.env|credentials|\.netrc|\.pgpass|\.npmrc|\.pypirc)", re.I
        ),
        "read_secrets",
    ),
    (re.compile(r"authorized_keys|(?:\$HOME/\.ssh|~/\.ssh)", re.I), "ssh_access"),
]

_SESSION_LOG_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (
        re.compile(
            r"^\s*(пользователь|юзер|user)\s+"
            r"(спросил|запросил|попросил|сказал|написал|asked|requested|said|told)\b",
            re.I,
        ),
        "user_utterance_log",
    ),
    (
        re.compile(
            r"^\s*(ассистент|агент|игор[её]к|assistant|agent)\s+"
            r"(ответил|сказал|сообщил|записал|replied|answered|said|saved)\b",
            re.I,
        ),
        "assistant_utterance_log",
    ),
    (
        re.compile(
            r"\b(эт(от|а|о)\s+ход|текущ(ая|ий|ее)\s+сесси|session outcome|task progress|"
            r"completed[- ]work|temporary todo|ход диалога|вопрос пользователя)\b",
            re.I,
        ),
        "temporary_session_state",
    ),
]

_UNKNOWN_FACT_PATTERNS: list[tuple[re.Pattern[str], str]] = [
    (
        re.compile(
            r"\b(не\s+(указал|указала|сообщил|сообщила|сказал|сказала|назвал|назвала|"
            r"предоставил|предоставила|дал|дала)|неизвестн|не\s+знаю|нет\s+данных)\b",
            re.I,
        ),
        "unknown_or_missing_fact",
    ),
]

_PRAISE_ONLY_PATTERNS: list[re.Pattern[str]] = [
    re.compile(
        r"\b(благодар|спасибо|ценит\s+работ|высоко\s+ценит|довол[еь]н|довольна|"
        r"похвал|рад(?:а)?\s+(?:работе|прогрессу|результату)|т[её]плые\s+слова|"
        r"appreciates|thank|thanks|praised|happy\s+with)\b",
        re.I,
    ),
]

_TOKEN_RE = re.compile(r"[0-9A-Za-zА-Яа-яЁё]+")
_DUPLICATE_STOPWORDS = {
    "а",
    "в",
    "для",
    "его",
    "ее",
    "её",
    "и",
    "игорь",
    "игоря",
    "к",
    "код",
    "кода",
    "мой",
    "моя",
    "моё",
    "не",
    "на",
    "о",
    "он",
    "она",
    "пользователь",
    "свой",
    "своя",
    "у",
    "что",
    "это",
    "the",
    "user",
}


@dataclass(frozen=True)
class MemoryPolicyResult:
    saved: bool
    scope: str
    fact: str
    paths: list[str]
    rejected: bool = False
    reason: str = ""
    daily_saved: bool = False
    long_saved: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "saved": self.saved,
            "scope": self.scope,
            "fact": self.fact,
            "paths": list(self.paths),
            "rejected": self.rejected,
            "reason": self.reason,
            "daily_saved": self.daily_saved,
            "long_saved": self.long_saved,
        }


def normalize_memory_policy(value: str | None = None) -> str:
    raw = (
        (value if value is not None else os.environ.get(MEMORY_POLICY_ENV, ""))
        .strip()
        .lower()
    )
    if raw in {"", "default"}:
        return DEFAULT_MEMORY_POLICY
    if raw in {"explicit", "manual"}:
        return "explicit_only"
    if raw in {"none", "disabled", "false", "0"}:
        return "off"
    if raw in {"auto", "automatic"}:
        return "proactive"
    if raw not in VALID_MEMORY_POLICIES:
        return DEFAULT_MEMORY_POLICY
    return raw


def _normalize_fact(fact: str) -> str:
    return re.sub(r"\s+", " ", (fact or "")).strip(" .,!?:;\n\t")


def validate_memory_fact(
    fact: str, *, source: str = "assistant", explicit: bool = False
) -> tuple[bool, str, str]:
    normalized = _normalize_fact(fact)
    if not normalized:
        return False, "empty", ""
    if len(normalized) > MAX_MEMORY_FACT_CHARS:
        return False, "too_long", normalized
    for char in _INVISIBLE_CHARS:
        if char in normalized:
            return False, f"invisible_unicode_U+{ord(char):04X}", normalized
    for pattern, reason in _THREAT_PATTERNS:
        if pattern.search(normalized):
            return False, reason, normalized
    if not explicit and source == "assistant":
        for pattern, reason in _SESSION_LOG_PATTERNS:
            if pattern.search(normalized):
                return False, reason, normalized
        for pattern, reason in _UNKNOWN_FACT_PATTERNS:
            if pattern.search(normalized):
                return False, reason, normalized
    return True, "", normalized


def _memory_file_text(data_dir: str | None = None) -> str:
    try:
        paths = get_foundation_paths(data_dir)
        chunks: list[str] = []
        for path in (paths.memory_index, paths.user):
            if path.exists():
                chunks.append(path.read_text(encoding="utf-8", errors="replace"))
        return "\n".join(chunks)
    except Exception:
        return ""


def _tokens(text: str) -> set[str]:
    tokens = {item.lower() for item in _TOKEN_RE.findall(text or "")}
    return {
        item for item in tokens if len(item) > 2 and item not in _DUPLICATE_STOPWORDS
    }


def _looks_duplicate_fact(fact: str, existing_text: str) -> bool:
    normalized_fact = _normalize_fact(fact).lower()
    normalized_existing = re.sub(r"\s+", " ", existing_text or "").lower()
    if not normalized_fact or not normalized_existing:
        return False
    if normalized_fact in normalized_existing:
        return True
    if len(normalized_existing) > len(normalized_fact):
        if normalized_existing in normalized_fact:
            return True

    fact_tokens = _tokens(normalized_fact)
    if len(fact_tokens) < 2:
        return False

    for line in existing_text.splitlines():
        line_tokens = _tokens(line)
        if not line_tokens:
            continue
        overlap = fact_tokens & line_tokens
        if len(overlap) < 2:
            continue
        coverage = len(overlap) / max(1, min(len(fact_tokens), len(line_tokens)))
        if coverage >= 0.66:
            return True
        if len(line_tokens) >= 2 and line_tokens.issubset(fact_tokens):
            return True
    return False


def _should_downgrade_to_daily(fact: str, *, source: str, explicit: bool) -> bool:
    if explicit or source != "assistant":
        return False
    normalized = _normalize_fact(fact)
    return any(pattern.search(normalized) for pattern in _PRAISE_ONLY_PATTERNS)


def edit_saved_memory_fact(
    target: str,
    *,
    replacement: str | None = None,
    data_dir: str | None = None,
) -> dict[str, Any]:
    """Replace or remove one exact bullet in MEMORY.md's Saved facts section."""
    normalized_target = _normalize_fact(target)
    if not normalized_target:
        return {"changed": False, "rejected": True, "reason": "empty_target", "fact": ""}

    normalized_replacement = None
    if replacement is not None:
        ok, reason, normalized_replacement = validate_memory_fact(
            replacement, source="user", explicit=True
        )
        if not ok:
            return {
                "changed": False,
                "rejected": True,
                "reason": f"invalid_replacement:{reason}",
                "fact": normalized_replacement,
            }
        if normalized_replacement.casefold() == normalized_target.casefold():
            return {"changed": False, "rejected": True, "reason": "replacement_unchanged", "fact": normalized_target}

    path = get_foundation_paths(data_dir).memory_index
    if not path.is_file():
        return {"changed": False, "rejected": True, "reason": "memory_file_missing", "fact": normalized_target}

    content = path.read_text(encoding="utf-8")
    section_re = re.compile(r"(?ms)^## Saved facts\s*\n(.*?)(?=^## |\Z)")
    section = section_re.search(content)
    if section is None:
        return {"changed": False, "rejected": True, "reason": "saved_facts_section_missing", "fact": normalized_target}

    body = section.group(1)
    lines = body.splitlines(keepends=True)
    matches: list[int] = []
    bullets: list[str] = []
    for index, line in enumerate(lines):
        match = re.fullmatch(r"\s*-\s+(.*?)\s*", line.rstrip("\r\n"))
        if not match:
            continue
        value = _normalize_fact(match.group(1))
        bullets.append(value)
        if value.casefold() == normalized_target.casefold():
            matches.append(index)
    if len(matches) != 1:
        reason = "target_not_found" if not matches else "target_ambiguous"
        return {"changed": False, "rejected": True, "reason": reason, "fact": normalized_target}

    target_index = matches[0]
    if normalized_replacement is not None and any(
        value.casefold() == normalized_replacement.casefold() for value in bullets
    ):
        return {"changed": False, "rejected": True, "reason": "replacement_already_exists", "fact": normalized_replacement}

    if normalized_replacement is None:
        del lines[target_index]
        changed_fact = normalized_target
    else:
        line_ending = "\r\n" if lines[target_index].endswith("\r\n") else "\n" if lines[target_index].endswith("\n") else ""
        lines[target_index] = f"- {normalized_replacement}{line_ending}"
        changed_fact = normalized_replacement

    updated_body = "".join(lines)
    updated = content[: section.start(1)] + updated_body + content[section.end(1) :]
    path.write_text(updated, encoding="utf-8")
    return {
        "changed": True,
        "rejected": False,
        "action": "replace" if normalized_replacement is not None else "forget",
        "fact": changed_fact,
        "target": normalized_target,
        "paths": [str(path)],
    }


def persist_memory_write(
    fact: str,
    *,
    data_dir: str | None = None,
    scope: str = "long",
    source: str = "assistant",
    explicit: bool = False,
    policy: str | None = None,
) -> dict[str, Any]:
    normalized_policy = normalize_memory_policy(policy)
    normalized_scope = (
        "daily" if str(scope or "").strip().lower() == "daily" else "long"
    )
    ok, reason, normalized_fact = validate_memory_fact(
        fact, source=source, explicit=explicit
    )
    if not ok:
        return MemoryPolicyResult(
            saved=False,
            scope=normalized_scope,
            fact=normalized_fact,
            paths=[],
            rejected=True,
            reason=reason,
        ).as_dict()
    if normalized_policy == "off":
        return MemoryPolicyResult(
            saved=False,
            scope=normalized_scope,
            fact=normalized_fact,
            paths=[],
            rejected=True,
            reason="policy_off",
        ).as_dict()
    if not explicit and normalized_policy == "explicit_only":
        return MemoryPolicyResult(
            saved=False,
            scope=normalized_scope,
            fact=normalized_fact,
            paths=[],
            rejected=True,
            reason="policy_explicit_only",
        ).as_dict()
    if normalized_scope == "long" and _should_downgrade_to_daily(
        normalized_fact, source=source, explicit=explicit
    ):
        normalized_scope = "daily"
    if not explicit and _looks_duplicate_fact(
        normalized_fact, _memory_file_text(data_dir)
    ):
        return MemoryPolicyResult(
            saved=False,
            scope=normalized_scope,
            fact=normalized_fact,
            paths=[],
            rejected=True,
            reason="duplicate_existing_memory",
        ).as_dict()

    result = write_memory_fact(
        normalized_fact,
        data_dir=data_dir,
        scope=normalized_scope,
        source=source,
    )
    return MemoryPolicyResult(
        saved=bool(result.get("saved")),
        scope=str(result.get("scope") or normalized_scope),
        fact=str(result.get("fact") or normalized_fact),
        paths=[str(path) for path in result.get("paths") or []],
        daily_saved=bool(result.get("daily_saved")),
        long_saved=bool(result.get("long_saved")),
    ).as_dict()
