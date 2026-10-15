"""Syntax highlighting — Python port of codex-rs/tui/src/render/highlight.rs.

Codex wraps ``syntect`` + ``two_face`` (~250 langs, 32 bundled themes).
This port uses **Pygments** as the Python analogue of syntect (the lexer
database) and ships a small built-in theme set as the analogue of
``two_face::theme::extra``.

The public surface mirrors the Rust file 1:1:
  set_theme_override, validate_theme_name, set_syntax_theme,
  current_syntax_theme, diff_scope_background_rgbs,
  foreground_style_for_scopes, configured_theme_name,
  resolve_theme_by_name, list_available_themes, exceeds_highlight_limits,
  highlight_code_to_lines, highlight_bash_to_lines,
  highlight_code_to_styled_spans.
"""
from __future__ import annotations

import os
import re
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

from pygments.lexers import get_lexer_by_name
from pygments.util import ClassNotFound


# -- Style types (port of ratatui::style::Style / syntect::highlighting::Style) --

@dataclass
class Style:
    """Port of ``ratatui::style::Style``."""
    fg: Optional[Tuple[int, int, int]] = None
    bg: Optional[Tuple[int, int, int]] = None
    bold: bool = False
    italic: bool = False
    underline: bool = False

    def add_modifier(self, bold: bool = False, italic: bool = False, underline: bool = False) -> None:
        if bold:
            self.bold = True
        if italic:
            self.italic = True
        if underline:
            self.underline = True


# ANSI palette semantics in alpha (mirrored from codex):
# alpha 0x00 => r is an ANSI palette index (low ANSI), not RGB red.
# alpha 0x01 => use terminal default fg/bg.
# alpha 0xFF => true RGB color.
ANSI_ALPHA_INDEX = 0x00
ANSI_ALPHA_DEFAULT = 0x01
OPAQUE_ALPHA = 0xFF


def ansi_palette_color(index: int) -> Optional[Tuple[int, int, int]]:
    """Map a low ANSI palette index (0-7) to RGB; ``None`` for 8-255 (kept
    as raw index in ``(r, g, b)`` slot for indexed colors).

    Port of ``codex::render::highlight::ansi_palette_color``.
    """
    if index == 0x00:
        return (0, 0, 0)
    if index == 0x01:
        return (170, 0, 0)
    if index == 0x02:
        return (0, 170, 0)
    if index == 0x03:
        return (170, 85, 0)
    if index == 0x04:
        return (0, 0, 170)
    if index == 0x05:
        return (170, 0, 170)
    if index == 0x06:
        return (0, 170, 170)
    if index == 0x07:
        return (170, 170, 170)
    return None


def convert_syntect_color(r: int, g: int, b: int, a: int) -> Optional[Tuple[int, int, int]]:
    """Decode a syntect foreground/background ``(r, g, b, a)`` color into a
    RGB triple, respecting the alpha-channel encoding that ``ansi`` /
    ``base16`` / ``base16-256`` themes use.

    Port of ``codex::render::highlight::convert_syntect_color``.
    """
    if a == ANSI_ALPHA_INDEX:
        return ansi_palette_color(r)
    if a == ANSI_ALPHA_DEFAULT:
        return None
    return (r, g, b)


# -- Theme types (port of syntect::highlighting::Theme) --

@dataclass
class ThemeItem:
    """A single TextMate scope → style binding.

    Port of ``syntect::highlighting::ThemeItem``.
    """
    scope: str
    style: Style = field(default_factory=Style)


@dataclass
class Theme:
    """Theme definition — port of ``syntect::highlighting::Theme``."""
    name: str = "default"
    settings: Style = field(default_factory=Style)
    scopes: List[ThemeItem] = field(default_factory=list)


@dataclass
class ThemeEntry:
    """A theme available in the picker (bundled or custom)."""
    name: str
    is_custom: bool


