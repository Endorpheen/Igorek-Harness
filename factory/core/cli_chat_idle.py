"""Idle-mode prompt, logo, help, and transcript pager for rich chat."""
from __future__ import annotations

import contextlib
import io
import json
import os
import re
import shutil
import time
import urllib.error
import urllib.request
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

try:
    from prompt_toolkit import PromptSession
    from prompt_toolkit.application import Application
    from prompt_toolkit.buffer import Buffer
    from prompt_toolkit.completion import Completer, Completion
    from prompt_toolkit.formatted_text import ANSI, HTML
    from prompt_toolkit.key_binding import KeyBindings
    from prompt_toolkit.layout import (
        ConditionalContainer,
        Float,
        FloatContainer,
        HSplit,
        Layout,
        Window,
    )
    from prompt_toolkit.layout.controls import BufferControl, FormattedTextControl
    from prompt_toolkit.layout.dimension import Dimension
    from prompt_toolkit.layout.processors import BeforeInput
    from prompt_toolkit.lexers import PygmentsLexer
    from prompt_toolkit.output import create_output
    from prompt_toolkit.styles import Style
    from prompt_toolkit.validation import Validator, ValidationError
    from prompt_toolkit.filters import Condition
    try:
        from prompt_toolkit.layout.menus import CompletionsMenu
    except Exception:
        CompletionsMenu = None
except Exception:
    PromptSession = None; Application = None; Buffer = None; KeyBindings = None
    Completer = object; Completion = None
    ANSI = None; HTML = None; BeforeInput = None; Style = None
    Validator = object; ValidationError = ValueError
    HSplit = None; Layout = None; Window = None; ConditionalContainer = None
    BufferControl = None; FormattedTextControl = None; Dimension = None
    FloatContainer = None; Float = None; CompletionsMenu = None; Condition = None
    PygmentsLexer = None
    create_output = None

from .cli_chat import _load_entries
from .runtime import get_profile_config
from .cli_chat_rich_shared import (
    _InputFieldProcessor,
    _ansi_to_formatted_text,
    _clipboard_image_path,
    _clipboard_text,
    _factory_version,
    _install_screenshot_hotkey_noops,
    _truncate,
)

_SLASH_COMMANDS: list[tuple[str, str]] = [
    ("/help", "📋 Показать все команды и сочетания клавиш"),
    ("/exit", "🚪 Выход"),
    ("/clear", "🧹 Очистить историю сессии"),
    ("/history", "📜 Последние 20 сообщений"),
    ("/resume", "📂 Выбрать прошлую сессию для resume"),
    ("/fork", "📋 Скопировать прошлую сессию в новую"),
    ("/status", "📊 Статус сессии, сообщения, профиль, модель"),
    ("/model", "🤖 Показать или сменить модель"),
    ("/profile", "👤 Показать или сменить профиль"),
    ("/provider", "🔌 Показать или сменить провайдера"),
    ("/models", "📋 Список и выбор рекомендованной модели"),
    ("/wally", "💳 Аккаунт и баланс RunAnywhere (Wally)"),
    ("/statusline", "📟 Показать status line и настройки"),
    ("/title", "📝 Настроить заголовок терминала"),
    ("/theme", "🎨 Выбрать тему markdown и diff"),
    ("/skills", "🛠️ Управлять локальными skills"),
    ("/remember", "🧠 Явно записать факт в долговременную память"),
    ("/smarthelp", "💡 Быстрый вопрос через OpenRouter/Qwen"),
    ("/tui", "🖥️ Режим TUI: /tui fullscreen | /tui classic"),
    ("/mouse", "🖱️ Мышь: /mouse on | off | status"),
    ("/debug", "🐛 Debug-режим (подробные логи)"),
]


def _is_first_press(event) -> bool:
    """True if the key event is a fresh press, not a Repeat (auto-repeat
    from holding the key down) or a Release event.

    The kitty keyboard protocol and modern Wayland terminals dispatch
    each physical key as multiple events — a Press, a stream of
    Repeats while held, and a Release. Without this filter, a single
    Ctrl+V would fire the handler once for the Press and once for the
    first Repeat, producing two attachments with different temp file
    names (one per call to ``_clipboard_image_path``).
    """
    key_seq = getattr(event, "key_sequence", None) or []
    if not key_seq:
        return True
    last = key_seq[-1]
    arg = getattr(last, "arg", None)
    # arg is None for simple key presses, an int >= 2 for auto-repeat.
    if arg is None:
        return True
    return int(arg) <= 1
class _IdleReasoningToggle(Exception):
    """Raised by Ctrl+O in idle prompt to signal reasoning expand toggle."""

_CLI_TIPS = [
    "Совет: /smarthelp <вопрос> — быстрый ответ через OpenRouter",
    "Совет: /clear — очистить контекст если модель зациклилась",
    "Совет: модель лучше понимает задачи с конкретными путями к файлам",
    "Совет: /model — сменить модель прямо в сессии",
    "Совет: /statusline — показать текущее состояние агента",
    "Совет: /skills — показать или переключить локальные skills",
]

