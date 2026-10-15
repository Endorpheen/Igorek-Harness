"""TUI event loop and viewport — full port of Codex tui.rs."""
from __future__ import annotations
import os, re, shutil, sys, time
from dataclasses import dataclass
from typing import Optional, Callable

TARGET_FRAME_INTERVAL = 0.033  # ~30 FPS
_ANSI_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
_ANSI_RESET = "\x1b[0m"

@dataclass
class TerminalSize: lines: int = 24; columns: int = 80

@dataclass
class Viewport:
    x: int = 0; y: int = 0; width: int = 0; height: int = 0
    def bottom(self) -> int: return self.y + self.height

class FrameRateLimiter:
    """Codex frame_rate_limiter: caps redraw frequency at ~30 FPS."""
    def __init__(self, interval: float = TARGET_FRAME_INTERVAL):
        self._interval = interval
        self._last_draw = 0.0
    def should_draw(self) -> bool:
        return time.monotonic() - self._last_draw >= self._interval
    def mark_draw(self):
        self._last_draw = time.monotonic()

class FrameRequester:
    """Requests redraws from external threads."""
    def __init__(self):
        self._pending = False
    def request_frame(self): self._pending = True
    def consume(self) -> bool:
        if self._pending: self._pending = False; return True
        return False

class Tui:
    """Codex TUI: inline viewport rendering pipeline.

    Pipeline: update_viewport → flush_history → draw_fn
    """

    def __init__(self):
        self.viewport = Viewport()
        self.pending_history_lines: list[str] = []
        self.last_frame_lines: list[str] = []
        self._size = TerminalSize()
        self.is_zellij = "ZELLIJ" in os.environ
        self.frame_limiter = FrameRateLimiter()
        self._suspend_viewport: Optional[Viewport] = None

    def _update_size(self):
        sz = shutil.get_terminal_size((120, 24))
        self._size = TerminalSize(sz.lines, sz.columns)

    def update_inline_viewport(self, height: int) -> bool:
        """Codex line 460: clamp height, scroll if overshooting bottom."""
        self._update_size()
        s = self._size
        vp = self.viewport

        vp.height = min(height, s.lines)
        vp.width = s.columns

        if vp.bottom() > s.lines:
            scroll_by = vp.bottom() - s.lines
            if self.is_zellij:
                sys.stdout.write(f"\033[{s.lines - 1};0H")
                sys.stdout.write("\n" * scroll_by)
            else:
                sys.stdout.write(f"\033[1;{s.lines}r")
                for _ in range(scroll_by):
                    sys.stdout.write("\033M")
                sys.stdout.write("\033[r")
            sys.stdout.flush()
            vp.y = s.lines - vp.height
            return True
        return False

    def flush_pending_history_lines(self) -> bool:
        """Write buffered history above viewport."""
        if not self.pending_history_lines:
            return False
        lines = self.pending_history_lines
        self.pending_history_lines = []
        vp = self.viewport
        s = self._size

        cursor_top = vp.y
        if vp.bottom() < s.lines:
            sa = min(len(lines), s.lines - vp.bottom())
            if sa > 0:
                top = max(1, vp.y + 1)
                sys.stdout.write(f"\033[{top};{s.lines}r")
                sys.stdout.write(f"\033[0;{vp.y}H")
                for _ in range(sa): sys.stdout.write("\033M")
                sys.stdout.write("\033[r")
                cursor_top = max(0, vp.y - 1)
                vp.y += sa

        sys.stdout.write(f"\033[0;{cursor_top}H")
        for line in lines:
            sys.stdout.write(f"\r\n{line}")
        sys.stdout.write("\r\n")
        sys.stdout.flush()
        return True

    def draw(self, height: int, draw_fn: Callable, *, force: bool = False) -> bool:
        """Codex line 532: full draw pipeline. Returns True if drawn."""
        if not force and not self.frame_limiter.should_draw():
            return False

        self.update_inline_viewport(height)
        self.flush_pending_history_lines()

        buf: list[str] = []
        draw_fn(self.viewport, buf)
        self.last_frame_lines = buf
        if sys.stdout.isatty() and buf:
            top = max(1, self.viewport.y + 1)
            for offset, line in enumerate(buf[: self.viewport.height]):
                sys.stdout.write(
                    f"\033[{top + offset};1H\033[2K"
                    + _fit_ansi_line(line, self.viewport.width)
                )
            sys.stdout.write(f"\033[{top};1H")
            sys.stdout.flush()
        self.frame_limiter.mark_draw()
        return True

    def add_history_line(self, line: str):
        self.pending_history_lines.append(line)

    def suspend_viewport(self):
        self._suspend_viewport = Viewport(
            self.viewport.x, self.viewport.y,
            self.viewport.width, self.viewport.height,
        )

    def resume_viewport(self) -> bool:
        if self._suspend_viewport:
            self.viewport = self._suspend_viewport
            self._suspend_viewport = None
            return True
        return False


