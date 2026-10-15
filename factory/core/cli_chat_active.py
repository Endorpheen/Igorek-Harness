"""Active agent-turn runtime for rich chat."""
from __future__ import annotations

import asyncio
import json
import queue
import re
import shutil
import threading
import time
from pathlib import Path
from typing import Optional

from rich.live import Live

try:
    from prompt_toolkit import PromptSession
    from prompt_toolkit.application import Application, run_in_terminal
    from prompt_toolkit.buffer import Buffer
    from prompt_toolkit.document import Document
    from prompt_toolkit.formatted_text import ANSI, HTML
    from prompt_toolkit.key_binding import KeyBindings
    from prompt_toolkit.layout import ConditionalContainer, Float, FloatContainer, HSplit, Layout, Window
    from prompt_toolkit.layout.controls import BufferControl, FormattedTextControl
    from prompt_toolkit.layout.dimension import Dimension
    from prompt_toolkit.layout.menus import CompletionsMenu
    from prompt_toolkit.layout.processors import BeforeInput
    from prompt_toolkit.output import create_output
    from prompt_toolkit.styles import Style
    from prompt_toolkit.validation import Validator, ValidationError
    from prompt_toolkit.filters import Condition
except Exception:
    PromptSession = None; Application = None; Buffer = None; Document = None
    run_in_terminal = None
    KeyBindings = None; ANSI = None; HTML = None
    HSplit = None; Layout = None; Window = None; ConditionalContainer = None
    Float = None; FloatContainer = None; CompletionsMenu = None
    BufferControl = None; FormattedTextControl = None; Dimension = None; BeforeInput = None; Style = None
    Validator = object; ValidationError = ValueError; Condition = None
    create_output = None

from .cli_tasks import cancel_task, create_task, get_task
from .message_queue import MessageQueue
from .runtime import get_profile_config, get_workspace_root, profile_model_name
from .status_line import StatusLineRuntime
from .tui.bottom_pane import BottomPane
from .tui.bottom_pane.chat_composer import ActivePopup, ChatComposer
from .tui.bottom_pane.request_user_input_form import RequestUserInputForm
from .tui.chatwidget import ChatWidget
from .tui.cells.history_cell import ReasoningCell
from .tui.render.renderable import Rect
from .tui.status_indicator_widget import StatusIndicatorWidget
from .tui.trace_reducer import TraceEventReducer
from .terminal_render_log import terminal_render_log
from .cli_chat_rich_shared import (
    _InputFieldProcessor, _ansi_to_formatted_text, _branch, _filter_control_chars,
    _install_screenshot_hotkey_noops,
)

_SPINNER_CHARS = ["·","✢","✳","✶","✻","✽"]
_QUEUED_TASK_NOTICE = "[33mTask is still queued; worker has not picked it up yet.[0m"
_CHAT_INPUT_MIN_HEIGHT = 3
_CHAT_INPUT_TERMINAL_RESERVED_ROWS = 6
_CHAT_TEXTAREA_MIN_HEIGHT = 1
_CHAT_COMPOSER_VERTICAL_PADDING = 2
_LIVE_SPACER_LINE = " "


def _display_width(text: str) -> int:
    try:
        from wcwidth import wcswidth
        width = wcswidth(text)
        return max(0, width)
    except Exception:
        return len(text)


