"""Shared helpers for the rich CLI TUI adapters."""
from __future__ import annotations

import contextlib
import os
import re
import shutil
import subprocess
import sys
import tempfile
import textwrap
from datetime import datetime
from importlib.metadata import PackageNotFoundError, version as package_version
from pathlib import Path

from rich.markdown import Markdown
from rich.padding import Padding
from rich.panel import Panel
from rich.text import Text
from rich.cells import get_character_cell_size

try:
    from prompt_toolkit.document import Document
    from prompt_toolkit.formatted_text import ANSI, HTML
    from prompt_toolkit.layout.processors import Processor, Transformation
    from prompt_toolkit.patch_stdout import patch_stdout
except Exception:
    Document = None; ANSI = None; HTML = None
    Processor = object; Transformation = None; patch_stdout = None

_ANSI_RE = re.compile(r'\x1b?\[([0-9;]*)m')
_CSI_CONTROL_RE = re.compile(r"(?:\x1b|\^\[)\[[0-9;?]*[ -/]*[@-~]")

# Keep terminal-theme colors intact across live prompt_toolkit and scrollback Rich paths.
_ANSI_STYLE_MAP = {
    '0': '',
    '1': 'bold', '2': 'dim', '3': 'italic', '4': 'underline', '7': 'reverse',
    '30': 'fg:ansiblack',
    '31': 'fg:ansired', '32': 'fg:ansigreen', '33': 'fg:ansiyellow',
    '34': 'fg:ansiblue', '35': 'fg:ansimagenta', '36': 'fg:ansicyan',
    '37': 'fg:ansiwhite',
    '39': '',
    '40': 'bg:ansiblack',
    '41': 'bg:ansired', '42': 'bg:ansigreen', '43': 'bg:ansiyellow',
    '44': 'bg:ansiblue', '45': 'bg:ansimagenta', '46': 'bg:ansicyan',
    '47': 'bg:ansiwhite',
    '49': '',
    '90': 'fg:ansibrightblack',
    '91': 'fg:ansibrightred', '92': 'fg:ansibrightgreen',
    '93': 'fg:ansibrightyellow', '94': 'fg:ansibrightblue',
    '95': 'fg:ansibrightmagenta',     '96': 'fg:ansibrightcyan',
    '97': 'fg:ansiwhite',
    '100': 'bg:ansibrightblack',
    '101': 'bg:ansibrightred', '102': 'bg:ansibrightgreen',
    '103': 'bg:ansibrightyellow', '104': 'bg:ansibrightblue',
    '105': 'bg:ansibrightmagenta', '106': 'bg:ansibrightcyan',
    '107': 'bg:ansiwhite',
}

_STANDARD_16_FG = (
    'fg:ansiblack', 'fg:ansired', 'fg:ansigreen', 'fg:ansiyellow',
    'fg:ansiblue', 'fg:ansimagenta', 'fg:ansicyan', 'fg:ansiwhite',
    'fg:ansibrightblack', 'fg:ansibrightred',
    'fg:ansibrightgreen', 'fg:ansibrightyellow',
    'fg:ansibrightblue', 'fg:ansibrightmagenta',
    'fg:ansibrightcyan', 'fg:ansiwhite',
)
_STANDARD_16_BG = tuple(style.replace('fg:', 'bg:') for style in _STANDARD_16_FG)


def _lookup_256_fg(idx: int) -> str:
    if 0 <= idx < 16:
        return _STANDARD_16_FG[idx]
    r, g, b = _256_to_rgb(idx)
    return f'fg:#{r:02x}{g:02x}{b:02x}'


def _lookup_256_bg(idx: int) -> str:
    if 0 <= idx < 16:
        return _STANDARD_16_BG[idx]
    r, g, b = _256_to_rgb(idx)
    return f'bg:#{r:02x}{g:02x}{b:02x}'


def _empty_attrs() -> dict:
    return {'fg': None, 'bg': None, 'mods': set()}


def _split_style_attrs(style: str) -> dict:
    attrs = _empty_attrs()
    for token in style.split():
        if token.startswith('fg:'):
            attrs['fg'] = token
        elif token.startswith('bg:'):
            attrs['bg'] = token
        else:
            attrs['mods'].add(token)
    return attrs


def _render_attrs(attrs: dict) -> str:
    parts = sorted(attrs['mods'])
    if attrs['fg']:
        parts.append(attrs['fg'])
    if attrs['bg']:
        parts.append(attrs['bg'])
    return ' '.join(parts)


