"""Live code rendering, theme fallback and safe lexer budget contracts."""

import re
from pathlib import Path
from unittest.mock import Mock

import pytest
from pygments.token import Token
from factory.core.tui.render import highlight as h


@pytest.fixture(autouse=True)
def isolated_theme(monkeypatch):
    for name in ("_theme", "_theme_override", "_codex_home", "_active_theme_name"):
        monkeypatch.setattr(h, name, None)
    monkeypatch.setattr(h, "_syntax_set_initialized", False)
    monkeypatch.delenv("COLORFGBG", raising=False)


@pytest.mark.parametrize("name", ["ansi", "base16", "base16-256", "catppuccin-latte", "nord"])
def test_builtin_theme_resolves_and_validates(name):
    assert h.validate_theme_name(name, None) is None
    assert h.resolve_theme_by_name(name, None).name == name
    assert h.resolve_theme_with_override(name, None).name == name


@pytest.mark.parametrize("setting,expected", [("", "catppuccin-mocha"), ("0;7", "catppuccin-latte"), ("7;0", "catppuccin-mocha"), ("0;255", "catppuccin-latte"), ("0;not-a-color", "catppuccin-mocha"), ("7", "catppuccin-mocha")])
def test_default_theme_tracks_known_background_without_terminal_io(monkeypatch, setting, expected):
    monkeypatch.setenv("COLORFGBG", setting)
    assert h.adaptive_default_theme_name() == expected
    assert h.configured_theme_name() == expected
    assert h.build_default_theme().name == expected


@pytest.mark.parametrize("content,valid", [("<plist><dict></dict></plist>", True), ("", False), ("not a theme", False)])
def test_custom_theme_validation_and_resolution_use_temporary_files(tmp_path, content, valid):
    folder = tmp_path / "themes"
    folder.mkdir()
    (folder / "fixture.tmTheme").write_text(content)
    loaded = h.load_custom_theme("fixture", tmp_path)
    assert (loaded is not None) is valid
    warning = h.validate_theme_name("fixture", tmp_path)
    assert (warning is None) is valid
    assert h.resolve_theme_by_name("fixture", tmp_path) == loaded
    chosen = h.resolve_theme_with_override("fixture", tmp_path)
    assert chosen.name == ("fixture" if valid else "catppuccin-mocha")


def test_missing_or_unreadable_custom_theme_falls_back(tmp_path, monkeypatch):
    assert h.load_custom_theme("missing", tmp_path) is None
    assert "not found" in h.validate_theme_name("missing", tmp_path)
    assert "$CODEX_HOME" in h.validate_theme_name("missing", None)
    assert h.resolve_theme_by_name("missing", None) is None
    folder = tmp_path / "themes"
    folder.mkdir()
    (folder / "blocked.tmTheme").write_text("<dict>")
    monkeypatch.setattr(Path, "read_text", Mock(side_effect=PermissionError("fixture denied")))
    assert h.load_custom_theme("blocked", tmp_path) is None
    assert "could not be loaded" in h.validate_theme_name("blocked", tmp_path)


def test_theme_picker_sorts_filters_and_deduplicates_custom_names(tmp_path):
    folder = tmp_path / "themes"
    folder.mkdir()
    for name in ["Zulu", "alpha", "ansi"]:
        (folder / (name + ".tmTheme")).write_text("<dict></dict>")
    (folder / "invalid.tmTheme").write_text("bad")
    (folder / "ignored.txt").write_text("<dict>")
    entries = h.list_available_themes(tmp_path)
    names = [e.name for e in entries]
    assert names == sorted(names, key=lambda x: (x.lower(), x))
    assert names.count("ansi") == 1
    assert {e.name for e in entries if e.is_custom} == {"Zulu", "alpha"}
    assert {e.name for e in h.list_available_themes(None)} == set(h.BUILTIN_THEME_NAMES)


def test_theme_override_applies_before_and_after_lazy_initialization(tmp_path):
    assert h.set_theme_override("nord", tmp_path) is None
    assert h.configured_theme_name() == "nord"
    assert h.theme_lock().name == "nord"
    assert h.set_theme_override("nord", tmp_path) is None
    assert h.theme_lock().name == "nord"
    h._ensure_syntax_set()
    h._ensure_syntax_set()
    assert h.syntax_set_initialized()


def test_theme_preview_snapshot_and_scope_style_queries():
    inserted, deleted = (1, 2, 3), (4, 5, 6)
    theme = h.Theme("fixture", h.Style(fg=(10, 20, 30), bold=True, italic=True, underline=True), [h.ThemeItem("diff.inserted", h.Style(bg=inserted)), h.ThemeItem("markup.deleted", h.Style(bg=deleted)), h.ThemeItem("name", h.Style(fg=(7, 8, 9), bold=True))])
    h.set_syntax_theme(theme)
    snapshot = h.current_syntax_theme()
    assert snapshot.name == "fixture" and snapshot.settings == theme.settings
    assert snapshot is not theme and snapshot.settings is not theme.settings
    assert h.diff_scope_background_rgbs() == h.DiffScopeBackgroundRgbs(inserted, deleted)
    assert h.foreground_style_for_scopes(["missing", "name"]) == h.Style(fg=(7, 8, 9), bold=True)
    assert h.foreground_style_for_scopes_with_theme(theme, ["missing"]) is None
    assert h.diff_scope_background_rgbs_for_theme(h.Theme()) == h.DiffScopeBackgroundRgbs()


