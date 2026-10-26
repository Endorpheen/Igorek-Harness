"""Editor argv parsing and owned subprocesses preserve text and remove temp files."""

import sys
from unittest.mock import Mock

import pytest

from factory.core.tui import external_editor as editor


@pytest.mark.parametrize('env,expected', [({'VISUAL': 'code --wait', 'EDITOR': 'vi'}, ['code', '--wait']), ({'EDITOR': '"my editor" --flag'}, ['my editor', '--flag']), ({'EDITOR': 'editor "literal ; $(not-a-command)"'}, ['editor', 'literal ; $(not-a-command)'])])
def test_editor_precedence_and_quoting_are_argv_only(env, expected):
    assert editor.resolve_editor_command(env) == expected


@pytest.mark.parametrize('env,kind', [({}, editor.EditorError.MISSING_EDITOR), ({'VISUAL': '   ', 'EDITOR': 'vi'}, editor.EditorError.EMPTY_COMMAND), ({'EDITOR': '"unterminated'}, editor.EditorError.PARSE_FAILED)])
def test_invalid_editor_setting_has_a_typed_reason(env, kind):
    with pytest.raises(editor.EditorLaunchError) as caught:
        editor.resolve_editor_command(env)
    assert caught.value.kind is kind


def test_default_environment_is_used_only_when_no_explicit_mapping(monkeypatch):
    monkeypatch.delenv('VISUAL', raising=False)
    monkeypatch.setenv('EDITOR', 'fixture --wait')
    assert editor.resolve_editor_command() == ['fixture', '--wait']
    with pytest.raises(editor.EditorLaunchError):
        editor.resolve_editor_command({})


@pytest.fixture
def owned_tempfiles(tmp_path, monkeypatch):
    original = editor.tempfile.mkstemp
    monkeypatch.setattr(editor.tempfile, 'mkstemp', lambda *, suffix: original(suffix=suffix, dir=tmp_path))
    return tmp_path


def test_real_owned_editor_preserves_unicode_and_literal_argv(owned_tempfiles):
    root = owned_tempfiles
    script = root / 'fixture editor.py'
    script.write_text("from pathlib import Path\nimport sys\nassert sys.argv[1] == 'literal ; $(never-run)'\np=Path(sys.argv[2])\nassert p.read_text() == 'Привет 🐙\\n'\np.write_text('Новая строка 🐙\\n', encoding='utf-8')\n")
    assert editor.run_editor('Привет 🐙\n', [sys.executable, str(script), 'literal ; $(never-run)']) == 'Новая строка 🐙\n'
    assert list(root.glob('*.md')) == []


def test_nonzero_editor_exit_is_typed_and_tempfile_is_removed(owned_tempfiles):
    with pytest.raises(editor.EditorLaunchError):
        editor.run_editor('owned seed', [sys.executable, '-c', 'raise SystemExit(7)'])
    assert list(owned_tempfiles.glob('*.md')) == []


def test_missing_program_still_removes_tempfile(owned_tempfiles):
    with pytest.raises(FileNotFoundError):
        editor.run_editor('owned seed', [str(owned_tempfiles / 'missing-editor')])
    assert list(owned_tempfiles.glob('*.md')) == []


def test_empty_editor_argv_never_creates_a_file_or_process(monkeypatch):
    create = Mock(side_effect=AssertionError('No temporary file expected'))
    monkeypatch.setattr(editor, '_create_tempfile', create)
    with pytest.raises(editor.EditorLaunchError) as caught:
        editor.run_editor('seed', [])
    assert caught.value.kind is editor.EditorError.EMPTY_COMMAND and not create.called
