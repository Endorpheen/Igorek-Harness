"""Alternate-screen history viewer opened by the mouse wheel.

The viewer is a separate, managed prompt_toolkit Application. It runs in
the alternate screen (``full_screen=True``) so closing it restores the
chat untouched, including the draft, cursor, message queue and any active
task. It takes a snapshot on entry; generation keeps running but its
output never lands on the viewer screen.

Scrolling, selection-copy and per-cell tool expansion use the same
coordinate rules as the main chat. Tool cells are structured items with
stable IDs: an item exposes collapsed and expanded line variants and can
be toggled by clicking it.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, List, Optional

from .mouse_frame import (
    FrameRegion,
    FrameRow,
    ScreenFrame,
    Selection,
    extract_selection,
)
from .mouse_protocol import (
    MouseButton,
    MouseEventKind,
    parse_sgr_mouse_sequence,
)

try:
    from prompt_toolkit.application import Application
    from prompt_toolkit.formatted_text import ANSI
    from prompt_toolkit.key_binding import KeyBindings
    from prompt_toolkit.layout import HSplit, Layout, Window
    from prompt_toolkit.layout.controls import FormattedTextControl
    from prompt_toolkit.output import create_output
    from prompt_toolkit.styles import Style
    from prompt_toolkit.keys import Keys
except Exception:  # pragma: no cover
    Application = None  # type: ignore
    create_output = None  # type: ignore


@dataclass
class ViewerItem:
    """A structured, optionally expandable block in the viewer."""

    cell_id: str
    lines_collapsed: List[str]
    lines_expanded: List[str] = field(default_factory=list)
    expandable: bool = False


class MouseHistoryViewer:
    """A pager over a fixed snapshot of history lines or items."""

    def __init__(
        self,
        lines: Optional[List[str]] = None,
        *,
        items: Optional[List[ViewerItem]] = None,
        controller=None,
        title: str = "История",
    ) -> None:
        self.lines = list(lines or [])
        self.items = list(items or [])
        self.controller = controller
        self.title = title
        self.offset = 0
        self.selection: Optional[Selection] = None
        self._press: Optional[tuple[int, int]] = None
        self._dragging = False
        self._visible_height = 1
        self._notification = ""
        self._content: Optional[tuple[list[str], list[int]]] = None

    # ── geometry ───────────────────────────────────────────────────
    def _terminal_size(self):
        import shutil

        size = shutil.get_terminal_size((80, 24))
        return size.columns, size.lines

    def _content_height(self) -> int:
        _w, h = self._terminal_size()
        return max(1, h - 1)  # one row for the footer

    def _max_offset(self) -> int:
        return max(0, len(self._build_content()[0]) - self._content_height())

    def _build_content(self) -> tuple[list[str], list[int]]:
        if self._content is not None:
            return self._content
        flat: list[str] = []
        row_item: list[int] = []
        if not self.items:
            for line in self.lines:
                flat.append(line)
                row_item.append(-1)
        else:
            for idx, item in enumerate(self.items):
                expanded = bool(
                    item.expandable
                    and self.controller is not None
                    and self.controller.is_cell_expanded(item.cell_id)
                )
                lines = item.lines_expanded if expanded else item.lines_collapsed
                for line in lines:
                    flat.append(line)
                    row_item.append(idx)
        self._content = (flat, row_item)
        return self._content

    def _invalidate_content(self) -> None:
        self._content = None

    def _visible(self) -> List[str]:
        flat, _ = self._build_content()
        height = self._content_height()
        self._visible_height = height
        start = max(0, min(self.offset, self._max_offset()))
        self.offset = start
        return flat[start:start + height]

    def _build_frame(self) -> ScreenFrame:
        flat, row_item = self._build_content()
        height = self._content_height()
        start = self.offset
        rows: List[FrameRow] = []
        regions: dict[str, FrameRegion] = {}
        item_span: dict[int, list[int]] = {}
        for i in range(height):
            content_idx = start + i
            if content_idx >= len(flat):
                break
            item_idx = row_item[content_idx]
            region_id = f"item:{item_idx}" if item_idx >= 0 else ""
            rows.append(FrameRow(i, flat[content_idx], region_id=region_id))
            if item_idx >= 0:
                item_span.setdefault(item_idx, [i, i])[1] = i
        for item_idx, (top, bottom) in item_span.items():
            item = self.items[item_idx]
            regions[f"item:{item_idx}"] = FrameRegion(
                region_id=f"item:{item_idx}",
                kind="tool",
                cell_id=item.cell_id,
                top=top,
                bottom=bottom,
                expandable=item.expandable,
                header_row=top,
                tail_row=bottom,
            )
        return ScreenFrame(rows=rows, regions=regions, composer_top=None)

    # ── scrolling ──────────────────────────────────────────────────
    def scroll(self, delta: int) -> None:
        self.offset = max(0, min(self.offset + delta, self._max_offset()))

    def page(self, direction: int) -> None:
        self.scroll(direction * max(1, self._content_height() - 1))

    def to_top(self) -> None:
        self.offset = 0

    def to_bottom(self) -> None:
        self.offset = self._max_offset()

    # ── mouse ──────────────────────────────────────────────────────
    def _handle_mouse(self, data: str) -> bool:
        event = parse_sgr_mouse_sequence(data)
        if event is None:
            return False
        if event.kind is MouseEventKind.SCROLL_UP:
            self.scroll(-3)
            return True
        if event.kind is MouseEventKind.SCROLL_DOWN:
            self.scroll(3)
            return True
        if event.button is MouseButton.LEFT and not event.has_modifiers:
            if event.kind is MouseEventKind.PRESS:
                self._press = (event.y, event.x)
                self._dragging = False
                self.selection = None
                return True
            if event.kind is MouseEventKind.MOVE and self._press is not None:
                self._dragging = True
                self.selection = Selection(
                    anchor_row=self._press[0],
                    anchor_col=self._press[1],
                    focus_row=event.y,
                    focus_col=event.x,
                    frame_revision=0,
                )
                return True
            if event.kind is MouseEventKind.RELEASE:
                if self._dragging and self._press is not None:
                    selection = Selection(
                        anchor_row=self._press[0],
                        anchor_col=self._press[1],
                        focus_row=event.y,
                        focus_col=event.x,
                        frame_revision=0,
                    )
                    self._copy_selection(selection)
                else:
                    self._handle_click(event.y, event.x)
                self._press = None
                self._dragging = False
                self.selection = None
                return True
        return False

    def _handle_click(self, row: int, col: int) -> None:
        if not self.items or self.controller is None:
            return
        region = self._build_frame().region_for_row(row)
        if region is None or not region.expandable or not region.cell_id:
            return
        self.controller.toggle_cell(region.cell_id)
        self._invalidate_content()
        self.offset = min(self.offset, self._max_offset())

    def _copy_selection(self, selection: Selection) -> None:
        text = extract_selection(self._build_frame(), selection)
        if not text or not text.strip():
            return
        if self.controller is not None:
            result = self.controller.clipboard.copy(text)
            if result.message:
                self._notification = result.message

    # ── application ────────────────────────────────────────────────
    def build_application(self):
        if Application is None:
            return None
        kb = KeyBindings()

        @kb.add("escape")
        @kb.add("q")
        def _close(event):
            event.app.exit()

        @kb.add("up")
        @kb.add("k")
        def _up(event):
            self.scroll(-1)

        @kb.add("down")
        @kb.add("j")
        def _down(event):
            self.scroll(1)

        @kb.add("pageup")
        def _pgup(event):
            self.page(-1)

        @kb.add("pagedown")
        @kb.add("space")
        def _pgdn(event):
            self.page(1)

        @kb.add("home")
        def _home(event):
            self.to_top()

        @kb.add("end")
        def _end(event):
            self.to_bottom()

        @kb.add(Keys.Vt100MouseEvent)
        def _mouse(event):
            if self._handle_mouse(getattr(event, "data", "") or ""):
                event.app.invalidate()

        def _text():
            visible = self._visible()
            if self._notification:
                visible = [*visible, "", f"  {self._notification}"]
            return ANSI("\n".join(visible))

        def _footer():
            return ANSI(
                "  Esc/q — закрыть · ↑↓/j/k — строки · PgUp/PgDn — страницы · "
                "Home/End — края · колесо — прокрутка · клик — раскрыть"
            )

        style = Style.from_dict({"footer": "#6c6c6c"})
        root = HSplit(
            [
                Window(FormattedTextControl(_text), wrap_lines=False),
                Window(
                    FormattedTextControl(_footer),
                    height=1,
                    style="class:footer",
                ),
            ]
        )
        output = None
        mouse_support = False
        if self.controller is not None and create_output is not None:
            wrapped = self.controller.wrap_output(create_output())
            if wrapped is not None and getattr(wrapped, "supports_capture", False):
                output = wrapped
                mouse_support = True
        return Application(
            layout=Layout(root),
            key_bindings=kb,
            style=style,
            output=output,
            full_screen=True,
            mouse_support=mouse_support,
        )

    def run(self) -> None:
        app = self.build_application()
        if app is None:
            return
        if self.controller is not None:
            self.controller.set_viewer_active(True)
            self.controller.release_output()
        try:
            app.run()
        finally:
            if self.controller is not None:
                self.controller.set_viewer_active(False)


def run_history_viewer(lines: List[str], controller=None) -> None:
    MouseHistoryViewer(lines, controller=controller).run()


__all__ = ["MouseHistoryViewer", "ViewerItem", "run_history_viewer"]
