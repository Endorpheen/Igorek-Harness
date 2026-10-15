"""History cell types — full port of Codex history_cell.rs."""
from __future__ import annotations
from abc import ABC, abstractmethod
from dataclasses import dataclass
from textwrap import wrap
from typing import Any, Optional

try:
    from factory.core.tui.exec_cell.render import render_tool_call, _tool_arg_preview
except ModuleNotFoundError as exc:
    if exc.name != "factory":
        raise
    from ..exec_cell.render import render_tool_call, _tool_arg_preview

class HistoryCell(ABC):
    @abstractmethod
    def display_lines(self, width: int) -> list[str]: ...
    def render(self, area, buf):
        width = area.width if hasattr(area, "width") else 80
        for line in self.display_lines(width):
            buf.append(line)
    def desired_height(self, width: int) -> int:
        return len(self.display_lines(width))
    def transcript_lines(self, width: int) -> list[str]:
        return self.display_lines(width)
    def is_stream_continuation(self) -> bool: return False
    def cursor_pos(self, area) -> Optional[tuple[int, int]]: return None

class TextCell(HistoryCell):
    def __init__(self, text: str, role: str = "assistant"):
        self.text = text; self.role = role
    def display_lines(self, width):
        out: list[str] = []
        max_width = max(1, int(width))
        for line in (self.text or "").splitlines() or [""]:
            if self.role == "user":
                wrapped = wrap(
                    line,
                    max_width,
                    initial_indent="  ",
                    subsequent_indent="  ",
                    replace_whitespace=False,
                    drop_whitespace=False,
                    break_long_words=True,
                    break_on_hyphens=False,
                ) or ["  "]
            else:
                wrapped = wrap(
                    line,
                    max_width,
                    replace_whitespace=False,
                    drop_whitespace=False,
                    break_long_words=True,
                    break_on_hyphens=False,
                ) or [""]
            out.extend(wrapped)
        return out

class MarkdownCell(HistoryCell):
    def __init__(self, text: str, marker: bool = False):
        self.text = text; self.marker = marker
    def display_lines(self, width):
        from ..markdown_render import render_markdown_to_lines
        lines = render_markdown_to_lines(self.text, width)
        if self.marker and lines:
            import re as _re
            _ansi_re = _re.compile(r'\x1b\[[0-9;]*[A-Za-z]')
            stripped = lines[0].lstrip()
            plain = _ansi_re.sub('', stripped)
            if plain and not plain.startswith(("│", "┌", "├", "└", "━", "─", "|")):
                lines[0] = f"• {stripped}"
        return lines
    def is_stream_continuation(self) -> bool: return True

class ToolCallCell(HistoryCell):
    def __init__(self, tool_name: str, tool_input: dict, status: str = "",
                 elapsed: float = 0, output: str = "", is_running: bool = False,
                 cell_id: str = "", expanded: bool = False):
        self.tool_name = tool_name; self.tool_input = tool_input
        self.status = status; self.elapsed = elapsed
        self.output = output; self.is_running = is_running
        self.cell_id = cell_id; self.expanded = expanded
    def display_lines(self, width):
        return render_tool_call(self.tool_name, self.tool_input,
                                self.status or None, self.elapsed if self.elapsed else None,
                                self.output or None, self.is_running, width=width,
                                expanded=self.expanded)


@dataclass
class ToolCallRecord:
    tool_id: str
    tool_name: str
    tool_input: dict
    status: str = ""
    elapsed: float = 0
    output: str = ""
    is_running: bool = True