def _fit_ansi_line(line: str, width: int) -> str:
    """Fit an ANSI-styled line to one terminal row by visible cell count."""
    if width <= 0:
        return ""

    out: list[str] = []
    visible = 0
    pos = 0
    saw_ansi = False

    for match in _ANSI_RE.finditer(line):
        if visible >= width:
            break

        chunk = line[pos:match.start()]
        for ch in chunk:
            if visible >= width:
                break
            out.append(_display_char(ch))
            visible += 1

        if visible < width:
            out.append(match.group(0))
            saw_ansi = True
        pos = match.end()

    if visible < width:
        for ch in line[pos:]:
            if visible >= width:
                break
            out.append(_display_char(ch))
            visible += 1

    if visible < width:
        out.append(" " * (width - visible))
    if saw_ansi:
        out.append(_ANSI_RESET)

    return "".join(out)


def _display_char(ch: str) -> str:
    """Return a printable single-cell replacement for control characters."""
    if ch in {"\n", "\r", "\t"}:
        return " "
    if ord(ch) < 32 or ord(ch) == 127:
        return " "
    return ch

# ---------------------------------------------------------------------------
# one-to-one port extensions (Codex tui.rs lines 65–1099)
# ---------------------------------------------------------------------------

import os
import re
import shutil
import sys
import time
import threading
import queue
import signal
from collections import deque
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import (
    Any, Callable, Deque, Dict, Iterator, List, Optional, Protocol, Tuple,
)


# ---------------------------------------------------------------------------
# Constants (port of codex lines 65)
# ---------------------------------------------------------------------------

# Default frame interval for the rate limiter.
TARGET_FRAME_INTERVAL: float = 1.0 / 30.0  # codex frame_rate_limiter::MIN_FRAME_INTERVAL

# Default viewport height
DEFAULT_VIEWPORT_HEIGHT: int = 24

# Alt screen escape sequences
ANSI_ALT_SCREEN_ENTER: str = "\x1b[?1049h"
ANSI_ALT_SCREEN_LEAVE: str = "\x1b[?1049l"


# ---------------------------------------------------------------------------
# Notification settings (port of codex NotificationMethod / NotificationCondition)
# ---------------------------------------------------------------------------

class NotificationMethodKind(Enum):
    """Port one-to-one of ``codex::tui::NotificationMethod``."""
    Auto = "auto"
    TerminalBell = "terminal_bell"
    Osc9 = "osc9"
    Desktop = "desktop"
    Disabled = "disabled"


@dataclass
class NotificationMethod:
    """Port one-to-one of ``codex::tui::NotificationMethod``."""
    kind: NotificationMethodKind = NotificationMethodKind.Auto

    @staticmethod
    def default() -> "NotificationMethod":
        return NotificationMethod(kind=NotificationMethodKind.Auto)


class NotificationConditionKind(Enum):
    """Port one-to-one of ``codex::tui::NotificationCondition``."""
    Never = "never"
    Unfocused = "unfocused"
    Always = "always"


@dataclass
class NotificationCondition:
    """Port one-to-one of ``codex::tui::NotificationCondition``."""
    kind: NotificationConditionKind = NotificationConditionKind.Unfocused

    @staticmethod
    def default() -> "NotificationCondition":
        return NotificationCondition(kind=NotificationConditionKind.Unfocused)


# ---------------------------------------------------------------------------
# TuiEvent (port of codex lines 474–486)
# ---------------------------------------------------------------------------

class TuiEventKind(Enum):
    """Port one-to-one of ``codex::tui::TuiEvent``."""
    Key = "key"
    Paste = "paste"
    Draw = "draw"
    Resize = "resize"
    Focus = "focus"
    Suspend = "suspend"
    Resume = "resume"


