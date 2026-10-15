"""Clipboard notifications and request serialization for mouse copy.

Copying a selection runs backends that may block on ``subprocess`` calls.
The notification text must be honest: a local owner (``arboard``,
``wl-copy``, ``xclip``, ``xsel``, WSL PowerShell) can confirm success,
while OSC 52 only *sends* an escape sequence to the terminal and cannot
prove the terminal accepted it.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass
from typing import Callable, Optional, Tuple

from .clipboard_copy import ClipboardLease, copy_to_clipboard_detailed

_LOCAL_BACKENDS = {"arboard", "wl-copy", "xclip", "xsel", "wsl"}
_TERMINAL_BACKENDS = {"osc52", "tmux"}


def _line_count(text: str) -> int:
    return text.count("\n") + 1


def format_copy_result(
    text: str,
    backend: Optional[str],
    error: Optional[str],
) -> Tuple[bool, str]:
    """Return ``(success, message)`` for a completed copy attempt."""
    count = _line_count(text)
    if error:
        # Never echo the copied text in an error message.
        return False, f"Не удалось скопировать: {error}"
    if backend in _LOCAL_BACKENDS:
        return True, f"Скопировано: {count} строк"
    if backend in _TERMINAL_BACKENDS:
        return (
            True,
            f"Отправлено в буфер терминала: {count} строк",
        )
    return True, f"Скопировано: {count} строк"


@dataclass
class CopyResult:
    success: bool
    message: str
    backend: Optional[str]
    error: Optional[str]


class ClipboardService:
    """Serialize clipboard requests so an older slow request cannot win.

    Each request takes a monotonically increasing ticket. The worker
    thread records the ticket it started under ``self._latest_ticket`` and
    only reports/keeps a lease when it is still the newest request.
    """

    def __init__(self, copy_fn: Optional[Callable[[str], Tuple[Optional[ClipboardLease], Optional[str], Optional[str]]]] = None) -> None:
        self._copy_fn = copy_fn or copy_to_clipboard_detailed
        self._lock = threading.Lock()
        self._ticket = 0
        self._latest_ticket = 0
        self.lease: Optional[ClipboardLease] = None
        self.last_backend: Optional[str] = None
        self.last_error: Optional[str] = None

    def next_ticket(self) -> int:
        with self._lock:
            self._ticket += 1
            return self._ticket

    def copy(self, text: str) -> CopyResult:
        ticket = self.next_ticket()
        try:
            lease, error, backend = self._copy_fn(text)
        except Exception as exc:  # pragma: no cover - defensive
            lease, error, backend = None, f"{type(exc).__name__}: {exc}", None
        with self._lock:
            stale = ticket < self._latest_ticket
            if not stale:
                self._latest_ticket = ticket
                if lease is not None:
                    self.lease = lease
                self.last_backend = backend
                self.last_error = error
        if stale:
            return CopyResult(False, "", backend, error)
        success, message = format_copy_result(text, backend, error)
        return CopyResult(success, message, backend, error)


__all__ = [
    "ClipboardService",
    "CopyResult",
    "format_copy_result",
]
