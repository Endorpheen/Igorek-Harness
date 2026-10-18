import json
from types import SimpleNamespace

import pytest

from factory.core import opencode_tasks as tasks
from factory.core import opencode_workspace as snapshots


@pytest.fixture
def project(tmp_path):
    root = tmp_path / 'project'
    root.mkdir()
    snapshots.git(root, 'init', '--initial-branch=main')
    (root / 'code.py').write_text('base\n')
    (root / 'other.py').write_text('other\n')
    (root / '.gitignore').write_text('*.ignored\n.env\n')
    snapshots.git(root, 'add', '--all')
    snapshots.git(root, '-c', 'user.name=Fixture', '-c', 'user.email=fixture@example.invalid', 'commit', '-m', 'Create snapshot fixture')
    return root


def test_snapshot_preserves_dirty_and_untracked_files_without_sharing_git(project, tmp_path):
    (project / 'code.py').write_text('dirty\n')
    (project / 'new.py').write_text('new\n')
    (project / '.env').write_text('private canary')
    (project / 'build.ignored').write_text('ignored')
    workspace = snapshots.prepare(str(project), tmp_path / 'task')
    assert (workspace / 'code.py').read_text() == 'dirty\n'
    assert (workspace / 'new.py').read_text() == 'new\n'
    assert not (workspace / '.env').exists()
    assert not (workspace / 'build.ignored').exists()
    assert snapshots.git(workspace, 'remote').strip() == b''
    assert snapshots.git(workspace, 'rev-parse', '--absolute-git-dir') != snapshots.git(project, 'rev-parse', '--absolute-git-dir')
    (workspace / 'code.py').write_text('delegated\n')
    assert (project / 'code.py').read_text() == 'dirty\n'


def test_apply_preserves_unrelated_work_and_detects_same_file_conflict(project, tmp_path):
    folder = tmp_path / 'task'
    workspace = snapshots.prepare(str(project), folder)
    (workspace / 'code.py').write_text('delegated\n')
    (project / 'other.py').write_text('parallel\n')
    assert snapshots.apply_result(str(project), folder)['applied']
    assert (project / 'code.py').read_text() == 'delegated\n'
    assert (project / 'other.py').read_text() == 'parallel\n'
    assert snapshots.apply_result(str(project), folder) == {'applied': False, 'conflicts': ['code.py'], 'files': ['code.py']}


def test_conflict_does_not_partially_apply_other_files(project, tmp_path):
    folder = tmp_path / 'task'
    workspace = snapshots.prepare(str(project), folder)
    (workspace / 'code.py').write_text('delegated\n')
    (workspace / 'other.py').unlink()
    (project / 'code.py').write_text('parallel\n')
    result = snapshots.apply_result(str(project), folder)
    assert not result['applied']
    assert (project / 'other.py').read_text() == 'other\n'
    assert (project / 'code.py').read_text() == 'parallel\n'


def test_symlink_result_cannot_write_outside_project(project, tmp_path):
    folder = tmp_path / 'task'
    workspace = snapshots.prepare(str(project), folder)
    (workspace / 'code.py').unlink()
    (workspace / 'code.py').symlink_to(tmp_path / 'outside')
    with pytest.raises(ValueError, match='symlink'):
        snapshots.apply_result(str(project), folder)
    assert (project / 'code.py').read_text() == 'base\n'


class Store:
    def __init__(self):
        self.events = []

    def append_event(self, **event):
        self.events.append(event)
        return len(self.events)


def client(tmp_path, monkeypatch):
    service = SimpleNamespace(store=Store(), _publish_event=lambda **_: None,
                              _active_task_id_for_session=lambda _: '', deliver_opencode_result=lambda _: True)
    instance = tasks.OpenCodeTasks(tmp_path / 'tasks', service, lambda _: None, lambda _: True, timeout=.01)
    replies = []

    def request(fields, *args, **kwargs):
        replies.append(fields)
        return json.dumps({'session_id': 'ses_fixture'} if fields['action'] == 'start' else {'outcome': None, 'inputs': [], 'messages': []})

    monkeypatch.setattr(instance, 'request', request)
    return instance, replies


def test_start_is_idempotent_and_model_cannot_answer_owner_questions(project, tmp_path, monkeypatch):
    instance, requests = client(tmp_path, monkeypatch)
    context = dict(workdir=str(project), current_session='chat-1', profile='fixture', task_id='parent', tool_use_id='call-1')
    first = json.loads(instance.call({'action': 'start', 'goal': 'Change code', 'title': 'Change'}, **context))
    second = json.loads(instance.call({'action': 'start', 'goal': 'Change code', 'title': 'Change'}, **context))
    assert first == second
    assert first['status'] == 'running'
    assert len(requests) == 1
    assert json.loads(instance.call({'action': 'answer', 'run_id': first['run_id']}, **context))['error'] == 'invalid_request'
    assert json.loads(instance.call({'action': 'status', 'run_id': first['run_id']}, **(context | {'current_session': 'other'})))['error'] == 'opencode_task_not_found'
    assert tasks.SCHEMA['input_schema']['properties']['action']['enum'] == ['start', 'status', 'cancel']