def _safe_int(value: str) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _parse_ansi_style(code: str, current_style: str) -> str:
    """Parse an SGR escape into a prompt_toolkit style string.

    ANSI named colors keep the terminal theme identical in live and scrollback.
    """
    if not code or code == '0':
        return ''
    parts = code.split(';')
    attrs = _split_style_attrs(current_style)
    i = 0
    while i < len(parts):
        part = parts[i]
        if part in ('38', '48') and i + 2 < len(parts) and parts[i + 1] == '5':
            lookup = _lookup_256_fg if part == '38' else _lookup_256_bg
            attrs['fg' if part == '38' else 'bg'] = lookup(_safe_int(parts[i + 2]))
            i += 3
            continue
        if part in ('38', '48') and i + 4 < len(parts) and parts[i + 1] == '2':
            r, g, b = (_safe_int(value) for value in parts[i + 2:i + 5])
            attrs['fg' if part == '38' else 'bg'] = f'{"fg" if part == "38" else "bg"}:#{r:02x}{g:02x}{b:02x}'
            i += 5
            continue
        if part == '0':
            attrs = _empty_attrs()
        elif part == '39':
            attrs['fg'] = None
        elif part == '49':
            attrs['bg'] = None
        elif part in ('22', '23', '24'):
            drop = {'22': {'bold', 'dim'}, '23': {'italic'}, '24': {'underline'}}[part]
            attrs['mods'] -= drop
        else:
            mapped = _ANSI_STYLE_MAP.get(part)
            if mapped:
                if mapped.startswith('fg:'):
                    attrs['fg'] = mapped
                elif mapped.startswith('bg:'):
                    attrs['bg'] = mapped
                else:
                    attrs['mods'].add(mapped)
        i += 1
    return _render_attrs(attrs)


def _256_to_rgb(idx: int) -> tuple:
    """Convert 256-color index to (r, g, b) tuple."""
    if idx < 16:
        # Standard 16 colors - rough approximations
        _16 = [(0,0,0),(128,0,0),(0,128,0),(128,128,0),(0,0,128),(128,0,128),
               (0,128,128),(192,192,192),(128,128,128),(255,0,0),(0,255,0),
               (255,255,0),(0,0,255),(255,0,255),(0,255,255),(255,255,255)]
        return _16[idx]
    if idx < 232:
        # 6x6x6 color cube
        idx -= 16
        b = idx % 6; idx //= 6
        g = idx % 6; idx //= 6
        r = idx
        return (r * 51 if r else 0, g * 51 if g else 0, b * 51 if b else 0)
    # Grayscale
    v = 8 + (idx - 232) * 10
    return (v, v, v)


def _ansi_to_formatted_text(text: str) -> list:
    """Convert ANSI-styled text to prompt_toolkit FormattedText tuples."""
    parts = []
    current_style = ''
    last_end = 0

    for m in _ANSI_RE.finditer(text):
        # Add text before this escape
        if m.start() > last_end:
            segment = text[last_end:m.start()]
            if segment:
                parts.append((current_style, segment))

        # Update style from ANSI code
        code = m.group(1)
        try:
            current_style = _parse_ansi_style(code, current_style)
        except Exception:
            # Never let an unknown SGR code crash the TUI renderer.
            pass

        last_end = m.end()

    # Add remaining text
    if last_end < len(text):
        parts.append((current_style, text[last_end:]))

    return parts if parts else [('', text)]

class _InputFieldProcessor(Processor):
    def apply_transformation(self, ti):
        if Transformation is None:
            return ti
        # Paint the whole input line with the composer surface, but PRESERVE
        # existing fragment styles: prompt_toolkit's HighlightSelectionProcessor
        # appends "class:selected" for mouse selection, and dropping it hides
        # the selection highlight.
        fragments = []
        for frag in ti.fragments:
            style = frag[0] if frag else ""
            text = frag[1] if len(frag) > 1 else ""
            rest = tuple(frag[2:])
            merged = f"class:input-field {style}".strip()
            fragments.append((merged, text, *rest))
        return Transformation(fragments)


def _read_input(ps) -> str:
    if ps is None: return input("> ")
    with (patch_stdout(raw=True) if patch_stdout else contextlib.nullcontext()):
        return ps.prompt()


def _drain_stdin():
    try:
        import select, termios, tty
        fd = sys.stdin.fileno(); old = termios.tcgetattr(fd)
        try:
            tty.setcbreak(fd)
            while select.select([sys.stdin],[],[],0.0)[0]: sys.stdin.read(1)
        finally: termios.tcsetattr(fd, termios.TCSADRAIN, old)
    except Exception: pass


def _factory_version():
    try: return package_version("igorek-agent-factory")
    except PackageNotFoundError: return "0.0.0"


def _branch():
    try:
        r = subprocess.run(["git","rev-parse","--abbrev-ref","HEAD"],capture_output=True,text=True,timeout=5)
        return r.stdout.strip() if r.returncode==0 else ""
    except Exception: return ""


