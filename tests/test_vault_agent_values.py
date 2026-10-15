import json
import os
from pathlib import Path

import pytest

from factory.core.vault.agent_values import AgentValueWriter
from factory.core.vault.lifecycle import VaultLifecycle
from factory.core.cc_backend_tools import execute_tool, get_tool_schemas
from factory.core.isolation.channel import ChannelMessage, MessageKind
from factory.core.isolation.vault_values import VaultStoreRelay


@pytest.fixture
def setup(tmp_path):
    vault = VaultLifecycle(tmp_path / 'vault')
    vault.create('fixture-password')
    config = {'name': 'fixture-model', 'model': 'model', 'base_url': 'https://example.test/v1', 'api_format': 'openai'}
    profile = vault.add_credential('desktop-profile:fixture-model', 'fixture-model-key', 'api_key',
        imported_from='desktop-provider-setup', secret_metadata={'desktop_profile': config})
    vault.check_provider_profile(profile.id, lambda *_: {'status': 'verified'})
    root = tmp_path / 'workspace'; root.mkdir()
    source = root / 'generated-token.txt'; source.write_text('fixture-private-agent-value')
    writer = AgentValueWriter(vault, profile.id, config)
    context = {'workdir': str(root), 'task_id': 'trusted-task-123', 'session_id': 'trusted-session', 'profile': config['name']}
    args = {'source_path': source.name, 'name': 'Created by agent', 'kind': 'secret'}
    yield vault, writer, context, args
    vault.lock()


def test_agent_append_origin_encryption_idempotency_and_no_value_in_result(setup):
    vault, writer, context, args = setup
    result = json.loads(writer.save(args, **context))
    assert result['ok'] is True and result['source_file_retained'] is True
    assert 'fixture-private-agent-value' not in json.dumps(result)
    assert b'fixture-private-agent-value' not in vault.db_path.read_bytes()
    entry = result['entry']
    assert entry['origin'] == {'kind': 'agent', 'task_id': context['task_id'], 'session_id': context['session_id'], 'profile': context['profile']}
    assert vault.list_values() == [entry]
    assert vault.reveal_value(entry['credential_id'], 'fixture-password') == 'fixture-private-agent-value'
    assert json.loads(writer.save(args, **context))['entry'] == entry
    assert len(vault.list_values()) == 1
    assert (Path(context['workdir']) / args['source_path']).exists()


def test_agent_cannot_replace_owner_record_or_another_task_record(setup):
    vault, writer, context, args = setup
    owner = vault.save_value('', args['name'], 'secret', 'owner-private-value', 'fixture-password')
    assert json.loads(writer.save(args, **context))['error'] == 'value_exists'
    assert vault.reveal_value(owner['credential_id'], 'fixture-password') == 'owner-private-value'
    other_args = {**args, 'name': 'Another record'}
    created = json.loads(writer.save(other_args, **context))['entry']
    assert json.loads(writer.save(other_args, **(context | {'task_id': 'another-task'})))['error'] == 'value_exists'
    vault.save_value(created['credential_id'], created['name'], 'secret', 'owner-replacement', 'fixture-password')
    assert json.loads(writer.save(other_args, **context))['error'] == 'value_exists'
    assert vault.list_values()[0]['origin']['kind'] == 'agent'


@pytest.mark.parametrize('changes', [{'source_path': '../private'}, {'source_path': '/etc/passwd'},
    {'source_path': './generated-token.txt'}, {'source_path': 'a//b'}, {'source_path': 'x\\y'},
    {'source_path': ''}, {'kind': 'api_key'}, {'name': ''}, {'name': 'bad\nname'},
    {'kind': 'variable', 'name': 'bad-name'}, {'task_id': 'spoof'}, {'password': 'fixture-password'},
    {'value': 'raw-private-value'}, {'credential_id': 'existing'}])
def test_invalid_agent_payload_cannot_write(setup, changes):
    vault, writer, context, args = setup
    reply = json.loads(writer.save(args | changes, **context))
    assert reply == {'ok': False, 'error': 'vault_store_refused'}
    assert vault.list_values() == []


