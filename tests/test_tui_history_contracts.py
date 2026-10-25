"""Terminal byte-protocol contracts; these do not claim native PTY acceptance."""

import io
import json
from types import SimpleNamespace

import pytest
from wcwidth import wcswidth

from factory.core.tui import (
    insert_history as h,
    terminal_hyperlinks as links,
    text_formatting as f,
)
from factory.core.tui.render.text import Color, Line, Span, Style, Modifier
from factory.core.tui.render.highlight import Style as Highlight
from factory.core.tui.wrapping import RtOptions


@pytest.mark.parametrize(
    "color,background,expected",
    [
        (Color.reset(), False, "39"),
        (Color.reset(), True, "49"),
        (Color.red(), False, "31"),
        (Color.blue(), True, "44"),
        (Color.white(), False, "97"),
        (Color.dark_gray(), True, "100"),
        (Color.from_rgb(1, 2, 3), False, "38;2;1;2;3"),
        (Color.from_rgb(1, 2, 3), True, "48;2;1;2;3"),
        (Color.from_indexed(42), False, "38;5;42"),
        (Color.from_indexed(42), True, "48;5;42"),
    ],
)
def test_ansi_color_packets_use_sgr_color_selectors(color, background, expected):
    assert h._color_to_ansi(color, background) == f"\x1b[{expected}m"


@pytest.mark.parametrize(
    "modifier,added,removed",
    [
        (Modifier.BOLD, 1, 22),
        (Modifier.DIM, 2, 22),
        (Modifier.ITALIC, 3, 23),
        (Modifier.UNDERLINED, 4, 24),
        (Modifier.REVERSED, 7, 27),
        (Modifier.CROSSED_OUT, 9, 29),
        (Modifier.SLOW_BLINK, 5, 25),
        (Modifier.RAPID_BLINK, 6, 25),
    ],
)
def test_modifier_transitions_emit_inverse_packets(modifier, added, removed):
    output = io.BytesIO()
    h.ModifierDiff(Modifier.EMPTY, modifier).queue(output)
    h.ModifierDiff(modifier, Modifier.EMPTY).queue(output)
    assert output.getvalue() == f"\x1b[{added}m\x1b[{removed}m".encode()


def test_span_colors_and_modifiers_are_reset_after_output():
    output = io.BytesIO()
    h.write_spans(
        output,
        [
            Span("red", Style(fg=Color.red(), add_modifier=Modifier.BOLD)),
            Span("normal", Style()),
            Span("plain"),
        ],
    )
    packet = output.getvalue()
    assert b"\x1b[31m" in packet and b"\x1b[22m" in packet
    assert packet.endswith(b"plain\x1b[39m\x1b[49m\x1b[0m")
    output = io.BytesIO()
    h.ModifierDiff(Modifier.BOLD, Modifier.DIM).queue(output)
    assert output.getvalue().startswith(b"\x1b[22m\x1b[2m")


@pytest.mark.parametrize(
    "line,expected",
    [
        ("  content", "  "),
        ("\t text", "\t "),
        ("", ""),
        ("   ", "   "),
        (Line([Span(" "), Span("\t"), Span("text")]), " \t"),
    ],
)
def test_indent_is_preserved_across_spans(line, expected):
    assert h.leading_whitespace_prefix(line) == expected


def test_history_line_flattens_decorated_fragments_and_clears_wrapped_rows():
    output = io.BytesIO()
    h.write_history_line(output, links.HyperlinkLine("abcdefgh"), 4)
    assert (
        b"abcdefgh" in output.getvalue()
        and b"\x1b7" in output.getvalue()
        and b"\x1b8" in output.getvalue()
    )


class Terminal:
    def __init__(self, top=4, height=3, width=10, screen_height=20):
        self.backend = SimpleNamespace(size=lambda: h.Size(width, screen_height))
        self.backend_mut = io.BytesIO()
        self.viewport_area = SimpleNamespace(
            y=top,
            top=top,
            width=width,
            height=height,
            bottom=lambda: top + height,
            as_position=lambda: (0, top),
        )
        self.last_known_cursor_pos = h.CursorPos(2, top + 1)
        self.rows = []
        self.updates = []
        self.cleared = []

    def set_viewport_area(self, area):
        self.updates.append(area.y)

    def note_history_rows_inserted(self, rows):
        self.rows.append(rows)

    def clear_after_position(self, position):
        self.cleared.append(position)


