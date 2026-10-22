import json
import os
import time
from pathlib import Path

import pytest

from factory.core.schema import initialize_database, open_db
from factory.core.workspace_manager import WorkspaceManager


@pytest.fixture
def workspace(tmp_path):
    database = tmp_path / 'factory.db'
    initialize_database(str(database))
    manager = WorkspaceManager(str(database), str(tmp_path / 'workspaces'))
    return manager


def task(manager, identity, status='processing', parent=None):
    with open_db(manager.db_path) as connection:
        connection.execute('INSERT INTO tasks (task_id, status, profile, input_text, parent_id, created_at) VALUES (?, ?, ?, ?, ?, ?)',
                           (identity, status, 'fixture', 'Synthetic task', parent, int(time.time())))


def aged(path):
    timestamp = time.time() - 30 * 86400
    os.utime(path, (timestamp, timestamp))


def test_workspace_creation_keeps_metadata_and_required_result_folders(workspace):
    path = Path(workspace.create_orchestration_workspace('main-task-1', 'Custom request'))
    assert path.parent == Path(workspace.workspace_root)
    assert all((path / folder).is_dir() for folder in ['subtasks', 'logs', 'assets', 'reports', 'temp'])
    metadata = json.loads((path / 'metadata.json').read_text())
    assert metadata['main_task_id'] == 'main-task-1'
    assert metadata['description'] == 'Custom request'
    assert metadata['subtask_count'] == metadata['completed_subtasks'] == 0
    assert 'Custom request' in (path / 'README.md').read_text()
    assert workspace.get_workspace_path('main-task-1') == str(path)


def test_workspace_lookup_ignores_corrupt_metadata_and_uses_legacy_fallback(workspace):
    root = Path(workspace.workspace_root)
    broken = root / 'broken'; broken.mkdir(); (broken / 'metadata.json').write_text('{broken')
    (root / 'unrelated.txt').write_text('Not a workspace')
    valid = Path(workspace.create_orchestration_workspace('known', 'Custom request'))
    assert workspace.get_workspace_path('known') == str(valid)
    assert workspace.get_workspace_path('missing') == str(root / 'missing')
    assert workspace.get_metadata('missing') == {}


def test_subtask_results_copy_existing_assets_and_count_database_children(workspace, tmp_path):
    main = 'main'; task(workspace, main); task(workspace, 'child-one', parent=main); task(workspace, 'child-two', parent=main)
    path = Path(workspace.create_orchestration_workspace(main, 'Custom request'))
    source = tmp_path / 'artifact.bin'; source.write_bytes(b'Synthetic artifact')
    result = {'output': {'files': [str(source), str(tmp_path / 'missing.bin')]}, 'success': True}
    subtask = Path(workspace.add_subtask_result(main, 'child-one', 'fixture', 'draft', result))
    assert json.loads((subtask / 'result.json').read_text()) == result
    assert (path / 'assets/fixture_draft/artifact.bin').read_bytes() == source.read_bytes()
    assert source.exists()
    metadata = workspace.get_metadata(main)
    assert metadata['subtask_count'] == 2
    assert metadata['completed_subtasks'] == 1


def test_subtask_without_files_still_persists_result_and_updates_count(workspace):
    task(workspace, 'main'); task(workspace, 'child', parent='main')
    path = Path(workspace.create_orchestration_workspace('main', 'Custom request'))
    result = {'output': {'text': 'A useful result'}}
    subtask = Path(workspace.add_subtask_result('main', 'child', 'fixture', 'draft', result))
    assert json.loads((subtask / 'result.json').read_text()) == result
    assert not (path / 'assets/fixture_draft').exists()
    assert workspace.get_metadata('main')['completed_subtasks'] == 1


