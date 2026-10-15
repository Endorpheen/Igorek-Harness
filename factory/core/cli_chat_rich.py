"""Rich CLI chat — Codex Rust TUI architecture, Python port."""
from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from rich.console import Console
from rich.markdown import Markdown
from rich.padding import Padding
from rich.table import Table
from rich.panel import Panel

try:
    from prompt_toolkit import PromptSession
    from prompt_toolkit.application import Application
    from prompt_toolkit.buffer import Buffer
    from prompt_toolkit.completion import Completer, Completion
    from prompt_toolkit.document import Document
    from prompt_toolkit.formatted_text import ANSI, HTML, FormattedText
    from prompt_toolkit.layout import HSplit, Layout, Window, ConditionalContainer
    from prompt_toolkit.layout.controls import BufferControl, FormattedTextControl
    from prompt_toolkit.layout.dimension import Dimension
    from prompt_toolkit.layout.processors import Processor, Transformation
    from prompt_toolkit.key_binding import KeyBindings
    from prompt_toolkit.filters import Condition
    from prompt_toolkit.patch_stdout import patch_stdout
    from prompt_toolkit.styles import Style
    from prompt_toolkit.validation import Validator, ValidationError
except Exception:
    PromptSession = None; Application = None; Buffer = None
    KeyBindings = None; Completer = object; Document = None
    Completion = None; ANSI = None; HTML = None; FormattedText = None
    HSplit = None; Layout = None; Window = None; ConditionalContainer = None
    BufferControl = None; FormattedTextControl = None; Dimension = None
    Processor = object; Transformation = None; patch_stdout = None; Style = None
    Condition = None; Validator = object; ValidationError = ValueError

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
from .memory_manager import MemoryManager
from .message_queue import MessageQueue
from .runtime import get_profile_config, profile_display_model_name
from .status_line import (
    DEFAULT_STATUS_LINE_ITEMS,
    StatusLineRuntime,
    render_configured_status_line,
    save_status_line_items,
    status_line_item_catalog,
)
from .cli_chat_rich_shared import (
    _ANSI_RE, _ANSI_STYLE_MAP, _parse_ansi_style, _256_to_rgb, _ansi_to_formatted_text,
    _InputFieldProcessor, _read_input, _drain_stdin, _factory_version, _branch,
    _truncate, _filter_control_chars, _user_msg, _assistant_msg, _error_msg, _prompt,
)
from . import cli_chat_idle as _idle
from . import cli_chat_active as _active
from . import cli_chat_approval as _approval
from . import cli_chat_terminal as _terminal
from .cli_chat_idle import _SLASH_COMMANDS, _CLI_TIPS
from .cli_chat_active import _SPINNER_CHARS, _QUEUED_TASK_NOTICE
from .tui.theme import list_available_themes, load_theme_name, preview_theme, save_theme_name
from .cli_chat_terminal import (
    DEFAULT_TERMINAL_TITLE_ITEMS,
    load_terminal_title_config,
    render_terminal_title,
    save_terminal_title_items,
    terminal_title_item_catalog,
)
from .terminal_render_log import terminal_render_log
from .tui.cells.history_cell import ReasoningCell
from .tui.trace_reducer import TraceEventReducer


def _sync_idle_prompt_toolkit_symbols() -> None:
    _idle.PromptSession = PromptSession
    _idle.KeyBindings = KeyBindings
    _idle.Completer = Completer
    _idle.Completion = Completion
    _idle.ANSI = ANSI
    _idle.HTML = HTML
    _idle.Style = Style
    _idle.Validator = Validator
    _idle.ValidationError = ValidationError


def _sync_active_prompt_toolkit_symbols() -> None:
    _active.PromptSession = PromptSession
    _active.Application = Application
    _active.Buffer = Buffer
    _active.Document = Document
    _active.KeyBindings = KeyBindings
    _active.ANSI = ANSI
    _active.HTML = HTML
    _active.HSplit = HSplit
    _active.Layout = Layout
    _active.Window = Window
    _active.ConditionalContainer = ConditionalContainer
    _active.BufferControl = BufferControl
    _active.FormattedTextControl = FormattedTextControl
    _active.Dimension = Dimension
    _active.Style = Style
    _active.Validator = Validator
    _active.ValidationError = ValidationError
    _active.Condition = Condition


def _sync_approval_prompt_toolkit_symbols() -> None:
    _approval.PromptSession = PromptSession
    _approval.KeyBindings = KeyBindings
    _approval.ANSI = ANSI
    _approval.HTML = HTML


def _make_console(**overrides) -> Console:
    options = {
        "highlight": False,
        "no_color": not sys.stdout.isatty(),
        "emoji": False,
    }
    options.update(overrides)
    return Console(**options)