@pytest.mark.parametrize(
    "mode", [h.InsertHistoryMode.Standard, h.InsertHistoryMode.ZellijRaw]
)
@pytest.mark.parametrize(
    "policy", [h.HistoryLineWrapPolicy.PreWrap, h.HistoryLineWrapPolicy.Terminal]
)
def test_history_insertion_serializes_rows_and_restores_cursor(mode, policy):
    terminal = Terminal()
    h.insert_history_lines_with_mode_and_wrap_policy(
        terminal, [Line("hello"), Line("world")], mode, policy
    )
    packet = terminal.backend_mut.getvalue()
    assert b"hello" in packet and b"world" in packet and packet.endswith(b"\x1b[6;3H")
    assert terminal.rows == [2] and terminal.updates == [6]
    assert bool(terminal.cleared) is (mode is h.InsertHistoryMode.ZellijRaw)


def test_default_history_adapter_wraps_long_prose_without_type_errors():
    terminal = Terminal(width=6)
    h.insert_history_lines(terminal, [Line("alpha beta gamma")])
    assert b"alpha" in terminal.backend_mut.getvalue() and terminal.rows[0] >= 3


def test_terminal_at_bottom_uses_scrollback_region_without_moving_viewport():
    terminal = Terminal(top=17, screen_height=20)
    h.insert_history_lines_with_wrap_policy(
        terminal, [Line("fixture")], h.HistoryLineWrapPolicy.Terminal
    )
    assert terminal.updates == [] and terminal.rows == [1]
    assert b"\x1b[1;17r" in terminal.backend_mut.getvalue()


@pytest.mark.parametrize(
    "url,expected",
    [
        ("https://example.test/a", "https://example.test/a"),
        ("http://example.test:81", "http://example.test:81"),
        ("javascript:alert(1)", None),
        ("file:///tmp/private", None),
        ("https:///missing", None),
        ("https://example.test/\x07x", "https://example.test/x"),
    ],
)
def test_hyperlink_destinations_only_allow_web_urls(url, expected):
    assert links.web_destination(url) == expected


def test_hyperlink_spans_are_separate_from_visible_text():
    line = links.HyperlinkLine.new("See ")
    line.push_span("docs", "https://example.test/docs")
    assert line.line == "See docs" and line.width() == 8
    assert [(link.columns, link.destination) for link in line.hyperlinks] == [
        ((4, 8), "https://example.test/docs")
    ]
    decorated = "".join(links.decorate_spans(line))
    assert (
        links.strip_osc8(decorated) == line.line
        and "\x1b]8;;https://example.test/docs\x07" in decorated
    )
    line.push_span("local", "file:///tmp/private")
    assert len(line.hyperlinks) == 1


def test_annotated_urls_trim_punctuation_and_keep_balanced_parentheses():
    line = links.annotate_web_urls_in_line(
        "See (https://example.test/a), https://example.test/b(c)."
    )
    assert [x.destination for x in line.hyperlinks] == [
        "https://example.test/a",
        "https://example.test/b(c)",
    ]
    assert links.visible_lines(links.annotate_web_urls(["plain"])) == ["plain"]


def test_wrapping_keeps_complete_link_destinations_on_each_fragment():
    source = links.HyperlinkLine(
        "aaaabbbb", [links.TerminalHyperlink((0, 8), "https://example.test")]
    )
    wrapped = links.remap_wrapped_line(source, ["aaaa", "bbbb"])
    assert [row.line for row in wrapped] == ["aaaa", "bbbb"]
    assert [row.hyperlinks[0].columns for row in wrapped] == [(0, 4), (0, 4)]
    assert all(
        row.hyperlinks[0].destination == "https://example.test" for row in wrapped
    )