def test_pending_inputs_do_not_make_main_chat_busy_and_restart_never_relaunches(project, tmp_path, monkeypatch):
    instance, requests = client(tmp_path, monkeypatch)
    context = dict(workdir=str(project), current_session='chat-1', profile='fixture', task_id='parent', tool_use_id='call-1')
    started = json.loads(instance.call({'action': 'start', 'goal': 'Change code', 'title': 'Change'}, **context))
    monkeypatch.setattr(instance, 'request', lambda *_args, **_kwargs: json.dumps({'outcome': None, 'messages': [], 'inputs': [
        {'id': 'frm_fixture', 'kind': 'form', 'title': 'Choose', 'fields': [{'key': 'answer', 'type': 'string'}]}]}))
    instance.poll()
    assert instance.approvals('chat-1')[0]['request_id'] == 'frm_fixture'
    assert not instance.approvals('other')
    recovered = tasks.OpenCodeTasks(instance.root, instance.service, lambda _: None, lambda _: True)
    assert recovered.lookup(started['run_id'])['state'] == 'interrupted'
    assert len(requests) == 1


def test_default_browser_pipe_protocol_stays_compatible(tmp_path, monkeypatch):
    instance, _ = client(tmp_path, monkeypatch)
    frames = []
    instance.emit = frames.append
    tasks.DesktopBrowserPipe.request(instance, {'action': 'status'})
    assert frames[0]['type'] == 'opencode_request'
    assert not instance.receive({'type': 'browser_reply', 'id': frames[0]['id'], 'result': {}})


def test_completion_applies_once_and_defers_main_result_until_conversation_is_idle(project, tmp_path, monkeypatch):
    import factory.core.cli_chat as journal
    monkeypatch.setattr(journal, '_append_entry', lambda *args, **kwargs: None)
    instance, _ = client(tmp_path, monkeypatch)
    busy, deliveries = [True], []
    instance.service._active_task_id_for_session = lambda _: 'current' if busy[0] else ''
    instance.service.deliver_opencode_result = lambda run: deliveries.append(run['id']) or True
    context = dict(workdir=str(project), current_session='chat-1', profile='fixture', task_id='parent', tool_use_id='call-1')
    started = json.loads(instance.call({'action': 'start', 'goal': 'Change code', 'title': 'Change'}, **context))
    run = instance.lookup(started['run_id'])
    (instance.root / run['id'] / 'workspace' / 'code.py').write_text('delegated\n')
    instance.request = lambda *_args, **_kwargs: json.dumps({'outcome': 'succeeded', 'inputs': [], 'messages': [{'id': 'msg_1', 'text': 'Done'}], 'result': 'Done'})
    instance.poll()
    assert not deliveries
    assert (project / 'code.py').read_text() == 'delegated\n'
    busy[0] = False
    instance.poll()
    instance.poll()
    assert deliveries == [run['id']]
    assert instance.lookup(run['id'])['delivered']


def test_forms_are_journaled_without_busy_main_task_and_direct_answer_resolves_them(project, tmp_path, monkeypatch):
    instance, _ = client(tmp_path, monkeypatch)
    context = dict(workdir=str(project), current_session='chat-1', profile='fixture', task_id='parent', tool_use_id='call-1')
    json.loads(instance.call({'action': 'start', 'goal': 'Change code', 'title': 'Change'}, **context))
    snapshot = {'outcome': None, 'messages': [], 'inputs': [{'id': 'frm_1', 'kind': 'form', 'title': 'Choose', 'fields': []}]}
    instance.request = lambda *_args, **_kwargs: json.dumps(snapshot)
    instance.poll()
    assert [item['kind'] for item in instance.service.store.events] == ['approval.required']
    instance.poll()
    assert len(instance.service.store.events) == 1
    snapshot['inputs'] = []
    instance.poll()
    assert instance.service.store.events[-1]['kind'] == 'approval.resolved'


def test_cancel_resolves_pending_cards_without_answering_them(project, tmp_path, monkeypatch):
    instance, _ = client(tmp_path, monkeypatch)
    context = dict(workdir=str(project), current_session='chat-1', profile='fixture', task_id='parent', tool_use_id='call-1')
    started = json.loads(instance.call({'action': 'start', 'goal': 'Change code', 'title': 'Change'}, **context))
    run = instance.lookup(started['run_id'])
    run['inputs'] = [{'id': 'frm_pending'}]
    instance.save(run)
    instance.call({'action': 'cancel', 'run_id': run['id']}, **context)
    assert instance.approvals('chat-1') == []
    assert instance.service.store.events[-1]['payload']['decision'] == 'cancel'