def _wrapped_line_count(text: str, width: int) -> int:
    wrap_width = max(1, width)
    total = 0
    for line in (text or "").split("\n"):
        total += max(1, (_display_width(line) + wrap_width - 1) // wrap_width)
    return max(1, total)


def _chat_textarea_height(buffer) -> object:
    """Codex-style textarea height: grow by wrapped lines, bounded by terminal rows."""
    if Dimension is None:
        return _CHAT_TEXTAREA_MIN_HEIGHT
    terminal = shutil.get_terminal_size((120, 24))
    max_allowed = max(
        _CHAT_TEXTAREA_MIN_HEIGHT,
        terminal.lines - _CHAT_INPUT_TERMINAL_RESERVED_ROWS - _CHAT_COMPOSER_VERTICAL_PADDING,
    )
    # Match Codex's width-aware textarea desired_height approach: count visual
    # wrapped lines, not only explicit newline characters. Leave room for the
    # composer chevron prefix.
    usable_width = max(1, terminal.columns - 4)
    text = getattr(buffer, "text", "") or ""
    line_count = _wrapped_line_count(text, usable_width)
    height = max(_CHAT_TEXTAREA_MIN_HEIGHT, min(max_allowed, line_count))
    return Dimension.exact(height)


def _chat_input_height(buffer) -> object:
    """Total Codex-like composer surface height: padding + textarea."""
    if Dimension is None:
        return _CHAT_INPUT_MIN_HEIGHT
    text_area = _dimension_preferred(
        _chat_textarea_height(buffer),
        _CHAT_TEXTAREA_MIN_HEIGHT,
    )
    height = max(_CHAT_INPUT_MIN_HEIGHT, text_area + _CHAT_COMPOSER_VERTICAL_PADDING)
    return Dimension.exact(height)


def _dimension_preferred(value, fallback: int) -> int:
    try:
        return int(getattr(value, "preferred", value))
    except Exception:
        return fallback


def _idle_output_height(lines: list[str], buffer, *, extra_reserved_rows: int = 0) -> object:
    """Size idle scrollback to its content so the bottom pane hugs the output."""
    if Dimension is None:
        return max(1, len(lines))
    terminal = shutil.get_terminal_size((120, 24))
    input_height = _dimension_preferred(
        _chat_input_height(buffer),
        _CHAT_INPUT_MIN_HEIGHT,
    )
    reserved_bottom = input_height + 2 + max(0, extra_reserved_rows)  # separator + footer
    max_output = max(1, terminal.lines - reserved_bottom)
    usable_width = max(1, terminal.columns)
    line_count = sum(_wrapped_line_count(str(line), usable_width) for line in lines)
    return Dimension.exact(max(1, min(max_output, line_count)))


def _has_visible_output_lines(lines: list[str]) -> bool:
    return any(str(line).strip() for line in lines)


def _working_activity_should_show(running_state: dict) -> bool:
    return bool(running_state.get("task_active"))


def _queued_input_lines(running_state: dict) -> list[str]:
    """Human-readable lines for follow-up messages queued behind a live turn."""
    queued = [
        str(item)
        for item in (running_state.get("queued_input_items") or [])
        if str(item).strip()
    ]
    lines: list[str] = []
    for item in queued[:3]:
        preview = " ".join(item.split())
        if len(preview) > 80:
            preview = preview[:79] + "…"
        lines.append(f"  ⏳ В очереди: {preview}")
    if len(queued) > 3:
        lines.append(f"  ⏳ ... и ещё {len(queued) - 3}")
    return lines


def _working_activity_needs_leading_gap(lines: list[str], running_state: dict) -> bool:
    return _working_activity_should_show(running_state) and (
        _has_visible_output_lines(lines)
        or bool(running_state.get("inline_scrollback_needs_activity_gap"))
    )


def _working_activity_reserved_rows(running_state: dict) -> int:
    if not _working_activity_should_show(running_state):
        return 0
    return 2

def _trace_path_for_task(task_id: str) -> Path:
    return Path(get_workspace_root())/f"temp_execution_{task_id[:8]}"/"cc_backend_trace.jsonl"

def _read_new_trace_events(trace_path: Path, seen: int):
    if not trace_path.exists(): return [], seen
    try: text = trace_path.read_text(encoding="utf-8")
    except OSError: return [], seen
    lines = text.splitlines()
    if len(lines) < seen: seen = 0
    evs = []
    next_seen = seen
    final_line_complete = text.endswith("\n")
    for idx, l in enumerate(lines[seen:], start=seen):
        try:
            e = json.loads(l)
        except json.JSONDecodeError:
            if idx == len(lines) - 1 and not final_line_complete:
                break
            next_seen = idx + 1
            continue
        next_seen = idx + 1
        if isinstance(e, dict): evs.append(e)
    return evs, next_seen

def _make_agent_prompt_session(status_line=None, *, running_state=None):
    """PromptSession for agent turn — Codex-style toolbar with StatusIndicatorWidget."""
    if PromptSession is None: return None
    style = Style.from_dict({
        "bottom-toolbar": "#6c6c6c",
        "input-field": "bg:#303030 #ffffff",
        "selected": "bg:#0f5f87 #ffffff",
    })
    class V(Validator):
        def validate(self, d):
            if not d.text.strip(): raise ValidationError(message="")
    kb = KeyBindings() if KeyBindings is not None else None
    if kb is not None:
        _install_screenshot_hotkey_noops(kb)

        @kb.add("enter")
        def _(e): e.current_buffer.validate_and_handle()
        @kb.add("c-j")
        def _(e): e.current_buffer.insert_text("\n")
        @kb.add("c-c")
        def _(e):
            if running_state is not None: running_state["interrupted"] = True
            e.app.exit(result="__INTERRUPT__")

    status_widget = StatusIndicatorWidget()
    if running_state is not None and "t0" in running_state:
        status_widget.t0 = running_state["t0"]

    def _tb():
        if running_state is None: return ""
        elapsed = time.monotonic() - running_state.get("t0", time.monotonic())
        status_widget.t0 = running_state.get("t0", time.monotonic())
        ch = "•"
        if elapsed < 60: ts = f"{elapsed:.0f}s"
        elif elapsed < 3600: ts = f"{int(elapsed//60)}m {int(elapsed%60):02d}s"
        else:
            h = int(elapsed//3600); m = int((elapsed%3600)//60)
            ts = f"{h}h {m:02d}m {int(elapsed%60):02d}s"
        parts = []
        # Line 1: activity + status (Codex: • Working (15s · esc to interrupt))
        parts.append(f"  {ch} Working ({ts} · esc to interrupt)")
        # Line 2: queued messages preview with ↳ prefix
        pending = running_state.get("pending_count",0)
        preview_items = running_state.get("preview_items",[])
        if pending > 0 and preview_items:
            for m in preview_items[:2]:
                t = m if len(m) <= 60 else m[:57] + "..."
                parts.append(f"  ↳ {t}")
        # Line 3: footer — status line info
        if status_line is not None:
            t = status_line.toolbar_text()
            if t and status_line.config.padding: t = (" " * status_line.config.padding) + t
            if t: parts.append(f"  {t}")
        combined = "\n".join(parts)
        if ANSI is not None: return ANSI(combined)
        if HTML is not None:
            clean = re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", combined)
            esc = clean.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("\n", "<br/>")
            return HTML(f"<style fg='#6c6c6c'>{esc}</style>")
        return combined
    return PromptSession(
        completer=None, complete_while_typing=False, erase_when_done=True,
        multiline=True, validator=V(), bottom_toolbar=_tb, key_bindings=kb, style=style,
    )


def _running_elapsed_label(elapsed: float) -> str:
    if elapsed < 60:
        return f"{elapsed:.0f}s"
    if elapsed < 3600:
        return f"{int(elapsed // 60)}m {int(elapsed % 60):02d}s"
    h = int(elapsed // 3600)
    m = int((elapsed % 3600) // 60)
    return f"{h}h {m:02d}m {int(elapsed % 60):02d}s"


def _running_activity_lines(running_state: dict) -> list[str]:
    elapsed = time.monotonic() - running_state.get("t0", time.monotonic())
    lines = []
    if not running_state.get("status_in_bottom_pane"):
        if running_state.get("task_done"):
            lines.append(f"  Worked for {_running_elapsed_label(elapsed)}")
        else:
            lines.append(f"  • Working ({_running_elapsed_label(elapsed)} · esc to interrupt)")
    return lines


def _running_pending_preview_lines(running_state: dict) -> list[str]:
    lines = []
    preview_items = list(running_state.get("preview_items") or [])
    pending_count = int(running_state.get("pending_count") or len(preview_items))
    if preview_items:
        lines.append("  • Messages to be submitted after next tool call (press esc to interrupt and send immediately)")
        for item in preview_items[:3]:
            text = item if len(item) <= 76 else item[:73] + "..."
            lines.append(f"  ↳ {text}")
        if pending_count > len(preview_items[:3]):
            lines.append(f"  ↳ ...and {pending_count - 3} more")
    return lines


def _running_shortcut_overlay_lines() -> list[str]:
    from .tui.bottom_pane.footer import shortcut_overlay_lines
    import shutil
    w = shutil.get_terminal_size((80, 24)).columns
    return ["  " + sl for sl in shortcut_overlay_lines(w)]


def _running_footer_status_lines(status_line=None) -> list[str]:
    if status_line is None:
        return []
    text = status_line.toolbar_text()
    if text and status_line.config.padding:
        text = (" " * status_line.config.padding) + text
    return [f"  {text}"] if text else []


def _running_composer_status_lines(running_state: dict) -> list[str]:
    lines = []
    lines.extend(_running_activity_lines(running_state))
    lines.extend(_running_pending_preview_lines(running_state))
    if running_state.get("show_shortcuts"):
        lines.extend(_running_shortcut_overlay_lines())
    return lines


def _running_status_lines(running_state: dict, status_line=None) -> list[str]:
    # Fallback/non-Application renderer: keep the same information in one
    # block. The live prompt_toolkit path splits this into Codex-like layers:
    # activity above the input field, project statusline below it.
    lines = _running_composer_status_lines(running_state)
    if running_state.get("show_shortcuts"):
        return lines
    lines.extend(_running_footer_status_lines(status_line))

    return lines


def _format_ansi_lines(lines: list[str]) -> list:
    result = []
    for line in lines:
        result.extend(_ansi_to_formatted_text(line))
        result.append(('', '\n'))
    if result:
        result.pop()
    return result


def _running_output_lines(output_lines: list[str], output_provider=None) -> list[str]:
    if output_provider is not None:
        provided = output_provider()
        if provided is None:
            return []
        return [str(line) for line in provided]
    return output_lines


def _append_running_activity_with_spacing(
    lines: list[str],
    activity_lines: list[str],
    *,
    force_leading_blank: bool = False,
) -> list[str]:
    if not activity_lines:
        return lines
    if lines and str(lines[-1]).strip():
        lines.append(_LIVE_SPACER_LINE)
    elif not lines and force_leading_blank:
        lines.append(_LIVE_SPACER_LINE)
    lines.extend(activity_lines)
    return lines


def _prepend_inline_scrollback_gap(
    lines: list[str],
    running_state: dict,
) -> list[str]:
    """Keep the live viewport separated from already-printed scrollback."""
    if (
        not lines
        or not running_state.get("inline_scrollback_needs_activity_gap")
        or not str(lines[0]).strip()
    ):
        return lines
    return [_LIVE_SPACER_LINE, *lines]


def _running_output_visible_line_budget() -> int:
    # In Life mode we want the full scrollback always visible, not a tail
    # trimmed to the terminal height. Return a value that is effectively
    # "unbounded" for any reasonable session history. We still expose this
    # helper because tests and other call sites depend on the symbol.
    return 1_000_000


def _print_reducer_new_lines(console, reducer: TraceEventReducer, printed_count: int) -> int:
    width = shutil.get_terminal_size((120, 24)).columns
    lines = reducer.display_lines(width)
    for line in lines[printed_count:]:
        console.print(line, markup=False)
    return len(lines)


def _has_streaming_output(running_state: dict) -> bool:
    return bool(
        running_state.get("deferred_scrollback_lines")
        or running_state.get("expanded_deferred_scrollback_lines")
        or running_state.get("expanded_deferred_reasoning_lines")
    )


def _set_buffer_text(buffer, text: str, cursor: int | None = None) -> None:
    cursor_pos = len(text) if cursor is None else max(0, min(cursor, len(text)))
    if Document is not None and hasattr(buffer, "set_document"):
        buffer.set_document(Document(text, cursor_position=cursor_pos), bypass_readonly=True)
        return
    buffer.text = text
    if hasattr(buffer, "cursor_position"):
        buffer.cursor_position = cursor_pos


def _sync_composer_from_prompt_buffer(composer: ChatComposer, buffer) -> None:
    composer.text = str(getattr(buffer, "text", "") or "")
    composer.cursor = int(getattr(buffer, "cursor_position", len(composer.text)) or 0)


def _route_prompt_buffer_key_to_composer(buffer, composer: ChatComposer, key: str) -> None:
    before_text = str(getattr(buffer, "text", "") or "")
    before_cursor = int(getattr(buffer, "cursor_position", len(before_text)) or 0)
    _sync_composer_from_prompt_buffer(composer, buffer)
    composer.handle_key_event(key)
    if composer.text != before_text or composer.cursor != before_cursor:
        _set_buffer_text(buffer, composer.text, composer.cursor)


def _attach_running_composer_callbacks(
    chat_widget: ChatWidget,
    queued_input: MessageQueue | None,
    *,
    on_change=None,
) -> ChatComposer:
    bottom_pane = chat_widget.bottom_pane
    composer = bottom_pane.composer

    def _queue_follow_up(text: str) -> None:
        if queued_input is not None:
            queued_input.enqueue(text)
        bottom_pane.pending_preview.add_pending_steer(text)
        chat_widget.request_redraw()
        if on_change:
            on_change()

    composer.set_on_submit(_queue_follow_up)
    composer.set_on_queue(_queue_follow_up)

    # Populate skill popup from skills_manager
    try:
        from .skills_manager import skills_manager
        if skills_manager.is_loaded:
            composer.skill_popup.set_skills([
                (s.name, s.description or "") for s in skills_manager.skills.values()
            ])
    except Exception:
        pass

    return composer


# Style for the fullscreen composer/transcript app. ``selected`` makes
# prompt_toolkit's mouse-drag selection in the input field visible
# (HighlightSelectionProcessor applies ``class:selected``).
_CHAT_COMPOSER_STYLE = {
    "composer-surface": "bg:#303030",
    "input-field": "bg:#303030 #ffffff",
    "input-prompt": "bg:#303030 bold #ffffff",
    "status": "#6c6c6c",
    "separator": "#444444",
    "completion-menu.completion": "bg:#202020 #d0d0d0",
    "completion-menu.completion.current": "bg:#0f5f87 #ffffff",
    "completion-menu.meta.completion": "bg:#202020 #8a8a8a",
    "completion-menu.meta.completion.current": "bg:#0f5f87 #ffffff",
    "notification": "bg:#0f5f87 #ffffff",
    "selected": "bg:#0f5f87 #ffffff",
}


def _create_chat_application(
    chat_state=None,
    status_line=None,
    *,
    running_state=None,
    output_provider=None,
    mouse_controller=None,
    fullscreen: bool = False,
    transcript_provider=None,
    transcript_regions_provider=None,
    transcript_full_provider=None,
    transcript_window_start_provider=None,
    on_scroll=None,
    on_scroll_edge=None,
    persistent: bool = False,
    on_submit=None,
):
    """Application for idle mode, with no active task.

    The HSplit layout places one scrollback/output area above a fixed bottom
    stack: separator, input field and statusline footer. Reasoning, shortcuts
    and Worked for lines are appended to scrollback rather than separate reserved
    panes, keeping the input/statusline anchor stable across active/idle changes.

    The ``enter`` handler stores text in ``running_state["submitted_text"]`` and
    returns it through ``Application.run()``. ``Ctrl+C`` returns
    ``"__INTERRUPT__"`` so the active loop cancels without an unhandled
    prompt_toolkit task exception. Reset the buffer on submit so the next prompt
    starts empty; this preserves Codex behavior and prevents the old PromptSession
    autofill regression after a short follow-up message.
    """
    if (
        Application is None or Buffer is None or KeyBindings is None
        or HSplit is None or Layout is None or Window is None
        or BufferControl is None or FormattedTextControl is None
        or Dimension is None or Style is None
        or FloatContainer is None or Float is None or CompletionsMenu is None
    ):
        return None, None, None

    if running_state is None:
        running_state = {}

    output_lines: list[str] = []
    from .cli_chat_idle import _make_idle_completer
    buffer = Buffer(
        multiline=True,
        completer=_make_idle_completer(),
        complete_while_typing=False,
    )
    style = Style.from_dict(_CHAT_COMPOSER_STYLE)
    kb = KeyBindings()
    _install_screenshot_hotkey_noops(kb)
    if mouse_controller is not None:
        mouse_controller.install_bindings(kb)

    def _get_active_form():
        """Return the active RequestUserInputForm or None."""
        entry = running_state.get("user_input_form")
        if isinstance(entry, dict):
            return entry.get("form")
        return None

    def _submit_current_buffer(_buf=None):
        text = _filter_control_chars(buffer.text).strip()
        if text:
            if mouse_controller is not None and text.startswith("/mouse"):
                message = mouse_controller.handle_command(text)
                if message:
                    mouse_controller.set_notification(
                        message.splitlines()[0]
                    )
                buffer.reset()
                return False
            if text.startswith("/tui"):
                running_state["local_tui_command"] = text
                buffer.reset()
                return False
            running_state["submitted_text"] = text
            buffer.reset()
        return False

    buffer.accept_handler = _submit_current_buffer

    @kb.add("enter")
    def _(event):
        form = _get_active_form()
        if form is not None:
            q = form.current_question
            if q and q.field_type in ("select", "multiselect"):
                form.handle_key("enter")
                event.app.invalidate()
            else:
                result = form.submit()
                if result:
                    from .cli_chat_user_input import serialize_user_input_response
                    running_state["user_input_form_result"] = {
                        "request_id": running_state.get("user_input_form", {}).get("request_id"),
                        "response": serialize_user_input_response(result),
                        "cancelled": False,
                    }
                    event.app.exit(result=result)
                else:
                    event.app.invalidate()
            return
        if persistent:
            text = _filter_control_chars(event.current_buffer.text).strip()
            if text:
                if mouse_controller is not None and text.startswith("/mouse"):
                    message = mouse_controller.handle_command(text)
                    if message:
                        mouse_controller.set_notification(message.splitlines()[0])
                elif text.startswith("/tui"):
                    event.app.exit(result="__tui__:" + text)
                    return
                elif on_submit is not None:
                    on_submit(text)
                event.current_buffer.reset()
                event.app.invalidate()
            return
        event.current_buffer.validate_and_handle()
        if running_state.get("submitted_text"):
            event.app.exit(result=running_state["submitted_text"])
        else:
            invalidate = getattr(event.app, "invalidate", None)
            if callable(invalidate):
                invalidate()

    @kb.add("c-j")
    def _(event):
        if _get_active_form() is not None:
            return
        event.current_buffer.insert_text("\n", fire_event=False)

    @kb.add("tab")
    def _(event):
        if _get_active_form() is not None:
            _get_active_form().handle_key("tab")
            event.app.invalidate()
            return
        # Tab: apply completion (file from @, command from /, skill from $)
        from .cli_chat_idle import _completion_to_apply
        completion = _completion_to_apply(event.current_buffer)
        if completion is not None:
            event.current_buffer.apply_completion(completion)

    @kb.add("s-tab")
    def _(event):
        form = _get_active_form()
        if form is not None:
            form.handle_key("s-tab")
            event.app.invalidate()

    @kb.add("up")
    def _(event):
        form = _get_active_form()
        if form is not None:
            form.handle_key("up")
            event.app.invalidate()

    @kb.add("down")
    def _(event):
        form = _get_active_form()
        if form is not None:
            form.handle_key("down")
            event.app.invalidate()

    if fullscreen:
        @kb.add("pageup")
        def _(event):
            if _get_active_form() is None and on_scroll is not None:
                on_scroll(5)
                event.app.invalidate()

        @kb.add("pagedown")
        def _(event):
            if _get_active_form() is None and on_scroll is not None:
                on_scroll(-5)
                event.app.invalidate()

        @kb.add("c-home")
        def _(event):
            if _get_active_form() is None and on_scroll_edge is not None:
                on_scroll_edge("top")
                event.app.invalidate()

        @kb.add("c-end")
        def _(event):
            if _get_active_form() is None and on_scroll_edge is not None:
                on_scroll_edge("bottom")
                event.app.invalidate()

    @kb.add("space")
    def _(event):
        form = _get_active_form()
        if form is not None:
            q = form.current_question
            if q and q.field_type in ("select", "multiselect"):
                form.handle_key(" ")
                event.app.invalidate()
                return
        event.current_buffer.insert_text(" ", fire_event=False)

    @kb.add("c-s")
    def _(event):
        form = _get_active_form()
        if form is not None:
            result = form.submit()
            if result:
                from .cli_chat_user_input import serialize_user_input_response
                running_state["user_input_form_result"] = {
                    "request_id": running_state.get("user_input_form", {}).get("request_id"),
                    "response": serialize_user_input_response(result),
                    "cancelled": False,
                }
                event.app.exit(result=result)
            else:
                event.app.invalidate()

    @kb.add("?")
    def _(event):
        if _get_active_form() is not None:
            return
        if not event.current_buffer.text.strip():
            running_state["show_shortcuts"] = not running_state.get("show_shortcuts", False)
            event.app.invalidate()
        else:
            event.current_buffer.insert_text("?", fire_event=False)

    def _insert_completion_trigger(event, text: str) -> None:
        if _get_active_form() is not None:
            return
        if not event.current_buffer.text.strip():
            event.current_buffer.insert_text(text, fire_event=False)
            event.current_buffer.start_completion(select_first=False)
        else:
            event.current_buffer.insert_text(text, fire_event=False)

    @kb.add("/")
    def _(event):
        _insert_completion_trigger(event, "/")

    @kb.add("@")
    def _(event):
        _insert_completion_trigger(event, "@")

    @kb.add("$")
    def _(event):
        _insert_completion_trigger(event, "$")

    @kb.add("c-t")
    def _(event):
        if _get_active_form() is not None:
            return
        event.current_buffer.text = "__transcript__"
        event.current_buffer.validate_and_handle()
        if running_state.get("submitted_text"):
            event.app.exit(result=running_state["submitted_text"])

    @kb.add("c-o")
    def _(event):
        if _get_active_form() is not None:
            return
        rs = running_state.get("reasoning_state")
        if not isinstance(rs, dict):
            return
        has_reasoning = bool(rs.get("lines") or rs.get("expanded_lines"))
        # In fullscreen the committed transcript may hold reasoning cells even
        # when the session-level cache is empty, so allow toggling regardless.
        if has_reasoning or fullscreen:
            rs["visible"] = not rs.get("visible", False)
            # Ctrl+O is the global toggle: drop per-block overrides so every
            # reasoning block follows the new global state.
            rs["cell_overrides"] = {}
            event.app.invalidate()

    @kb.add("c-v")
    def _(event):
        if _get_active_form() is not None:
            return
        # Ctrl+V image paste — mirrors Codex TUI fixed.paste_image.
        from .cli_chat_idle import _is_first_press
        from .cli_chat_rich_shared import _clipboard_image_path, _clipboard_text
        if not _is_first_press(event):
            return
        path = _clipboard_image_path()
        if path:
            buf = event.current_buffer
            if buf.text and not buf.text.endswith((" ", "\n", "\t")):
                buf.insert_text(" ")
            buf.insert_text(f"📎 {path} ")
            event.app.invalidate()
            return
        # No image on the clipboard: fall back to pasting text.
        text = _clipboard_text()
        if text:
            event.current_buffer.insert_text(text, fire_event=False)
            event.app.invalidate()

    @kb.add("escape")
    def _(event):
        form = _get_active_form()
        if form is not None:
            form.cancel()
            running_state["user_input_form_result"] = {
                "request_id": running_state.get("user_input_form", {}).get("request_id"),
                "response": None,
                "cancelled": True,
            }
            event.app.exit(result=None)
            return
        if running_state.get("escape_interrupts") and running_state.get("task_active"):
            if persistent:
                # Persistent fullscreen: cancel the running task in place,
                # keep the session/app alive.
                running_state["interrupted"] = True
                # Stop the "Working" spinner right away; the actual task
                # cancellation is cooperative (between model rounds), so the
                # turn may take a while to unwind.
                running_state["interrupting"] = True
                task_id = str(running_state.get("current_task_id") or "")
                if task_id:
                    try:
                        cancel_task(task_id)
                    except Exception:
                        pass
                else:
                    # Esc pressed before the task row exists: remember it and
                    # cancel the task as soon as it is created.
                    running_state["interrupt_pending"] = True
                event.app.invalidate()
            else:
                event.app.exit(result="__INTERRUPT__")
            return
        if running_state.get("show_shortcuts"):
            running_state["show_shortcuts"] = False
            event.app.invalidate()

    @kb.add("c-c")
    def _(event):
        form = _get_active_form()
        if form is not None:
            form.cancel()
            running_state["user_input_form_result"] = {
                "request_id": running_state.get("user_input_form", {}).get("request_id"),
                "response": None,
                "cancelled": True,
            }
            event.app.exit(result=None)
            return
        running_state["interrupted"] = True
        event.app.exit(result="__INTERRUPT__")

    def _output_lines():
        if fullscreen and transcript_provider is not None:
            lines = list(transcript_provider())
            lines.extend(_running_pending_preview_lines(running_state))
            if mouse_controller is not None and mouse_controller.selection is not None:
                from .tui.mouse_frame import highlight_lines

                lines = highlight_lines(lines, mouse_controller.selection)
            return lines
        if output_provider is not None:
            provided = output_provider()
            if provided is None:
                lines = []
            else:
                lines = list(provided)
        else:
            lines = output_lines[-200:]
        lines.extend(_running_pending_preview_lines(running_state))
        lines.extend(_reasoning_lines())
        lines.extend(_worked_for_lines())
        if running_state.get("show_shortcuts"):
            lines.extend(_shortcut_overlay_lines())
        return lines

    def _working_lines() -> list[str]:
        if not running_state.get("task_active"):
            return []
        # The DB task is already final (finished or interrupted) but the turn is
        # still finalizing: show nothing instead of a spinning "Working".
        if running_state.get("task_finished"):
            return []
        elapsed = time.monotonic() - running_state.get("t0", time.monotonic())
        # Animate spinner frame every ~0.1s
        idx = running_state.get("spinner_idx", 0)
        last_ts = running_state.get("spinner_last_ts", 0)
        now = time.monotonic()
        if now - last_ts >= 0.1:
            idx = (idx + 1) % len(_SPINNER_CHARS)
            running_state["spinner_idx"] = idx
            running_state["spinner_last_ts"] = now
        if running_state.get("interrupting"):
            lines = [
                f"  ⏹ Прерывание… ({_running_elapsed_label(elapsed)})"
            ]
        else:
            ch = _SPINNER_CHARS[idx]
            lines = [
                f"  {ch} Working ({_running_elapsed_label(elapsed)} · esc to interrupt)"
            ]
        lines.extend(_queued_input_lines(running_state))
        return lines

    def _output_text():
        lines = _output_lines()
        return _format_ansi_lines(lines) if lines else []

    def _output_height():
        if fullscreen:
            return Dimension(weight=1)
        lines = _output_lines()
        extra_reserved_rows = _working_activity_reserved_rows(running_state)
        return _idle_output_height(lines, buffer, extra_reserved_rows=extra_reserved_rows)

    def _working_text():
        lines = _working_lines()
        if not lines:
            return []
        return _format_ansi_lines([_LIVE_SPACER_LINE, *lines])

    def _reasoning_lines() -> list[str]:
        rs = running_state.get("reasoning_state")
        if not rs:
            return []
        key = "expanded_lines" if rs.get("visible") else "lines"
        lines = list(rs.get(key) or [])
        if (
            not lines
            and not rs.get("visible")
            and rs.get("expanded_lines")
            and not rs.get("frozen_scrollback")
        ):
            return ["  \033[2m• Размышления модели доступны · Ctrl+O раскрыть\033[0m"]
        return lines

    def _worked_for_lines() -> list[str]:
        worked_for = str(running_state.get("last_worked_for") or "").strip()
        return [f"  {worked_for}"] if worked_for else []

    def _shortcut_overlay_lines() -> list[str]:
        from .tui.bottom_pane.footer import shortcut_overlay_lines
        import shutil
        w = shutil.get_terminal_size((80, 24)).columns
        return ["  " + sl for sl in shortcut_overlay_lines(w)]

    def _footer_text():
        notice = (
            mouse_controller.current_notification()
            if mouse_controller is not None else None
        )
        result = None
        if status_line is None:
            result = [('', '  Enter — send  Ctrl+J — newline  Ctrl+C — exit')]
        else:
            t = status_line.toolbar_text()
            if t and status_line.config.padding:
                t = (" " * status_line.config.padding) + t
            if t:
                if "\033[" not in t:
                    last_sep = t.rfind(" · ")
                    if last_sep > 0:
                        suffix = t[last_sep + 3:]
                        if suffix.startswith("~") or suffix.startswith("/"):
                            t = t[:last_sep] + "\n" + suffix
                built = []
                for line in t.splitlines():
                    built.extend(_ansi_to_formatted_text(f"  {line}"))
                    built.append(('', '\n'))
                if built:
                    built.pop()
                    result = built
        if result is None:
            result = [('', '  Enter — send  Ctrl+J — newline  Ctrl+C — exit')]
        if notice:
            result = list(result) + [('', '\n'), ('class:status', f"  {notice}")]
        return result

    def _composer_input_processors():
        processors = [_InputFieldProcessor()]
        if BeforeInput is not None:
            processors.append(BeforeInput("› ", style="class:input-prompt"))
        return processors

    root_body = HSplit([
        ConditionalContainer(
            Window(
                FormattedTextControl(_output_text),
                wrap_lines=(not fullscreen),
                height=_output_height,
            ),
            filter=Condition(
                lambda: True if fullscreen else bool(_output_text())
            ),
        ),
        ConditionalContainer(
            Window(
                FormattedTextControl(_working_text),
                # Spacer line + spinner + one line per queued follow-up.
                height=lambda: 1 + len(_working_lines()),
                style="class:status",
            ),
            filter=Condition(lambda: _working_activity_should_show(running_state)),
        ),
        Window(
            FormattedTextControl(
                lambda: "  " + "─" * max(8, shutil.get_terminal_size((120, 24)).columns - 4)
            ),
            height=1,
            style="class:separator",
        ),
        HSplit(
            [
                Window(
                    FormattedTextControl(lambda: ""),
                    height=1,
                    style="class:composer-surface",
                ),
                Window(
                    BufferControl(
                        buffer=buffer,
                        input_processors=_composer_input_processors(),
                    ),
                    wrap_lines=True,
                    height=lambda: _chat_textarea_height(buffer),
                    style="class:input-field",
                ),
                Window(
                    FormattedTextControl(lambda: ""),
                    height=1,
                    style="class:composer-surface",
                ),
            ],
            style="class:composer-surface",
        ),
        Window(
            FormattedTextControl(_footer_text),
            height=1,
            style="class:status",
        ),
    ])
    notification_float = ConditionalContainer(
        Window(
            FormattedTextControl(
                lambda: [
                    (
                        "class:notification",
                        " "
                        + (
                            (mouse_controller.current_notification() if mouse_controller else None)
                            or ""
                        )
                        + " ",
                    )
                ]
            ),
            height=1,
            width=Dimension(min=10),
            style="class:notification",
        ),
        filter=Condition(
            lambda: mouse_controller is not None
            and bool(mouse_controller.current_notification())
        ),
    )
    root = FloatContainer(
        content=root_body,
        floats=[
            Float(
                xcursor=True,
                ycursor=True,
                content=CompletionsMenu(max_height=12, scroll_offset=1),
            ),
            Float(
                bottom=3,
                right=2,
                content=notification_float,
            ),
        ],
    )
    mouse_support_filter = False
    app_output = None
    if mouse_controller is not None:
        mouse_support_filter = mouse_controller.mouse_support_filter()
        if create_output is not None:
            try:
                app_output = mouse_controller.wrap_output(create_output())
            except Exception:
                app_output = None
    app = Application(
        layout=Layout(root),
        key_bindings=kb,
        style=style,
        output=app_output,
        full_screen=fullscreen,
        mouse_support=mouse_support_filter,
        erase_when_done=True,
        refresh_interval=0.1,
    )
    if mouse_controller is not None:
        from .tui.mouse_frame import FrameRegion, FrameRow, ScreenFrame

        mouse_controller.set_app(app)

        def _mouse_frame():
            if _get_active_form() is not None:
                # Stage 6 (approval/forms) is out of scope: no coordinate actions.
                return None
            try:
                base = app.renderer.rows_above_layout
            except Exception:
                return None
            if fullscreen:
                import shutil as _shutil

                lines = _output_lines()
                # Fullscreen owns the whole screen: the transcript starts at
                # absolute row 0. Do not use rows_above_layout here.
                base = 0
                rows = [
                    FrameRow(base + i, line, region_id="output")
                    for i, line in enumerate(lines)
                ]
                regions = {}
                if rows:
                    regions["output"] = FrameRegion(
                        region_id="output",
                        kind="output",
                        top=base,
                        bottom=base + len(lines) - 1,
                    )
                # Structural spans (reasoning/tool) mapped to visible rows
                # by the session become interactive zones.
                spans = []
                if transcript_regions_provider is not None:
                    try:
                        spans = list(transcript_regions_provider() or [])
                    except Exception:
                        spans = []
                for span in spans:
                    s = int(span.get("start", 0))
                    e = int(span.get("end", -1))
                    if e < s or e < 0:
                        continue
                    top = base + s
                    bottom = base + e
                    rid = f"span:{s}:{span.get('kind')}"
                    regions[rid] = FrameRegion(
                        region_id=rid,
                        kind=str(span.get("kind", "output")),
                        cell_id=str(span.get("cell_id", "")),
                        top=top,
                        bottom=bottom,
                        expandable=bool(span.get("expandable", False)),
                        header_row=top,
                        tail_row=bottom,
                    )
                window_h = max(
                    len(lines),
                    _shutil.get_terminal_size((80, 24)).lines - 8,
                )
                cursor = base + window_h
                if _working_activity_should_show(running_state):
                    # Match the working Window height: spacer + spinner +
                    # queued follow-up lines.
                    cursor += 1 + len(_working_lines())
                composer_top = cursor + 1
                window_start = 0
                if transcript_window_start_provider is not None:
                    try:
                        window_start = int(transcript_window_start_provider() or 0)
                    except Exception:
                        window_start = 0
                full_lines: list[str] = []
                if transcript_full_provider is not None:
                    try:
                        full_lines = list(transcript_full_provider() or [])
                    except Exception:
                        full_lines = []
                return ScreenFrame(
                    rows=rows,
                    regions=regions,
                    composer_top=composer_top,
                    window_start=window_start,
                    full_lines=full_lines,
                )
            if output_provider is not None:
                provided = list(output_provider() or [])
            else:
                provided = list(output_lines[-200:])
            pending = _running_pending_preview_lines(running_state)
            reasoning = _reasoning_lines()
            worked = _worked_for_lines()
            segments = [
                ("output", provided),
                ("output", pending),
                ("reasoning", reasoning),
                ("output", worked),
            ]
            rows = []
            regions = {}
            cursor = base
            for kind, seg_lines in segments:
                if not seg_lines:
                    continue
                if kind == "reasoning":
                    regions["reasoning"] = FrameRegion(
                        region_id="reasoning",
                        kind="reasoning",
                        top=cursor,
                        bottom=cursor + len(seg_lines) - 1,
                        header_row=cursor,
                    )
                for line in seg_lines:
                    rows.append(FrameRow(cursor, line, region_id=kind))
                    cursor += 1
            if rows:
                regions.setdefault(
                    "output",
                    FrameRegion(
                        region_id="output", kind="output", top=base, bottom=base
                    ),
                )
                regions["output"].top = base
                regions["output"].bottom = cursor - 1
            if _working_activity_should_show(running_state):
                cursor += 2
            composer_top = cursor + 1
            return ScreenFrame(
                rows=rows, regions=regions, composer_top=composer_top
            )

        mouse_controller.set_frame_builder(_mouse_frame)
    return app, buffer, output_lines


def _create_running_application(
    status_line=None,
    *,
    running_state: Optional[dict] = None,
    output_provider=None,
    composer=None,
):
    """Backward-compatible alias for ``_create_chat_application``.

    The legacy ``_create_running_application`` factory was collapsed into
    the unified ``_create_chat_application`` factory at the start of
    Phase 4. Callers in ``cli_chat_rich.py`` (and the legacy Rich
    path) still import the old name; this thin shim keeps the
    signature stable and forwards to the new implementation.
    """
    chat_state = dict(running_state or {})
    chat_state.setdefault("phase", "running")
    app, buffer, _output_lines = _create_chat_application(
        chat_state=chat_state,
        status_line=status_line,
        output_provider=output_provider,
        running_state=running_state,
    )
    # Legacy callers expect a 3-tuple ``(app, input_buffer, output_lines)``;
    # surface the current ``output_lines`` from chat_state for parity.
    output_lines = list(chat_state.get("output_lines", []))
    return app, buffer, output_lines


def _create_idle_application(
    status_line=None,
    *,
    running_state: Optional[dict] = None,
):
    """Backward-compatible alias for ``_create_chat_application`` (phase=input).

    Legacy callers in ``cli_chat_rich.py`` still use the old name.
    This thin shim forwards to the unified implementation.
    """
    chat_state = dict(running_state or {})
    chat_state.setdefault("phase", "input")
    app, buffer, _output_lines = _create_chat_application(
        chat_state=chat_state,
        status_line=status_line,
        running_state=running_state,
    )
    output_lines = list(chat_state.get("output_lines", []))
    return app, buffer, output_lines


def _chat_widget_frame_lines(widget: ChatWidget, *, width: int, height: int = 24) -> list[str]:
    buf: list[str] = []
    widget.render(Rect(0, 0, width, height), buf)
    return buf


class _InlineScrollbackProjector:
    """Minimal stub for the inline scrollback projector.

    Tracks which chat-widget lines have already been written to the
    terminal scrollback so that subsequent frames can return only the
    new ones. The legacy class was removed during the chat_render
    consolidation; this stub preserves the call-site API so the
    Rich/Textual chat loop keeps working while the full one-to-one port
    lands.
    """

    def __init__(self, chat_widget: Optional[ChatWidget] = None) -> None:
        self._chat_widget = chat_widget
        self._last_committed_frame: list[str] = []
        self._deferred_collapsed: list[str] = []
        self._deferred_expanded: list[str] = []
        self._deferred_reasoning: list[str] = []

    def _committed_frame(
        self,
        widget: Optional[ChatWidget] = None,
        width: int = 80,
        *,
        expand_reasoning: bool = False,
    ) -> list[str]:
        w = widget or self._chat_widget
        if w is None:
            return []
        try:
            history = w._HistoryArea(
                w.committed_cells,
                None,
                show_reasoning=expand_reasoning,
            )
            return history.display_lines(width)
        except Exception:
            return []

    def _active_frame(
        self,
        widget: Optional[ChatWidget] = None,
        width: int = 80,
        *,
        expand_reasoning: bool = False,
    ) -> list[str]:
        w = widget or self._chat_widget
        if w is None:
            return []
        try:
            history = w._HistoryArea(
                [],
                w.active_cell,
                show_reasoning=expand_reasoning,
            )
            return history.display_lines(width)
        except Exception:
            return []

    def take_new_lines(self, chat_widget: ChatWidget, width: int) -> list[str]:
        """Return lines that have not yet been emitted to scrollback.

        Compares the current frame with the last seen frame by position
        instead of deduplicating by string identity. This preserves
        repeated blank-line separators (multiple ``""`` at different
        positions) that the earlier set-based dedup collapsed.
        """
        lines = self._committed_frame(chat_widget, width)
        if lines == self._last_committed_frame:
            return []
        start = 0
        min_len = min(len(lines), len(self._last_committed_frame))
        while start < min_len and lines[start] == self._last_committed_frame[start]:
            start += 1
        new = lines[start:]
        self._last_committed_frame = list(lines)
        return new

    def viewport_lines(self, chat_widget: ChatWidget, width: int, height: int = 24, **kwargs) -> list[str]:
        """Return the current viewport (committed + active cell) lines.

        Accepts and ignores extra keyword arguments (e.g. ``expand_reasoning``)
        so the call site is forward-compatible with the full one-to-one port.
        """
        return self._active_frame(
            chat_widget,
            width,
            expand_reasoning=bool(kwargs.get("expand_reasoning")),
        )

    def deferred_lines(self, chat_widget: ChatWidget, width: int, expand_reasoning: bool = False) -> list[str]:
        """Return lines that should be flushed to scrollback on exit."""
        if expand_reasoning:
            if not self._deferred_expanded:
                self._deferred_expanded = (
                    self._committed_frame(
                        chat_widget,
                        width,
                        expand_reasoning=True,
                    )
                    + self._active_frame(
                        chat_widget,
                        width,
                        expand_reasoning=True,
                    )
                )
            return list(self._deferred_expanded)
        if not self._deferred_collapsed:
            self._deferred_collapsed = (
                self._committed_frame(
                    chat_widget,
                    width,
                )
                + self._active_frame(
                    chat_widget,
                    width,
                )
            )
        return list(self._deferred_collapsed)

    def expanded_reasoning_lines(self, chat_widget: ChatWidget, width: int) -> list[str]:
        """Returns expanded reasoning blocks without regular history cells."""
        if self._deferred_reasoning:
            return list(self._deferred_reasoning)
        lines: list[str] = []
        for cell in chat_widget.committed_cells:
            if not isinstance(cell, ReasoningCell):
                continue
            if lines and lines[-1] != "":
                lines.append("")
            lines.extend(cell.expanded_display_lines(width))
        if isinstance(chat_widget.active_cell, ReasoningCell):
            if lines and lines[-1] != "":
                lines.append("")
            lines.extend(chat_widget.active_cell.expanded_display_lines(width))
        self._deferred_reasoning = lines
        return list(lines)


def _sync_chat_composer_from_buffer(widget: ChatWidget, text: str) -> None:
    pane = widget.bottom_pane
    composer = getattr(pane, "composer", None)
    if composer is not None and getattr(composer, "text", None) != text:
        composer.set_text(text)


def _clear_running_preview(widget: ChatWidget, running_state: dict) -> None:
    """Clears the running-turn preview without affecting the real message queue."""
    pane = widget.bottom_pane
    pending_preview = getattr(pane, "pending_preview", None)
    if pending_preview is not None:
        pending_preview.clear_pending_steers()
        pending_preview.clear_rejected_steers()
        pending_preview.clear_queued()
    running_state["pending_count"] = 0
    running_state["preview_items"] = []


def _should_fallback_render_trace(running_state: dict) -> bool:
    return bool(
        running_state.get("app_was_used")
        and not running_state.get("task_finish_applied")
    )


def _should_print_app_transcript(running_state: dict) -> bool:
    return bool(
        running_state.get("app_was_used")
        and running_state.get("task_finish_applied")
        and running_state.get("final_trace_lines")
        and not running_state.get("inline_scrollback_used")
        and not running_state.get("has_deferred_scrollback")
    )




def _toggle_reasoning_visibility(running_state: dict) -> bool:
    visible = not running_state.get("show_reasoning", False)
    running_state["show_reasoning"] = visible
    return visible


def _ask_task_with_live_trace(
    console, text, profile, interval_s, timeout_s, metadata,
    status_line=None, queued_input=None, queued_prompt_session=None,
    input_files=None, shown_edit_diff_signatures=None,
):
    """Agent turn: prompt_toolkit Application plus reducer-driven trace rendering."""
    created = create_task(
        text=text,
        profile=profile,
        metadata=metadata,
        input_files=list(input_files or []),
    )
    task_id = str(created["task_id"])
    trace_path = _trace_path_for_task(task_id)
    final_states = {"ok","fail","dead_letter"}
    deadline = time.time() + timeout_s
    turn_started = time.monotonic()
    if status_line is not None: status_line.refresh()

    trace_queue = queue.Queue()
    result_holder = [None]
    error_holder = [None]
    done_event = threading.Event()
    queued_notice_flag = [False]
    running_state = {
        "t0": turn_started,
        "pending_count": 0,
        "preview_items": [],
        "interrupted": False,
        "task_active": True,
        "user_input_form": None,
        "shown_edit_diff_signatures": shown_edit_diff_signatures,
    }

    def _poll():
        seen = 0
        next_notice = time.monotonic() + 8
        try:
            while not done_event.is_set():
                evs, seen = _read_new_trace_events(trace_path, seen)
                if evs: trace_queue.put(evs)
                task = get_task(task_id)
                if task is None:
                    error_holder[0] = RuntimeError(f"Task not found: {task_id}")
                    done_event.set(); return
                if task.get("status")=="queued" and time.monotonic()>=next_notice:
                    queued_notice_flag[0] = True
                    next_notice = time.monotonic() + 15
                if not running_state.get("user_input_form"):
                    from .cli_chat_user_input import user_input_request_from_payload
                    from .user_input_store import list_pending_user_input_requests

                    pending_requests = list_pending_user_input_requests(task_id=task_id)
                    if pending_requests:
                        pending = pending_requests[0]
                        running_state["user_input_form"] = {
                            "request_id": pending["request_id"],
                            "form": RequestUserInputForm(
                                user_input_request_from_payload(pending["payload"])
                            ),
                        }
                if task.get("status") in final_states:
                    # Grace period: keep reading until 1s passes with no new events
                    grace_deadline = time.monotonic() + 1.0
                    while time.monotonic() < grace_deadline:
                        evs, seen = _read_new_trace_events(trace_path, seen)
                        if evs:
                            trace_queue.put(evs)
                            grace_deadline = time.monotonic() + 1.0
                        else:
                            time.sleep(0.1)
                    result_holder[0] = task
                    done_event.set(); return
                if time.time() > deadline:
                    error_holder[0] = TimeoutError(f"Timeout waiting for task {task_id}")
                    done_event.set(); return
                done_event.wait(timeout=interval_s)
        except Exception as exc:
            error_holder[0] = exc; done_event.set()

    poll_thread = threading.Thread(target=_poll, daemon=True)
    poll_thread.start()

    async def _agent_loop():
        term_size = shutil.get_terminal_size((120, 24))
        chat_widget = ChatWidget(
            width=term_size.columns,
            model=profile_model_name(get_profile_config(profile)),
            branch=_branch(),
            cwd=str(Path.cwd()),
        )
        bottom_pane = BottomPane()
        status_widget = StatusIndicatorWidget()
        status_widget.t0 = turn_started
        bottom_pane.set_status(status_widget)
        bottom_pane.update_task_running(True)
        chat_widget.set_bottom_pane(bottom_pane)
        frame_cache: list[str] = []
        reducer = TraceEventReducer(
            widget=chat_widget,
            shown_edit_diff_signatures=running_state.get("shown_edit_diff_signatures"),
        )
        scrollback = _InlineScrollbackProjector(chat_widget=chat_widget)

        async def _freeze_deferred_scrollback_for_exit() -> bool:
            if run_in_terminal is None:
                return False
            lines = list(running_state.get("deferred_scrollback_lines") or [])
            if not lines:
                return False

            def _print_lines() -> None:
                terminal_render_log(
                    "active.deferred_scrollback.freeze",
                    line_count=len(lines),
                    lines=lines,
                )
                for line in lines:
                    console.print(line, markup=False)

            await run_in_terminal(_print_lines)
            running_state["deferred_scrollback_lines"] = []
            running_state["has_deferred_scrollback"] = False
            running_state["deferred_scrollback_frozen"] = True
            running_state["inline_scrollback_used"] = True
            return True

        async def _insert_stable_scrollback() -> bool:
            if run_in_terminal is None:
                return False
            width = shutil.get_terminal_size((120, 24)).columns
            lines = scrollback.take_new_lines(chat_widget, width)
            if not lines:
                return False

            def _print_lines() -> None:
                terminal_render_log(
                    "active.stable_scrollback.print",
                    line_count=len(lines),
                    lines=lines,
                )
                for line in lines:
                    console.print(line, markup=False)

            await run_in_terminal(_print_lines)
            running_state["inline_scrollback_used"] = True
            if any(str(line).strip() for line in lines):
                running_state["inline_scrollback_needs_activity_gap"] = True
            return True

        def _current_chat_widget_frame() -> list[str]:
            size = shutil.get_terminal_size((120, 24))
            lines = scrollback.viewport_lines(
                chat_widget,
                width=size.columns,
                expand_reasoning=bool(running_state.get("show_reasoning")),
            )
            # Add popup lines from composer if skill popup is active
            composer = bottom_pane.composer
            if composer is not None and composer.active_popup == ActivePopup.SKILL:
                popup_lines = composer.skill_popup.render_lines(size.columns)
                lines = popup_lines + lines
            # Add form overlay when request_user_input is active
            _form_entry = running_state.get("user_input_form")
            if isinstance(_form_entry, dict):
                _form = _form_entry.get("form")
                if _form is not None:
                    _form.set_width(size.columns)
                    lines = _form.render_lines() + lines
            lines = _prepend_inline_scrollback_gap(lines, running_state)
            # Life mode must always show the full scrollback, from the very
            # first line to the last, regardless of how many tool calls and
            # reasoning blocks came before. We do NOT trim to a tail budget
            # here — that is what used to push old reasoning out of the
            # active Life window.
            frame_cache[:] = lines
            return frame_cache

        running_state["status_in_bottom_pane"] = False  # status renders in the prompt_toolkit status area
        composer = _attach_running_composer_callbacks(chat_widget, queued_input)
        app, input_buffer, output_lines = _create_running_application(
            status_line,
            running_state=running_state,
            output_provider=_current_chat_widget_frame,
            composer=composer,
        )
        if app is None:
            printed_count = 0
            while not done_event.is_set():
                active_user_input = running_state.get("user_input_form")
                if isinstance(active_user_input, dict):
                    from .cli_chat_user_input import (
                        _ask_user_input,
                        serialize_user_input_response,
                    )
                    from .user_input_store import resolve_user_input_request

                    response = _ask_user_input(console, active_user_input["form"].request)
                    resolve_user_input_request(
                        str(active_user_input["request_id"]),
                        response=(
                            None
                            if response is None
                            else serialize_user_input_response(response)
                        ),
                        cancelled=response is None,
                    )
                    running_state["user_input_form"] = None
                while not trace_queue.empty():
                    try: evs = trace_queue.get_nowait()
                    except queue.Empty: break
                    reducer.apply_many(evs)
                    printed_count = _print_reducer_new_lines(console, reducer, printed_count)
                await asyncio.sleep(0.1)
            while not trace_queue.empty():
                try: evs = trace_queue.get_nowait()
                except queue.Empty: break
                reducer.apply_many(evs)
                printed_count = _print_reducer_new_lines(console, reducer, printed_count)
            running_state["trace_printing_complete"] = True
            return

        running_state["app_was_used"] = True

        last_input_text = ""
        active_user_input_id = ""
        while not done_event.is_set():
            app_task = asyncio.create_task(app.run_async())
            try:
                while not done_event.is_set() and not app_task.done():
                    changed = False
                    if input_buffer is not None:
                        input_text = str(getattr(input_buffer, "text", "") or "")
                        active_user_input = running_state.get("user_input_form")
                        current_user_input_id = (
                            str(active_user_input.get("request_id") or "")
                            if isinstance(active_user_input, dict)
                            else ""
                        )
                        if current_user_input_id != active_user_input_id:
                            active_user_input_id = current_user_input_id
                            input_buffer.reset()
                            input_text = ""
                            changed = True
                            # Exit the Application so the outer loop can
                            # handle the form via _ask_user_input
                            app.exit(result="__form__")
                        if isinstance(active_user_input, dict):
                            form = active_user_input["form"]
                            question = form.current_question
                            answer = form.current_answer
                            if (
                                question
                                and question.field_type in {"text", "secret"}
                                and answer
                                and answer.text_value != input_text
                            ):
                                answer.text_value = input_text
                                changed = True
                        elif input_text != last_input_text:
                            _sync_chat_composer_from_buffer(chat_widget, input_text)
                            last_input_text = input_text
                            changed = True
                    if queued_input is not None:
                        pending_count = queued_input.pending_count
                        preview_items = (
                            list(bottom_pane.pending_preview.pending_steers)
                            + list(bottom_pane.pending_preview.queued_messages)
                        )
                        if (
                            running_state.get("pending_count") != pending_count
                            or running_state.get("preview_items") != preview_items
                        ):
                            running_state["pending_count"] = pending_count
                            running_state["preview_items"] = preview_items
                            changed = True
                    while not trace_queue.empty():
                        try: evs = trace_queue.get_nowait()
                        except queue.Empty: break
                        reducer.apply_many(evs)
                        if reducer.consume_dirty():
                            changed = True
                        if reducer._was_streaming or reducer._is_streaming:
                            running_state["had_streaming_output"] = True

                    if queued_notice_flag[0]:
                        chat_widget.add_status_cell(_QUEUED_TASK_NOTICE)
                        queued_notice_flag[0] = False
                        changed = True

                    if await _insert_stable_scrollback():
                        changed = True

                    submitted = str(running_state.pop("submitted_text", "") or "").strip()
                    if submitted:
                        if queued_input is not None:
                            queued_input.enqueue(submitted)
                            running_state["pending_count"] = queued_input.pending_count
                        _sync_chat_composer_from_buffer(chat_widget, "")
                        last_input_text = ""
                        changed = True

                    if changed:
                        app.invalidate()
                    await asyncio.sleep(0.05)

                # When the Application exits, check if it's because of a form
                active_form_entry = running_state.get("user_input_form")
                if isinstance(active_form_entry, dict) and not done_event.is_set():
                    from .user_input_store import resolve_user_input_request
                    # Check if the Application already handled the form
                    # (Enter/Ctrl+S/Esc in the running Application set
                    #  user_input_form_result before calling app.exit).
                    existing_result = running_state.pop("user_input_form_result", None)
                    if existing_result is not None:
                        resolve_user_input_request(
                            str(active_form_entry["request_id"]),
                            response=existing_result.get("response"),
                            cancelled=existing_result.get("cancelled", False),
                        )
                    else:
                        from .cli_chat_user_input import _ask_user_input, serialize_user_input_response
                        form = active_form_entry["form"]
                        response = _ask_user_input(console, form.request)
                        resolve_user_input_request(
                            str(active_form_entry["request_id"]),
                            response=(
                                None if response is None
                                else serialize_user_input_response(response)
                            ),
                            cancelled=response is None,
                        )
                    running_state["user_input_form"] = None
                    # Reset buffer and continue — agent task is still running
                    if input_buffer is not None:
                        input_buffer.reset()
                    last_input_text = ""
                    active_user_input_id = ""
            finally:
                if not app_task.done():
                    app_task.cancel()
                    try:
                        await app_task
                    except (asyncio.CancelledError, Exception):
                        pass

            # If task is done, break out — proceed to post-loop cleanup
            if done_event.is_set():
                break
            # Otherwise the Application exited because of form submit/cancel —
            # recreate it and continue the outer loop so the next form can appear.
            app, input_buffer, _output_lines = _create_running_application(
                status_line,
                running_state=running_state,
                output_provider=_current_chat_widget_frame,
                composer=composer,
            )

        # Drain remaining trace events to ensure complete text display
        while not trace_queue.empty():
            try: evs = trace_queue.get_nowait()
            except queue.Empty: break
            reducer.apply_many(evs)
            if reducer._was_streaming or reducer._is_streaming:
                running_state["had_streaming_output"] = True
        await _insert_stable_scrollback()

        _clear_running_preview(chat_widget, running_state)
        running_state["task_done"] = True
        running_state["task_finish_applied"] = reducer._task_finish_applied
        width = shutil.get_terminal_size((120, 24)).columns
        running_state["final_trace_lines"] = reducer.display_lines(width)
        running_state["deferred_scrollback_lines"] = scrollback.deferred_lines(
            chat_widget,
            width,
        )
        running_state["expanded_deferred_scrollback_lines"] = scrollback.deferred_lines(
            chat_widget,
            width,
            expand_reasoning=True,
        )
        running_state["expanded_deferred_reasoning_lines"] = scrollback.expanded_reasoning_lines(
            chat_widget,
            width,
        )
        running_state["has_deferred_scrollback"] = bool(
            running_state["deferred_scrollback_lines"]
        )
        # ChatWidget already rendered everything — no need to re-print
        # via _freeze_deferred_scrollback_for_exit.
        # If TASK_FINISH was never processed, Application didn't render the full output
        if not reducer._task_finish_applied:
            running_state["had_streaming_output"] = False
        app.invalidate()

    try:
        can = (queued_input is not None and queued_prompt_session is not None
               and hasattr(queued_prompt_session,"prompt_async"))
        if can:
            asyncio.run(_agent_loop())
        else:
            reducer = TraceEventReducer(
                shown_edit_diff_signatures=running_state.get("shown_edit_diff_signatures"),
            )
            printed_count = 0
            def _st():
                import re, shutil
                e = time.monotonic()-turn_started
                es = f" ({e:.1f}s)" if e>=1 else ""
                tw = shutil.get_terminal_size((80, 24)).columns
                GRAY = "\033[38;5;242m"
                RESET = "\033[0m"
                working = f"  • Working{es}  \033[2mCtrl+C — interrupt\033[0m"
                w_plain = re.sub(r'\x1b\[[0-9;]*[A-Za-z]', '', working)
                working = f"{GRAY}{w_plain.ljust(tw)}{RESET}"
                if status_line is not None:
                    footer_a = status_line.toolbar_text()
                    if footer_a:
                        footer_a = footer_a.replace(" \033[2m·\033[0m ", f" {GRAY}·{GRAY} ")
                        footer_a = footer_a.replace("\033[0m", GRAY)
                        f_plain = re.sub(r'\x1b\[[0-9;]*[A-Za-z]', '', footer_a)
                        padding = max(0, tw - len(f_plain))
                        return f"{working}\n{GRAY}  {footer_a}{' ' * padding}{RESET}"
                return working
            with Live(_st(), console=console, transient=True, refresh_per_second=10) as live:
                while not done_event.is_set():
                    while not trace_queue.empty():
                        try: evs = trace_queue.get_nowait()
                        except queue.Empty: break
                        reducer.apply_many(evs)
                        printed_count = _print_reducer_new_lines(console, reducer, printed_count)
                    if queued_notice_flag[0]:
                        console.print("  [yellow]Task is still queued; worker has not picked it up yet.[/yellow]")
                        queued_notice_flag[0] = False
                    live.update(_st())
                    done_event.wait(timeout=0.1)

        while not trace_queue.empty():
            try: evs = trace_queue.get_nowait()
            except queue.Empty: break
            # Non-Application fallback prints through TraceEventReducer above.
            if (
                not running_state.get("app_was_used")
                and not running_state.get("trace_printing_complete")
            ):
                reducer.apply_many(evs)
                printed_count = _print_reducer_new_lines(console, reducer, printed_count)

    except KeyboardInterrupt:
        cancel_task(task_id)
        done_event.set(); poll_thread.join(timeout=2)
        console.print("  [yellow]Interrupted by user.[/yellow]")
        raise

    if running_state.get("interrupted"):
        cancel_task(task_id)
        done_event.set(); poll_thread.join(timeout=2)
        console.print("  [yellow]Interrupted by user.[/yellow]")
        raise KeyboardInterrupt()

    if error_holder[0] is not None: raise error_holder[0]

    task = result_holder[0]
    if task is not None:
        task["_worked_for_label"] = f"Worked for {_running_elapsed_label(time.monotonic() - turn_started)}"
    if status_line is not None and task is not None:
        status_line.note_task(str(task.get("task_id") or task_id))
    if task is not None and running_state.get("app_was_used"):
        deferred_scrollback_lines = list(
            running_state.get("deferred_scrollback_lines") or []
        )
        expanded_deferred_scrollback_lines = list(
            running_state.get("expanded_deferred_scrollback_lines") or []
        )
        expanded_deferred_reasoning_lines = list(
            running_state.get("expanded_deferred_reasoning_lines") or []
        )
        task["_had_streaming_output"] = _has_streaming_output(running_state)
        task["_deferred_scrollback_lines"] = deferred_scrollback_lines
        task["_expanded_deferred_scrollback_lines"] = expanded_deferred_scrollback_lines
        task["_expanded_deferred_reasoning_lines"] = expanded_deferred_reasoning_lines
        task["_deferred_scrollback_frozen"] = bool(
            running_state.get("deferred_scrollback_frozen")
        )
    return task




__all__ = [
    "_SPINNER_CHARS", "_QUEUED_TASK_NOTICE", "_trace_path_for_task", "_read_new_trace_events",
    "_make_agent_prompt_session", "_running_elapsed_label", "_running_status_lines",
    "_running_output_lines", "_print_reducer_new_lines", "_set_buffer_text",
    "_sync_composer_from_prompt_buffer", "_route_prompt_buffer_key_to_composer",
    "_attach_running_composer_callbacks", "_create_running_application",
    "_chat_widget_frame_lines", "_sync_chat_composer_from_buffer", "_clear_running_preview",
    "_should_fallback_render_trace", "_has_streaming_output", "_ask_task_with_live_trace",
    "PromptSession", "Application", "Buffer", "Document", "KeyBindings", "ANSI", "HTML",
    "HSplit", "Layout", "Window", "ConditionalContainer", "BufferControl",
    "FormattedTextControl", "Dimension", "Style", "Validator", "ValidationError", "Condition",
]
