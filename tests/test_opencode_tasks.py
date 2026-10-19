import json
import os
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


def test_private_snapshot_disables_background_maintenance_without_changing_source_config(project, tmp_path):
    snapshots.git(project, 'config', 'maintenance.auto', 'true')
    snapshots.git(project, 'config', 'gc.auto', '1')
    folder = tmp_path / 'task'
    workspace = snapshots.prepare(str(project), folder)
    assert snapshots.git(workspace, 'config', '--get', 'maintenance.auto').strip() == b'false'
    assert snapshots.git(workspace, 'config', '--get', 'gc.auto').strip() == b'0'
    assert snapshots.git(folder, '--git-dir=baseline.git', 'config', '--get', 'maintenance.auto').strip() == b'false'
    assert snapshots.git(project, 'config', '--get', 'maintenance.auto').strip() == b'true'
    assert snapshots.git(project, 'config', '--get', 'gc.auto').strip() == b'1'


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
        return json.dumps({'session_id': 'ses_fixture', 'execution_mode': 'direct'} if fields['action'] == 'start' else {'outcome': None, 'inputs': [], 'messages': []})

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
    assert tasks.SCHEMA['input_schema']['properties']['action']['enum'] == ['start', 'status']


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


def test_direct_completion_does_not_transfer_files_and_defers_result_until_conversation_is_idle(project, tmp_path, monkeypatch):
    import factory.core.cli_chat as journal
    monkeypatch.setattr(journal, '_append_entry', lambda *args, **kwargs: None)
    instance, _ = client(tmp_path, monkeypatch)
    busy, deliveries = [True], []
    instance.service._active_task_id_for_session = lambda _: 'current' if busy[0] else ''
    instance.service.deliver_opencode_result = lambda run: deliveries.append(run['id']) or True
    context = dict(workdir=str(project), current_session='chat-1', profile='fixture', task_id='parent', tool_use_id='call-1')
    started = json.loads(instance.call({'action': 'start', 'goal': 'Change code', 'title': 'Change'}, **context))
    run = instance.lookup(started['run_id'])
    (project / 'code.py').write_text('delegated\n')
    monkeypatch.setattr(snapshots, 'apply_result', lambda *_: pytest.fail('Direct execution must never apply a copy'))
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


def test_model_cannot_cancel_or_answer_even_with_pending_owner_cards(project, tmp_path, monkeypatch):
    instance, requests = client(tmp_path, monkeypatch)
    context = dict(workdir=str(project), current_session='chat-1', profile='fixture', task_id='parent', tool_use_id='call-1')
    started = json.loads(instance.call({'action': 'start', 'goal': 'Change code', 'title': 'Change'}, **context))
    run = instance.lookup(started['run_id'])
    run['inputs'] = [{'id': 'frm_pending'}]
    instance.save(run)
    assert json.loads(instance.call({'action': 'cancel', 'run_id': run['id']}, **context))['error'] == 'opencode_observation_read_only'
    assert json.loads(instance.call({'action': 'answer', 'run_id': run['id']}, **context))['error'] == 'invalid_request'
    assert instance.approvals('chat-1')[0]['request_id'] == 'frm_pending'
    assert instance.lookup(run['id'])['state'] == 'running'
    assert [r['action'] for r in requests] == ['start']
    assert not instance.service.store.events


def start(instance, project, **extra):
    return json.loads(instance.call({'action': 'start', 'goal': 'Change code', 'title': 'Change'},
        **(dict(workdir=str(project), current_session='chat-1', profile='fixture', task_id='parent', tool_use_id='call-1') | extra)))


