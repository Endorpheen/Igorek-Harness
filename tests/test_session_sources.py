import json
from pathlib import Path

import pytest

from factory.core.cli_chat import _session_path, _append_entry
from factory.core.cli_tasks import create_task
from factory.core.mobile_bridge.service import MobileService, MobileNotFound
from factory.core.mobile_bridge.sources import references, safe_url
from factory.core.runtime import get_db_path
from factory.core.schema import initialize_database
from factory.core.session_changes import TaskChangeTracker, load_task_changes


@pytest.fixture
def session(tmp_path, monkeypatch):
    monkeypatch.setenv('IGOREK_FACTORY_HOME', str(tmp_path / 'runtime'))
    monkeypatch.setenv('FACTORY_DATA_DIR', str(tmp_path / 'runtime'))
    initialize_database(get_db_path())
    work = tmp_path / 'workspace'
    work.mkdir()
    service = MobileService(workdir=str(work))
    identity = 'chat-20261017-120000-sources'
    path = _session_path(identity)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({'type': 'meta', 'session_id': identity, 'cwd': str(work)}) + '\n')
    return service, identity, work


@pytest.mark.parametrize('url', ['javascript:alert(1)', 'file:///etc/passwd', 'https://user:password@example.com/a',
    'https://example.com/a?api_key=private', 'https://example.com/a?X-Amz-Signature=private',
    'https://example.com/#access_token=private', 'https://example.com:bad/a', 'https://example.com/\nprivate'])
def test_sources_reject_privileged_or_credential_bearing_urls(url):
    assert safe_url(url) is None


def test_nested_tool_references_preserve_title_and_group_pull_requests():
    payload = {'status': 'done', 'context_output': json.dumps({'content': [{'text': json.dumps({
        'results': [{'title': 'Official reference', 'url': 'https://example.com/reference'},
                    {'html_url': 'https://github.com/example/project/pull/12/files#diff-1'}]})}]})}
    result = references(payload)
    assert [(x['kind'], x['url']) for x in result] == [
        ('web', 'https://example.com/reference'), ('pull_request', 'https://github.com/example/project/pull/12')]
    assert result[0]['name'] == 'Official reference'
    assert references({**payload, 'status': 'error'}) == []
    assert references({'status': 'done', 'context_output': '{"error":"https://example.com/private"}'}) == []


def test_changed_files_are_listed_without_paths_in_final_answer(session):
    service, identity, work = session
    created = create_task(text='Update a journal', profile='fixture', metadata={'session': identity, 'cwd': str(work)})
    target = work / 'journal.md'
    target.write_text('new entry\n')
    tracker = TaskChangeTracker(task_id=created['task_id'], workdir=str(work))
    tracker.record_edit(str(target), 'old entry', 'new entry')
    _append_entry(_session_path(identity), role='assistant', text='The journal was updated.', task_id=created['task_id'])
    files = service.list_session_files(identity)
    assert [(x['name'], x['path']) for x in files] == [('journal.md', 'journal.md')]
    info, handle = service.open_session_file(identity, files[0]['id'])
    with handle:
        assert handle.read() == target.read_bytes()
    assert service.session_sources(identity)['sources'][0]['id'] == info['id']


def test_existing_shell_artifacts_and_references_survive_restart_and_session_isolation(session):
    service, identity, work = session
    target = work / 'table.xlsx'
    target.write_bytes(b'PK\x00synthetic-workbook')
    service.store.append_event(session_id=identity, task_id='fixture', kind='tool.result', payload={
        'status': 'done', 'tool_name': 'Bash', 'context_output': 'output: ' + str(target) + '\nhttps://github.com/example/project/pull/18'})
    service.store.append_event(session_id='other', task_id='other', kind='tool.result', payload={
        'status': 'done', 'tool_name': 'web_search', 'context_output': 'https://example.com/other'})
    first = service.session_sources(identity)
    restarted = MobileService(workdir=str(work), store=service.store).session_sources(identity)
    assert first == restarted
    assert {x['kind'] for x in first['sources']} == {'file', 'pull_request'}
    assert service.list_session_files(identity)[0]['name'] == 'table.xlsx'
    service.store.append_event(session_id=identity, kind='tool.result', payload={
        'status': 'done', 'tool_name': 'web_search', 'context_output': 'https://example.com/new'})
    assert len(service.session_sources(identity)['sources']) == 3


def test_index_refuses_unrecorded_paths_secrets_and_external_symlinks(session, tmp_path):
    service, identity, work = session
    outside = tmp_path / 'private.txt'
    outside.write_text('private')
    (work / 'linked.txt').symlink_to(outside)
    (work / '.env').write_text('private')
    (work / 'private.pem').write_text('private')
    (work / 'unrelated.md').write_text('unrelated')
    service.store.append_event(session_id=identity, kind='tool.result', payload={
        'status': 'done', 'tool_name': 'Bash',
        'context_output': '\n'.join(str(work / x) for x in ['linked.txt', '.env', 'private.pem'])})
    assert service.list_session_files(identity) == []
    with pytest.raises(MobileNotFound):
        service.open_session_file(identity, '0' * 64)


def test_shell_tracking_observes_binary_output_without_printed_filenames(tmp_path):
    from factory.core.cc_backend_tools import _handle_bash
    work = tmp_path / 'workspace'
    work.mkdir()
    tracker = TaskChangeTracker(task_id='shell-fixture', workdir=str(work), data_root=str(tmp_path / 'runtime'))
    result = _handle_bash({'command': "printf 'PK binary' > table.xlsx; printf 'entry' > journal.md"},
        workdir=str(work), change_tracker=tracker, filesystem_mode='workspace', guard_mode='on')
    assert 'Error' not in result
    assert {x['path'] for x in tracker.files()} == {'table.xlsx', 'journal.md'}
    before = tracker.workspace_snapshot()
    tracker.record_edit(str(work / 'journal.md'), 'old', 'new\nentry')
    (work / 'journal.md').write_text('new\nentry')
    count = next(x for x in tracker.files() if x['path'] == 'journal.md')['additions']
    tracker.record_shell_changes(before)
    assert next(x for x in tracker.files() if x['path'] == 'journal.md')['additions'] == count


def test_incomplete_shell_snapshot_does_not_report_false_deletions(tmp_path, monkeypatch):
    tracker = TaskChangeTracker(task_id='partial', workdir=str(tmp_path), data_root=str(tmp_path / '.runtime'))
    monkeypatch.setattr(tracker, 'workspace_snapshot', lambda: None)
    tracker.record_shell_changes({'retained.md': (1, 1, 1, 1)})
    assert tracker.files() == []
    assert load_task_changes('partial', data_root=str(tmp_path / '.runtime'))['tracking_partial'] is True