_IDLE_TEXTAREA_MIN_HEIGHT = 1
_IDLE_COMPOSER_VERTICAL_PADDING = 2
_IDLE_TERMINAL_RESERVED_ROWS = 6
_IDLE_LIVE_PREFIX_COLS = 2
_IDLE_RIGHT_MARGIN_COLS = 1


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


def _make_prompt_session(status_line=None, *, toolbar_enabled=True, reasoning_state=None, reasoning_toggle_exits=False):
    if PromptSession is None: return None
    # Codex-like composer surface styling — mirrors the live composer in
    # cli_chat_active._create_chat_application so idle and live modes look
    # visually identical (same chevron, same bg, same composer-surface).
    style = Style.from_dict({
        "bottom-toolbar": "#6c6c6c",
        "composer-surface": "bg:#303030",
        "input-field": "bg:#303030 #ffffff",
        "input-prompt": "bg:#303030 bold #ffffff",
        "prompt": "bg:#303030 bold #ffffff",
        "completion-menu.completion": "bg:#202020 #d0d0d0",
        "completion-menu.completion.current": "bg:#0f5f87 #ffffff",
        "completion-menu.meta.completion": "bg:#202020 #8a8a8a",
        "completion-menu.meta.completion.current": "bg:#0f5f87 #ffffff",
        "selected": "bg:#0f5f87 #ffffff",
    })
    class V(Validator):
        def validate(self, d):
            if not d.text.strip(): raise ValidationError(message="")

    # Slash command completer for idle mode
    from .tui.bottom_pane.file_search_popup import FileSearchPopup, _list_repo_files
    idle_file_popup = FileSearchPopup()

    class SlashCompleter(Completer if Completer is not object else object):
        def get_completions(self, document, complete_event):
            text = document.text_before_cursor
            # @ file search
            if text.startswith("@"):
                first_line = text.splitlines()[0] if text else ""
                after_at = first_line[1:] if first_line.startswith("@") and len(first_line) > 1 else ""
                query = after_at.strip().lower()
                # Get files from the popup's file list
                if not idle_file_popup._all_files:
                    idle_file_popup._all_files = _list_repo_files(idle_file_popup.cwd)
                files = idle_file_popup._all_files or []
                matches = [f for f in files if query in f.lower()] if query else files
                for f in matches[:20]:
                    yield Completion(
                        f,
                        start_position=-len(document.text_before_cursor.splitlines()[-1]),
                        display=f,
                        display_meta="",
                    )
                return

            # $ skill search
            if text.startswith("$"):
                first_line = text.splitlines()[0] if text else ""
                after_dollar = first_line[1:] if first_line.startswith("$") and len(first_line) > 1 else ""
                query = after_dollar.strip().lower()
                try:
                    from .skills_manager import skills_manager
                    if not skills_manager.is_loaded:
                        import io, contextlib
                        with contextlib.redirect_stdout(io.StringIO()):
                            skills_manager.load_settings()
                            skills_manager.load_skills()
                    skills = list(skills_manager.skills.values())
                except Exception:
                    skills = []
                if query:
                    matches = [s for s in skills if query in s.name.lower() or query in (s.description or "").lower()]
                else:
                    matches = skills
                for s in matches[:20]:
                    yield Completion(
                        f"${s.name} ",
                        start_position=-len(document.text_before_cursor.splitlines()[-1]),
                        display=f"${s.name}",
                        display_meta=(s.description or "")[:60],
                    )
                return

            if not text.startswith("/"):
                return
            # First token after /
            first_line = text.splitlines()[0] if text else ""
            after_slash = first_line[1:] if first_line.startswith("/") and len(first_line) > 1 else ""
            query = after_slash.split()[0] if after_slash else ""
            query_lower = query.lower()

            from .tui.bottom_pane.command_popup import _COMMANDS
            matches = []
            for cmd, desc in _COMMANDS:
                name = cmd[1:]  # strip /
                if not query_lower or name.lower().startswith(query_lower):
                    matches.append((0, cmd, desc))  # 0 = exact-ish match

            # Sort: exact match first
            matches.sort(key=lambda m: (0 if m[1][1:].lower() == query_lower else 1, m[0]))

            for idx, (_, cmd, desc) in enumerate(matches):
                yield Completion(
                    cmd + " ",
                    start_position=-len(document.text_before_cursor.splitlines()[-1]),
                    display=cmd,
                    display_meta=desc[:60],
                )

    kb = KeyBindings() if KeyBindings is not None else None
    idle_shortcuts_visible = [False]
    if kb is not None:
        _install_screenshot_hotkey_noops(kb)

        @kb.add("enter")
        def _(e): e.current_buffer.validate_and_handle()
        @kb.add("c-j")
        def _(e): e.current_buffer.insert_text("\n")

        @kb.add("tab")
        def _(e):
            # Tab: applies completion (file from @ or command from /)
            completion = _completion_to_apply(e.current_buffer)
            if completion is not None:
                e.current_buffer.apply_completion(completion)

        @kb.add("?")
        def _(e):
            if not e.current_buffer.text.strip():
                idle_shortcuts_visible[0] = not idle_shortcuts_visible[0]
            else:
                e.current_buffer.insert_text("?")

        @kb.add("c-t")
        def _(e):
            e.current_buffer.text = "__transcript__"
            e.current_buffer.validate_and_handle()

        @kb.add("c-o")
        def _(e):
            if reasoning_state and (
                reasoning_state.get("lines")
                or reasoning_state.get("expanded_lines")
            ):
                reasoning_state["visible"] = not reasoning_state.get("visible", False)
                if reasoning_toggle_exits:
                    e.app.exit(exception=_IdleReasoningToggle())
                else:
                    e.app.invalidate()

        @kb.add("c-v")
        def _(e):
            # Ctrl+V image paste — mirrors Codex TUI fixed.paste_image.
            # Reads an image from the system clipboard (Wayland:
            # wl-paste, X11: xclip) and inserts its path into the
            # buffer with a 📎 marker. The submit pipeline strips
            # the path from the text and attaches it as `input_files`
            # (image block). If the clipboard holds no image, leave
            # the buffer alone so the user can still use Shift+Insert
            # / middle-click for plain-text paste.
            #
            # The _is_first_press guard prevents the kitty / Wayland
            # terminal from dispatching the same key event as both
            # Press and Repeat — without it, each Ctrl+V would
            # attach the same screenshot twice with different temp
            # file names. We do the check inline (not via
            # filter=Condition) so it works the same way for both
            # real prompt_toolkit events and direct handler calls
            # (e.g. in tests / scripted invocations).
            if not _is_first_press(e):
                return
            path = _clipboard_image_path()
            if path:
                buf = e.current_buffer
                if buf.text and not buf.text.endswith((" ", "\n", "\t")):
                    buf.insert_text(" ")
                buf.insert_text(f"📎 {path} ")
                e.app.invalidate()

        @kb.add("escape")
        def _(e):
            if idle_shortcuts_visible[0]:
                idle_shortcuts_visible[0] = False

    def _tb():
        if idle_shortcuts_visible[0]:
            from .tui.bottom_pane.footer import shortcut_overlay_lines
            import shutil
            w = shutil.get_terminal_size((80, 24)).columns
            # On narrow screens, always one entry per line
            if w < 60:
                entries = [
                    ("/", "commands"), ("!", "shell commands"),
                    ("ctrl+j", "newline"), ("tab", "queue message"),
                    ("@", "file paths"), ("?", "shortcuts"),
                    ("ctrl+c", "exit"), ("esc", "edit previous"),
                ]
                lines = ["  " + f"{k} {v}" for k, v in entries]
            else:
                lines = ["  " + sl for sl in shortcut_overlay_lines(w)]
            text = "\n".join(lines)
            if ANSI is not None:
                return ANSI(text)
            return text
        h = "Enter — send  Ctrl+J — newline  Ctrl+C — exit"
        if status_line is None:
            return HTML(f"<style fg='#6c6c6c'>{h}</style>") if HTML is not None else h
        t = status_line.toolbar_text()
        if t and status_line.config.padding: t = (" " * status_line.config.padding) + t
        # Move path to a separate line so it doesn't get cut off on narrow terminals
        if t:
            last_sep = t.rfind(" · ")
            if last_sep > 0:
                suffix = t[last_sep + 3:]
                if suffix.startswith("~") or suffix.startswith("/"):
                    t = t[:last_sep] + "\n" + suffix
        c = f"{h}\n{t}" if t else h
        if ANSI is not None: return ANSI(c)
        if HTML is not None:
            clean = re.sub(r"\x1b\[[0-9;]*[A-Za-z]","",c)
            esc = clean.replace("&","&amp;").replace("<","&lt;").replace(">","&gt;").replace("\n","<br/>")
            return HTML(f"<style fg='#6c6c6c'>{esc}</style>")
        return c

    def _message():
        parts: list[str] = []
        if reasoning_state and (
            reasoning_state.get("lines")
            or reasoning_state.get("expanded_lines")
        ):
            key = "expanded_lines" if reasoning_state.get("visible") else "lines"
            reasoning_lines = [str(line) for line in reasoning_state.get(key) or []]
            if reasoning_lines and reasoning_lines[0] == "":
                reasoning_lines = reasoning_lines[1:]
            if (
                not reasoning_lines
                and not reasoning_state.get("visible")
                and reasoning_state.get("expanded_lines")
            ):
                reasoning_lines = ["  \033[2m• Размышления модели доступны · Ctrl+O раскрыть\033[0m"]
            parts.extend(reasoning_lines)
        # Codex-like: no plain "> " prefix — chevron is injected via BeforeInput
        # so it matches the live composer surface exactly.
        text = "\n".join(parts) if parts else ""
        return ANSI(text) if ANSI is not None else text

    # Codex-like composer chevron — same as live mode in cli_chat_active.
    _idle_processors = [_InputFieldProcessor()]
    if BeforeInput is not None:
        _idle_processors.append(BeforeInput("› ", style="class:input-prompt"))

    return PromptSession(
        message=_message,
        completer=SlashCompleter(), complete_while_typing=True,
        erase_when_done=True, prompt_continuation="  ",
        input_processors=_idle_processors,
        multiline=True, validator=V(), bottom_toolbar=_tb if toolbar_enabled else None,
        key_bindings=kb, style=style,
    )