def _trace_replay_lines_for_task(task_id: str, width: int) -> list[str]:
    task_id = str(task_id or "").strip()
    if not task_id:
        return []
    trace_path = _trace_path_for_task(task_id)
    events, _seen = _active._read_new_trace_events(trace_path, 0)
    if not events:
        return []
    reducer = TraceEventReducer()
    reducer.apply_many(events)
    return reducer.display_lines(width)


def _trace_reasoning_lines_for_task(task_id: str, width: int) -> tuple[list[str], list[str]]:
    task_id = str(task_id or "").strip()
    if not task_id:
        return [], []
    trace_path = _trace_path_for_task(task_id)
    events, _seen = _active._read_new_trace_events(trace_path, 0)
    if not events:
        return [], []
    reducer = TraceEventReducer()
    reducer.apply_many(events)
    collapsed: list[str] = []
    expanded: list[str] = []
    cells = list(reducer.widget.committed_cells)
    if reducer.widget.reasoning_buffer:
        cells.append(ReasoningCell(reducer.widget.reasoning_buffer))
    for cell in cells:
        if not isinstance(cell, ReasoningCell):
            continue
        if collapsed and collapsed[-1] != "":
            collapsed.append("")
        if expanded and expanded[-1] != "":
            expanded.append("")
        collapsed.extend(cell.display_lines(width))
        expanded.extend(cell.expanded_display_lines(width))
    return collapsed, expanded


def _restore_resume_reasoning_state(
    entries: list[dict],
    reasoning_state: dict,
    *,
    width: int,
) -> None:
    for entry in reversed(entries):
        if str(entry.get("role") or "") != "assistant":
            continue
        task_id = str(entry.get("task_id") or "").strip()
        if not task_id:
            continue
        collapsed, expanded = _trace_reasoning_lines_for_task(task_id, width)
        if not expanded:
            return
        reasoning_state["visible"] = False
        reasoning_state["lines"] = collapsed
        reasoning_state["expanded_lines"] = expanded
        reasoning_state.pop("frozen_scrollback", None)
        return


def _replay_initial_history(console, entries: list[dict]) -> None:
    previous_rendered_role = ""
    for entry in entries:
        text = str(entry.get("text", ""))
        if not text:
            continue
        if _looks_like_raw_codex_jsonl(text):
            console.print("  [dim][skipped old raw Codex JSONL output][/dim]")
            previous_rendered_role = "system"
            continue
        role = str(entry.get("role", ""))
        if role == "assistant":
            trace_lines = _trace_replay_lines_for_task(
                str(entry.get("task_id") or ""),
                max(40, int(getattr(console, "width", 120) or 120)),
            )
            if trace_lines:
                for line in trace_lines:
                    console.print(line, markup=False)
            else:
                _assistant_msg(console, text)
            previous_rendered_role = "assistant"
        elif role == "user":
            if previous_rendered_role == "assistant":
                console.print()
            _user_msg(console, text)
            previous_rendered_role = "user"


def _print_resume_history_header(console, session_name: str, meta_parts: list[str]) -> None:
    console.print(f"  [dim]Resuming {session_name} · {' · '.join(meta_parts)}[/dim]")
    console.print()


def _make_prompt_session(status_line=None, *, toolbar_enabled=True, reasoning_state=None):
    _sync_idle_prompt_toolkit_symbols()
    return _idle._make_prompt_session(
        status_line,
        toolbar_enabled=toolbar_enabled,
        reasoning_state=reasoning_state,
    )


def _update_reasoning_state(
    reasoning_state: dict,
    task: dict,
    *,
    console=None,
    preserve_existing_reasoning: bool = False,
) -> None:
    lines = list(task.get("_deferred_scrollback_lines") or [])
    expanded_lines = list(task.get("_expanded_deferred_scrollback_lines") or [])
    expanded_reasoning_lines = list(task.get("_expanded_deferred_reasoning_lines") or [])
    previous_expanded_lines = list(reasoning_state.get("expanded_lines") or [])
    frozen_scrollback = bool(task.get("_deferred_scrollback_frozen"))
    printed_to_console = False
    if console is not None and len(lines) > _running_output_visible_line_budget():
        for line in lines:
            console.print(line, markup=False)
        printed_to_console = True
        lines = []
        if expanded_reasoning_lines:
            expanded_lines = expanded_reasoning_lines
        frozen_scrollback = True
    if expanded_reasoning_lines:
        expanded_lines = expanded_reasoning_lines
    elif preserve_existing_reasoning and previous_expanded_lines:
        expanded_lines = previous_expanded_lines
    terminal_render_log(
        "idle.reasoning_state.update",
        source_task_id=str(task.get("task_id") or ""),
        collapsed_line_count=len(lines),
        expanded_line_count=len(expanded_lines),
        expanded_reasoning_line_count=len(expanded_reasoning_lines),
        preserved_existing_reasoning=bool(
            preserve_existing_reasoning
            and not expanded_reasoning_lines
            and previous_expanded_lines
        ),
        frozen_scrollback=frozen_scrollback,
        printed_to_console=printed_to_console,
        collapsed_lines=lines,
        expanded_lines=expanded_lines,
    )
    reasoning_state["visible"] = False
    reasoning_state["lines"] = lines
    reasoning_state["expanded_lines"] = expanded_lines
    reasoning_state.pop("frozen_scrollback", None)
    if frozen_scrollback:
        reasoning_state["frozen_scrollback"] = True


