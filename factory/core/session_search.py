from __future__ import annotations

import json
import re
import time
from pathlib import Path

from .runtime import get_chat_sessions_dir
from .session_recall_store import SessionRecallStore

_MAX_PREVIEW_CHARS = 500
_MAX_EXCERPT_CHARS = 1200
_RECENT_LIMIT_MAX = 5
_VALID_MODES = {"lexical", "semantic", "hybrid"}


def _sessions_dir() -> Path:
    path = Path(get_chat_sessions_dir())
    path.mkdir(parents=True, exist_ok=True)
    return path


def _normalize_text(value: str) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def _load_entries(path: Path) -> list[dict]:
    if not path.exists():
        return []
    entries: list[dict] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict) and item.get("role") in {"user", "assistant"}:
            entries.append(item)
    return entries


def _format_ts(ts: int) -> str:
    if not ts:
        return ""
    try:
        return time.strftime("%Y-%m-%d %H:%M", time.localtime(ts))
    except Exception:
        return ""


def _build_preview(entries: list[dict]) -> str:
    for entry in reversed(entries):
        text = _normalize_text(entry.get("text", ""))
        if text:
            if len(text) > _MAX_PREVIEW_CHARS:
                return text[:_MAX_PREVIEW_CHARS].rstrip() + "…"
            return text
    return ""


def _split_query_terms(query: str) -> list[str]:
    normalized = str(query or "").strip()
    if not normalized:
        return []
    quoted = [match.group(1).strip() for match in re.finditer(r'"([^"]+)"', normalized) if match.group(1).strip()]
    stripped = re.sub(r'"[^"]+"', " ", normalized)
    if quoted:
        terms = quoted
    elif re.search(r"\s+OR\s+", normalized, flags=re.IGNORECASE):
        terms = [part.strip() for part in re.split(r"\s+OR\s+", stripped, flags=re.IGNORECASE) if part.strip()]
    else:
        terms = [normalized]
    deduped: list[str] = []
    seen: set[str] = set()
    for term in terms:
        lowered = term.lower()
        if lowered in seen:
            continue
        seen.add(lowered)
        deduped.append(term)
    return deduped


def _searchable_text(entries: list[dict], role_filter: set[str] | None) -> str:
    lines = []
    for entry in entries:
        role = str(entry.get("role") or "")
        if role_filter and role not in role_filter:
            continue
        text = _normalize_text(entry.get("text", ""))
        if text:
            lines.append(f"{role}: {text}")
    return "\n".join(lines)


def _build_excerpt(text: str, terms: list[str]) -> str:
    lowered = text.lower()
    best_index = -1
    best_term = ""
    for term in terms:
        index = lowered.find(term.lower())
        if index >= 0 and (best_index < 0 or index < best_index):
            best_index = index
            best_term = term
    if best_index < 0:
        excerpt = text[:_MAX_EXCERPT_CHARS]
        return excerpt.rstrip() + ("…" if len(text) > _MAX_EXCERPT_CHARS else "")
    start = max(0, best_index - 250)
    end = min(len(text), best_index + max(len(best_term), 1) + 700)
    excerpt = text[start:end].strip()
    if start > 0:
        excerpt = "… " + excerpt
    if end < len(text):
        excerpt = excerpt + " …"
    if len(excerpt) > _MAX_EXCERPT_CHARS:
        excerpt = excerpt[:_MAX_EXCERPT_CHARS].rstrip() + "…"
    return excerpt


def _list_recent_sessions(*, current_session: str | None, limit: int) -> dict:
    current_name = f"{current_session}.jsonl" if current_session else ""
    files = sorted(
        (path for path in _sessions_dir().glob("*.jsonl") if path.name != current_name),
        key=lambda item: item.stat().st_mtime,
        reverse=True,
    )
    results = []
    for path in files:
        entries = _load_entries(path)
        if not entries:
            continue
        results.append(
            {
                "session": path.stem,
                "last_active": _format_ts(int(entries[-1].get("ts") or 0)),
                "message_count": len(entries),
                "preview": _build_preview(entries),
            }
        )
        if len(results) >= limit:
            break
    return {"success": True, "mode": "recent", "count": len(results), "results": results}


