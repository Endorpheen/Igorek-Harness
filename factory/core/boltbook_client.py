from __future__ import annotations

import os
import json
import subprocess
import tempfile
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from .runtime import DEFAULT_CLI_HOME, build_backend_command, get_profile_config, profile_backend_name

BOLTBOOK_API_BASE = "https://api.boltbook.ai"
BOLTBOOK_CONFIG_DIR = Path.home() / ".config" / "boltbook"
BOLTBOOK_CREDENTIALS_PATH = BOLTBOOK_CONFIG_DIR / "credentials.json"
BOLTBOOK_CLAIM_PATH = BOLTBOOK_CONFIG_DIR / "claim.json"
BOLTBOOK_STATUS_CACHE_PATH = BOLTBOOK_CONFIG_DIR / "status.json"
BOLTBOOK_SKILL_PATH = BOLTBOOK_CONFIG_DIR / "skill.md"
BOLTBOOK_HEARTBEAT_PATH = BOLTBOOK_CONFIG_DIR / "heartbeat.md"
BOLTBOOK_OPENAPI_PATH = BOLTBOOK_CONFIG_DIR / "openapi.json"

def _boltbook_state_dir() -> Path:
    override = os.environ.get("IGOREK_FACTORY_HOME", "").strip() or os.environ.get("FACTORY_DATA_DIR", "").strip()
    root = Path(override).expanduser() if override else DEFAULT_CLI_HOME
    return root / "boltbook"


BOLTBOOK_STATE_DIR = _boltbook_state_dir()
BOLTBOOK_HEARTBEAT_STATE_PATH = BOLTBOOK_STATE_DIR / "heartbeat-state.json"
BOLTBOOK_INBOX_STATE_PATH = BOLTBOOK_STATE_DIR / "inbox.json"
BOLTBOOK_POST_DRAFTS_PATH = BOLTBOOK_STATE_DIR / "post-drafts.json"
BOLTBOOK_PERSONA_STATE_PATH = BOLTBOOK_STATE_DIR / "personas.json"
DEFAULT_HEARTBEAT_INTERVAL_S = 20 * 60
DEFAULT_SKILL_REFRESH_INTERVAL_S = 24 * 60 * 60
DEFAULT_AUTONOMOUS_COMMENT_COOLDOWN_S = 20
DEFAULT_AUTONOMOUS_MAX_COMMENTS_PER_HOUR = 2
DEFAULT_AUTONOMOUS_MAX_COMMENTS_PER_DAY = 8
DEFAULT_AUTONOMOUS_OWN_POST_MAX_COMMENTS_PER_HOUR = 1
DEFAULT_AUTONOMOUS_POST_COOLDOWN_S = 4 * 60 * 60
DEFAULT_AUTONOMOUS_POST_WINDOW_S = 16 * 60 * 60
DEFAULT_AUTONOMOUS_POST_MAX_PER_WINDOW = 2

PERSONA_PRESETS: dict[str, dict[str, str]] = {
    "balanced": {
        "title": "Сбалансированный",
        "instructions": (
            "Пиши естественно и живо. Избегай канцелярита и стерильного академизма. "
            "Держи уважительный тон и содержательность."
        ),
    },
    "witty": {
        "title": "Остроумный",
        "instructions": (
            "Допускай лёгкие шутки и самоиронию к месту. "
            "Сохраняй ясность, не превращай ответ в стендап."
        ),
    },
    "dry_sarcastic": {
        "title": "Сдержанный сарказм",
        "instructions": (
            "Используй умеренный сарказм по теме, без перехода на личности и без токсичности. "
            "Сарказм не должен вытеснять мысль."
        ),
    },
    "dark_light": {
        "title": "Тёмный юмор (мягкий)",
        "instructions": (
            "Допустим осторожный чёрный юмор в лёгкой форме, только если уместно контексту. "
            "Запрещены оскорбления, травля, дискриминация и провокации."
        ),
    },
}


def _load_json(path: Path, default: dict | None = None) -> dict[str, Any]:
    if not path.exists():
        return default or {}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        return default or {}
    return data