def _flush_deferred_scrollback(console, reasoning_state: dict) -> None:
    for line in reasoning_state.get("lines") or []:
        console.print(line, markup=False)
    reasoning_state["visible"] = False
    reasoning_state["lines"] = []
    reasoning_state["expanded_lines"] = []
    reasoning_state.pop("frozen_scrollback", None)


def _idle_transcript_pager(console, sp: Path):
    return _idle._idle_transcript_pager(console, sp)


def _logo(console: Console, profile: str, model: str, branch: str, *, backend: str = "", version: str = "",
           update_info: tuple[bool, str, str] | None = None) -> None:
    return _idle._logo(console, profile, model, branch, backend=backend, version=version,
                       update_info=update_info)


def _get_update_status(console: Console, *, ttl: float = 3600.0) -> tuple[bool, str, str] | None:
    return _idle._get_update_status(console, ttl=ttl)


_IMAGE_ATTACH_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".webp"}
_MAX_ATTACH_BYTES = 8 * 1024 * 1024


def _extract_dragdrop_images(text: str) -> tuple[str, list[str]]:
    """Split drag-dropped file paths out of a raw user input line.

    Terminal emulators (iTerm2, GNOME Terminal, Windows Terminal, etc.) paste
    a literal filesystem path — sometimes quoted, sometimes space-escaped —
    when a file is dropped onto the shell. We treat every whitespace- or
    quote-delimited token that resolves to an existing supported image file
    as an attachment, strip it from the textual prompt, and let the backend
    embed it as an image block via `input_files`.
    """
    if not text:
        return text, []
    try:
        tokens = shlex.split(text, posix=True)
    except ValueError:
        tokens = text.split()

    attached: list[str] = []
    remaining: list[str] = []
    for tok in tokens:
        cleaned = tok.strip().rstrip(",;")
        if not cleaned:
            continue
        candidate = cleaned.strip("\"'`")
        if len(candidate) < 4 or "/" not in candidate and not candidate.startswith("~"):
            remaining.append(tok)
            continue
        suffix = Path(candidate).suffix.lower()
        if suffix not in _IMAGE_ATTACH_SUFFIXES:
            remaining.append(tok)
            continue
        try:
            resolved = Path(candidate).expanduser()
        except (OSError, RuntimeError):
            remaining.append(tok)
            continue
        if not resolved.is_file():
            remaining.append(tok)
            continue
        try:
            if resolved.stat().st_size > _MAX_ATTACH_BYTES:
                remaining.append(tok)
                continue
        except OSError:
            remaining.append(tok)
            continue
        attached.append(str(resolved))
        # also drop a paired "comma" / dangling separator tokens that some
        # terminals insert right after the path
        continue

    rebuilt = " ".join(remaining).strip()
    return rebuilt, attached


def _print_attached_files(console: Console, paths: list[str]) -> None:
    if not paths:
        return
    from .multimodal_payload import MAX_IMAGE_BYTES, _image_media_type
    for p in paths:
        try:
            size = os.path.getsize(p)
        except OSError:
            size = -1
        mb = f"{size / 1024:.0f} KB" if size >= 0 else "?"
        mtype = _image_media_type(Path(p)) or "image/?"
        console.print(
            f"  [cyan]📎 attached:[/cyan] [bold]{os.path.basename(p)}[/bold]"
            f" [dim]({mtype}, {mb}, ≤ {MAX_IMAGE_BYTES // (1024*1024)} MB cap)[/dim]"
        )


