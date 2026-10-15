"""Approval prompt adapter for rich chat."""
from __future__ import annotations

import shutil
import sys

try:
    from prompt_toolkit import PromptSession
    from prompt_toolkit.formatted_text import ANSI, HTML
    from prompt_toolkit.key_binding import KeyBindings
except Exception:
    PromptSession = None; KeyBindings = None; ANSI = None; HTML = None

from .cli_chat_rich_shared import _drain_stdin
from .isolation.host_op_approval import is_host_op_blocked_record
from .terminal_render_log import terminal_render_log
from .tui.bottom_pane.approval_overlay import ApprovalOverlay, ApprovalRequest

_PROMPT_TOOLKIT_APPROVAL_INPUT_ROWS = 1

def _approval_target_snippet(target: str, max_chars: int = 80) -> str:
    lines = str(target or "").splitlines()
    text = lines[0].strip() if lines else ""
    if len(text) <= max_chars:
        return text
    return text[: max(0, max_chars - 1)].rstrip() + "…"


def _approval_history_line(tool: str, target: str, decision: str) -> str:
    """Render a compact Codex-style transcript line after an approval decision."""
    name = str(tool or "").lower()
    snippet = _approval_target_snippet(target)
    if not snippet:
        if decision == "allow_session":
            return "✔ You approved this request every time this session"
        if decision == "allow":
            return "✔ You approved this request this time"
        return "✗ You rejected this request"

    if name in {"bash", "exec_command"} and snippet:
        action = f" to run {snippet}"
    elif name in {"write", "edit", "notebookedit"} and snippet:
        action = f" to make edits to {snippet}"
    elif snippet:
        action = f" to allow {tool}: {snippet}"
    else:
        action = " this request"

    if decision == "allow_session":
        return f"✔ You approved Igorek{action} every time this session"
    if decision == "allow":
        return f"✔ You approved Igorek{action} this time"
    return f"✗ You rejected Igorek's request{action}"


def _print_approval_history(console, tool: str, target: str, decision: str) -> None:
    """Commit a compact decision line with one spacer before the next tool cell."""
    line = "  " + _approval_history_line(tool, target, decision)
    terminal_render_log(
        "approval.history.print",
        tool=str(tool or ""),
        decision=str(decision or ""),
        target=str(target or ""),
        line=line,
    )
    console.print(line, markup=False)
    console.print("", markup=False)


def _approval_request(
    blocked_or_tool: dict | str,
    target: str = "",
    *,
    reason: str = "",
) -> ApprovalRequest:
    if not isinstance(blocked_or_tool, dict):
        return ApprovalRequest(str(blocked_or_tool), target, reasoning=reason)

    blocked = blocked_or_tool
    if is_host_op_blocked_record(blocked):
        return ApprovalRequest(
            "host-op",
            _blocked_tool_target(blocked),
            tool_input=blocked.get("args") if isinstance(blocked.get("args"), dict) else {},
            reasoning=str(blocked.get("reason") or ""),
        )
    tool_input = blocked.get("input") if isinstance(blocked.get("input"), dict) else {}
    return ApprovalRequest(
        str(blocked.get("tool") or "?"),
        _blocked_tool_target(blocked),
        signature=str(blocked.get("signature") or ""),
        source=str(blocked.get("source") or ""),
        tool_input=tool_input,
        reasoning=str(blocked.get("reason") or ""),
    )


def _blocked_tool_session_rule(blocked: dict) -> dict[str, str] | None:
    if is_host_op_blocked_record(blocked):
        # A host op is resolved by the host-op gate, never by an approval
        # signature: no session rule can express "this typed broker operation".
        return None
    rule = blocked.get("session_rule")
    if isinstance(rule, dict):
        return {str(key): str(value) for key, value in rule.items()}

    tool = str(blocked.get("tool") or "")
    tool_input = blocked.get("input") if isinstance(blocked.get("input"), dict) else {}
    if tool.lower() in {"write", "edit", "notebookedit"}:
        file_path = str(tool_input.get("file_path") or "").strip()
        if file_path:
            return {"rule_type": "file_path", "tool": tool, "file_path": file_path}
    signature = str(blocked.get("signature") or "").strip()
    if signature:
        return {"rule_type": "exact_tool_call", "tool": tool, "signature": signature}
    return None