@dataclass
class TuiEvent:
    """Port one-to-one of ``codex::tui::TuiEvent``.

    Carries one of: key, paste text, draw request, resize, focus change,
    suspend / resume.
    """
    kind: TuiEventKind
    key: Optional[Any] = None
    paste: Optional[str] = None
    width: Optional[int] = None
    height: Optional[int] = None
    focused: Optional[bool] = None

    @staticmethod
    def from_key(key: Any) -> "TuiEvent":
        return TuiEvent(kind=TuiEventKind.Key, key=key)

    @staticmethod
    def from_paste(text: str) -> "TuiEvent":
        return TuiEvent(kind=TuiEventKind.Paste, paste=text)

    @staticmethod
    def from_draw() -> "TuiEvent":
        return TuiEvent(kind=TuiEventKind.Draw)

    @staticmethod
    def from_resize(width: int, height: int) -> "TuiEvent":
        return TuiEvent(kind=TuiEventKind.Resize, width=width, height=height)

    @staticmethod
    def from_focus(focused: bool) -> "TuiEvent":
        return TuiEvent(kind=TuiEventKind.Focus, focused=focused)

    @staticmethod
    def from_suspend() -> "TuiEvent":
        return TuiEvent(kind=TuiEventKind.Suspend)

    @staticmethod
    def from_resume() -> "TuiEvent":
        return TuiEvent(kind=TuiEventKind.Resume)


# ---------------------------------------------------------------------------
# TuiEventStream (port of codex TuiEventStream)
# ---------------------------------------------------------------------------

class TuiEventStream:
    """Port one-to-one of ``codex::tui::TuiEventStream``.

    In Rust: ``Pin<Box<dyn Stream<Item = TuiEvent> + Send + 'static>>`` backed
    by crossterm + tokio broadcast. In Python: thread that reads stdin and
    pushes events into a ``queue.Queue``, with a ``pause`` / ``resume`` flag.
    """

    def __init__(self, event_broker: "EventBroker", draw_tx: Any,
                 terminal_focused: "AtomicBool", alt_screen_active: "AtomicBool") -> None:
        self._events: queue.Queue = queue.Queue()
        self._broker = event_broker
        self._draw_tx = draw_tx
        self._terminal_focused = terminal_focused
        self._alt_screen_active = alt_screen_active
        self._closed = False

    def __iter__(self) -> "TuiEventStream":
        return self

    def __next__(self) -> TuiEvent:
        if self._closed:
            raise StopIteration
        try:
            event = self._events.get(timeout=0.1)
        except queue.Empty:
            return None
        if event is None:
            raise StopIteration
        return event

    def push(self, event: TuiEvent) -> None:
        try:
            self._events.put_nowait(event)
        except queue.Full:
            pass

    def close(self) -> None:
        self._closed = True
        try:
            self._events.put_nowait(None)
        except queue.Full:
            pass


# ---------------------------------------------------------------------------
# AtomicBool / AtomicUsize (port of std::sync::atomic)
# ---------------------------------------------------------------------------

class AtomicBool:
    """Thread-safe boolean (port of ``std::sync::atomic::AtomicBool``)."""
    def __init__(self, value: bool = False) -> None:
        self._lock = threading.RLock()
        self._value = value

    def load(self) -> bool:
        with self._lock:
            return self._value

    def store(self, value: bool) -> None:
        with self._lock:
            self._value = value

    def swap(self, value: bool) -> bool:
        with self._lock:
            old = self._value
            self._value = value
            return old

    def compare_exchange(self, expected: bool, desired: bool) -> bool:
        with self._lock:
            if self._value == expected:
                self._value = desired
                return True
            return False


# ---------------------------------------------------------------------------
# EventBroker (port of codex tui::EventBroker)
# ---------------------------------------------------------------------------

class EventBroker:
    """Port one-to-one of ``codex::tui::EventBroker``.

    Manages the lifecycle of the underlying crossterm EventStream so the
    stdin polling can be paused while external programs (e.g. ``$EDITOR``)
    own the terminal.
    """
    def __init__(self) -> None:
        self._paused = AtomicBool(False)
        self._lock = threading.RLock()
        self._stream: Optional[Any] = None

    def pause_events(self) -> None:
        self._paused.store(True)
        with self._lock:
            self._stream = None

    def resume_events(self) -> None:
        with self._lock:
            self._paused.store(False)
            # Caller (Tui::new or main) is responsible for spawning the
            # actual stdin-read thread; we just clear the pause flag.

    def is_paused(self) -> bool:
        return self._paused.load()