# Image-paste helpers are centralized in cli_chat_rich_shared.py so the
# PromptSession key bindings in cli_chat_idle.py can also call them.
# Re-exported here for backward compatibility with any external import.
from .cli_chat_rich_shared import (  # noqa: E402,F401
    _IMAGE_ATTACH_SUFFIXES,
    _MAX_ATTACH_BYTES,
    _resolve_image_path_from_text,
    _clipboard_image_wl,
    _clipboard_image_x11,
    _clipboard_image_path,
)


def _goodbye_banner(console: Console) -> None:
    """Print ASCII art 'IGOREK' banner on exit."""
    console.print()
    console.print(
        "  [bold cyan]█    █▀▀▀█  █▀▀▀█  █▀▀▀█  █▀▀▀▀  █  ▄▀[/bold cyan]\n"
        "  [bold cyan]█    █ ▄▄▄  █   █  █▄▄▄▀  █▀▀▀   █▀▀▄[/bold cyan] \n"
        "  [bold cyan]█    █▄▄▄█  █▄▄▄█  █  ▀▄  █▄▄▄▄  █  ▀▄[/bold cyan]"
    )
    console.print()


def _help(console: Console) -> None:
    return _idle._help(console)


def _sanitize_terminal_title(text: str, *, max_chars: int = 90) -> str:
    return _terminal._sanitize_terminal_title(text, max_chars=max_chars)


def _task_terminal_title(user_text: str) -> str:
    return _terminal._task_terminal_title(user_text)


def _set_terminal_title(title: str) -> None:
    return _terminal._set_terminal_title(title)


def _notify_turn_complete() -> None:
    return _terminal._notify_turn_complete()


def _configured_terminal_title(sl: StatusLineRuntime, user_text: str = "") -> str:
    sl.refresh()
    config = load_terminal_title_config(sl.settings_path)
    return render_terminal_title(sl.last_input, config.items, user_text=user_text)


def _terminal_title_summary(sl: StatusLineRuntime) -> str:
    sl.refresh()
    config = load_terminal_title_config(sl.settings_path)
    items = list(config.items)
    item_text = ", ".join(items) if items else "legacy task title default"
    preview_items = config.items or DEFAULT_TERMINAL_TITLE_ITEMS
    preview = render_terminal_title(sl.last_input, preview_items)
    return (
        f"[dim]Settings:[/dim] {sl.settings_path}\n"
        f"[dim]Configured:[/dim] {bool(items)}\n"
        f"[dim]Items:[/dim] {item_text}\n"
        f"[dim]Preview:[/dim] {preview}"
    )


def _terminal_title_setup(console, sl: StatusLineRuntime, *, input_fn=input) -> bool:
    sl.refresh()
    config = load_terminal_title_config(sl.settings_path)
    current = list(config.items or DEFAULT_TERMINAL_TITLE_ITEMS)
    catalog = terminal_title_item_catalog()
    index_by_id = {item["id"]: idx + 1 for idx, item in enumerate(catalog)}
    current_numbers = [str(index_by_id[item]) for item in current if item in index_by_id]
    preview = render_terminal_title(sl.last_input, tuple(current))
    console.print("  [bold]Configure terminal title[/bold]")
    console.print(f"  [dim]Current order:[/dim] {', '.join(current)}")
    if preview:
        console.print("  [dim]Preview:[/dim] ", end="")
        console.print(preview, markup=False)
    console.print()
    for idx, item in enumerate(catalog, start=1):
        selected = "x" if item["id"] in current else " "
        console.print(
            f"  {idx:>2}. [{selected}] {item['id']} - {item['description']}",
            markup=False,
        )
    console.print()
    console.print(
        "  [dim]Enter numbers in display order, e.g. "
        f"{' '.join(current_numbers)}. Empty cancels; reset restores legacy task title default.[/dim]"
    )
    console.print("  > ", end="")
    try:
        raw = str(input_fn()).strip()
    except (EOFError, KeyboardInterrupt):
        console.print()
        return False
    if not raw:
        console.print("  [dim]Terminal title setup cancelled.[/dim]")
        return False
    if raw.lower() in {"reset", "default"}:
        save_terminal_title_items([], settings_path=sl.settings_path)
        console.print("  [green]Terminal title reset to legacy task title default.[/green]")
        return True
    raw_parts = [part for part in raw.replace(",", " ").split() if part]
    selected: list[str] = []
    for part in raw_parts:
        try:
            index = int(part)
        except ValueError:
            console.print(f"  [red]Invalid item number:[/red] {part}")
            return False
        if index < 1 or index > len(catalog):
            console.print(f"  [red]Item number out of range:[/red] {index}")
            return False
        item_id = catalog[index - 1]["id"]
        if item_id not in selected:
            selected.append(item_id)
    if not selected:
        console.print("  [dim]No terminal title items selected; cancelled.[/dim]")
        return False
    save_terminal_title_items(selected, settings_path=sl.settings_path)
    console.print("  [green]Terminal title updated.[/green]")
    console.print("  [dim]Preview:[/dim] ", end="")
    console.print(render_terminal_title(sl.last_input, tuple(selected)), markup=False)
    return True


