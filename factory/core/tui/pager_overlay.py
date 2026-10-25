"""Overlay UIs rendered in an alternate screen (port of pager_overlay.rs).

The Python port preserves the high-level structure: ``Overlay`` is a
discriminated union of :class:`TranscriptOverlay` and
:class:`StaticOverlay`, both backed by :class:`PagerView`. The rendering
calls are stubs because we don't have a real ratatui ``Buffer``/``Rect``
in the Python pipeline; the legacy :func:`chat_render` path still uses
``renderable.desired_height(width)`` to position overlays.

The test module from the source is **not** ported per the project's
"no tests" policy.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable, List, Optional, Protocol, Sequence, Tuple

from .key_hint import KeyCode, KeyEvent
from .render.renderable import InsetRenderable, Insets, Renderable
from .terminal_hyperlinks import HyperlinkLine


# ---------------------------------------------------------------------------
# PagerKeymap stub (full version comes from keymap.rs once ported)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class PagerKeyBindingList:
    """Port of ``keymap::PagerKeyBindingList``/``KeyBindingList``."""
    bindings: Tuple[KeyCode, ...] = ()


@dataclass(frozen=True)
class PagerKeymap:
    """Port of ``keymap::PagerKeymap``.

    Default bindings match the ``RuntimeKeymap::defaults().pager`` of the
    Rust source: arrow keys for scroll/page/jump, Esc/q to close.
    """
    scroll_up: PagerKeyBindingList = field(default_factory=lambda: PagerKeyBindingList((KeyCode.Up(),)))
    scroll_down: PagerKeyBindingList = field(default_factory=lambda: PagerKeyBindingList((KeyCode.Down(),)))
    page_up: PagerKeyBindingList = field(default_factory=lambda: PagerKeyBindingList((KeyCode.PageUp(),)))
    page_down: PagerKeyBindingList = field(default_factory=lambda: PagerKeyBindingList((KeyCode.PageDown(),)))
    jump_top: PagerKeyBindingList = field(default_factory=lambda: PagerKeyBindingList((KeyCode("home"),)))
    jump_bottom: PagerKeyBindingList = field(default_factory=lambda: PagerKeyBindingList((KeyCode("end"),)))
    close: PagerKeyBindingList = field(default_factory=lambda: PagerKeyBindingList((KeyCode.Esc(), KeyCode.Char("q"),)))
    close_transcript: PagerKeyBindingList = field(default_factory=lambda: PagerKeyBindingList((KeyCode.Char("t"),)))


# ---------------------------------------------------------------------------
# ActiveCellTranscriptKey (port of chatwidget::ActiveCellTranscriptKey)
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ActiveCellTranscriptKey:
    """Cache key for the active cell transcript (port one-to-one)."""
    width: int
    revision: int
    is_stream_continuation: bool
    animation_tick: Optional[int]


@dataclass(frozen=True)
class LiveTailKey:
    """Cache key for the live tail (port one-to-one)."""
    width: int
    revision: int
    is_stream_continuation: bool
    animation_tick: Optional[int]


# ---------------------------------------------------------------------------
# HistoryCell protocol (port of history_cell::HistoryCell)
# ---------------------------------------------------------------------------

class HistoryCellLike(Protocol):
    def as_any(self) -> Any: ...
    def is_stream_continuation(self) -> bool: ...
    def display_lines(self, width: int) -> List: ...
    def raw_lines(self) -> List: ...
    def transcript_lines(self, width: int) -> List: ...


# ---------------------------------------------------------------------------
# CachedRenderable / CellRenderable / HyperlinkLinesRenderable
# ---------------------------------------------------------------------------

class CachedRenderable(Renderable):
    """Stub ``Renderable`` that caches ``desired_height`` (port one-to-one)."""

    def __init__(self, inner: Renderable) -> None:
        self.inner = inner
        self._cache: dict = {}

    def render(self, area, buf) -> None:
        return self.inner.render(area, buf)

    def desired_height(self, width: int) -> int:
        if width not in self._cache:
            self._cache[width] = self.inner.desired_height(width)
        return self._cache[width]

    def cursor_pos(self, area):
        return self.inner.cursor_pos(area)

    def cursor_style(self, area) -> str:
        return self.inner.cursor_style(area)


@dataclass
class CellRenderable(Renderable):
    """Renderable wrapping a :class:`HistoryCell` (port one-to-one)."""
    cell: HistoryCellLike
    style: Any = None

    def render(self, area, buf) -> None:
        return None

    def desired_height(self, width: int) -> int:
        return len(self.cell.display_lines(width))

    def cursor_pos(self, area):
        return None

    def cursor_style(self, area) -> str:
        return "default"


@dataclass
class HyperlinkLinesRenderable(Renderable):
    """Renderable wrapping a list of ``HyperlinkLine`` (port one-to-one)."""
    lines: List[HyperlinkLine]

    def render(self, area, buf) -> None:
        return None

    def desired_height(self, width: int) -> int:
        return len(self.lines)

    def cursor_pos(self, area):
        return None

    def cursor_style(self, area) -> str:
        return "default"


# ---------------------------------------------------------------------------
# PagerView (port one-to-one)
# ---------------------------------------------------------------------------

@dataclass
class PagerView:
    renderables: List[Renderable]
    title: str
    keymap: PagerKeymap
    scroll_offset: int
    last_content_height: Optional[int] = None
    last_rendered_height: Optional[int] = None
    pending_scroll_chunk: Optional[int] = None

    @classmethod
    def new(
        cls,
        renderables: List[Renderable],
        title: str,
        scroll_offset: int,
        keymap: PagerKeymap,
    ) -> "PagerView":
        return cls(
            renderables=renderables,
            title=title,
            keymap=keymap,
            scroll_offset=scroll_offset,
        )

    def content_height(self, width: int) -> int:
        return sum(r.desired_height(width) for r in self.renderables)

    def render(self, area, buf) -> None:
        # Stub — the Python pipeline doesn't use ratatui Buffer.
        content_height = self.content_height(int(getattr(area, "width", 80)))
        self.last_content_height = content_height
        self.last_rendered_height = max(0, int(getattr(area, "height", 0)))
        if self.pending_scroll_chunk is not None:
            self.ensure_chunk_visible(self.pending_scroll_chunk, area)
            self.pending_scroll_chunk = None
        self.scroll_offset = min(
            self.scroll_offset,
            max(0, content_height - int(getattr(area, "height", 0))),
        )

    def render_header(self, area, buf) -> None:
        return None

    def render_content(self, area, buf) -> None:
        return None

    def render_bottom_bar(self, area, content_area, buf, content_height: int) -> None:
        return None

    def content_area(self, area):
        return area

    def is_scrolled_to_bottom(self) -> bool:
        if self.last_content_height is None or self.last_rendered_height is None:
            return False
        return self.scroll_offset >= max(
            0, (self.last_content_height or 0) - (self.last_rendered_height or 0)
        )

    def ensure_chunk_visible(self, idx: int, content_area) -> None:
        if idx >= len(self.renderables):
            return
        self.pending_scroll_chunk = None

    def scroll_chunk_into_view(self, idx: int) -> None:
        if idx >= len(self.renderables):
            return
        self.pending_scroll_chunk = idx

    def handle_key_event(self, tui: Any, event: KeyEvent) -> None:
        return None

    def update_last_content_height(self, h: int) -> None:
        self.last_content_height = int(h)


# ---------------------------------------------------------------------------
# TranscriptOverlay (port one-to-one)
# ---------------------------------------------------------------------------

class Overlay:
    pass


@dataclass
class TranscriptOverlay:
    view: PagerView
    cells: List[HistoryCellLike]
    highlight_cell: Optional[int] = None
    live_tail_key: Optional[LiveTailKey] = None
    is_done: bool = False

    @classmethod
    def new(cls, cells: List[HistoryCellLike], keymap: PagerKeymap) -> "TranscriptOverlay":
        return cls(
            view=PagerView.new(
                cls._render_cells(cells, None),
                "T R A N S C R I P T",
                2**64 - 1,
                keymap,
            ),
            cells=cells,
        )

    @staticmethod
    def _render_cells(
        cells: List[HistoryCellLike],
        highlight_cell: Optional[int],
    ) -> List[Renderable]:
        renderables: List[Renderable] = []
        for i, c in enumerate(cells):
            style_marker = _USER_HIGHLIGHT_STYLE if highlight_cell == i else _USER_STYLE
            cell_renderable: Renderable = CachedRenderable(CellRenderable(cell=c, style=style_marker))
            if not c.is_stream_continuation() and i > 0:
                cell_renderable = InsetRenderable(cell_renderable, Insets.tlbr(1, 0, 0, 0))
            renderables.append(cell_renderable)
        return renderables

    def insert_cell(self, cell: HistoryCellLike) -> None:
        follow_bottom = self.view.is_scrolled_to_bottom()
        had_prior_cells = bool(self.cells)
        tail = self._take_live_tail_renderable()
        self.cells.append(cell)
        self.view.renderables = self._render_cells(self.cells, self.highlight_cell)
        if tail is not None:
            if not had_prior_cells and self.live_tail_key is not None and not self.live_tail_key.is_stream_continuation:
                tail = InsetRenderable(tail, Insets.tlbr(1, 0, 0, 0))
            self.view.renderables.append(tail)
        if follow_bottom:
            self.view.scroll_offset = 2**64 - 1

    def replace_cells(self, cells: List[HistoryCellLike]) -> None:
        follow_bottom = self.view.is_scrolled_to_bottom()
        self.cells = cells
        if self.highlight_cell is not None and self.highlight_cell >= len(self.cells):
            self.highlight_cell = None
        self._rebuild_renderables()
        if follow_bottom:
            self.view.scroll_offset = 2**64 - 1

    def consolidate_cells(self, range, consolidated: HistoryCellLike) -> None:
        follow_bottom = self.view.is_scrolled_to_bottom()
        clamped_end = min(range.end, len(self.cells))
        clamped_start = min(range.start, clamped_end)
        if clamped_start < clamped_end:
            removed = clamped_end - clamped_start
            if self.highlight_cell is not None and self.highlight_cell >= clamped_start:
                if self.highlight_cell < clamped_end:
                    self.highlight_cell = clamped_start
                else:
                    self.highlight_cell = max(0, self.highlight_cell - (removed - 1))
            self.cells[clamped_start:clamped_end] = [consolidated]
            if self.highlight_cell is not None and self.highlight_cell >= len(self.cells):
                self.highlight_cell = None
            self._rebuild_renderables()
        if follow_bottom:
            self.view.scroll_offset = 2**64 - 1

    def sync_live_tail(
        self,
        width: int,
        active_key: Optional[ActiveCellTranscriptKey],
        compute_lines: Callable[[int], Optional[List[HyperlinkLine]]],
    ) -> None:
        next_key: Optional[LiveTailKey] = None
        if active_key is not None:
            next_key = LiveTailKey(
                width=width,
                revision=active_key.revision,
                is_stream_continuation=active_key.is_stream_continuation,
                animation_tick=active_key.animation_tick,
            )
        if self.live_tail_key == next_key:
            return
        follow_bottom = self.view.is_scrolled_to_bottom()
        self._take_live_tail_renderable()
        self.live_tail_key = next_key
        if next_key is not None:
            lines = compute_lines(width) or []
            if lines:
                self.view.renderables.append(self._live_tail_renderable(
                    lines, bool(self.cells), next_key.is_stream_continuation
                ))
        if follow_bottom:
            self.view.scroll_offset = 2**64 - 1

    def set_highlight_cell(self, cell: Optional[int]) -> None:
        self.highlight_cell = cell
        self._rebuild_renderables()
        if self.highlight_cell is not None:
            self.view.scroll_chunk_into_view(self.highlight_cell)

    def is_scrolled_to_bottom(self) -> bool:
        return self.view.is_scrolled_to_bottom()

    def _rebuild_renderables(self) -> None:
        tail = self._take_live_tail_renderable()
        self.view.renderables = self._render_cells(self.cells, self.highlight_cell)
        if tail is not None:
            self.view.renderables.append(tail)

    def _take_live_tail_renderable(self) -> Optional[Renderable]:
        if len(self.view.renderables) > len(self.cells):
            return self.view.renderables.pop()
        return None

    def _live_tail_renderable(
        self,
        lines: List[HyperlinkLine],
        has_prior_cells: bool,
        is_stream_continuation: bool,
    ) -> Renderable:
        renderable: Renderable = CachedRenderable(HyperlinkLinesRenderable(lines=lines))
        if has_prior_cells and not is_stream_continuation:
            renderable = InsetRenderable(renderable, Insets.tlbr(1, 0, 0, 0))
        return renderable

    def render_hints(self, area, buf) -> None:
        return None

    def render(self, area, buf) -> None:
        # Split: top 3 rows reserved for hints, rest is content.
        area_height = int(getattr(area, "height", 0))
        top_h = max(0, area_height - 3)
        # Stub: no real buffer in the Python port.
        self.view.render(area, buf)
        self.render_hints(area, buf)

    def handle_event(self, tui: Any, event: Any) -> bool:
        if _is_key_event(event):
            key_event = event
            if _matches_keymap(self.view.keymap.close, key_event) or _matches_keymap(self.view.keymap.close_transcript, key_event):
                self.is_done = True
                return True
            self.view.handle_key_event(tui, key_event)
            return True
        return False

    def is_done_p(self) -> bool:
        return self.is_done


# ---------------------------------------------------------------------------
# StaticOverlay (port one-to-one)
# ---------------------------------------------------------------------------

@dataclass
class StaticOverlay:
    view: PagerView
    is_done: bool = False

    @classmethod
    def with_title(
        cls,
        lines: List[Any],
        title: str,
        keymap: PagerKeymap,
    ) -> "StaticOverlay":
        # In Rust, Paragraph is a single renderable. In the Python port we
        # expose a LinesRenderable that approximates it.
        return cls.with_renderables(
            [_LinesRenderable(lines=lines)],
            title,
            keymap,
        )

    @classmethod
    def with_renderables(
        cls,
        renderables: List[Renderable],
        title: str,
        keymap: PagerKeymap,
    ) -> "StaticOverlay":
        return cls(view=PagerView.new(renderables, title, 0, keymap))

    def render(self, area, buf) -> None:
        self.view.render(area, buf)

    def render_hints(self, area, buf) -> None:
        return None

    def handle_event(self, tui: Any, event: Any) -> bool:
        if _is_key_event(event):
            key_event = event
            if _matches_keymap(self.view.keymap.close, key_event):
                self.is_done = True
                return True
            self.view.handle_key_event(tui, key_event)
            return True
        return False

    def is_done_p(self) -> bool:
        return self.is_done


@dataclass
class _LinesRenderable(Renderable):
    """Approximate ``Paragraph`` for the legacy line-list API."""
    lines: List[Any]

    def render(self, area, buf) -> None:
        return None

    def desired_height(self, width: int) -> int:
        return len(self.lines)

    def cursor_pos(self, area):
        return None

    def cursor_style(self, area) -> str:
        return "default"


# ---------------------------------------------------------------------------
# Overlay union (port one-to-one)
# ---------------------------------------------------------------------------

class OverlayKind(Enum):
    Transcript = "transcript"
    Static = "static"


@dataclass
class Overlay:
    kind: OverlayKind
    transcript: Optional[TranscriptOverlay] = None
    static: Optional[StaticOverlay] = None

    @classmethod
    def new_transcript(cls, cells: List[HistoryCellLike], keymap: PagerKeymap) -> "Overlay":
        return cls(kind=OverlayKind.Transcript, transcript=TranscriptOverlay.new(cells, keymap))

    @classmethod
    def new_static_with_lines(
        cls,
        lines: List[Any],
        title: str,
        keymap: PagerKeymap,
    ) -> "Overlay":
        return cls(
            kind=OverlayKind.Static,
            static=StaticOverlay.with_title(lines, title, keymap),
        )

    @classmethod
    def new_static_with_renderables(
        cls,
        renderables: List[Renderable],
        title: str,
        keymap: PagerKeymap,
    ) -> "Overlay":
        return cls(
            kind=OverlayKind.Static,
            static=StaticOverlay.with_renderables(renderables, title, keymap),
        )

    def handle_event(self, tui: Any, event: Any) -> bool:
        if self.kind == OverlayKind.Transcript and self.transcript is not None:
            return self.transcript.handle_event(tui, event)
        if self.kind == OverlayKind.Static and self.static is not None:
            return self.static.handle_event(tui, event)
        return False

    def is_done(self) -> bool:
        if self.kind == OverlayKind.Transcript and self.transcript is not None:
            return self.transcript.is_done_p()
        if self.kind == OverlayKind.Static and self.static is not None:
            return self.static.is_done_p()
        return False


# ---------------------------------------------------------------------------
# Style stubs (full versions come from style.rs once ported)
# ---------------------------------------------------------------------------

_USER_STYLE = "user_message"
_USER_HIGHLIGHT_STYLE = "user_message_highlight"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _matches_keymap(bindings: PagerKeyBindingList, event: KeyEvent) -> bool:
    return event.code in bindings.bindings


def _is_key_event(event: Any) -> bool:
    if isinstance(event, KeyEvent):
        return True
    if hasattr(event, "code") and hasattr(event, "modifiers"):
        return True
    return False


def first_or_empty(bindings: PagerKeyBindingList) -> List[KeyCode]:
    return list(bindings.bindings)


def render_key_hints(area, buf, pairs: Sequence[Tuple[Sequence[KeyCode], str]]) -> None:
    """Render footer key hints (port of ``render_key_hints``).

    In the Python port this only assembles the line content; the
    caller decides how to display it.  Returns the first line as a
    plain string for tests.
    """
    parts: List[str] = []
    for i, (keys, desc) in enumerate(pairs):
        if i > 0:
            parts.append("   ")
        parts.append("/".join(_key_label(k) for k in keys))
        parts.append(f" {desc}")
    return "".join(parts)


def _key_label(code: KeyCode) -> str:
    return getattr(code, "label", None) or str(code)


__all__ = [
    "ActiveCellTranscriptKey",
    "CachedRenderable",
    "CellRenderable",
    "HistoryCellLike",
    "HyperlinkLinesRenderable",
    "LiveTailKey",
    "Overlay",
    "OverlayKind",
    "PagerKeyBindingList",
    "PagerKeymap",
    "PagerView",
    "StaticOverlay",
    "TranscriptOverlay",
    "first_or_empty",
    "render_key_hints",
]