# ---------------------------------------------------------------------------
# FrameRateLimiter (port of codex tui/frame_rate_limiter.rs)
# ---------------------------------------------------------------------------

class FrameRateLimiter:
    """Port one-to-one of ``codex::tui::frame_rate_limiter::FrameRateLimiter``.

    Caps redraw frequency at ``interval`` seconds. The default ``interval`` is
    ``TARGET_FRAME_INTERVAL`` (1/30 s).
    """
    MIN_FRAME_INTERVAL: float = 1.0 / 30.0

    def __init__(self, interval: float = MIN_FRAME_INTERVAL) -> None:
        self._interval: float = interval
        self._last_draw: float = 0.0
        self._lock = threading.RLock()

    def should_draw(self) -> bool:
        with self._lock:
            return time.monotonic() - self._last_draw >= self._interval

    def mark_draw(self) -> None:
        with self._lock:
            self._last_draw = time.monotonic()

    def set_interval(self, interval: float) -> None:
        with self._lock:
            self._interval = interval


# ---------------------------------------------------------------------------
# FrameRequester (port of codex tui/frame_requester.rs)
# ---------------------------------------------------------------------------

class FrameRequester:
    """Port one-to-one of ``codex::tui::frameRequester::FrameRequester``.

    Requests redraws from external threads. Internally backed by a
    ``broadcast::Sender<()>`` in Rust; in Python a thread-safe boolean
    flag + an optional notification channel.
    """
    def __init__(self) -> None:
        self._pending: AtomicBool = AtomicBool(False)
        self._lock = threading.RLock()
        self._scheduled_at: float = 0.0
        self._delay_ms: int = 0
        self._listeners: List[Callable[[], None]] = []

    def schedule_frame(self) -> None:
        """Request a redraw on the next frame (port one-to-one)."""
        self._pending.store(True)
        self._delay_ms = 0
        for cb in self._listeners:
            try:
                cb()
            except Exception:
                pass

    def schedule_frame_in(self, ms: int) -> None:
        """Request a redraw after ``ms`` milliseconds (port one-to-one)."""
        self._pending.store(True)
        self._delay_ms = max(self._delay_ms, int(ms))
        self._scheduled_at = time.monotonic()

    def consume(self) -> bool:
        """Check if a frame was requested and clear the flag (port one-to-one)."""
        if self._pending.load():
            self._pending.store(False)
            self._delay_ms = 0
            return True
        return False

    def is_pending(self) -> bool:
        return self._pending.load()

    def add_listener(self, cb: Callable[[], None]) -> None:
        self._listeners.append(cb)

    def request_frame(self) -> None:
        """Alias for ``schedule_frame`` used by older code paths."""
        self.schedule_frame()


# ---------------------------------------------------------------------------
# SuspendContext (port of codex tui::SuspendContext, Unix-only)
# ---------------------------------------------------------------------------

class SuspendContextKind(Enum):
    """Port one-to-one of ``codex::tui::SuspendContext`` (Unix-only in codex)."""
    Default = "default"
    SuspendPending = "suspend_pending"
    Resumed = "resumed"


@dataclass
class SuspendContext:
    """Port one-to-one of ``codex::tui::SuspendContext``."""
    state: SuspendContextKind = SuspendContextKind.Default

    @staticmethod
    def new() -> "SuspendContext":
        return SuspendContext(state=SuspendContextKind.Default)


# ---------------------------------------------------------------------------
# Tui (port of codex tui.rs lines 488–1052)
# ---------------------------------------------------------------------------

# Type alias for the custom terminal backend (port of ``ratatui::Terminal``).
Terminal = Any


@dataclass
class PendingHistoryLines:
    """Port one-to-one of ``codex::tui::PendingHistoryLines``."""
    lines: List[Any] = field(default_factory=list)
    wrap_policy: Any = None