def _idle_composer_height(buffer):
    """Codex-style textarea height: grow by visual wrapped lines.

    Codex keeps pasted text as a single logical line and lets the textarea
    compute wrapped ranges from the available width
    (bottom_pane/textarea.rs::desired_height).  Mirror that contract here:
    long pasted prompts expand the composer down instead of staying in a
    one-row horizontal scroller.
    """
    if Dimension is None:
        return _IDLE_TEXTAREA_MIN_HEIGHT
    if buffer is None:
        return Dimension.exact(_IDLE_TEXTAREA_MIN_HEIGHT)
    terminal = shutil.get_terminal_size((120, 24))
    max_allowed = max(
        _IDLE_TEXTAREA_MIN_HEIGHT,
        terminal.lines - _IDLE_TERMINAL_RESERVED_ROWS - _IDLE_COMPOSER_VERTICAL_PADDING,
    )
    usable_width = max(
        1,
        terminal.columns - _IDLE_LIVE_PREFIX_COLS - _IDLE_RIGHT_MARGIN_COLS,
    )
    line_count = _wrapped_line_count(getattr(buffer, "text", "") or "", usable_width)
    height = max(_IDLE_TEXTAREA_MIN_HEIGHT, min(max_allowed, line_count))
    return Dimension.exact(height)


