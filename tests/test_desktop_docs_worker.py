import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'desktop'))
from vault_owner import OwnerVault
from factory.core import mcp_bridge
from factory.core.mcp_tasks.access import McpAccess
from factory.core.mcp_tasks.plugins import PluginSettings
from factory.core.tool_registry import registry


@pytest.fixture
def setup(tmp_path, monkeypatch):
    owner = OwnerVault(tmp_path)
    owner.service.create('fixture-password')
    p = owner.providers.save('fixture-model', 'fixture', 'https://example.test/v1', 'openai', 'fixture-key')
    owner.lifecycle.check_provider_profile(p['credential_id'], lambda *_: {'status': 'verified'})
    (tmp_path / 'profiles').mkdir(exist_ok=True)
    (tmp_path / 'profiles/fixture-model.json').write_text(json.dumps(p['config']))
    monkeypatch.setenv('FACTORY_DATA_DIR', str(tmp_path))
    monkeypatch.setenv('IGOREK_FACTORY_HOME', str(tmp_path))
    monkeypatch.setattr(mcp_bridge, '_MCP_CLIENTS', [])
    monkeypatch.setattr(mcp_bridge, '_DESKTOP_ACCESS_CHECK', None)
    monkeypatch.setattr(mcp_bridge, '_DESKTOP_REGISTRY', None)
    monkeypatch.setattr(mcp_bridge, '_DESKTOP_CALENDAR', None)
    access = McpAccess(tmp_path, owner.lifecycle)
    mcp_bridge.set_desktop_access_check(lambda name: access.authorize(name, p['config']), access.registry)
    PluginSettings(tmp_path).update('openai_docs', enabled=True)
    yield owner, p, access
    mcp_bridge.shutdown_mcp_servers()
    for name in ['search_openai_docs', 'fetch_openai_doc']:
        registry.deregister(name)
    owner.close()


class Remote:
    starts = 0
    calls = []

    def __init__(self, check):
        self.check = check
        self.tools = []

    def start(self):
        assert self.check()
        type(self).starts += 1
        self.tools = [{'name': name, 'description': 'Official documentation', 'inputSchema': {'type': 'object', 'properties': {'query': {'type': 'string'}}}} for name in ['search_openai_docs', 'fetch_openai_doc']]

    def call_tool(self, name, args):
        if not self.check():
            raise PermissionError()
        assert 'output_dir' not in args
        type(self).calls.append((name, args))
        return json.dumps({'hits': [{'url': 'https://developers.openai.com/api/docs/guides/streaming-responses'}]})

    def stop(self):
        self.tools = []


def test_worker_grant_after_start_registers_docs_and_caches_discovery(setup, monkeypatch):
    from factory.core.worker import _reload_enabled_mcp_servers_for_cc_backend
    owner, profile, access = setup
    Remote.starts = 0
    monkeypatch.setattr('factory.core.mcp_tasks.remote.RemoteMcpClient', Remote)
    assert owner.plugins.status()['plugins']['openai_docs']['model_access'] == []
    assert _reload_enabled_mcp_servers_for_cc_backend('fixture-model') == []
    access.save('openai_docs', profile['credential_id'], 'allow', 'fixture-password')
    assert owner.plugins.status()['plugins']['openai_docs']['model_access'] == [{'profile': 'fixture-model', 'access': 'allow'}]
    for _ in range(2):
        result = _reload_enabled_mcp_servers_for_cc_backend('fixture-model')
        assert result[0]['status'] == 'ok'
        assert 'search_openai_docs' in {tool['name'] for tool in registry.get_definitions('mcp')}
    assert Remote.starts == 1
    assert 'hits' in registry.dispatch('search_openai_docs', {'query': 'streaming'}, workdir='/tmp')
    owner.plugins.action('openai_docs', 'disconnect')
    assert 'search_openai_docs' not in {tool['name'] for tool in registry.get_definitions('mcp')}
    assert json.loads(registry.dispatch('search_openai_docs', {'query': 'streaming'}))['error'] == 'mcp_access_denied'


