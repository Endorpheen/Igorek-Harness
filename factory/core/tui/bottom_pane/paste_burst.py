"""Paste burst detection — port of Codex bottom_pane/paste_burst.rs."""
from __future__ import annotations
from typing import Optional
import time

class PasteBurst:
    def __init__(self, interval: float = 0.01):
        self._interval = interval
        self._last_char_time = 0.0
        self._buffer = ""

    def feed(self, ch: str) -> Optional[str]:
        now = time.monotonic()
        if now - self._last_char_time < self._interval:
            self._buffer += ch
            self._last_char_time = now
            return None
        flushed = self._buffer
        self._buffer = ch
        self._last_char_time = now
        return flushed
