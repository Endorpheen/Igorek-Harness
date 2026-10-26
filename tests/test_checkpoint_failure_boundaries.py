"""Snapshot failures stay nonfatal and never exceed preview or scan budgets."""

import subprocess
from pathlib import Path
from unittest.mock import Mock

import pytest

from factory.core import checkpoint_manager as cp


@pytest.fixture
def manager(tmp_path, monkeypatch):
    monkeypatch.setattr(cp, '_git_executable', lambda: 'fixture-git')
    return cp.CheckpointManager(data_root=str(tmp_path / 'private'))


@pytest.mark.parametrize('failure', [subprocess.TimeoutExpired('git', 30), FileNotFoundError('missing'), OSError('fixture failure')])
def test_git_execution_failures_return_a_bounded_failure(manager, tmp_path, monkeypatch, failure):
    monkeypatch.setattr(cp, '_git_command', Mock(side_effect=failure))
    ok, out, error, rc = cp._run_git(['status'], shadow_repo=tmp_path / 'shadow', working_dir=str(tmp_path))
    assert not ok and not out and error and rc == -1


def test_missing_git_is_cached_and_never_attempts_a_snapshot(manager, tmp_path, monkeypatch):
    probe = Mock(return_value=None)
    take = Mock(side_effect=AssertionError('No snapshot without Git'))
    monkeypatch.setattr(cp, '_git_executable', probe)
    monkeypatch.setattr(manager, '_take', take)
    assert not manager.ensure_checkpoint(working_dir=str(tmp_path))
    assert not manager.ensure_checkpoint(working_dir=str(tmp_path))
    assert probe.call_count == 1 and not take.called
    assert cp._run_git([], shadow_repo=tmp_path, working_dir=str(tmp_path)) == (False, '', 'git not found', -1)