def _ask_approval(
    console,
    blocked_or_tool: dict | str,
    target: str = "",
    *,
    reason: str = "",
) -> str:
    _drain_stdin()
    overlay = ApprovalOverlay()
    overlay.set_width(shutil.get_terminal_size((120, 24)).columns)
    overlay.set_requests([_approval_request(blocked_or_tool, target, reason=reason)])
    choices = [("allow","Разрешить"),("reject","Отклонить"),("allow_session","Разрешить до конца сессии")]
    sel = {"index":0}
    if PromptSession is None or KeyBindings is None or HTML is None:
        for line in overlay.render_lines():
            console.print(line, markup=False)
        console.print("  > ", end="")
        try: r = input().strip().lower()
        except (EOFError, KeyboardInterrupt): return "reject"
        if r in ("3","s","session","сессия"): return "allow_session"
        if r in ("2","n","no","нет","н"): return "reject"
        return "allow"
    kb = KeyBindings()
    rendered_line_count = {"value": 0}
    def _rp():
        overlay.set_width(shutil.get_terminal_size((120, 24)).columns)
        overlay.set_action_selection(sel["index"])
        lines = overlay.render_lines()
        rendered_line_count["value"] = len(lines)
        text = "\n".join(lines) + "\n"
        return ANSI(text) if ANSI is not None else text
    def _mv(d):
        sel["index"] = (sel["index"]+d)%len(choices)
        overlay.set_action_selection(sel["index"])
    @kb.add("left")
    @kb.add("up")
    def _(e): _mv(-1); e.app.invalidate()
    @kb.add("right")
    @kb.add("down")
    def _(e): _mv(1); e.app.invalidate()
    @kb.add("y")
    @kb.add("1")
    def _(e): sel["index"]=0; e.app.exit(result="allow")
    @kb.add("n")
    @kb.add("2")
    def _(e): sel["index"]=1; e.app.exit(result="reject")
    @kb.add("s")
    @kb.add("3")
    def _(e): sel["index"]=2; e.app.exit(result="allow_session")
    @kb.add("enter")
    def _(e): e.app.exit(result=choices[sel["index"]][0])
    @kb.add("c-o")
    def _(e):
        terminal_render_log("approval.reasoning_toggle.noop")
        e.app.invalidate()
    @kb.add("escape")
    @kb.add("c-c")
    def _(e): e.app.exit(result="reject")
    s = PromptSession(key_bindings=kb, reserve_space_for_menu=0, erase_when_done=False)
    try:
        r = s.prompt(_rp)
    except (EOFError, KeyboardInterrupt):
        clear_line_count = rendered_line_count["value"] + _PROMPT_TOOLKIT_APPROVAL_INPUT_ROWS
        terminal_render_log(
            "approval.prompt.done",
            result="reject",
            rendered_line_count=rendered_line_count["value"],
            clear_line_count=clear_line_count,
            interrupted=True,
        )
        _clear_recent_terminal_lines(clear_line_count)
        return "reject"
    clear_line_count = rendered_line_count["value"] + _PROMPT_TOOLKIT_APPROVAL_INPUT_ROWS
    terminal_render_log(
        "approval.prompt.done",
        result=str(r or "reject"),
        rendered_line_count=rendered_line_count["value"],
        clear_line_count=clear_line_count,
        interrupted=False,
    )
    _clear_recent_terminal_lines(clear_line_count)
    return str(r or "reject")


def _clear_recent_terminal_lines(line_count: int) -> None:
    """Clear only the approval prompt rows, never the transcript above it."""
    count = max(0, int(line_count or 0))
    if count <= 0:
        return
    terminal_render_log("approval.clear_recent_lines", line_count=count)
    out = sys.stdout
    out.write("\r")
    out.write(f"\x1b[{count}A")
    for index in range(count):
        out.write("\r\x1b[2K")
        if index + 1 < count:
            out.write("\x1b[1B")
    if count > 1:
        out.write(f"\x1b[{count - 1}A")
    out.write("\r")
    out.flush()


def _blocked_tool_target(blocked: dict) -> str:
    if is_host_op_blocked_record(blocked):
        op = str(blocked.get("op") or "").strip() or "?"
        approval_class = str(blocked.get("approval_class") or "").strip()
        return f"{op} [{approval_class}]" if approval_class else op
    explicit = str(blocked.get("target") or "").strip()
    if explicit: return explicit
    tn = str(blocked.get("tool") or "").lower()
    inp = blocked.get("input") if isinstance(blocked.get("input"),dict) else {}
    if tn in {"write","edit"}: return str(inp.get("file_path") or "")
    if tn=="bash": return str(inp.get("command") or "")
    return ""




__all__ = [
    "_ask_approval", "_approval_history_line", "_print_approval_history",
    "_blocked_tool_target", "_blocked_tool_session_rule", "_clear_recent_terminal_lines",
    "PromptSession", "KeyBindings", "ANSI", "HTML",
]