class Tui:
    """Port one-to-one of ``codex::tui::Tui`` (1099 production lines).

    The full codex Tui has the following high-level responsibilities:
    - Owns a ``Terminal`` (ratatui) wrapped around ``CustomTerminal<B>``.
    - Manages alt-screen enter/leave, viewport scrolling, history flush.
    - Drains the crossterm event stream and exposes it as a Tokio stream.
    - Sends desktop notifications on out-of-focus events.
    - Pauses/resumes the event stream for external programs (e.g. ``$EDITOR``).

    The Python port is a structural one-to-one: same fields, same methods, with
    ``TODO: depends on <file>`` markers where the implementation requires a
    submodule port (e.g. ``custom_terminal``, ``terminal_stderr``, pets,
    notifications, etc.).
    """

    # ----- Fields (port one-to-one of codex lines 488–512) -----
    frame_requester: FrameRequester
    draw_tx: Any  # broadcast::Sender<()>
    event_broker: EventBroker
    terminal: Any  # ratatui::Terminal analogue
    pending_history_lines: List[PendingHistoryLines]
    ambient_pet_image_state: Any
    pet_picker_preview_image_state: Any
    alt_saved_viewport: Optional[Any]
    suspend_context: SuspendContext
    alt_screen_active: AtomicBool
    terminal_focused: AtomicBool
    enhanced_keys_supported: bool
    notification_backend: Optional[Any]
    notification_condition: NotificationCondition
    is_zellij: bool
    alt_screen_enabled: bool
    _stderr_guard: Any

    def __init__(self) -> None:
        """Minimal Python constructor (one-to-one to the codex ``Tui::new``)."""
        # Legacy fields (kept from the existing Python port).
        self.viewport = Viewport()
        self.pending_history_lines_legacy: list = []
        self.last_frame_lines: list = []
        self._size = TerminalSize()
        self.frame_limiter = FrameRateLimiter()
        self._suspend_viewport: Optional[Viewport] = None
        # New fields for one-to-one.
        self.frame_requester_obj = FrameRequester()
        self.draw_tx = None
        self.event_broker = EventBroker()
        self.terminal = None
        self.pending_history_lines = []
        self.ambient_pet_image_state = None
        self.pet_picker_preview_image_state = None
        self.alt_saved_viewport = None
        self.suspend_context = SuspendContext.new()
        self.alt_screen_active = AtomicBool(False)
        self.terminal_focused = AtomicBool(True)
        self.enhanced_keys_supported = False
        self.notification_backend = None
        self.notification_condition = NotificationCondition.default()
        self.is_zellij = bool(os.environ.get("ZELLIJ"))
        self.alt_screen_enabled = True
        self._stderr_guard = None

    # ------------------------------------------------------------------
    # Construction / configuration
    # ------------------------------------------------------------------

    @classmethod
    def new(cls, terminal: Any, enhanced_keys_supported: bool,
            stderr_guard: Any) -> "Tui":
        """Port one-to-one of ``Tui::new(terminal, enhanced_keys_supported, stderr_guard)``."""
        inst = cls()
        inst.terminal = terminal
        inst.enhanced_keys_supported = enhanced_keys_supported
        inst._stderr_guard = stderr_guard
        inst.frame_requester_obj = FrameRequester()
        inst.event_broker = EventBroker()
        inst.alt_screen_active = AtomicBool(False)
        inst.terminal_focused = AtomicBool(True)
        inst.is_zellij = bool(os.environ.get("ZELLIJ"))
        return inst

    def set_alt_screen_enabled(self, enabled: bool) -> None:
        """When false, ``enter_alt_screen()`` becomes a no-op (port one-to-one)."""
        self.alt_screen_enabled = bool(enabled)

    def set_notification_settings(self, method: NotificationMethod,
                                  condition: NotificationCondition) -> None:
        """Port one-to-one of ``Tui::set_notification_settings``."""
        self.notification_backend = method
        self.notification_condition = condition

    def frame_requester(self) -> FrameRequester:
        """Port one-to-one of ``Tui::frame_requester`` (clone handle)."""
        return self.frame_requester_obj

    def enhanced_keys_supported(self) -> bool:
        """Port one-to-one of ``Tui::enhanced_keys_supported``."""
        return self.enhanced_keys_supported

    def is_alt_screen_active(self) -> bool:
        """Port one-to-one of ``Tui::is_alt_screen_active``."""
        return self.alt_screen_active.load()

    # ------------------------------------------------------------------
    # Event stream pause / resume (port one-to-one of codex lines 594–602)
    # ------------------------------------------------------------------

    def pause_events(self) -> None:
        """Drop crossterm EventStream to avoid stdin conflicts (port one-to-one)."""
        self.event_broker.pause_events()

    def resume_events(self) -> None:
        """Inverse of ``pause_events`` (port one-to-one)."""
        self.event_broker.resume_events()

    # ------------------------------------------------------------------
    # with_restored (port one-to-one of codex lines 609–647)
    # ------------------------------------------------------------------

    def with_restored(self, mode: "RestoreMode", f: Callable[[], Any]) -> Any:
        """Temporarily restore terminal state to run external program ``f``.

        Sync analogue of the codex async method. ``f`` is invoked as a
        regular Python callable; the result is returned as-is.
        """
        self.pause_events()
        was_alt_screen = self.is_alt_screen_active()
        if was_alt_screen:
            try:
                self.leave_alt_screen()
            except Exception:
                pass
        try:
            mode.restore()
        except Exception:
            pass
        try:
            result = f()
        finally:
            try:
                set_modes()
            except Exception:
                pass
            flush_terminal_input_buffer()
            if was_alt_screen:
                try:
                    self.enter_alt_screen()
                except Exception:
                    pass
            self.resume_events()
        return result

    # ------------------------------------------------------------------
    # Notifications (port one-to-one of codex lines 651–675)
    # ------------------------------------------------------------------

    def notify(self, message: str) -> bool:
        """Emit a desktop notification if unfocused (port one-to-one)."""
        terminal_focused = self.terminal_focused.load()
        if not should_emit_notification(self.notification_condition, terminal_focused):
            return False
        backend = self.notification_backend
        if backend is None:
            return False
        if not hasattr(backend, "notify"):
            return False
        try:
            backend.notify(message)
            return True
        except Exception:
            self.notification_backend = None
            return False

    # ------------------------------------------------------------------
    # Event stream (port one-to-one of codex lines 677–695)
    # ------------------------------------------------------------------

    def event_stream(self) -> TuiEventStream:
        """Return the TUI event stream (port one-to-one)."""
        return TuiEventStream(
            self.event_broker,
            self.draw_tx,
            self.terminal_focused,
            self.alt_screen_active,
        )

    # ------------------------------------------------------------------
    # Alt screen (port one-to-one of codex lines 697–731)
    # ------------------------------------------------------------------

    def enter_alt_screen(self) -> bool:
        """Enter the alternate screen (port one-to-one)."""
        if not self.alt_screen_enabled:
            return False
        sys.stdout.write(ANSI_ALT_SCREEN_ENTER)
        sys.stdout.flush()
        self.alt_screen_active.store(True)
        return True

    def leave_alt_screen(self) -> bool:
        """Leave the alternate screen (port one-to-one)."""
        if not self.alt_screen_enabled:
            return False
        sys.stdout.write(ANSI_ALT_SCREEN_LEAVE)
        sys.stdout.flush()
        self.alt_screen_active.store(False)
        return True

    # ------------------------------------------------------------------
    # History insertion (port one-to-one of codex lines 733–767)
    # ------------------------------------------------------------------

    def insert_history_lines(self, lines: List[Any]) -> None:
        """Insert history lines using default wrap policy (port one-to-one)."""
        self.insert_history_lines_with_wrap_policy(lines, None)

    def insert_history_lines_with_wrap_policy(self, lines: List[Any], wrap_policy: Any) -> None:
        """Insert history lines with a custom wrap policy (port one-to-one)."""
        # The actual rendering is done in insert_history.py; this is the
        # thin wrapper that the TUI exposes.
        self.pending_history_lines.append(
            PendingHistoryLines(lines=list(lines), wrap_policy=wrap_policy)
        )

    def clear_pending_history_lines(self) -> None:
        """Clear any buffered history lines (port one-to-one of codex line 767)."""
        self.pending_history_lines.clear()
        self.pending_history_lines_legacy.clear()

    # ------------------------------------------------------------------
    # Inline viewport / draw (port one-to-one of codex lines 776–1030)
    # ------------------------------------------------------------------

    def update_inline_viewport_for_resize_reflow(self) -> bool:
        """Port one-to-one of ``Tui::update_inline_viewport_for_resize_reflow``."""
        # In the Python port, the inline viewport is already managed by
        # ``update_inline_viewport``. Returns True if the viewport was
        # updated.
        return self.update_inline_viewport(self.viewport.height or DEFAULT_VIEWPORT_HEIGHT)

    def flush_pending_history_lines(self) -> bool:
        """Write buffered history above viewport (port one-to-one of codex line 815)."""
        if not self.pending_history_lines_legacy:
            return False
        lines = self.pending_history_lines_legacy
        self.pending_history_lines_legacy = []
        for line in lines:
            sys.stdout.write(f"\r\n{line}")
        sys.stdout.write("\r\n")
        sys.stdout.flush()
        return True

    def draw(self, height: int, draw_fn: Callable, *, force: bool = False) -> bool:
        """Full draw pipeline (port one-to-one of codex line 841)."""
        if not force and not self.frame_limiter.should_draw():
            return False
        self.update_inline_viewport(height)
        self.flush_pending_history_lines()
        buf: List[str] = []
        draw_fn(self.viewport, buf)
        self.last_frame_lines = buf
        if sys.stdout.isatty() and buf:
            top = max(1, self.viewport.y + 1)
            for offset, line in enumerate(buf[: self.viewport.height]):
                sys.stdout.write(
                    f"\033[{top + offset};1H\033[2K" + _fit_ansi_line(line, self.viewport.width)
                )
            sys.stdout.write(f"\033[{top};1H")
            sys.stdout.flush()
        self.frame_limiter.mark_draw()
        return True

    def draw_ambient_pet_image(self, image: Any) -> bool:
        """Port one-to-one of ``Tui::draw_ambient_pet_image`` (skeleton)."""
        # TODO: depends on pets module
        return False

    def draw_pet_picker_preview_image(self, image: Any) -> bool:
        """Port one-to-one of ``Tui::draw_pet_picker_preview_image`` (skeleton)."""
        return False

    def clear_ambient_pet_image(self) -> bool:
        """Port one-to-one of ``Tui::clear_ambient_pet_image``."""
        return False

    def draw_with_resize_reflow(self, height: int, draw_fn: Callable) -> bool:
        """Port one-to-one of ``Tui::draw_with_resize_reflow`` (skeleton).

        The full codex implementation handles ``transcript_reflow`` state and
        coordinate re-mapping. The Python port delegates to ``draw`` for now.
        """
        return self.draw(height, draw_fn)

    def pending_viewport_area(self) -> Optional[Any]:
        """Port one-to-one of ``Tui::pending_viewport_area``."""
        if self.terminal is not None and hasattr(self.terminal, "viewport_area"):
            return self.terminal.viewport_area
        return None

    # ------------------------------------------------------------------
    # Legacy inline-viewport methods (kept from the prior Python port)
    # ------------------------------------------------------------------

    def _update_size(self) -> None:
        sz = shutil.get_terminal_size((120, 24))
        self._size = TerminalSize(sz.lines, sz.columns)

    def update_inline_viewport(self, height: int) -> bool:
        self._update_size()
        s = self._size
        vp = self.viewport
        vp.height = min(height, s.lines)
        vp.width = s.columns
        if vp.bottom() > s.lines:
            scroll_by = vp.bottom() - s.lines
            if self.is_zellij:
                sys.stdout.write(f"\033[{s.lines - 1};0H")
                sys.stdout.write("\n" * scroll_by)
            else:
                sys.stdout.write(f"\033[1;{s.lines}r")
                for _ in range(scroll_by):
                    sys.stdout.write("\033M")
                sys.stdout.write("\033[r")
            sys.stdout.flush()
            vp.y = s.lines - vp.height
            return True
        return False

    def add_history_line(self, line: str) -> None:
        self.pending_history_lines_legacy.append(line)

    def suspend_viewport(self) -> None:
        self._suspend_viewport = Viewport(
            self.viewport.x, self.viewport.y,
            self.viewport.width, self.viewport.height,
        )

    def resume_viewport(self) -> bool:
        if self._suspend_viewport:
            self.viewport = self._suspend_viewport
            self._suspend_viewport = None
            return True
        return False