@pytest.mark.parametrize('condition', ['deny', 'lock', 'changed_profile', 'local_only'])
def test_docs_main_worker_never_bypasses_permissions(setup, monkeypatch, condition):
    from factory.core.worker import _reload_enabled_mcp_servers_for_cc_backend
    owner, profile, access = setup
    monkeypatch.setattr('factory.core.mcp_tasks.remote.RemoteMcpClient', Remote)
    access.save('openai_docs', profile['credential_id'], 'allow', 'fixture-password')
    assert _reload_enabled_mcp_servers_for_cc_backend('fixture-model')[0]['status'] == 'ok'
    if condition in {'deny', 'local_only'}:
        access.save('openai_docs', profile['credential_id'], condition, 'fixture-password')
    elif condition == 'lock':
        owner.lifecycle.lock()
    else:
        profile['config']['model'] = 'changed'
    assert 'search_openai_docs' not in {tool['name'] for tool in registry.get_definitions('mcp')}
    assert json.loads(registry.dispatch('search_openai_docs', {'query': 'streaming'}))['error'] == 'mcp_access_denied'


def test_no_desktop_binding_and_calendar_not_exposed(setup):
    owner, profile, access = setup
    PluginSettings(owner.root).update('google_calendar', enabled=True)
    assert set(mcp_bridge.desktop_mcp_entries()) == {'openai_docs','google_calendar'}
    mcp_bridge.set_desktop_access_check(None)
    assert mcp_bridge.desktop_mcp_entries() == {}
    result = mcp_bridge.reload_mcp_server_from_registry('openai_docs', {'type': 'builtin', 'adapter': 'openai_docs'})
    assert result['reason'] == 'mcp_access_denied'


@pytest.mark.parametrize('api_format', ['openai', 'anthropic', 'openai-responses'])
def test_existing_executor_calls_docs_with_both_model_transports(setup, monkeypatch, tmp_path, api_format):
    from factory.core import cc_backend_executor as executor
    from factory.core.worker import _reload_enabled_mcp_servers_for_cc_backend
    from factory.core.prompt_assembly import _mcp_tools_guidance, _compact_mcp_tools_guidance
    owner, profile, access = setup
    monkeypatch.setattr('factory.core.mcp_tasks.remote.RemoteMcpClient', Remote)
    access.save('openai_docs', profile['credential_id'], 'allow', 'fixture-password')
    _reload_enabled_mcp_servers_for_cc_backend('fixture-model')
    calls = []
    def post(url, payload, headers, *args, **kwargs):
        calls.append(payload)
        first = len(calls) == 1
        if api_format == 'openai':
            answer = {'role': 'assistant', 'content': None, 'tool_calls': [{'id': 'docs', 'type': 'function', 'function': {'name': 'search_openai_docs', 'arguments': '{"query":"streaming"}'}}]} if first else {'role': 'assistant', 'content': 'Документация: https://developers.openai.com/api/docs/guides/streaming-responses'}
            return {'choices': [{'message': answer, 'finish_reason': 'tool_calls' if first else 'stop'}], 'usage': {'prompt_tokens': 1, 'completion_tokens': 1}}, 1
        content = [{'type': 'tool_use', 'id': 'docs', 'name': 'search_openai_docs', 'input': {'query': 'streaming'}}] if first else [{'type': 'text', 'text': 'Документация: https://developers.openai.com/api/docs/guides/streaming-responses'}]
        return {'content': content, 'stop_reason': 'tool_use' if first else 'end_turn', 'usage': {'input_tokens': 1, 'output_tokens': 1}}, 1
    monkeypatch.setattr(executor, '_http_post_streaming_openai', post)
    monkeypatch.setattr(executor, '_http_post', post)
    monkeypatch.setattr(executor, '_resolve_backend_context_window', lambda **kwargs: (32768, 'fixture'))
    monkeypatch.setattr(executor, 'is_interrupt_requested', lambda: False)
    monkeypatch.setattr(executor, 'task_should_stop', lambda task_id: False)
    result = executor.execute_cc_backend_task(base_url='http://127.0.0.1:1/v1', model='fixture', system_prompt=_mcp_tools_guidance(['search_openai_docs']),
        user_message='Найди документацию по streaming.', workdir=str(tmp_path), artifact_workdir=str(tmp_path), api_format=api_format,
        enabled_tools=registry.get_definitions('mcp'), checkpoint_config={'enabled': False})
    assert result['status'] == 'ok'
    assert len(calls) == 2
    assert 'streaming-responses' in json.dumps(calls[1])
    assert 'Не ищи настройки' in _compact_mcp_tools_guidance(['search_openai_docs'])
    assert 'OpenAI Docs' not in _mcp_tools_guidance(['other_tool'])