@pytest.mark.parametrize('kind', ['file_symlink', 'dir_symlink', 'hardlink', 'directory', 'oversized', 'fifo', 'invalid_utf8'])
def test_workspace_file_boundary_refuses_unsafe_sources(setup, tmp_path, kind):
    vault, writer, context, args = setup
    root = Path(context['workdir']); source = root / args['source_path']
    source.unlink()
    outside = tmp_path / 'outside.txt'; outside.write_text('outside-private')
    if kind == 'file_symlink': source.symlink_to(outside)
    elif kind == 'dir_symlink':
        (root / 'linked').symlink_to(tmp_path, target_is_directory=True)
        args['source_path'] = 'linked/outside.txt'
    elif kind == 'hardlink': os.link(outside, source)
    elif kind == 'directory': source.mkdir()
    elif kind == 'oversized': source.write_text('x' * 1025)
    elif kind == 'fifo': os.mkfifo(source)
    else: source.write_bytes(b'\xff\xfe')
    assert json.loads(writer.save(args, **context))['ok'] is False
    assert vault.list_values() == []
    assert outside.read_text() == 'outside-private'


def test_binding_lock_empty_variable_recovery_and_owner_edit_preserve_origin(setup, tmp_path):
    vault, writer, context, args = setup
    assert json.loads(writer.save(args, **(context | {'profile': 'another-model'})))['ok'] is False
    vault.lock()
    assert json.loads(writer.save(args, **context))['error'] == 'vault_locked'
    vault.unlock('fixture-password')
    (Path(context['workdir']) / args['source_path']).write_text('')
    assert json.loads(writer.save(args, **context))['ok'] is False
    result = json.loads(writer.save(args | {'name': 'EMPTY_VAR', 'kind': 'variable'}, **context))
    assert result['ok'] is True
    entry = result['entry']
    vault.save_value(entry['credential_id'], 'RENAMED_VAR', 'variable', 'new-value', 'fixture-password')
    assert vault.list_values()[0]['origin'] == entry['origin']
    restored = VaultLifecycle.restore_recovery(tmp_path / 'restored', vault.export_recovery('fixture-recovery'), 'fixture-recovery', 'new-password')
    try:
        assert restored.list_values() == vault.list_values()
    finally:
        restored.lock()


def test_task_has_bounded_append_only_budget(setup):
    vault, writer, context, args = setup
    for index in range(32):
        assert json.loads(writer.save(args | {'name': 'Generated ' + str(index)}, **context))['ok']
    assert json.loads(writer.save(args | {'name': 'Over budget'}, **context))['ok'] is False
    assert len(vault.list_values()) == 32


def test_tool_calls_only_trusted_provider_and_relay_binds_host_identity(setup):
    vault, writer, context, args = setup
    assert json.loads(execute_tool('VaultStore', args, context['workdir']))['error'] == 'vault_store_unavailable'
    provider = lambda value: writer.save(value, **context)
    result = json.loads(execute_tool('VaultStore', args, context['workdir'], vault_store_provider=provider))
    assert result['ok'] is True
    assert any(tool['name'] == 'VaultStore' for tool in get_tool_schemas())
    relay = VaultStoreRelay(writer, **context)
    request = ChannelMessage(kind=MessageKind.VAULT_STORE_REQUEST, seq=1,
        payload={'request_id': '12345678-1234-1234-1234-123456789abc', 'args': args | {'name': 'Relayed key'}})
    reply = relay.handle_request(request)
    assert relay.handle_request(request) == reply
    assert json.loads(reply['result'])['entry']['origin']['task_id'] == context['task_id']
    assert 'fixture-private-agent-value' not in json.dumps(reply)
    denied = VaultStoreRelay(None, **context).handle_request(request)
    assert json.loads(denied['result'])['ok'] is False


