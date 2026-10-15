from __future__ import annotations

import json
import os
import re
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .agent_foundation import extract_memory_write_blocks, get_foundation_paths
from .memory_policy import edit_saved_memory_fact, persist_memory_write
from .runtime import get_profile_config, profile_backend_name, profile_model_name

MEMORY_REVIEW_ENV = "IGOREK_MEMORY_REVIEW"
DEFAULT_MEMORY_REVIEW = "on"
MAX_REVIEW_TEXT_CHARS = 8000


@dataclass(frozen=True)
class MemoryReviewItem:
    action: str
    scope: str
    fact: str
    kind: str = ""
    reason: str = ""
    target: str = ""


def memory_review_enabled(value: str | None = None) -> bool:
    raw = (value if value is not None else os.environ.get(MEMORY_REVIEW_ENV, DEFAULT_MEMORY_REVIEW)).strip().lower()
    return raw not in {"0", "false", "off", "no", "disabled"}


def review_and_persist_memory(
    *,
    user_text: str,
    assistant_text: str,
    profile: str,
    data_dir: str | None = None,
    legacy_raw_text: str = "",
    timeout_s: int = 45,
    log_fn=None,
) -> list[dict[str, Any]]:
    """Trigger Hermes-style memory review after a response and save the approved version.

    The assistant's visible response remains clean. Memory extraction occurs after the move, and a deterministic policy checks scope, safety, and duplicates.
    Older MEMORY_WRITE blocks remain as fallback mode for other backends.
    """
    if log_fn is None:
        log_fn = lambda _msg: None

    review_enabled = memory_review_enabled()
    cc_backend_profile = _is_cc_backend_profile(profile)
    legacy_items = [] if review_enabled and cc_backend_profile else _legacy_memory_items(legacy_raw_text)
    review_items: list[MemoryReviewItem] = []
    if review_enabled:
        try:
            review_items = _review_with_profile(
                user_text=user_text,
                assistant_text=assistant_text,
                profile=profile,
                data_dir=data_dir,
                timeout_s=timeout_s,
                log_fn=log_fn,
            )
        except Exception as exc:
            log_fn(f"[memory-review] skipped: {exc}")
            if cc_backend_profile:
                log_fn("[memory-review] legacy MEMORY_WRITE ignored for cc-backend")

    persisted: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for item in [*review_items, *legacy_items]:
        action = item.action.strip().lower()
        if action in {"discard", "none", "skip", "session_only", "session-search-only"}:
            continue
        if action in {"forget", "replace"}:
            target = item.target.strip()
            target_key = re.sub(r"\s+", " ", target).casefold()
            user_key = re.sub(r"\s+", " ", user_text or "").casefold()
            authorized = bool(target_key and target_key in user_key)
            if not authorized or (action == "replace" and not item.fact.strip()):
                result = {
                    "changed": False,
                    "rejected": True,
                    "reason": "target_not_explicitly_cited" if not authorized else "replacement_missing",
                    "target": target,
                    "fact": item.fact.strip(),
                }
            else:
                result = edit_saved_memory_fact(
                    target,
                    replacement=item.fact.strip() if action == "replace" else None,
                    data_dir=data_dir,
                )
            result = {**result, "review_action": action, "review_kind": item.kind, "review_reason": item.reason}
            persisted.append(result)
            continue
        fact = item.fact.strip()
        if action != "save" or not fact:
            continue
        scope = "daily" if item.scope.strip().lower() == "daily" else "long"
        key = (scope, _dedupe_key(fact))
        if key in seen:
            continue
        seen.add(key)
        result = persist_memory_write(
            fact,
            data_dir=data_dir,
            scope=scope,
            source="assistant",
            explicit=False,
        )
        if item.kind or item.reason:
            result = {
                **result,
                "review_kind": item.kind,
                "review_reason": item.reason,
                "review_action": action,
            }
        persisted.append(result)
    return persisted