def _idle_composer_footer_text(status_line=None):
    """Footer/status line below the composer — same visual as live mode."""
    h = "  Enter — send  Ctrl+J — newline  Ctrl+C — exit"
    if status_line is None:
        return [('', h)]
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
        result = []
        for line in t.splitlines():
            result.extend(_ansi_to_formatted_text_idle(f"  {line}"))
            result.append(('', '\n'))
        if result:
            result.pop()
            return result
    return [('', h)]


def _idle_reasoning_text(reasoning_state):
    """Render reasoning lines above the composer (if any)."""
    if not reasoning_state:
        return []
    if not (reasoning_state.get("lines") or reasoning_state.get("expanded_lines")):
        return []
    key = "expanded_lines" if reasoning_state.get("visible") else "lines"
    raw_lines = list(reasoning_state.get(key) or [])
    if raw_lines and raw_lines[0] == "":
        raw_lines = raw_lines[1:]
    if not raw_lines and not reasoning_state.get("visible") and reasoning_state.get("expanded_lines"):
        raw_lines = ["  \033[2m• Размышления модели доступны · Ctrl+O раскрыть\033[0m"]
    result = []
    for line in raw_lines:
        result.extend(_ansi_to_formatted_text_idle(str(line)))
        result.append(('', '\n'))
    if result:
        result.pop()
    return result


def _ansi_to_formatted_text_idle(text):
    """ANSI → prompt_toolkit converter shared with active composer."""
    return _ansi_to_formatted_text(text)