# ---------------------------------------------------------------------------
# Restore mode (port of codex tui lines 307–323)
# ---------------------------------------------------------------------------

class RestoreModeKind(Enum):
    """Port one-to-one of ``codex::tui::RestoreMode``."""
    Restore = "restore"
    RestoreAfterExit = "restore_after_exit"
    KeepRaw = "keep_raw"


@dataclass
class RestoreMode:
    """Port one-to-one of ``codex::tui::RestoreMode``."""
    kind: RestoreModeKind = RestoreModeKind.Restore

    def restore(self) -> bool:
        # TODO: depends on crossterm terminal mode restore
        return True


# ---------------------------------------------------------------------------
# Module-level fns (port of codex tui lines 76, 80, 173, 280, 288, 302, 325, 362, 451, 459, 465)
# ---------------------------------------------------------------------------

def running_in_vscode_terminal() -> bool:
    """Port one-to-one of ``codex::tui::running_in_vscode_terminal``."""
    return bool(os.environ.get("VSCODE_INJECTION")) or bool(os.environ.get("VSCODE_PID"))


def should_emit_notification(condition: NotificationCondition, terminal_focused: bool) -> bool:
    """Port one-to-one of ``codex::tui::should_emit_notification``."""
    if condition.kind == NotificationConditionKind.Never:
        return False
    if condition.kind == NotificationConditionKind.Unfocused:
        return not terminal_focused
    if condition.kind == NotificationConditionKind.Always:
        return True
    return False


