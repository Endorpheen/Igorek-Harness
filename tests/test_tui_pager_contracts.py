"""Pager state contracts independent of a real terminal or legacy render stubs."""

from types import SimpleNamespace

import pytest

from factory.core.tui import pager_overlay as p
from factory.core.tui.key_hint import KeyCode, KeyEvent
from factory.core.tui.terminal_hyperlinks import HyperlinkLine


@pytest.fixture
def keymap():
    binding = lambda *codes: p.PagerKeyBindingList(tuple(codes))
    return p.PagerKeymap(
        scroll_up=binding(KeyCode.Up()),
        scroll_down=binding(KeyCode.Down()),
        page_up=binding(KeyCode.PageUp()),
        page_down=binding(KeyCode.PageDown()),
        jump_top=binding(KeyCode("home")),
        jump_bottom=binding(KeyCode("end")),
        close=binding(KeyCode.Esc(), KeyCode.Char("q")),
        close_transcript=binding(KeyCode.Char("t")),
    )


class Cell:
    def __init__(self, text, rows=1, continuation=False):
        self.text = text
        self.rows = rows
        self.continuation = continuation

    def is_stream_continuation(self):
        return self.continuation

    def display_lines(self, width):
        return [self.text] * self.rows


class Renderable:
    def __init__(self, height):
        self.height = height
        self.calls = []

    def desired_height(self, width):
        self.calls.append(("height", width))
        return self.height

    def render(self, area, buf):
        self.calls.append(("render", area, buf))

    def cursor_pos(self, area):
        return (1, 2)

    def cursor_style(self, area):
        return "fixture"


def test_default_keymap_uses_real_key_code_primitives():
    mapping = p.PagerKeymap()
    assert KeyCode.Up() in mapping.scroll_up.bindings
    assert KeyCode.Esc() in mapping.close.bindings
    assert KeyCode.Char("q") in mapping.close.bindings


def test_cached_height_is_keyed_by_width_and_delegation_remains_live():
    inner = Renderable(3)
    cached = p.CachedRenderable(inner)
    assert cached.desired_height(10) == cached.desired_height(10) == 3
    assert cached.desired_height(20) == 3 and inner.calls == [
        ("height", 10),
        ("height", 20),
    ]
    area = object()
    buffer = object()
    cached.render(area, buffer)
    assert inner.calls[-1] == ("render", area, buffer)
    assert cached.cursor_pos(area) == (1, 2) and cached.cursor_style(area) == "fixture"


def test_render_tracks_viewport_height_for_follow_bottom_detection(keymap):
    view = p.PagerView.new([Renderable(10)], "Fixture", 0, keymap)
    view.update_last_content_height(10)
    view.render(SimpleNamespace(width=80, height=3), None)
    assert not view.is_scrolled_to_bottom()
    view.scroll_offset = 7
    assert view.is_scrolled_to_bottom()


def test_pending_chunk_is_consumed_and_scroll_is_clamped(keymap):
    view = p.PagerView.new([Renderable(8)], "Fixture", 100, keymap)
    view.scroll_chunk_into_view(0)
    assert view.pending_scroll_chunk == 0
    view.render(SimpleNamespace(width=10, height=3), None)
    assert view.scroll_offset == 5 and view.pending_scroll_chunk is None
    view.scroll_chunk_into_view(99)
    assert view.pending_scroll_chunk is None


def test_transcript_insert_replace_and_highlight_preserve_cell_identity(keymap):
    first = Cell("first")
    second = Cell("second")
    transcript = p.TranscriptOverlay.new([first], keymap)
    transcript.insert_cell(second)
    assert transcript.cells == [first, second]
    transcript.set_highlight_cell(1)
    assert transcript.highlight_cell == 1 and transcript.view.pending_scroll_chunk == 1
    transcript.replace_cells([first])
    assert transcript.cells == [first] and transcript.highlight_cell is None


@pytest.mark.parametrize("highlight,expected", [(0, 0), (1, 1), (2, 1), (3, 2)])
def test_consolidation_rebases_highlight_without_losing_other_cells(
    keymap, highlight, expected
):
    cells = [Cell(str(i)) for i in range(4)]
    merged = Cell("merged")
    transcript = p.TranscriptOverlay.new(cells[:], keymap)
    transcript.highlight_cell = highlight
    transcript.consolidate_cells(SimpleNamespace(start=1, end=3), merged)
    assert (
        transcript.cells == [cells[0], merged, cells[3]]
        and transcript.highlight_cell == expected
    )


