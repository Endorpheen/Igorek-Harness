"""Repository configuration changes cannot turn read-only Git into host code."""
import os
from pathlib import Path
import subprocess

import pytest

from factory.core import review_changes, status_line


def git(root, *args):
    env = {'PATH': os.defpath, 'GIT_CONFIG_NOSYSTEM': '1', 'GIT_CONFIG_GLOBAL': os.devnull}
    return subprocess.check_output(['git', '-C', str(root), *args], env=env).decode().strip()


@pytest.fixture
def repository(tmp_path):
    root = tmp_path / 'repository'
    root.mkdir()
    git(root, 'init', '-b', 'main')
    git(root, 'config', 'user.name', 'Fixture')
    git(root, 'config', 'user.email', 'fixture@example.invalid')
    (root / 'a.py').write_text('old fixture\n')
    git(root, 'add', 'a.py')
    git(root, 'commit', '-m', 'Fixture baseline')
    return root


def test_filter_added_after_configuration_read_never_executes_on_host(repository, monkeypatch):
    root = repository
    (root / 'a.py').write_text('changed fixture\n')
    marker = root.parent / 'outside-scope-marker'
    original = subprocess.run
    changed = False

    def race(command, *args, **kwargs):
        nonlocal changed
        result = original(command, *args, **kwargs)
        if not changed and 'config' in command and '--get-regexp' in command:
            changed = True
            (root / '.gitattributes').write_text('*.py filter=late\n')
            git(root, 'config', 'filter.late.clean', f'touch "{marker}"; cat')
        return result

    monkeypatch.setattr(subprocess, 'run', race)
    reader = review_changes.ChangesReview(root, session_files=[], repositories=[], include_untracked=True)
    reader.prepare('working', '')
    result = reader.result()
    assert changed
    assert not marker.exists()
    assert any(file['path'] == 'a.py' for file in result['files'])


def test_status_indicator_ignores_repository_clean_programs(repository):
    marker = repository.parent / 'outside-status-marker'
    (repository / '.gitattributes').write_text('*.py filter=late\n')
    git(repository, 'config', 'filter.late.clean', f'touch "{marker}"; cat')
    (repository / 'a.py').write_text('changed fixture\n')
    result = status_line.git_status(str(repository))
    assert result['is_clean'] is False
    assert result['branch'] == 'main'
    assert not marker.exists()


def test_status_indicator_preserves_nested_folder_context(repository):
    nested = repository / 'nested'
    nested.mkdir()
    result = status_line.git_status(str(nested))
    assert result['branch'] == 'main'
    assert result['root'] == str(repository)
    assert result['is_clean'] is True


def test_registered_linked_worktree_keeps_correct_working_comparison(repository):
    linked = repository.parent / 'linked-fixture'
    git(repository, 'worktree', 'add', '--detach', str(linked))
    (linked / 'a.py').write_text('changed linked fixture\n')
    reader = review_changes.ChangesReview(linked, session_files=[], repositories=[])
    reader.prepare('working', '')
    assert any(file['path'] == 'a.py' for file in reader.result()['files'])


def test_external_git_pointer_cannot_read_another_repository(repository, tmp_path):
    workdir = tmp_path / 'unregistered'
    workdir.mkdir()
    (workdir / '.git').write_text('gitdir: ' + str(repository / '.git'))
    with pytest.raises(review_changes.ReviewError):
        review_changes.git_read(workdir, ['log', '-1'])


def test_configuration_roundtrip_keeps_upstream_but_drops_executables(repository):
    from factory.core import readonly_git
    git(repository, 'config', 'branch.main.remote', 'origin')
    git(repository, 'config', 'branch.main.merge', 'refs/heads/feature"one')
    git(repository, 'config', 'filter.fixture.process', 'never-run-fixture')
    rows = readonly_git._configuration('/usr/bin/git', repository / '.git/config')
    frozen = repository.parent / 'frozen-config'
    frozen.write_text(readonly_git._frozen_config(rows))
    assert subprocess.check_output(['git', 'config', '--file', str(frozen),
                                    '--get', 'branch.main.merge']).decode().strip() == 'refs/heads/feature"one'
    assert 'never-run-fixture' not in frozen.read_text()


def test_process_output_budget_is_enforced_before_capture(repository, monkeypatch):
    from factory.core import readonly_git
    (repository / 'large.txt').write_text('x' * 8192)
    git(repository, 'add', 'large.txt')
    git(repository, 'commit', '-m', 'Bounded fixture')
    monkeypatch.setattr(readonly_git, 'OUTPUT_LIMIT', 1024)
    monkeypatch.setattr(readonly_git, '_LIMIT_EXEC', readonly_git._LIMIT_EXEC.replace('20000000', '1024'))
    with pytest.raises(ValueError, match='output budget'):
        readonly_git.read_git(repository, ['cat-file', 'blob', 'HEAD:large.txt'])
    assert len(readonly_git.read_git(repository, ['rev-parse', 'HEAD']).strip()) == 40
