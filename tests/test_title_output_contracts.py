"""Untrusted title text cannot insert controls into the OSC title payload."""

import io
from unittest.mock import Mock

import pytest

from factory.core.tui import terminal_title as title


@pytest.mark.parametrize('text,expected', [('', ''), ('  hello\n\t world  ', 'hello world'), ('Привет 🐙', 'Привет 🐙'), ('a\x1b\x07b', 'ab'), ('a\u202eb\u2066c\u2069', 'abc'), ('a\u034fb\ufe0fc', 'abc'), ('\x00\x1b\x07', '')])
def test_sanitizing_preserves_visible_text_and_removes_control_payloads(text, expected):
    actual = title.sanitize_terminal_title(text)
    assert actual == expected and title.sanitize_terminal_title(actual) == actual


@pytest.mark.parametrize('codepoint', [0x00ad, 0x034f, 0x061c, 0x180e, 0x200b, 0x200f, 0x202a, 0x202e, 0x2060, 0x206f, 0xfe00, 0xfe0f, 0xfeff, 0xfff9, 0xfffb, 0x1bca0, 0x1bca3, 0xe0100, 0xe01ef])
def test_invisible_and_directional_characters_never_reach_title_payload(codepoint):
    char = chr(codepoint)
    assert title.is_disallowed_terminal_title_char(char)
    assert title.sanitize_terminal_title('a' + char + 'b') == 'ab'


def test_unicode_limit_is_in_characters_and_never_leaves_outer_whitespace():
    value = title.sanitize_terminal_title('  ' + '界' * 300 + ' ')
    assert value == '界' * title.MAX_TERMINAL_TITLE_CHARS
    value = title.sanitize_terminal_title('a' * 239 + '  b')
    assert len(value) <= 240 and value == value.strip()
    assert not title.is_disallowed_terminal_title_char('')
    assert not title.is_disallowed_terminal_title_char('🐙')


@pytest.mark.parametrize('tty', [False, True])
def test_title_and_clear_emit_only_on_a_terminal(monkeypatch, tty):
    output = io.StringIO()
    output.isatty = lambda: tty
    monkeypatch.setattr(title.sys, 'stdout', output)
    assert title.set_terminal_title(' hello\nworld\x07\x1b ') == title.SetTerminalTitleResult.Applied
    title.clear_terminal_title()
    assert output.getvalue() == ('\x1b]0;hello world\x07\x1b]0;\x07' if tty else '')


def test_all_invisible_title_is_not_sent_to_real_terminal(monkeypatch):
    output = io.StringIO()
    output.isatty = lambda: True
    monkeypatch.setattr(title.sys, 'stdout', output)
    assert title.set_terminal_title('\x1b\x07') == title.SetTerminalTitleResult.NoVisibleContent
    assert output.getvalue() == ''


def test_broken_isatty_does_not_write_control_sequences(monkeypatch):
    output = Mock()
    output.isatty.side_effect = OSError('fixture detached stream')
    monkeypatch.setattr(title.sys, 'stdout', output)
    assert title.set_terminal_title('fixture') == title.SetTerminalTitleResult.Applied
    title.clear_terminal_title()
    output.write.assert_not_called()


def test_low_level_writer_preserves_exact_trusted_osc_shape():
    output = io.StringIO()
    title.write_set_window_title_ansi(output, 'Fixture title')
    assert output.getvalue() == '\x1b]0;Fixture title\x07'