def _search_sessions(query: str, *, current_session: str | None, limit: int, role_filter: set[str] | None) -> dict:
    current_name = f"{current_session}.jsonl" if current_session else ""
    terms = _split_query_terms(query)
    files = sorted(
        (path for path in _sessions_dir().glob("*.jsonl") if path.name != current_name),
        key=lambda item: item.stat().st_mtime,
        reverse=True,
    )
    results: list[dict] = []
    for file_index, path in enumerate(files):
        entries = _load_entries(path)
        if not entries:
            continue
        text = _searchable_text(entries, role_filter)
        if not text:
            continue
        lowered = text.lower()
        matched_terms = [term for term in terms if term.lower() in lowered]
        if not matched_terms:
            continue
        score = len(matched_terms) * 100 + max(1, 20 - min(file_index, 19))
        results.append(
            {
                "session": path.stem,
                "last_active": _format_ts(int(entries[-1].get("ts") or 0)),
                "matched_terms": matched_terms,
                "score": score,
                "excerpt": _build_excerpt(text, matched_terms),
            }
        )
    results.sort(key=lambda item: item["score"], reverse=True)
    trimmed = results[:limit]
    for item in trimmed:
        item.pop("score", None)
    return {
        "success": True,
        "mode": "search",
        "query": query,
        "count": len(trimmed),
        "results": trimmed,
    }


def _apply_provenance_floor(ranked: list[dict], limit: int) -> list[dict]:
    """Guarantee one local and one archive semantic candidate survive the trim.

    Only invokes when archive-provenance semantic candidates are present. Entries
    already carrying a provenance that another entry still satisfies are the
    "floor" and are never dropped; the lowest-ranked non-floor entries make room
    for the missing provenance. Rank order of everything else is preserved.
    """
    required = [
        provenance
        for provenance in ("local", "archive")
        if any(str(item.get("_provenance")) == provenance for item in ranked)
    ]
    top = list(ranked[:limit])
    missing = [p for p in required if not any(str(i.get("_provenance")) == p for i in top)]
    if not missing:
        return top
    additions = []
    for provenance in missing:
        candidate = next(
            (item for item in ranked if str(item.get("_provenance")) == provenance and item not in top),
            None,
        )
        if candidate is None:
            return top
        additions.append(candidate)
    protected = set()
    for provenance in required:
        for item in top:
            if str(item.get("_provenance")) == provenance:
                protected.add(id(item))
                break
    kept: list[dict] = []
    remaining = len(additions)
    for item in reversed(top):
        if remaining > 0 and id(item) not in protected:
            remaining -= 1
            continue
        kept.append(item)
    if remaining > 0:
        return top
    kept.extend(additions)
    kept.sort(key=lambda item: float(item.get("_rank") or 0.0), reverse=True)
    return kept


