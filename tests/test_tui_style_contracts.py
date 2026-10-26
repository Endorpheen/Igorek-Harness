"""Shared styles preserve independent values and predictable theme contrast."""

import pytest

from factory.core.tui import style as s


@pytest.fixture(autouse=True)
def true_color(monkeypatch):
    monkeypatch.setenv('COLORTERM', 'truecolor')


def test_style_updates_are_independent_values_with_consistent_equality_and_hash():
    original = s.Style(fg='white', bg='black', italic=True, underline=True)
    foreground = original.with_fg('green')
    background = original.with_bg('blue')
    bold = original.with_bold()
    dim = original.with_dim()
    assert original == s.Style(fg='white', bg='black', italic=True, underline=True)
    assert foreground.fg == 'green' and foreground.bg == 'black'
    assert background.fg == 'white' and background.bg == 'blue'
    assert bold.bold and not original.bold and dim.dim and not original.dim
    assert all(value.italic and value.underline for value in [foreground, background, bold, dim])
    assert len({original, s.Style(fg='white', bg='black', italic=True, underline=True)}) == 1
    assert original != object() and foreground != original


@pytest.mark.parametrize('background,expected', [((255, 255, 255), (244, 244, 244)), ((0, 0, 0), (30, 30, 30))])
def test_message_and_plan_backgrounds_are_subtle_and_theme_relative(background, expected):
    message = s.user_message_style_for(background)
    plan = s.proposed_plan_style_for(background)
    assert message == plan and message.bg == ('rgb', expected)
    assert s.user_message_bg(background) == s.proposed_plan_bg(background) == ('rgb', expected)


def test_unknown_background_preserves_terminal_defaults():
    assert s.user_message_style_for(None) == s.Style()
    assert s.proposed_plan_style_for(None) == s.Style()
    assert s.accent_style_for(None) == s.Style(fg=('indexed', 6), bold=True)


@pytest.mark.parametrize('background,expected', [((255, 255, 255), ('rgb', (0, 95, 135))), ((0, 0, 0), ('indexed', 6))])
def test_accent_keeps_contrast_on_both_themes(background, expected):
    accent = s.accent_style_for(background)
    assert accent.fg == expected and accent.bold and accent.bg is None


@pytest.mark.parametrize('level,kind', [(s.StdoutColorLevel.TrueColor, 'rgb'), (s.StdoutColorLevel.Ansi256, 'indexed'), (s.StdoutColorLevel.Ansi16, None), (s.StdoutColorLevel.Unknown, None)])
def test_table_rule_falls_back_on_low_color_terminals(level, kind, monkeypatch):
    if level == s.StdoutColorLevel.Ansi256:
        monkeypatch.delenv('COLORTERM', raising=False)
        monkeypatch.setenv('TERM', 'xterm-256color')
    separator = s.table_separator_style_for((255, 255, 255), (0, 0, 0), level)
    if kind:
        assert separator.fg[0] == kind and not separator.dim
        if kind == 'rgb':
            assert separator.fg == ('rgb', (51, 51, 51))
    else:
        assert separator == s.Style(dim=True)
    assert s.table_separator_style_for(None, (0, 0, 0), level) == s.Style(dim=True)


def test_public_default_styles_use_selected_terminal_colors(monkeypatch):
    monkeypatch.setattr(s, 'default_bg', lambda: (255, 255, 255))
    monkeypatch.setattr(s, 'default_fg', lambda: (0, 0, 0))
    monkeypatch.setattr(s, 'stdout_color_level', lambda: s.StdoutColorLevel.TrueColor)
    assert s.user_message_style() == s.proposed_plan_style() == s.Style(bg=('rgb', (244, 244, 244)))
    assert s.accent_style() == s.Style(fg=('rgb', (0, 95, 135)), bold=True)
    assert s.table_separator_style() == s.Style(fg=('rgb', (204, 204, 204)))