@pytest.mark.parametrize('broad', ['/', 'home'])
def test_root_and_home_are_rejected_before_scanning(manager, tmp_path, monkeypatch, broad):
    monkeypatch.setattr(Path, 'home', classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(cp, '_dir_file_count', Mock(side_effect=AssertionError('Unexpected broad scan')))
    assert not manager.ensure_checkpoint(working_dir='/' if broad == '/' else str(tmp_path))


def test_scan_budget_and_transient_snapshot_failure_leave_other_roots_usable(manager, tmp_path, monkeypatch):
    monkeypatch.setattr(cp, '_dir_file_count', lambda path: cp.MAX_FILES + 1)
    take = Mock(side_effect=OSError('fixture snapshot failure'))
    monkeypatch.setattr(manager, '_take', take)
    assert not manager.ensure_checkpoint(working_dir=str(tmp_path))
    assert not take.called
    monkeypatch.setattr(cp, '_dir_file_count', lambda path: 1)
    assert not manager.ensure_checkpoint(working_dir=str(tmp_path))
    assert not manager.ensure_checkpoint(working_dir=str(tmp_path))
    assert take.call_count == 1
    manager.new_turn()
    assert not manager.ensure_checkpoint(working_dir=str(tmp_path))
    assert take.call_count == 2


def test_file_scan_stops_at_budget_and_returns_partial_count_on_os_error(tmp_path, monkeypatch):
    monkeypatch.setattr(cp, 'MAX_FILES', 2)
    monkeypatch.setattr(Path, 'rglob', lambda *args: iter([1, 2, 3, 4]))
    assert cp._dir_file_count(str(tmp_path)) == 3

    def partial(*args):
        yield 1
        raise PermissionError('fixture unreadable subtree')

    monkeypatch.setattr(Path, 'rglob', partial)
    assert cp._dir_file_count(str(tmp_path)) == 1


@pytest.mark.parametrize('case', ['no-git', 'bad-oid', 'size-error', 'shown-error', 'shown-too-big', 'invalid-size', 'os-error'])
def test_blob_preview_refuses_unverified_or_unbounded_content(case, tmp_path, monkeypatch):
    monkeypatch.setattr(cp, 'MAX_CHECKPOINT_PREVIEW_BYTES', 3)
    monkeypatch.setattr(cp, '_git_executable', lambda: None if case == 'no-git' else 'fixture-git')
    calls = []

    def git(argv, **kwargs):
        calls.append(argv)
        if case == 'os-error':
            raise OSError('fixture missing object')
        if argv[1] == 'rev-parse':
            return subprocess.CompletedProcess(argv, 0, 'not-an-object' if case == 'bad-oid' else 'a' * 40, '')
        if argv[2] == '-s':
            return subprocess.CompletedProcess(argv, int(case == 'size-error'), 'invalid' if case == 'invalid-size' else '3', '')
        return subprocess.CompletedProcess(argv, int(case == 'shown-error'), 'four' if case == 'shown-too-big' else 'abc', '')

    monkeypatch.setattr(cp, '_git_command', git)
    assert cp._checkpoint_text('HEAD:file', root=tmp_path, env={}) == (False, None)
    if case == 'no-git':
        assert calls == []
    if case == 'bad-oid':
        assert len(calls) == 1


def test_git_file_output_budget_uses_actual_bytes_and_replaces_invalid_utf8(tmp_path, monkeypatch):
    monkeypatch.setattr(cp, 'MAX_GIT_OUTPUT_BYTES', 3)

    def run(argv, **kwargs):
        kwargs['stdout'].write(b'four')
        return subprocess.CompletedProcess(argv, 0)

    monkeypatch.setattr(cp.subprocess, 'run', run)
    with pytest.raises(ValueError, match='read budget'):
        cp._git_to_files(['fixture-git'], cwd=str(tmp_path), env={}, timeout=1)

    def invalid(argv, **kwargs):
        kwargs['stdout'].write(b'a\xff')
        kwargs['stderr'].write(b'x')
        return subprocess.CompletedProcess(argv, 7)

    monkeypatch.setattr(cp.subprocess, 'run', invalid)
    result = cp._git_to_files(['fixture-git'], cwd=str(tmp_path), env={}, timeout=1)
    assert (result.returncode, result.stdout, result.stderr) == (7, 'a\ufffd', 'x')


def test_external_target_chooses_nearest_repository_or_existing_parent(manager, tmp_path, monkeypatch):
    work = tmp_path / 'work'
    work.mkdir()
    repo = tmp_path / 'other'
    (repo / '.git').mkdir(parents=True)
    (repo / 'nested').mkdir()
    assert manager._resolve_checkpoint_root(target_path=str(repo / 'nested/file.txt'), workdir=str(work)) == str(repo)
    monkeypatch.setattr(cp, '_nearest_git_root', lambda path: None)
    plain = tmp_path / 'plain'
    plain.mkdir()
    assert manager._resolve_checkpoint_root(target_path=str(plain / 'file.txt'), workdir=str(work)) == str(plain)
    assert manager._resolve_checkpoint_root(target_path=str(tmp_path / 'missing/file.txt'), workdir=str(work)) is None
    assert not manager.ensure_checkpoint_for_path(target_path=str(tmp_path / 'missing/file.txt'), workdir=str(work), reason='fixture')
    assert cp._format_reason('fixture') == 'fixture'


@pytest.mark.parametrize('case', ['init', 'diff', 'commit', 'empty-commit', 'log'])
def test_partial_shadow_failure_is_not_reported_as_saved(manager, tmp_path, monkeypatch, case):
    monkeypatch.setattr(cp, '_init_shadow_repo', lambda *a, **k: 'fixture init failure' if case == 'init' else None)
    calls = []

    def git(args, **kwargs):
        calls.append(args)
        if args[0] == 'add':
            return True, '', '', 0
        if args[0] == 'diff':
            return (True, '', '', 0) if case in {'empty-commit', 'log'} else (False, '', 'fixture diff', 2 if case == 'diff' else 1)
        if args[0] == 'log':
            return (False, '', 'fixture log', 1) if case == 'log' else (True, cp.BASENAME_COMMIT_MESSAGE, '', 0)
        if args[0] == 'commit':
            return False, '', 'fixture commit', 1
        raise AssertionError(args)

    monkeypatch.setattr(cp, '_run_git', git)
    assert not manager._take(str(tmp_path), 'fixture')
    assert not any(args[0] == 'reset' for args in calls)
    if case == 'init':
        assert not calls