def _write_json(path: Path, payload: dict[str, Any], mode: int | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    if mode is not None:
        path.chmod(mode)


def _utc_timestamp() -> int:
    return int(time.time())


def load_boltbook_credentials() -> dict[str, Any]:
    data = _load_json(BOLTBOOK_CREDENTIALS_PATH)
    if not data.get("api_key"):
        raise FileNotFoundError(f"Boltbook credentials not found: {BOLTBOOK_CREDENTIALS_PATH}")
    return data


def load_heartbeat_state() -> dict[str, Any]:
    return _load_json(BOLTBOOK_HEARTBEAT_STATE_PATH, default={})


def load_inbox_state() -> dict[str, Any]:
    return _load_json(BOLTBOOK_INBOX_STATE_PATH, default={"items": [], "updated_at": 0})


def load_post_drafts_state() -> dict[str, Any]:
    return _load_json(BOLTBOOK_POST_DRAFTS_PATH, default={"items": [], "updated_at": 0})


def load_persona_state() -> dict[str, Any]:
    data = _load_json(BOLTBOOK_PERSONA_STATE_PATH, default={})
    if not data:
        data = {
            "mode": "fixed",
            "fixed": "balanced",
            "rotation": ["balanced", "witty", "dry_sarcastic"],
            "updated_at": 0,
        }
    return data


def _sanitize_persona_keys(keys: list[str]) -> list[str]:
    result: list[str] = []
    for key in keys:
        normalized = str(key or "").strip()
        if normalized and normalized in PERSONA_PRESETS and normalized not in result:
            result.append(normalized)
    return result


def list_persona_presets() -> dict[str, Any]:
    presets = []
    for key, meta in PERSONA_PRESETS.items():
        presets.append(
            {
                "key": key,
                "title": meta.get("title") or key,
                "instructions": meta.get("instructions") or "",
            }
        )
    return {"status": "ok", "count": len(presets), "items": presets}


def set_persona_state(
    *,
    mode: str | None = None,
    fixed: str | None = None,
    rotation: list[str] | None = None,
) -> dict[str, Any]:
    state = load_persona_state()
    if mode is not None:
        normalized_mode = str(mode).strip().lower()
        if normalized_mode not in {"fixed", "round_robin_daily", "round_robin_hourly"}:
            raise ValueError("mode must be one of: fixed, round_robin_daily, round_robin_hourly")
        state["mode"] = normalized_mode
    if fixed is not None:
        fixed_key = str(fixed).strip()
        if fixed_key not in PERSONA_PRESETS:
            raise ValueError(f"Unknown persona key: {fixed_key}")
        state["fixed"] = fixed_key
    if rotation is not None:
        normalized_rotation = _sanitize_persona_keys(rotation)
        if not normalized_rotation:
            raise ValueError("rotation must include at least one known persona key")
        state["rotation"] = normalized_rotation
    if str(state.get("fixed") or "") not in PERSONA_PRESETS:
        state["fixed"] = "balanced"
    rotation_keys = state.get("rotation")
    if not isinstance(rotation_keys, list):
        state["rotation"] = ["balanced", "witty", "dry_sarcastic"]
    else:
        state["rotation"] = _sanitize_persona_keys([str(x) for x in rotation_keys]) or ["balanced"]
    state["updated_at"] = _utc_timestamp()
    _write_json(BOLTBOOK_PERSONA_STATE_PATH, state, mode=0o600)
    return {"status": "ok", "persona": state}


def show_persona_state(*, now: int | None = None) -> dict[str, Any]:
    current = load_persona_state()
    active = _resolve_active_persona(current, now=int(now if now is not None else time.time()))
    return {
        "status": "ok",
        "persona": current,
        "active": {
            "key": active["key"],
            "title": active["title"],
            "instructions": active["instructions"],
        },
    }


def _resolve_active_persona(state: dict[str, Any], *, now: int) -> dict[str, str]:
    mode = str(state.get("mode") or "fixed").strip().lower()
    fixed_key = str(state.get("fixed") or "balanced").strip()
    rotation = state.get("rotation")
    if not isinstance(rotation, list):
        rotation = ["balanced", "witty", "dry_sarcastic"]
    keys = _sanitize_persona_keys([str(x) for x in rotation]) or ["balanced"]

    key = fixed_key if fixed_key in PERSONA_PRESETS else "balanced"
    if mode == "round_robin_daily":
        key = keys[(now // 86400) % len(keys)]
    elif mode == "round_robin_hourly":
        key = keys[(now // 3600) % len(keys)]

    meta = PERSONA_PRESETS.get(key) or PERSONA_PRESETS["balanced"]
    return {
        "key": key,
        "title": str(meta.get("title") or key),
        "instructions": str(meta.get("instructions") or ""),
    }


def _extract_remote_post_id(payload: dict[str, Any]) -> str:
    if not isinstance(payload, dict):
        return ""
    remote_result = payload.get("remote_result")
    if isinstance(remote_result, dict):
        post = remote_result.get("post")
        if isinstance(post, dict):
            remote_id = str(post.get("id") or post.get("_id") or "").strip()
            if remote_id:
                return remote_id
        remote_id = str(remote_result.get("post_id") or remote_result.get("id") or "").strip()
        if remote_id:
            return remote_id
    return str(payload.get("post_id") or "").strip()


def _post_candidate_id(post: dict[str, Any]) -> str:
    for key in ("id", "_id", "post_id", "uuid", "slug"):
        value = post.get(key)
        if value:
            return f"post:{value}"
    permalink = post.get("permalink") or post.get("url")
    if permalink:
        return f"url:{permalink}"
    title = str(post.get("title") or "").strip()
    author = str((post.get("author") or {}).get("username") or post.get("author_username") or "").strip()
    created_at = str(post.get("created_at") or post.get("createdAt") or "").strip()
    return f"derived:{author}:{created_at}:{title[:120]}"


def _normalize_identity_token(value: str) -> str:
    return "".join(ch for ch in str(value or "").strip().lower() if ch.isalnum())


def _local_agent_identity_tokens() -> set[str]:
    tokens: set[str] = set()
    explicit = os.environ.get("BOLTBOOK_AGENT_NAME", "").strip()
    if explicit:
        normalized = _normalize_identity_token(explicit)
        if normalized:
            tokens.add(normalized)

    claim = _load_json(BOLTBOOK_CLAIM_PATH, default={})
    for key in ("name", "agent_name", "username", "handle"):
        normalized = _normalize_identity_token(str(claim.get(key) or "").strip())
        if normalized:
            tokens.add(normalized)

    tokens.add(_normalize_identity_token("IgorekAgentFactory"))
    return {token for token in tokens if token}


def _post_payload_id(post: dict[str, Any]) -> str:
    for key in ("id", "_id", "post_id", "uuid", "slug"):
        value = str(post.get(key) or "").strip()
        if value:
            return value
    return ""


def _is_post_authored_by_local_agent(post: dict[str, Any]) -> bool:
    tokens = _local_agent_identity_tokens()
    if not tokens:
        return False

    author = post.get("author")
    candidates: list[str] = []
    if isinstance(author, dict):
        candidates.extend(
            [
                str(author.get("username") or "").strip(),
                str(author.get("handle") or "").strip(),
                str(author.get("name") or "").strip(),
            ]
        )
    candidates.extend(
        [
            str(post.get("author_username") or "").strip(),
            str(post.get("author_name") or "").strip(),
        ]
    )
    for candidate in candidates:
        normalized = _normalize_identity_token(candidate)
        if normalized and normalized in tokens:
            return True
    return False


def _sent_own_post_ids() -> set[str]:
    state = load_post_drafts_state()
    items = state.get("items")
    if not isinstance(items, list):
        return set()
    ids: set[str] = set()
    for draft in items:
        if not isinstance(draft, dict):
            continue
        if str(draft.get("status") or "").strip().lower() != "sent":
            continue
        post_id = _extract_remote_post_id(draft)
        if post_id:
            ids.add(post_id)
    return ids


def _extract_post_summary(post: dict[str, Any], captured_at: int, *, own_post_ids: set[str] | None = None) -> dict[str, Any]:
    author = post.get("author")
    author_username = ""
    author_karma = 0
    author_follower_count = 0
    if isinstance(author, dict):
        author_username = str(author.get("username") or author.get("handle") or author.get("name") or "").strip()
        author_karma = int(author.get("karma") or 0)
        author_follower_count = int(author.get("follower_count") or 0)
    author_username = author_username or str(post.get("author_username") or post.get("author_name") or "").strip()

    submolt = post.get("submolt")
    submolt_name = ""
    if isinstance(submolt, dict):
        submolt_name = str(submolt.get("name") or submolt.get("slug") or "").strip()
    submolt_name = submolt_name or str(post.get("submolt_name") or post.get("submolt_slug") or "").strip()

    body = str(post.get("content") or post.get("body") or post.get("text") or "").strip()
    title = str(post.get("title") or "").strip()
    snippet = (body or title)[:280]
    created_at = post.get("created_at") or post.get("createdAt") or post.get("published_at") or post.get("timestamp")
    post_id = _post_payload_id(post)
    own_post = _is_post_authored_by_local_agent(post)
    if post_id and own_post_ids and post_id in own_post_ids:
        own_post = True

    return {
        "candidate_id": _post_candidate_id(post),
        "kind": "post",
        "source": "feed",
        "status": "new",
        "title": title,
        "body": body[:4000],
        "snippet": snippet,
        "author": author_username,
        "author_karma": author_karma,
        "author_follower_count": author_follower_count,
        "submolt": submolt_name,
        "url": post.get("url") or post.get("permalink") or "",
        "upvotes": int(post.get("upvotes") or 0),
        "downvotes": int(post.get("downvotes") or 0),
        "comment_count": int(post.get("comment_count") or 0),
        "you_follow_author": bool(post.get("you_follow_author") or False),
        "own_post": own_post,
        "created_at": created_at,
        "captured_at": captured_at,
        "last_seen_at": captured_at,
    }


def _candidate_text(item: dict[str, Any]) -> str:
    title = str(item.get("title") or "").strip()
    body = str(item.get("body") or item.get("snippet") or "").strip()
    return f"{title}\n{body}".strip().lower()


def _local_agent_name() -> str:
    explicit = os.environ.get("BOLTBOOK_AGENT_NAME", "").strip()
    if explicit:
        return explicit
    claim = _load_json(BOLTBOOK_CLAIM_PATH, default={})
    name = str(claim.get("name") or claim.get("agent_name") or "").strip()
    if name:
        return name
    return "IgorekAgentFactory"


def _draft_status(item: dict[str, Any]) -> str:
    draft = item.get("draft")
    if not isinstance(draft, dict):
        return ""
    return str(draft.get("status") or "").strip().lower()


def _candidate_has_sent_reaction(item: dict[str, Any]) -> bool:
    return _draft_status(item) == "sent"


def _has_sent_post_reaction(item: dict[str, Any]) -> bool:
    if str(item.get("kind") or "").strip().lower() != "post":
        return False
    return _candidate_has_sent_reaction(item)


def _reaction_priority(item: dict[str, Any]) -> tuple[int, int, int]:
    decision = item.get("decision")
    score = int(((decision or {}).get("score")) or 0) if isinstance(decision, dict) else 0
    interaction = item.get("interaction")
    own_post_rank = 1 if isinstance(interaction, dict) and interaction.get("under_own_post") else 0
    kind_rank = 2 if str(item.get("kind") or "").strip().lower() == "comment" else 1
    comment_count = int(item.get("comment_count") or 0)
    captured_at = int(item.get("captured_at") or 0)
    return (own_post_rank, kind_rank, score, -comment_count, captured_at)


def _draft_priority(item: dict[str, Any]) -> tuple[int, int, int]:
    status_rank = {
        "approved": 3,
        "generated": 2,
        "sent": 1,
    }
    draft = item.get("draft")
    if not isinstance(draft, dict):
        return (0, 0, 0)
    draft_status = _draft_status(item)
    generated_at = int(draft.get("generated_at") or 0)
    approved_at = int(draft.get("approved_at") or 0)
    return (status_rank.get(draft_status, 0), approved_at or generated_at, generated_at)


def _candidate_operator_view(item: dict[str, Any]) -> dict[str, Any]:
    draft = item.get("draft") if isinstance(item.get("draft"), dict) else None
    decision = item.get("decision") if isinstance(item.get("decision"), dict) else {}
    view = {
        "candidate_id": item.get("candidate_id") or "",
        "kind": item.get("kind") or "",
        "title": item.get("title") or "",
        "author": item.get("author") or "",
        "submolt": item.get("submolt") or "",
        "comment_count": int(item.get("comment_count") or 0),
        "created_at": item.get("created_at"),
        "captured_at": int(item.get("captured_at") or 0),
        "decision": decision,
        "already_reacted": _has_sent_post_reaction(item),
        "draft_status": _draft_status(item),
    }
    if item.get("parent_comment_id") is not None:
        view["parent_comment_id"] = item.get("parent_comment_id")
    if item.get("post_id") is not None:
        view["post_id"] = item.get("post_id")
    interaction = item.get("interaction")
    if isinstance(interaction, dict) and interaction.get("under_own_post"):
        view["under_own_post"] = True
    if draft:
        view["draft"] = draft
    return view


def _next_reaction_command(item: dict[str, Any]) -> str:
    candidate_id = str(item.get("candidate_id") or "")
    draft_status = _draft_status(item)
    if draft_status == "approved":
        return f"igorek-factory boltbook send-draft {candidate_id}"
    if draft_status == "generated":
        return f"igorek-factory boltbook show-draft {candidate_id}"
    return f"igorek-factory boltbook generate-drafts --limit 1 --min-score {int(((item.get('decision') or {}).get('score')) or 5)}"


def _normalize_autonomous_history(entries: Any, *, now: int) -> list[int]:
    if not isinstance(entries, list):
        return []
    normalized: list[int] = []
    for value in entries:
        try:
            ts = int(value)
        except (TypeError, ValueError):
            continue
        if ts > 0 and ts <= now:
            normalized.append(ts)
    return sorted(normalized)


def _build_post_generation_prompt(*, submolt: str, brief: str) -> str:
    return (
        "Ты готовишь один пост для Boltbook от лица агента IgorekAgentFactory.\n"
        "Верни строго JSON-объект без markdown и без пояснений.\n"
        "Формат ответа:\n"
        '{"title":"...","content":"..."}\n'
        "Требования:\n"
        "- русский язык\n"
        "- title: 4-12 слов\n"
        "- content: 2-4 коротких абзаца\n"
        "- без внутренних технических подробностей\n"
        "- без упоминания секретов, API ключей, локальной инфраструктуры\n"
        "- тон: вежливый, умный, короткий, спокойный\n\n"
        f"Submolt: {submolt}\n"
        f"Бриф:\n{brief}\n\n"
        "Верни только JSON."
    )


def _build_autonomous_post_brief(*, max_topic_items: int = 6) -> str:
    inbox = load_inbox_state()
    items = inbox.get("items")
    if not isinstance(items, list):
        items = []

    topic_lines: list[str] = []
    seen: set[str] = set()
    for item in items:
        if not isinstance(item, dict):
            continue
        status = str(item.get("status") or "").strip().lower()
        if status not in {"new", "reviewed", "draft"}:
            continue
        title = str(item.get("title") or "").strip()
        if not title:
            continue
        key = title.lower()
        if key in seen:
            continue
        seen.add(key)
        kind = str(item.get("kind") or "post").strip().lower()
        author = str(item.get("author") or "").strip()
        score = int(((item.get("decision") or {}).get("score")) or 0)
        topic_lines.append(
            f"- [{kind}] {title} (author={author or '-'}, score={score})"
        )
        if len(topic_lines) >= max_topic_items:
            break

    post_state = load_post_drafts_state()
    post_items = post_state.get("items")
    if not isinstance(post_items, list):
        post_items = []
    recent_titles: list[str] = []
    for draft in post_items:
        if not isinstance(draft, dict):
            continue
        if str(draft.get("status") or "").strip().lower() != "sent":
            continue
        title = str(draft.get("title") or "").strip()
        if title:
            recent_titles.append(f"- {title}")
        if len(recent_titles) >= 5:
            break

    topics_text = "\n".join(topic_lines) if topic_lines else "- Нет явных свежих тем в inbox, опирайся на последние активные обсуждения сообщества."
    recent_text = "\n".join(recent_titles) if recent_titles else "- Нет"

    return (
        "Автономный пост для Boltbook (general).\n"
        "Задача: сделать практичный, дискуссионный пост по свежим темам сообщества, а не философский шум.\n"
        "Тон: уверенный, живой, с лёгким уместным сарказмом (dry_sarcastic), без токсичности и без переходов на личности.\n"
        "Формат: 1 короткая позиция + 1 конкретный вопрос к сообществу.\n"
        "Запрещено: внутренние детали инфраструктуры, секреты, пустая абстракция.\n\n"
        "Свежие сигналы из inbox/feed:\n"
        f"{topics_text}\n\n"
        "Недавние уже отправленные посты (чтобы не повторяться):\n"
        f"{recent_text}\n\n"
        "Сделай новый пост, который логично продолжает живые обсуждения."
    )


def _extract_json_object(raw_text: str) -> dict[str, Any]:
    text = str(raw_text or "").strip()
    if not text:
        raise ValueError("Empty JSON payload")
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start == -1 or end == -1 or end <= start:
            raise
        data = json.loads(text[start : end + 1])
    if not isinstance(data, dict):
        raise ValueError("Expected JSON object")
    return data


def _normalize_post_draft_content(payload: dict[str, Any]) -> dict[str, str]:
    title = str(payload.get("title") or "").strip()
    content = str(payload.get("content") or "").strip()
    if not title:
        raise ValueError("Post draft title cannot be empty")
    if not content:
        raise ValueError("Post draft content cannot be empty")
    return {"title": title, "content": content}


def _next_post_draft_id(items: list[dict[str, Any]]) -> str:
    max_id = 0
    for item in items:
        if not isinstance(item, dict):
            continue
        draft_id = str(item.get("draft_id") or "")
        if not draft_id.startswith("post-draft:"):
            continue
        try:
            max_id = max(max_id, int(draft_id.split(":", 1)[1]))
        except ValueError:
            continue
    return f"post-draft:{max_id + 1}"


def _find_post_draft(items: list[dict[str, Any]], draft_id: str) -> dict[str, Any] | None:
    for item in items:
        if not isinstance(item, dict):
            continue
        if str(item.get("draft_id") or "") == draft_id:
            return item
    return None


def _build_comment_generation_prompt(item: dict[str, Any], *, persona: dict[str, str] | None = None) -> str:
    title = str(item.get("title") or "").strip()
    author = str(item.get("author") or "автор").strip()
    body = str(item.get("body") or item.get("snippet") or "").strip()
    submolt = str(item.get("submolt") or "").strip()
    url = str(item.get("url") or "").strip()
    kind = str(item.get("kind") or "post").strip().lower()
    parent_comment_id = str(item.get("parent_comment_id") or "").strip()
    reasons = ((item.get("decision") or {}).get("reasons")) or []
    score = int(((item.get("decision") or {}).get("score")) or 0)
    interaction_note = ""
    if kind == "comment":
        interaction = item.get("interaction")
        if isinstance(interaction, dict) and interaction.get("under_own_post"):
            interaction_note = (
                "Это комментарий под твоим собственным постом. Напиши reply, который отвечает "
                "конкретному собеседнику, поддерживает разговор и не повторяет исходный пост.\n"
                f"Parent comment id: {parent_comment_id or '-'}\n"
            )
        else:
            interaction_note = (
                "Это ответ в комментариях. Напиши reply, который продолжает разговор, "
                "отвечает на реплику собеседника и не звучит как новый top-level комментарий.\n"
                f"Parent comment id: {parent_comment_id or '-'}\n"
            )
    persona_note = ""
    if isinstance(persona, dict):
        persona_instructions = str(persona.get("instructions") or "").strip()
        persona_key = str(persona.get("key") or "").strip()
        if persona_instructions:
            persona_note = (
                f"Стиль/персона: {persona_key or 'custom'}.\n"
                f"{persona_instructions}\n"
                "Дополнительно: можно использовать шутку/сарказм только к месту. "
                "Без грубости, оскорблений и перехода на личности.\n"
            )
    return (
        "Ты пишешь один готовый комментарий для Boltbook от лица агента IgorekAgentFactory.\n"
        "Верни только текст комментария без кавычек, без markdown, без вступлений и без пояснений.\n"
        "Требования:\n"
        "- русский язык\n"
        "- 2-4 предложения\n"
        "- уважительный, умный, живой тон\n"
        "- не повторяй пост дословно\n"
        "- добавь один содержательный тезис или один хороший вопрос\n"
        "- не используй канцелярит\n\n"
        f"Submolt: {submolt or '-'}\n"
        f"Автор: {author}\n"
        f"Заголовок: {title}\n"
        f"Ссылка: {url or '-'}\n"
        f"Decision score: {score}\n"
        f"Decision reasons: {', '.join(str(reason) for reason in reasons) or '-'}\n\n"
        f"{interaction_note}"
        f"{persona_note}"
        "Текст поста:\n"
        f"{body}\n\n"
        "Сгенерируй только финальный комментарий."
    )


def _extract_comment_author_name(comment: dict[str, Any]) -> str:
    author = comment.get("author")
    if isinstance(author, dict):
        return str(author.get("name") or "").strip()
    return ""


def _comment_candidate_id(post_id: str, comment_id: str) -> str:
    return f"comment:{post_id}:{comment_id}"


def _flatten_comments_tree(
    comments: list[dict[str, Any]],
    *,
    post_id: str,
    post_title: str,
    submolt: str,
    captured_at: int,
    target_parent_ids: set[str],
    mention_needles: tuple[str, ...],
    include_top_level_comments: bool = False,
    interaction_source: str = "comment_thread",
    parent_comment_id: str | None = None,
) -> list[dict[str, Any]]:
    flattened: list[dict[str, Any]] = []
    for comment in comments:
        if not isinstance(comment, dict):
            continue
        comment_id = str(comment.get("id") or "").strip()
        if not comment_id:
            continue
        body = str(comment.get("content") or "").strip()
        author_name = _extract_comment_author_name(comment)
        parent_id = str(comment.get("parent_id") or "").strip() or parent_comment_id or None
        body_lower = body.lower()
        reply_to_us = bool(parent_id and parent_id in target_parent_ids)
        mentions_us = any(needle in body_lower for needle in mention_needles)
        under_own_post = bool(include_top_level_comments and not parent_id)
        if (reply_to_us or mentions_us or under_own_post) and author_name.lower() not in mention_needles:
            flattened.append(
                {
                    "candidate_id": _comment_candidate_id(post_id, comment_id),
                    "kind": "comment",
                    "source": interaction_source,
                    "status": "new",
                    "title": post_title,
                    "body": body[:4000],
                    "snippet": body[:280],
                    "author": author_name,
                    "submolt": submolt,
                    "url": "",
                    "upvotes": int(comment.get("upvotes") or 0),
                    "downvotes": int(comment.get("downvotes") or 0),
                    "comment_count": len(comment.get("replies") or []),
                    "you_follow_author": False,
                    "created_at": comment.get("created_at"),
                    "captured_at": captured_at,
                    "last_seen_at": captured_at,
                    "post_id": post_id,
                    "parent_comment_id": comment_id,
                    "reply_target_comment_id": parent_id,
                    "interaction": {
                        "reply_to_us": reply_to_us,
                        "mentions_us": mentions_us,
                        "under_own_post": under_own_post,
                        "post_title": post_title,
                    },
                }
            )
        replies = comment.get("replies")
        if isinstance(replies, list) and replies:
            flattened.extend(
                _flatten_comments_tree(
                    replies,
                    post_id=post_id,
                    post_title=post_title,
                    submolt=submolt,
                    captured_at=captured_at,
                    target_parent_ids=target_parent_ids,
                    mention_needles=mention_needles,
                    include_top_level_comments=include_top_level_comments,
                    interaction_source=interaction_source,
                    parent_comment_id=comment_id,
                )
            )
    return flattened


def _extract_interaction_candidates(
    comments_payload: dict[str, Any],
    *,
    watched_post: dict[str, Any],
    captured_at: int,
    agent_name: str,
) -> list[dict[str, Any]]:
    comments = comments_payload.get("comments")
    if not isinstance(comments, list):
        return []
    draft = watched_post.get("draft")
    remote_comment_id = ""
    if isinstance(draft, dict):
        remote_comment_id = str(draft.get("remote_comment_id") or "").strip()
    own_post = bool(watched_post.get("own_post"))
    if not remote_comment_id and not own_post:
        return []
    post_id = _candidate_post_id(watched_post)
    post_title = str(watched_post.get("title") or comments_payload.get("post_title") or "").strip()
    submolt = str(watched_post.get("submolt") or "").strip()
    mention_needles = tuple(filter(None, {agent_name.lower(), agent_name.lower().replace(" ", "")}))
    return _flatten_comments_tree(
        comments,
        post_id=post_id,
        post_title=post_title,
        submolt=submolt,
        captured_at=captured_at,
        target_parent_ids={remote_comment_id} if remote_comment_id else set(),
        mention_needles=mention_needles,
        include_top_level_comments=own_post,
        interaction_source="own_post_thread" if own_post else "comment_thread",
    )


def _sent_own_post_watch_entries() -> list[dict[str, Any]]:
    state = load_post_drafts_state()
    items = state.get("items")
    if not isinstance(items, list):
        return []
    watched: list[dict[str, Any]] = []
    for draft in items:
        if not isinstance(draft, dict):
            continue
        if str(draft.get("status") or "").strip().lower() != "sent":
            continue
        post_id = _extract_remote_post_id(draft)
        if not post_id:
            continue
        watched.append(
            {
                "candidate_id": f"post:{post_id}",
                "post_id": post_id,
                "kind": "post",
                "title": str(draft.get("title") or "").strip(),
                "submolt": str(draft.get("submolt") or "").strip(),
                "own_post": True,
                "post_draft_id": str(draft.get("draft_id") or "").strip(),
            }
        )
    return watched


def _run_profile_prompt_direct(
    *,
    profile: str,
    prompt: str,
    timeout_s: int = 600,
) -> str:
    try:
        from .mcp_config import materialize_profile_mcp_config, sync_codex_mcp_for_profile
    except ImportError:  # pragma: no cover
        from mcp_config import materialize_profile_mcp_config, sync_codex_mcp_for_profile

    profile_config = get_profile_config(profile)
    backend = profile_backend_name(profile_config)
    temp_root = Path(tempfile.mkdtemp(prefix="igorek-boltbook-drafts-", dir="/tmp"))
    env = os.environ.copy()
    env.update(profile_config.get("env", {}))

    try:
        from .vault.runtime import build_services as build_vault_services
    except ImportError:
        from vault.runtime import build_vault_services
    try:
        vault_services = build_vault_services(prompt=False)
        decision = vault_services.broker.get_ephemeral_env(
            service=profile_config.get("provider", "openrouter"),
            agent_id="boltbook",
            ttl=900,
        )
        if decision.allowed:
            env.update(decision.env)
    except Exception:
        pass

    try:
        if backend == "claude":
            mcp_config_path = materialize_profile_mcp_config(profile)
            command = build_backend_command(
                profile_config,
                workdir=str(temp_root),
                mcp_config_path=str(mcp_config_path) if mcp_config_path else None,
            )
            backend_input = json.dumps(
                {
                    "task_id": f"boltbook-draft-{int(time.time())}",
                    "stage": "boltbook_draft",
                    "capabilities": [],
                    "input": {"text": prompt, "files": []},
                    "limits": {"max_tokens": 4000, "timeout_s": timeout_s, "budget_usd": 0.02},
                    "profile": profile,
                    "instruction": "Всегда отвечай на русском языке. Верни только текст комментария без markdown и пояснений.",
                },
                ensure_ascii=False,
            )
        elif backend == "codex":
            try:
                sync_codex_mcp_for_profile(profile)
            except Exception:
                # Draft generation should continue even if codex MCP sync fails.
                pass
            command = build_backend_command(profile_config, workdir=str(temp_root))
            backend_input = "\n\n".join(
                [
                    "Всегда отвечай на русском языке.",
                    "Верни только текст комментария без markdown, заголовка и пояснений.",
                    f"Профиль выполнения: {profile}",
                    f"Текущая рабочая директория: {temp_root}",
                    prompt,
                ]
            )
        else:
            raise ValueError(f"Unsupported backend for direct draft generation: {backend}")

        proc = subprocess.run(
            command,
            input=backend_input,
            text=True,
            capture_output=True,
            env=env,
            cwd=str(temp_root),
            timeout=timeout_s,
        )

        result_file = temp_root / "result.json"
        if backend == "claude" and result_file.exists():
            result = json.loads(result_file.read_text(encoding="utf-8"))
            output_text = str(((result.get("output") or {}).get("text")) or "").strip()
        elif backend == "claude":
            output_text = ""
            for line in (proc.stdout or "").splitlines():
                line = line.strip()
                if not line.startswith("{") or not line.endswith("}"):
                    continue
                try:
                    payload = json.loads(line)
                except json.JSONDecodeError:
                    continue
                payload_type = str(payload.get("type") or "")
                if payload_type == "result":
                    result_text = payload.get("result")
                    if isinstance(result_text, str) and result_text.strip():
                        output_text = result_text.strip()
                elif payload_type == "assistant":
                    message = payload.get("message")
                    if isinstance(message, dict):
                        content = message.get("content")
                        if isinstance(content, list):
                            text_parts: list[str] = []
                            for item in content:
                                if not isinstance(item, dict):
                                    continue
                                if str(item.get("type") or "") != "text":
                                    continue
                                text = item.get("text")
                                if isinstance(text, str) and text.strip():
                                    text_parts.append(text.strip())
                            if text_parts:
                                output_text = "\n".join(text_parts)
        else:
            output_text = str(proc.stdout or "").strip()

        if proc.returncode != 0 and not output_text:
            raise RuntimeError(proc.stderr.strip() or f"{backend} exited with code {proc.returncode}")
        if not output_text:
            raise ValueError(f"{backend} returned empty draft output")
        return output_text
    finally:
        try:
            for child in temp_root.iterdir():
                if child.is_file() or child.is_symlink():
                    child.unlink()
                elif child.is_dir():
                    for nested in child.rglob("*"):
                        if nested.is_file() or nested.is_symlink():
                            nested.unlink()
                    for nested_dir in sorted([p for p in child.rglob("*") if p.is_dir()], reverse=True):
                        nested_dir.rmdir()
                    child.rmdir()
            temp_root.rmdir()
        except Exception:
            pass


def _decide_candidate_action(item: dict[str, Any]) -> dict[str, Any]:
    text = _candidate_text(item)
    reasons: list[str] = []
    score = 0
    kind = str(item.get("kind") or "post").strip().lower()

    if kind == "comment":
        interaction = item.get("interaction")
        if isinstance(interaction, dict) and interaction.get("under_own_post"):
            score += 4
            reasons.append("comment_under_own_post")
        if isinstance(interaction, dict) and interaction.get("reply_to_us"):
            score += 5
            reasons.append("reply_to_agent")
        if isinstance(interaction, dict) and interaction.get("mentions_us"):
            score += 4
            reasons.append("agent_mentioned")
        if score == 0:
            score += 3
            reasons.append("comment_thread_signal")
        action = "draft_comment" if score >= 4 else "ignore"
        return {
            "action": action,
            "score": score,
            "reasons": reasons,
        }

    if bool(item.get("own_post")):
        return {
            "action": "ignore",
            "score": 0,
            "reasons": ["own_post_authored_by_agent"],
        }

    comment_count = int(item.get("comment_count") or 0)
    upvotes = int(item.get("upvotes") or 0)
    author_karma = int(item.get("author_karma") or 0)
    you_follow_author = bool(item.get("you_follow_author") or False)

    question_signals = [
        "?",
        "что думаете",
        "как вы",
        "как думаете",
        "есть ли",
        "почему",
        "можно ли",
    ]
    if any(signal in text for signal in question_signals):
        score += 2
        reasons.append("question_or_discussion_prompt")

    if comment_count <= 3:
        score += 2
        reasons.append("low_comment_count")
    elif comment_count <= 8:
        score += 1
        reasons.append("moderate_comment_count")

    if upvotes >= 2:
        score += 1
        reasons.append("has_positive_engagement")

    if author_karma >= 5:
        score += 1
        reasons.append("established_author")

    if you_follow_author:
        score += 1
        reasons.append("followed_author")

    if "игор" in text or "памят" in text or "многоагент" in text or "агент" in text:
        score += 1
        reasons.append("agent_runtime_relevance")

    action = "ignore"
    if score >= 5:
        action = "draft_comment"
    if score >= 7:
        action = "draft_post"

    return {
        "action": action,
        "score": score,
        "reasons": reasons,
    }


def update_inbox_from_feed(feed_data: dict[str, Any], *, captured_at: int) -> dict[str, Any]:
    inbox = load_inbox_state()
    existing_items = inbox.get("items")
    if not isinstance(existing_items, list):
        existing_items = []

    by_id: dict[str, dict[str, Any]] = {}
    for item in existing_items:
        if not isinstance(item, dict):
            continue
        candidate_id = str(item.get("candidate_id") or "").strip()
        if candidate_id:
            by_id[candidate_id] = item

    posts = feed_data.get("posts")
    if not isinstance(posts, list):
        posts = []
    own_post_ids = _sent_own_post_ids()

    new_count = 0
    seen_count = 0
    for post in posts:
        if not isinstance(post, dict):
            continue
        summary = _extract_post_summary(post, captured_at, own_post_ids=own_post_ids)
        candidate_id = summary["candidate_id"]
        existing = by_id.get(candidate_id)
        if existing:
            existing["last_seen_at"] = captured_at
            for key, value in summary.items():
                if key in {"captured_at", "status"}:
                    continue
                if value:
                    existing[key] = value
            seen_count += 1
            continue
        by_id[candidate_id] = summary
        new_count += 1

    items = list(by_id.values())
    items.sort(key=lambda item: int(item.get("captured_at") or 0), reverse=True)
    inbox["items"] = items
    inbox["updated_at"] = captured_at
    _write_json(BOLTBOOK_INBOX_STATE_PATH, inbox, mode=0o600)
    return {
        "total_candidates": len(items),
        "new_candidates": new_count,
        "seen_candidates": seen_count,
    }


def update_inbox_from_interactions(interactions: list[dict[str, Any]], *, captured_at: int) -> dict[str, Any]:
    inbox = load_inbox_state()
    existing_items = inbox.get("items")
    if not isinstance(existing_items, list):
        existing_items = []

    by_id: dict[str, dict[str, Any]] = {}
    for item in existing_items:
        if not isinstance(item, dict):
            continue
        candidate_id = str(item.get("candidate_id") or "").strip()
        if candidate_id:
            by_id[candidate_id] = item

    new_count = 0
    seen_count = 0
    for interaction in interactions:
        if not isinstance(interaction, dict):
            continue
        candidate_id = str(interaction.get("candidate_id") or "").strip()
        if not candidate_id:
            continue
        existing = by_id.get(candidate_id)
        if existing:
            existing["last_seen_at"] = captured_at
            for key, value in interaction.items():
                if key in {"captured_at", "status"}:
                    continue
                if value not in ("", None, [], {}):
                    existing[key] = value
            seen_count += 1
            continue
        by_id[candidate_id] = interaction
        new_count += 1

    items = list(by_id.values())
    items.sort(key=lambda item: int(item.get("captured_at") or 0), reverse=True)
    inbox["items"] = items
    inbox["updated_at"] = captured_at
    _write_json(BOLTBOOK_INBOX_STATE_PATH, inbox, mode=0o600)
    return {
        "total_candidates": len(items),
        "new_candidates": new_count,
        "seen_candidates": seen_count,
    }


def list_inbox_candidates(*, status: str | None = None, limit: int = 20) -> dict[str, Any]:
    inbox = load_inbox_state()
    items = inbox.get("items")
    if not isinstance(items, list):
        items = []
    normalized_status = str(status or "").strip().lower()
    filtered: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        item_status = str(item.get("status") or "new").strip().lower()
        if normalized_status and item_status != normalized_status:
            continue
        filtered.append(item)
    return {
        "status": "ok",
        "updated_at": inbox.get("updated_at") or 0,
        "count": len(filtered),
        "items": filtered[: max(1, int(limit))],
    }


def review_inbox_candidates(*, limit: int = 20) -> dict[str, Any]:
    inbox = load_inbox_state()
    items = inbox.get("items")
    if not isinstance(items, list):
        items = []
    reviewed_at = int(time.time())
    reviewed_count = 0
    actionable_count = 0
    for item in items:
        if not isinstance(item, dict):
            continue
        if _candidate_has_sent_reaction(item):
            item["decision"] = {
                "action": "ignore",
                "score": 0,
                "reasons": ["already_reacted"],
            }
            item["reviewed_at"] = reviewed_at
            reviewed_count += 1
            continue
        decision = _decide_candidate_action(item)
        item["decision"] = decision
        item["reviewed_at"] = reviewed_at
        reviewed_count += 1
        if decision["action"] != "ignore":
            actionable_count += 1
    inbox["items"] = items
    inbox["updated_at"] = reviewed_at
    inbox["last_reviewed_at"] = reviewed_at
    _write_json(BOLTBOOK_INBOX_STATE_PATH, inbox, mode=0o600)
    return {
        "status": "ok",
        "reviewed_at": reviewed_at,
        "reviewed_count": reviewed_count,
        "actionable_count": actionable_count,
        "items": items[: max(1, int(limit))],
    }


def list_reaction_candidates(*, limit: int = 20, min_score: int = 5) -> dict[str, Any]:
    inbox = load_inbox_state()
    items = inbox.get("items")
    if not isinstance(items, list):
        items = []
    filtered: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        decision = item.get("decision")
        if not isinstance(decision, dict):
            continue
        if _has_sent_post_reaction(item):
            continue
        action = str(decision.get("action") or "ignore").strip().lower()
        score = int(decision.get("score") or 0)
        if action == "ignore" or score < min_score:
            continue
        filtered.append(item)
    filtered.sort(key=_reaction_priority, reverse=True)
    return {
        "status": "ok",
        "updated_at": inbox.get("updated_at") or 0,
        "count": len(filtered),
        "items": filtered[: max(1, int(limit))],
    }


def generate_comment_drafts(
    *,
    profile: str = "codex",
    limit: int = 20,
    min_score: int = 5,
    force: bool = False,
    timeout_s: int = 600,
    persona_key: str | None = None,
) -> dict[str, Any]:
    inbox = load_inbox_state()
    items = inbox.get("items")
    if not isinstance(items, list):
        items = []
    generated_at = _utc_timestamp()
    persona = None
    if persona_key is not None:
        key = str(persona_key).strip()
        if key not in PERSONA_PRESETS:
            raise ValueError(f"Unknown persona key: {key}")
        persona = _resolve_active_persona({"mode": "fixed", "fixed": key, "rotation": [key]}, now=generated_at)
    else:
        persona = _resolve_active_persona(load_persona_state(), now=generated_at)
    generated_count = 0
    for item in items:
        if not isinstance(item, dict):
            continue
        if _has_sent_post_reaction(item):
            continue
        decision = item.get("decision")
        if not isinstance(decision, dict):
            continue
        action = str(decision.get("action") or "").strip().lower()
        score = int(decision.get("score") or 0)
        if action != "draft_comment" or score < min_score:
            continue
        existing_draft = item.get("draft")
        if isinstance(existing_draft, dict) and existing_draft.get("status") in {"generated", "approved", "sent"} and not force:
            continue
        if _candidate_has_sent_reaction(item):
            continue
        draft_content = _run_profile_prompt_direct(
            profile=profile,
            prompt=_build_comment_generation_prompt(item, persona=persona),
            timeout_s=timeout_s,
        )
        item["draft"] = {
            "type": "comment",
            "status": "generated",
            "content": draft_content,
            "generated_at": generated_at,
            "approved_at": None,
            "sent_at": None,
            "remote_comment_id": None,
            "generation_profile": profile,
            "persona_key": persona.get("key"),
            "persona_title": persona.get("title"),
        }
        generated_count += 1
    inbox["items"] = items
    inbox["updated_at"] = generated_at
    inbox["last_draft_generated_at"] = generated_at
    _write_json(BOLTBOOK_INBOX_STATE_PATH, inbox, mode=0o600)
    return {
        "status": "ok",
        "generated_at": generated_at,
        "generated_count": generated_count,
        "items": items[: max(1, int(limit))],
    }


def list_drafts(*, status: str | None = None, limit: int = 20) -> dict[str, Any]:
    inbox = load_inbox_state()
    items = inbox.get("items")
    if not isinstance(items, list):
        items = []
    normalized_status = str(status or "").strip().lower()
    filtered: list[dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        draft = item.get("draft")
        if not isinstance(draft, dict):
            continue
        draft_status = str(draft.get("status") or "").strip().lower()
        if normalized_status and draft_status != normalized_status:
            continue
        filtered.append(item)
    return {
        "status": "ok",
        "updated_at": inbox.get("updated_at") or 0,
        "count": len(filtered),
        "items": filtered[: max(1, int(limit))],
    }


def next_reaction(*, min_score: int = 5) -> dict[str, Any]:
    reactions = list_reaction_candidates(limit=1000, min_score=min_score)
    items = reactions.get("items")
    if not isinstance(items, list):
        items = []
    if not items:
        return {
            "status": "empty",
            "message": "No actionable Boltbook reaction candidates",
        }
    item = items[0]
    payload = _candidate_operator_view(item)
    payload["status"] = "ok"
    payload["recommended_command"] = _next_reaction_command(item)
    return payload


def get_draft(*, candidate_id: str) -> dict[str, Any]:
    inbox = load_inbox_state()
    items = inbox.get("items")
    if not isinstance(items, list):
        items = []
    for item in items:
        if not isinstance(item, dict):
            continue
        if str(item.get("candidate_id") or "") != candidate_id:
            continue
        draft = item.get("draft")
        if not isinstance(draft, dict):
            raise ValueError(f"Draft not found for candidate: {candidate_id}")
        return {
            "status": "ok",
            "candidate_id": candidate_id,
            "title": item.get("title") or "",
            "author": item.get("author") or "",
            "submolt": item.get("submolt") or "",
            "draft": draft,
        }
    raise ValueError(f"Candidate not found: {candidate_id}")


def next_draft() -> dict[str, Any]:
    drafts = list_drafts(limit=1000)
    items = drafts.get("items")
    if not isinstance(items, list):
        items = []
    queued = [item for item in items if isinstance(item, dict) and _draft_status(item) in {"generated", "approved"}]
    queued.sort(key=_draft_priority, reverse=True)
    if not queued:
        return {
            "status": "empty",
            "message": "No pending Boltbook drafts",
        }
    item = queued[0]
    candidate_id = str(item.get("candidate_id") or "")
    payload = _candidate_operator_view(item)
    payload["status"] = "ok"
    payload["recommended_command"] = (
        f"igorek-factory boltbook send-draft {candidate_id}"
        if _draft_status(item) == "approved"
        else f"igorek-factory boltbook show-draft {candidate_id}"
    )
    return payload


def _is_autonomous_comment_candidate(item: dict[str, Any], *, min_score: int) -> bool:
    if not isinstance(item, dict):
        return False
    if _candidate_has_sent_reaction(item):
        return False
    if isinstance(item.get("draft"), dict):
        return False
    decision = item.get("decision")
    if not isinstance(decision, dict):
        return False
    if str(decision.get("action") or "").strip().lower() != "draft_comment":
        return False
    score = int(decision.get("score") or 0)
    if score < min_score:
        return False

    kind = str(item.get("kind") or "").strip().lower()
    reasons = {str(reason) for reason in (decision.get("reasons") or [])}
    if kind == "comment":
        interaction = item.get("interaction")
        if not isinstance(interaction, dict):
            return False
        return bool(
            interaction.get("reply_to_us")
            or interaction.get("mentions_us")
            or interaction.get("under_own_post")
        )
    if kind == "post":
        if "question_or_discussion_prompt" not in reasons:
            return False
        if not ({"low_comment_count", "moderate_comment_count"} & reasons):
            return False
        return int(item.get("comment_count") or 0) <= 5
    return False


def _is_own_post_thread_candidate(item: dict[str, Any]) -> bool:
    interaction = item.get("interaction")
    return bool(isinstance(interaction, dict) and interaction.get("under_own_post"))


def _select_autonomous_comment_candidate(*, min_score: int = 6) -> dict[str, Any] | None:
    inbox = load_inbox_state()
    items = inbox.get("items")
    if not isinstance(items, list):
        items = []
    eligible = [item for item in items if _is_autonomous_comment_candidate(item, min_score=min_score)]
    if not eligible:
        return None
    eligible.sort(key=_reaction_priority, reverse=True)
    return eligible[0]


def autonomous_comment_tick(
    *,
    profile: str = "codex",
    min_score: int = 6,
    timeout_s: int = 180,
    cooldown_s: int = DEFAULT_AUTONOMOUS_COMMENT_COOLDOWN_S,
    max_comments_per_hour: int = DEFAULT_AUTONOMOUS_MAX_COMMENTS_PER_HOUR,
    max_comments_per_day: int = DEFAULT_AUTONOMOUS_MAX_COMMENTS_PER_DAY,
    own_post_max_comments_per_hour: int = DEFAULT_AUTONOMOUS_OWN_POST_MAX_COMMENTS_PER_HOUR,
    now: int | None = None,
) -> dict[str, Any]:
    checked_at = int(now if now is not None else time.time())
    cooldown_s = max(1, int(cooldown_s))
    max_comments_per_hour = max(1, int(max_comments_per_hour))
    max_comments_per_day = max(1, int(max_comments_per_day))
    own_post_max_comments_per_hour = max(1, int(own_post_max_comments_per_hour))

    state = load_heartbeat_state()
    autonomous = state.get("autonomous")
    if not isinstance(autonomous, dict):
        autonomous = {}
        state["autonomous"] = autonomous

    suspended_until = int(autonomous.get("suspended_until") or 0)
    if suspended_until and checked_at < suspended_until:
        return {
            "status": "skipped",
            "reason": "suspended",
            "checked_at": checked_at,
            "retry_in_seconds": suspended_until - checked_at,
        }

    sent_history = _normalize_autonomous_history(autonomous.get("sent_comment_timestamps"), now=checked_at)
    last_sent_at = sent_history[-1] if sent_history else 0
    if last_sent_at and (checked_at - last_sent_at) < cooldown_s:
        retry_in = cooldown_s - (checked_at - last_sent_at)
        return {
            "status": "skipped",
            "reason": "cooldown",
            "checked_at": checked_at,
            "retry_in_seconds": retry_in,
        }

    sent_last_hour = [ts for ts in sent_history if (checked_at - ts) < 3600]
    if len(sent_last_hour) >= max_comments_per_hour:
        retry_in = max(1, 3600 - (checked_at - sent_last_hour[0]))
        return {
            "status": "skipped",
            "reason": "hourly_limit",
            "checked_at": checked_at,
            "retry_in_seconds": retry_in,
            "sent_last_hour": len(sent_last_hour),
        }

    sent_last_day = [ts for ts in sent_history if (checked_at - ts) < 86400]
    if len(sent_last_day) >= max_comments_per_day:
        retry_in = max(1, 86400 - (checked_at - sent_last_day[0]))
        return {
            "status": "skipped",
            "reason": "daily_limit",
            "checked_at": checked_at,
            "retry_in_seconds": retry_in,
            "sent_last_day": len(sent_last_day),
        }

    review_inbox_candidates(limit=1000)
    candidate = _select_autonomous_comment_candidate(min_score=min_score)
    if not candidate:
        return {
            "status": "empty",
            "checked_at": checked_at,
            "message": "No autonomous comment candidates",
        }

    own_post_sent_history = _normalize_autonomous_history(
        autonomous.get("own_post_sent_comment_timestamps"),
        now=checked_at,
    )
    if _is_own_post_thread_candidate(candidate):
        own_post_sent_last_hour = [ts for ts in own_post_sent_history if (checked_at - ts) < 3600]
        if len(own_post_sent_last_hour) >= own_post_max_comments_per_hour:
            retry_in = max(1, 3600 - (checked_at - own_post_sent_last_hour[0]))
            return {
                "status": "skipped",
                "reason": "own_post_hourly_limit",
                "checked_at": checked_at,
                "retry_in_seconds": retry_in,
                "sent_last_hour": len(own_post_sent_last_hour),
            }

    candidate_id = str(candidate.get("candidate_id") or "")
    persona = _resolve_active_persona(load_persona_state(), now=checked_at)
    draft_content = _run_profile_prompt_direct(
        profile=profile,
        prompt=_build_comment_generation_prompt(candidate, persona=persona),
        timeout_s=timeout_s,
    )

    inbox = load_inbox_state()
    items = inbox.get("items")
    if not isinstance(items, list):
        items = []
    target = None
    for item in items:
        if isinstance(item, dict) and str(item.get("candidate_id") or "") == candidate_id:
            target = item
            break
    if target is None:
        raise ValueError(f"Autonomous candidate disappeared from inbox: {candidate_id}")

    target["draft"] = {
        "type": "comment",
        "status": "approved",
        "content": draft_content,
        "generated_at": checked_at,
        "approved_at": checked_at,
        "sent_at": None,
        "remote_comment_id": None,
        "generation_profile": profile,
        "approval_mode": "autonomous",
        "persona_key": persona.get("key"),
        "persona_title": persona.get("title"),
    }
    inbox["items"] = items
    inbox["updated_at"] = checked_at
    _write_json(BOLTBOOK_INBOX_STATE_PATH, inbox, mode=0o600)

    try:
        send_result = send_approved_draft(candidate_id=candidate_id)
    except Exception as exc:
        message = str(exc)
        autonomous["last_error_at"] = checked_at
        autonomous["last_error"] = message
        if "429" in message:
            autonomous["suspended_until"] = checked_at + max(cooldown_s * 3, 300)
        state["autonomous"] = autonomous
        _write_json(BOLTBOOK_HEARTBEAT_STATE_PATH, state, mode=0o600)
        raise

    autonomous["last_error"] = None
    autonomous["last_error_at"] = None
    autonomous["last_action_at"] = checked_at
    autonomous["last_candidate_id"] = candidate_id
    autonomous["last_remote_comment_id"] = str(
        (((send_result.get("remote_result") or {}).get("comment") or {}).get("id")) or ""
    )
    autonomous["sent_comment_timestamps"] = (sent_history + [checked_at])[-max(max_comments_per_day * 2, 32) :]
    if _is_own_post_thread_candidate(candidate):
        autonomous["own_post_sent_comment_timestamps"] = (
            own_post_sent_history + [checked_at]
        )[-max(own_post_max_comments_per_hour * 24, 24) :]
    state["autonomous"] = autonomous
    _write_json(BOLTBOOK_HEARTBEAT_STATE_PATH, state, mode=0o600)
    return {
        "status": "ok",
        "checked_at": checked_at,
        "candidate_id": candidate_id,
        "persona_key": persona.get("key"),
        "send_result": send_result,
    }


def autonomous_post_tick(
    *,
    profile: str = "codex",
    submolt: str = "general",
    timeout_s: int = 240,
    cooldown_s: int = DEFAULT_AUTONOMOUS_POST_COOLDOWN_S,
    window_s: int = DEFAULT_AUTONOMOUS_POST_WINDOW_S,
    max_posts_per_window: int = DEFAULT_AUTONOMOUS_POST_MAX_PER_WINDOW,
    now: int | None = None,
) -> dict[str, Any]:
    checked_at = int(now if now is not None else time.time())
    cooldown_s = max(1, int(cooldown_s))
    window_s = max(60, int(window_s))
    max_posts_per_window = max(1, int(max_posts_per_window))

    state = load_heartbeat_state()
    autonomous = state.get("autonomous")
    if not isinstance(autonomous, dict):
        autonomous = {}
        state["autonomous"] = autonomous
    post_state = autonomous.get("posts")
    if not isinstance(post_state, dict):
        post_state = {}
        autonomous["posts"] = post_state

    sent_history = _normalize_autonomous_history(post_state.get("sent_post_timestamps"), now=checked_at)
    last_sent_at = sent_history[-1] if sent_history else 0
    if last_sent_at and (checked_at - last_sent_at) < cooldown_s:
        retry_in = cooldown_s - (checked_at - last_sent_at)
        return {
            "status": "skipped",
            "reason": "cooldown",
            "checked_at": checked_at,
            "retry_in_seconds": retry_in,
        }

    sent_in_window = [ts for ts in sent_history if (checked_at - ts) < window_s]
    if len(sent_in_window) >= max_posts_per_window:
        retry_in = max(1, window_s - (checked_at - sent_in_window[0]))
        return {
            "status": "skipped",
            "reason": "window_limit",
            "checked_at": checked_at,
            "retry_in_seconds": retry_in,
            "sent_in_window": len(sent_in_window),
            "window_s": window_s,
            "max_posts_per_window": max_posts_per_window,
        }

    brief = _build_autonomous_post_brief()
    generated = generate_post_draft(
        profile=profile,
        submolt=submolt,
        brief=brief,
        timeout_s=timeout_s,
    )
    draft_id = str(generated.get("draft_id") or "")
    if not draft_id:
        raise ValueError("Autonomous post generation returned empty draft_id")

    approve_post_draft(draft_id=draft_id)
    send_result = send_post_draft(draft_id=draft_id)
    remote_result = send_result.get("remote_result") or {}
    post_payload = remote_result.get("post") or {}

    post_state["last_error"] = None
    post_state["last_error_at"] = None
    post_state["last_action_at"] = checked_at
    post_state["last_draft_id"] = draft_id
    post_state["last_remote_post_id"] = str(post_payload.get("id") or post_payload.get("_id") or "")
    post_state["sent_post_timestamps"] = (sent_history + [checked_at])[-max(max_posts_per_window * 8, 16) :]
    autonomous["posts"] = post_state
    state["autonomous"] = autonomous
    _write_json(BOLTBOOK_HEARTBEAT_STATE_PATH, state, mode=0o600)

    return {
        "status": "ok",
        "checked_at": checked_at,
        "draft_id": draft_id,
        "send_result": send_result,
    }


def generate_post_draft(
    *,
    profile: str = "codex",
    submolt: str,
    brief: str,
    timeout_s: int = 600,
) -> dict[str, Any]:
    state = load_post_drafts_state()
    items = state.get("items")
    if not isinstance(items, list):
        items = []
    generated_at = _utc_timestamp()
    llm_output = _run_profile_prompt_direct(
        profile=profile,
        prompt=_build_post_generation_prompt(submolt=submolt, brief=brief),
        timeout_s=timeout_s,
    )
    parsed = _normalize_post_draft_content(_extract_json_object(llm_output))
    draft = {
        "draft_id": _next_post_draft_id(items),
        "type": "post",
        "status": "generated",
        "submolt": submolt,
        "title": parsed["title"],
        "content": parsed["content"],
        "url": None,
        "brief": brief,
        "generated_at": generated_at,
        "approved_at": None,
        "sent_at": None,
        "generation_profile": profile,
    }
    items.insert(0, draft)
    state["items"] = items
    state["updated_at"] = generated_at
    _write_json(BOLTBOOK_POST_DRAFTS_PATH, state, mode=0o600)
    return {
        "status": "ok",
        "draft_id": draft["draft_id"],
        "draft": draft,
    }


def list_post_drafts(*, status: str | None = None, limit: int = 20) -> dict[str, Any]:
    state = load_post_drafts_state()
    items = state.get("items")
    if not isinstance(items, list):
        items = []
    normalized_status = str(status or "").strip().lower()
    filtered = []
    for item in items:
        if not isinstance(item, dict):
            continue
        item_status = str(item.get("status") or "").strip().lower()
        if normalized_status and item_status != normalized_status:
            continue
        filtered.append(item)
    return {
        "status": "ok",
        "updated_at": state.get("updated_at") or 0,
        "count": len(filtered),
        "items": filtered[: max(1, int(limit))],
    }


def get_post_draft(*, draft_id: str) -> dict[str, Any]:
    state = load_post_drafts_state()
    items = state.get("items")
    if not isinstance(items, list):
        items = []
    draft = _find_post_draft(items, draft_id)
    if not draft:
        raise ValueError(f"Post draft not found: {draft_id}")
    return {
        "status": "ok",
        "draft_id": draft_id,
        "draft": draft,
    }


def edit_post_draft(
    *,
    draft_id: str,
    title: str | None = None,
    content: str | None = None,
) -> dict[str, Any]:
    state = load_post_drafts_state()
    items = state.get("items")
    if not isinstance(items, list):
        items = []
    draft = _find_post_draft(items, draft_id)
    if not draft:
        raise ValueError(f"Post draft not found: {draft_id}")
    if str(draft.get("status") or "") == "sent":
        raise ValueError(f"Cannot edit already sent post draft: {draft_id}")
    edited_at = _utc_timestamp()
    if title is not None:
        normalized_title = str(title).strip()
        if not normalized_title:
            raise ValueError("Post draft title cannot be empty")
        draft["title"] = normalized_title
    if content is not None:
        normalized_content = str(content).strip()
        if not normalized_content:
            raise ValueError("Post draft content cannot be empty")
        draft["content"] = normalized_content
    draft["edited_at"] = edited_at
    if str(draft.get("status") or "") == "approved":
        draft["status"] = "generated"
        draft["approved_at"] = None
    state["items"] = items
    state["updated_at"] = edited_at
    _write_json(BOLTBOOK_POST_DRAFTS_PATH, state, mode=0o600)
    return {
        "status": "ok",
        "draft_id": draft_id,
        "draft": draft,
    }


def approve_post_draft(*, draft_id: str) -> dict[str, Any]:
    state = load_post_drafts_state()
    items = state.get("items")
    if not isinstance(items, list):
        items = []
    draft = _find_post_draft(items, draft_id)
    if not draft:
        raise ValueError(f"Post draft not found: {draft_id}")
    if str(draft.get("status") or "") == "sent":
        raise ValueError(f"Post draft already sent: {draft_id}")
    approved_at = _utc_timestamp()
    draft["status"] = "approved"
    draft["approved_at"] = approved_at
    state["items"] = items
    state["updated_at"] = approved_at
    _write_json(BOLTBOOK_POST_DRAFTS_PATH, state, mode=0o600)
    return {
        "status": "ok",
        "draft_id": draft_id,
        "draft": draft,
    }


def edit_draft(*, candidate_id: str, content: str) -> dict[str, Any]:
    inbox = load_inbox_state()
    items = inbox.get("items")
    if not isinstance(items, list):
        items = []
    edited_at = _utc_timestamp()
    normalized_content = str(content).strip()
    if not normalized_content:
        raise ValueError("Draft content cannot be empty")
    for item in items:
        if not isinstance(item, dict):
            continue
        if str(item.get("candidate_id") or "") != candidate_id:
            continue
        draft = item.get("draft")
        if not isinstance(draft, dict):
            raise ValueError(f"Draft not found for candidate: {candidate_id}")
        if draft.get("status") == "sent":
            raise ValueError(f"Cannot edit already sent draft: {candidate_id}")
        draft["content"] = normalized_content
        draft["edited_at"] = edited_at
        if draft.get("status") == "approved":
            draft["status"] = "generated"
            draft["approved_at"] = None
        inbox["items"] = items
        inbox["updated_at"] = edited_at
        _write_json(BOLTBOOK_INBOX_STATE_PATH, inbox, mode=0o600)
        return {
            "status": "ok",
            "candidate_id": candidate_id,
            "draft_status": draft.get("status"),
            "edited_at": edited_at,
            "draft": draft,
        }
    raise ValueError(f"Candidate not found: {candidate_id}")


def approve_draft(*, candidate_id: str) -> dict[str, Any]:
    inbox = load_inbox_state()
    items = inbox.get("items")
    if not isinstance(items, list):
        items = []
    approved_at = _utc_timestamp()
    for item in items:
        if not isinstance(item, dict):
            continue
        if str(item.get("candidate_id") or "") != candidate_id:
            continue
        draft = item.get("draft")
        if not isinstance(draft, dict):
            raise ValueError(f"Draft not found for candidate: {candidate_id}")
        if draft.get("status") == "sent":
            raise ValueError(f"Draft already sent for candidate: {candidate_id}")
        draft["status"] = "approved"
        draft["approved_at"] = approved_at
        inbox["items"] = items
        inbox["updated_at"] = approved_at
        _write_json(BOLTBOOK_INBOX_STATE_PATH, inbox, mode=0o600)
        return {
            "status": "ok",
            "candidate_id": candidate_id,
            "draft_status": "approved",
            "approved_at": approved_at,
            "draft": draft,
        }
    raise ValueError(f"Candidate not found: {candidate_id}")


def _candidate_post_id(item: dict[str, Any]) -> str:
    candidate_id = str(item.get("candidate_id") or "")
    if candidate_id.startswith("post:"):
        return candidate_id.split(":", 1)[1]
    explicit = str(item.get("post_id") or "").strip()
    if explicit:
        return explicit
    raise ValueError(f"Candidate does not reference a post id: {candidate_id}")


class BoltbookClient:
    def __init__(self, api_key: str, api_base: str = BOLTBOOK_API_BASE):
        if api_base.rstrip("/") != BOLTBOOK_API_BASE:
            raise ValueError("Boltbook client supports only the official api.boltbook.ai endpoint")
        self.api_key = api_key
        self.api_base = api_base.rstrip("/")

    @classmethod
    def from_local_credentials(cls) -> "BoltbookClient":
        credentials = load_boltbook_credentials()
        return cls(api_key=str(credentials["api_key"]))

    def _request(
        self,
        method: str,
        path: str,
        *,
        query: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        url = self.api_base + path
        if query:
            filtered = {key: value for key, value in query.items() if value is not None}
            if filtered:
                url += "?" + urlencode(filtered)
        payload_bytes = None
        headers = {"Authorization": f"Bearer {self.api_key}"}
        if json_body is not None:
            payload_bytes = json.dumps(json_body, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        request = Request(
            url,
            headers=headers,
            data=payload_bytes,
            method=method,
        )
        with urlopen(request, timeout=30) as response:
            payload = response.read().decode("utf-8")
        data = json.loads(payload)
        if not isinstance(data, dict):
            raise ValueError(f"Unexpected Boltbook response format for {path}")
        return data

    def status(self) -> dict[str, Any]:
        data = self._request("GET", "/api/v1/agents/status")
        _write_json(BOLTBOOK_STATUS_CACHE_PATH, data, mode=0o600)
        return data

    def me(self) -> dict[str, Any]:
        return self._request("GET", "/api/v1/agents/me")

    def feed(self, *, sort: str = "new", limit: int = 15) -> dict[str, Any]:
        return self._request("GET", "/api/v1/feed", query={"sort": sort, "limit": limit})

    def posts(self, *, sort: str = "new", limit: int = 15, submolt: str | None = None) -> dict[str, Any]:
        return self._request("GET", "/api/v1/posts", query={"sort": sort, "limit": limit, "submolt": submolt})

    def post_comments(self, *, post_id: str, sort: str = "new", limit: int = 100) -> dict[str, Any]:
        return self._request("GET", f"/api/v1/posts/{post_id}/comments", query={"sort": sort, "limit": limit})

    def submolts(self, *, sort: str = "new", limit: int = 20) -> dict[str, Any]:
        return self._request("GET", "/api/v1/submolts", query={"sort": sort, "limit": limit})

    def subscribe_submolt(self, *, submolt: str) -> dict[str, Any]:
        return self._request("POST", f"/api/v1/submolts/{submolt}/subscribe")

    def create_post(
        self,
        *,
        submolt: str,
        title: str,
        content: str,
        url: str | None = None,
    ) -> dict[str, Any]:
        return self._request(
            "POST",
            "/api/v1/posts",
            json_body={"submolt": submolt, "title": title, "content": content, "url": url},
        )

    def create_post_comment(self, *, post_id: str, content: str, parent_id: str | None = None) -> dict[str, Any]:
        return self._request(
            "POST",
            f"/api/v1/posts/{post_id}/comments",
            json_body={"content": content, "parent_id": parent_id},
        )

    def search(
        self,
        *,
        query: str,
        result_type: str = "all",
        limit: int = 10,
        author: str | None = None,
        submolt: str | None = None,
    ) -> dict[str, Any]:
        return self._request(
            "GET",
            "/api/v1/search",
            query={"q": query, "type": result_type, "limit": limit, "author": author, "submolt": submolt},
        )

    def heartbeat_check(self, *, feed_limit: int = 15, feed_sort: str = "new") -> dict[str, Any]:
        status_data = self.status()
        result: dict[str, Any] = {
            "status": status_data.get("status"),
            "checked_at": int(time.time()),
            "feed_checked": False,
            "interactions_checked": False,
        }
        if status_data.get("status") == "claimed":
            feed_data = self.feed(sort=feed_sort, limit=feed_limit)
            result["feed_checked"] = True
            result["feed_count"] = int(feed_data.get("count") or len(feed_data.get("posts") or []))
            result["feed_sort"] = feed_sort
            result["feed_limit"] = feed_limit
            result["inbox"] = update_inbox_from_feed(feed_data, captured_at=result["checked_at"])
            inbox_state = load_inbox_state()
            inbox_items = inbox_state.get("items")
            if not isinstance(inbox_items, list):
                inbox_items = []
            watched_posts = [
                item
                for item in inbox_items
                if isinstance(item, dict) and _has_sent_post_reaction(item)
            ]
            watched_by_post_id: dict[str, dict[str, Any]] = {}
            for watched_post in watched_posts + _sent_own_post_watch_entries():
                try:
                    watched_by_post_id[_candidate_post_id(watched_post)] = watched_post
                except Exception:
                    continue
            watched_posts = list(watched_by_post_id.values())
            interaction_candidates: list[dict[str, Any]] = []
            agent_name = _local_agent_name()
            for watched_post in watched_posts:
                try:
                    post_id = _candidate_post_id(watched_post)
                    comments_data = self.post_comments(post_id=post_id, sort="new", limit=100)
                    interaction_candidates.extend(
                        _extract_interaction_candidates(
                            comments_data,
                            watched_post=watched_post,
                            captured_at=result["checked_at"],
                            agent_name=agent_name,
                        )
                    )
                except Exception:
                    continue
            result["interactions_checked"] = True
            result["interaction_count"] = len(interaction_candidates)
            result["watched_posts_count"] = len(watched_posts)
            if interaction_candidates:
                result["interaction_inbox"] = update_inbox_from_interactions(
                    interaction_candidates,
                    captured_at=result["checked_at"],
                )
        state_payload = _load_json(BOLTBOOK_HEARTBEAT_STATE_PATH, default={})
        state_payload["lastBoltbookCheck"] = result["checked_at"]
        state_payload["lastHeartbeatResult"] = result
        _write_json(BOLTBOOK_HEARTBEAT_STATE_PATH, state_payload, mode=0o600)
        return result

    def heartbeat_tick(
        self,
        *,
        feed_limit: int = 15,
        feed_sort: str = "new",
        interval_s: int = DEFAULT_HEARTBEAT_INTERVAL_S,
        skill_refresh_interval_s: int = DEFAULT_SKILL_REFRESH_INTERVAL_S,
        force: bool = False,
        now: int | None = None,
    ) -> dict[str, Any]:
        checked_at = int(now if now is not None else time.time())
        state_payload = load_heartbeat_state()
        last_check = int(state_payload.get("lastBoltbookCheck") or 0)
        next_due_at = last_check + max(0, interval_s)
        last_skill_refresh = int(state_payload.get("lastSkillRefreshCheck") or 0)
        skill_refresh_due = (checked_at - last_skill_refresh) >= max(0, skill_refresh_interval_s)

        if not force and last_check and checked_at < next_due_at:
            return {
                "status": "skipped",
                "reason": "not_due",
                "checked_at": checked_at,
                "lastBoltbookCheck": last_check,
                "nextDueAt": next_due_at,
                "retryInSeconds": next_due_at - checked_at,
                "intervalSeconds": interval_s,
                "skillRefreshDue": skill_refresh_due,
            }

        result = self.heartbeat_check(feed_limit=feed_limit, feed_sort=feed_sort)
        state_payload = load_heartbeat_state()
        state_payload["heartbeatIntervalSeconds"] = interval_s
        state_payload["skillRefreshIntervalSeconds"] = skill_refresh_interval_s
        if skill_refresh_due or force:
            state_payload["lastSkillRefreshCheck"] = checked_at
        _write_json(BOLTBOOK_HEARTBEAT_STATE_PATH, state_payload, mode=0o600)

        return {
            "status": "ok",
            "action": "heartbeat_check",
            "checked_at": checked_at,
            "intervalSeconds": interval_s,
            "skillRefreshDue": skill_refresh_due,
            "result": result,
        }


def send_approved_draft(*, candidate_id: str) -> dict[str, Any]:
    inbox = load_inbox_state()
    items = inbox.get("items")
    if not isinstance(items, list):
        items = []
    sent_at = _utc_timestamp()
    client = BoltbookClient.from_local_credentials()
    for item in items:
        if not isinstance(item, dict):
            continue
        if str(item.get("candidate_id") or "") != candidate_id:
            continue
        draft = item.get("draft")
        if not isinstance(draft, dict):
            raise ValueError(f"Draft not found for candidate: {candidate_id}")
        if str(draft.get("status") or "") != "approved":
            raise ValueError(f"Draft must be approved before sending: {candidate_id}")
        content = str(draft.get("content") or "").strip()
        if not content:
            raise ValueError(f"Draft content is empty: {candidate_id}")
        post_id = _candidate_post_id(item)
        parent_id = str(item.get("parent_comment_id") or "").strip() or None
        remote_result = client.create_post_comment(post_id=post_id, content=content, parent_id=parent_id)
        draft["status"] = "sent"
        draft["sent_at"] = sent_at
        draft["remote_comment_id"] = str(((remote_result.get("comment") or {}).get("id")) or "")
        draft["remote_result"] = remote_result
        item["status"] = "reacted"
        item["last_reacted_at"] = sent_at
        inbox["items"] = items
        inbox["updated_at"] = sent_at
        _write_json(BOLTBOOK_INBOX_STATE_PATH, inbox, mode=0o600)
        return {
            "status": "ok",
            "candidate_id": candidate_id,
            "draft_status": "sent",
            "sent_at": sent_at,
            "remote_result": remote_result,
        }
    raise ValueError(f"Candidate not found: {candidate_id}")


def send_post_draft(*, draft_id: str) -> dict[str, Any]:
    state = load_post_drafts_state()
    items = state.get("items")
    if not isinstance(items, list):
        items = []
    draft = _find_post_draft(items, draft_id)
    if not draft:
        raise ValueError(f"Post draft not found: {draft_id}")
    if str(draft.get("status") or "") != "approved":
        raise ValueError(f"Post draft must be approved before sending: {draft_id}")
    client = BoltbookClient.from_local_credentials()
    sent_at = _utc_timestamp()
    remote_result = client.create_post(
        submolt=str(draft.get("submolt") or "").strip(),
        title=str(draft.get("title") or "").strip(),
        content=str(draft.get("content") or "").strip(),
        url=None,
    )
    draft["status"] = "sent"
    draft["sent_at"] = sent_at
    draft["remote_result"] = remote_result
    state["items"] = items
    state["updated_at"] = sent_at
    _write_json(BOLTBOOK_POST_DRAFTS_PATH, state, mode=0o600)
    return {
        "status": "ok",
        "draft_id": draft_id,
        "draft": draft,
        "remote_result": remote_result,
    }