def _legacy_memory_items(raw_text: str) -> list[MemoryReviewItem]:
    _cleaned, writes = extract_memory_write_blocks(raw_text or "")
    items: list[MemoryReviewItem] = []
    for write in writes:
        fact = str(write.get("fact") or "").strip()
        if not fact:
            continue
        items.append(
            MemoryReviewItem(
                action="save",
                scope=str(write.get("scope") or "long").strip() or "long",
                fact=fact,
                kind="legacy_memory_write",
                reason="legacy MEMORY_WRITE block",
            )
        )
    return items


def _is_cc_backend_profile(profile: str) -> bool:
    try:
        return profile_backend_name(get_profile_config(profile)) == "cc-backend"
    except Exception:
        return False


def _review_api_format(config: dict[str, Any], model: str) -> str:
    configured = str(config.get("api_format") or "").strip().lower() or "anthropic"
    provider = str(config.get("provider") or "").strip().lower()
    if provider == "wally":
        return "openai"
    if provider in {"opencode", "opencode-go", "minimax", "glm"}:
        from .opencode_go_catalog import opencode_go_model_transport
        return opencode_go_model_transport(model) or configured
    return configured


def _anthropic_messages_url(base_url: str) -> str:
    normalized = base_url.rstrip("/")
    if normalized.endswith("/v1"):
        return f"{normalized}/messages"
    return f"{normalized}/v1/messages"


def _review_with_profile(
    *,
    user_text: str,
    assistant_text: str,
    profile: str,
    data_dir: str | None,
    timeout_s: int,
    log_fn,
) -> list[MemoryReviewItem]:
    config = get_profile_config(profile)
    if profile_backend_name(config) != "cc-backend":
        return []

    base_url = str(config.get("base_url") or "http://127.0.0.1:8001").rstrip("/")
    model = profile_model_name(config)
    if not model:
        return []

    system_prompt = _review_system_prompt()
    user_prompt = _review_user_prompt(
        user_text=user_text,
        assistant_text=assistant_text,
        existing_memory=_existing_memory_context(data_dir),
    )
    api_format = _review_api_format(config, model)
    if api_format == "openai":
        payload = {
            "model": model,
            "max_tokens": 1200,
            "temperature": 0.1,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        }
        response = _http_post_json(
            f"{base_url}/chat/completions",
            payload,
            timeout_s=timeout_s,
            headers=_openai_request_headers(config, api_format=api_format),
        )
    else:
        payload = {
            "model": model,
            "max_tokens": 1200,
            "temperature": 0.1,
            "system": system_prompt,
            "messages": [{"role": "user", "content": user_prompt}],
        }
        response = _http_post_json(
            _anthropic_messages_url(base_url),
            payload,
            timeout_s=timeout_s,
            headers=_openai_request_headers(config, api_format=api_format) if str(config.get("provider") or "").strip().lower() in {"opencode", "opencode-go", "minimax", "glm"} else None,
        )
    text = _extract_text(response)
    items = _parse_review_items(text)
    if items:
        log_fn(f"[memory-review] model proposed {len(items)} item(s)")
    else:
        log_fn("[memory-review] nothing to save")
    return items