def _theme_preview_lines(theme_name: str) -> list[str]:
    from .tui.cells.history_cell import DiffCell
    from .tui.markdown_render import render_markdown_to_lines
    with preview_theme(theme_name):
        lines = render_markdown_to_lines("## Theme preview\n`code` and **bold** text\n- list item", width=72)
        lines.extend(DiffCell("old line", "new line", "preview.py").display_lines(72))
    return lines


def _theme_summary(sl: StatusLineRuntime) -> str:
    name = load_theme_name(sl.settings_path)
    return (
        f"[dim]Settings:[/dim] {sl.settings_path}\n"
        f"[dim]Theme:[/dim] {name}\n"
        "[dim]Use:[/dim] /theme to select a bundled theme; /theme reset restores dark."
    )



def _skills_summary(*, data_dir=None, path=None) -> str:
    from .cli_skills import list_skills

    payload = list_skills(data_dir=data_dir, path=path)
    skills = list(payload.get("skills") or [])
    enabled = sum(1 for item in skills if item.get("enabled"))
    root = str(payload.get("skills_root") or "")
    lines = [
        f"[dim]Skills root:[/dim] {root}",
        f"[dim]Loaded:[/dim] {payload.get('count', 0)}",
        f"[dim]Enabled:[/dim] {enabled}",
    ]
    if skills:
        lines.append("")
        lines.append("[bold]Skills[/bold]")
        for idx, item in enumerate(skills[:20], start=1):
            marker = "x" if item.get("enabled") else " "
            lines.append(
                f"{idx:>2}. [{marker}] {item.get('skill_id') or item.get('name') or '?'}"
                f" — {item.get('description') or ''}"
            )
    return "\n".join(lines)


def _skills_manage(console, *, data_dir=None, path=None, input_fn=input) -> bool:
    from .cli_skills import list_skills, set_skill_enabled

    payload = list_skills(data_dir=data_dir, path=path)
    skills = [dict(item) for item in (payload.get("skills") or [])]
    if not skills:
        console.print("  [dim]No skills found.[/dim]")
        return False

    def render() -> None:
        console.print("  [bold]Skills[/bold]")
        console.print("  [dim]Enter numbers to toggle, blank to save and exit.[/dim]")
        for idx, item in enumerate(skills, start=1):
            marker = "x" if item.get("enabled") else " "
            skill_id = item.get("skill_id") or item.get("name") or "?"
            desc = str(item.get("description") or "").strip()
            tail = f" — {desc}" if desc else ""
            console.print(f"  {idx:>2}. [{marker}] {skill_id}{tail}")
        console.print("  > ", end="")

    while True:
        render()
        try:
            raw = str(input_fn()).strip()
        except (EOFError, KeyboardInterrupt):
            console.print()
            console.print("  [dim]Skills management cancelled.[/dim]")
            return False
        console.print()
        if not raw:
            break

        numbers = [part for part in raw.replace(",", " ").split() if part]
        if not numbers:
            break
        changed = False
        for part in numbers:
            try:
                index = int(part)
            except ValueError:
                _error_msg(console, f"Invalid skill number: {part}")
                console.print()
                return False
            if index < 1 or index > len(skills):
                _error_msg(console, f"Skill number out of range: {index}")
                console.print()
                return False
            item = skills[index - 1]
            item["enabled"] = not bool(item.get("enabled"))
            changed = True
            set_skill_enabled(
                str(item.get("skill_id") or item.get("name") or ""),
                enabled=bool(item.get("enabled")),
                data_dir=data_dir,
                path=path,
            )
        if changed:
            console.print("  [green]Skills updated.[/green]")
            console.print()

    console.print("  [green]Skills management saved.[/green]")
    return True


