import json
import re
import shlex
from typing import Any


def parse_json_payload(text: str) -> Any | None:
    raw = (text or "").strip()
    if not raw:
        return None
    try:
        return json.loads(raw)
    except Exception:
        decoder = json.JSONDecoder()
        for match in re.finditer(r"[\{\[]", raw):
            start = match.start()
            try:
                payload, _end = decoder.raw_decode(raw[start:])
                return payload
            except Exception:
                continue
        return None


def json_payload_looks_like_error(payload: Any) -> bool:
    if not isinstance(payload, dict):
        return False
    status = payload.get("status")
    if isinstance(status, int) and status >= 400:
        return True
    code = payload.get("code")
    if isinstance(code, int) and code >= 400:
        return True
    if payload.get("success") is False:
        return True
    if "errors" in payload and isinstance(payload.get("errors"), list) and bool(payload.get("errors")):
        return True
    if "error" in payload and payload.get("error") not in (None, False, "", [], {}):
        return True
    if "detail" in payload and not any(key in payload for key in ("results", "data", "answer", "content")):
        return True
    return False


def json_payload_has_body_validation_error(payload: Any) -> bool:
    if not isinstance(payload, dict):
        return False
    detail = payload.get("detail")
    if not isinstance(detail, list) or not detail:
        return False
    for item in detail:
        if not isinstance(item, dict):
            continue
        loc = item.get("loc")
        if isinstance(loc, list) and "body" in loc:
            return True
    return False


def is_tavily_search_command(command: str) -> bool:
    return "api.tavily.com/search" in command


def extract_successful_tavily_payload(command: str, result_text: str, strip_system_reminder_fn) -> dict[str, Any] | None:
    if not is_tavily_search_command(command):
        return None
    payload = parse_json_payload(strip_system_reminder_fn(result_text or "").strip())
    if not isinstance(payload, dict):
        return None
    results = payload.get("results")
    if not isinstance(results, list) or not results:
        return None
    if json_payload_looks_like_error(payload):
        return None
    return payload


def extract_urls_from_text(text: str) -> list[str]:
    seen: set[str] = set()
    urls: list[str] = []
    for raw in re.findall(r"https?://[^\s'\"`<>]+", text or ""):
        normalized = raw.rstrip(".,);]}>")
        if normalized in seen:
            continue
        seen.add(normalized)
        urls.append(normalized)
    return urls


def extract_tavily_result_urls(payload: dict[str, Any], limit: int = 5) -> list[str]:
    seen: set[str] = set()
    urls: list[str] = []
    for item in payload.get("results", []):
        if not isinstance(item, dict):
            continue
        url = str(item.get("url", "")).strip()
        if not url or url in seen:
            continue
        seen.add(url)
        urls.append(url)
        if len(urls) >= limit:
            break
    return urls


def extract_tavily_bearer_token(text: str) -> str | None:
    match = re.search(r"Authorization:\s*Bearer\s+([A-Za-z0-9._-]+)", text or "")
    if match:
        return match.group(1).strip()
    return None