def _create_idle_composer_application(status_line=None, *, reasoning_state=None,
                                       reasoning_toggle_exits=False, mouse_controller=None):
    """Full-screen Application for idle input with Codex-style composer surface.

    This replaces PromptSession.prompt() for idle mode so the composer
    surface background (#303030) fills the entire terminal width —
    PromptSession only colours the characters, not the full row.

    Layout (top to bottom):
      - optional reasoning lines (if reasoning_state has content)
      - separator line
      - composer surface: padding + input field + padding (all bg:#303030)
      - footer/status line

    Returns (app, buffer) or (None, None) if prompt_toolkit is unavailable.
    """
    if (
        Application is None or Buffer is None or KeyBindings is None
        or HSplit is None or Layout is None or Window is None
        or BufferControl is None or FormattedTextControl is None
        or Style is None or CompletionsMenu is None
        or Dimension is None or Condition is None
    ):
        return None, None

    style = Style.from_dict({
        "composer-surface": "bg:#303030",
        "input-field": "bg:#303030 #ffffff",
        "input-prompt": "bg:#303030 bold #ffffff",
        "status": "#6c6c6c",
        "separator": "#444444",
        "completion-menu.completion": "bg:#202020 #d0d0d0",
        "completion-menu.completion.current": "bg:#0f5f87 #ffffff",
        "completion-menu.meta.completion": "bg:#202020 #8a8a8a",
        "completion-menu.meta.completion.current": "bg:#0f5f87 #ffffff",
        # Visible mouse-drag selection in the input field (prompt_toolkit
        # HighlightSelectionProcessor uses "class:selected").
        "selected": "bg:#0f5f87 #ffffff",
    })

    buffer = Buffer(
        multiline=True,
        completer=_make_idle_completer(),
        complete_while_typing=False,
    )

    kb = KeyBindings()
    _install_screenshot_hotkey_noops(kb)
    idle_shortcuts_visible = [False]
    rs = reasoning_state if reasoning_state is not None else {}
    if mouse_controller is not None:
        mouse_controller.install_bindings(kb)

    def _submit():
        text = buffer.text.strip()
        if text:
            buffer.reset()
        return text if text else None

    @kb.add("enter")
    def _(event):
        result = _submit()
        if result is not None:
            event.app.exit(result=result)

    @kb.add("c-j")
    def _(event):
        event.current_buffer.insert_text("\n", fire_event=False)

    @kb.add("tab")
    def _(event):
        completion = _completion_to_apply(event.current_buffer)
        if completion is not None:
            event.current_buffer.apply_completion(completion)

    @kb.add("?")
    def _(event):
        if not event.current_buffer.text.strip():
            idle_shortcuts_visible[0] = not idle_shortcuts_visible[0]
            event.app.invalidate()
        else:
            event.current_buffer.insert_text("?", fire_event=False)

    def _insert_completion_trigger(event, text: str) -> None:
        if not event.current_buffer.text.strip():
            event.current_buffer.insert_text(text, fire_event=False)
            event.current_buffer.start_completion(select_first=False)
            event.app.invalidate()
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
        event.current_buffer.text = "__transcript__"
        event.current_buffer.validate_and_handle()
        event.app.exit(result="__transcript__")

    @kb.add("c-o")
    def _(event):
        if rs and (rs.get("lines") or rs.get("expanded_lines")):
            rs["visible"] = not rs.get("visible", False)
            if reasoning_toggle_exits:
                event.app.exit(exception=_IdleReasoningToggle())
            else:
                event.app.invalidate()

    @kb.add("c-v")
    def _(event):
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
        text = _clipboard_text()
        if text:
            event.current_buffer.insert_text(text, fire_event=False)
            event.app.invalidate()

    @kb.add("escape")
    def _(event):
        if idle_shortcuts_visible[0]:
            idle_shortcuts_visible[0] = False
            event.app.invalidate()

    @kb.add("c-c")
    def _(event):
        event.app.exit(result=None)

    def _reasoning_lines():
        return _idle_reasoning_text(rs)

    def _footer_text():
        notice = (
            mouse_controller.current_notification()
            if mouse_controller is not None else None
        )
        if idle_shortcuts_visible[0]:
            from .tui.bottom_pane.footer import shortcut_overlay_lines
            w = shutil.get_terminal_size((80, 24)).columns
            if w < 60:
                entries = [
                    ("/", "commands"), ("!", "shell commands"),
                    ("ctrl+j", "newline"), ("tab", "queue message"),
                    ("@", "file paths"), ("?", "shortcuts"),
                    ("ctrl+c", "exit"), ("esc", "edit previous"),
                ]
                lines = ["  " + f"{k} {v}" for k, v in entries]
            else:
                lines = ["  " + sl for sl in shortcut_overlay_lines(w)]
            result = []
            for line in lines:
                result.append(('', line))
                result.append(('', '\n'))
            if result:
                result.pop()
        else:
            result = _idle_composer_footer_text(status_line)
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
                FormattedTextControl(_reasoning_lines),
                wrap_lines=True,
                height=Dimension(min=0),
            ),
            filter=Condition(lambda: bool(_reasoning_lines())),
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
                    height=lambda: _idle_composer_height(buffer),
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
        ConditionalContainer(
            CompletionsMenu(max_height=12, scroll_offset=1),
            filter=Condition(lambda: getattr(buffer, "complete_state", None) is not None),
        ),
        Window(
            FormattedTextControl(_footer_text),
            height=Dimension(min=1, max=3),
            style="class:status",
        ),
    ])
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
        layout=Layout(root_body),
        key_bindings=kb,
        style=style,
        output=app_output,
        full_screen=False,
        mouse_support=mouse_support_filter,
        erase_when_done=True,
        refresh_interval=0.1,
    )
    if mouse_controller is not None:
        mouse_controller.set_app(app)
        _idle_mouse_frame_builder(app, mouse_controller, rs)
    return app, buffer