@pytest.mark.parametrize("lang,expected", [("python3", "python"), ("golang", "go"), ("csharp", "c#"), ("c-sharp", "c#"), ("shell", "bash")])
def test_live_lexer_aliases(lang, expected):
    assert h.find_syntax(lang) == expected


@pytest.mark.parametrize("code", ["", "hello", "hello\n", "hello\n\n", "привет 🐙\nsecond"])
def test_unknown_language_plain_fallback_preserves_text(code):
    result = h.highlight_code_to_lines(code, "fixture-unknown-language")
    expected = code.split("\n")
    if code.endswith("\n"):
        expected.pop()
    assert result == (expected or [""])
    assert h.highlight_code_to_styled_spans(code, "fixture-unknown-language") is None


@pytest.mark.parametrize("kind", ["bytes", "lines", "unicode-bytes"])
def test_oversized_code_never_enters_the_lexer(monkeypatch, kind):
    code = {"bytes": "x" * (h.MAX_HIGHLIGHT_BYTES + 1), "lines": "x\n" * h.MAX_HIGHLIGHT_LINES, "unicode-bytes": "🐙" * (h.MAX_HIGHLIGHT_BYTES // 4 + 1)}[kind]
    forbidden = Mock(side_effect=AssertionError("Oversized code reached lexer"))
    monkeypatch.setattr(h, "get_lexer_by_name", forbidden)
    assert h.highlight_code_to_styled_spans(code, "python") is None
    assert h.highlight_code_to_lines(code, "python")
    forbidden.assert_not_called()


@pytest.mark.parametrize("byte_count,line_count,expected", [(0, 0, False), (524288, 10000, False), (524289, 10000, True), (524288, 10001, True)])
def test_exact_guardrail_boundaries(byte_count, line_count, expected):
    assert h.exceeds_highlight_limits(byte_count, line_count) is expected


@pytest.mark.parametrize("token,flags", [(Token.Keyword, (True, False, False)), (Token.Name.Function, (True, False, False)), (Token.Name.Class, (True, False, False)), (Token.Comment.Single, (False, True, False)), (Token.Name.Variable, (False, True, False)), (Token.Name.Builtin, (False, False, True)), (Token.Text, (False, False, False))])
def test_real_pygments_token_objects_receive_declared_styles(token, flags):
    style = h._pygments_token_to_style(token)
    assert (style.bold, style.italic, style.underline) == flags


def test_real_python_lexer_keeps_unicode_and_function_styling():
    code = 'def привет():\n    return "🐙"\n'
    spans = h.highlight_code_to_styled_spans(code, "python")
    flat = [(style, text) for line in spans for style, text in line]
    assert any("привет" in text and style.bold for style, text in flat)
    rendered = h.highlight_code_to_lines(code, "python")
    plain = [re.sub(r"\x1b\[[0-9;]*m", "", line) for line in rendered]
    assert "\n".join(plain) + "\n" == code
    assert h.highlight_bash_to_lines("echo hello")


def test_ansi_render_keeps_plain_text_and_applies_all_modifiers():
    assert h._style_to_ansi(h.Style(), "plain") == "plain"
    assert h._style_to_ansi(h.Style(fg=(1, 2, 3), bold=True, italic=True, underline=True), "🐙") == "\x1b[38;2;1;2;3;1;3;4m🐙\x1b[0m"


def test_lexer_lookup_can_recover_on_second_attempt(monkeypatch):
    fake = Mock(side_effect=[h.ClassNotFound("first"), object()])
    monkeypatch.setattr(h, "get_lexer_by_name", fake)
    assert h.find_syntax("fixture") == "fixture"
    assert fake.call_args.kwargs == {"stripall": True}


def test_lexer_disappearing_after_resolution_falls_back(monkeypatch):
    monkeypatch.setattr(h, "get_lexer_by_name", Mock(side_effect=[object(), h.ClassNotFound("gone")]))
    assert h.highlight_code_to_styled_spans("fixture", "fixture") is None


def test_markdown_code_fence_uses_live_highlighter():
    from factory.core.tui.markdown_render import render_markdown_text
    lines = render_markdown_text('```python\ndef привет():\n    return "🐙"\n```')
    rendered = "\n".join(lines)
    assert "привет" in rendered and "🐙" in rendered
    assert "\x1b[1m" in rendered


@pytest.mark.parametrize("flag", ["bold", "italic", "underline"])
def test_each_modifier_only_enables_its_own_flag(flag):
    style = h.Style()
    assert not any((style.bold, style.italic, style.underline))
    style.add_modifier(**{flag: True})
    assert {name: getattr(style, name) for name in ("bold", "italic", "underline")} == {name: name == flag for name in ("bold", "italic", "underline")}