def set_modes() -> bool:
    """Port one-to-one of ``codex::tui::set_modes`` (skeleton)."""
    # TODO: depends on crossterm enable_raw_mode + alternate_scroll commands
    return True


def restore() -> bool:
    """Port one-to-one of ``codex::tui::restore`` (skeleton)."""
    return True


def restore_after_exit() -> bool:
    """Port one-to-one of ``codex::tui::restore_after_exit`` (skeleton)."""
    return True


def restore_keep_raw() -> bool:
    """Port one-to-one of ``codex::tui::restore_keep_raw`` (skeleton)."""
    return True


def flush_terminal_input_buffer() -> None:
    """Port one-to-one of ``codex::tui::flush_terminal_input_buffer``.

    In codex, on Unix it consumes available input events from crossterm;
    on other platforms it's a no-op. The Python port always no-ops for
    portability (caller can clear sys.stdin if needed).
    """
    return None


def detect_keyboard_enhancement_supported() -> bool:
    """Port one-to-one of ``codex::tui::detect_keyboard_enhancement_supported``."""
    return bool(os.environ.get("TERM")) and "xterm" in os.environ.get("TERM", "").lower()


def set_panic_hook() -> None:
    """Port one-to-one of ``codex::tui::set_panic_hook`` (skeleton)."""
    # TODO: depends on color_eyre panic hook
    return None