def _theme_setup(console, sl: StatusLineRuntime, *, input_fn=input) -> bool:
    themes = list_available_themes()
    current = load_theme_name(sl.settings_path)
    console.print("  [bold]Select syntax theme[/bold]")
    console.print(f"  [dim]Current:[/dim] {current}")
    console.print()
    for idx, theme in enumerate(themes, start=1):
        selected = "x" if theme.name == current else " "
        console.print(f"  {idx:>2}. [{selected}] {theme.name} - {theme.description}", markup=False)
        for line in _theme_preview_lines(theme.name):
            console.print("     " + line, markup=False)
        console.print()
    console.print("  [dim]Enter theme number. Empty cancels; reset restores dark.[/dim]")
    console.print("  > ", end="")
    try:
        raw = str(input_fn()).strip()
    except (EOFError, KeyboardInterrupt):
        console.print()
        return False
    if not raw:
        console.print("  [dim]Theme selection cancelled.[/dim]")
        return False
    if raw.lower() in {"reset", "default"}:
        theme = save_theme_name(None, settings_path=sl.settings_path)
        console.print(f"  [green]Theme reset to {theme.name}.[/green]")
        return True
    try:
        index = int(raw)
    except ValueError:
        console.print(f"  [red]Invalid theme number:[/red] {raw}")
        return False
    if index < 1 or index > len(themes):
        console.print(f"  [red]Theme number out of range:[/red] {index}")
        return False
    theme = save_theme_name(themes[index - 1].name, settings_path=sl.settings_path)
    console.print(f"  [green]Theme updated:[/green] {theme.name}")
    return True


def _model_recommendation_prompt(
    console,
    *,
    current_model: str,
    provider: str,
    api_format: str | None = None,
    input_fn=input,
) -> str | None:
    from .codex_provider import recommended_model_for_provider, user_facing_codex_provider

    recommended = recommended_model_for_provider(provider, api_format=api_format)
    target_model = recommended if recommended and recommended != current_model else None
    provider_name = user_facing_codex_provider(provider)
    if target_model is None:
        console.print(f"  [dim]No newer recommended model for {provider_name}.[/dim]")
        return None

    console.print(f"  [bold]> Igorek recommends trying {target_model}.[/bold]")
    console.print(f"  We recommend switching from {current_model} to {target_model} for this session.")
    console.print()
    console.print("  Choose how you'd like Igorek to proceed.")
    console.print()
    console.print(f"  1. Try recommended model ({target_model})")
    console.print(f"  2. Use existing model ({current_model})")
    console.print()
    console.print("  [dim]Enter 1 or 2. Empty cancels.[/dim]")
    console.print("  > ", end="")
    try:
        raw = str(input_fn()).strip()
    except (EOFError, KeyboardInterrupt):
        console.print()
        return None
    if raw == "1":
        console.print(f"  [green]Model for this session → {target_model}[/green]")
        return target_model
    if raw == "2":
        console.print(f"  [dim]Keeping model for this session: {current_model}[/dim]")
        return current_model
    if not raw:
        console.print("  [dim]Model recommendation cancelled.[/dim]")
    else:
        console.print(f"  [red]Invalid choice:[/red] {raw}")
    return None


def _apply_session_model(sl: StatusLineRuntime, *, profile_model: str, selected_model: str) -> str | None:
    model_override = selected_model if selected_model != profile_model else None
    sl.model = selected_model
    sl.refresh()
    return model_override


def _trace_path_for_task(task_id: str) -> Path:
    return _active._trace_path_for_task(task_id)


def _read_new_trace_events(trace_path: Path, seen: int):
    return _active._read_new_trace_events(trace_path, seen)


def _make_agent_prompt_session(status_line=None, *, running_state=None):
    _sync_active_prompt_toolkit_symbols()
    return _active._make_agent_prompt_session(status_line, running_state=running_state)


def _running_elapsed_label(elapsed: float) -> str:
    return _active._running_elapsed_label(elapsed)


def _running_status_lines(running_state: dict, status_line=None) -> list[str]:
    return _active._running_status_lines(running_state, status_line)


def _statusline_summary(sl: StatusLineRuntime) -> str:
    sl.refresh()
    summary = sl.debug_summary()
    items = summary.get("items") if isinstance(summary.get("items"), list) else []
    configured = bool(summary["configured"] or items)
    item_text = ", ".join(str(item) for item in items) if items else "builtin default"
    return (
        f"[dim]Settings:[/dim] {summary['settings_path']}\n"
        f"[dim]Configured:[/dim] {configured}\n"
        f"[dim]Command:[/dim] {summary['command'] or '-'}\n"
        f"[dim]Padding:[/dim] {summary['padding']}\n"
        f"[dim]Items:[/dim] {item_text}\n"
        f"[dim]Text:[/dim] {sl.plain_text()}"
    )


