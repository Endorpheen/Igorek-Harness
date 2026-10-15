"""Working-directory selection prompt (port of codex-rs/tui/src/cwd_prompt.rs).

The Python port is synchronous; the Rust source uses ``async`` because
``tui::Tui::event_stream`` is a ``tokio`` stream. The test module from
the source is **not** ported as part of this module.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Callable, Optional, Protocol

from .key_hint import (
    KeyCode,
    KeyEvent,
    KeyEventKind,
    KeyModifiers,
    plain,
)
from .render.renderable import ColumnRenderable, Insets, Renderable, RenderableItem
from .render.text import Line, Span, Style, Modifier


# ---------------------------------------------------------------------------
# Public enums
# ---------------------------------------------------------------------------

class CwdPromptAction(Enum):
    Resume = "resume"
    Fork = "fork"

    def verb(self) -> str:
        return {
            CwdPromptAction.Resume: "resume",
            CwdPromptAction.Fork: "fork",
        }[self]

    def past_participle(self) -> str:
        return {
            CwdPromptAction.Resume: "resumed",
            CwdPromptAction.Fork: "forked",
        }[self]


class CwdSelection(Enum):
    Current = "current"
    Session = "session"

    def next(self) -> "CwdSelection":
        return (
            CwdSelection.Session
            if self == CwdSelection.Current
            else CwdSelection.Current
        )

    def prev(self) -> "CwdSelection":
        return self.next()


class CwdPromptOutcomeKind(Enum):
    Selection = "selection"
    Exit = "exit"


@dataclass
class CwdPromptOutcome:
    kind: CwdPromptOutcomeKind
    selection: Optional[CwdSelection] = None


# ---------------------------------------------------------------------------
# Selection option row (port of selection_list::selection_option_row)
# ---------------------------------------------------------------------------

def selection_option_row(index: int, label: str, highlighted: bool) -> "OptionRow":
    """Return a renderable row representing a single selection option (port one-to-one)."""
    return OptionRow(index=index, label=label, highlighted=highlighted)


@dataclass
class OptionRow:
    """Renderable row for a single selection option."""
    index: int
    label: str
    highlighted: bool

    def render(self, area, buf) -> None:
        # In the Python port we don't have a real ratatui Buffer; render is a
        # no-op that exists so the screen can be invoked from
        # ``ColumnRenderable`` uniformly. The visual representation lives in
        # :meth:`as_lines` and is consumed by ``chat_render`` in the legacy
        # pipeline.
        return None

    def desired_height(self, width: int) -> int:
        return 1

    def cursor_pos(self, area):
        return None

    def cursor_style(self, area) -> str:
        return "default"

    def as_lines(self, width: int) -> list:
        marker = "▶ " if self.highlighted else "  "
        style = "highlight" if self.highlighted else "default"
        return [f"{marker}{self.label}"]


# ---------------------------------------------------------------------------
# FrameRequester (port of tui::FrameRequester)
# ---------------------------------------------------------------------------

class FrameRequester(Protocol):
    def schedule_frame(self) -> None: ...
    def schedule_frame_in(self, ms: int) -> None: ...


class _NullFrameRequester:
    def schedule_frame(self) -> None:
        return None

    def schedule_frame_in(self, ms: int) -> None:
        return None


# ---------------------------------------------------------------------------
# Screen + driver
# ---------------------------------------------------------------------------

@dataclass
class CwdPromptScreen:
    """The CWD selection screen state (port of ``CwdPromptScreen``)."""
    request_frame: FrameRequester
    action: CwdPromptAction
    current_cwd: str
    session_cwd: str
    highlighted: CwdSelection = CwdSelection.Session
    selection: Optional[CwdSelection] = None
    should_exit: bool = False

    @classmethod
    def new(
        cls,
        request_frame: FrameRequester,
        action: CwdPromptAction,
        current_cwd: str,
        session_cwd: str,
    ) -> "CwdPromptScreen":
        return cls(
            request_frame=request_frame,
            action=action,
            current_cwd=current_cwd,
            session_cwd=session_cwd,
            highlighted=CwdSelection.Session,
        )

    def handle_key(self, key_event: KeyEvent) -> None:
        """React to a key event (port of ``CwdPromptScreen::handle_key``)."""
        if key_event.kind == KeyEventKind.RELEASE:
            return
        if (key_event.modifiers & int(KeyModifiers.CONTROL)) and key_event.code in (
            KeyCode.CHAR_C,
            KeyCode.CHAR_D,
        ):
            self.selection = None
            self.should_exit = True
            self.request_frame.schedule_frame()
            return
        code = key_event.code
        if code == KeyCode.UP or code == KeyCode.CHAR_K:
            self.set_highlight(self.highlighted.prev())
        elif code == KeyCode.DOWN or code == KeyCode.CHAR_J:
            self.set_highlight(self.highlighted.next())
        elif code == KeyCode.CHAR_1:
            self.select(CwdSelection.Session)
        elif code == KeyCode.CHAR_2:
            self.select(CwdSelection.Current)
        elif code == KeyCode.ENTER:
            self.select(self.highlighted)
        elif code == KeyCode.ESC:
            self.select(CwdSelection.Session)

    def set_highlight(self, highlight: CwdSelection) -> None:
        if self.highlighted != highlight:
            self.highlighted = highlight
            self.request_frame.schedule_frame()

    def select(self, selection: CwdSelection) -> None:
        self.highlighted = selection
        self.selection = selection
        self.request_frame.schedule_frame()

    def is_done(self) -> bool:
        return self.should_exit or self.selection is not None

    def get_selection(self) -> Optional[CwdSelection]:
        return self.selection


# ---------------------------------------------------------------------------
# Driver: run_cwd_selection_prompt (synchronous, port of the async fn)
# ---------------------------------------------------------------------------

class TuiEventLike(Protocol):
    pass


class TuiLike(Protocol):
    def frame_requester(self) -> FrameRequester: ...
    def event_stream(self) -> object: ...
    def draw(self, height: int, render: "Callable[[object], None]") -> None: ...


def run_cwd_selection_prompt(
    tui: TuiLike,
    action: CwdPromptAction,
    current_cwd: Path,
    session_cwd: Path,
) -> CwdPromptOutcome:
    """Drive the CWD selection modal (port of ``run_cwd_selection_prompt``)."""
    screen = CwdPromptScreen.new(
        tui.frame_requester(),
        action,
        _display_path(current_cwd),
        _display_path(session_cwd),
    )

    def _do_draw(_frame: object) -> None:
        # In the Python port we don't have a real frame; the screen only
        # tracks its own state.
        return None

    tui.draw(2**16 - 1, _do_draw)
    return _drive_event_loop(tui, screen)


def _drive_event_loop(
    tui: TuiLike,
    screen: CwdPromptScreen,
) -> CwdPromptOutcome:
    """Synchronous event loop driving the CWD prompt (port of the async loop)."""
    while not screen.is_done():
        try:
            event = next(_iter_stream(tui.event_stream()))
        except StopIteration:
            break
        if event is None:
            break
        if _is_key_event(event):
            screen.handle_key(event)
        elif _is_paste_event(event):
            continue
        elif _is_draw_or_resize(event):
            tui.draw(2**16 - 1, lambda _f: None)
    if screen.should_exit:
        return CwdPromptOutcome(kind=CwdPromptOutcomeKind.Exit)
    return CwdPromptOutcome(
        kind=CwdPromptOutcomeKind.Selection,
        selection=screen.get_selection() or CwdSelection.Session,
    )


def _iter_stream(stream):
    """Yield items from a tokio-style async stream synchronously.

    The Python port expects a plain iterator (the ``Tui`` we have not
    yet ported will produce a real iterator).
    """
    if hasattr(stream, "__iter__"):
        yield from stream
    else:
        raise TypeError("Tui.event_stream() must return an iterable")


def _is_key_event(event) -> bool:
    return isinstance(event, KeyEvent) or _hasattr(event, "key") or hasattr(event, "code")


def _is_paste_event(event) -> bool:
    return hasattr(event, "paste") and not hasattr(event, "code")


def _is_draw_or_resize(event) -> bool:
    return not _is_key_event(event) and not _is_paste_event(event)


def _hasattr(obj, name) -> bool:
    return hasattr(obj, name)


def _display_path(p: Path) -> str:
    """Port of ``Path::display`` formatting."""
    return str(p)


# ---------------------------------------------------------------------------
# Renderable adapter for CwdPromptScreen
# ---------------------------------------------------------------------------

def _span_text(text: str, style: Optional[Style] = None) -> Span:
    return Span(content=text, style=style or Style())


def render_cwd_prompt_screen(screen: CwdPromptScreen, area=None, buf=None) -> None:
    """Render the CWD prompt screen (port of ``impl WidgetRef``)."""
    action_verb = screen.action.verb()
    action_past = screen.action.past_participle()
    column = ColumnRenderable()
    column.push(RenderableItem(_empty_renderable()))
    column.push(RenderableItem(_text_line([
        _span_text("Choose working directory to "),
        _span_text(action_verb, Style(add_modifier=Modifier.BOLD)),
        _span_text(" this session"),
    ])))
    column.push(RenderableItem(_empty_renderable()))
    column.push(RenderableItem(_text_line([
        _span_text(
            f"Session = latest cwd recorded in the {action_past} session",
            Style(),
        )
    ])))
    column.push(RenderableItem(_text_line([
        _span_text("Current = your current working directory", Style())
    ])))
    column.push(RenderableItem(_empty_renderable()))
    column.push(RenderableItem(selection_option_row(
        0,
        f"Use session directory ({screen.session_cwd})",
        screen.highlighted == CwdSelection.Session,
    )))
    column.push(RenderableItem(selection_option_row(
        1,
        f"Use current directory ({screen.current_cwd})",
        screen.highlighted == CwdSelection.Current,
    )))
    column.push(RenderableItem(_empty_renderable()))
    column.push(RenderableItem(_text_line([
        _span_text("Press ", Style()),
        _span_text(plain(KeyCode.ENTER), Style()),
        _span_text(" to continue", Style()),
    ])))
    if area is not None and buf is not None:
        column.render(area, buf)


def _text_line(spans) -> Renderable:
    return _LineRenderable(Line(spans=spans, style=Style()))


def _empty_renderable() -> Renderable:
    return _EmptyRenderable()


class _LineRenderable(Renderable):
    def __init__(self, line: Line) -> None:
        self.line = line

    def render(self, area, buf) -> None:
        return None

    def desired_height(self, width: int) -> int:
        return 1

    def cursor_pos(self, area):
        return None

    def cursor_style(self, area) -> str:
        return "default"


class _EmptyRenderable(Renderable):
    def render(self, area, buf) -> None:
        return None

    def desired_height(self, width: int) -> int:
        return 0

    def cursor_pos(self, area):
        return None

    def cursor_style(self, area) -> str:
        return "default"


__all__ = [
    "CwdPromptAction",
    "CwdPromptOutcome",
    "CwdPromptOutcomeKind",
    "CwdPromptScreen",
    "CwdSelection",
    "FrameRequester",
    "OptionRow",
    "render_cwd_prompt_screen",
    "run_cwd_selection_prompt",
    "selection_option_row",
]