def test_direct_admission_does_not_walk_hash_or_copy_project(project, tmp_path, monkeypatch):
    (project / 'relative-link').symlink_to('code.py')
    (project / 'absolute-link').symlink_to(project / 'code.py')
    if os.name != 'nt':
        with (project / 'large-data.bin').open('wb') as stream:
            stream.truncate(10 * 1024 ** 3)
    instance, requests = client(tmp_path, monkeypatch)
    monkeypatch.setattr(snapshots, 'prepare', lambda *_: pytest.fail('Must not copy the project'))
    monkeypatch.setattr(snapshots, 'inventory', lambda *_: pytest.fail('Must not enumerate or hash files'))
    result = start(instance, project)
    assert result['status'] == 'running'
    assert requests[0]['source'] == str(project) and requests[0]['execution_mode'] == 'direct'
    assert 'workspace' not in requests[0]
    assert list(instance.root.iterdir()) == [instance.db]
    assert (project / 'relative-link').is_symlink()


def test_owner_interruption_preserves_live_changes_and_releases_writes(project, tmp_path, monkeypatch):
    from factory.core.tool_registry import ToolRegistry
    instance, _ = client(tmp_path, monkeypatch)
    registry = ToolRegistry()
    registry.add_execution_guard(instance)
    calls = []
    for name in ['Read', 'Grep', 'Glob', 'Write', 'Edit', 'Bash', 'delegate_task', 'unknown_mutator']:
        registry.register(name=name, toolset='fixture', schema={}, handler=lambda args, **kw: calls.append(args) or 'ok')
    result = start(instance, project)
    ctx = dict(workdir=str(project), current_session='chat-1')
    for name in ['Read', 'Grep', 'Glob']:
        assert registry.dispatch(name, {}, **ctx) == 'ok'
    for name in ['Write', 'Edit', 'Bash', 'delegate_task', 'unknown_mutator']:
        assert json.loads(registry.dispatch(name, {}, **ctx))['error'] == 'opencode_project_read_only'
    assert len(calls) == 3
    assert registry.dispatch('Write', {}, workdir=str(tmp_path / 'unrelated'), current_session='other') == 'ok'
    assert json.loads(registry.dispatch('Write', {'file_path': str(project / 'code.py')},
        workdir=str(tmp_path / 'unrelated'), current_session='other'))['error'] == 'opencode_project_read_only'
    (project / 'code.py').write_text('live change')
    monkeypatch.setattr(instance, 'request', lambda *a, **kw: json.dumps({'outcome': 'interrupted', 'inputs': [], 'messages': []}))
    instance.poll()
    assert (project / 'code.py').read_text() == 'live change'
    assert registry.dispatch('Write', {}, **ctx) == 'ok'


def test_direct_start_rejects_inflight_writer_without_stalling_other_projects(project, tmp_path, monkeypatch):
    instance, requests = client(tmp_path, monkeypatch)
    with instance.lease('Bash', {}, dict(workdir=str(project), current_session='other')) as error:
        assert error is None
        assert start(instance, project)['error'] == 'opencode_project_writer_active'
        assert not requests
    assert start(instance, project)['status'] == 'running'
    assert start(instance, project, task_id='second')['error'] == 'opencode_task_already_running'


def test_direct_mode_refuses_mixed_old_desktop_protocol(project, tmp_path, monkeypatch):
    instance, _ = client(tmp_path, monkeypatch)
    instance.request = lambda *args, **kwargs: json.dumps({'session_id': 'ses_old'})
    result = start(instance, project)
    assert result['status'] == 'failed' and 'opencode_direct_protocol_required' in result['result']


def test_recovery_does_not_apply_legacy_workspace(project, tmp_path, monkeypatch):
    instance, _ = client(tmp_path, monkeypatch)
    result = start(instance, project)
    run = instance.lookup(result['run_id'])
    run.pop('execution_mode')
    run['workspace'] = str(tmp_path / 'old-workspace')
    instance.save(run)
    monkeypatch.setattr(snapshots, 'apply_result', lambda *_: pytest.fail('Never apply legacy workspaces on restart'))
    recovered = tasks.OpenCodeTasks(instance.root, instance.service, lambda _: None, lambda _: True)
    assert recovered.lookup(run['id'])['state'] == 'interrupted'
    assert (project / 'code.py').read_text() == 'base\n'