def _review_system_prompt() -> str:
    return (
        "Ты отдельный фоновый memory-review процесс. Твоя задача - решить, "
        "есть ли в последнем ходе durable память для будущих сессий.\n\n"
        "Сохраняй proactive только если это реально избавит Игоря от повторения информации:\n"
        "- устойчивые предпочтения пользователя;\n"
        "- стабильные личные факты;\n"
        "- явные коррекции о том, как агент должен себя вести;\n"
        "- долгоживущие правила проекта, окружение, workflow, tool quirks.\n\n"
        "Не сохраняй в long: похвалу, благодарность, разовую эмоцию, ход текущего диалога, "
        "отчёт о выполненной задаче, временный TODO, вопрос пользователя, твой ответ, "
        "отрицательные псевдо-факты вида 'Игорь не указал ...'.\n"
        "Похвала/благодарность/эмоциональная оценка допустима только как daily, "
        "если она полезна как контекст сегодняшнего общения.\n"
        "Если факт нужен только для поиска по прошлым диалогам, выбирай action=session_only.\n\n"
        "Если такой факт уже есть в existing-memory или отличается только формулировкой, "
        "не сохраняй дубль и верни пустой список.\n"
        "Если пользователь явно попросил исправить или удалить старый факт и дословно указал его, "
        "можно вернуть action=replace или action=forget. target должен быть точной полной записью из раздела Saved facts в MEMORY; "
        "для replace поле fact содержит исправленный факт, основанный только на сообщении пользователя. "
        "Не исправляй и не удаляй записи по собственному предположению, не трогай USER, IDENTITY, SOUL и дневники.\n\n"
        "Верни только JSON без markdown:\n"
        "{\n"
        '  "items": [\n'
        "    {\n"
        '      "action": "save|replace|forget|discard|session_only",\n'
        '      "scope": "long|daily",\n'
        '      "kind": "user_preference|stable_user_fact|behavior_instruction|project_rule|environment_fact|daily_note|other",\n'
        '      "target": "для replace/forget — точная старая запись, явно процитированная пользователем",\n'
        '      "fact": "для save — новый факт; для replace — исправленный факт",\n'
        '      "reason": "коротко почему"\n'
        "    }\n"
        "  ]\n"
        "}\n"
        'Если сохранять нечего, верни {"items":[]}.'
    )


def _review_user_prompt(*, user_text: str, assistant_text: str, existing_memory: str = "") -> str:
    user_part = _clip(user_text)
    assistant_part = _clip(assistant_text)
    memory_part = _clip(existing_memory, limit=12000)
    return (
        "<existing-memory>\n"
        "[System note: это уже сохранённая память. Не сохраняй новый факт, если он уже есть здесь или является семантическим дублем.]\n"
        f"{memory_part}\n"
        "</existing-memory>\n\n"
        "<last-user-message>\n"
        f"{user_part}\n"
        "</last-user-message>\n\n"
        "<last-assistant-response>\n"
        f"{assistant_part}\n"
        "</last-assistant-response>\n\n"
        "Реши, нужно ли что-то сохранить в память. Помни: это review, не ответ пользователю."
    )


def _existing_memory_context(data_dir: str | None) -> str:
    try:
        paths = get_foundation_paths(data_dir)
        chunks: list[str] = []
        for label, path in (("USER", paths.user), ("MEMORY", paths.memory_index)):
            resolved = Path(path)
            if resolved.exists():
                text = resolved.read_text(encoding="utf-8", errors="replace").strip()
                if text:
                    chunks.append(f"[{label}]\n{text}")
        return "\n\n".join(chunks)
    except Exception:
        return ""


def _clip(text: str, *, limit: int = MAX_REVIEW_TEXT_CHARS) -> str:
    normalized = str(text or "").strip()
    if len(normalized) <= limit:
        return normalized
    half = limit // 2
    return normalized[:half].rstrip() + "\n...[truncated]...\n" + normalized[-half:].lstrip()


_OPENCODE_SESSION_ID = (
    os.environ.get("FACTORY_OPENCODE_SESSION", "").strip() or f"factory-{uuid.uuid4().hex}"
)