def test_live_tail_is_cached_by_key_and_survives_history_changes(keymap):
    transcript = p.TranscriptOverlay.new([Cell("history")], keymap)
    calls = []

    def compute(width):
        calls.append(width)
        return [HyperlinkLine("live")]

    key = p.ActiveCellTranscriptKey(20, 1, False, None)
    transcript.sync_live_tail(20, key, compute)
    transcript.sync_live_tail(20, key, compute)
    assert calls == [20] and len(transcript.view.renderables) == 2
    transcript.insert_cell(Cell("next"))
    assert len(transcript.view.renderables) == 3
    transcript.set_highlight_cell(0)
    assert len(transcript.view.renderables) == 3
    transcript.sync_live_tail(20, p.ActiveCellTranscriptKey(20, 2, True, None), compute)
    assert calls == [20, 20] and len(transcript.view.renderables) == 3
    transcript.sync_live_tail(20, None, compute)
    assert len(transcript.view.renderables) == 2


def test_live_tail_empty_content_and_first_history_insertion_are_safe(keymap):
    transcript = p.TranscriptOverlay.new([], keymap)
    transcript.sync_live_tail(
        20, p.ActiveCellTranscriptKey(20, 1, False, None), lambda _: []
    )
    assert transcript.view.renderables == []
    transcript.sync_live_tail(
        20,
        p.ActiveCellTranscriptKey(20, 2, False, None),
        lambda _: [HyperlinkLine("live")],
    )
    transcript.insert_cell(Cell("first"))
    assert len(transcript.view.renderables) == 2


@pytest.mark.parametrize(
    "kind,close",
    [
        ("static", KeyCode.Esc()),
        ("static", KeyCode.Char("q")),
        ("transcript", KeyCode.Char("t")),
        ("transcript", KeyCode.Esc()),
    ],
)
def test_overlay_close_bindings_and_non_key_events(keymap, kind, close):
    overlay = (
        p.Overlay.new_transcript([Cell("fixture")], keymap)
        if kind == "transcript"
        else p.Overlay.new_static_with_lines(["fixture"], "Title", keymap)
    )
    assert not overlay.is_done() and not overlay.handle_event(None, object())
    assert (
        overlay.handle_event(None, KeyEvent(KeyCode.Down(), 0))
        and not overlay.is_done()
    )
    assert overlay.handle_event(None, KeyEvent(close, 0)) and overlay.is_done()


def test_static_renderables_and_key_hints_keep_order_and_labels(keymap):
    overlay = p.Overlay.new_static_with_renderables(
        [Renderable(2), Renderable(3)], "Fixture", keymap
    )
    assert overlay.static.view.content_height(80) == 5
    assert p.first_or_empty(p.PagerKeyBindingList()) == []
    assert (
        p.render_key_hints(
            None,
            None,
            [
                ([KeyCode.Char("q")], "close"),
                ([KeyCode.Up(), KeyCode.Down()], "scroll"),
            ],
        )
        == "q close   up/down scroll"
    )


def test_incomplete_union_has_no_accidental_actions():
    overlay = p.Overlay(p.OverlayKind.Static)
    assert not overlay.is_done() and not overlay.handle_event(
        None, KeyEvent(KeyCode.Char("q"), 0)
    )


def test_transcript_follows_new_cells_only_when_already_at_bottom(keymap):
    transcript = p.TranscriptOverlay.new([Cell("first", rows=10)], keymap)
    area = SimpleNamespace(width=80, height=3)
    transcript.view.render(area, None)
    assert transcript.view.scroll_offset == 7 and transcript.is_scrolled_to_bottom()
    transcript.insert_cell(Cell("second", rows=2))
    transcript.view.render(area, None)
    assert transcript.view.scroll_offset == 10 and transcript.is_scrolled_to_bottom()
    transcript.view.scroll_offset = 0
    transcript.insert_cell(Cell("third", rows=2))
    transcript.view.render(area, None)
    assert transcript.view.scroll_offset == 0 and not transcript.is_scrolled_to_bottom()
