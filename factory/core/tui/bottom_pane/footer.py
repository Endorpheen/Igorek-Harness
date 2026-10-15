"""Footer rendering — port of Codex bottom_pane/footer.rs (lines 1–1750).

Footer modes, shortcut overlay, context window display, single-line collapse logic.
"""
from __future__ import annotations
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

FOOTER_INDENT_COLS = 2
FOOTER_CONTEXT_GAP_COLS = 1
FOOTER_SPACING_HEIGHT = 0
MODE_CYCLE_HINT = "shift+tab to cycle"


class FooterMode(Enum):
    QUIT_SHORTCUT_REMINDER = "quit"
    SHORTCUT_OVERLAY = "overlay"
    ESC_HINT = "esc_hint"
    COMPOSER_EMPTY = "empty"
    COMPOSER_HAS_DRAFT = "draft"


class SummaryHintKind(Enum):
    NONE = "none"
    SHORTCUTS = "shortcuts"
    QUEUE_MESSAGE = "queue_message"
    QUEUE_SHORT = "queue_short"


class CollaborationModeIndicator(Enum):
    PLAN = "plan"
    PAIR_PROGRAMMING = "pair_programming"
    EXECUTE = "execute"

    def label(self, show_cycle_hint: bool) -> str:
        suffix = f" ({MODE_CYCLE_HINT})" if show_cycle_hint else ""
        base = {self.PLAN: "Plan mode", self.PAIR_PROGRAMMING: "Pair Programming mode",
                self.EXECUTE: "Execute mode"}[self]
        return f"{base}{suffix}"


@dataclass
class LeftSideState:
    hint: SummaryHintKind = SummaryHintKind.NONE
    show_cycle_hint: bool = False


@dataclass
class FooterProps:
    mode: FooterMode = FooterMode.COMPOSER_EMPTY
    esc_backtrack_hint: bool = False
    use_shift_enter_hint: bool = False
    is_task_running: bool = False
    collaboration_modes_enabled: bool = False
    is_wsl: bool = False
    quit_shortcut_key: str = "ctrl+c"
    context_window_percent: Optional[int] = None
    context_window_used_tokens: Optional[int] = None
    status_line_value: Optional[str] = None
    status_line_enabled: bool = True
    active_agent_label: Optional[str] = None


class SummaryLeft(Enum):
    DEFAULT = "default"
    CUSTOM = "custom"
    NONE = "none"


# ─── formatting helpers ───────────────────────────────────────────

def _format_tokens_compact(tokens: int) -> str:
    if tokens >= 1_000_000:
        return f"{tokens / 1_000_000:.1f}M"
    if tokens >= 1_000:
        return f"{tokens / 1_000:.1f}k"
    return str(tokens)


def context_window_line(percent: Optional[int], used_tokens: Optional[int]) -> str:
    """Codex line 848: "{percent}% context left" or "{tokens} used"."""
    if percent is not None:
        p = max(0, min(100, percent))
        return f"{p}% context left"
    if used_tokens is not None:
        return f"{_format_tokens_compact(used_tokens)} used"
    return "100% context left"


def left_side_line(
    indicator: Optional[CollaborationModeIndicator],
    state: LeftSideState,
) -> str:
    """Codex line 271: Build left side of footer based on hint kind."""
    parts = []

    if state.hint == SummaryHintKind.SHORTCUTS:
        parts.append("? for shortcuts")
    elif state.hint == SummaryHintKind.QUEUE_MESSAGE:
        parts.append("tab to queue message")
    elif state.hint == SummaryHintKind.QUEUE_SHORT:
        parts.append("tab to queue")

    if indicator is not None:
        if state.hint != SummaryHintKind.NONE:
            parts.append("·")
        parts.append(indicator.label(state.show_cycle_hint))

    return " ".join(parts)


def quit_shortcut_reminder_line(key: str) -> str:
    """Codex line 731."""
    return f"{key} again to quit"


def esc_hint_line(esc_backtrack_hint: bool) -> str:
    """Codex line 735."""
    if esc_backtrack_hint:
        return "esc again to edit previous message"
    return "esc esc to edit previous message"