def _idle_mouse_frame_builder(app, controller, reasoning_state):
    """Register a frame builder describing the idle reasoning region."""
    from .tui.mouse_frame import FrameRegion, FrameRow, ScreenFrame

    def _build():
        try:
            base = app.renderer.rows_above_layout
        except Exception:
            return None
        lines = []
        if reasoning_state:
            key = "expanded_lines" if reasoning_state.get("visible") else "lines"
            lines = list(reasoning_state.get(key) or [])
            if lines and lines[0] == "":
                lines = lines[1:]
            if (
                not lines
                and not reasoning_state.get("visible")
                and reasoning_state.get("expanded_lines")
            ):
                lines = ["  \033[2m• Размышления модели доступны · Ctrl+O раскрыть\033[0m"]
        rows = []
        for i, line in enumerate(lines):
            rows.append(FrameRow(base + i, line, region_id="reasoning"))
        regions = {}
        if lines:
            regions["reasoning"] = FrameRegion(
                region_id="reasoning",
                kind="reasoning",
                top=base,
                bottom=base + len(lines) - 1,
                header_row=base,
            )
            composer_top = base + len(lines) + 1
        else:
            composer_top = base + 1
        return ScreenFrame(rows=rows, regions=regions, composer_top=composer_top)

    controller.set_frame_builder(_build)


def _make_idle_completer():
    """Return the idle completer used for slash commands, file paths, and skills."""
    if Completer is object or Completion is None:
        return None

    from .tui.bottom_pane.file_search_popup import FileSearchPopup, _list_repo_files
    idle_file_popup = FileSearchPopup()

    class SlashCompleter(Completer):
        def get_completions(self, document, complete_event):
            text = document.text_before_cursor
            if "\n" in text or "\r" in text:
                return
            if text.startswith("@"):
                first_line = text.splitlines()[0] if text else ""
                after_at = first_line[1:] if first_line.startswith("@") and len(first_line) > 1 else ""
                query = after_at.strip().lower()
                if not idle_file_popup._all_files:
                    idle_file_popup._all_files = _list_repo_files(idle_file_popup.cwd)
                files = idle_file_popup._all_files or []
                matches = [f for f in files if query in f.lower()] if query else files
                for f in matches[:20]:
                    yield Completion(
                        f,
                        start_position=-len(document.text_before_cursor.splitlines()[-1]),
                        display=f,
                        display_meta="",
                    )
                return

            if text.startswith("$"):
                first_line = text.splitlines()[0] if text else ""
                after_dollar = first_line[1:] if first_line.startswith("$") and len(first_line) > 1 else ""
                query = after_dollar.strip().lower()
                try:
                    from .skills_manager import skills_manager
                    if not skills_manager.is_loaded:
                        import io, contextlib
                        with contextlib.redirect_stdout(io.StringIO()):
                            skills_manager.load_settings()
                            skills_manager.load_skills()
                    skills = list(skills_manager.skills.values())
                except Exception:
                    skills = []
                if query:
                    matches = [s for s in skills if query in s.name.lower() or query in (s.description or "").lower()]
                else:
                    matches = skills
                for s in matches[:20]:
                    yield Completion(
                        f"${s.name} ",
                        start_position=-len(document.text_before_cursor.splitlines()[-1]),
                        display=f"${s.name}",
                        display_meta=(s.description or "")[:60],
                    )
                return

            if not text.startswith("/"):
                return
            first_line = text.splitlines()[0] if text else ""
            after_slash = first_line[1:] if first_line.startswith("/") and len(first_line) > 1 else ""
            query = after_slash.split()[0] if after_slash else ""
            query_lower = query.lower()

            from .tui.bottom_pane.command_popup import _COMMANDS
            matches = []
            for cmd, desc in _COMMANDS:
                name = cmd[1:]
                if not query_lower or name.lower().startswith(query_lower):
                    matches.append((0, cmd, desc))
            matches.sort(key=lambda m: (0 if m[1][1:].lower() == query_lower else 1, m[0]))

            for _, cmd, desc in matches:
                yield Completion(
                    cmd + " ",
                    start_position=-len(document.text_before_cursor.splitlines()[-1]),
                    display=cmd,
                    display_meta=desc[:60],
                )

    return SlashCompleter()


def _completion_to_apply(buffer):
    """Return the selected completion, or the first available completion."""
    state = getattr(buffer, "complete_state", None)
    if state is None:
        start_completion = getattr(buffer, "start_completion", None)
        if callable(start_completion):
            try:
                start_completion(select_first=True)
            except TypeError:
                start_completion()
            state = getattr(buffer, "complete_state", None)
    if state is None:
        return None

    current = getattr(state, "current_completion", None)
    if current is not None:
        return current

    completions = (
        getattr(state, "completions", None)
        or getattr(state, "current_completions", None)
        or []
    )
    if completions:
        return completions[0]
    return None