def _statusline_setup(console, sl: StatusLineRuntime, *, input_fn=input) -> bool:
    sl.refresh()
    current = list(sl.config.items or DEFAULT_STATUS_LINE_ITEMS)
    catalog = status_line_item_catalog()
    index_by_id = {item["id"]: idx + 1 for idx, item in enumerate(catalog)}
    current_numbers = [str(index_by_id[item]) for item in current if item in index_by_id]
    preview = render_configured_status_line(sl.last_input, tuple(current))
    console.print("  [bold]Configure status line[/bold]")
    console.print(f"  [dim]Current order:[/dim] {', '.join(current) or 'builtin default'}")
    if preview:
        console.print("  [dim]Preview:[/dim] ", end="")
        console.print(preview, markup=False)
    console.print()
    for idx, item in enumerate(catalog, start=1):
        selected = "x" if item["id"] in current else " "
        console.print(
            f"  {idx:>2}. [{selected}] {item['id']} - {item['description']}",
            markup=False,
        )
    console.print()
    console.print(
        "  [dim]Enter numbers in display order, e.g. "
        f"{' '.join(current_numbers)}. Empty cancels; reset restores builtin default.[/dim]"
    )
    console.print("  > ", end="")
    try:
        raw = str(input_fn()).strip()
    except (EOFError, KeyboardInterrupt):
        console.print()
        return False
    if not raw:
        console.print("  [dim]Status line setup cancelled.[/dim]")
        return False
    if raw.lower() in {"reset", "default"}:
        save_status_line_items([], settings_path=sl.settings_path)
        sl.refresh()
        console.print("  [green]Status line reset to builtin default.[/green]")
        return True
    raw_parts = [part for part in raw.replace(",", " ").split() if part]
    selected: list[str] = []
    for part in raw_parts:
        try:
            index = int(part)
        except ValueError:
            console.print(f"  [red]Invalid item number:[/red] {part}")
            return False
        if index < 1 or index > len(catalog):
            console.print(f"  [red]Item number out of range:[/red] {index}")
            return False
        item_id = catalog[index - 1]["id"]
        if item_id not in selected:
            selected.append(item_id)
    if not selected:
        console.print("  [dim]No status line items selected; cancelled.[/dim]")
        return False
    save_status_line_items(selected, settings_path=sl.settings_path)
    sl.refresh()
    console.print("  [green]Status line updated.[/green]")
    console.print("  [dim]Preview:[/dim] ", end="")
    console.print(sl.plain_text(), markup=False)
    return True


def _running_output_lines(output_lines: list[str], output_provider=None) -> list[str]:
    return _active._running_output_lines(output_lines, output_provider)


def _running_output_visible_line_budget() -> int:
    return _active._running_output_visible_line_budget()


def _print_reducer_new_lines(console, reducer, printed_count: int) -> int:
    return _active._print_reducer_new_lines(console, reducer, printed_count)


def _set_buffer_text(buffer, text: str, cursor: int | None = None) -> None:
    return _active._set_buffer_text(buffer, text, cursor)


def _sync_composer_from_prompt_buffer(composer, buffer) -> None:
    return _active._sync_composer_from_prompt_buffer(composer, buffer)


def _route_prompt_buffer_key_to_composer(buffer, composer, key: str) -> None:
    return _active._route_prompt_buffer_key_to_composer(buffer, composer, key)


def _attach_running_composer_callbacks(chat_widget, queued_input, *, on_change=None):
    return _active._attach_running_composer_callbacks(chat_widget, queued_input, on_change=on_change)


def _create_running_application(status_line=None, *, running_state=None, output_provider=None, composer=None):
    _sync_active_prompt_toolkit_symbols()
    return _active._create_running_application(
        status_line, running_state=running_state, output_provider=output_provider, composer=composer
    )


def _chat_widget_frame_lines(widget, *, width: int, height: int = 24) -> list[str]:
    return _active._chat_widget_frame_lines(widget, width=width, height=height)


def _sync_chat_composer_from_buffer(widget, text: str) -> None:
    return _active._sync_chat_composer_from_buffer(widget, text)


def _clear_running_preview(widget, running_state: dict) -> None:
    return _active._clear_running_preview(widget, running_state)


def _ask_task_with_live_trace(
    console, text, profile, interval_s, timeout_s, metadata,
    status_line=None, queued_input=None, queued_prompt_session=None,
    input_files=None, shown_edit_diff_signatures=None,
):
    _sync_active_prompt_toolkit_symbols()
    return _active._ask_task_with_live_trace(
        console, text, profile, interval_s, timeout_s, metadata,
        status_line=status_line, queued_input=queued_input, queued_prompt_session=queued_prompt_session,
        input_files=list(input_files or []),
        shown_edit_diff_signatures=shown_edit_diff_signatures,
    )


def _ask_approval(console, blocked_or_tool: dict | str, target: str = "", *, reason: str = "") -> str:
    _sync_approval_prompt_toolkit_symbols()
    return _approval._ask_approval(console, blocked_or_tool, target, reason=reason)