def footer_from_props_lines(
    props: FooterProps,
    collaboration_mode_indicator: Optional[CollaborationModeIndicator] = None,
    show_cycle_hint: bool = False,
    show_shortcuts_hint: bool = False,
    show_queue_hint: bool = False,
) -> list[str]:
    """Codex line 580: canonical FooterMode → text mapping."""
    if props.mode == FooterMode.QUIT_SHORTCUT_REMINDER:
        return [quit_shortcut_reminder_line(props.quit_shortcut_key)]

    if props.mode == FooterMode.ESC_HINT:
        return [esc_hint_line(props.esc_backtrack_hint)]

    if props.mode == FooterMode.COMPOSER_EMPTY:
        hint = SummaryHintKind.SHORTCUTS if show_shortcuts_hint else SummaryHintKind.NONE
        state = LeftSideState(hint=hint, show_cycle_hint=show_cycle_hint)
        return [left_side_line(collaboration_mode_indicator, state)]

    if props.mode == FooterMode.COMPOSER_HAS_DRAFT:
        if show_queue_hint:
            hint = SummaryHintKind.QUEUE_MESSAGE
        elif show_shortcuts_hint:
            hint = SummaryHintKind.SHORTCUTS
        else:
            hint = SummaryHintKind.NONE
        state = LeftSideState(hint=hint, show_cycle_hint=show_cycle_hint)
        return [left_side_line(collaboration_mode_indicator, state)]

    return []


def shows_passive_footer_line(props: FooterProps) -> bool:
    """Codex line 665."""
    if props.mode == FooterMode.COMPOSER_EMPTY:
        return True
    if props.mode == FooterMode.COMPOSER_HAS_DRAFT:
        return not props.is_task_running
    return False


def passive_footer_status_line(props: FooterProps) -> Optional[str]:
    """Codex line 638: contextual status line when footer is passive."""
    if not shows_passive_footer_line(props):
        return None
    if props.status_line_enabled and props.status_line_value:
        line = props.status_line_value
        if props.active_agent_label:
            line = f"{line} · {props.active_agent_label}"
        return line
    if props.active_agent_label:
        return props.active_agent_label
    return None


def right_aligned_x(area_width: int, content_width: int, right_padding: int = 2) -> Optional[int]:
    """Codex line 481: compute right-aligned x position."""
    if area_width <= 0 or content_width <= 0:
        return None
    max_width = max(0, area_width - right_padding)
    if content_width >= max_width:
        return right_padding
    return area_width - content_width - right_padding


def can_show_left_with_context(area_width: int, left_width: int, context_width: int) -> bool:
    """Codex line 518."""
    cx = right_aligned_x(area_width, context_width)
    if cx is None:
        return True
    if left_width <= 0:
        return True
    left_extent = FOOTER_INDENT_COLS + left_width + FOOTER_CONTEXT_GAP_COLS
    return left_extent <= cx


def render_footer_line(line: str, indent: int = FOOTER_INDENT_COLS) -> str:
    """Codex line 213: prefix footer line with indent."""
    return " " * indent + line


def footer_height(props: FooterProps) -> int:
    """Codex line 573: number of rows needed by footer."""
    if props.mode == FooterMode.SHORTCUT_OVERLAY:
        return 4  # shortcut overlay takes multiple rows
    return 1


def shortcut_overlay_lines(width: int = 120) -> list[str]:
    """Codex shortcut overlay: adaptive layout that fits terminal width.

    At narrow widths: single column, all entries stacked.
    At medium widths: two columns.
    At wide widths: two columns with spacing.
    """
    entries = [
        ("/", "commands"),
        ("!", "shell commands"),
        ("ctrl+j", "newline"),
        ("tab", "queue message"),
        ("@", "file paths"),
        ("?", "shortcuts"),
        ("ctrl+c", "exit"),
        ("esc", "edit previous"),
    ]
    
    indent = "  "
    col_spacing = 4
    usable = width - len(indent)
    
    # Try 2 columns, fall back to 1
    rows = (len(entries) + 1) // 2
    
    # Check if 2 columns fit
    col_widths = [0, 0]
    for i, (key, label) in enumerate(entries):
        col = i // rows
        entry_w = len(key) + 1 + len(label)
        col_widths[col] = max(col_widths[col], entry_w)
    
    if col_widths[0] + col_widths[1] + col_spacing <= usable:
        # Two columns
        lines = []
        for r in range(rows):
            left_idx = r
            right_idx = r + rows
            key, label = entries[left_idx]
            line = indent + f"{key} {label}"
            if right_idx < len(entries):
                rkey, rlabel = entries[right_idx]
                pad = col_widths[0] - len(key) - 1 - len(label) + col_spacing
                line += " " * max(0, pad) + f"{rkey} {rlabel}"
            lines.append(line)
        return lines
    else:
        # Single column
        return [indent + f"{key} {label}" for key, label in entries]


