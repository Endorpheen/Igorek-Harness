"""Optional JSONL trace for Rich CLI terminal rendering decisions."""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any


_TRUE_VALUES = {"1", "true", "yes", "on"}


def _resolve_log_path() -> Path | None:
    raw = str(os.environ.get("IGOREK_TERMINAL_RENDER_LOG") or "").strip()
    if not raw:
        return None
    if raw.lower() in _TRUE_VALUES:
        base = Path(os.environ.get("IGOREK_AGENT_DATA_DIR") or Path.home() / ".igorek-agent-factory")
        return base / "logs" / f"terminal_render_{os.getpid()}.jsonl"
    return Path(raw).expanduser()


def terminal_render_log(event: str, **fields: Any) -> None:
    path = _resolve_log_path()
    if path is None:
        return
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "ts": time.time(),
            "pid": os.getpid(),
            "event": str(event),
            **fields,
        }
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(payload, ensure_ascii=False, default=str) + "\n")
    except Exception:
        return