def _idle_transcript_pager(console, sp: Path):
    """Show session transcript in a simple pager (idle mode Ctrl+T)."""
    from .tui.pager_overlay import PagerOverlay
    entries = _load_entries(sp)
    if not entries:
        console.print("  [dim]Transcript is empty.[/dim]"); console.print(); return
    lines = []
    for e in entries:
        text = str(e.get("text", ""))
        if not text:
            continue
        role = str(e.get("role", ""))
        if role == "user":
            lines.append(f"  \033[96m▶ {_truncate(text, 300)}\033[0m")
        elif role == "assistant":
            for tl in text.rstrip().splitlines():
                lines.append(f"  {tl}")
        else:
            lines.append(f"  \033[2m{_truncate(text, 200)}\033[0m")
        lines.append("")  # blank line between entries
    overlay = PagerOverlay()
    size = shutil.get_terminal_size((80, 24))
    overlay.set_size(size.columns, size.lines)
    overlay.set_show_empty_tildes(False)
    overlay.set_content(lines)
    overlay.show()
    while True:
        # Render current page — use print() for ANSI passthrough, not Rich
        rendered = overlay.render()
        print()
        for rl in rendered:
            print(rl)
        print()
        try:
            raw_cmd = input("  j/k/space/q: ")
        except (EOFError, KeyboardInterrupt):
            break
        cmd = raw_cmd.strip()
        cmd_lower = cmd.lower()
        if cmd_lower in ("q", "quit", "exit", ""):
            break
        elif cmd_lower in ("j", "down", "d"):
            overlay.scroll_down(3)
        elif cmd_lower in ("k", "up", "u"):
            overlay.scroll_up(3)
        elif raw_cmd == " " or cmd_lower in ("space", "page", "pgdn"):
            overlay.page_down()
        elif cmd_lower in ("pgup", "b"):
            overlay.page_up()
        elif cmd == "G" or cmd_lower in ("bottom", "end"):
            overlay.scroll_to_bottom()
        elif cmd_lower in ("g", "top"):
            overlay.scroll_to_top()
        # Re-check terminal size in case it changed
        size = shutil.get_terminal_size((80, 24))
        overlay.set_size(size.columns, size.lines)
    print()


def _logo(console: Console, profile: str, model: str, branch: str, *, backend: str = "", version: str = "",
           update_info: tuple[bool, str, str] | None = None) -> None:
    if backend == "gguf": provider = "YOUR_PC"
    else:
        try:
            from .codex_provider import user_facing_codex_provider
            provider = user_facing_codex_provider(get_profile_config(profile).get("provider"))
        except Exception: provider = ""
    tbl = Table.grid(padding=(0, 1))
    tbl.add_column(style="dim", min_width=10, no_wrap=True)
    tbl.add_column(style="bright_white", no_wrap=True)
    tbl.add_row("Profile", profile)
    if provider: tbl.add_row("Provider", provider)
    tbl.add_row("Model", model)
    if branch: tbl.add_row("Branch", branch)
    if version: tbl.add_row("Version", version)
    if update_info and update_info[0]:
        _, latest, command = update_info
        tbl.add_row(
            "Update",
            f"[yellow]↑ {latest} available[/yellow]  [dim]{command}[/dim]",
        )
    logo_panel = Panel(tbl, title="[bold blue] Igorek Agent Factory [/bold blue]",
                        title_align="left", border_style="blue", padding=(0, 1), expand=False)

    # Commands panel — grouped side by side with logo
    cmd_tbl = Table(show_header=False, box=None, padding=(0, 1))
    cmd_tbl.add_column(style="bold bright_blue", no_wrap=True, width=12)
    cmd_tbl.add_column(style="blue", no_wrap=True, width=12)
    cmd_tbl.add_column(style="blue", no_wrap=True, width=12)
    cmd_tbl.add_column(style="blue", no_wrap=True, width=12)
    cmd_tbl.add_column(style="blue", no_wrap=True, width=12)
    cmd_groups = [
        ("Сессия", ["/clear", "/history", "/resume", "/fork"]),
        ("Модель", ["/model", "/models", "/profile", "/provider"]),
        ("Интерфейс", ["/theme", "/title", "/statusline", ""]),
        ("Память/tools", ["/remember", "/skills", "/smarthelp", ""]),
        ("Сервис", ["/status", "/debug", "/help", "/exit"]),
    ]
    for group_name, cmds in cmd_groups:
        cmd_tbl.add_row(group_name, *cmds)
    cmd_panel = Panel(cmd_tbl, title="[bold blue] Команды [/bold blue]",
                      title_align="left", border_style="blue", padding=(0, 1), expand=False)

    # Layout: logo + commands side by side
    layout = Table(show_header=False, box=None, padding=(0, 2), expand=False)
    layout.add_column()
    layout.add_column()
    layout.add_row(logo_panel, cmd_panel)

    console.print(layout)
    console.print()


def _help(console: Console) -> None:
    console.print("  [dim]Enter — отправить  Ctrl+J — новая строка  Ctrl+C — выход  /help — справка[/dim]")
    console.print()


# ---------------------------------------------------------------------------
# Update-check helpers
# ---------------------------------------------------------------------------

_PYPI_URL = "https://pypi.org/pypi/igorek-agent-factory/json"
_GITHUB_RELEASES_URL = os.environ.get("IGOREK_RELEASES_API_URL", "").strip()
_PKG_NAME = "igorek-agent-factory"
_UPDATE_TTL_SECONDS = 3600.0
_UPDATE_HTTP_TIMEOUT = 2.5
_UPDATE_CACHE_DIR = Path(os.path.expanduser("~/.cache/igorek-agent-factory"))