def render_footer_from_props(
    props: FooterProps,
    collaboration_mode_indicator: Optional[CollaborationModeIndicator] = None,
    show_cycle_hint: bool = False,
    show_shortcuts_hint: bool = False,
    show_queue_hint: bool = False,
) -> list[str]:
    """Codex line 229: render footer lines with indent prefix."""
    lines: list[str] = []
    indent = " " * FOOTER_INDENT_COLS

    passive = passive_footer_status_line(props)
    if passive and not props.is_task_running:
        lines.append(f"{indent}{passive}")
        return lines

    if props.mode == FooterMode.SHORTCUT_OVERLAY:
        for sl in shortcut_overlay_lines(80):
            lines.append(f"{indent}{sl}")
        return lines

    body = footer_from_props_lines(
        props, collaboration_mode_indicator,
        show_cycle_hint, show_shortcuts_hint, show_queue_hint,
    )
    ctx = context_window_line(props.context_window_percent, props.context_window_used_tokens)

    if body and ctx:
        body_width = len(body[0])
        if can_show_left_with_context(80, body_width, len(ctx)):
            x = right_aligned_x(80, len(ctx))
            if x is not None:
                pad = x - FOOTER_INDENT_COLS - body_width - FOOTER_CONTEXT_GAP_COLS
                if pad > 0:
                    body[0] = body[0] + " " * pad + ctx

    return [f"{indent}{b}" for b in body]


def render_footer_with_context(
    props: FooterProps,
    width: int = 80,
    show_cycle_hint: bool = False,
    show_shortcuts_hint: bool = False,
    show_queue_hint: bool = False,
) -> list[str]:
    """Complete footer rendering with context window alignment.

    Returns lines ready for display in the terminal.
    """
    lines: list[str] = []
    indent = " " * FOOTER_INDENT_COLS

    # Passive mode: show contextual status line when idle
    if shows_passive_footer_line(props) and not props.is_task_running:
        passive = passive_footer_status_line(props)
        if passive:
            lines.append(f"{indent}{passive}")
            return lines

    # Shortcut overlay: two-column key bindings
    if props.mode == FooterMode.SHORTCUT_OVERLAY:
        overlay_lines = shortcut_overlay_lines(80)
        for sl in overlay_lines:
            pad = max(0, width - len(sl) - FOOTER_INDENT_COLS)
            lines.append(f"{indent}{sl}")
        return lines

    # Active modes: left hint + right context
    body = footer_from_props_lines(
        props, None, show_cycle_hint,
        show_shortcuts_hint, show_queue_hint,
    )

    ctx_text = context_window_line(
        props.context_window_percent,
        props.context_window_used_tokens,
    )

    if body:
        body_line = body[0]
        if ctx_text:
            body_width = len(body_line)
            ctx_width = len(ctx_text)
            available = width - FOOTER_INDENT_COLS - FOOTER_CONTEXT_GAP_COLS

            if body_width + ctx_width <= available:
                pad = available - body_width - ctx_width
                body_line = body_line + " " * pad + ctx_text
            elif body_width <= available:
                body_line = body_line

        lines.append(f"{indent}{body_line}")
    elif ctx_text:
        lines.append(f"{indent}{ctx_text}")

    return lines


def footer_height_from_props(props: FooterProps) -> int:
    """Calculate footer height based on mode."""
    if props.mode == FooterMode.SHORTCUT_OVERLAY:
        return len(shortcut_overlay_lines(80))
    return 1
