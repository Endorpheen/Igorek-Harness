"""Uncovered public stream/width/turn APIs preserve boundaries and ownership."""

from concurrent.futures import ThreadPoolExecutor
import re
import threading

import pytest
from wcwidth import wcswidth

from factory.core.tui import line_truncation as trunc, width
from factory.core.tui.markdown_stream import MarkdownStreamCollector
from factory.core.turn_guard import TurnGuard


@pytest.mark.parametrize('line,expected', [('', 0), ('plain', 5), ('Привет', 6), ('界B', 3), ('🐙', 2), ('e\u0301', 1), ('\x1b[31m界B\x1b[0m', 3)])
def test_styled_line_width_counts_terminal_columns(line, expected):
    assert trunc.line_width(line) == expected


@pytest.mark.parametrize('line,limit,expected', [('abcdef', 3, 'abc'), ('Привет', 3, 'При'), ('界AB', 2, '界'), ('e\u0301a', 1, 'e\u0301'), ('abc', 0, ''), ('', 4, '')])
def test_truncation_preserves_code_points_and_combining_marks(line, limit, expected):
    assert trunc.truncate_line_to_width(line, limit) == expected


@pytest.mark.parametrize('line,limit,expected', [('abc', 3, 'abc'), ('abcdef', 3, 'ab…'), ('界界', 3, '界…'), ('e\u0301', 1, 'e\u0301'), ('abc', 0, '')])
def test_ellipsis_is_added_only_on_real_display_overflow(line, limit, expected):
    value = trunc.truncate_line_with_ellipsis_if_overflow(line, limit)
    assert value == expected and wcswidth(value) <= limit


def test_ansi_sequences_do_not_consume_truncation_budget():
    line = '\x1b[31mABCDEF\x1b[0m'
    value = trunc.truncate_line_to_width(line, 3)
    assert re.sub(r'\x1b\[[0-9;]*m', '', value) == 'ABC'
    assert value.startswith('\x1b[31m')
    assert trunc.truncate_line_to_width('\x1b[31m\x1b[0m', 3) == ''


@pytest.mark.parametrize('total,reserved,expected', [(80, 4, 76), (4, 4, None), (3, 4, None), (0, 0, None), (1, 0, 1)])
def test_content_width_guard_never_returns_nonpositive_width(total, reserved, expected):
    assert width.usable_content_width(total, reserved) == expected
    assert width.usable_content_width_u16(total, reserved) == expected


@pytest.mark.parametrize('source', ['Привет 🐙', 'one\ntwo\n', '```python\nprint(1)\n```', '\n\n'])
def test_every_stream_split_commits_each_source_character_once(source):
    for split in range(len(source) + 1):
        stream = MarkdownStreamCollector()
        received = ''
        for fragment in [source[:split], source[split:]]:
            stream.push_delta(fragment)
            committed = stream.commit_complete_source()
            if committed is not None:
                received += committed
            assert stream.commit_complete_source() is None
        received += stream.finalize_and_drain_source()
        assert received == (source if source.endswith('\n') else source + '\n')
        assert stream.buffer == '' and stream.committed_source_len == 0
        assert stream.finalize_and_drain_source() == ''


def test_stream_clear_discards_old_pending_tail_before_next_response():
    stream = MarkdownStreamCollector()
    stream.push_delta('completed\npending')
    assert stream.commit_complete_source() == 'completed\n'
    stream.clear()
    stream.push_delta('new')
    assert stream.finalize_and_drain_source() == 'new\n'


def test_turn_guard_allows_one_winner_under_real_concurrent_attempts():
    guard = TurnGuard()
    barrier = threading.Barrier(8)

    def compete():
        barrier.wait(timeout=3)
        return guard.try_start()

    with ThreadPoolExecutor(max_workers=8) as pool:
        outcomes = list(pool.map(lambda _: compete(), range(8)))
    assert sum(outcomes) == 1 and guard.is_active
    guard.end()
    assert not guard.is_active and guard.try_start()
    assert not guard.try_start()
    guard.end()
    guard.end()
    assert not guard.is_active