def _parse_version(v: str) -> tuple[int, ...]:
    """Best-effort semver-ish parse: drop leading 'v', split on '.'/-'/'+'."""
    if not v:
        return ()
    s = v.strip().lstrip("vV")
    for sep in ("+", "-"):
        if sep in s:
            s = s.split(sep, 1)[0]
    parts: list[int] = []
    for chunk in s.split("."):
        digits = ""
        for ch in chunk:
            if ch.isdigit():
                digits += ch
            else:
                break
        parts.append(int(digits) if digits else 0)
    return tuple(parts)


def _versions_differ(a: str, b: str) -> bool:
    pa, pb = _parse_version(a), _parse_version(b)
    if not pa or not pb:
        return a.strip() != b.strip()
    # Pad to the same length for fair comparison.
    n = max(len(pa), len(pb))
    pa = pa + (0,) * (n - len(pa))
    pb = pb + (0,) * (n - len(pb))
    return pb > pa


def _http_get_json(url: str) -> dict | None:
    try:
        req = urllib.request.Request(url, headers={
            "Accept": "application/json",
            "User-Agent": f"{_PKG_NAME}/update-check",
        })
        with urllib.request.urlopen(req, timeout=_UPDATE_HTTP_TIMEOUT) as resp:
            raw = resp.read()
    except (urllib.error.URLError, TimeoutError, OSError, ValueError):
        return None
    try:
        data = json.loads(raw.decode("utf-8", errors="replace"))
    except (ValueError, UnicodeDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _fetch_latest_from_pypi() -> str:
    data = _http_get_json(_PYPI_URL)
    if not data:
        return ""
    info = data.get("info")
    if not isinstance(info, dict):
        return ""
    return str(info.get("version") or "").strip()


def _fetch_latest_from_github() -> str:
    if not _GITHUB_RELEASES_URL:
        return ""
    data = _http_get_json(_GITHUB_RELEASES_URL)
    if not data:
        return ""
    tag = str(data.get("tag_name") or "").strip()
    # Also accept "name" as fallback (some repos use vX.Y.Z there).
    if not tag:
        tag = str(data.get("name") or "").strip()
    return tag


def _update_cache_path() -> Path:
    _UPDATE_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    return _UPDATE_CACHE_DIR / "update-status.json"


def _read_update_cache(ttl: float) -> tuple[bool, str, str] | None:
    try:
        path = _update_cache_path()
        if not path.is_file():
            return None
        stat = path.stat()
        if (time.time() - stat.st_mtime) > ttl:
            return None
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(payload, dict):
        return None
    latest = str(payload.get("latest") or "")
    if not latest:
        return None
    available = bool(payload.get("available"))
    command = str(payload.get("command") or "")
    return (available, latest, command)


def _write_update_cache(available: bool, latest: str, command: str) -> None:
    try:
        path = _update_cache_path()
        path.write_text(
            json.dumps(
                {"available": available, "latest": latest, "command": command,
                 "checked_at": int(time.time())},
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
    except OSError:
        return


def _get_update_status(console: Console, *, ttl: float = _UPDATE_TTL_SECONDS) -> tuple[bool, str, str] | None:
    """Check whether a newer version of igorek-agent-factory is available.

    Returns ``(available, latest_version, upgrade_command)`` or ``None`` if
    the check could not be performed (no network, rate limit, parse error,
    already on the latest version, etc.). The result is cached on disk for
    ``ttl`` seconds to keep the chat startup snappy.
    """
    # Cache hit short-circuits the network.
    cached = _read_update_cache(ttl)
    if cached is not None:
        # Cache stores the "found anything newer" verdict, so a False here
        # already means "you're up to date".
        if not cached[0]:
            return None
        return cached

    current = _factory_version()
    if not current or current == "0.0.0":
        return None

    latest = _fetch_latest_from_pypi()
    source = "pypi"
    if not latest:
        latest = _fetch_latest_from_github()
        source = "github"
    if not latest:
        return None

    if not _versions_differ(current, latest):
        _write_update_cache(False, latest, "")
        return None

    # Prefer the "uv tool" upgrade hint because that matches the current
    # install path; mention pip as a portable fallback.
    if source == "pypi":
        command = f"uv tool upgrade {_PKG_NAME}    # or: pip install -U {_PKG_NAME}"
    else:
        command = f"uv tool install --force {_PKG_NAME}    # or: pip install -U {_PKG_NAME}"

    _write_update_cache(True, latest, command)
    return (True, latest, command)




__all__ = [
    "_SLASH_COMMANDS", "_CLI_TIPS", "_make_prompt_session",
    "_create_idle_composer_application",
    "_completion_to_apply", "_idle_transcript_pager", "_logo", "_help", "_get_update_status",
    "_make_idle_completer",
    "PromptSession", "KeyBindings",
    "Completer", "Completion", "ANSI", "HTML", "BeforeInput", "Style", "Validator", "ValidationError",
]