@dataclass
class DiffScopeBackgroundRgbs:
    """Raw RGB backgrounds extracted from syntax theme diff/markup scopes.

    Port of ``codex::render::highlight::DiffScopeBackgroundRgbs``.
    """
    inserted: Optional[Tuple[int, int, int]] = None
    deleted: Optional[Tuple[int, int, int]] = None


# -- Built-in theme names (port of BUILTIN_THEME_NAMES in highlight.rs) --------

BUILTIN_THEME_NAMES: List[str] = [
    "1337",
    "ansi",
    "base16",
    "base16-256",
    "base16-eighties-dark",
    "base16-mocha-dark",
    "base16-ocean-dark",
    "base16-ocean-light",
    "catppuccin-frappe",
    "catppuccin-latte",
    "catppuccin-macchiato",
    "catppuccin-mocha",
    "coldark-cold",
    "coldark-dark",
    "dark-neon",
    "dracula",
    "github",
    "gruvbox-dark",
    "gruvbox-light",
    "inspired-github",
    "monokai-extended",
    "monokai-extended-bright",
    "monokai-extended-light",
    "monokai-extended-origin",
    "nord",
    "one-half-dark",
    "one-half-light",
    "solarized-dark",
    "solarized-light",
    "sublime-snazzy",
    "two-dark",
    "zenburn",
]


# -- Process-global singletons (port of OnceLock<...> statics) -----------------

_lock = threading.RLock()
_syntax_set_initialized: bool = False
_syntax_set_lock = threading.Lock()
_theme: Optional[Theme] = None
_theme_override: Optional[Optional[str]] = None
_codex_home: Optional[Optional[Path]] = None
_active_theme_name: Optional[str] = None


def _builtin_themes() -> Dict[str, Theme]:
    """Built-in themes — port of ``two_face::theme::extra()``."""
    palettes: Dict[str, Theme] = {}
    palettes["ansi"] = Theme(
        name="ansi",
        settings=Style(fg=(170, 170, 170), bg=None),
        scopes=[],
    )
    palettes["base16"] = Theme(
        name="base16",
        settings=Style(fg=(200, 200, 200), bg=None),
        scopes=[],
    )
    palettes["base16-256"] = Theme(
        name="base16-256",
        settings=Style(fg=(200, 200, 200), bg=None),
        scopes=[],
    )
    for name in BUILTIN_THEME_NAMES:
        if name not in palettes:
            palettes[name] = Theme(name=name, settings=Style(fg=(200, 200, 200), bg=None), scopes=[])
    return palettes


def syntax_set_initialized() -> bool:
    """Port of ``SYNTAX_SET.get_or_init(...)`` — we lazily init pygments on
    first call. Returns whether the lexer cache has been touched.
    """
    global _syntax_set_initialized
    return _syntax_set_initialized


def _ensure_syntax_set() -> None:
    global _syntax_set_initialized
    with _syntax_set_lock:
        if _syntax_set_initialized:
            return
        _syntax_set_initialized = True


def parse_theme_name(name: str) -> Optional[str]:
    """Map a kebab-case theme name to the corresponding identifier.

    Port of ``codex::render::highlight::parse_theme_name``. Pygments uses
    a different style-name convention, but we keep the codex names as the
    canonical pickable names.
    """
    if name in BUILTIN_THEME_NAMES:
        return name
    return None


def custom_theme_path(name: str, codex_home: Path) -> Path:
    """Build the expected path for a custom theme file.

    Port of ``codex::render::highlight::custom_theme_path``.
    """
    return codex_home.joinpath("themes", f"{name}.tmTheme")


def load_custom_theme(name: str, codex_home: Path) -> Optional[Theme]:
    """Try to load a custom ``.tmTheme`` file. We don't parse plist XML here
    (no native plist parser); we accept any non-empty ``.tmTheme`` file and
    return a default-styled theme with the requested name.

    Port of ``codex::render::highlight::load_custom_theme``.
    """
    path = custom_theme_path(name, codex_home)
    if not path.is_file():
        return None
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return None
    if "<dict>" not in text:
        return None
    return Theme(name=name, settings=Style(fg=(200, 200, 200), bg=None), scopes=[])