class ToolCallGroupCell(HistoryCell):
    _EXPLORATION_LABELS = {
        "Read": "Read",
        "Grep": "Search",
        "Search": "Search",
        "Glob": "List",
        "LS": "List",
    }

    def __init__(self, records: list[ToolCallRecord] | None = None):
        self.records = records or []

    def add_call(self, tool_id: str, tool_name: str, tool_input: dict):
        self.records.append(ToolCallRecord(tool_id, tool_name, tool_input))

    def complete_call(
        self,
        tool_id: str,
        *,
        status: str,
        elapsed: float = 0,
        output: str = "",
    ) -> bool:
        for record in reversed(self.records):
            if record.tool_id == tool_id:
                record.status = status
                record.elapsed = elapsed
                record.output = output
                record.is_running = False
                return True
        return False

    def is_active(self) -> bool:
        return any(record.is_running for record in self.records)

    def is_exploration_group(self) -> bool:
        return bool(self.records) and all(
            record.tool_name in self._EXPLORATION_LABELS
            and record.status not in {"error", "exit"}
            for record in self.records
        )

    def extend_exploration(self, other: "ToolCallGroupCell") -> bool:
        if not self.is_exploration_group() or not other.is_exploration_group():
            return False
        self.records.extend(other.records)
        return True

    def display_lines(self, width):
        if self.is_exploration_group():
            return self._exploration_lines(width)
        lines = []
        segments: list[tuple[bool, list[ToolCallRecord]]] = []
        for record in self.records:
            compact = self._is_exploration_record(record)
            if segments and segments[-1][0] == compact:
                segments[-1][1].append(record)
            else:
                segments.append((compact, [record]))
        for compact, records in segments:
            if lines:
                lines.append("")
            if compact:
                lines.extend(self._exploration_lines(width, records=records))
                continue
            for index, record in enumerate(records):
                if index:
                    lines.append("")
                lines.extend(
                    ToolCallCell(
                        record.tool_name,
                        record.tool_input,
                        status=record.status,
                        elapsed=record.elapsed,
                        output=record.output,
                        is_running=record.is_running,
                    ).display_lines(width)
                )
        return lines

    def _is_exploration_record(self, record: ToolCallRecord) -> bool:
        return (
            record.tool_name in self._EXPLORATION_LABELS
            and record.status not in {"error", "exit"}
        )

    def _exploration_lines(
        self,
        width: int,
        *,
        records: list[ToolCallRecord] | None = None,
    ) -> list[str]:
        records = self.records if records is None else records
        title = "Exploring" if any(record.is_running for record in records) else "Explored"
        lines = [f"\033[2m•\033[0m \033[1m{title}\033[0m"]
        for index, record in enumerate(records):
            prefix = "  └ " if index == 0 else "    "
            label = self._EXPLORATION_LABELS[record.tool_name]
            detail = _tool_arg_preview(record.tool_name, record.tool_input)
            text = f"{label} {detail}".rstrip()
            wrapped = wrap(
                text,
                width=max(1, width - len(prefix)),
                subsequent_indent="  ",
                break_long_words=False,
                break_on_hyphens=False,
            ) or [""]
            lines.extend(
                f"\033[2m{prefix if line_index == 0 else ' ' * len(prefix)}\033[0m{line}"
                for line_index, line in enumerate(wrapped)
            )
        return lines

class StatusCell(HistoryCell):
    def __init__(self, text: str): self.text = text
    def display_lines(self, width): return [f"  {self.text}"]

class ReasoningCell(HistoryCell):
    def __init__(self, text: str): self.text = text

    def display_lines(self, width):
        line_count = max(1, len(self.text.splitlines()))
        return [
            f"\033[2m• Размышления модели · {line_count} строк · Ctrl+O раскрыть\033[0m"
        ]

    def expanded_display_lines(self, width):
        lines = ["", "\033[2m┌ Размышления модели · Ctrl+O скрыть\033[0m"]
        for raw_line in self.text.splitlines() or [""]:
            wrapped = wrap(
                raw_line,
                width=max(1, width - 6),
                break_long_words=False,
                break_on_hyphens=False,
            ) or [""]
            lines.extend(f"\033[2m│ \033[3m{line}\033[0m" for line in wrapped)
        lines.append("\033[2m└\033[0m")
        return lines


class RestoredReasoningCell(ReasoningCell):
    """Reasoning restored from a resumed session (pre-rendered lines).

    Behaves like :class:`ReasoningCell` for toggling, but the collapsed and
    expanded variants are taken verbatim from the trace-replay lines because
    the original reasoning text is not available.
    """

    def __init__(self, collapsed: list[str], expanded: list[str]):
        super().__init__("")
        self._collapsed = list(collapsed or [])
        self._expanded = list(expanded or [])

    def display_lines(self, width):
        return list(self._collapsed)

    def expanded_display_lines(self, width):
        return list(self._expanded)

