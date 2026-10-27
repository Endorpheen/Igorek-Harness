"""Codex-style chat session: ChatWidget rendering, one loop, no prompt_toolkit layout."""
from __future__ import annotations

import asyncio
import json
import os
import queue
import re
import select
import shutil
import subprocess
import sys
import termios
import threading
import time
import tty
from pathlib import Path
from typing import Any, Optional

from rich.console import Console
from rich.live import Live
from rich.padding import Padding
from rich.panel import Panel
from rich.table import Table

try:
    from prompt_toolkit import PromptSession
    from prompt_toolkit.formatted_text import ANSI, HTML
    from prompt_toolkit.history import FileHistory
    from prompt_toolkit.key_binding import KeyBindings
    from prompt_toolkit.styles import Style
except Exception:
    PromptSession = None
    ANSI = HTML = None
    FileHistory = None
    KeyBindings = None
    Style = None

from .agent_foundation import (
    bootstrap_active, bootstrap_intro_message,
    extract_memory_write_blocks, process_bootstrap_answer,
    strip_memory_status_lines,
)
from .cli_chat import (
    _append_entry, _clear_session, _history_tail, _load_entries,
    _print_resume_hint, _read_session_meta, _write_session_meta,
    _sanitize_session_name, _session_path,
)
from .cli_chat_rich_shared import (
    _ANSI_RE, _ansi_to_formatted_text,
    _branch, _factory_version, _filter_control_chars,
    _truncate, _user_msg, _assistant_msg, _error_msg,
    _clipboard_image_path,
)
from .cli_chat_terminal import (
    save_terminal_title_items,
)
from .cli_tasks import cancel_task, create_task, get_task
from .isolation.host_op_approval import (
    HostOpApprovalError,
    is_host_op_blocked_record,
    parse_host_op_blocked_record,
)
from .isolation.host_op_gate import HostOpApprovalOutcome
from .memory_manager import MemoryManager
from .message_queue import MessageQueue
from .runtime import get_profile_config, profile_model_name
from .status_line import (
    StatusLineRuntime, save_status_line_items,
)
from .tui.chatwidget import ChatWidget
from .tui.cells.history_cell import (
    ErrorCell, MarkdownCell, ReasoningCell,
    SeparatorCell, StatusCell, TextCell, ToolCallGroupCell,
)
from .tui.render.renderable import Rect
from .tui.trace_reducer import TraceEventReducer
from .tui.theme import list_available_themes, load_theme_name, preview_theme, save_theme_name


DEFAULT_SLASH_COMMANDS = [
    ("/help", "Показать все команды"),
    ("/exit", "Выход"),
    ("/clear", "Очистить историю"),
    ("/history", "Последние 20 сообщений"),
    ("/goal", "Управлять долгосрочной целью"),
    ("/resume", "Выбрать сессию для resume"),
    ("/fork", "Скопировать сессию в новую"),
    ("/status", "Статус сессии"),
    ("/model", "Показать/сменить модель"),
    ("/profile", "Показать/сменить профиль"),
    ("/provider", "Показать/сменить провайдера"),
    ("/models", "Список моделей"),
    ("/wally", "Аккаунт и баланс RunAnywhere (Wally)"),
    ("/statusline", "Настройки status line"),
    ("/title", "Настройки заголовка"),
    ("/theme", "Выбрать тему"),
    ("/skills", "Управлять skills"),
    ("/remember", "Записать в память"),
    ("/smarthelp", "Быстрый вопрос"),
    ("/debug", "Debug-режим"),
]


_REASONING_TOGGLE = "__reasoning__"
_ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


class _ReasoningToggle(Exception):
    """Raised by Ctrl+O key binding to toggle reasoning expand."""


def _terminal_width() -> int:
    return shutil.get_terminal_size((120, 24)).columns


def _trace_path_for_task(task_id: str) -> str:
    from .cli_chat_active import _trace_path_for_task as _tp
    return str(_tp(task_id))


def _read_new_trace_events(trace_path: str, seen: int) -> tuple[list[dict], int]:
    try:
        with open(trace_path) as f:
            lines = f.readlines()
    except (FileNotFoundError, OSError):
        return [], seen
    if seen >= len(lines):
        return [], seen
    events = []
    for line in lines[seen:]:
        line = line.strip()
        if line:
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                pass
    return events, len(lines)