def test_final_report_preserves_text_and_marks_workspace_complete(workspace):
    workspace.create_orchestration_workspace('main', 'Custom request')
    path = Path(workspace.generate_final_report('main', 'Verified result 🐙'))
    assert path.name == 'final_report.md'
    assert 'Verified result 🐙' in path.read_text()
    metadata = workspace.get_metadata('main')
    assert metadata['status'] == 'completed'
    assert metadata['completed_at']


def test_metadata_update_does_not_replace_corrupt_metadata(workspace):
    root = Path(workspace.workspace_root); path = root / 'main'; path.mkdir()
    metadata = path / 'metadata.json'; metadata.write_text('{broken')
    workspace.update_metadata('main')
    assert metadata.read_text() == '{broken'
    workspace.update_metadata('absent')


@pytest.mark.parametrize('status', ['queued', 'processing', 'waiting_approval', 'waiting_user_input', 'retry', 'cancelling'])
def test_cleanup_preserves_active_workspaces_with_human_readable_names(workspace, status):
    task(workspace, 'active-task', status)
    path = Path(workspace.create_orchestration_workspace('active-task', 'Custom request'))
    (path / 'result.txt').write_text('Unfinished work')
    aged(path); workspace.cleanup_old_workspaces(days_old=7)
    assert (path / 'result.txt').read_text() == 'Unfinished work'


@pytest.mark.parametrize('status', ['ok', 'fail', 'dead_letter'])
def test_cleanup_still_removes_old_completed_workspaces(workspace, status):
    task(workspace, 'finished-task', status)
    path = Path(workspace.create_orchestration_workspace('finished-task', 'Custom request'))
    aged(path); workspace.cleanup_old_workspaces(days_old=7)
    assert not path.exists()


def test_cleanup_preserves_recent_completed_and_legacy_active_workspaces(workspace):
    task(workspace, 'recent', 'ok'); task(workspace, 'legacy', 'processing')
    recent = Path(workspace.create_orchestration_workspace('recent', 'Custom request'))
    legacy = Path(workspace.workspace_root) / 'legacy'; legacy.mkdir(); (legacy / 'data.txt').write_text('Active legacy task')
    aged(legacy); workspace.cleanup_old_workspaces(days_old=7)
    assert recent.exists() and (legacy / 'data.txt').exists()


@pytest.mark.parametrize('content', ['{broken', '[]', '{}', '{"main_task_id": null}'])
def test_cleanup_preserves_unidentifiable_metadata_and_continues_with_completed_tasks(workspace, content):
    unknown = Path(workspace.workspace_root) / 'unknown'; unknown.mkdir(); (unknown / 'metadata.json').write_text(content)
    (unknown / 'data.txt').write_text('Keep unknown work'); aged(unknown)
    task(workspace, 'finished', 'ok')
    finished = Path(workspace.create_orchestration_workspace('finished', 'Custom request')); aged(finished)
    workspace.cleanup_old_workspaces(days_old=7)
    assert (unknown / 'data.txt').read_text() == 'Keep unknown work'
    assert not finished.exists()


def test_cleanup_never_follows_workspace_directory_symlinks(workspace, tmp_path):
    outside = tmp_path / 'outside'; outside.mkdir(); (outside / 'data.txt').write_text('Outside data'); aged(outside)
    link = Path(workspace.workspace_root) / 'external'; link.symlink_to(outside, target_is_directory=True)
    task(workspace, 'finished', 'ok'); finished = Path(workspace.create_orchestration_workspace('finished', 'Custom request')); aged(finished)
    workspace.cleanup_old_workspaces(days_old=7)
    assert link.is_symlink() and (outside / 'data.txt').read_text() == 'Outside data'
    assert not finished.exists()


def test_cleanup_leaves_workspace_untouched_if_task_database_cannot_be_read(workspace, tmp_path):
    path = Path(workspace.create_orchestration_workspace('active', 'Custom request')); aged(path)
    invalid = tmp_path / 'invalid.db'; invalid.write_text('Not a SQLite database'); workspace.db_path = str(invalid)
    workspace.cleanup_old_workspaces(days_old=7)
    assert path.exists()