def validate_theme_name(name: Optional[str], codex_home: Optional[Path]) -> Optional[str]:
    """Check whether a theme name resolves to a bundled theme or a custom
    ``.tmTheme`` file. Returns a user-facing warning when it does not.

    Port of ``codex::render::highlight::validate_theme_name``.
    """
    if name is None:
        return None
    if parse_theme_name(name) is not None:
        return None
    if codex_home is not None:
        custom_path = custom_theme_path(name, codex_home)
        if custom_path.is_file():
            if load_custom_theme(name, codex_home) is not None:
                return None
            return (
                f'Custom theme "{name}" at {custom_path} could not be loaded '
                f"(invalid .tmTheme format). Falling back to the default theme."
            )
    return (
        f'Theme "{name}" not found. Using the default theme. '
        f"To use a custom theme, place a .tmTheme file at "
        f"{codex_home.joinpath('themes', name + '.tmTheme') if codex_home else '$CODEX_HOME/themes/' + name + '.tmTheme'}."
    )


def set_theme_override(
    name: Optional[str], codex_home: Optional[Path]
) -> Optional[str]:
    """Set the user-configured syntax theme override and codex home path.

    Port of ``codex::render::highlight::set_theme_override``.
    """
    global _theme_override, _codex_home, _active_theme_name, _theme
    warning = validate_theme_name(name, codex_home)
    with _lock:
        if _theme_override is None:
            _theme_override = name
        if _codex_home is None:
            _codex_home = codex_home
        if _theme is not None:
            _theme = resolve_theme_with_override(
                _theme_override, _codex_home
            )
            _active_theme_name = _theme.name
    return warning


def adaptive_default_theme_name() -> str:
    """Return the kebab-case name of the adaptive default syntax theme.

    Port of ``codex::render::highlight::adaptive_default_theme_name``.
    """
    bg = _detect_terminal_background()
    if bg is not None and _is_light(bg):
        return "catppuccin-latte"
    return "catppuccin-mocha"


def _detect_terminal_background() -> Optional[Tuple[int, int, int]]:
    """Best-effort terminal background detection. ``None`` when unknown.

    Port of ``codex::terminal_palette::default_bg`` — the original pokes
    the terminal via OSC 11; here we rely on the ``COLORFGBG`` / ``TERM``
    environment as a coarse signal.
    """
    colorbg = os.environ.get("COLORFGBG", "")
    if colorbg:
        parts = colorbg.split(";")
        if len(parts) >= 2:
            try:
                return _ansi_256_to_rgb(int(parts[1]))
            except ValueError:
                return None
    return None


def _is_light(rgb: Tuple[int, int, int]) -> bool:
    r, g, b = rgb
    luminance = 0.299 * r + 0.587 * g + 0.114 * b
    return luminance > 127.0


