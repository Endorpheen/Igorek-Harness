"""Mouse controller — single integration point for TUI mouse support.

The controller owns the user's *desire* to use the mouse (``/mouse on``
and ``IGOREK_TUI_MOUSE``) separately from the *actual capture*, which also
depends on the terminal type, output adapter and current interface. It is
stored at chat-session level so it survives idle/active application
switches.

The controller is deliberately testable without a terminal: its event
dispatch takes an injected frame and standard prompt_toolkit handler, and
all deterministic policy (settings, zones, selection, cleanup) is pure
Python. The TZ contract is explicit: the interceptor never relies on
``return NotImplemented`` for routing — when it does not handle an event
it calls the saved stock handler exactly once.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Callable, Optional

from .mouse_clipboard import ClipboardService
from .mouse_frame import (
    FrameRegion,
    FrameRow,
    ScreenFrame,
    Selection,
    extract_selection,
    extract_selection_absolute,
)
from .mouse_protocol import (
    MouseButton,
    MouseEvent,
    MouseEventKind,
    parse_sgr_mouse_sequence,
)

try:
    from prompt_toolkit.filters import Condition
    from prompt_toolkit.keys import Keys
except Exception:  # pragma: no cover
    Condition = None  # type: ignore
    Keys = None  # type: ignore

from .mouse_output import MouseCaptureOutput, wrap_output_for_mouse


_TRUE_VALUES = {"1", "on", "true", "yes"}
_FALSE_VALUES = {"0", "off", "false", "no", ""}


def env_mouse_forced_off() -> bool:
    """``IGOREK_TUI_MOUSE=0|off|false`` forces capture off."""
    value = os.environ.get("IGOREK_TUI_MOUSE")
    if value is None:
        return False
    return value.strip().lower() in _FALSE_VALUES


def env_mouse_requested() -> bool:
    value = os.environ.get("IGOREK_TUI_MOUSE")
    if value is None:
        return False
    return value.strip().lower() in _TRUE_VALUES


# Lines scrolled per wheel notch in fullscreen transcript. Terminals differ in
# how many wheel events they emit per physical notch, so this is tunable; the
# default is deliberately small so scrolling stays smooth (like a terminal).
_WHEEL_LINES_DEFAULT = 1
_WHEEL_LINES_MAX = 20


def env_wheel_lines() -> int:
    """``IGOREK_TUI_WHEEL_LINES=N`` controls lines per wheel event (1..20)."""
    raw = os.environ.get("IGOREK_TUI_WHEEL_LINES", "").strip()
    if raw:
        try:
            return max(1, min(_WHEEL_LINES_MAX, int(raw)))
        except ValueError:
            pass
    return _WHEEL_LINES_DEFAULT


def _load_standard_mouse_handler() -> Optional[Callable]:
    try:
        from prompt_toolkit.key_binding.bindings.mouse import load_mouse_bindings

        kb = load_mouse_bindings()
        bindings = kb.get_bindings_for_keys((Keys.Vt100MouseEvent,))
        if bindings:
            return bindings[-1].handler
    except Exception:
        return None
    return None


@dataclass
class GestureState:
    owner: str = "none"  # none | app | delegate
    press_row: int = -1
    press_col: int = -1
    dragging: bool = False


class MouseController:
    """Coordinates mouse capture, zones, selection and clipboard."""

    def __init__(
        self,
        *,
        user_enabled: Optional[bool] = None,
        notify: Optional[Callable[[str], None]] = None,
        on_toggle_reasoning: Optional[Callable[[], None]] = None,
        on_toggle_reasoning_cell: Optional[Callable[[str], None]] = None,
        on_open_viewer: Optional[Callable[[], Optional[bool]]] = None,
        clipboard_service: Optional[ClipboardService] = None,
    ) -> None:
        self._user_enabled = True if user_enabled is None else bool(user_enabled)
        self._forced_off = env_mouse_forced_off()
        if self._forced_off:
            self._user_enabled = False
        self._supported = False
        self._capture_output: Optional[MouseCaptureOutput] = None
        self._standard_handler = _load_standard_mouse_handler()
        self._wheel_lines = env_wheel_lines()

        self._notify = notify or (lambda msg: self.set_notification(msg))
        self._on_toggle_reasoning = on_toggle_reasoning
        self._on_toggle_reasoning_cell = on_toggle_reasoning_cell
        self._on_open_viewer = on_open_viewer
        self.clipboard = clipboard_service or ClipboardService()

        self._frame = ScreenFrame()
        self._frame_builder: Optional[Callable[[], Optional[ScreenFrame]]] = None
        self._revision = 0

        self.gesture = GestureState()
        self.selection: Optional[Selection] = None
        # Absolute transcript-coordinate selection (fullscreen only) so a drag
        # can span rows scrolled off the visible window.
        self._abs_anchor: Optional[tuple[int, int]] = None
        self._abs_focus: Optional[tuple[int, int]] = None
        self._expanded_cells: set[str] = set()

        self._app = None
        self._viewer_active = False
        self._viewer_requested = False
        self._on_wheel: Optional[Callable[[int], None]] = None
        self._capture_allowed = False
        self._notification: Optional[str] = None
        self._notification_expiry = 0.0
        # Set when a delegated (composer) mouse release may have finished a
        # text selection that should be copied to the system clipboard.
        self._pending_composer_copy = False

    # ── viewer hand-off ────────────────────────────────────────────

    def consume_viewer_request(self) -> bool:
        requested = self._viewer_requested
        self._viewer_requested = False
        return requested

    # ── notifications ──────────────────────────────────────────────

    def set_notification(self, message: str, *, ttl: float = 2.0) -> None:
        import time

        self._notification = message
        self._notification_expiry = time.monotonic() + ttl
        if self._app is not None:
            try:
                self._app.invalidate()
            except Exception:
                pass

    def current_notification(self) -> Optional[str]:
        import time

        if self._notification is None:
            return None
        if time.monotonic() >= self._notification_expiry:
            self._notification = None
            return None
        return self._notification

    # ── capture state ──────────────────────────────────────────────

    @property
    def user_enabled(self) -> bool:
        return self._user_enabled

    @property
    def forced_off(self) -> bool:
        return self._forced_off

    @property
    def supported(self) -> bool:
        return self._supported

    def configure_support(self, supported: bool) -> None:
        self._supported = bool(supported)

    def should_capture(self) -> bool:
        # Capture is only useful where the app owns an in-app transcript
        # (fullscreen mode). In the default bottom-anchored renderer the
        # tracked history lives in the terminal scrollback, so we leave the
        # mouse to the terminal and keep native selection/scroll working.
        return (
            self._user_enabled
            and not self._forced_off
            and self._supported
            and self._capture_allowed
            and not self._viewer_active
        )

    def set_capture_allowed(self, allowed: bool) -> None:
        self._capture_allowed = bool(allowed)

    def enable(self) -> bool:
        if self._forced_off:
            return False
        self._user_enabled = True
        self._capture_allowed = True
        return True

    def disable(self) -> None:
        self._user_enabled = False
        self._capture_allowed = False
        self.gesture = GestureState()
        self.selection = None
        self._abs_anchor = None
        self._abs_focus = None
        self._frame = ScreenFrame()
        if self._app is not None:
            try:
                self._app.invalidate()
            except Exception:
                pass

    def toggle(self) -> bool:
        if self._user_enabled:
            self.disable()
            return False
        return self.enable()

    # ── output / bindings integration ──────────────────────────────

    def wrap_output(self, output: object) -> object:
        if not self._terminal_supported(output):
            self.configure_support(False)
            return output
        wrapped = wrap_output_for_mouse(output)
        if wrapped is None:
            self.configure_support(False)
            return output
        self.configure_support(True)
        self._capture_output = wrapped
        return wrapped

    @staticmethod
    def _terminal_supported(output: object) -> bool:
        if output is None:
            return False
        term = (os.environ.get("TERM") or "").strip().lower()
        if term in ("", "dumb"):
            return False
        try:
            import sys

            if not sys.stdin.isatty() or not sys.stdout.isatty():
                return False
        except Exception:
            return False
        return True

    def mouse_support_filter(self):
        if Condition is None:
            return False
        return Condition(lambda: self.should_capture())

    def install_bindings(self, kb) -> None:
        if Keys is None or kb is None:
            return

        @kb.add(Keys.Vt100MouseEvent)
        def _on_mouse(event):
            verdict = self.dispatch(event)
            if verdict == "handled":
                return None
            handler = self._standard_handler
            if handler is not None:
                event = self._normalize_delegated_drag_event(event)
                result = handler(event)
            else:
                result = None
            if self._pending_composer_copy:
                self._pending_composer_copy = False
                # Delegate first (prompt_toolkit finalizes the buffer
                # selection), then copy it to the system clipboard.
                self._copy_composer_selection()
            return result

    def _normalize_delegated_drag_event(self, event):
        """Rewrite no-button motion as left-button motion during a drag.

        Some terminals report mouse movement with button code 35 (NO_BUTTON)
        while the left button is actually held. ``prompt_toolkit``'s
        ``BufferControl`` only starts a text selection on motion events with a
        button, so the composer showed no selection highlight while dragging.
        During an active delegated press we normalize ``<35;`` motion to
        ``<32;`` (left-button motion) so the selection is created live.
        """
        if self.gesture.owner != "delegate":
            return event
        data = getattr(event, "data", "") or ""
        if not data.startswith("\x1b[<35;"):
            return event

        class _EventProxy:
            __slots__ = ("_event", "data")

            def __init__(self, inner, data):
                self._event = inner
                self.data = data

            def __getattr__(self, name):
                return getattr(self._event, name)

        return _EventProxy(event, "\x1b[<32;" + data[len("\x1b[<35;"):])

    def release_output(self) -> None:
        """Best-effort idempotent disable on teardown."""
        out = self._capture_output
        if out is not None and getattr(out, "capture_enabled", False):
            try:
                out.disable_mouse_support()
            except Exception:
                pass

    def reassert_capture(self) -> None:
        """Re-enable terminal mouse tracking after a nested prompt session.

        A nested ``PromptSession`` (e.g. the ``request_user_input`` form)
        disables mouse support on exit; re-assert it so the fullscreen app
        keeps wheel/click handling.
        """
        out = self._capture_output
        if out is not None and self.should_capture():
            try:
                out.enable_mouse_support()
            except Exception:
                pass

    # ── frame model ────────────────────────────────────────────────

    def set_frame_builder(self, builder: Optional[Callable[[], Optional[ScreenFrame]]]) -> None:
        self._frame_builder = builder

    def set_frame(self, frame: Optional[ScreenFrame]) -> None:
        if frame is None:
            return
        self._revision += 1
        frame.revision = self._revision
        self._frame = frame
        self.gesture = GestureState()
        self.selection = None

    def current_frame(self) -> ScreenFrame:
        if self._frame_builder is not None:
            try:
                built = self._frame_builder()
            except Exception:
                built = None
            if built is not None:
                return built
        return self._frame

    def set_app(self, app) -> None:
        self._app = app
        self._viewer_requested = False
        self.gesture = GestureState()
        self.selection = None

    def clear_app(self) -> None:
        self._app = None
        self._viewer_requested = False
        self.gesture = GestureState()
        self.selection = None

    # ── cell expand state ──────────────────────────────────────────

    def is_cell_expanded(self, cell_id: str) -> bool:
        return bool(cell_id) and cell_id in self._expanded_cells

    def toggle_cell(self, cell_id: str) -> None:
        if not cell_id:
            return
        if cell_id in self._expanded_cells:
            self._expanded_cells.discard(cell_id)
        else:
            self._expanded_cells.add(cell_id)
        if self._app is not None:
            try:
                self._app.invalidate()
            except Exception:
                pass

    # ── dispatch ───────────────────────────────────────────────────

    def dispatch(self, pt_event) -> str:
        """Return ``"handled"`` or ``"delegate"`` for a prompt_toolkit event."""
        if not self.should_capture():
            self._debug("dispatch capture=off -> delegate")
            return "delegate"
        data = getattr(pt_event, "data", "") or ""
        parsed = parse_sgr_mouse_sequence(data)
        if parsed is None:
            return "delegate"
        if parsed.button in (MouseButton.MIDDLE, MouseButton.RIGHT):
            return "delegate"
        if parsed.kind in (MouseEventKind.SCROLL_LEFT, MouseEventKind.SCROLL_RIGHT):
            return "delegate"
        if parsed.button is MouseButton.LEFT and parsed.has_modifiers:
            # Shift/Alt/Ctrl clicks are left to the terminal or native code.
            return "delegate"

        frame = self.current_frame()
        if frame is None:
            return "delegate"

        if parsed.kind is MouseEventKind.SCROLL_UP:
            verdict = self._on_scroll_up(parsed, frame)
        elif parsed.kind is MouseEventKind.SCROLL_DOWN:
            verdict = self._on_scroll_down(parsed, frame)
        elif parsed.button is not MouseButton.LEFT:
            verdict = "delegate"
        elif parsed.kind is MouseEventKind.PRESS:
            verdict = self._on_left_press(parsed, frame)
        elif parsed.kind is MouseEventKind.MOVE:
            verdict = self._on_left_move(parsed, frame)
        elif parsed.kind is MouseEventKind.RELEASE:
            verdict = self._on_left_release(parsed, frame)
        else:
            verdict = "delegate"
        self._debug(
            f"dispatch kind={parsed.kind.value} btn={parsed.button.value} "
            f"x={parsed.x} y={parsed.y} rows={len(frame.rows)} "
            f"composer_top={frame.composer_top} -> {verdict}"
        )
        return verdict

    def _debug(self, message: str) -> None:
        if os.environ.get("IGOREK_TUI_MOUSE_DEBUG", "").strip().lower() not in (
            "1",
            "on",
            "true",
            "yes",
        ):
            return
        try:
            with open("/tmp/igorek-tui-mouse.log", "a", encoding="utf-8") as fh:
                fh.write(message + "\n")
        except Exception:
            pass

    # -- helpers ----------------------------------------------------

    @staticmethod
    def _in_composer(row: int, frame: ScreenFrame) -> bool:
        return frame.composer_top is not None and row >= frame.composer_top

    def _in_history_area(self, row: int, frame: ScreenFrame) -> bool:
        if frame.composer_top is None:
            return False
        return row < frame.composer_top

    def _on_scroll_up(self, event: MouseEvent, frame: ScreenFrame) -> str:
        if self._in_composer(event.y, frame):
            return "delegate"
        if self._on_wheel is not None:
            self._on_wheel(self._wheel_lines)
            return "handled"
        # No in-app scroll: hand the wheel back to the stock handler/terminal.
        return "delegate"

    def _on_scroll_down(self, event: MouseEvent, frame: ScreenFrame) -> str:
        if self._in_composer(event.y, frame):
            return "delegate"
        if self._on_wheel is not None:
            self._on_wheel(-self._wheel_lines)
            return "handled"
        return "delegate"

    def _on_left_press(self, event: MouseEvent, frame: ScreenFrame) -> str:
        if self._in_composer(event.y, frame):
            self.gesture = GestureState(owner="delegate")
            return "delegate"
        self.gesture = GestureState(
            owner="app", press_row=event.y, press_col=event.x, dragging=False
        )
        self.selection = None
        self._abs_anchor = None
        self._abs_focus = None
        if frame.full_lines:
            self._abs_anchor = (frame.window_start + event.y, event.x)
            self._abs_focus = self._abs_anchor
        return "handled"

    def _on_left_move(self, event: MouseEvent, frame: ScreenFrame) -> str:
        if self.gesture.owner != "app":
            return "delegate" if self.gesture.owner == "delegate" else "handled"
        if not self.gesture.dragging:
            if (event.y, event.x) == (self.gesture.press_row, self.gesture.press_col):
                return "handled"
            self.gesture.dragging = True
        self.selection = Selection(
            anchor_row=self.gesture.press_row,
            anchor_col=self.gesture.press_col,
            focus_row=event.y,
            focus_col=event.x,
            frame_revision=frame.revision,
        )
        if frame.full_lines and self._abs_anchor is not None:
            self._abs_focus = (frame.window_start + event.y, event.x)
            # Auto-scroll when dragging against the top/bottom edge so the
            # selection can reach rows outside the visible window.
            if self._on_wheel is not None:
                last_row = max(0, len(frame.rows) - 1)
                if event.y <= 0:
                    self._on_wheel(self._wheel_lines)
                elif event.y >= last_row:
                    self._on_wheel(-self._wheel_lines)
        if self._app is not None:
            try:
                self._app.invalidate()
            except Exception:
                pass
        return "handled"

    def _on_left_release(self, event: MouseEvent, frame: ScreenFrame) -> str:
        if self.gesture.owner == "delegate":
            self.gesture = GestureState()
            # The stock handler owns this event (e.g. the composer input field).
            # After it finalizes a drag selection, copy that selection.
            self._pending_composer_copy = True
            return "delegate"
        if self.gesture.owner != "app":
            self.gesture = GestureState()
            return "handled"
        dragging = self.gesture.dragging
        self.gesture = GestureState()
        if dragging:
            self.selection = Selection(
                anchor_row=self._last_anchor_row(frame),
                anchor_col=self._last_anchor_col(frame),
                focus_row=event.y,
                focus_col=event.x,
                frame_revision=frame.revision,
            )
            if frame.full_lines and self._abs_anchor is not None:
                self._abs_focus = (frame.window_start + event.y, event.x)
            self._finish_selection(frame)
            return "handled"
        return self._handle_click(event, frame)

    # The anchor must survive across the release; ``_on_left_move`` stores
    # it in ``self.selection`` while dragging, so reuse it when present.
    def _last_anchor_row(self, frame: ScreenFrame) -> int:
        if self.selection is not None:
            return self.selection.anchor_row
        return self.gesture.press_row

    def _last_anchor_col(self, frame: ScreenFrame) -> int:
        if self.selection is not None:
            return self.selection.anchor_col
        return self.gesture.press_col

    def _finish_selection(self, frame: ScreenFrame) -> None:
        selection = self.selection
        self.selection = None
        abs_anchor = self._abs_anchor
        abs_focus = self._abs_focus
        self._abs_anchor = None
        self._abs_focus = None
        text: Optional[str] = None
        if frame.full_lines and abs_anchor is not None and abs_focus is not None:
            text = extract_selection_absolute(
                frame.full_lines,
                abs_anchor[0],
                abs_anchor[1],
                abs_focus[0],
                abs_focus[1],
            )
        if text is None and selection is not None:
            text = extract_selection(frame, selection)
        self._debug(
            f"selection abs={abs_anchor}..{abs_focus}"
            f" extracted={None if text is None else len(text)}"
        )
        if not text or not text.strip():
            return
        result = self.clipboard.copy(text)
        if result.message and self._notify is not None:
            self._notify(result.message)

    def _copy_composer_selection(self) -> None:
        """Copy a mouse selection made in the input field to the clipboard.

        ``prompt_toolkit`` selects text in a ``BufferControl`` on drag, but does
        not copy it anywhere. This mirrors opencode-style behavior: releasing
        the mouse over a selection copies the text.
        """
        app = self._app
        if app is None:
            return
        try:
            buffer = app.current_buffer
        except Exception:
            return
        if buffer is None or getattr(buffer, "selection_state", None) is None:
            return
        try:
            data = buffer.copy_selection()
        except Exception:
            return
        text = str(getattr(data, "text", "") or "")
        if not text.strip():
            return
        result = self.clipboard.copy(text)
        if result.message and self._notify is not None:
            self._notify(result.message)
        try:
            app.invalidate()
        except Exception:
            pass

    def _handle_click(self, event: MouseEvent, frame: ScreenFrame) -> str:
        row = event.y
        region = frame.region_for_row(row)
        if region is None:
            return "handled"
        if region.kind == "reasoning":
            if (
                region.cell_id
                and region.expandable
                and self._on_toggle_reasoning_cell is not None
            ):
                try:
                    self._on_toggle_reasoning_cell(region.cell_id)
                except Exception:
                    pass
            elif self._on_toggle_reasoning is not None:
                try:
                    self._on_toggle_reasoning()
                except Exception:
                    pass
            return "handled"
        if region.expandable and region.cell_id:
            if region.tail_row is not None and row >= region.tail_row:
                self.toggle_cell(region.cell_id)
                return "handled"
            if region.header_row is not None and row == region.header_row:
                self.toggle_cell(region.cell_id)
                return "handled"
            self.toggle_cell(region.cell_id)
            return "handled"
        return "handled"

    # ── local commands / status ────────────────────────────────────

    def parse_command(self, text: str) -> Optional[str]:
        stripped = (text or "").strip()
        if stripped == "/mouse":
            return "status"
        parts = stripped.split()
        if not parts or parts[0] != "/mouse":
            return None
        if len(parts) == 1:
            return "status"
        arg = parts[1].lower()
        if arg in ("on", "off", "status"):
            return arg
        return "invalid"

    def handle_command(self, text: str) -> Optional[str]:
        """Handle a ``/mouse`` command locally; return a message or None."""
        action = self.parse_command(text)
        if action is None:
            return None
        if action == "on":
            if self._forced_off:
                return "Мышь отключена принудительно через IGOREK_TUI_MOUSE."
            if not self._supported:
                return "Мышь недоступна для текущего терминала/вывода."
            self.enable()
            return "Мышь включена."
        if action == "off":
            self.disable()
            self.release_output()
            return "Мышь выключена."
        if action == "invalid":
            return "Использование: /mouse [on|off|status]"
        return self.status_text()

    def status_text(self) -> str:
        desired = "on" if self._user_enabled else "off"
        supported = "yes" if self._supported else "no"
        capture = "active" if self.should_capture() else "inactive"
        backend = self.clipboard.last_backend or "none"
        error = self.clipboard.last_error
        import shutil

        def _has(name: str) -> str:
            try:
                return "yes" if shutil.which(name) else "no"
            except Exception:
                return "?"

        candidates = (
            f"wl-copy={_has('wl-copy')} xclip={_has('xclip')} "
            f"xsel={_has('xsel')} tmux={_has('tmux')} arboard=?"
        )
        lines = [
            "Mouse: "
            f"desired={desired} supported={supported} capture={capture} "
            f"forced_off={'yes' if self._forced_off else 'no'}",
            f"Clipboard candidates: {candidates}",
            f"Clipboard last={backend}" + (f" error={error}" if error else ""),
            "Использование: /mouse [on|off|status]",
        ]
        return "\n".join(lines)

    def set_viewer_active(self, active: bool) -> None:
        self._viewer_active = bool(active)

    def set_wheel_handler(
        self, handler: Optional[Callable[[int], None]]
    ) -> None:
        """Install an in-app transcript scroller (fullscreen mode).

        ``handler(delta)`` receives a positive delta for wheel-up. When no
        handler is installed the wheel event is delegated to the terminal /
        stock handler.
        """
        self._on_wheel = handler


__all__ = [
    "MouseController",
    "env_mouse_forced_off",
    "env_mouse_requested",
]
