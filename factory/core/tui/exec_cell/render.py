"""Exec cell rendering — port of Codex exec_cell/render.rs.

Renders tool call cells with Codex formatting:
  ●  Ran tool_name(args)
    ⎿ done · 0.2s
    └ output line 1
      output line 2
"""
from __future__ import annotations
from typing import Optional
from ..shimmer import spinner_char
import re, textwrap, time

_ANSI_RE = re.compile(r'\033\[[0-9;]*[A-Za-z]')

# ANSI colors
_GREEN = "\033[32m"
_RED = "\033[31m"
_YELLOW = "\033[33m"
_DIM = "\033[2m"
_BOLD = "\033[1m"
_CYAN = "\033[36m"
_RESET = "\033[0m"

# Constants from exec_cell/render.rs
EXEC_COMMAND_CONTINUATION_MAX_LINES = 2
EXEC_OUTPUT_MAX_LINES = 5
TOOL_CALL_MAX_LINES = 5


def _bullet(status: Optional[bool], start_time: Optional[float] = None) -> str:
    """Codex line 362: bullet for tool call status.
    True=green ●, False=red ●, None=spinner ●/◦
    """
    if status is True:
        return f"{_GREEN}{_BOLD}•{_RESET}"
    if status is False:
        return f"{_RED}{_BOLD}•{_RESET}"
    if start_time is not None:
        elapsed = time.monotonic() - start_time
        blink = int(elapsed * 1000 / 600) % 2 == 0
        ch = "•" if blink else "◦"
        return f"{_YELLOW}{ch}{_RESET}"
    return f"{_DIM}•{_RESET}"


def _format_duration(seconds: float) -> str:
    """Codex line 244: duration display like '0.2s', '1.5s', '2m 03s'."""
    if seconds < 0.1:
        return "<0.1s"
    if seconds < 60:
        return f"{seconds:.1f}s"
    m = int(seconds // 60)
    s = int(seconds % 60)
    return f"{m}m {s:02d}s"


def render_tool_call(
    tool_name: str,
    tool_input: dict,
    status: Optional[str] = None,
    elapsed: Optional[float] = None,
    output: Optional[str] = None,
    is_running: bool = False,
    width: int = 80,
    expanded: bool = False,
) -> list[str]:
    """Render a tool call cell — Codex format with ANSI colors.

    Returns list of display lines. ``expanded`` only affects how many
    stored output lines are shown; the collapsed rendering is byte-for-byte
    identical to the historical behaviour.
    """
    lines = []

    # Line 1 follows Codex TUI: "• Ran tool(args)" from the viewport edge.
    preview = _tool_arg_preview(tool_name, tool_input)
    if is_running:
        title = f"{_DIM}Running{_RESET}"
        bullet = _bullet(None)
    elif status == "done":
        title = f"{_GREEN}Ran{_RESET}"
        bullet = _bullet(True)
    elif status == "error":
        title = f"{_RED}Ran{_RESET}"
        bullet = _bullet(False)
    elif status == "exit":
        title = f"{_YELLOW}Ran{_RESET}"
        bullet = _bullet(False)
    else:
        title = "Ran"
        bullet = _bullet(None)

    tool_styled = f"{_BOLD}{tool_name}{_RESET}"
    title_prefix = f"{bullet} {title} {tool_styled}"
    visible_budget = max(24, width - 14 - len(tool_name))
    if preview and len(preview) > visible_budget:
        preview = preview[: max(0, visible_budget - 1)] + "…"
    if preview:
        lines.append(f"{title_prefix}({_DIM}{preview}{_RESET})")
    else:
        lines.append(title_prefix)

    # Line 2: ⎿ status · elapsed
    if status and elapsed is not None:
        dur = _format_duration(elapsed)
        if status == "done":
            st = f"{_GREEN}{status}{_RESET}"
        elif status == "error":
            st = f"{_RED}{status}{_RESET}"
        else:
            st = f"{_YELLOW}{status}{_RESET}"
        lines.append(f"  {_DIM}└{_RESET} {st} {_DIM}· {dur}{_RESET}")
    elif status:
        if status == "done":
            st = f"{_GREEN}{status}{_RESET}"
        elif status == "error":
            st = f"{_RED}{status}{_RESET}"
        else:
            st = f"{_YELLOW}{status}{_RESET}"
        lines.append(f"  {_DIM}└{_RESET} {st}")

    # Output lines:   └ first,     rest
    if output and not is_running:
        clean_output = _ANSI_RE.sub('', output)
        out_lines = clean_output.strip().splitlines()
        max_lines = len(out_lines) if expanded else EXEC_OUTPUT_MAX_LINES
        for i, ol in enumerate(out_lines[:max_lines]):
            marker = f"  {_DIM}└{_RESET} " if i == 0 and not status else "    "
            continuation = "    "
            wrapped = textwrap.wrap(
                ol,
                width=max(20, width - 8),
                break_long_words=False,
                break_on_hyphens=False,
            ) or [""]
            for wi, chunk in enumerate(wrapped):
                lines.append(f"{marker if wi == 0 else continuation}{chunk}")
        if len(out_lines) > max_lines:
            omitted = len(out_lines) - max_lines
            lines.append(f"    {_DIM}… +{omitted} lines{_RESET}")

    return lines


def _tool_arg_preview(name: str, inp: dict) -> str:
    """Extract key argument for preview display."""
    if name in ("Bash", "exec_command"):
        cmd = inp.get("command") or inp.get("cmd", "")
        return cmd[:100] if cmd else ""
    if name in ("Read", "Write", "Edit"):
        return inp.get("file_path", "")[:80]
    if name in ("Grep", "Search"):
        pattern = inp.get("pattern") or inp.get("query", "")
        path = inp.get("path") or inp.get("include", "")
        parts = [p for p in (pattern, path) if p]
        return " ".join(parts)[:80]
    if name in ("Glob", "LS"):
        return inp.get("pattern") or inp.get("path", "")[:80]
    if name in ("web_search", "WebSearch"):
        return inp.get("query", "")[:80]
    if name in ("web_extract", "WebFetch"):
        return inp.get("url", "")[:80]
    return ""