def test_prefix_offsets_and_style_do_not_drop_semantic_links():
    line = links.HyperlinkLine(
        "abc", [links.TerminalHyperlink((0, 3), "https://example.test")]
    )
    prefixed = links.prefix_hyperlink_lines([line], "> ", "  ")[0]
    assert prefixed.line == "> abc" and prefixed.hyperlinks[0].columns == (2, 5)
    assert prefixed.style(Highlight(fg=(1, 2, 3), bold=True)).line.startswith("\x1b[")
    plain = links.adaptive_wrap_hyperlink_lines(
        links.plain_hyperlink_lines(["alpha beta", "gamma"]), RtOptions(5)
    )
    assert [row.line for row in plain] == ["alpha", "beta", "gamma"]


@pytest.mark.parametrize(
    "raw",
    [
        "\x1b]8;;https://example.test\x07label\x1b]8;;\x07",
        "\x1b]8;;https://example.test\x1b\\label\x1b]8;;\x1b\\",
    ],
)
def test_osc8_stripping_accepts_bell_and_st_terminators(raw):
    assert links.strip_osc8(raw) == "label"


@pytest.mark.parametrize(
    "value",
    [
        {"text": "hello world", "nested": [1, True, None]},
        {"escaped": 'a "quote" and \\ slash'},
        ["русский", "text"],
        42,
    ],
)
def test_json_compaction_preserves_values_and_string_whitespace(value):
    result = f.format_json_compact(json.dumps(value, ensure_ascii=False, indent=2))
    assert json.loads(result) == value and "\n" not in result


def test_invalid_json_and_tool_result_limits_are_safe():
    assert f.format_json_compact("not-json") is None
    assert f.format_and_truncate_tool_result("abcdefghijk", 2, 4) == "abc..."
    assert f.format_and_truncate_tool_result("anything", 0, 4) == ""


@pytest.mark.parametrize(
    "limit,expected",
    [(0, ""), (1, "a"), (2, "ab"), (3, "..."), (5, "ab..."), (8, "abcdef")],
)
def test_text_truncation_never_exceeds_requested_budget(limit, expected):
    assert f.truncate_text("abcdef", limit) == expected


@pytest.mark.parametrize(
    "path,width",
    [
        ("/home/example/project/deep/file.txt", 20),
        ("filename-without-separators", 5),
        ("/资料/项目/文件.txt", 10),
        ("/a/b/c/d/e/", 8),
        ("abc", 3),
        ("", 4),
    ],
)
def test_center_path_truncation_respects_display_columns(path, width):
    result = f.center_truncate_path(path, width)
    assert wcswidth(result) <= width
    if wcswidth(path) <= width:
        assert result == path
    else:
        assert "…" in result


@pytest.mark.parametrize(
    "items,expected",
    [
        ([], ""),
        (["one"], "one"),
        (["one", "two"], "one and two"),
        (["one", "two", "three"], "one, two and three"),
    ],
)
def test_human_list_punctuation(items, expected):
    assert f.proper_join(items) == expected


def test_capitalization_preserves_rest_and_cyrillic():
    assert f.capitalize_first("привет") == "Привет" and f.capitalize_first("") == ""


def test_style_patch_retains_base_colors_and_merges_modifiers_without_mutation():
    base = Style(fg=Color.red(), add_modifier=Modifier.BOLD)
    patched = base.patch(Style(bg=Color.blue(), add_modifier=Modifier.ITALIC))
    assert patched.fg == Color.red() and patched.bg == Color.blue()
    assert patched.add_modifier == Modifier.BOLD | Modifier.ITALIC
    assert base.bg is None and base.add_modifier == Modifier.BOLD
    assert base.bold().fg == base.fg


def test_line_copy_and_style_patch_do_not_rewrite_original_spans():
    original = Line([Span.styled("abc", Style(fg=Color.red())), Span.raw("def")])
    copied = Line(original)
    copied.spans.append(Span.raw("g"))
    assert original.width() == 6 and copied.width() == 7
    styled = original.patch_style(Style(bg=Color.blue()))
    assert all(span.style.bg == Color.blue() for span in styled.spans)
    assert all(span.style.bg is None for span in original.spans)
    assert Line(Span.raw("one")).width() == 3 and Line().width() == 0