def _truncate(s: str, n: int=60) -> str:
    return s if len(s)<=n else s[:n-1]+"…"


def _filter_control_chars(text: str) -> str:
    text = _CSI_CONTROL_RE.sub("", text)
    return re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]","",text)


def _install_screenshot_hotkey_noops(kb) -> None:
    """Swallow terminal screenshot hotkeys that may otherwise insert CSI text."""
    if kb is None:
        return
    for key in ("c-f11", "f11"):
        try:
            @kb.add(key)
            def _(event):  # pragma: no cover - depends on terminal key decoding
                return None
        except Exception:
            continue


def _user_msg(console, text: str):
    console.print(Padding(Panel(text, border_style="dim", padding=(0,1), style="on grey19"),(0,0,1,2)))


# ---------------------------------------------------------------------------
# Markdown table rendering (GFM) with fixed terminal width
# ---------------------------------------------------------------------------
# Mirrors the pattern used in OpenCode (internal/tui/styles/markdown.go):
# the table renderer is given an explicit width up front, computes column
# widths deterministically, and then draws rows as ASCII pipe-tables. This
# keeps the layout stable across re-renders and prevents long cells from
# blowing the line up.
#
# Activated by _split_text_and_tables(), called from _assistant_msg()
# BEFORE the text is handed to rich.markdown.Markdown. A GFM table block is
# a non-empty line starting with `|`, followed by a separator line of
# `|---|` / `|:---|` / `|---:|` / `|:---:|` form, followed by zero or more
# data rows starting with `|`.

_GFM_TABLE_RE = re.compile(
    r"(?:(?:^\|\s*[^\n]*\s*\|\s*\n)+)"            # header line(s) - first must be header
    r"(?:^\|[\s:|-]+\|\s*\n)"                       # separator line
    r"(?:^\|[^\n]*\|\s*\n?)*",                      # zero or more body rows
    re.MULTILINE,
)
_GFM_TABLE_FIRST = re.compile(r"^\|[^\n]*\|", re.MULTILINE)
_ANSI_ESCAPE_RE = re.compile(r"\033\[[0-9;]*m")


def _cell_display_width(text: str) -> int:
    """Display width of a cell (ANSI escapes ignored, CJK/emoji respected)."""
    if not text:
        return 0
    visible = _ANSI_ESCAPE_RE.sub("", text)
    return sum(get_character_cell_size(ch) for ch in visible)


def _table_cell_plain_text(text: str) -> str:
    """Return visible table-cell text without simple inline markdown markers."""
    text = _ANSI_ESCAPE_RE.sub("", str(text or ""))
    text = re.sub(r"!\[([^\]]*)\]\(([^)]+)\)", r"Image: \1", text)
    text = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r"\1", text)
    text = re.sub(r"`([^`]+)`", r"\1", text)
    text = re.sub(r"\*\*\*(.+?)\*\*\*", r"\1", text)
    text = re.sub(r"\*\*(.+?)\*\*", r"\1", text)
    text = re.sub(r"\*([^*]+?)\*", r"\1", text)
    text = re.sub(r"~~(.+?)~~", r"\1", text)
    return text