@pytest.mark.parametrize('forced', [False, True])
def test_readonly_executor_never_asks_approval_or_executes_normal_and_resumed_writes(project, tmp_path, monkeypatch, forced):
    from factory.core import cc_backend_executor as executor
    from factory.core.cc_backend_tools import get_tool_schemas
    from factory.core.tool_registry import registry
    instance, _ = client(tmp_path, monkeypatch)
    start(instance, project)
    registry.add_execution_guard(instance)
    posted = []
    args = {'file_path': str(project / 'code.py'), 'content': 'must not write'}
    def post(url, payload, headers, *unused, **kwargs):
        posted.append(json.loads(json.dumps(payload)))
        if not forced and len(posted) == 1:
            message = {'role': 'assistant', 'content': None, 'tool_calls': [{'id': 'write', 'type': 'function',
                'function': {'name': 'Write', 'arguments': json.dumps(args)}}]}
        else:
            message = {'role': 'assistant', 'content': 'Поручение выполняется; запись недоступна.'}
        return {'choices': [{'message': message, 'finish_reason': 'stop'}], 'usage': {}}, 1
    monkeypatch.setattr(executor, '_http_post_streaming_openai', post)
    monkeypatch.setattr(executor, '_resolve_backend_context_window', lambda **kwargs: (32768, 'fixture'))
    monkeypatch.setattr(executor, 'is_interrupt_requested', lambda: False)
    monkeypatch.setattr(executor, 'task_should_stop', lambda task_id: False)
    monkeypatch.setattr(executor, 'needs_approval', lambda *a: pytest.fail('Read-only calls must not request approval'))
    metadata = {'forced_tool_call': {'name': 'Write', 'input': args, 'tool_use_id': 'write',
        'pending_tool_calls': [{'name': 'Write', 'input': args, 'id': 'pending-write'}]}} if forced else {}
    (tmp_path / 'artifacts').mkdir()
    try:
        result = executor.execute_cc_backend_task(base_url='http://127.0.0.1:1/v1', model='fixture', system_prompt='fixture',
            user_message='Continue talking', workdir=str(project), artifact_workdir=str(tmp_path / 'artifacts'),
            api_format='openai', current_session='chat-1', metadata=metadata,
            enabled_tools=[tool for tool in get_tool_schemas() if tool['name'] == 'Write'], checkpoint_config={'enabled': False})
        assert result['status'] == 'ok'
        assert 'opencode_project_read_only' in json.dumps(posted)
        assert (project / 'code.py').read_text() == 'base\n'
    finally:
        registry.remove_execution_guard(instance)


@pytest.mark.parametrize('kind', ['relative', 'absolute', 'external', 'directory', 'broken'])
def test_windows_project_enumeration_accepts_only_internal_file_links(project, tmp_path, kind):
    from factory.core.platform_support.windows_sandbox import WindowsSandbox
    target = project / 'code.py'
    if kind == 'external':
        target = tmp_path / 'outside'
        target.write_text('outside')
    elif kind == 'directory':
        target = project / 'directory'
        target.mkdir()
    elif kind == 'broken':
        target = project / 'missing'
    (project / 'link').symlink_to('code.py' if kind == 'relative' else target, target_is_directory=kind == 'directory')
    if kind in {'relative', 'absolute'}:
        assert target in list(WindowsSandbox._paths(project))
    else:
        with pytest.raises((ValueError, OSError)):
            list(WindowsSandbox._paths(project))


def progress_fixture(**extra):
    return dict(schema=1, observed_at=tasks.time.time() * 1000, session_active=True,
                latest_message={'id': 'msg_live', 'text': 'Reading the project', 'completed': False},
                recent_actions=[{'id': 'read-1', 'tool': 'read', 'status': 'completed', 'target': 'code.py'}],
                active_actions=[{'id': 'write-1', 'tool': 'write', 'status': 'running', 'target': 'other.py'}],
                action_counts={'completed': 1, 'running': 1, 'streaming': 0, 'error': 0}, **extra)