def extract_tavily_query(text: str) -> str:
    cleaned = re.sub(r"Authorization:\s*Bearer\s+[A-Za-z0-9._-]+", " ", text or "", flags=re.IGNORECASE)
    cleaned = re.sub(r"https?://api\.tavily\.com/search", " ", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\bTAVILY_API_KEY\b", " ", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    if len(cleaned) >= 2 and cleaned[0] == cleaned[-1] and cleaned[0] in "`\"'":
        cleaned = cleaned[1:-1].strip()
    return cleaned


def build_tavily_search_command(user_text: str) -> str | None:
    bearer = extract_tavily_bearer_token(user_text)
    if not bearer:
        return None
    payload = {
        "query": extract_tavily_query(user_text),
        "max_results": 5,
        "include_images": False,
        "include_answer": False,
    }
    serialized = json.dumps(payload, ensure_ascii=False)
    return (
        "curl -s -X POST "
        f'-H "Authorization: Bearer {bearer}" '
        '-H "Content-Type: application/json" '
        '"https://api.tavily.com/search" '
        f"-d {shlex.quote(serialized)}"
    )


def is_url_fetch_bash_command(command: str, allowed_urls: list[str] | None = None) -> bool:
    if not isinstance(command, str) or "curl" not in command or is_tavily_search_command(command):
        return False
    urls = extract_urls_from_text(command)
    if not urls:
        return False
    if allowed_urls is None:
        return True
    allowed = set(allowed_urls)
    return all(url in allowed for url in urls)


def summarize_fetched_web_source(url: str, result_text: str, strip_system_reminder_fn) -> str:
    cleaned = strip_system_reminder_fn(result_text or "")
    preview_marker = "Preview (first 2KB):"
    if preview_marker in cleaned:
        cleaned = cleaned.split(preview_marker, 1)[1].strip()
    cleaned = re.sub(r"(?is)<script.*?>.*?</script>", " ", cleaned)
    cleaned = re.sub(r"(?is)<style.*?>.*?</style>", " ", cleaned)
    cleaned = re.sub(r"(?im)^Output too large.*$", " ", cleaned)
    cleaned = re.sub(r"(?im)^Full output saved to:.*$", " ", cleaned)
    cleaned = re.sub(r"(?im)^Preview \(first .*?\):$", " ", cleaned)
    cleaned = re.sub(r"/tmp/[^\s|]+", " ", cleaned)
    title_match = re.search(r"(?is)<title[^>]*>(.*?)</title>", cleaned)
    title = re.sub(r"\s+", " ", title_match.group(1)).strip() if title_match else ""
    visible = re.sub(r"(?is)<[^>]+>", " ", cleaned)
    visible = re.sub(r"\s+", " ", visible).strip()
    snippet = visible[:900].strip()
    parts = [url]
    if title:
        parts.append(f"title={title}")
    if snippet:
        parts.append(f"snippet={snippet}")
    return " | ".join(parts)


def build_tavily_result_summaries(payload: dict[str, Any], limit: int = 5) -> list[str]:
    summaries: list[str] = []
    for item in payload.get("results", [])[:limit]:
        if not isinstance(item, dict):
            continue
        url = str(item.get("url", "")).strip()
        title = re.sub(r"\s+", " ", str(item.get("title", ""))).strip()
        published = re.sub(r"\s+", " ", str(item.get("published_date", ""))).strip()
        content = re.sub(r"\s+", " ", str(item.get("content", ""))).strip()[:900].strip()
        parts = [url] if url else []
        if title:
            parts.append(f"title={title}")
        if published:
            parts.append(f"published={published}")
        if content:
            parts.append(f"snippet={content}")
        if parts:
            summaries.append(" | ".join(parts))
    return summaries


def extract_curl_json_payload(command: str) -> dict[str, Any] | None:
    try:
        arguments = shlex.split(command)
    except ValueError:
        return None
    bodies = [arguments[i + 1] for i, argument in enumerate(arguments[:-1]) if argument == "-d"]
    if not bodies:
        return None
    try:
        parsed = json.loads(bodies[0])
    except (ValueError, TypeError):
        parsed = None
    if isinstance(parsed, dict):
        return parsed

    payload: dict[str, Any] = {}
    for pair in bodies:
        if "=" not in pair:
            continue
        key, value = pair.split("=", 1)
        normalized_key = key.strip()
        normalized_value = value.strip()
        if not normalized_key:
            continue
        if re.fullmatch(r"-?\d+", normalized_value):
            payload[normalized_key] = int(normalized_value)
        else:
            payload[normalized_key] = normalized_value
    return payload or None


def extract_authorization_bearer(command: str) -> str | None:
    match = re.search(r'''Authorization:\s*Bearer\s+([^"\s]+)''', command)
    if not match:
        return None
    token = match.group(1).strip()
    return token or None


def tavily_retry_bash_command_for_error(command: str, error_text: str, strip_system_reminder_fn) -> str | None:
    if "https://api.tavily.com/search" not in command:
        return None

    payload_error = parse_json_payload(strip_system_reminder_fn(error_text or "").strip())
    if not json_payload_has_body_validation_error(payload_error):
        return None

    payload = extract_curl_json_payload(command)
    if not isinstance(payload, dict):
        return None

    if "num_results" in payload and "max_results" not in payload:
        payload["max_results"] = payload.pop("num_results")
    if "number" in payload and "max_results" not in payload:
        payload["max_results"] = payload.pop("number")

    bearer = extract_authorization_bearer(command)
    if not bearer:
        return None

    serialized_payload = json.dumps(payload, ensure_ascii=False)
    return (
        "curl -s -X POST "
        f'-H "Authorization: Bearer {bearer}" '
        '-H "Content-Type: application/json" '
        '"https://api.tavily.com/search" '
        f"-d {shlex.quote(serialized_payload)}"
    )