def _format_table_cell_inline(text: str) -> str:
    """Apply simple inline markdown styling inside a table cell.

    The table frame is still calculated from `_table_cell_plain_text()`;
    this function only injects ANSI escapes into already-sized content.
    """
    from .tui.theme import current_theme

    theme = current_theme()
    text = _ANSI_ESCAPE_RE.sub("", str(text or ""))
    text = re.sub(r"!\[([^\]]*)\]\(([^)]+)\)", r"Image: \1", text)
    text = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", rf"{theme.code_ansi}\1\033[0m", text)
    text = re.sub(r"`([^`]+)`", rf"{theme.code_ansi}\1\033[0m", text)
    text = re.sub(r"\*\*\*(.+?)\*\*\*", r"\033[1m\033[3m\1\033[0m", text)
    text = re.sub(r"\*\*(.+?)\*\*", r"\033[1m\1\033[0m", text)
    text = re.sub(r"\*([^*]+?)\*", r"\033[3m\1\033[0m", text)
    text = re.sub(r"~~(.+?)~~", r"\033[2m\1\033[0m", text)
    return text


def _wrap_cell(text: str, width: int) -> list[str]:
    """Wrap one cell to width display-columns; preserves hard newlines.

    Hard newlines inside the cell split it into sub-lines first; each
    sub-line is then wrapped independently. Empty input returns [""].
    """
    if width <= 0:
        return [text or ""]
    out: list[str] = []
    for line in text.split("\n"):
        if _cell_display_width(line) <= width:
            out.append(line)
            continue
        wrapped = textwrap.wrap(
            line,
            width=width,
            break_long_words=True,
            break_on_hyphens=False,
            replace_whitespace=True,
            drop_whitespace=True,
        )
        out.extend(wrapped or [line])
    return out or [""]


def _parse_alignment(sep_cell: str) -> str:
    s = sep_cell.strip().strip(":")
    left = sep_cell.strip().startswith(":")
    right = sep_cell.strip().endswith(":")
    if left and right:
        return "center"
    if right:
        return "right"
    return "left"


def _parse_gfm_table_block(block: str) -> tuple[list[str], list[str], list[list[str]]] | None:
    """Parse one GFM table block into (header_cells, aligns, body_rows)."""
    lines = [ln for ln in block.splitlines() if ln.strip()]
    if len(lines) < 2:
        return None
    header = [c.strip() for c in lines[0].strip().strip("|").split("|")]
    sep = lines[1].strip().strip("|").split("|")
    if len(sep) != len(header):
        return None
    sep_kinds = [s for s in sep if re.fullmatch(r":?-+:?", s.strip())]
    if len(sep_kinds) != len(header):
        return None
    aligns = [_parse_alignment(s) for s in sep]
    body: list[list[str]] = []
    for ln in lines[2:]:
        cells = [c.strip() for c in ln.strip().strip("|").split("|")]
        if len(cells) < len(header):
            cells += [""] * (len(header) - len(cells))
        elif len(cells) > len(header):
            cells = cells[: len(header) - 1] + ["\n".join(cells[len(header) - 1:])]
        body.append(cells)
    return header, aligns, body


def _compute_column_widths(
    header: list[str],
    body: list[list[str]],
    max_total: int,
    min_col: int = 3,
) -> list[int]:
    """Compute display-width per column, then shrink to fit max_total if needed.

    Each column starts at max(min_col, max cell width across header+body).
    The actual rendered total per row is
        sum(cols) + 4n (cell padding) + (n+1) "│"/"┤"/"├" borders
    so we shrink to keep the total <= max_total.
    """
    n = len(header)
    widths = [min_col] * n
    for i, h in enumerate(header):
        widths[i] = max(widths[i], _cell_display_width(_table_cell_plain_text(h)))
    for row in body:
        for i, c in enumerate(row):
            if i >= n:
                break
            widths[i] = max(widths[i], _cell_display_width(_table_cell_plain_text(c)))
    # rendered_total = sum(cols) + 2n (cell padding) + 2n (separator padding)
    #                 + (n+1) "│" / "┤" / "├" border chars
    rendered_total = sum(widths) + 4 * n + (n + 1)
    if rendered_total <= max_total:
        return widths
    # We need to shrink. To keep the frame intact, we never shrink a
    # column below the width of its header cell — the caller is expected
    # to wrap header cells (multi-row header) for extra space.
    header_widths = [max(min_col, _cell_display_width(_table_cell_plain_text(h))) for h in header]
    over = rendered_total - max_total
    while over > 0:
        # candidates: cols where we can shrink without going below header
        candidates = [
            (widths[i] - header_widths[i], i)
            for i in range(n)
            if widths[i] > header_widths[i]
        ]
        if not candidates:
            break
        candidates.sort(reverse=True)
        room, idx = candidates[0]
        if room <= 0:
            break
        take = min(room, over)
        widths[idx] -= take
        over -= take
    return widths


def _pad_cell(text: str, width: int, align: str) -> str:
    """Left/right/center pad a cell to `width` display-columns.

    If the cell text is wider than `width`, truncate it to fit (with a
    trailing `…`) so the column borders do not skew. This is a safety
    net — the caller should ensure column widths are wide enough for
    the header — but if budget pressure forces a narrower column, the
    frame stays intact.
    """
    if width <= 0:
        return ""
    text_w = _cell_display_width(text)
    if text_w > width:
        # truncate to width-1 and add `…`
        if width == 1:
            return "…"
        out: list[str] = []
        w = 0
        target = width - 1
        for ch in text:
            cw = get_character_cell_size(ch)
            if w + cw > target:
                break
            out.append(ch)
            w += cw
        return "".join(out) + "…"
    if text_w == width:
        return text
    extra = width - text_w
    if align == "right":
        return " " * extra + text
    if align == "center":
        left = extra // 2
        right = extra - left
        return " " * left + text + " " * right
    return text + " " * extra


def _draw_table(
    header: list[str],
    body: list[list[str]],
    aligns: list[str],
    widths: list[int],
) -> str:
    """Render a GFM table using plain ASCII pipe-table format.

    Output uses `|` for column borders and `-` for horizontal separators.
    Unicode box-drawing junctions are avoided because width mismatches between
    the separator and content rows break alignment.

    Pipe-table input and output retain the same form: `| a | b |`.
    """
    _table_debug_log(
        "draw_table: header=%r aligns=%r widths=%s body_rows=%d",
        header, aligns, widths, len(body),
    )
    n = len(header)

    def _wrap_raw_cell(raw: str, width: int) -> list[tuple[str, bool]]:
        plain = _table_cell_plain_text(raw)
        wrapped = _wrap_cell(plain, width)
        # Preserve inline styling when the cell fits on one visual line.
        # Wrapped/truncated fragments fall back to plain text so ANSI spans
        # cannot leak across lines or corrupt padding.
        if len(wrapped) == 1 and wrapped[0] == plain and _cell_display_width(plain) <= width:
            return [(raw, True)]
        return [(line, False) for line in wrapped]

    def _render_cell_fragment(fragment: str, formatted: bool) -> str:
        return _format_table_cell_inline(fragment) if formatted else fragment

    def _fmt_row(cells: list[str]) -> str:
        parts = ["|"]
        for i, c in enumerate(cells):
            parts.append(" ")
            parts.append(c)
            parts.append(" |")
        return "".join(parts)

    def _fmt_sep(widths: list[int]) -> str:
        parts = ["|"]
        for i, w in enumerate(widths):
            parts.append("-" * (w + 2))
            parts.append("|")
        return "".join(parts)

    out: list[str] = []

    head_wrapped = [_wrap_raw_cell(h, widths[i]) for i, h in enumerate(header)]
    head_h = max(len(w) for w in head_wrapped)
    for i in range(n):
        if len(head_wrapped[i]) < head_h:
            head_wrapped[i] = head_wrapped[i] + [("", False)] * (head_h - len(head_wrapped[i]))
    for line_i in range(head_h):
        cells = [
            _pad_cell(
                _render_cell_fragment(head_wrapped[i][line_i][0], head_wrapped[i][line_i][1]),
                widths[i],
                aligns[i],
            )
            for i in range(n)
        ]
        out.append(_fmt_row(cells))
    out.append(_fmt_sep(widths))

    for row in body:
        wrapped = [_wrap_raw_cell(c, widths[i]) for i, c in enumerate(row)]
        row_h = max(len(w) for w in wrapped)
        for i in range(n):
            if len(wrapped[i]) < row_h:
                wrapped[i] = wrapped[i] + [("", False)] * (row_h - len(wrapped[i]))
        for line_i in range(row_h):
            cells = [
                _pad_cell(
                    _render_cell_fragment(wrapped[i][line_i][0], wrapped[i][line_i][1]),
                    widths[i],
                    aligns[i],
                )
                for i in range(n)
            ]
            out.append(_fmt_row(cells))

    return "\n".join(out)


def _table_debug_log(fmt: str, *args) -> None:
    """Write a debug line to a log file.

    TUI swallows stderr, so we append to /tmp/igorek-table-debug.log.
    Logging is **always on** — the file is the only way to diagnose
    what the renderer is doing from outside the TUI. If the file is
    not appearing, that itself is the diagnostic signal (module not
    loaded, subprocess lost env, wrong path, etc).
    """
    import os
    try:
        msg = fmt % args
    except Exception:
        msg = f"{fmt} {args!r}"
    try:
        with open("/tmp/igorek-table-debug.log", "a", encoding="utf-8") as f:
            f.write(f"[table-debug] pid={os.getpid()} {msg}\n")
    except Exception:
        # never let debug logging crash the renderer
        pass


# Import-time marker: proves the module was loaded by *this* process.
# If this line never appears in /tmp/igorek-table-debug.log, the running
# process is still using a cached/stale copy of the module from sys.modules.
try:
    import os as _os_dbg
    with open("/tmp/igorek-table-debug.log", "a", encoding="utf-8") as _f_dbg:
        _f_dbg.write(
            f"[table-debug] pid={_os_dbg.getpid()} module cli_chat_rich_shared "
            f"imported; IGOREK_TABLE_DEBUG={_os_dbg.environ.get('IGOREK_TABLE_DEBUG')!r}\n"
        )
except Exception:
    pass


def _split_text_and_tables(text: str, width: int) -> list[tuple[str, str]]:
    """Split `text` into a sequence of (kind, content) chunks.

    `kind` is either "text" (regular markdown prose) or "table" (a
    rendered GFM table that should be printed as plain text). Splitting
    tables out of the text stream is necessary because Rich's
    `Markdown` parser reinterprets pipe-table syntax (`| a | b |`,
    `|---|---|`) and re-renders it with Unicode box-drawing characters,
    which conflicts with our own pipe-table rendering. By extracting
    the table blocks up front, we keep control of how they look.
    """
    if "|" not in text or width is None or width <= 4:
        return [("text", text)]
    max_total = max(8, width - 8) if width > 12 else max(8, width - 1)
    _table_debug_log(
        "split_text_and_tables: text_len=%d width=%d max_total=%d",
        len(text), width, max_total,
    )
    out: list[tuple[str, str]] = []
    lines = text.split("\n")
    buf: list[str] = []
    idx = 0
    n = len(lines)
    while idx < n:
        ln = lines[idx]
        if (
            ln.lstrip().startswith("|")
            and idx + 1 < n
            and re.fullmatch(r"\|[\s:|-]+\|", lines[idx + 1].strip())
        ):
            block_lines = [ln, lines[idx + 1]]
            j = idx + 2
            while j < n and lines[j].lstrip().startswith("|"):
                block_lines.append(lines[j])
                j += 1
            block = "\n".join(block_lines)
            parsed = _parse_gfm_table_block(block)
            if parsed is not None:
                # Flush buffered prose as a text chunk first
                if buf:
                    out.append(("text", "\n".join(buf)))
                    buf = []
                header, aligns, body = parsed
                widths = _compute_column_widths(header, body, max_total)
                rendered = _draw_table(header, body, aligns, widths)
                out.append(("table", rendered))
                _table_debug_log(
                    "rendered: lines=%d first_line_w=%d last_line_w=%d",
                    rendered.count("\n") + 1,
                    _cell_display_width(rendered.split("\n", 1)[0]),
                    _cell_display_width(rendered.rstrip("\n").split("\n")[-1]),
                )
                idx = j
                continue
        buf.append(ln)
        idx += 1
    if buf:
        out.append(("text", "\n".join(buf)))
    return out


def _preprocess_markdown_tables(text: str, width: int) -> str:
    """Backwards-compatible wrapper around `_split_text_and_tables`.

    Concatenates the chunks back into a single string. Kept because
    external callers (and earlier tests) imported this name; new code
    should call `_split_text_and_tables` directly so the table chunks
    can be printed as plain text without going back through Markdown.
    """
    return "\n".join(body for _kind, body in _split_text_and_tables(text, width))


def _split_markdown_code_blocks(text: str) -> list[tuple[str, str, str]]:
    blocks: list[tuple[str, str, str]] = []
    pattern = re.compile(r"```([a-zA-Z0-9_+.-]*)\n(.*?)```", re.DOTALL)
    cursor = 0
    for match in pattern.finditer(text):
        if match.start() > cursor:
            plain = text[cursor:match.start()].strip()
            if plain:
                blocks.append(("markdown", "", plain))
        language = (match.group(1) or "").strip().lower()
        code = match.group(2).rstrip("\n")
        blocks.append(("code", language, code))
        cursor = match.end()
    if cursor < len(text):
        tail = text[cursor:].strip()
        if tail:
            blocks.append(("markdown", "", tail))
    return blocks


def _strip_unbalanced_inline_backticks(text: str) -> str:
    """Drop the last unpaired inline backtick on each non-fenced line.

    CommonMark and rich.markdown display an unpaired `` ` `` literally. A model
    may emit three inline backticks, for example `` `__reasoning` block` ``, or a
    trailing backtick such as `` word ` ``. The stray marker then remains visible.
    Scan each line, ignoring fenced code blocks (``` ... ```) and backslash-escaped
    backticks (\\`). If the remaining inline single-backtick count is odd, remove
    the last one to balance the inline code markers.

    Behavior:
    * Preserve fenced code blocks (``` ... ```) verbatim, including their backticks.
    * Leave paired inline code (`code`) unchanged.
    * Leave escaped backticks (`\\``) unchanged.
    * Remove only the final inline backtick when the count is odd.
    * Return a new string without mutating the input.
    """
    fence_pattern = re.compile(
        r"(?<!`)```[^\n]*\n(?:[\s\S]*?\n)?```(?!`)"
    )
    parts: list[tuple[str, str]] = []
    cursor = 0
    for m in fence_pattern.finditer(text):
        if m.start() > cursor:
            parts.append(("text", text[cursor:m.start()]))
        parts.append(("code", m.group(0)))
        cursor = m.end()
    if cursor < len(text):
        parts.append(("text", text[cursor:]))

    def _fix_line(line: str) -> str:
        result: list[str] = []
        i = 0
        n = len(line)
        backticks: list[int] = []
        while i < n:
            ch = line[i]
            if ch == "\\" and i + 1 < n and line[i + 1] in ("`", "*", "_"):
                result.append(line[i : i + 2])
                i += 2
                continue
            if ch == "`":
                if i + 2 < n and line[i + 1] == "`" and line[i + 2] == "`":
                    result.append("```")
                    i += 3
                    continue
                backticks.append(len(result))
                result.append(ch)
                i += 1
                continue
            result.append(ch)
            i += 1
        if len(backticks) % 2 == 0:
            return "".join(result)
        pos = backticks[-1]
        del result[pos]
        return "".join(result)

    out: list[str] = []
    for kind, body in parts:
        if kind == "code":
            out.append(body)
        else:
            out.append("\n".join(_fix_line(line) for line in body.split("\n")))
    return "".join(out)


def _assistant_msg(console, text: str):
    from .tui.theme import current_theme
    theme = current_theme()
    text = _strip_unbalanced_inline_backticks(text)
    try:
        width = console.width or shutil.get_terminal_size((80, 24)).columns
    except Exception:
        width = 80

    def _print_chunk(kind: str, body: str) -> None:
        if not body.strip():
            return
        if kind == "table":
            # Tables are printed as plain text so Rich's Markdown parser
            # does not re-interpret our pipe-table and re-render it
            # with Unicode box-drawing.
            for ln in body.split("\n"):
                console.print(f"  {ln}")
            return
        has_md = any(k in body for k in ("```","**","* ","- ","## ","# ","> ","`","1. "))
        if has_md:
            console.print(Padding(
                Markdown(body, code_theme=theme.pygments_style, hyperlinks=True),
                (0, 0, 0, 2),
            ))
        else:
            for ln in body.rstrip().splitlines() or [""]:
                console.print(f"  {ln}")

    blocks = _split_markdown_code_blocks(text)
    if not blocks:
        blocks = [("markdown", "", text)]
    for block_type, _language, content in blocks:
        if block_type == "code":
            console.print(Padding(
                Text(content, style=theme.rich_code_style),
                (0, 0, 0, 2),
            ))
            continue
        for kind, body in _split_text_and_tables(content, width):
            _print_chunk(kind, body)


def _error_msg(console, text: str):
    for line in text.rstrip().splitlines() or [""]: console.print(f"  [red]{line}[/red]")


def _prompt(console):
    w = shutil.get_terminal_size((80,24)).columns
    console.print(f"  [dim]{'─'*(w-4)}[/dim]")


# ---------------------------------------------------------------------------
# Image clipboard paste (mirrors Codex TUI Ctrl+V image-paste behavior).
# ---------------------------------------------------------------------------

_IMAGE_ATTACH_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".webp"}
_MAX_ATTACH_BYTES = 8 * 1024 * 1024


