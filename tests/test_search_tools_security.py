import ast
import glob
import os
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest


def search_tools(root):
    source = Path(__file__).parents[1] / 'factory/core/cc_backend_tools.py'
    names = {'_is_within_root', '_is_in_any_root', '_resolve_mode', '_validate_system_block',
             '_validate_read_path', '_tool_grep', '_tool_glob', '_allowed_glob_matches'}
    tree = ast.parse(source.read_text())
    tree.body = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in names]
    scope = dict(__name__='factory.core.cc_backend_tools', __package__='factory.core',
                 os=os, _glob=glob, subprocess=subprocess, Path=Path, Any=object,
                 SYSTEM_BLOCKED_ROOTS=('/etc',), USER_READ_ROOTS=(str(root / 'allowed'),))
    exec(compile(tree, str(source), 'exec'), scope)
    return SimpleNamespace(**scope)


def test_grep_pattern_cannot_launch_preprocessor(tmp_path, monkeypatch):
    work = tmp_path / 'work'
    work.mkdir()
    monkeypatch.chdir(work)
    marker = tmp_path / 'executed'
    program = work / 'preprocessor'
    program.write_text('#!/bin/sh\nprintf triggered > ' + str(marker) + '\n')
    program.chmod(0o700)
    (work / 'input.txt').write_text('--pre=' + str(program) + '\nordinary\n')
    result = search_tools(tmp_path)._tool_grep({'pattern': '--pre=' + str(program)}, str(work), 'workspace')
    assert not marker.exists()
    assert '--pre=' in result


@pytest.mark.parametrize('target', ['../private.txt', '../work-other/private.txt', 'external.txt'])
def test_grep_rejects_relative_and_symlink_escape(tmp_path, target):
    work = tmp_path / 'work'
    work.mkdir()
    (tmp_path / 'private.txt').write_text('outside-canary')
    sibling = tmp_path / 'work-other'
    sibling.mkdir()
    (sibling / 'private.txt').write_text('outside-canary')
    (work / 'external.txt').symlink_to(tmp_path / 'private.txt')
    with pytest.raises(ValueError):
        search_tools(tmp_path)._tool_grep({'pattern': '.', 'path': target}, str(work), 'workspace')


def test_glob_never_descends_into_external_symlink(tmp_path, monkeypatch):
    work = tmp_path / 'work'
    work.mkdir()
    external = tmp_path / 'external'
    external.mkdir()
    (external / 'private.txt').write_text('outside-canary')
    (work / 'external').symlink_to(external, target_is_directory=True)
    (work / 'local.txt').write_text('ordinary')
    original = glob.iglob

    def checked(pattern, **kwargs):
        assert Path(pattern).parent.resolve().is_relative_to(work)
        assert not kwargs.get('recursive')
        return original(pattern, **kwargs)

    monkeypatch.setattr(glob, 'iglob', checked)
    result = search_tools(tmp_path)._tool_glob({'pattern': '**/*.txt'}, str(work), 'workspace')
    assert result == str(work / 'local.txt')


@pytest.mark.parametrize('mode', ['workspace', 'user-read', 'full-dangerous'])
def test_search_preserves_local_patterns_and_internal_links(tmp_path, mode):
    work = tmp_path / 'work'
    (work / 'sub').mkdir(parents=True)
    target = work / 'sub' / 'note.txt'
    target.write_text('ordinary regex 123\n')
    (work / 'alias').symlink_to(work / 'sub', target_is_directory=True)
    tools = search_tools(tmp_path)
    assert 'ordinary regex' in tools._tool_grep({'pattern': r'regex \d+', 'path': 'alias/note.txt'}, str(work), mode)
    matches = tools._tool_glob({'pattern': '**/*.txt'}, str(work), mode).splitlines()
    assert str(target) in matches
    assert str(work / 'alias/note.txt') in matches


@pytest.mark.parametrize('mode', ['user-read', 'full-dangerous'])
def test_search_preserves_explicit_allowed_roots(tmp_path, mode):
    work = tmp_path / 'work'
    work.mkdir()
    allowed = tmp_path / 'allowed'
    allowed.mkdir()
    target = allowed / 'note.txt'
    target.write_text('allowed-canary')
    tools = search_tools(tmp_path)
    assert 'allowed-canary' in tools._tool_grep({'pattern': '.', 'path': str(target)}, str(work), mode)
    assert tools._tool_glob({'pattern': str(allowed / '*.txt')}, str(work), mode) == str(target)


@pytest.mark.parametrize('pattern', ['../*', '../work-other/*.txt'])
def test_glob_rejects_parent_and_prefix_sibling(tmp_path, pattern):
    work = tmp_path / 'work'
    work.mkdir()
    with pytest.raises(ValueError):
        search_tools(tmp_path)._tool_glob({'pattern': pattern}, str(work), 'workspace')


def test_glob_filters_external_file_links_and_preserves_hidden_patterns(tmp_path):
    work = tmp_path / 'work'
    work.mkdir()
    private = tmp_path / 'private.txt'
    private.write_text('outside-canary')
    (work / 'escape.txt').symlink_to(private)
    (work / '.hidden.txt').write_text('ordinary')
    tools = search_tools(tmp_path)
    assert tools._tool_glob({'pattern': '*.txt'}, str(work), 'workspace') == 'No files matched.'
    assert tools._tool_glob({'pattern': '.*.txt'}, str(work), 'workspace') == str(work / '.hidden.txt')


def test_grep_accepts_dash_named_path_without_interpreting_options(tmp_path):
    target = tmp_path / '--pre=fixture.txt'
    target.write_text('-literal-pattern\n')
    result = search_tools(tmp_path)._tool_grep({'pattern': '-literal-pattern', 'path': target.name}, str(tmp_path), 'workspace')
    assert '-literal-pattern' in result


def test_glob_preserves_directory_only_trailing_slash(tmp_path):
    (tmp_path / 'sub').mkdir()
    (tmp_path / 'plain.txt').write_text('ordinary')
    result = search_tools(tmp_path)._tool_glob({'pattern': '*/'}, str(tmp_path), 'workspace')
    assert result == str(tmp_path / 'sub') + os.sep