def _merge_search_results(
    transcript_results: list[dict],
    recall_lexical_results: list[dict],
    semantic_results: list[dict],
    *,
    limit: int,
) -> list[dict]:
    by_key: dict[tuple[str, str], dict] = {}
    for index, item in enumerate(transcript_results):
        session_id = str(item.get("session") or "")
        excerpt = _normalize_text(item.get("excerpt", ""))
        key = (session_id, excerpt[:240] or f"transcript:{index}")
        payload = dict(item)
        payload["_rank"] = float(max(0.0, 1.0 - index * 0.1))
        by_key[key] = payload
    for index, item in enumerate(recall_lexical_results):
        entry_id = str(item.get("entry_id") or "")
        session_id = str(item.get("session") or "")
        key = (session_id, entry_id or _normalize_text(item.get("excerpt", ""))[:240] or f"recall:{index}")
        payload = dict(item)
        payload["_rank"] = float(payload.get("_rank") or 0.0) + float(max(0.0, 0.9 - index * 0.1))
        existing = by_key.get(key)
        if existing:
            existing["_rank"] = float(existing.get("_rank") or 0.0) + payload["_rank"]
            existing.setdefault("matched_terms", payload.get("matched_terms") or [])
            continue
        by_key[key] = payload
    for item in semantic_results:
        entry_id = str(item.get("entry_id") or "")
        session_id = str(item.get("session") or "")
        key = (session_id, entry_id or _normalize_text(item.get("excerpt", ""))[:240])
        score = float(item.get("semantic_score") or 0.0)
        payload = dict(item)
        payload["_rank"] = score
        existing = by_key.get(key)
        if existing:
            existing["_rank"] = float(existing.get("_rank") or 0.0) + score
            existing["semantic_score"] = max(float(existing.get("semantic_score") or 0.0), score)
            continue
        by_key[key] = payload
    merged = sorted(by_key.values(), key=lambda item: float(item.get("_rank") or 0.0), reverse=True)
    archive_present = any(
        str(item.get("_provenance")) == "archive" for item in semantic_results if isinstance(item, dict)
    )
    if archive_present and limit >= 2:
        merged = _apply_provenance_floor(merged, limit)
    else:
        merged = merged[:limit]
    for item in merged:
        item.pop("_rank", None)
        item.pop("_provenance", None)
    return merged


def _hybrid_search_sessions(
    query: str,
    *,
    current_session: str | None,
    limit: int,
    role_filter: set[str] | None,
    mode: str,
) -> dict:
    lexical_payload = _search_sessions(
        query,
        current_session=current_session,
        limit=limit,
        role_filter=role_filter,
    )
    transcript_results = [
        {
            **item,
            "source": "transcript",
            "entry_kind": "session_transcript",
        }
        for item in lexical_payload.get("results", [])
        if isinstance(item, dict)
    ]
    recall_store = SessionRecallStore()
    recall_lexical_results = recall_store.search_lexical(query, current_session=current_session, limit=limit)
    semantic_available = recall_store.embedder.available
    semantic_results = (
        recall_store.search_semantic(query, current_session=current_session, limit=limit)
        if mode in {"semantic", "hybrid"} and semantic_available
        else []
    )
    effective_mode = "lexical"
    if mode == "semantic":
        effective_mode = "semantic" if semantic_results else "lexical"
    elif mode == "hybrid":
        effective_mode = "hybrid" if semantic_results else "lexical"
    merged = _merge_search_results(
        transcript_results,
        recall_lexical_results,
        semantic_results,
        limit=limit,
    )
    return {
        "success": True,
        "mode": "search",
        "requested_mode": mode,
        "effective_mode": effective_mode,
        "semantic_available": semantic_available,
        "query": query,
        "count": len(merged),
        "results": merged,
    }


def session_search(
    *,
    query: str = "",
    current_session: str | None = None,
    limit: int = 3,
    role_filter: str = "user,assistant",
    mode: str = "lexical",
) -> str:
    normalized_limit = max(1, min(int(limit or 3), _RECENT_LIMIT_MAX))
    roles = {
        part.strip()
        for part in str(role_filter or "").split(",")
        if part.strip() in {"user", "assistant"}
    } or None
    selected_mode = str(mode or "lexical").strip().lower()
    if selected_mode not in _VALID_MODES:
        selected_mode = "lexical"
    if not str(query or "").strip():
        payload = _list_recent_sessions(current_session=current_session, limit=normalized_limit)
    else:
        payload = _hybrid_search_sessions(
            str(query or "").strip(),
            current_session=current_session,
            limit=normalized_limit,
            role_filter=roles,
            mode=selected_mode,
        )
    return json.dumps(payload, ensure_ascii=False)