def _resolve_image_path_from_text(text: str) -> str | None:
    """If ``text`` is a file:// URI or filesystem path to a supported
    image file, return the resolved absolute path. Otherwise None.
    """
    if not text:
        return None
    candidate = text.strip()
    if not candidate:
        return None
    if candidate.startswith("file://"):
        from urllib.parse import unquote, urlparse
        parsed = urlparse(candidate)
        if parsed.scheme != "file":
            return None
        candidate = unquote(parsed.path)
    candidate = candidate.strip().strip("\"'`")
    if not candidate:
        return None
    try:
        p = Path(candidate).expanduser()
    except (OSError, RuntimeError, ValueError):
        return None
    try:
        if not p.is_file():
            return None
    except (OSError, RuntimeError, ValueError):
        # Very long pasted text (e.g. a whole message) can exceed the
        # filesystem name limit; treat it as "not a path".
        return None
    if p.suffix.lower() not in _IMAGE_ATTACH_SUFFIXES:
        return None
    try:
        if p.stat().st_size > _MAX_ATTACH_BYTES:
            return None
    except OSError:
        return None
    return str(p)


def _clipboard_image_wl() -> str | None:
    try:
        result = subprocess.run(
            ["wl-paste", "--list-types"],
            capture_output=True, text=True, timeout=2,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        return None
    if result.returncode != 0:
        return None
    types = [line.strip() for line in result.stdout.splitlines()]

    image_type = next((t for t in types if t.startswith("image/")), None)
    if image_type:
        sub = image_type.split("/", 1)[1].lower()
        suffix = ".jpg" if sub == "jpeg" else f".{sub}"
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        try:
            tmp = tempfile.NamedTemporaryFile(
                prefix=f"clip_{stamp}_", suffix=suffix, delete=False,
            )
            tmp.close()
            with open(tmp.name, "wb") as out:
                subprocess.run(
                    ["wl-paste", "--type", image_type, "--no-newline"],
                    stdout=out, timeout=10, check=True,
                )
        except (subprocess.TimeoutExpired, subprocess.CalledProcessError, OSError):
            try:
                os.unlink(tmp.name)
            except OSError:
                pass
            return None
        if os.path.getsize(tmp.name) <= 0:
            try:
                os.unlink(tmp.name)
            except OSError:
                pass
            return None
        return tmp.name

    if "text/uri-list" in types:
        try:
            uri_result = subprocess.run(
                ["wl-paste", "--type", "text/uri-list", "--no-newline"],
                capture_output=True, text=True, timeout=2,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
            uri_result = None
        if uri_result is not None and uri_result.returncode == 0:
            for line in uri_result.stdout.splitlines():
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                path = _resolve_image_path_from_text(line)
                if path:
                    return path

    plain_types = [t for t in types if t.startswith("text/plain")]
    if plain_types:
        try:
            plain_result = subprocess.run(
                ["wl-paste", "--type", plain_types[0], "--no-newline"],
                capture_output=True, text=True, timeout=2,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
            plain_result = None
        if plain_result is not None and plain_result.returncode == 0:
            path = _resolve_image_path_from_text(plain_result.stdout)
            if path:
                return path

    return None


def _clipboard_image_x11() -> str | None:
    try:
        subprocess.run(
            ["xclip", "-version"], capture_output=True, timeout=2, check=True,
        )
    except (FileNotFoundError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return None

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    try:
        tmp = tempfile.NamedTemporaryFile(
            prefix=f"clip_{stamp}_", suffix=".png", delete=False,
        )
        tmp.close()
        with open(tmp.name, "wb") as out:
            result = subprocess.run(
                ["xclip", "-selection", "clipboard", "-t", "image/png", "-o"],
                stdout=out, timeout=10,
            )
    except (subprocess.TimeoutExpired, OSError):
        try:
            os.unlink(tmp.name)
        except OSError:
            pass
        return None
    if result.returncode == 0 and os.path.getsize(tmp.name) > 0:
        return tmp.name
    try:
        os.unlink(tmp.name)
    except OSError:
        pass

    try:
        uri_result = subprocess.run(
            ["xclip", "-selection", "clipboard", "-t", "text/uri-list", "-o"],
            capture_output=True, text=True, timeout=2,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        uri_result = None
    if uri_result is not None and uri_result.returncode == 0:
        for line in uri_result.stdout.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            path = _resolve_image_path_from_text(line)
            if path:
                return path

    try:
        plain_result = subprocess.run(
            ["xclip", "-selection", "clipboard", "-t", "text/plain", "-o"],
            capture_output=True, text=True, timeout=2,
        )
    except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
        plain_result = None
    if plain_result is not None and plain_result.returncode == 0:
        path = _resolve_image_path_from_text(plain_result.stdout)
        if path:
            return path

    return None


def _clipboard_text() -> str | None:
    """Return clipboard text via wl-paste/xclip/xsel, or ``None``."""
    import subprocess

    candidates: list[list[str]] = []
    if os.environ.get("WAYLAND_DISPLAY"):
        candidates.append(["wl-paste", "--no-newline"])
    if os.environ.get("DISPLAY"):
        candidates.extend([
            ["xclip", "-selection", "clipboard", "-o"],
            ["xsel", "--clipboard", "--output"],
        ])
    if not candidates:
        candidates = [
            ["wl-paste", "--no-newline"],
            ["xclip", "-selection", "clipboard", "-o"],
            ["xsel", "--clipboard", "--output"],
        ]
    for cmd in candidates:
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=2)
        except (FileNotFoundError, subprocess.TimeoutExpired, OSError):
            continue
        if result.returncode == 0 and result.stdout:
            return result.stdout
    return None


def _clipboard_image_path() -> str | None:
    """Read an image from the system clipboard and return a path
    (temp file for raw image bytes, or resolved file path for
    ``text/uri-list`` / ``text/plain`` that points to a real image).

    Tries Wayland first (``wl-paste``), then X11 (``xclip``).
    Returns ``None`` if the clipboard has no usable image.
    """
    try:
        if os.environ.get("WAYLAND_DISPLAY"):
            path = _clipboard_image_wl()
            if path:
                return path
        if os.environ.get("DISPLAY"):
            path = _clipboard_image_x11()
            if path:
                return path
    except Exception:
        # Never let clipboard probing (or long pasted text) crash the input
        # handler; a failed image lookup simply means "no image".
        return None
    return None


__all__ = [
    "_ANSI_RE", "_ANSI_STYLE_MAP", "_parse_ansi_style", "_256_to_rgb",
    "_ansi_to_formatted_text", "_InputFieldProcessor", "_read_input",
    "_drain_stdin", "_factory_version", "_branch", "_truncate",
    "_filter_control_chars", "_install_screenshot_hotkey_noops",
    "_user_msg", "_assistant_msg", "_error_msg",
    "_prompt", "ANSI", "HTML", "Document", "patch_stdout", "Processor",
    "Transformation",
    "_IMAGE_ATTACH_SUFFIXES", "_MAX_ATTACH_BYTES",
    "_resolve_image_path_from_text", "_clipboard_image_wl",
    "_clipboard_image_x11", "_clipboard_image_path", "_clipboard_text",
]