def _blocked_tool_target(blocked: dict) -> str:
    return _approval._blocked_tool_target(blocked)


def _extract_rejection_context(approval_blocked: list[dict], max_chars: int = 4000) -> str:
    """Build a compact context summary from approval_context_messages for rejected tasks."""
    all_assistant_texts: list[str] = []
    seen_texts: set[str] = set()

    def _append_text(value: object) -> None:
        text = str(value or "").strip()
        if text and text not in seen_texts:
            all_assistant_texts.append(text)
            seen_texts.add(text)

    for blocked in approval_blocked:
        if not isinstance(blocked, dict):
            continue
        msgs = blocked.get("approval_context_messages")
        if isinstance(msgs, list):
            for msg in msgs:
                if not isinstance(msg, dict) or msg.get("role") != "assistant":
                    continue
                content = msg.get("content")
                if isinstance(content, str):
                    _append_text(content)
                elif isinstance(content, list):
                    for part in content:
                        if isinstance(part, dict) and part.get("type") == "text":
                            _append_text(part.get("text", ""))
        _append_text(blocked.get("assistant_text"))
    if not all_assistant_texts:
        return ""
    result = "\n\n".join(all_assistant_texts)
    if len(result) > max_chars:
        result = result[-max_chars:]
    return result


def _approval_context_messages_from_blocked(approval_blocked: list[dict]) -> list[dict]:
    messages: list[dict] = []
    for blocked in approval_blocked:
        if not isinstance(blocked, dict):
            continue
        raw_messages = blocked.get("approval_context_messages")
        if not isinstance(raw_messages, list):
            continue
        for msg in raw_messages:
            if not isinstance(msg, dict):
                continue
            role = msg.get("role")
            content = msg.get("content")
            if role in {"assistant", "user"} and isinstance(content, list):
                messages.append({"role": role, "content": content})
    return messages


def _merge_approval_context_messages(existing: list[dict], incoming: list[dict]) -> list[dict]:
    merged: list[dict] = []
    seen: set[str] = set()
    for msg in [*existing, *incoming]:
        if not isinstance(msg, dict):
            continue
        role = msg.get("role")
        content = msg.get("content")
        if role not in {"assistant", "user"} or not isinstance(content, list):
            continue
        normalized = {"role": role, "content": content}
        signature = json.dumps(normalized, ensure_ascii=False, sort_keys=True)
        if signature in seen:
            continue
        seen.add(signature)
        merged.append(normalized)
    return merged


def _approval_text_for_history(task: dict, approval_blocked: list[dict]) -> str:
    preamble = str(task.get("result_text") or task.get("output_text") or "").strip()
    blocked_reasons = {
        str(item.get("reason") or "").strip()
        for item in approval_blocked
        if isinstance(item, dict)
    }
    if (
        preamble
        and preamble != "Для выполнения этого действия требуется подтверждение."
        and preamble not in blocked_reasons
    ):
        return preamble
    return _extract_rejection_context(approval_blocked) or preamble


def _handle_active_turn_interrupt(console, mq: MessageQueue, pending_inputs: list[str]) -> None:
    console.print()
    pending_inputs.extend(mq.drain_all())


def _build_prompt(entries, user_text, data_dir, mm, mc, memory_manager):
    from .cli_chat import _build_prompt as _bp
    ctx = _history_tail(entries, max_messages=mm, max_chars=mc)
    foundation = memory_manager.foundation_context(data_dir=data_dir)
    return _bp(ctx, user_text, foundation_context=foundation)


def _looks_like_raw_codex_jsonl(text: str) -> bool:
    lines = [l.strip() for l in str(text or "").splitlines() if l.strip()]
    if not lines: return False
    je = 0
    for l in lines[:8]:
        if not l.startswith("{"): continue
        try: e = json.loads(l)
        except json.JSONDecodeError: continue
        if isinstance(e,dict) and isinstance(e.get("type"),str): je+=1
    return je>=2


def run_rich_chat(
    profile="zai", interval_s=0.5, timeout_s=1200, session="default",
    debug=False,
    data_dir=None,
):
    from .chat_session import ChatSession
    sn = _sanitize_session_name(session)
    sp = _session_path(sn)
    mm = MemoryManager()
    pc = get_profile_config(profile)
    profile_model = profile_display_model_name(pc)
    branch = _branch()
    console = _make_console()

    cs = ChatSession(
        console=console,
        profile=profile,
        session_name=sn,
        model=profile_model,
        branch=branch,
        cwd=str(Path.cwd()),
        data_dir=data_dir,
        mm=mm,
        interval_s=interval_s,
        timeout_s=timeout_s,
        show_debug=debug,
    )
    return cs.run()