class ErrorCell(HistoryCell):
    _RED = "\033[31m"
    _RESET = "\033[0m"

    def __init__(self, text: str): self.text = text
    def display_lines(self, width):
        out: list[str] = []
        max_width = max(1, int(width) - 2)
        for line in (self.text or "").splitlines() or [""]:
            chunks = wrap(
                line,
                max_width,
                replace_whitespace=False,
                drop_whitespace=False,
                break_long_words=True,
                break_on_hyphens=False,
            ) or [""]
            for chunk in chunks:
                out.append(f"  {self._RED}{chunk}{self._RESET}")
        return out

class PlanCell(HistoryCell):
    """Codex PlanUpdateCell port: append-only plan revision with structured statuses."""

    _RESET = "\033[0m"

    def __init__(self, plan: list[dict[str, Any] | str], explanation: str | None = None):
        self.explanation = explanation
        self.plan = [
            item if isinstance(item, dict) else {"step": str(item), "status": "pending"}
            for item in plan
        ]

    @classmethod
    def _styled(cls, text: str, *codes: str) -> str:
        return "".join(codes) + text + cls._RESET

    @staticmethod
    def _wrapped(text: str, width: int, *, initial_indent: str = "", subsequent_indent: str = "") -> list[str]:
        return wrap(
            text,
            width=max(1, width),
            initial_indent=initial_indent,
            subsequent_indent=subsequent_indent,
            break_long_words=False,
            break_on_hyphens=False,
        ) or [initial_indent.rstrip()]

    def display_lines(self, width):
        lines = [self._styled("• ", "\033[2m") + self._styled("Updated Plan", "\033[1m")]
        body: list[str] = []
        note = str(self.explanation or "").strip()
        if note:
            body.extend(
                self._styled(line, "\033[2m", "\033[3m")
                for line in self._wrapped(note, max(1, width - 4))
            )
        if not self.plan:
            body.append(self._styled("(no steps provided)", "\033[2m", "\033[3m"))
        else:
            for item in self.plan:
                step = str(item.get("step") or "")
                status = str(item.get("status") or "pending")
                if status == "completed":
                    prefix, codes = "✔ ", ("\033[9m", "\033[2m")
                elif status == "in_progress":
                    prefix, codes = "□ ", ("\033[36m", "\033[1m")
                else:
                    prefix, codes = "□ ", ("\033[2m",)
                body.extend(
                    self._styled(line, *codes)
                    for line in self._wrapped(
                        step,
                        max(1, width - 4),
                        initial_indent=prefix,
                        subsequent_indent="  ",
                    )
                )
        lines.extend(
            (self._styled("  └ ", "\033[2m") if index == 0 else "    ") + line
            for index, line in enumerate(body)
        )
        return lines

class SessionHeaderCell(HistoryCell):
    def __init__(self, model: str, branch: str, cwd: str):
        self.model = model; self.branch = branch; self.cwd = cwd
    def display_lines(self, width):
        w = min(width - 4, 56)
        return [
            f"  ╭{'─' * w}╮",
            f"  │  Model: {self.model}" + " " * max(0, w - 10 - len(self.model)) + "│",
            f"  │  Branch: {self.branch}" + " " * max(0, w - 11 - len(self.branch)) + "│",
            f"  │  Dir: {self.cwd}" + " " * max(0, w - 8 - len(self.cwd)) + "│",
            f"  ╰{'─' * w}╯",
        ]

class ImageCell(HistoryCell):
    def __init__(self, path: str, number: int = 1):
        self.path = path; self.number = number
    def display_lines(self, width): return [f"  [Image #{self.number}: {self.path}]"]