@pytest.mark.parametrize('api_format', ['openai', 'anthropic'])
def test_executor_routes_vault_store_without_secret_in_trace_or_model_payload(setup, tmp_path, monkeypatch, api_format):
    from factory.core import cc_backend_executor as executor
    vault, writer, context, args = setup
    calls = []
    def post(url, payload, headers, *unused, **kwargs):
        calls.append(json.loads(json.dumps(payload)))
        first = len(calls) == 1
        if api_format == 'openai':
            answer = {'role': 'assistant', 'content': None, 'tool_calls': [{'id': 'store', 'type': 'function', 'function': {'name': 'VaultStore', 'arguments': json.dumps(args)}}]} if first else {'role': 'assistant', 'content': 'Запись сохранена.'}
            return {'choices': [{'message': answer, 'finish_reason': 'tool_calls' if first else 'stop'}], 'usage': {'prompt_tokens': 1, 'completion_tokens': 1}}, 1
        content = [{'type': 'tool_use', 'id': 'store', 'name': 'VaultStore', 'input': args}] if first else [{'type': 'text', 'text': 'Запись сохранена.'}]
        return {'content': content, 'stop_reason': 'tool_use' if first else 'end_turn', 'usage': {'input_tokens': 1, 'output_tokens': 1}}, 1
    monkeypatch.setattr(executor, '_http_post_streaming_openai', post)
    monkeypatch.setattr(executor, '_http_post', post)
    monkeypatch.setattr(executor, '_resolve_backend_context_window', lambda **kwargs: (32768, 'fixture'))
    monkeypatch.setattr(executor, 'is_interrupt_requested', lambda: False)
    monkeypatch.setattr(executor, 'task_should_stop', lambda task_id: False)
    artifacts = tmp_path / 'artifacts'; artifacts.mkdir()
    result = executor.execute_cc_backend_task(base_url='http://127.0.0.1:1/v1', model='fixture', system_prompt='fixture',
        user_message='Сохрани новый ключ в Vault.', workdir=context['workdir'], artifact_workdir=str(artifacts),
        api_format=api_format, task_id=context['task_id'], profile=context['profile'], current_session=context['session_id'],
        vault_store_provider=lambda value: writer.save(value, **context),
        enabled_tools=[tool for tool in get_tool_schemas() if tool['name'] == 'VaultStore'], checkpoint_config={'enabled': False})
    assert result['status'] == 'ok'
    assert len(vault.list_values()) == 1
    assert 'fixture-private-agent-value' not in json.dumps(calls)
    assert 'fixture-private-agent-value' not in (artifacts / 'cc_backend_trace.jsonl').read_text()


@pytest.mark.parametrize('restricted', [False, True])
def test_unavailable_and_readonly_executors_cannot_store_values(setup, tmp_path, monkeypatch, restricted):
    from factory.core import cc_backend_executor as executor
    vault, writer, context, args = setup
    posted, writes = [], []
    def provider(value):
        writes.append(value)
        return writer.save(value, **context)
    def post(url, payload, headers, *unused, **kwargs):
        posted.append(json.loads(json.dumps(payload)))
        if restricted and len(posted) == 1:
            message = {'role': 'assistant', 'content': None, 'tool_calls': [{'id': 'store', 'type': 'function', 'function': {'name': 'VaultStore', 'arguments': json.dumps(args)}}]}
        else:
            message = {'role': 'assistant', 'content': 'Сохранение не выполнялось.'}
        return {'choices': [{'message': message, 'finish_reason': 'stop'}], 'usage': {}}, 1
    monkeypatch.setattr(executor, '_http_post_streaming_openai', post)
    monkeypatch.setattr(executor, '_resolve_backend_context_window', lambda **kwargs: (32768, 'fixture'))
    monkeypatch.setattr(executor, 'is_interrupt_requested', lambda: False)
    monkeypatch.setattr(executor, 'task_should_stop', lambda task_id: False)
    (tmp_path / 'artifacts').mkdir()
    executor.execute_cc_backend_task(base_url='http://127.0.0.1:1/v1', model='fixture', system_prompt='fixture', user_message='Проверка доступности.',
        workdir=context['workdir'], artifact_workdir=str(tmp_path / 'artifacts'), api_format='openai',
        vault_store_provider=provider if restricted else None,
        metadata={'restrict_tools_to': ['Read']} if restricted else {},
        enabled_tools=[tool for tool in get_tool_schemas() if tool['name'] == 'VaultStore'], checkpoint_config={'enabled': False})
    assert writes == [] and vault.list_values() == []
    if not restricted:
        assert not any(tool['function']['name'] == 'VaultStore' for tool in posted[0]['tools'])