def _elapsed_label(elapsed: float) -> str:
    if elapsed < 60:
        return f"{elapsed:.0f}s"
    if elapsed < 3600:
        return f"{int(elapsed // 60)}m {int(elapsed % 60):02d}s"
    h = int(elapsed // 3600)
    m = int((elapsed % 3600) // 60)
    return f"{h}h {m:02d}m {int(elapsed % 60):02d}s"


def _print_help(console: Console) -> None:
    console.print("  Команды")
    for cmd, desc in DEFAULT_SLASH_COMMANDS:
        console.print(f"  {cmd:<12} {desc}")
    console.print()
    console.print("  Enter — отправить  Ctrl+J — новая строка  Ctrl+C — выход")


def _print_blocked_tool_target(blocked: dict) -> str:
    if is_host_op_blocked_record(blocked):
        op = str(blocked.get("op") or "?").strip()
        approval_class = str(blocked.get("approval_class") or "").strip()
        return f"{op} [{approval_class}]" if approval_class else op
    tool = str(blocked.get("tool") or "?")
    target = str(blocked.get("target") or blocked.get("input", {}).get("file_path", "") or "")
    if target:
        return f"{tool} {_truncate(target, 80)}"
    return tool


def _plain_terminal_line(line: str) -> str:
    return _ANSI_ESCAPE_RE.sub("", str(line or "")).strip()


def _is_activity_header_line(line: str) -> bool:
    plain = _plain_terminal_line(line)
    if plain.startswith("• "):
        plain = plain[2:].lstrip()
    return (
        plain in {"Explored", "Exploring"}
        or plain.startswith("Ran ")
        or plain.startswith("Running ")
    )


def _with_scrollback_activity_gaps(lines: list[str]) -> list[str]:
    spaced: list[str] = []
    for line in lines:
        if (
            spaced
            and spaced[-1] != ""
            and line != ""
            and _is_activity_header_line(line)
        ):
            spaced.append("")
        spaced.append(line)
    return spaced


def _has_rendered_task_output(scrollback_lines: list[str], emitted_line_count: int) -> bool:
    return emitted_line_count > 0 or bool(scrollback_lines)


from .approval_resume import _approval_context_for_resume, _forced_tool_call_for_resume


def _is_first_press(event) -> bool:
    """Check if this is the first key press (not a repeat)."""
    return True


def _set_raw_with_output(fd: int) -> list:
    """Switch terminal to raw mode but keep OPOST|ONLCR so \n -> \r\n."""
    old = termios.tcgetattr(fd)
    tty.setraw(fd)
    mode = termios.tcgetattr(fd)
    mode[1] |= termios.OPOST | termios.ONLCR  # OFLAG
    termios.tcsetattr(fd, termios.TCSANOW, mode)
    return old


def _check_interrupt_key(fd: int) -> bool:
    """Non-blocking read of stdin; return True if Escape or Ctrl+C pressed."""
    if not select.select([sys.stdin], [], [], 0)[0]:
        return False
    ch = os.read(fd, 1)
    if ch == b"\x03":
        return True
    if ch == b"\x1b":
        # Single Escape vs escape sequence (arrow keys, etc.)
        time.sleep(0.05)
        if select.select([sys.stdin], [], [], 0)[0]:
            # Drain the rest of the escape sequence
            while select.select([sys.stdin], [], [], 0)[0]:
                try:
                    os.read(fd, 1)
                except Exception:
                    break
            return False
        return True
    return False


class ChatSession:
    """Codex-style chat: one ChatWidget, one loop, clean input via PromptSession.prompt()."""

    def __init__(
        self,
        console: Console,
        profile: str,
        session_name: str,
        model: str,
        branch: str,
        cwd: str,
        data_dir: Optional[str] = None,
        mm: Optional[MemoryManager] = None,
        interval_s: float = 0.5,
        timeout_s: float = 1200,
        show_debug: bool = False,
        on_profile_change=None, before_turn=None, exit_on_interrupt: bool = False, submit_task=None,
        start_fullscreen=None, classic_style="legacy",
    ):
        self.on_profile_change = on_profile_change
        self.before_turn = before_turn
        self.exit_on_interrupt = exit_on_interrupt
        self.submit_task = submit_task
        self.classic_style = classic_style
        self.console = console
        self.profile = profile
        self.session_name = session_name
        self.model = model
        self.branch = branch
        self.cwd = cwd
        self.data_dir = data_dir
        self.mm = mm or MemoryManager()
        self.interval_s = interval_s
        self.timeout_s = timeout_s
        self.show_debug = show_debug

        self.pc = get_profile_config(profile)
        self.session_path = _session_path(session_name)
        self.sl = StatusLineRuntime(
            session_name=session_name, session_path=self.session_path,
            profile=profile, profile_config=self.pc,
            model=model, version=_factory_version(),
            cwd=cwd,
        )
        self.mq = MessageQueue()
        self.model_override: Optional[str] = None
        self.pending_inputs: list[str] = []
        self.approved_session_rules: list[dict] = []
        self.reasoning_state: dict = {"visible": False, "lines": [], "expanded_lines": []}
        self.shown_edit_diff_signatures: set = set()
        self.last_worked_for = ""
        self._active = True

        # ChatWidget — single source of truth for transcript
        self.widget = ChatWidget(model=model, branch=branch, cwd=cwd)
        self._emitted_line_count = 0

        self._mouse_controller = self._make_mouse_controller()
        self._viewer_history_items: list = []
        self._tui_fullscreen = os.environ.get(
            "IGOREK_TUI_FULLSCREEN", "1"
        ).strip().lower() not in {"0", "off", "false", "no"}
        if start_fullscreen is not None:
            self._tui_fullscreen = bool(start_fullscreen)
        self._fs_scroll = {"offset": 0, "follow": True}
        self._startup_lines: list[str] = []
        self._fs_history_cells: list = []

        history_path = os.path.expanduser("~/.igorek_history")
        self._history = FileHistory(history_path) if FileHistory else None

    # ── public entry ───────────────────────────────────────────

    def run(self) -> int:
        """Full session: logo, history replay, main loop."""
        try:
            self._startup()
            while self._active:
                if (self._tui_fullscreen or getattr(self, "classic_style", "legacy") == "modern"
                        or getattr(self, "exit_on_interrupt", False)):
                    self.run_fullscreen()
                    continue
                self._render_widget()
                if self.pending_inputs:
                    text = self.pending_inputs.pop(0)
                else:
                    goal_prompt = self._next_goal_prompt()
                    if goal_prompt:
                        self._execute_user_turn(goal_prompt, True)
                        continue
                    text = self._read_input()
                if text is None:
                    break
                if text == _REASONING_TOGGLE:
                    # Compatibility fallback for older prompt sessions. The
                    # current path toggles inside prompt_toolkit and redraws
                    # the prompt message without touching terminal scrollback.
                    continue
                if isinstance(text, str) and text.startswith("\x00"):
                    # Debug: unknown special key
                    continue
                if not text:
                    continue
                if text == "/exit":
                    break
                if not self._tui_fullscreen:
                    _user_msg(self.console, text)
                self._handle_input(text)
        except KeyboardInterrupt:
            pass
        finally:
            if getattr(self, "exit_on_interrupt", False):
                task_id = getattr(self, "_current_task_id", None)
                if task_id:
                    task = get_task(task_id)
                    if task and task["status"] not in {"ok", "fail", "dead_letter", "cancelled"}:
                        cancel_task(task_id)
            try:
                self._mouse_controller.disable()
                self._mouse_controller.release_output()
            except Exception:
                pass
            if getattr(self, "_used_fullscreen", False):
                # Exiting a fullscreen session: keep only the resume hint,
                # not the replayed startup banner/help from the main buffer.
                try:
                    self.console.clear()
                except Exception:
                    pass
        self._goodbye()
        return 0

    # ── startup ────────────────────────────────────────────────

    def _startup(self):
        sp = self.session_path
        is_new = not sp.exists() or sp.stat().st_size == 0
        _write_session_meta(
            sp, session_id=self.session_name, profile=self.profile,
            model=self.model, provider=str(self.pc.get("provider", "")),
            cwd=self.cwd, git_branch=self.branch,
        )
        from .cli_chat_idle import _logo as _print_logo, _help as _print_help_cmd
        if getattr(self, "classic_style", "legacy") == "modern":
            from .cli_classic import header
            header(self.console, profile=self.profile, model=self.model,
                   version=_factory_version(), cwd=self.cwd)
        else:
            _print_logo(
                self.console, self.profile, self.model, self.branch,
                backend=self.pc.get("backend", ""),
                version=_factory_version(),
                update_info=self._get_update_status(),
            )
            _print_help_cmd(self.console)

        # Capture the same banner/help so fullscreen mode can render the
        # startup header inside its own viewport.
        try:
            import io as _io
            from rich.console import Console as _RichConsole
            _buf = _io.StringIO()
            _cap = _RichConsole(
                file=_buf, force_terminal=True, width=self.console.width
            )
            if getattr(self, "classic_style", "legacy") == "modern":
                from .cli_classic import header
                header(_cap, profile=self.profile, model=self.model,
                       version=_factory_version(), cwd=self.cwd)
            else:
                _print_logo(
                    _cap, self.profile, self.model, self.branch,
                    backend=self.pc.get("backend", ""),
                    version=_factory_version(),
                )
                _print_help_cmd(_cap)
            self._startup_lines = _buf.getvalue().rstrip("\n").splitlines()
        except Exception:
            self._startup_lines = []

        initial = _load_entries(sp)
        if initial:
            for entry in initial:
                tid = str(entry.get("task_id") or "").strip()
                if tid:
                    self.sl.note_task(tid)
            self.sl.refresh()
            meta = _read_session_meta(sp)
            meta_parts = [f"{len(initial)} messages"]
            if meta:
                if meta.get("model"):
                    meta_parts.append(meta["model"])
                if meta.get("cwd"):
                    meta_parts.append(meta["cwd"])
            self._print_resume_history_header(meta_parts)
            self._replay_initial_history(initial)
            self._restore_reasoning_state(initial)
            self.console.print()

        if not initial and not is_new and bootstrap_active(self.data_dir):
            intro = bootstrap_intro_message(self.data_dir)
            _append_entry(sp, role="assistant", text=intro, status="bootstrap")
            _assistant_msg(self.console, intro)
            self.console.print()

    def _get_update_status(self) -> Optional[tuple]:
        from .cli_chat_idle import _get_update_status
        return _get_update_status(self.console)

    def _print_resume_history_header(self, meta_parts: list[str]):
        from .cli_chat_rich import _print_resume_history_header
        _print_resume_history_header(self.console, self.session_name, meta_parts)

    def _replay_initial_history(self, initial: list[dict]):
        from .cli_chat_rich import _replay_initial_history
        _replay_initial_history(self.console, initial)

    def _restore_reasoning_state(self, initial: list[dict]):
        from .cli_chat_rich import _restore_resume_reasoning_state
        _restore_resume_reasoning_state(
            initial, self.reasoning_state,
            width=max(40, _terminal_width()),
        )

    def _goodbye(self):
        self.console.print()
        if getattr(self, "exit_on_interrupt", False):
            import shlex
            command = ["igorek", "--profile", self.profile, "--resume", self.session_name]
            if self.data_dir:
                command.extend(["--data-dir", self.data_dir])
            self.console.print("Resume: " + shlex.join(command), markup=False)
        else:
            _print_resume_hint(self.session_name, profile=self.profile, data_dir=self.data_dir)

    # ── input ──────────────────────────────────────────────────

    def _read_input(self) -> Optional[str]:
        self._mouse_controller.set_wheel_handler(
            self._fs_scroll_by if self._tui_fullscreen else None
        )
        self._mouse_controller.set_capture_allowed(self._tui_fullscreen)
        if self._tui_fullscreen:
            return self._read_input_fullscreen()
        from .cli_chat_idle import (
            _IdleReasoningToggle,
            _create_idle_composer_application,
            _make_prompt_session,
        )
        while True:
            app, _buffer = _create_idle_composer_application(
                self.sl,
                reasoning_state=self.reasoning_state,
                reasoning_toggle_exits=False,
                mouse_controller=self._mouse_controller,
            )
            try:
                if app is not None:
                    text = str(app.run() or "").strip()
                    if text == "__mouse_viewer__" or self._mouse_controller.consume_viewer_request():
                        self._open_history_viewer()
                        continue
                    return text if text else None
                session = _make_prompt_session(
                    self.sl,
                    toolbar_enabled=True,
                    reasoning_state=self.reasoning_state,
                    reasoning_toggle_exits=False,
                )
                if session is None:
                    try:
                        return input("> ").strip()
                    except (EOFError, KeyboardInterrupt):
                        return None
                text = session.prompt().strip()
                return text if text else None
            except (_ReasoningToggle, _IdleReasoningToggle):
                return _REASONING_TOGGLE
            except (EOFError, KeyboardInterrupt):
                return None
            finally:
                self._mouse_controller.clear_app()

    def _read_input_fullscreen(self) -> Optional[str]:
        from .cli_chat_active import _create_chat_application

        running_state = {
            "task_active": False,
            "t0": time.monotonic(),
            "pending_count": self.mq.pending_count,
            "preview_items": self.mq.peek_all(),
            "reasoning_state": self.reasoning_state,
            "show_shortcuts": False,
            "spinner_idx": 0,
            "spinner_last_ts": 0,
        }
        app, _buffer, _lines = _create_chat_application(
            status_line=self.sl,
            running_state=running_state,
            fullscreen=True,
            transcript_provider=self._fullscreen_output_lines,
            transcript_regions_provider=self._fullscreen_regions,
            transcript_full_provider=self._fullscreen_transcript_lines,
            transcript_window_start_provider=self._fullscreen_window_start,
            mouse_controller=self._mouse_controller,
            on_scroll=self._fs_scroll_by,
            on_scroll_edge=self._fs_scroll_edge,
        )
        if app is None:
            return None
        try:
            text = str(app.run() or "").strip()
        except (EOFError, KeyboardInterrupt):
            return None
        finally:
            self._mouse_controller.clear_app()
        if not text or text == "__INTERRUPT__":
            return None
        return text

    # ── persistent fullscreen session ──────────────────────────

    def run_fullscreen(self) -> int:
        """Run the whole session inside one persistent fullscreen Application.

        This avoids the alternate-screen exit/enter that a per-turn
        Application causes (the main buffer briefly flashing between turns).
        Turns are executed in a worker thread and the running app is
        repainted in place via ``invalidate()``.
        """
        import asyncio

        from .cli_chat_active import _create_chat_application

        fullscreen = getattr(self, "_tui_fullscreen", True)
        self._used_fullscreen = fullscreen
        if fullscreen:
            self._seed_fullscreen_history_from_session()

        self._mouse_controller.set_wheel_handler(self._fs_scroll_by)
        self._mouse_controller.set_capture_allowed(fullscreen)
        if not hasattr(self, "_fs_submitted") or self._fs_submitted is None:
            self._fs_submitted = []
        self._fs_in_turn = False
        self._fs_external_app = None
        self._fs_loop = None

        running_state = {
            "task_active": False,
            "t0": time.monotonic(),
            "pending_count": self.mq.pending_count,
            "preview_items": self.mq.peek_all(),
            "reasoning_state": self.reasoning_state,
            "show_shortcuts": False,
            "spinner_idx": 0,
            "spinner_last_ts": 0,
            "queued_input_items": [],
            "escape_interrupts": True,
            "interrupted": False,
            "interrupting": False,
            "interrupt_pending": False,
            "task_finished": False,
            "current_task_id": None,
            "exit_on_interrupt": getattr(self, "exit_on_interrupt", False),
        }
        self._fs_running_state = running_state
        app, _buffer, _lines = _create_chat_application(
            status_line=self.sl,
            running_state=running_state,
            fullscreen=fullscreen,
            transcript_provider=self._fullscreen_output_lines if fullscreen else None,
            output_provider=self._live_widget_lines if not fullscreen else None,
            transcript_regions_provider=self._fullscreen_regions,
            transcript_full_provider=self._fullscreen_transcript_lines,
            transcript_window_start_provider=self._fullscreen_window_start,
            mouse_controller=self._mouse_controller,
            on_scroll=self._fs_scroll_by,
            on_scroll_edge=self._fs_scroll_edge,
            persistent=True,
            on_submit=self._fs_enqueue,
        )
        if app is None:
            self._tui_fullscreen = False
            return 0

        async def _session():
            self._fs_loop = asyncio.get_running_loop()
            app_task = asyncio.create_task(app.run_async())
            next_goal_check = 0.0
            try:
                while not app_task.done():
                    await asyncio.sleep(0.03)
                    while self._fs_submitted and not self._fs_in_turn:
                        text = self._fs_submitted.pop(0)
                        running_state["queued_input_items"] = list(self._fs_submitted)
                        await self._fs_run_turn(text, running_state, app)
                    if self._active and not self._fs_submitted and not self._fs_in_turn and time.monotonic() >= next_goal_check:
                        goal_prompt = self._next_goal_prompt()
                        next_goal_check = time.monotonic() + 0.5
                        if goal_prompt:
                            await self._fs_run_turn(goal_prompt, running_state, app, goal_turn=True)
                    if not self._active and app.is_running and not app.is_done:
                        app.exit()
            finally:
                if not app_task.done():
                    app_task.cancel()
                    try:
                        await app_task
                    except asyncio.CancelledError:
                        pass
            if app_task.done() and not app_task.cancelled():
                return app_task.result()
            return None

        try:
            result = asyncio.run(_session())
        except KeyboardInterrupt:
            result = "__INTERRUPT__"
        finally:
            self._fs_external_app = None
            self._fs_loop = None
            self._fs_in_turn = False
            self._mouse_controller.clear_app()

        if running_state.get("exit_requested"):
            self._active = False

        if isinstance(result, str) and result.startswith("__tui__:"):
            self._cmd_tui(result[len("__tui__:"):])
            if not self._tui_fullscreen:
                # Leaving fullscreen: do not replay the startup banner/help
                # into the terminal; show only the fresh chat surface.
                try:
                    self.console.clear()
                except Exception:
                    pass
        elif result == "__INTERRUPT__":
            self._active = False
        return 0

    def _seed_fullscreen_history_from_session(self) -> None:
        """Seed the fullscreen transcript with resumed session history."""
        if getattr(self, "_fs_history_cells", None):
            return
        if self.widget.committed_cells:
            return
        from .tui.cells.history_cell import (
            MarkdownCell,
            ReasoningCell,
            RestoredReasoningCell,
            TextCell,
        )

        try:
            entries = _load_entries(self.session_path)
        except Exception:
            entries = []
        cells: list = []
        for entry in entries:
            text = str(entry.get("text", "") or "")
            if not text:
                continue
            role = str(entry.get("role", ""))
            if role == "user":
                cells.append(TextCell(text, role="user"))
            elif role == "assistant":
                # Rebuild the turn from its trace so reasoning, tool calls and
                # intermediate answers keep their original order (question ->
                # reasoning -> tool -> ... -> answer) instead of piling all
                # reasoning at the bottom.
                trace_cells = self._trace_cells_for_task(entry.get("task_id"))
                if trace_cells:
                    cells.extend(trace_cells)
                    if not self._trace_cells_end_with_answer(trace_cells):
                        cells.append(MarkdownCell(text))
                else:
                    cells.append(MarkdownCell(text))
        rs = self.reasoning_state
        collapsed = list(rs.get("lines") or [])
        expanded = list(rs.get("expanded_lines") or [])
        if (collapsed or expanded) and not any(
            isinstance(c, ReasoningCell) for c in cells
        ):
            cells.append(RestoredReasoningCell(collapsed, expanded))
        if cells:
            self._fs_history_cells = cells

    def _trace_cells_for_task(self, task_id) -> list:
        """Rebuild one turn's structured history cells from its trace.

        Returns ``[]`` when the task has no trace (old sessions, fail-before-
        trace) so the caller can fall back to the session text.
        """
        from .cli_chat_active import _read_new_trace_events
        from .cli_chat_rich import _trace_path_for_task
        from .tui.cells.history_cell import TextCell
        from .tui.trace_reducer import TraceEventReducer

        task_id = str(task_id or "").strip()
        if not task_id:
            return []
        try:
            events, _seen = _read_new_trace_events(
                _trace_path_for_task(task_id), 0
            )
        except Exception:
            return []
        if not events:
            return []
        reducer = TraceEventReducer()
        reducer.apply_many(events)
        raw_cells = list(reducer.widget.committed_cells)
        if reducer.widget.active_cell is not None:
            raw_cells.append(reducer.widget.active_cell)
        cells: list = []
        for cell in raw_cells:
            if isinstance(cell, TextCell) and not str(
                getattr(cell, "text", "") or ""
            ).strip():
                continue
            cells.append(cell)
        return cells

    @staticmethod
    def _trace_cells_end_with_answer(cells: list) -> bool:
        """True when the trace already contains the turn's final answer."""
        from .tui.cells.history_cell import MarkdownCell, TextCell

        for cell in reversed(cells):
            if isinstance(cell, TextCell) and not str(
                getattr(cell, "text", "") or ""
            ).strip():
                continue
            return isinstance(cell, MarkdownCell)
        return False

    def _fs_enqueue(self, text: str) -> None:
        if not hasattr(self, "_fs_submitted") or self._fs_submitted is None:
            self._fs_submitted = []
        self._fs_submitted.append(text)
        running_state = getattr(self, "_fs_running_state", None)
        if isinstance(running_state, dict):
            # Expose the pending follow-up messages so the fullscreen "Working"
            # area can show that the new input was queued behind the active turn.
            running_state["queued_input_items"] = list(self._fs_submitted)

    async def _fs_run_turn(self, text: str, running_state: dict, app, goal_turn: bool = False) -> None:
        import asyncio

        if text.startswith("/"):
            # Local slash commands must not be sent to the model and may
            # print; run them with the app temporarily suspended.
            if text.strip() in ("/exit", "/quit"):
                self._active = False
                try:
                    app.exit()
                except Exception:
                    pass
                return
            if text.split()[0].lower() == "/profile":
                # Profile startup can block. Keep the composer attached and raw;
                # run_in_terminal would cook an immediately typed Enter into LF.
                def change_profile():
                    with self.console.capture() as captured:
                        self._handle_command(text)
                    from rich.text import Text
                    return Text.from_ansi(captured.get()).plain.strip()
                output = await asyncio.to_thread(change_profile)
                self._fs_command_lines = output.splitlines()
                if self._tui_fullscreen and output:
                    self.widget.committed_cells.append(TextCell(output))
                app.invalidate()
                return
            self._fs_external_app = app
            try:
                from prompt_toolkit.application import run_in_terminal

                await run_in_terminal(lambda: self._handle_command(text))
            except Exception:
                pass
            finally:
                self._fs_external_app = None
                try:
                    app.invalidate()
                except Exception:
                    pass
            return

        self._fs_in_turn = True
        self._fs_command_lines = []
        if not self._tui_fullscreen and not goal_turn:
            from prompt_toolkit.application import run_in_terminal
            await run_in_terminal(lambda: _user_msg(self.console, text))
        running_state["task_active"] = True
        running_state["t0"] = time.monotonic()
        running_state["interrupted"] = False
        running_state["interrupting"] = False
        running_state["interrupt_pending"] = False
        running_state["task_finished"] = False
        running_state["current_task_id"] = None
        self._fs_external_app = app
        try:
            if goal_turn:
                await asyncio.get_running_loop().run_in_executor(None, self._execute_user_turn, text, True)
            else:
                await asyncio.get_running_loop().run_in_executor(None, self._execute_user_turn, text)
        finally:
            self._fs_external_app = None
            self._fs_in_turn = False
            running_state["task_active"] = False
            running_state["interrupting"] = False
            running_state["task_finished"] = False
            running_state["preview_items"] = self.mq.peek_all()
            running_state["pending_count"] = self.mq.pending_count
            try:
                app.invalidate()
            except Exception:
                pass

    def _fs_run_on_terminal(self, func):
        """Run a console callable, temporarily suspending a persistent app."""
        app = getattr(self, "_fs_external_app", None)
        loop = getattr(self, "_fs_loop", None)
        if app is None or loop is None:
            return func()
        import asyncio

        try:
            from prompt_toolkit.application import run_in_terminal
        except Exception:
            return func()

        async def _do():
            # Run the callable in a worker thread: interactive forms start
            # their own event loop (asyncio.run), which is illegal on the
            # running TUI loop.
            return await run_in_terminal(func, in_executor=True)

        future = asyncio.run_coroutine_threadsafe(_do(), loop)
        try:
            return future.result(timeout=600)
        except Exception:
            return None

    # ── input dispatch ─────────────────────────────────────────

    def _handle_input(self, text: str):
        if text.startswith("/"):
            self._handle_command(text)
            return
        if bootstrap_active(self.data_dir):
            self._handle_bootstrap(text)
            return
        if self.mm.handle_explicit_user_memory(text, data_dir=self.data_dir)[0]:
            self._handle_memory_write(text)
            return
        self._execute_user_turn(text)

    def _handle_command(self, text: str):
        cmd = text.split()[0].lower()
        self.console.print()
        handler = {
            "/help": self._cmd_help,
            "/clear": self._cmd_clear,
            "/debug": self._cmd_debug,
            "/status": self._cmd_status,
            "/statusline": self._cmd_statusline,
            "/title": self._cmd_title,
            "/theme": self._cmd_theme,
            "/skills": self._cmd_skills,
            "/history": self._cmd_history,
            "/goal": self._cmd_goal,
            "/resume": self._cmd_resume,
            "/fork": self._cmd_fork,
            "/provider": self._cmd_provider,
            "/models": self._cmd_models,
            "/model": self._cmd_model,
            "/wally": self._cmd_wally,
            "/profile": self._cmd_profile,
            "/smarthelp": self._cmd_smarthelp,
            "/mouse": self._cmd_mouse,
            "/tui": self._cmd_tui,
        }.get(cmd)
        if handler:
            handler(text)
        else:
            _error_msg(self.console, f"Unknown command: {cmd}")
        self.console.print()

    def _next_goal_prompt(self) -> str:
        from . import thread_goals
        from .prompt_assembly import goal_continuation_prompt

        goal = thread_goals.get_goal(self.session_name)
        if not goal or goal["status"] != "active" or goal["surface"] != "cli_chat_rich":
            self._pending_goal_snapshot = None
            return ""
        self._pending_goal_snapshot = (goal["goal_id"], goal["revision"])
        return goal_continuation_prompt(goal["objective"])

    def _cmd_goal(self, text: str = ""):
        from . import thread_goals

        value = text[len("/goal"):].strip()
        current = thread_goals.get_goal(self.session_name)
        try:
            if not value or value == "status":
                goal = current
            elif value in {"pause", "resume"}:
                goal = thread_goals.update_goal(self.session_name, "paused" if value == "pause" else "active", by_user=True)
            elif value == "clear":
                thread_goals.clear_goal(self.session_name)
                self.console.print("Цель удалена.", markup=False)
                return
            elif value.startswith("edit "):
                goal = thread_goals.edit_goal(self.session_name, value[5:].strip())
            elif value == "edit":
                raise ValueError("после /goal edit укажите новый текст цели")
            else:
                goal = thread_goals.create_goal(self.session_name, value, surface="cli_chat_rich")
            if goal is None:
                self.console.print("Цель не задана. Используйте /goal и текст цели.", markup=False)
                return
            usage = str(goal["tokens_used"])
            if goal["token_budget"] is not None:
                usage += " / " + str(goal["token_budget"])
            self.console.print(f"Цель: {goal['objective']}\nСтатус: {goal['status']} · токены: {usage}", markup=False)
        except ValueError as exc:
            self.console.print(f"Не удалось изменить цель: {exc}", markup=False)

    def _cmd_help(self, text: str = ""):
        _print_help(self.console)

    def _cmd_clear(self, text: str = ""):
        _clear_session(self.session_path)
        self._fs_history_cells.clear()
        self._fs_scroll = {"offset": 0, "follow": True}
        self.console.print("  [green]Session history cleared.[/green]")

    def _cmd_debug(self, text: str = ""):
        self.show_debug = not self.show_debug
        self.console.print(f"  [dim]Debug: {'on' if self.show_debug else 'off'}[/dim]")

    def _cmd_status(self, text: str = ""):
        entries = _load_entries(self.session_path)
        self.sl.refresh()
        st = (
            f"[dim]Session:[/dim] {self.session_name}\n"
            f"[dim]Messages:[/dim] {len(entries)}\n"
            f"[dim]Profile:[/dim] {self.profile}\n"
            f"[dim]Model:[/dim] {self.model}\n"
            f"[dim]Status:[/dim] {self.sl.plain_text()}"
        )
        self.console.print(Padding(Panel(st, border_style="blue", padding=(0, 1)), (0, 0, 1, 0)))

    def _cmd_statusline(self, text: str = ""):
        parts = text.split()
        if len(parts) > 1 and parts[1] == "setup":
            from .cli_chat_rich import _statusline_setup
            _statusline_setup(self.console, self.sl)
            return
        if len(parts) > 1 and parts[1] in {"reset", "default"}:
            save_status_line_items([], settings_path=self.sl.settings_path)
            self.sl.refresh()
            self.console.print("  [green]Status line reset to builtin default.[/green]")
            return
        from .cli_chat_rich import _statusline_summary
        ct = _statusline_summary(self.sl)
        self.console.print(Padding(Panel(ct, border_style="blue", padding=(0, 1)), (0, 0, 1, 0)))

    def _cmd_title(self, text: str = ""):
        parts = text.split()
        if len(parts) > 1 and parts[1] == "setup":
            from .cli_chat_rich import _terminal_title_setup
            _terminal_title_setup(self.console, self.sl)
            return
        if len(parts) > 1 and parts[1] in {"reset", "default"}:
            save_terminal_title_items([], settings_path=self.sl.settings_path)
            self.console.print("  [green]Terminal title reset.[/green]")
            return
        from .cli_chat_rich import _terminal_title_summary
        ct = _terminal_title_summary(self.sl)
        self.console.print(Padding(Panel(ct, border_style="blue", padding=(0, 1)), (0, 0, 1, 0)))

    def _cmd_theme(self, text: str = ""):
        parts = text.split()
        if len(parts) > 1 and parts[1] in {"reset", "default"}:
            theme = save_theme_name(None, settings_path=self.sl.settings_path)
            self.console.print(f"  [green]Theme reset to {theme.name}.[/green]")
            return
        if len(parts) > 1 and parts[1] in {"status", "show"}:
            from .cli_chat_rich import _theme_summary
            ct = _theme_summary(self.sl)
            self.console.print(Padding(Panel(ct, border_style="blue", padding=(0, 1)), (0, 0, 1, 0)))
            return
        from .cli_chat_rich import _theme_setup
        _theme_setup(self.console, self.sl)

    def _cmd_skills(self, text: str = ""):
        parts = text.split(maxsplit=2)
        action = parts[1] if len(parts) > 1 else ""
        arg = parts[2] if len(parts) > 2 else ""
        try:
            from . import cli_skills
            if action in {"", "status", "show", "list"}:
                from .cli_chat_rich import _skills_summary
                ct = _skills_summary(data_dir=self.data_dir)
                self.console.print(Padding(Panel(ct, border_style="blue", padding=(0, 1)), (0, 0, 1, 0)))
            elif action == "activate-local":
                result = cli_skills.activate_local_skills(data_dir=self.data_dir)
                self.console.print(f"  [green]{result['message']}[/green]")
            elif action in {"enable", "disable"} and arg:
                enabled = action == "enable"
                result = cli_skills.set_skill_enabled(arg, enabled=enabled, data_dir=self.data_dir)
                state = "enabled" if enabled else "disabled"
                self.console.print(f"  [green]Skill {result['skill_id']} {state}.[/green]")
            elif action == "manage":
                from .cli_chat_rich import _skills_manage
                _skills_manage(self.console, data_dir=self.data_dir)
            else:
                _error_msg(self.console,
                    "Usage: /skills [status|list|manage|activate-local|enable <id>|disable <id>]")
        except Exception as exc:
            _error_msg(self.console, str(exc))

    def _cmd_history(self, text: str = ""):
        entries = _load_entries(self.session_path)
        if not entries:
            self.console.print("  [dim]History is empty.[/dim]")
        else:
            for e in entries[-20:]:
                role = e.get("role", "?")
                t = _truncate(str(e.get("text", "")), 120)
                if role == "user":
                    self.console.print(f"  [dim]>[/dim] [bright_cyan]{t}[/bright_cyan]")
                else:
                    self.console.print(f"  {t}")

    def _session_feedback(self, message: str) -> None:
        """Show a short message in fullscreen (notification) or classic mode."""
        if self._tui_fullscreen:
            try:
                self._mouse_controller.set_notification(message)
            except Exception:
                pass
        else:
            self.console.print(f"  [green]{message}[/green]")

    def _switch_session(self, session_id: str, *, announce: bool = True) -> bool:
        """Load an existing session into this running chat (in-process resume).

        Resets transcript, reasoning and status-line state, then re-seeds the
        history from the chosen session file. Returns ``False`` when the
        session is already active or the file does not exist.
        """
        session_id = _sanitize_session_name(session_id)
        if session_id == self.session_name:
            if announce:
                self._session_feedback("Already in this session.")
            return False
        new_path = _session_path(session_id)
        if not new_path.exists():
            _error_msg(self.console, f"Session not found: {session_id}")
            return False

        self.session_name = session_id
        self.session_path = new_path

        self.widget = ChatWidget(
            model=self.model, branch=self.branch, cwd=self.cwd
        )
        self._emitted_line_count = 0
        self._fs_history_cells = []
        self._fs_scroll = {"offset": 0, "follow": True}
        self.reasoning_state = {"visible": False, "lines": [], "expanded_lines": []}
        self.shown_edit_diff_signatures = set()
        self.last_worked_for = ""
        self.pending_inputs = []
        self.approved_session_rules = []
        self._fs_submitted = []

        self.sl.session_name = session_id
        self.sl.session_path = new_path
        self.sl.task_ids = []

        entries = _load_entries(new_path)
        for entry in entries:
            tid = str(entry.get("task_id") or "").strip()
            if tid and tid not in self.sl.task_ids:
                self.sl.task_ids.append(tid)
        self.sl.refresh()
        self._restore_reasoning_state(entries)

        if self._tui_fullscreen:
            self._seed_fullscreen_history_from_session()
        else:
            self._print_resume_history_header([f"{len(entries)} messages"])
            self._replay_initial_history(entries)

        if announce:
            self._session_feedback(f"Resumed {session_id}")
        return True

    def _cmd_resume(self, text: str = ""):
        arg = text[len("/resume"):].strip()
        if arg:
            self._switch_session(arg)
            return
        from .cli_chat import list_sessions as _list_sessions
        from .tui.resume_picker import run_resume_picker, SessionPickerAction, SessionSelection
        sessions = _list_sessions()
        if not sessions:
            self.console.print("  [dim]No past sessions found.[/dim]")
            return
        sel, chosen = run_resume_picker(sessions, action=SessionPickerAction.RESUME)
        self.console.print()
        if sel == SessionSelection.EXIT:
            self._active = False
            return
        if sel == SessionSelection.START_FRESH or chosen is None:
            self.console.print("  [dim]Starting fresh session.[/dim]")
            return
        self._switch_session(chosen["session_id"])

    def _cmd_fork(self, text: str = ""):
        from .cli_chat import (list_sessions as _list_sessions,
                                fork_session as _fork_session,
                                _session_path as _sp_fn)
        from .tui.resume_picker import run_resume_picker, SessionPickerAction, SessionSelection
        sessions = _list_sessions()
        if not sessions:
            self.console.print("  [dim]No past sessions to fork from.[/dim]")
            return
        sel, chosen = run_resume_picker(sessions, action=SessionPickerAction.FORK)
        self.console.print()
        if sel == SessionSelection.EXIT:
            self._active = False
            return
        if sel == SessionSelection.START_FRESH or chosen is None:
            self.console.print("  [dim]Cancelled.[/dim]")
            return
        new_name = _fork_session(_sp_fn(chosen["session_id"]))
        if new_name:
            self.console.print(f"  [green]Forked to {new_name}[/green]")
            self.console.print(
                f"  [dim]Resume with: igorek-factory chat --resume {new_name} "
                f"--profile {self.profile}[/dim]"
            )
        else:
            self.console.print("  [dim]Source session is empty, nothing to fork.[/dim]")

    def _cmd_provider(self, text: str = ""):
        a = text[len("/provider"):].strip()
        if not a:
            pd = "YOUR_PC"
            if self.pc.get("backend") != "gguf":
                try:
                    from .codex_provider import user_facing_codex_provider
                    pd = user_facing_codex_provider(self.pc.get("provider"))
                except Exception:
                    pass
            self.console.print(f"  [dim]Provider:[/dim] {pd}")
        else:
            if a not in {"openai", "openrouter"}:
                _error_msg(self.console, "Invalid provider. Available: openai, openrouter")
            else:
                self.console.print(f"  [green]Provider → {a}[/green]")

    def _cmd_models(self, text: str = ""):
        try:
            from .codex_provider import (model_catalog_for_provider,
                                          user_facing_codex_provider)
            catalog_provider = ("YOUR_PC" if self.pc.get("backend") == "gguf"
                                else self.pc.get("provider"))
            models = model_catalog_for_provider(catalog_provider)
            provider_name = user_facing_codex_provider(catalog_provider)
            if not models:
                self.console.print(f"  [dim]No recommended models for {provider_name}.[/dim]")
            elif text == "/models recommend":
                from .cli_chat_rich import _model_recommendation_prompt
                api_fmt = ("auto" if str(catalog_provider or "").lower()
                           in {"opencode", "opencode-go", "minimax", "glm"}
                           else self.pc.get("api_format"))
                selected = _model_recommendation_prompt(
                    self.console, current_model=self.model,
                    provider=catalog_provider, api_format=api_fmt,
                )
                if selected is not None:
                    self.model = selected
                    self.model_override = self._apply_session_model(selected)
            else:
                tbl = Table(show_lines=False, show_header=False, padding=(0, 1))
                tbl.add_column(style="bright_white")
                tbl.add_column(style="cyan")
                tbl.add_column(style="green", width=2)
                for item in models:
                    mid = str(item.get("id") or "")
                    transport = str(item.get("transport") or "unknown")
                    tbl.add_row(mid, transport, "●" if mid == self.model else "")
                self.console.print(Padding(tbl, (0, 0, 1, 2)))
        except Exception as exc:
            _error_msg(self.console, str(exc))

    def _cmd_wally(self, text: str = ""):
        """Display Wally account state (email, plan, balance)."""
        try:
            from .wally_auth import WallyAuthError, wally_account_summary_lines

            for line in wally_account_summary_lines():
                self.console.print(f"  [cyan]{line}[/cyan]")
        except WallyAuthError as exc:
            _error_msg(self.console, f"Wally: {exc}")
        except Exception as exc:  # Network parsing should not disrupt chat functionality.
            _error_msg(self.console, f"Wally: {exc}")

    def _cmd_model(self, text: str = ""):
        a = text[len("/model"):].strip()
        if not a:
            self.console.print(f"  [dim]Model:[/dim] {self.model}")
        elif a.lower() in {"reset", "default"}:
            self.model = profile_model_name(get_profile_config(self.profile))
            self.model_override = self._apply_session_model(self.model)
            self.console.print(f"  [green]Model reset:[/green] {self.model}")
        else:
            from .codex_provider import (model_is_compatible_with_profile,
                                          model_transport_for_provider)
            profile_api_fmt = ("auto" if str(
                self.pc.get("provider") or "").strip().lower()
                in {"opencode", "opencode-go", "minimax", "glm"}
                else self.pc.get("api_format"))
            if not model_is_compatible_with_profile(
                self.pc.get("provider"), a, api_format=profile_api_fmt,
            ):
                transport = (model_transport_for_provider(self.pc.get("provider"), a)
                             or "unknown")
                _error_msg(self.console,
                    f"Model {a} uses {transport} transport and "
                    f"is not compatible with this profile.")
                return
            self.model = a
            self.model_override = self._apply_session_model(self.model)
            self.console.print(f"  [green]Model for this session → {self.model}[/green]")

    def _apply_session_model(self, selected_model: str) -> Optional[str]:
        from .cli_chat_rich import _apply_session_model
        model_override = _apply_session_model(
            self.sl, profile_model=profile_model_name(get_profile_config(self.profile)),
            selected_model=selected_model,
        )
        self.widget.model = selected_model
        return model_override

    def _cmd_profile(self, text: str = ""):
        a = text[len("/profile"):].strip()
        if not a:
            self.console.print(f"  [dim]Profile:[/dim] {self.profile}")
            from .runtime import get_profiles_dir, list_profile_names
            for name in list_profile_names(get_profiles_dir()):
                self.console.print("  " + name, markup=False)
            self.console.print("  /profile ИМЯ — переключить профиль", markup=False)
        else:
            try:
                config = get_profile_config(a)
                if getattr(self, "on_profile_change", None) is not None:
                    self.on_profile_change(a)
            except (OSError, ValueError, RuntimeError) as error:
                _error_msg(self.console, str(error))
                return
            self.profile, self.pc = a, config
            self.model = profile_model_name(config)
            self.model_override = None
            self.approved_session_rules = []
            self.sl.profile, self.sl.profile_config, self.sl.model = a, config, self.model
            self.widget.model = self.model
            from .cli_chat import update_session_meta
            update_session_meta(self.session_path, {"profile": a, "model": self.model,
                                                   "provider": str(config.get("provider", ""))})
            self.console.print(f"  [green]Profile → {a}[/green]")

    def _cmd_smarthelp(self, text: str = ""):
        q = text[len("/smarthelp"):].strip()
        if not q:
            self.console.print("  [dim]Usage: /smarthelp <question>[/dim]")
            return
        try:
            from .smarthelp import smarthelp_agent_query, SmarthelpError, build_tool_preview
            self.console.print("  [dim]→ OpenRouter agent...[/dim]")
            def _shp(et: str, **kw):
                if et != "tool.started":
                    return
                nm = str(kw.get("tool_name") or kw.get("name") or "tool")
                a = kw.get("args") if isinstance(kw.get("args"), dict) else {}
                pv = str(kw.get("preview") or build_tool_preview(nm, a)).strip()
                sfx = f"  [dim]{pv}[/dim]" if pv else ""
                self.console.print(f"  [blue]•[/blue] Ran [bold]{nm}[/bold]{sfx}")
            ans, ll, _ = smarthelp_agent_query(
                q, workdir=self.cwd, progress_callback=_shp,
            )
            print(f"[cc-local] {ll}", flush=True)
            self.console.print()
            _assistant_msg(self.console, ans)
        except SmarthelpError as exc:
            _error_msg(self.console, str(exc))
        except Exception as exc:
            _error_msg(self.console, str(exc))

    def _handle_bootstrap(self, text: str):
        _user_msg(self.console, text)
        _append_entry(self.session_path, role="user", text=text, status="bootstrap")
        outcome = process_bootstrap_answer(text, self.data_dir)
        _append_entry(
            self.session_path, role="assistant",
            text=outcome["message"], status="bootstrap",
        )
        _assistant_msg(self.console, outcome["message"])

    def _handle_memory_write(self, text: str):
        _user_msg(self.console, text)
        _append_entry(self.session_path, role="user", text=text)
        _, at, mw = self.mm.handle_explicit_user_memory(text, data_dir=self.data_dir)
        _append_entry(
            self.session_path, role="assistant", text=at or "",
            status="memory_write", extra={"memory_writes": mw},
        )
        _assistant_msg(self.console, at or "")

    # ── rendering ──────────────────────────────────────────────

    def _render_widget(self):
        if self._tui_fullscreen:
            # Fullscreen mode owns the viewport; nothing is printed to the
            # terminal scrollback. The app reads the transcript directly.
            return
        def emit():
            lines = self._scrollback_widget_lines()
            new = lines[self._emitted_line_count:]
            if new:
                for line in new:
                    self.console.print(f" {line}", markup=False)
                self._emitted_line_count = len(lines)
        if (getattr(self, "_fs_external_app", None) is not None
                and getattr(self, "_fs_loop", None) is not None
                and threading.current_thread() is not threading.main_thread()):
            self._fs_run_on_terminal(emit)
        else:
            emit()

    def _has_unemitted_widget_lines(self) -> bool:
        return len(self._scrollback_widget_lines()) > self._emitted_line_count

    def _scrollback_widget_lines(self) -> list[str]:
        """Renders a frozen history without reasoning cells.

        Reasoning is a toggleable UI state. It cannot be printed to the terminal scrollback because scrollback only appends and does not reliably edit existing content. A live prompt message renders reasoning itself, while prompt_toolkit redraws it on Ctrl+O.
        """
        from .tui.cells.history_cell import ReasoningCell

        width = _terminal_width()
        original_cells = self.widget.committed_cells
        active_cell = self.widget.active_cell
        bottom_pane = self.widget.bottom_pane
        try:
            self.widget.committed_cells = [
                cell for cell in original_cells
                if not isinstance(cell, ReasoningCell)
            ]
            self.widget.active_cell = None
            self.widget.bottom_pane = None
            buf: list[str] = []
            self.widget.render(Rect(0, 0, width, 1000), buf)
            return _with_scrollback_activity_gaps(buf)
        finally:
            self.widget.active_cell = active_cell
            self.widget.bottom_pane = bottom_pane
            self.widget.committed_cells = original_cells

    def _live_widget_lines(self) -> list[str]:
        """Renders only the modifiable active cell for the prompt_toolkit viewport.

        This mirrors the Codex TUI split: a frozen history is inserted into the terminal scrollback, while the live viewport maintains the current active cell and the prompt’s lower area.
        """
        from .tui.cells.history_cell import ReasoningCell

        active = self.widget.active_cell
        if active is None or isinstance(active, ReasoningCell):
            return list(getattr(self, "_fs_command_lines", []))
        width = _terminal_width()
        history = self.widget._HistoryArea([], active)
        lines = history.display_lines(width)
        if (
            self._emitted_line_count > 0
            and lines
            and lines[0] != ""
            and _is_activity_header_line(lines[0])
        ):
            return ["", *lines]
        return lines

    def _render_widget_lines(self) -> list[str]:
        buf = self._scrollback_widget_lines()
        new = buf[self._emitted_line_count:]
        return [f" {l}" for l in new]

    # ── fullscreen transcript (experimental /tui fullscreen) ───

    def _styled_user_lines(self, text: str, width: int) -> list[str]:
        """Render a user message with a lighter background and a '›' marker."""
        from .tui.mouse_frame import display_width

        bg = "\x1b[48;2;58;47;92m"
        fg = "\x1b[38;2;235;232;250m"
        reset = "\x1b[0m"
        raw_lines = (text or "").splitlines() or [""]
        out: list[str] = []
        for i, raw in enumerate(raw_lines):
            prefix = "› " if i == 0 else "  "
            content = prefix + raw
            pad = max(0, width - 2 - display_width(content))
            out.append(bg + fg + content + (" " * pad) + reset)
        return out

    def _fullscreen_transcript_data(self) -> tuple[list[str], list[dict]]:
        """Lines + structural spans for the fullscreen viewport.

        Spans carry a stable region kind/id so the mouse controller can build
        interactive zones without scanning rendered text. ``start``/``end``
        are line indices in the returned full transcript.
        """
        from .tui.cells.history_cell import (
            ReasoningCell,
            ToolCallCell,
            ToolCallGroupCell,
        )
        from .tui.exec_cell.render import render_tool_call

        width = _terminal_width()
        show_reasoning = bool(self.reasoning_state.get("visible"))
        reasoning_overrides = self.reasoning_state.get("cell_overrides") or {}
        lines: list[str] = list(getattr(self, "_startup_lines", []) or [])
        if lines:
            lines.append("")
        spans: list[dict] = []
        prev_type = None

        def _emit(cell, idx: int) -> None:
            nonlocal prev_type
            cur_type = type(cell).__name__
            needs_spacing = bool(prev_type and cur_type != prev_type)
            if prev_type in {"ToolCallCell", "ToolCallGroupCell"} and cur_type in {
                "ToolCallCell",
                "ToolCallGroupCell",
            }:
                needs_spacing = True
            if needs_spacing and lines and lines[-1] != "":
                lines.append("")
            start = len(lines)
            is_user = getattr(cell, "role", None) == "user"
            reasoning_cid = ""
            if isinstance(cell, ReasoningCell):
                reasoning_cid = f"reasoning-{idx}"
                use_expanded = bool(
                    reasoning_overrides.get(reasoning_cid, show_reasoning)
                )
            else:
                use_expanded = False
            if is_user:
                sub = self._styled_user_lines(getattr(cell, "text", ""), width)
            elif use_expanded:
                sub = cell.expanded_display_lines(width)
                if sub and sub[0] == "" and lines and lines[-1] == "":
                    sub = sub[1:]
            elif isinstance(cell, ToolCallCell):
                cid = cell.cell_id or f"tool-{idx}"
                mc = getattr(self, "_mouse_controller", None)
                cell.expanded = bool(mc and mc.is_cell_expanded(cid))
                sub = render_tool_call(
                    cell.tool_name,
                    cell.tool_input,
                    cell.status or None,
                    cell.elapsed if cell.elapsed else None,
                    cell.output or None,
                    cell.is_running,
                    width=width,
                    expanded=cell.expanded,
                )
            else:
                sub = cell.display_lines(width)
            lines.extend(sub)
            end = len(lines) - 1
            if isinstance(cell, ReasoningCell):
                kind, cid, expandable = "reasoning", reasoning_cid, True
            elif isinstance(cell, ToolCallCell):
                kind = "tool"
                cid = cell.cell_id or f"tool-{idx}"
                expandable = bool(cell.output) and not cell.is_running
            elif isinstance(cell, ToolCallGroupCell):
                kind, cid, expandable = "tool", f"group-{idx}", False
            else:
                kind, cid, expandable = "", "", False
            if kind:
                spans.append(
                    {
                        "start": start,
                        "end": end,
                        "kind": kind,
                        "cell_id": cid,
                        "expandable": expandable,
                    }
                )
            prev_type = cur_type

        all_cells = list(getattr(self, "_fs_history_cells", []) or []) + list(
            self.widget.committed_cells
        )
        for idx, cell in enumerate(all_cells):
            _emit(cell, idx)
        if self.widget.active_cell is not None:
            _emit(self.widget.active_cell, -1)
        return lines, spans

    def _fullscreen_transcript_lines(self) -> list[str]:
        return self._fullscreen_transcript_data()[0]

    def _fullscreen_regions(self) -> list[dict]:
        """Structural spans mapped to the currently visible window rows."""
        _full_lines, spans = self._fullscreen_transcript_data()
        lines, height, total, offset = self._fullscreen_view()
        start = max(0, total - height - offset)
        out: list[dict] = []
        for span in spans:
            s = int(span.get("start", 0)) - start
            e = int(span.get("end", -1)) - start
            if e < 0 or s >= len(lines):
                continue
            s = max(0, s)
            e = min(len(lines) - 1, e)
            if e < s:
                continue
            out.append({**span, "start": s, "end": e})
        return out

    def _fullscreen_view(self) -> tuple[list[str], int, int, int]:
        import shutil

        full = self._fullscreen_transcript_lines()
        total = len(full)
        term_rows = shutil.get_terminal_size((80, 24)).lines
        # Conservative reserve for separator + composer + footer (+ working).
        # Keeping the slice shorter than the real window avoids clipping the
        # most recent (bottom) lines; blank space below is acceptable.
        reserved = 8
        height = max(1, term_rows - reserved)
        offset = int(self._fs_scroll.get("offset", 0))
        if self._fs_scroll.get("follow", True):
            offset = 0
        offset = max(0, min(offset, max(0, total - height)))
        start = max(0, total - height - offset)
        return full[start:start + height], height, total, offset

    def _fullscreen_output_lines(self) -> list[str]:
        lines, _h, _t, _o = self._fullscreen_view()
        return lines

    def _fullscreen_window_start(self) -> int:
        full = self._fullscreen_transcript_lines()
        total = len(full)
        import shutil

        term_rows = shutil.get_terminal_size((80, 24)).lines
        height = max(1, term_rows - 8)
        offset = int(self._fs_scroll.get("offset", 0))
        if self._fs_scroll.get("follow", True):
            offset = 0
        offset = max(0, min(offset, max(0, total - height)))
        return max(0, total - height - offset)

    def _fs_scroll_by(self, delta: int) -> None:
        full = self._fullscreen_transcript_lines()
        _lines, height, total, offset = self._fullscreen_view()
        max_offset = max(0, total - height)
        new_offset = max(0, min(offset + delta, max_offset))
        self._fs_scroll["offset"] = new_offset
        self._fs_scroll["follow"] = new_offset == 0

    def _fs_scroll_edge(self, where: str) -> None:
        full = self._fullscreen_transcript_lines()
        _lines, height, total, _offset = self._fullscreen_view()
        max_offset = max(0, total - height)
        if where == "top":
            self._fs_scroll["offset"] = max_offset
            self._fs_scroll["follow"] = False
        elif where == "bottom":
            self._fs_scroll["offset"] = 0
            self._fs_scroll["follow"] = True

    def _cmd_tui(self, text: str = ""):
        arg = text[len("/tui"):].strip().lstrip("/").strip().lower()
        if arg in ("fullscreen", "full"):
            self._tui_fullscreen = True
            self._fs_scroll = {"offset": 0, "follow": True}
            # Do not print to the terminal scrollback: the line would briefly
            # flash on screen between fullscreen/alternate-screen transitions.
            self._mouse_controller.set_notification("TUI: fullscreen")
        elif arg in ("classic", "default", "inline", "off"):
            self._tui_fullscreen = False
            self._mouse_controller.set_notification("TUI: classic")
        elif not arg or arg == "status":
            message = f"TUI: {'fullscreen' if self._tui_fullscreen else 'classic'}"
            if self._tui_fullscreen:
                self._mouse_controller.set_notification(message)
            else:
                self.console.print(f"  [dim]{message}[/dim]")
        else:
            self.console.print(
                "  [dim]Использование: /tui [fullscreen|classic|status][/dim]"
            )

    # ── task execution ─────────────────────────────────────────

    def _execute_user_turn(self, text: str, goal_turn: bool = False):
        from . import thread_goals

        goal = thread_goals.get_goal(self.session_name)
        if goal_turn:
            expected = getattr(self, "_pending_goal_snapshot", None)
            if (not goal or goal["status"] != "active" or goal["surface"] != "cli_chat_rich"
                    or (expected is not None and expected != (goal["goal_id"], goal["revision"]))):
                return
        bound_goal = goal if goal and goal["status"] == "active" and goal["surface"] == "cli_chat_rich" else None
        sp = self.session_path
        self._snapshot_viewer_items()
        if self._tui_fullscreen:
            # Fullscreen owns its own transcript; keep previous turns so the
            # conversation does not disappear when a new turn starts.
            self._fs_history_cells.extend(self.widget.committed_cells)
            if len(self._fs_history_cells) > 4000:
                self._fs_history_cells = self._fs_history_cells[-4000:]
        self.widget.committed_cells.clear()
        self.widget.active_cell = None
        self._emitted_line_count = 0
        self.widget._show_reasoning = False
        self.reasoning_state = {"visible": False, "lines": [], "expanded_lines": []}
        from .cli_chat_rich import _configured_terminal_title, _set_terminal_title
        _set_terminal_title(_configured_terminal_title(self.sl, text))

        prompt_text, attached_files = self._extract_images(text)
        if attached_files:
            self._print_attached_files(attached_files)
        prompt_user_text = (prompt_text.strip() or
                            ("[attached image]" if attached_files else text))
        if self._tui_fullscreen and not goal_turn:
            # In fullscreen the transcript is rendered in-app, so the user's
            # message must be part of the committed cells to be visible.
            self.widget.committed_cells.append(
                TextCell(prompt_user_text, role="user")
            )

        entries = _load_entries(sp)
        prompt = self._build_prompt(entries, prompt_user_text)
        meta = {
            "source": "cli_chat_rich", "created_via": "igorek-factory chat",
            "session": self.session_name, "cwd": self.cwd,
            "approved_session_rules": list(self.approved_session_rules),
        }
        if bound_goal:
            meta.update(goal_id=bound_goal["goal_id"], goal_revision=bound_goal["revision"])
            if goal_turn:
                meta["goal_continuation"] = True
        if self.model_override:
            meta["model_override"] = self.model_override
        from .runtime import profile_stable_prompt_prefix

        if profile_stable_prompt_prefix(self.pc):
            # Local stable-prefix profiles: replay history as real messages so
            # the system prompt stays byte-identical across turns.
            history = _history_tail(entries, max_messages=30, max_chars=50_000)
            meta["history_messages"] = [
                {
                    "role": "user" if str(e.get("role")) == "user" else "assistant",
                    "content": str(e.get("text") or ""),
                }
                for e in history
                if str(e.get("text") or "").strip()
            ]

        turn_user_persisted = goal_turn
        last_approval_history_text = ""
        approval_context_messages: list[dict] = []
        last_worked_for = self.last_worked_for

        while True:
            task = self._execute_one_turn(prompt, meta, attached_files)

            if task is None:
                if goal_turn and bound_goal:
                    try:
                        thread_goals.update_goal(self.session_name, "blocked", expected_goal_id=bound_goal["goal_id"],
                                                 expected_revision=bound_goal["revision"])
                    except (ValueError, thread_goals.GoalConflict):
                        pass
                self.pending_inputs.extend(self.mq.drain_all())
                return

            last_worked_for = str(task.get("_worked_for_label") or last_worked_for)
            self._update_reasoning_state(task)
            task_id = str(task.get("task_id", "")) or None
            status = str(task.get("status", "")) or None
            approval_blocked = task.get("approval_blocked_tools", [])
            if isinstance(approval_blocked, list):
                approval_blocked = [item for item in approval_blocked if isinstance(item, dict)]

            if not (isinstance(approval_blocked, list) and approval_blocked):
                break

            # Merge protocol context before collecting each decision once.
            from .cli_chat_rich import _approval_text_for_history
            from .cli_chat_approval import (
                _blocked_tool_session_rule,
                _print_approval_history,
            )
            for blocked in approval_blocked:
                if not isinstance(blocked, dict):
                    continue
                approval_context_messages = self._merge_approval_context(
                    approval_context_messages,
                    blocked.get("approval_context_messages", []),
                )
            history_text = _approval_text_for_history(task, approval_blocked)

            if not turn_user_persisted:
                _append_entry(sp, role="user", text=text)
                turn_user_persisted = True
            if history_text and history_text != last_approval_history_text:
                _append_entry(sp, role="assistant", text=history_text,
                               task_id=task_id, status=status)
                last_approval_history_text = history_text

            self._fs_run_on_terminal(lambda: self.console.print())

            approved_signatures = []
            approved_host_ops: list[dict] = []
            ftc = None
            for blocked in approval_blocked:
                if not isinstance(blocked, dict):
                    continue
                tn = "host-op" if is_host_op_blocked_record(blocked) else str(blocked.get("tool") or "?")
                target = _print_blocked_tool_target(blocked)
                aa = self._ask_approval(blocked)
                if is_host_op_blocked_record(blocked):
                    # Host ops are resolved by the host broker, so the TUI
                    # carries the decision in the resume metadata instead of
                    # an approval signature.
                    if aa in {"allow", "allow_session"}:
                        decision = "allow_session" if aa == "allow_session" else "allow_once"
                        try:
                            request = parse_host_op_blocked_record(blocked)
                        except HostOpApprovalError:
                            self.console.print(
                                "  [red]Некорректная заявка host-op пропущена (нет действия).[/red]"
                            )
                        else:
                            approved_host_ops.append(
                                HostOpApprovalOutcome.pending_broker(
                                    request, decision=decision, resolved_by="tui"
                                ).to_record()
                            )
                    self._fs_run_on_terminal(
                        lambda: _print_approval_history(self.console, tn, target, aa)
                    )
                    continue
                if aa in {"allow", "allow_session"}:
                    sig = str(blocked.get("signature") or "").strip()
                    if sig:
                        approved_signatures.append(sig)
                    ftc = _forced_tool_call_for_resume(
                        blocked,
                        approval_context_messages,
                    )
                    if aa == "allow_session":
                        session_rule = _blocked_tool_session_rule(blocked)
                        if (session_rule and
                            session_rule not in self.approved_session_rules):
                            self.approved_session_rules.append(session_rule)
                self._fs_run_on_terminal(
                    lambda: _print_approval_history(self.console, tn, target, aa)
                )

            if not approved_signatures and not approved_host_ops:
                from .cli_chat_rich import _extract_rejection_context
                rejection_ctx = _extract_rejection_context(approval_blocked)
                rejection_text = (rejection_ctx
                                  if rejection_ctx
                                  else "Action rejected by user.")
                if not turn_user_persisted:
                    _append_entry(sp, role="user", text=text)
                    turn_user_persisted = True
                if (rejection_text and
                    rejection_text != last_approval_history_text):
                    _append_entry(sp, role="assistant", text=rejection_text,
                                  task_id=task_id, status=status)
                self.pending_inputs.extend(self.mq.drain_all())
                return

            if approved_signatures:
                meta["approved_tool_calls"] = approved_signatures
            if approved_host_ops:
                meta["host_op_resolutions"] = approved_host_ops
            meta["approved_session_rules"] = list(self.approved_session_rules)
            resume_context = _approval_context_for_resume(approval_context_messages)
            if resume_context:
                meta["approval_context_messages"] = resume_context
            if ftc is not None:
                meta["forced_tool_call"] = ftc

        raw_text = str(task.get("result_text") or
                       task.get("output_text") or
                       task.get("error_message") or "")
        at, _ = extract_memory_write_blocks(raw_text)
        at = strip_memory_status_lines(at)
        at = _filter_control_chars(at)

        persisted = self.mm.review_turn(
            user_text=text, assistant_text=at, profile=self.profile,
            data_dir=self.data_dir, legacy_raw_text=raw_text,
            log_fn=(lambda m: self.console.print(f"  [dim]debug: {m}[/dim]")
                    if self.show_debug else None),
        )

        if not turn_user_persisted:
            _append_entry(sp, role="user", text=text)
        _append_entry(sp, role="assistant", text=at, task_id=task_id,
                       status=status, extra={"memory_writes": persisted})

        if bound_goal and task_id:
            tokens = max(0, int(task.get("usage_prompt") or 0)) + max(0, int(task.get("usage_completion") or 0))
            started, completed = task.get("started_at"), task.get("completed_at")
            seconds = max(0, int(completed) - int(started)) if started and completed else 0
            try:
                thread_goals.account_usage(self.session_name, bound_goal["goal_id"], task_id,
                                           tokens=tokens, seconds=seconds, empty=status == "ok" and not at.strip(),
                                           failed=status != "ok", revision=bound_goal["revision"])
            except thread_goals.GoalConflict:
                pass

        if getattr(self, "_fs_external_app", None) is None:
            if not task.get("_had_streaming_output"):
                if task.get("status") == "ok":
                    _assistant_msg(self.console, at)
                else:
                    _error_msg(self.console, at or "Task failed")
            self._notify_turn_complete()
            self.console.print()
        else:
            self._notify_turn_complete()
        self.pending_inputs.extend(self.mq.drain_all())

    def _execute_one_turn(
        self, prompt: str, meta: dict, input_files: Optional[list] = None,
    ) -> Optional[dict]:
        if getattr(self, "before_turn", None) is not None:
            self.before_turn(self.profile)
        creator = getattr(self, "submit_task", None) or create_task
        created = creator(
            text=prompt, profile=self.profile, metadata=meta,
            input_files=list(input_files or []),
        )
        task_id = str(created["task_id"])
        self._current_task_id = task_id
        # Expose the live task id so Esc in the persistent fullscreen app can
        # cancel exactly this task.
        fs_state = getattr(self, "_fs_running_state", None)
        if isinstance(fs_state, dict):
            fs_state["current_task_id"] = task_id
            fs_state["task_finished"] = False
            if fs_state.pop("interrupt_pending", False):
                # Esc arrived before this task was created; cancel it now.
                try:
                    cancel_task(task_id)
                except Exception:
                    pass
        trace_path = _trace_path_for_task(task_id)
        final_states = {"ok", "fail", "dead_letter", "cancelled"}
        turn_started = time.monotonic()
        if getattr(self, "_fs_external_app", None) is None:
            self.sl.refresh()

        reducer = TraceEventReducer(
            widget=self.widget,
            shown_edit_diff_signatures=self.shown_edit_diff_signatures,
        )
        done_event = threading.Event()
        task_result: list[Optional[dict]] = [None]
        error: list[Optional[Exception]] = [None]
        trace_queue = queue.Queue()

        def poll():
            seen = 0
            try:
                while not done_event.is_set():
                    evs, seen = _read_new_trace_events(trace_path, seen)
                    if evs:
                        trace_queue.put(evs)
                    task = get_task(task_id)
                    if task is None:
                        error[0] = RuntimeError(f"Task not found: {task_id}")
                        done_event.set()
                        return
                    if task.get("status") in final_states:
                        # Disable the "[Interrupted...]" indicator once the task completes, even if turn finalization (history, rendering) takes additional time.
                        fs_state = getattr(self, "_fs_running_state", None)
                        if isinstance(fs_state, dict):
                            fs_state["interrupting"] = False
                            fs_state["task_finished"] = True
                        deadline = time.monotonic() + 1.0
                        while time.monotonic() < deadline:
                            evs, seen = _read_new_trace_events(trace_path, seen)
                            if evs:
                                trace_queue.put(evs)
                                deadline = time.monotonic() + 1.0
                            else:
                                time.sleep(0.05)
                        task_result[0] = task
                        done_event.set()
                        return
                    done_event.wait(timeout=self.interval_s)
            except Exception as exc:
                error[0] = exc
                done_event.set()

        poll_thread = threading.Thread(target=poll, daemon=True)
        poll_thread.start()

        # Application-based working loop with prompt_toolkit input field
        running_state = {
            "t0": turn_started,
            "pending_count": self.mq.pending_count,
            "preview_items": self.mq.peek_all(),
            "interrupted": False,
            "task_active": True,
            "user_input_form": None,
            "escape_interrupts": True,
            "reasoning_state": self.reasoning_state,
            "show_shortcuts": False,
            "spinner_idx": 0,
            "spinner_last_ts": 0,
        }

        def _output_provider():
            return self._live_widget_lines()

        from .cli_chat_active import _create_chat_application
        self._mouse_controller.set_wheel_handler(
            self._fs_scroll_by if self._tui_fullscreen else None
        )
        self._mouse_controller.set_capture_allowed(self._tui_fullscreen)
        if getattr(self, "_fs_external_app", None) is not None:
            # Persistent fullscreen session owns the Application; drive it
            # in-place from the headless loop below instead of creating a
            # second full-screen app (which would flash the main buffer).
            app, input_buffer = None, None
        else:
            app, input_buffer, _ = _create_chat_application(
                chat_state={"phase": "running"},
                status_line=self.sl,
                running_state=running_state,
                output_provider=_output_provider,
                mouse_controller=self._mouse_controller,
                fullscreen=self._tui_fullscreen,
                transcript_provider=(
                    self._fullscreen_output_lines if self._tui_fullscreen else None
                ),
                transcript_regions_provider=(
                    self._fullscreen_regions if self._tui_fullscreen else None
                ),
                transcript_full_provider=(
                    self._fullscreen_transcript_lines if self._tui_fullscreen else None
                ),
                transcript_window_start_provider=(
                    self._fullscreen_window_start if self._tui_fullscreen else None
                ),
                on_scroll=self._fs_scroll_by if self._tui_fullscreen else None,
                on_scroll_edge=self._fs_scroll_edge if self._tui_fullscreen else None,
            )

        _fs_state = getattr(self, "_fs_running_state", None)
        interrupted_by_user = (
            bool(_fs_state.get("interrupted"))
            if isinstance(_fs_state, dict)
            else False
        )

        if app is None:
            # Headless loop. In persistent fullscreen it only drives the
            # already-running Application via invalidate(); otherwise it
            # prints transcript lines directly.
            external_app = getattr(self, "_fs_external_app", None)
            try:
                while not done_event.is_set():
                    if isinstance(_fs_state, dict) and _fs_state.get("exit_requested"):
                        interrupted_by_user = True
                        self._active = False
                        done_event.set()
                        cancel_task(task_id)
                        break
                    pending_user_input = self._pending_user_input_request(task_id)
                    if pending_user_input is not None:
                        if external_app is not None:
                            # Persistent fullscreen: suspend the app while the
                            # interactive form runs, then resume it.
                            self._fs_run_on_terminal(
                                lambda: self._resolve_user_input_request(
                                    pending_user_input
                                )
                            )
                        else:
                            self._resolve_user_input_request(pending_user_input)
                        continue

                    while not trace_queue.empty():
                        try:
                            evs = trace_queue.get_nowait()
                        except queue.Empty:
                            break
                        reducer.apply_many(evs)

                    elapsed = time.monotonic() - turn_started
                    if external_app is not None:
                        try:
                            external_app.invalidate()
                        except Exception:
                            pass
                    else:
                        new_lines = self._render_widget_lines()
                        for line in new_lines:
                            self.console.print(line, markup=False)
                        self._emitted_line_count += len(new_lines)

                        self.console.print(
                            f"  • Working ({_elapsed_label(elapsed)} · esc to interrupt)"
                        )
                        if self._active and elapsed > 1:
                            self.sl.refresh()
                    time.sleep(0.05)
                if external_app is not None:
                    # Flush any trailing events to the persistent UI.
                    while not trace_queue.empty():
                        try:
                            evs = trace_queue.get_nowait()
                        except queue.Empty:
                            break
                        reducer.apply_many(evs)
                    try:
                        external_app.invalidate()
                    except Exception:
                        pass
            except KeyboardInterrupt:
                interrupted_by_user = True
                done_event.set()
                cancel_task(task_id)
        else:
            async def _app_loop():
                nonlocal interrupted_by_user
                from .cli_chat_active import run_in_terminal

                async def _flush_committed_scrollback() -> bool:
                    if self._tui_fullscreen:
                        return False
                    if not self._has_unemitted_widget_lines():
                        return False
                    if run_in_terminal is None:
                        self._render_widget()
                        return True
                    await run_in_terminal(self._render_widget)
                    return True

                while not done_event.is_set():
                    application_ready = asyncio.Event()
                    app_task = asyncio.create_task(
                        app.run_async(pre_run=application_ready.set)
                    )
                    try:
                        while not done_event.is_set() and not app_task.done():
                            if not application_ready.is_set():
                                await asyncio.sleep(0.01)
                                continue
                            pending_user_input = self._pending_user_input_request(task_id)
                            if pending_user_input is not None:
                                app.exit(result="__form__")
                                break

                            submitted = str(
                                running_state.pop("submitted_text", "") or ""
                            ).strip()
                            if submitted:
                                self.mq.enqueue(submitted)
                                running_state["pending_count"] = self.mq.pending_count
                                running_state["preview_items"] = self.mq.peek_all()
                                if input_buffer is not None:
                                    input_buffer.reset()
                                app.invalidate()

                            while not trace_queue.empty():
                                try:
                                    evs = trace_queue.get_nowait()
                                except queue.Empty:
                                    break
                                reducer.apply_many(evs)
                                if reducer.consume_dirty():
                                    if await _flush_committed_scrollback():
                                        pass
                                    app.invalidate()

                            elapsed = time.monotonic() - turn_started
                            if self._active and elapsed > 1:
                                self.sl.refresh()
                            # Always invalidate so the working timer updates
                            app.invalidate()

                            await asyncio.sleep(0.05)

                        if app_task.done():
                            result = app_task.result()
                            if result == "__INTERRUPT__":
                                interrupted_by_user = True
                                done_event.set()
                                cancel_task(task_id)
                                break
                            elif result == "__form__":
                                pending = self._pending_user_input_request(task_id)
                                if pending:
                                    self._resolve_user_input_request(pending)
                                if input_buffer is not None:
                                    input_buffer.reset()
                                app.invalidate()
                                continue
                            elif result == "__mouse_viewer__":
                                self._mouse_controller.clear_app()
                                self._open_history_viewer()
                                self._mouse_controller.set_app(app)
                                app.invalidate()
                                continue
                        else:
                            app_task.cancel()
                            try:
                                await app_task
                            except asyncio.CancelledError:
                                pass

                            pending = self._pending_user_input_request(task_id)
                            if pending:
                                self._resolve_user_input_request(pending)
                                if input_buffer is not None:
                                    input_buffer.reset()
                                continue
                    finally:
                        if not app_task.done():
                            app_task.cancel()
                            try:
                                await app_task
                            except asyncio.CancelledError:
                                pass

            try:
                asyncio.run(_app_loop())
            except KeyboardInterrupt:
                interrupted_by_user = True
                done_event.set()
                cancel_task(task_id)
            finally:
                try:
                    if app.is_running:
                        app.exit()
                except Exception:
                    pass

        poll_thread.join(timeout=2)

        local_tui_command = running_state.pop("local_tui_command", None)
        if local_tui_command:
            self._cmd_tui(local_tui_command)

        # If cells were added after the loop exited, print missing transcript lines
        _buf = self._scrollback_widget_lines()
        if not self._tui_fullscreen and self._emitted_line_count < len(_buf):
            for line in _buf[self._emitted_line_count:]:
                self.console.print(f" {line}", markup=False)
            self._emitted_line_count = len(_buf)
        had_rendered_output = _has_rendered_task_output(
            _buf,
            self._emitted_line_count,
        )

        # Final render + worked-for separator (Codex FinalMessageSeparator port):
        # instead of a bare "• Worked for Xs" bullet line, emit a full-width dim
        # separator carrying the label in the middle, then flush it to scrollback.
        elapsed = time.monotonic() - turn_started
        if interrupted_by_user:
            if getattr(self, "exit_on_interrupt", False):
                self._active = False
            worked_label = f"Interrupted by user for {_elapsed_label(elapsed)}"
        else:
            worked_label = f"Worked for {_elapsed_label(elapsed)}"
        self.widget.committed_cells.append(
            SeparatorCell(full_width=True, label=worked_label)
        )
        self.widget.committed_cells.append(TextCell(""))
        self._render_widget()
        if getattr(self, "_fs_external_app", None) is None:
            self.sl.refresh()

        if error[0] is not None:
            raise error[0]

        task = task_result[0]
        if task is not None:
            if interrupted_by_user:
                task["_worked_for_label"] = (
                    f"Interrupted by user for {_elapsed_label(time.monotonic() - turn_started)}"
                )
            else:
                task["_worked_for_label"] = (
                    f"Worked for {_elapsed_label(time.monotonic() - turn_started)}"
                )
            self.sl.note_task(task_id)
            task["_had_streaming_output"] = had_rendered_output
            task["_deferred_scrollback_lines"] = []
            self.sl.note_task(task_id)
        return task

    def _extract_images(self, text: str) -> tuple[str, list]:
        from .cli_chat_rich import _extract_dragdrop_images
        return _extract_dragdrop_images(text)

    def _print_attached_files(self, files: list):
        from .cli_chat_rich import _print_attached_files
        self._fs_run_on_terminal(
            lambda: _print_attached_files(self.console, files)
        )

    def _build_prompt(
        self, entries: list[dict], user_text: str,
    ) -> str:
        from .cli_chat_rich import _build_prompt
        return _build_prompt(
            entries, user_text, self.data_dir,
            30, 50_000, self.mm,
        )

    def _pending_user_input_request(self, task_id: str) -> dict[str, Any] | None:
        from .user_input_store import list_pending_user_input_requests

        pending = list_pending_user_input_requests(task_id=task_id)
        return pending[0] if pending else None

    def _resolve_user_input_request(self, pending: dict[str, Any]) -> None:
        from .cli_chat_user_input import (
            _ask_user_input,
            serialize_user_input_response,
            user_input_request_from_payload,
        )
        from .user_input_store import resolve_user_input_request

        request_id = str(pending.get("request_id") or "")
        payload = pending.get("payload") if isinstance(pending.get("payload"), dict) else {}
        try:
            response = _ask_user_input(
                self.console,
                user_input_request_from_payload(payload),
            )
        except Exception as exc:
            resolve_user_input_request(
                request_id,
                response={
                    "cancelled": True,
                    "error": f"request_user_input render failed: {type(exc).__name__}: {exc}",
                },
                cancelled=False,
            )
            return
        finally:
            # The nested PromptSession disables mouse support on exit; restore
            # it so the fullscreen transcript keeps wheel/click handling.
            try:
                self._mouse_controller.reassert_capture()
            except Exception:
                pass

        resolve_user_input_request(
            request_id,
            response=None if response is None else serialize_user_input_response(response),
            cancelled=response is None,
        )

    def _make_mouse_controller(self):
        from .tui.mouse_controller import MouseController

        return MouseController(
            on_toggle_reasoning=self._mouse_toggle_reasoning,
            on_toggle_reasoning_cell=self._mouse_toggle_reasoning_cell,
            on_open_viewer=self._open_history_viewer,
        )

    def _mouse_toggle_reasoning(self):
        rs = self.reasoning_state
        rs["visible"] = not rs.get("visible", False)
        rs["cell_overrides"] = {}
        app = getattr(self._mouse_controller, "_app", None)
        if app is not None:
            try:
                app.invalidate()
            except Exception:
                pass

    def _mouse_toggle_reasoning_cell(self, cell_id: str) -> None:
        """Toggle a single reasoning block without collapsing the others."""
        rs = self.reasoning_state
        overrides = rs.setdefault("cell_overrides", {})
        current = bool(overrides.get(cell_id, rs.get("visible", False)))
        overrides[cell_id] = not current
        app = getattr(self._mouse_controller, "_app", None)
        if app is not None:
            try:
                app.invalidate()
            except Exception:
                pass

    def _history_lines_for_viewer(self) -> list[str]:
        lines: list[str] = []
        try:
            entries = _load_entries(self.session_path)
        except Exception:
            entries = []
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
            lines.append("")
        try:
            width = _terminal_width()
            for cell in self.widget.committed_cells:
                cell_lines = cell.display_lines(width)
                if cell_lines:
                    lines.extend(cell_lines)
                    lines.append("")
        except Exception:
            pass
        if not lines:
            lines = ["  \033[2mИстория пуста.\033[0m", ""]
        return lines

    def _current_viewer_items(self):
        """Build structured viewer items from the current committed cells."""
        from .tui.mouse_viewer import ViewerItem
        from .tui.cells.history_cell import ToolCallCell, ToolCallGroupCell
        from .tui.exec_cell.render import render_tool_call

        width = _terminal_width()
        items = []
        for idx, cell in enumerate(self.widget.committed_cells):
            if isinstance(cell, ToolCallCell):
                expanded_lines = render_tool_call(
                    cell.tool_name,
                    cell.tool_input,
                    cell.status or None,
                    cell.elapsed if cell.elapsed else None,
                    cell.output or None,
                    False,
                    width=width,
                    expanded=True,
                )
                items.append(
                    ViewerItem(
                        cell_id=cell.cell_id or f"tool-{idx}",
                        lines_collapsed=cell.display_lines(width),
                        lines_expanded=expanded_lines,
                        expandable=bool(cell.output) and not cell.is_running,
                    )
                )
                continue
            if isinstance(cell, ToolCallGroupCell):
                for ridx, record in enumerate(cell.records):
                    collapsed = ToolCallCell(
                        record.tool_name,
                        record.tool_input,
                        status=record.status,
                        elapsed=record.elapsed,
                        output=record.output,
                        is_running=record.is_running,
                    ).display_lines(width)
                    expanded_lines = render_tool_call(
                        record.tool_name,
                        record.tool_input,
                        record.status or None,
                        record.elapsed if record.elapsed else None,
                        record.output or None,
                        record.is_running,
                        width=width,
                        expanded=True,
                    )
                    items.append(
                        ViewerItem(
                            cell_id=record.tool_id or f"group-{idx}-{ridx}",
                            lines_collapsed=collapsed,
                            lines_expanded=expanded_lines,
                            expandable=bool(record.output) and not record.is_running,
                        )
                    )
                continue
            lines = cell.display_lines(width)
            if lines:
                items.append(
                    ViewerItem(cell_id="", lines_collapsed=lines, expandable=False)
                )
        return items

    def _snapshot_viewer_items(self):
        """Persist structured tool items before committed cells are cleared."""
        try:
            items = self._current_viewer_items()
        except Exception:
            return
        if not items:
            return
        self._viewer_history_items.extend(items)
        # Bound the retained history to keep memory reasonable.
        max_items = 500
        if len(self._viewer_history_items) > max_items:
            self._viewer_history_items = self._viewer_history_items[-max_items:]

    def _viewer_items(self):
        return list(self._viewer_history_items) + self._current_viewer_items()

    def _open_history_viewer(self):
        from .tui.mouse_viewer import MouseHistoryViewer

        items = self._viewer_items()
        if items:
            viewer = MouseHistoryViewer(
                items=items, controller=self._mouse_controller
            )
        else:
            viewer = MouseHistoryViewer(
                self._history_lines_for_viewer(), controller=self._mouse_controller
            )
        viewer.run()

    def _cmd_mouse(self, text: str = ""):
        message = self._mouse_controller.handle_command(text)
        if message:
            for line in message.splitlines():
                self.console.print(f"  [dim]{line}[/dim]")

    def _update_reasoning_state(self, task: dict):
        lines = list(task.get("_deferred_scrollback_lines") or [])
        expanded = list(task.get("_expanded_deferred_scrollback_lines") or [])
        if not expanded:
            expanded = lines
        if not lines:
            from .tui.cells.history_cell import ReasoningCell
            width = _terminal_width()
            for cell in self.widget.committed_cells:
                if isinstance(cell, ReasoningCell):
                    collapsed = cell.display_lines(width)
                    lines = collapsed
                    expanded = cell.expanded_display_lines(width)
        self.reasoning_state["visible"] = False
        self.reasoning_state["lines"] = lines
        self.reasoning_state["expanded_lines"] = expanded

    def _merge_approval_context(
        self, existing: list, new_context: list,
    ) -> list:
        if not new_context:
            return existing
        merged = list(existing)
        for msg in new_context:
            if msg not in merged:
                merged.append(msg)
        return merged

    def _ask_approval(self, blocked: dict) -> str:
        from .cli_chat_approval import _ask_approval
        return self._fs_run_on_terminal(
            lambda: _ask_approval(self.console, blocked)
        )

    def _notify_turn_complete(self):
        from .cli_chat_rich import _notify_turn_complete
        _notify_turn_complete()