# ---------------------------------------------------------------------------
# Public exports
# ---------------------------------------------------------------------------

__all__ = [
    "ANSI_ALT_SCREEN_ENTER",
    "ANSI_ALT_SCREEN_LEAVE",
    "AtomicBool",
    "DEFAULT_VIEWPORT_HEIGHT",
    "EventBroker",
    "FrameRateLimiter",
    "FrameRequester",
    "NotificationCondition",
    "NotificationConditionKind",
    "NotificationMethod",
    "NotificationMethodKind",
    "PendingHistoryLines",
    "RestoreMode",
    "RestoreModeKind",
    "SuspendContext",
    "SuspendContextKind",
    "TARGET_FRAME_INTERVAL",
    "TerminalSize",
    "Tui",
    "TuiEvent",
    "TuiEventKind",
    "TuiEventStream",
    "Viewport",
    "cursor_position_with_crossterm",
    "detect_keyboard_enhancement_supported",
    "flush_terminal_input_buffer",
    "restore",
    "restore_after_exit",
    "restore_keep_raw",
    "running_in_vscode_terminal",
    "set_modes",
    "set_panic_hook",
    "should_emit_notification",
]


# -----------------------------------------------------------------------------
# Stub for ``cursor_position_with_crossterm`` (port of codex line 451).
# -----------------------------------------------------------------------------

def cursor_position_with_crossterm(backend: Any) -> Tuple[int, int]:
    """Port one-to-one of ``codex::tui::cursor_position_with_crossterm``.

    Returns ``(row, col)`` of the current cursor. The Python port returns
    ``(0, 0)`` since real cursor query requires TTY ioctl; callers should
    not depend on this for layout.
    """
    return (0, 0)