def _openai_request_headers(config: dict[str, Any], *, api_format: str | None = None) -> dict[str, str]:
    provider = str(config.get("provider") or "").strip().lower()
    if provider == "wally":
        try:
            from .wally_auth import WallyAuthError, get_wally_access_token
        except ImportError:  # pragma: no cover - direct script fallback
            from wally_auth import WallyAuthError, get_wally_access_token

        try:
            token = get_wally_access_token()
        except WallyAuthError:
            return {}
        return {"Authorization": f"Bearer {token}"}
    if provider == "openrouter":
        key = (
            os.environ.get("OPENROUTER_API_KEY", "").strip()
            or os.environ.get("CODEX_OPENROUTER_API_KEY", "").strip()
            or os.environ.get("FACTORY_OPENROUTER_API_KEY", "").strip()
        )
    elif provider == "minimax":
        key = (
            os.environ.get("ANTHROPIC_AUTH_TOKEN", "").strip()
            or os.environ.get("MINIMAX_API_KEY", "").strip()
        )
    elif provider == "glm":
        key = (
            os.environ.get("ANTHROPIC_AUTH_TOKEN", "").strip()
            or os.environ.get("GLM_API_KEY", "").strip()
        )
    else:
        key = os.environ.get("OPENCODE_API_KEY", "").strip()
    headers = {}
    if provider in {"opencode", "opencode-go"}:
        headers.update(
            {
                "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36",
                "Origin": "https://opencode.ai",
                "x-opencode-session": _OPENCODE_SESSION_ID,
            }
        )
    if key:
        effective_format = str(api_format or config.get("api_format") or "").strip().lower()
        if effective_format == "anthropic":
            headers["x-api-key"] = key
        else:
            headers["Authorization"] = f"Bearer {key}"
    return headers


def _http_post_json(
    url: str,
    payload: dict[str, Any],
    *,
    timeout_s: int,
    headers: dict[str, str] | None = None,
) -> dict[str, Any]:
    request_headers = {
        "Content-Type": "application/json",
        "x-api-key": "dummy",
        "anthropic-version": "2023-06-01",
    }
    if headers is not None:
        request_headers = {"Content-Type": "application/json", **headers}
    started = time.time()
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=request_headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = ""
        try:
            detail = exc.read().decode("utf-8", errors="replace")[:300]
        except Exception:
            pass
        raise RuntimeError(f"HTTP {exc.code}: {detail}") from exc
    except Exception as exc:
        elapsed = round(time.time() - started, 1)
        raise RuntimeError(f"{exc} after {elapsed}s") from exc


def _extract_text(response: dict[str, Any]) -> str:
    content = response.get("content") if isinstance(response, dict) else None
    if isinstance(content, list):
        parts: list[str] = []
        for block in content:
            if isinstance(block, dict) and block.get("type") == "text":
                text = block.get("text")
                if isinstance(text, str):
                    parts.append(text)
        return "\n".join(parts).strip()
    choices = response.get("choices") if isinstance(response, dict) else None
    if isinstance(choices, list) and choices:
        choice = choices[0]
        message = choice.get("message") if isinstance(choice, dict) else None
        text = message.get("content") if isinstance(message, dict) else None
        return text.strip() if isinstance(text, str) else ""
    return ""


def _parse_review_items(text: str) -> list[MemoryReviewItem]:
    payload = _load_json_object(text)
    raw_items = payload.get("items") if isinstance(payload, dict) else None
    if not isinstance(raw_items, list):
        return []

    items: list[MemoryReviewItem] = []
    for raw in raw_items:
        if not isinstance(raw, dict):
            continue
        action = str(raw.get("action") or "discard").strip().lower()
        fact = str(raw.get("fact") or "").strip()
        target = str(raw.get("target") or "").strip()
        if action not in {"save", "forget", "replace"}:
            continue
        if action == "save" and not fact:
            continue
        if action in {"forget", "replace"} and (not target or (action == "replace" and not fact)):
            continue
        scope = str(raw.get("scope") or "long").strip().lower()
        if scope not in {"long", "daily"}:
            scope = "long"
        items.append(
            MemoryReviewItem(
                action=action,
                scope=scope,
                fact=fact,
                kind=str(raw.get("kind") or "").strip(),
                reason=str(raw.get("reason") or "").strip(),
                target=target,
            )
        )
    return items


def _load_json_object(text: str) -> dict[str, Any]:
    cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", str(text or "").strip(), flags=re.I | re.S)
    try:
        parsed = json.loads(cleaned)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", cleaned, flags=re.S)
        if not match:
            return {}
        try:
            parsed = json.loads(match.group(0))
        except json.JSONDecodeError:
            return {}
    return parsed if isinstance(parsed, dict) else {}


def _dedupe_key(fact: str) -> str:
    return re.sub(r"\s+", " ", fact.strip().lower())