class DiffCell(HistoryCell):
    def __init__(self, old: str, new: str, filename: str = "",
                 start_line: int | None = None, replace_all: bool = False):
        self.old = old
        self.new = new
        self.filename = filename
        self.start_line = start_line
        self.replace_all = replace_all

    def display_lines(self, width):
        import difflib
        result = []
        old_lines = self.old.splitlines()
        new_lines = self.new.splitlines()
        # Count changes for summary
        matcher = difflib.SequenceMatcher(None, old_lines, new_lines)
        added = sum(j2 - j1 for tag, _, _, j1, j2 in matcher.get_opcodes() if tag in ("insert", "replace"))
        removed = sum(i2 - i1 for tag, i1, i2, _, _ in matcher.get_opcodes() if tag in ("delete", "replace"))
        # Codex-style summary: └ filename (+N -N)
        name = self.filename.rsplit("/", 1)[-1] if "/" in self.filename else self.filename
        result.append(f"  \033[2m└ {name} (+{added} -{removed})\033[0m")
        # When the actual file line is known, render a hunk header
        # (Codex-style: `@@ file.py:458-470 @@` or with replace_all marker).
        # Suppress the header when there is no content to place inside it.
        if self.start_line is not None and (old_lines or new_lines):
            end_line = self.start_line + max(len(old_lines), len(new_lines)) - 1
            if self.replace_all:
                header = f"  \033[2m@@ {name}:{self.start_line}-{end_line} (replace_all) @@\033[0m"
            else:
                header = f"  \033[2m@@ {name}:{self.start_line}-{end_line} @@\033[0m"
            result.append(header)
        from ..theme import current_theme
        theme = current_theme()
        _ADD = theme.diff_add_ansi
        _DEL = theme.diff_del_ansi
        _CTX = theme.diff_context_ansi
        _RESET = "\033[0m"
        offset = (self.start_line - 1) if self.start_line is not None else 0
        for tag, i1, i2, j1, j2 in matcher.get_opcodes():
            if tag == "equal":
                for i in range(max(0, i1 - 3), i1):
                    if i < len(old_lines):
                        result.append(f" {_CTX} {i+1+offset:4d}  {old_lines[i]}{_RESET}")
                if i2 - i1 > 10:
                    result.append(f"  ... ({i2 - i1} unchanged lines)")
                for i in range(i2, min(len(old_lines), i2 + 3)):
                    result.append(f" {_CTX} {i+1+offset:4d}  {old_lines[i]}{_RESET}")
            elif tag == "delete":
                for i in range(i1, i2):
                    result.append(f" {_DEL}−{i+1+offset:4d}  {old_lines[i]}{_RESET}")
            elif tag == "insert":
                for j in range(j1, j2):
                    result.append(f" {_ADD}+{j+1+offset:4d}  {new_lines[j]}{_RESET}")
            elif tag == "replace":
                for i in range(i1, i2):
                    result.append(f" {_DEL}−{i+1+offset:4d}  {old_lines[i]}{_RESET}")
                for j in range(j1, j2):
                    result.append(f" {_ADD}+{j+1+offset:4d}  {new_lines[j]}{_RESET}")
        return result

class SeparatorCell(HistoryCell):
    def __init__(self, char: str = "─", length: int | None = None, *, full_width: bool = False, label: str | None = None):
        self.char = char; self.length = length; self.full_width = full_width
        self.label = label
    def display_lines(self, width):
        # Codex renders the final-message separator as a full-width dim rule,
        # optionally carrying a "Worked for Xs" label in the middle (port of
        # ``FinalMessageSeparator`` from codex-rs/tui/src/history_cell.rs).
        #
        # The line is emitted to the terminal scrollback via Rich
        # ``console.print(line, markup=False)``. With embedded ANSI escapes
        # (``\033[2m ... \033[0m``) Rich's ``cell_len`` does **not** subtract the
        # escape sequences, so it counts the 6 escape bytes as visible columns
        # and wraps a line whose visible length is close to ``width`` onto a
        # second physical row. Empirically a dim line wraps once its visible
        # length exceeds ``width - 6`` (verified across widths 80/100/120/200
        # and both ``force_terminal`` modes). ``full_width`` mode therefore
        # reserves 6 columns so the separator stays on a single row regardless
        # of which width source (``shutil`` vs ``console.width``) fed it. The
        # default ``width - 4`` length is kept for the idle/TUI separator
        # window that shares layout margins with the input field.
        if self.full_width:
            budget = max(1, width - 6)
        elif self.length is not None:
            budget = self.length
        else:
            budget = width - 4
        if self.label:
            # Format: ─ {label} ──────  (label flanked by dashes, dim).
            # ``label`` must be plain text (no embedded ANSI) so the outer
            # ``\033[2m`` dim wraps the whole line cleanly.
            prefix = f"{self.char} {self.label} {self.char}"
            prefix_visible = len(prefix)
            if prefix_visible >= budget:
                return [f"\033[2m{prefix[:budget]}\033[0m"]
            dashes = self.char * (budget - prefix_visible)
            return [f"\033[2m{prefix}{dashes}\033[0m"]
        return [f"\033[2m{self.char * max(1, min(budget, width))}\033[0m"]