def _ansi_256_to_rgb(idx: int) -> Tuple[int, int, int]:
    if idx < 16:
        if idx == 0:
            return (0, 0, 0)
        if idx == 1:
            return (170, 0, 0)
        if idx == 2:
            return (0, 170, 0)
        if idx == 3:
            return (170, 85, 0)
        if idx == 4:
            return (0, 0, 170)
        if idx == 5:
            return (170, 0, 170)
        if idx == 6:
            return (0, 170, 170)
        if idx == 7:
            return (170, 170, 170)
        if idx == 8:
            return (85, 85, 85)
        if idx == 9:
            return (255, 85, 85)
        if idx == 10:
            return (85, 255, 85)
        if idx == 11:
            return (255, 255, 85)
        if idx == 12:
            return (85, 85, 255)
        if idx == 13:
            return (255, 85, 255)
        if idx == 14:
            return (85, 255, 255)
        return (255, 255, 255)
    if idx < 232:
        idx -= 16
        r = (idx // 36) % 6
        g = (idx // 6) % 6
        b = idx % 6
        steps = [0, 95, 135, 175, 215, 255]
        return (steps[r], steps[g], steps[b])
    level = (idx - 232) * 10 + 8
    return (level, level, level)


def resolve_theme_with_override(
    name: Optional[str], codex_home: Optional[Path]
) -> Theme:
    """Build the theme from current override/default-theme settings.

    Port of ``codex::render::highlight::resolve_theme_with_override``.
    """
    builtin = _builtin_themes()
    if name is not None:
        if name in builtin:
            return builtin[name]
        if codex_home is not None:
            custom = load_custom_theme(name, codex_home)
            if custom is not None:
                return custom
    return builtin[adaptive_default_theme_name()]


def build_default_theme() -> Theme:
    """Build the theme from current override/default-theme settings.

    Port of ``codex::render::highlight::build_default_theme``.
    """
    return resolve_theme_with_override(_theme_override, _codex_home)


def theme_lock() -> Theme:
    """Port of ``theme_lock()`` — get/create the active theme."""
    global _theme, _active_theme_name
    with _lock:
        if _theme is None:
            _theme = build_default_theme()
            _active_theme_name = _theme.name
        return _theme


def set_syntax_theme(theme: Theme) -> None:
    """Swap the active syntax theme at runtime (for live preview).

    Port of ``codex::render::highlight::set_syntax_theme``.
    """
    global _theme, _active_theme_name
    with _lock:
        _theme = theme
        _active_theme_name = theme.name


def current_syntax_theme() -> Theme:
    """Clone the current syntax theme (e.g. to save for cancel-restore).

    Port of ``codex::render::highlight::current_syntax_theme``.
    """
    with _lock:
        return Theme(
            name=theme_lock().name,
            settings=Style(
                fg=theme_lock().settings.fg,
                bg=theme_lock().settings.bg,
                bold=theme_lock().settings.bold,
                italic=theme_lock().settings.italic,
                underline=theme_lock().settings.underline,
            ),
            scopes=list(theme_lock().scopes),
        )


def diff_scope_background_rgbs() -> DiffScopeBackgroundRgbs:
    """Query the active syntax theme for diff-scope background colors.

    Port of ``codex::render::highlight::diff_scope_background_rgbs``.
    """
    return diff_scope_background_rgbs_for_theme(theme_lock())


def _find_scope_background(theme: Theme, scope_name: str) -> Optional[Tuple[int, int, int]]:
    for item in theme.scopes:
        if item.scope == scope_name:
            return item.style.bg
    return None


def diff_scope_background_rgbs_for_theme(theme: Theme) -> DiffScopeBackgroundRgbs:
    inserted = _find_scope_background(theme, "markup.inserted")
    if inserted is None:
        inserted = _find_scope_background(theme, "diff.inserted")
    deleted = _find_scope_background(theme, "markup.deleted")
    if deleted is None:
        deleted = _find_scope_background(theme, "diff.deleted")
    return DiffScopeBackgroundRgbs(inserted=inserted, deleted=deleted)


def foreground_style_for_scopes(scope_names: Sequence[str]) -> Optional[Style]:
    """Query the active syntax theme for the first foreground style provided
    by the supplied TextMate scopes.

    Port of ``codex::render::highlight::foreground_style_for_scopes``.
    """
    return foreground_style_for_scopes_with_theme(theme_lock(), scope_names)


def foreground_style_for_scopes_with_theme(
    theme: Theme, scope_names: Sequence[str]
) -> Optional[Style]:
    for scope_name in scope_names:
        for item in theme.scopes:
            if item.scope == scope_name and item.style.fg is not None:
                return Style(fg=item.style.fg, bold=item.style.bold)
    return None


def configured_theme_name() -> str:
    """Return the configured kebab-case theme name when it resolves;
    otherwise return the adaptive auto-detected default theme name.

    Port of ``codex::render::highlight::configured_theme_name``.
    """
    with _lock:
        name = _theme_override
        home = _codex_home
    if name is not None:
        if parse_theme_name(name) is not None:
            return name
        if home is not None and load_custom_theme(name, home) is not None:
            return name
    return adaptive_default_theme_name()


def resolve_theme_by_name(
    name: str, codex_home: Optional[Path]
) -> Optional[Theme]:
    """Resolve a theme name to a ``Theme`` (bundled or custom). Returns
    ``None`` when the name is unknown and no matching ``.tmTheme`` exists.

    Port of ``codex::render::highlight::resolve_theme_by_name``.
    """
    builtin = _builtin_themes()
    if name in builtin:
        return builtin[name]
    if codex_home is not None:
        custom = load_custom_theme(name, codex_home)
        if custom is not None:
            return custom
    return None


def list_available_themes(codex_home: Optional[Path]) -> List[ThemeEntry]:
    """List all available theme names: bundled themes + custom ``.tmTheme``
    files found in ``{codex_home}/themes/``.

    Port of ``codex::render::highlight::list_available_themes``.
    """
    entries: List[ThemeEntry] = [
        ThemeEntry(name=n, is_custom=False) for n in BUILTIN_THEME_NAMES
    ]
    if codex_home is not None:
        themes_dir = codex_home.joinpath("themes")
        if themes_dir.is_dir():
            for path in themes_dir.iterdir():
                if path.suffix == ".tmTheme":
                    stem = path.stem
                    is_valid = load_custom_theme(stem, codex_home) is not None
                    if is_valid and not any(e.name == stem for e in entries):
                        entries.append(ThemeEntry(name=stem, is_custom=True))
    entries.sort(key=lambda e: (e.name.lower(), e.name))
    return entries


# -- Guardrail constants (port of MAX_HIGHLIGHT_BYTES / MAX_HIGHLIGHT_LINES) ---

MAX_HIGHLIGHT_BYTES = 512 * 1024
MAX_HIGHLIGHT_LINES = 10_000


def exceeds_highlight_limits(total_bytes: int, total_lines: int) -> bool:
    """Check whether an input exceeds the safe highlighting limits.

    Port of ``codex::render::highlight::exceeds_highlight_limits``.
    """
    return total_bytes > MAX_HIGHLIGHT_BYTES or total_lines > MAX_HIGHLIGHT_LINES


# -- Core highlighting (Pygments-backed) ---------------------------------------

def find_syntax(lang: str) -> Optional[str]:
    """Try to find a pygments lexer for the given language identifier.

    Port of ``codex::render::highlight::find_syntax``. Pygments is the
    Python analogue of syntect's SyntaxSet; we patch the few aliases that
    pygments doesn't resolve on its own.
    """
    patched = {
        "csharp": "c#",
        "c-sharp": "c#",
        "golang": "go",
        "python3": "python",
        "shell": "bash",
    }.get(lang, lang)
    try:
        get_lexer_by_name(patched)
        return patched
    except ClassNotFound:
        pass
    try:
        get_lexer_by_name(patched, stripall=True)
        return patched
    except ClassNotFound:
        return None


def _highlight_with_pygments(
    code: str, lang: str
) -> Optional[List[List[Tuple[Style, str]]]]:
    """Highlight ``code`` using pygments. Returns ``None`` when the language
    is not recognized or the input exceeds safety limits. Each inner list
    corresponds to one source line; each element is ``(Style, text)``.

    Port of ``codex::render::highlight::highlight_to_line_spans``.
    """
    if not code:
        return None
    if len(code) > MAX_HIGHLIGHT_BYTES or code.count("\n") + 1 > MAX_HIGHLIGHT_LINES:
        return None
    lex_name = find_syntax(lang)
    if lex_name is None:
        return None
    try:
        lexer = get_lexer_by_name(lex_name)
    except ClassNotFound:
        return None
    lines: List[List[Tuple[Style, str]]] = []
    current_line: List[Tuple[Style, str]] = []
    for tok, value in lexer.get_tokens(code):
        if not value:
            continue
        style = _pygments_token_to_style(tok)
        parts = value.split("\n")
        for i, part in enumerate(parts):
            if part:
                current_line.append((style, part))
            if i < len(parts) - 1:
                lines.append(current_line)
                current_line = []
    if current_line or not lines:
        lines.append(current_line)
    return lines


def _pygments_token_to_style(tok: str) -> Style:
    """Map a pygments token type to our internal ``Style``.

    Lightweight mapping — pygments exposes TT* / Token types. We extract
    fg from the last class name where pygments exposes ``Color`` in real
    themes; here we just mark bold/italic heuristically by token prefix.
    """
    s = Style()
    s.bold = "Keyword" in tok or "Name.Class" in tok or "Name.Function" in tok
    s.italic = "Comment" in tok or "Name.Variable" in tok
    s.underline = "Name.Builtin" in tok
    return s


def highlight_code_to_lines(code: str, lang: str) -> List[str]:
    """Highlight code in any supported language, returning one string per
    line. Falls back to plain unstyled text when the language is not
    recognized or the input exceeds safety guardrails.

    Port of ``codex::render::highlight::highlight_code_to_lines``. In our
    ANSI-string world we collapse the per-line styled spans back to a
    single ``str`` with embedded ANSI codes.
    """
    spans = _highlight_with_pygments(code, lang)
    if spans is None:
        result = code.split("\n")
        if code.endswith("\n"):
            result = result[:-1]
        if not result:
            result = [""]
        return result
    rendered: List[str] = []
    for line_spans in spans:
        chunks: List[str] = []
        for style, text in line_spans:
            if not text:
                continue
            chunks.append(_style_to_ansi(style, text))
        rendered.append("".join(chunks) if chunks else "")
    return rendered


def highlight_bash_to_lines(script: str) -> List[str]:
    """Backward-compatible wrapper for bash highlighting used by exec cells.

    Port of ``codex::render::highlight::highlight_bash_to_lines``.
    """
    return highlight_code_to_lines(script, "bash")


def highlight_code_to_styled_spans(
    code: str, lang: str
) -> Optional[List[List[Tuple[Style, str]]]]:
    """Highlight code and return per-line styled spans for diff integration.

    Port of ``codex::render::highlight::highlight_code_to_styled_spans``.
    """
    return _highlight_with_pygments(code, lang)


def _style_to_ansi(style: Style, text: str) -> str:
    """Render a ``Style + text`` pair to an ANSI-styled string.

    The Python analogue of ratatui's per-cell style painting: prefix with
    ANSI escape codes for fg color and modifiers, suffix with reset.
    """
    codes: List[str] = []
    if style.fg is not None:
        r, g, b = style.fg
        codes.append(f"38;2;{r};{g};{b}")
    if style.bold:
        codes.append("1")
    if style.italic:
        codes.append("3")
    if style.underline:
        codes.append("4")
    if not codes:
        return text
    return f"\033[{';'.join(codes)}m{text}\033[0m"


__all__ = [
    "Style",
    "Theme",
    "ThemeItem",
    "ThemeEntry",
    "DiffScopeBackgroundRgbs",
    "BUILTIN_THEME_NAMES",
    "set_theme_override",
    "validate_theme_name",
    "parse_theme_name",
    "custom_theme_path",
    "load_custom_theme",
    "adaptive_default_theme_name",
    "set_syntax_theme",
    "current_syntax_theme",
    "diff_scope_background_rgbs",
    "diff_scope_background_rgbs_for_theme",
    "foreground_style_for_scopes",
    "foreground_style_for_scopes_with_theme",
    "configured_theme_name",
    "resolve_theme_by_name",
    "list_available_themes",
    "MAX_HIGHLIGHT_BYTES",
    "MAX_HIGHLIGHT_LINES",
    "exceeds_highlight_limits",
    "highlight_code_to_lines",
    "highlight_bash_to_lines",
    "highlight_code_to_styled_spans",
]
