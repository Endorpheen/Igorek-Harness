import json
import time
from pathlib import Path
from typing import Any


def get_request_id(request: Any) -> str:
    if request is None:
        return "unknown"
    return getattr(request.state, "request_id", "unknown")


def truncate_for_log(value: Any, max_text: int = 400) -> Any:
    if isinstance(value, str):
        return value if len(value) <= max_text else value[:max_text] + "...[truncated]"
    if isinstance(value, list):
        return [truncate_for_log(item, max_text=max_text) for item in value]
    if isinstance(value, dict):
        return {key: truncate_for_log(item, max_text=max_text) for key, item in value.items()}
    return value


def log_jsonl(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(payload, ensure_ascii=False) + "\n")


def log_anthropic_request(log_path: Path, payload: dict[str, Any]) -> None:
    log_jsonl(log_path, payload)


def trace_event(
    trace_path: Path,
    debug_enabled: bool,
    request: Any,
    kind: str,
    **payload: Any,
) -> None:
    if not debug_enabled:
        return
    event = {
        "ts": time.time(),
        "request_id": get_request_id(request) if request is not None else None,
        "kind": kind,
        "payload": truncate_for_log(payload, max_text=1200),
    }
    log_jsonl(trace_path, event)

