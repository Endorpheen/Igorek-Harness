"""Active voice call registry and minitone frequency limiter.

The ephemeral OpenAI secret is stored only in the memory of the bridge process and
never sent to the browser. The browser receives an opaque ``call_id`` and
uses it for server-side SDP exchange.
"""

from __future__ import annotations

import threading
import time
import uuid
from dataclasses import dataclass, field


@dataclass
class VoiceCall:
    call_id: str
    session_id: str
    ephemeral: str
    expires_at: float
    model: str
    voice: str
    provider_call_id: str = ""
    created_at: float = field(default_factory=time.monotonic)


class VoiceRateLimiter:
    """Simple window: no more than `min_interval_s` and no more than `max_per_window` calls within `window_s`."""

    def __init__(self, *, min_interval_s: float = 3.0, window_s: float = 3600.0, max_per_window: int = 60) -> None:
        self.min_interval_s = float(min_interval_s)
        self.window_s = float(window_s)
        self.max_per_window = int(max_per_window)
        self._last: dict[str, float] = {}
        self._events: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    def allow(self, key: str) -> bool:
        now = time.monotonic()
        with self._lock:
            last = self._last.get(key)
            if last is not None and now - last < self.min_interval_s:
                return False
            events = [stamp for stamp in self._events.get(key, []) if now - stamp < self.window_s]
            if len(events) >= self.max_per_window:
                self._events[key] = events
                return False
            events.append(now)
            self._events[key] = events
            self._last[key] = now
            return True


class VoiceCallRegistry:
    """Limited set of active calls; expired ones are lazily cleaned up."""

    def __init__(self, *, max_calls: int = 8) -> None:
        self.max_calls = max(1, int(max_calls))
        self._calls: dict[str, VoiceCall] = {}
        self._lock = threading.Lock()

    def create(
        self,
        *,
        session_id: str,
        ephemeral: str,
        expires_at: float,
        model: str,
        voice: str,
    ) -> VoiceCall:
        with self._lock:
            self._prune_locked()
            if len(self._calls) >= self.max_calls:
                oldest = min(self._calls.values(), key=lambda call: call.created_at)
                self._calls.pop(oldest.call_id, None)
            call = VoiceCall(
                call_id=uuid.uuid4().hex,
                session_id=session_id,
                ephemeral=ephemeral,
                expires_at=float(expires_at or 0.0),
                model=model,
                voice=voice,
            )
            self._calls[call.call_id] = call
            return call

    def get(self, call_id: str) -> VoiceCall | None:
        with self._lock:
            self._prune_locked()
            return self._calls.get(str(call_id or ""))

    def pop(self, call_id: str) -> VoiceCall | None:
        with self._lock:
            return self._calls.pop(str(call_id or ""), None)

    def count(self) -> int:
        with self._lock:
            self._prune_locked()
            return len(self._calls)

    def _prune_locked(self) -> None:
        now = time.time()
        expired = [
            call_id
            for call_id, call in self._calls.items()
            if call.expires_at and call.expires_at <= now
        ]
        for call_id in expired:
            self._calls.pop(call_id, None)
