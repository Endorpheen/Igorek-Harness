"""Incremental row wrapping preserves code points and logical line boundaries."""

import pytest
from wcwidth import wcswidth

from factory.core.tui import live_wrap as wrap


def rebuild(rows):
    return ''.join(row.text + ('\n' if row.explicit_break else '') for row in rows)


@pytest.mark.parametrize('source,columns,prefix,suffix,used', [('abcdef', 3, 'abc', 'def', 3), ('Привет', 3, 'При', 'вет', 3), ('界ab', 2, '界', 'ab', 2), ('🐙ab', 2, '🐙', 'ab', 2), ('abc', 0, '', 'abc', 0), ('', 3, '', '', 0)])
def test_prefix_uses_display_columns_and_code_point_boundaries(source, columns, prefix, suffix, used):
    assert wrap.take_prefix_by_width(source, columns) == (prefix, suffix, used)


@pytest.mark.parametrize('source', ['abcdef\ngh\n\n', 'Привет\nмир', '界界ab\n文', '🐙a🐙b\n'])
@pytest.mark.parametrize('width', [1, 2, 3, 8])
def test_every_chunk_partition_preserves_all_text_and_completed_lines(source, width):
    for split in range(len(source) + 1):
        builder = wrap.RowBuilder(width)
        builder.push_fragment(source[:split])
        builder.push_fragment(source[split:])
        rows = builder.display_rows()
        assert rebuild(rows) == source
        assert all(wcswidth(row.text) <= width or len(row.text) == 1 for row in rows)


def test_resize_reflows_without_losing_explicit_blank_lines():
    builder = wrap.RowBuilder(3)
    builder.push_fragment('Привет\n\n界abc')
    for width in [8, 2, 1, 6]:
        builder.set_width(width)
        assert builder.width() == width
        assert rebuild(builder.display_rows()) == 'Привет\n\n界abc'
        assert all(row.width() <= width or len(row.text) == 1 for row in builder.display_rows())


def test_oversized_single_glyph_does_not_swallow_following_ascii():
    builder = wrap.RowBuilder(1)
    builder.push_fragment('界AB')
    assert [row.text for row in builder.display_rows()] == ['界', 'A', 'B']


def test_completed_rows_drain_in_order_but_partial_tail_is_retained():
    builder = wrap.RowBuilder(3)
    builder.push_fragment('abcdef\ngh')
    snapshot = builder.rows_snapshot()
    assert [(r.text, r.explicit_break) for r in snapshot] == [('abc', False), ('def', True)]
    snapshot.clear()
    assert len(builder.rows_snapshot()) == 2
    assert builder.drain_commit_ready(3) == []
    first = builder.drain_commit_ready(2)
    assert rebuild(first) == 'abc'
    rest = builder.drain_commit_ready(0)
    assert rebuild(first + rest + builder.display_rows()) == 'abcdef\ngh'
    assert [row.text for row in builder.display_rows()] == ['gh']
    builder.end_line()
    assert rebuild(builder.display_rows()) == 'gh\n'


def test_zero_width_clamps_and_empty_fragments_do_not_create_rows():
    builder = wrap.RowBuilder(0)
    builder.push_fragment('')
    assert builder.width() == 1 and builder.display_rows() == []
    builder.end_line()
    assert rebuild(builder.rows_snapshot()) == '\n'


def test_documented_fallback_counts_code_points_without_optional_wcwidth(monkeypatch):
    monkeypatch.setattr(wrap, '_HAVE_WCWIDTH', False)
    assert wrap.Row('界a').width() == 2
    assert wrap.take_prefix_by_width('界a', 1) == ('界', 'a', 1)
    assert wrap._char_width('') == 0


def test_unmeasurable_characters_and_strings_have_bounded_fallbacks(monkeypatch):
    monkeypatch.setattr(wrap, 'wcwidth', lambda value: -1)
    monkeypatch.setattr(wrap, 'wcswidth', lambda value: -1)
    assert wrap._char_width('\x00') == 0
    assert wrap.Row('x\x00').width() == 2


def test_combining_marks_stay_attached_to_the_visible_prefix():
    assert wrap.take_prefix_by_width('e\u0301a', 1) == ('e\u0301', 'a', 1)
    builder = wrap.RowBuilder(1)
    builder.push_fragment('e')
    builder.push_fragment('\u0301a')
    assert [row.text for row in builder.display_rows()] == ['e\u0301', 'a']
