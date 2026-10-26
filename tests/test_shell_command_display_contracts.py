"""Command display tokenization preserves literal arguments and malformed input."""

from pathlib import Path
import shlex

import pytest

from factory.core.tui import exec_command as command


@pytest.mark.parametrize('parts', [[], ['plain'], ['editor', 'a b'], ['echo', 'Привет 🐙', '$(never-run)', ';'], ['/owned path/editor', "author's notes", '']])
def test_escape_round_trips_literal_argument_boundaries(parts):
    assert shlex.split(command.escape_command(parts)) == parts


@pytest.mark.parametrize('parts,expected', [(['bash', '-lc', 'printf fixture'], 'printf fixture'), (['/bin/zsh', '-cl', 'printf fixture'], 'printf fixture'), (['/bin/sh', '-c', 'printf fixture'], "/bin/sh -c 'printf fixture'"), (['tool', 'a b'], "tool 'a b'")])
def test_only_supported_shell_wrappers_are_unwrapped(parts, expected):
    assert command.strip_bash_lc_and_escape(parts) == expected


@pytest.mark.parametrize('text,expected', [('plain', ['plain']), ('tool "a b"', ['tool', 'a b']), ('', ['']), ('tool "unterminated', ['tool "unterminated']), (r'C:\Users\fixture\Editor.exe "a b"', [r'C:\Users\fixture\Editor.exe "a b"'])])
def test_non_roundtrippable_strings_are_preserved_without_guessing(text, expected):
    assert command.split_command_string(text) == expected


def test_relativize_only_absolute_paths_within_owned_home(tmp_path, monkeypatch):
    home = tmp_path / 'owned-home'
    home.mkdir()
    monkeypatch.setenv('HOME', str(home))
    assert command.relativize_to_home(home / 'nested/file.txt') == Path('nested/file.txt')
    assert command.relativize_to_home(home) == Path('.')
    assert command.relativize_to_home(tmp_path / 'sibling/file.txt') is None
    assert command.relativize_to_home(Path('relative.txt')) is None
