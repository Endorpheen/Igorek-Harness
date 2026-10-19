#!/usr/bin/env python3
import json
import os
import sys
import time
from typing import Any, Dict

_LEVELS = {
    "debug": 10,
    "info": 20,
    "warn": 30,
    "error": 40,
}


class FactoryLogger:
    def __init__(self, component: str):
        self.component = component
        self.log_format = os.environ.get("FACTORY_LOG_FORMAT", "pretty").strip().lower()
        self.level_name = os.environ.get("FACTORY_LOG_LEVEL", "info").strip().lower()
        if self.level_name not in _LEVELS:
            self.level_name = "info"
        self.level_value = _LEVELS[self.level_name]

    def debug(self, event: str, message: str, **kwargs: Any):
        self._log("debug", event, message, **kwargs)

    def info(self, event: str, message: str, **kwargs: Any):
        self._log("info", event, message, **kwargs)

    def warn(self, event: str, message: str, **kwargs: Any):
        self._log("warn", event, message, **kwargs)

    def error(self, event: str, message: str, **kwargs: Any):
        self._log("error", event, message, **kwargs)

    def _log(self, level: str, event: str, message: str, **kwargs: Any):
        from .diagnostics import safe_log_value
        if _LEVELS[level] < self.level_value:
            return

        ts = int(time.time())
        payload: Dict[str, Any] = {
            "ts": ts,
            "level": level,
            "component": self.component,
            "event": event,
            "message": message,
        }
        payload.update({k: v for k, v in kwargs.items() if v is not None})
        payload = safe_log_value(payload)

        if self.log_format == "json":
            line = json.dumps(payload, ensure_ascii=False)
        else:
            extra = []
            if "request_id" in payload:
                extra.append(f"request_id={payload['request_id']}")
            if "task_id" in payload:
                extra.append(f"task_id={payload['task_id']}")
            extra_text = f" ({', '.join(extra)})" if extra else ""
            line = f"[{ts}] {level.upper()} [{self.component}] {event}: {payload['message']}{extra_text}"

        try:
            print(line, file=sys.stdout, flush=True)
        except OSError:
            pass


def get_logger(component: str) -> FactoryLogger:
    return FactoryLogger(component)