def test_status_fetches_live_progress_without_sending_control_or_losing_auto_delivery(project, tmp_path, monkeypatch):
    import factory.core.cli_chat as journal
    journaled = []
    monkeypatch.setattr(journal, '_append_entry', lambda *args, **kwargs: journaled.append(kwargs))
    instance, requests = client(tmp_path, monkeypatch)
    started = start(instance, project)
    def request(fields, *args, **kwargs):
        requests.append(fields)
        assert fields['action'] == 'status'
        return json.dumps({'outcome': None, 'inputs': [{'id': 'frm_pending', 'kind': 'form', 'title': 'Choose', 'fields': []}],
                           'messages': [{'id': 'msg_auto', 'text': 'Finished reading'}], 'progress': progress_fixture()})
    monkeypatch.setattr(instance, 'request', request)
    status = json.loads(instance.call({'action': 'status'}, current_session='chat-1', profile='fixture'))
    assert status['run_id'] == started['run_id'] and status['status'] == 'running' and status['result'] == ''
    progress = status['progress']
    assert progress['available'] and progress['freshness'] == 'live'
    assert progress['active_actions'][0]['target'] == 'other.py'
    assert progress['recent_actions'][0]['status'] == 'completed'
    assert progress['latest_message']['completed'] is False
    assert progress['waiting_for_owner'][0]['id'] == 'frm_pending'
    instance.poll()
    assert len(journaled) == 1
    assert [event['kind'] for event in instance.service.store.events].count('opencode.message') == 1
    assert [event['kind'] for event in instance.service.store.events].count('approval.required') == 1
    assert all(r['action'] in {'start', 'status'} for r in requests)


def test_status_falls_back_to_marked_stale_snapshot_and_never_relaunches(project, tmp_path, monkeypatch):
    instance, requests = client(tmp_path, monkeypatch)
    started = start(instance, project)
    run = instance.lookup(started['run_id'])
    run['progress'] = instance.progress_snapshot(progress_fixture(), [])
    run['progress']['observed_at'] -= 20000
    instance.save(run)
    monkeypatch.setattr(instance, 'request', lambda *a, **kw: json.dumps({'error': 'desktop_browser_timeout'}))
    result = json.loads(instance.call({'action': 'status'}, current_session='chat-1', profile='fixture'))
    assert result['status'] == 'running' and result['progress']['freshness'] == 'stale'
    assert result['progress']['age_seconds'] >= 20
    assert result['progress']['refresh_error'] == 'opencode_status_unavailable'
    assert len(requests) == 1
    assert json.loads(instance.call({'action': 'status', 'run_id': started['run_id']},
                                   current_session='other', profile='fixture'))['error'] == 'opencode_task_not_found'
    assert json.loads(instance.call({'action': 'status'}, current_session='other', profile='fixture'))['error'] == 'opencode_task_not_found'


def test_missing_progress_is_unavailable_not_fabricated_and_legacy_receipts_remain_readable(project, tmp_path, monkeypatch):
    instance, _ = client(tmp_path, monkeypatch)
    start(instance, project)
    result = json.loads(instance.call({'action': 'status'}, current_session='chat-1', profile='fixture'))
    assert result['progress']['freshness'] == 'unavailable' and result['progress']['available'] is False
    run = instance.runs()[0]
    run.update(state='completed', result='Done')
    instance.save(run)
    monkeypatch.setattr(instance, 'request', lambda *a, **kw: pytest.fail('Completed receipts must not contact an unrelated live service'))
    assert json.loads(instance.call({'action': 'status'}, current_session='chat-1', profile='fixture'))['result'] == 'Done'


def test_observation_payload_is_bounded_redacted_and_does_not_relay_reasoning_or_tool_inputs():
    value = progress_fixture()
    value['latest_message']['text'] = 'token=fixture-secret-value ' + 'x' * 6000
    value['latest_message']['reasoning'] = 'must not relay'
    value['active_actions'] = [dict(id=str(i), tool='shell', status='running', input={'command': 'must not relay'},
                                     content='must not relay', time={'ran': 5, 'completed': float('inf')}) for i in range(30)]
    result = tasks.OpenCodeTasks.progress_snapshot(value, [{'id':str(i),'kind':'form','title':'Choose'} for i in range(20)])
    assert len(result['active_actions']) == len(result['waiting_for_owner']) == 10
    assert result['pending_input_count'] == 20 and result['pending_inputs_truncated']
    assert len(result['latest_message']['text']) <= 4000
    assert 'fixture-secret-value' not in json.dumps(result) and 'must not relay' not in json.dumps(result)
    assert tasks.OpenCodeTasks.progress_snapshot({'schema':1,'observed_at':float('nan')}, []) is None


