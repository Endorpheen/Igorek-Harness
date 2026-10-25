"""Source-range, indentation and URL preservation contracts for terminal wrapping."""

import pytest

from factory.core.tui import wrapping as w
from factory.core.tui.render.highlight import Style


@pytest.mark.parametrize(
    "text,width,expected",
    [
        ("one two", 10, ["> one two"]),
        ("alpha beta gamma", 10, ["> alpha", "  beta", "  gamma"]),
    ],
)
def test_indent_reduces_available_columns_exactly_once(text, width, expected):
    options = w.RtOptions(width).with_initial_indent("> ").with_subsequent_indent("  ")
    assert w.word_wrap_line(text, options) == expected


def test_multiple_logical_lines_keep_subsequent_indent():
    options = w.RtOptions(12).with_initial_indent("> ").with_subsequent_indent("  ")
    assert w.word_wrap_lines(["first", "second"], options) == ["> first", "  second"]
    assert w.adaptive_wrap_lines(["first", "second"], options) == [
        "> first",
        "  second",
    ]


@pytest.mark.parametrize(
    "text,width", [("alpha beta", 5), ("abc", 4), ("alpha  beta gamma", 7), ("", 2)]
)
def test_untrimmed_ranges_are_valid_exclusive_source_slices(text, width):
    ranges = w.wrap_ranges(text, width)
    assert all(0 <= start <= end <= len(text) for start, end in ranges)
    assert "".join(text[start:end] for start, end in ranges).split() == text.split()


@pytest.mark.parametrize(
    "text",
    [
        "See https://example.test/path now",
        "See   https://example.test/path   now",
        "  See https://example.test/path now",
    ],
)
def test_mixed_prose_wrap_preserves_url_and_all_word_content(text):
    rows = w.adaptive_wrap_line(text, w.RtOptions(15))
    assert any("https://example.test/path" in row for row in rows)
    assert " ".join(" ".join(rows).split()) == " ".join(text.split())


def test_long_url_is_not_broken_even_when_wider_than_viewport():
    url = "https://example.test/very-long-path"
    assert w.adaptive_wrap_line(url, w.RtOptions(5)) == [url]
    assert w.url_preserving_wrap_options(w.RtOptions(5)).break_words is False


@pytest.mark.parametrize(
    "token,expected",
    [
        ("https://example.test/path", True),
        ("custom+v1://resource", True),
        ("1bad://host", False),
        ("://host", False),
        ("no-scheme", False),
    ],
)
def test_scheme_prefix_requires_ascii_letter_and_valid_scheme_characters(
    token, expected
):
    assert w.has_valid_scheme_prefix(token) is expected


@pytest.mark.parametrize(
    "token,expected",
    [
        ("www.example.test", True),
        ("localhost:8080/path", True),
        ("127.0.0.1/path", True),
        ("localhost/path", True),
        ("example.test/path", True),
        ("example.test", False),
        ("example.test:99999/path", False),
    ],
)
def test_bare_url_heuristic_requires_hostname_and_supported_trailer(token, expected):
    assert w.is_bare_url_like(token) is expected


def test_decorative_list_markers_do_not_turn_link_only_lines_into_prose():
    assert not w.text_has_mixed_url_and_non_url_tokens("1. https://example.test")
    assert not w.text_has_mixed_url_and_non_url_tokens("• https://example.test")
    assert w.text_has_mixed_url_and_non_url_tokens("Read https://example.test")


def test_styled_span_slicing_preserves_exact_content_and_styles():
    first = Style(bold=True)
    second = Style(italic=True)
    line = [(first, "abc"), (second, "def")]
    flat, bounds = w.flatten_line(line)
    assert flat == "abcdef" and [pair[0] for pair in bounds] == [(0, 3), (3, 6)]
    assert w.slice_line_spans(line, bounds, (2, 5)) == [(first, "c"), (second, "de")]
    assert w.slice_line_spans(line, bounds, (3, 6)) == [(second, "def")]
    assert w.slice_line_spans("abcdef", [], (1, 4)) == "bcd"


def test_wrapped_line_mapping_ignores_only_synthetic_prefix_and_hyphen():
    assert w.map_owned_wrapped_line_to_range("  abcdef", 0, "> abc-", "> ") == (2, 5)
    assert w.borrowed_slice_range("abcdef", "cde") == (2, 5)
    assert w.borrowed_slice_range("abcdef", "missing") is None
    assert w.borrowed_slice_range("abcdef", "") is None


def test_options_builders_preserve_other_fields_and_original():
    original = w.RtOptions.new(12)
    changed = (
        original.with_width(8)
        .with_line_ending("\r\n")
        .with_initial_indent("> ")
        .with_subsequent_indent("  ")
        .with_break_words(False)
        .with_word_separator("ascii_space")
        .with_word_splitter("no_hyphenation")
        .with_wrap_algorithm("first_fit")
    )
    assert (
        original.width == 12 and original.initial_indent == "" and original.break_words
    )
    assert (
        changed.width == 8
        and changed.line_ending == "\r\n"
        and changed.initial_indent == "> "
        and not changed.break_words
    )
    assert w.word_wrap_line("", changed) == ["> "]


def test_line_input_ownership_adapters_preserve_data_and_styles():
    raw = [(Style(bold=True), "fixture")]
    assert w.LineInput.borrowed(raw).as_ref() is raw
    owned = w.LineInput.owned("fixture")
    assert w._into_line_input(owned) is owned
    assert w._into_line_input("text").as_ref() == "text"
    assert w._into_line_input(raw).as_ref() is raw