def test_main_chat_can_request_and_explain_running_progress_via_actual_tool_dispatch(project, tmp_path, monkeypatch):
    from factory.core import cc_backend_executor as executor
    from factory.core.tool_registry import registry
    from factory.core.cc_backend_tools import get_tool_schemas
    instance, _ = client(tmp_path, monkeypatch)
    start(instance, project)
    instance.request = lambda *a, **kw: json.dumps({'outcome': None, 'inputs': [], 'messages': [], 'progress': progress_fixture()})
    monkeypatch.setattr(registry, '_tools', dict(registry._tools))
    monkeypatch.setattr(registry, '_toolset_checks', dict(registry._toolset_checks))
    instance.install()
    posted = []
    def post(url, payload, headers, *a, **kw):
        posted.append(json.loads(json.dumps(payload)))
        message = {'role': 'assistant', 'content': 'OpenCode редактирует other.py; чтение code.py завершено.'}
        if len(posted) == 1:
            message = {'role': 'assistant', 'content': None, 'tool_calls': [{'id':'observe','type':'function',
                'function':{'name':'opencode_task','arguments':'{"action":"status"}'}}]}
        return {'choices':[{'message':message,'finish_reason':'stop'}], 'usage':{}}, 1
    monkeypatch.setattr(executor, '_http_post_streaming_openai', post)
    monkeypatch.setattr(executor, '_resolve_backend_context_window', lambda **kw: (32768, 'fixture'))
    monkeypatch.setattr(executor, 'is_interrupt_requested', lambda: False)
    monkeypatch.setattr(executor, 'task_should_stop', lambda task_id: False)
    (tmp_path / 'artifacts').mkdir()
    try:
        result = executor.execute_cc_backend_task(base_url='http://127.0.0.1:1/v1', model='fixture', system_prompt='fixture',
            user_message='What is OpenCode doing?', workdir=str(project), artifact_workdir=str(tmp_path/'artifacts'),
            api_format='openai', current_session='chat-1', profile='fixture',
            enabled_tools=[t for t in get_tool_schemas() if t['name']=='opencode_task'], checkpoint_config={'enabled':False})
        assert result['status']=='ok' and 'other.py' in result['output_text']
        tool_results=[m for m in posted[1]['messages'] if m.get('role')=='tool']
        assert len(tool_results)==1
        value=json.loads(tool_results[0]['content'])
        assert value['progress']['active_actions'][0]['target']=='other.py'
        assert value['status']=='running' and (project/'other.py').read_text()=='other\n'
    finally:
        instance.close()


def test_owner_interrupt_closes_pending_chat_cards_without_answer_or_interrupt_requests(project, tmp_path, monkeypatch):
    instance, _ = client(tmp_path, monkeypatch)
    started = start(instance, project)
    run = instance.lookup(started['run_id'])
    run['inputs'] = [{'id':'frm_stopped','kind':'form','title':'Choose'}]
    instance.save(run)
    requests = []
    def request(fields, *a, **kw):
        requests.append(fields)
        return json.dumps({'outcome':'interrupted','inputs':run['inputs'],'messages':[], 'progress':progress_fixture()})
    monkeypatch.setattr(instance, 'request', request)
    value=json.loads(instance.call({'action':'status'}, current_session='chat-1', profile='fixture'))
    assert value['status']=='interrupted' and value['progress']['freshness']=='historical'
    assert value['progress']['waiting_for_owner']==[]
    assert instance.approvals('chat-1')==[]
    assert instance.service.store.events[-1]['payload']['decision']=='cancel'
    assert [r['action'] for r in requests]==['status']
