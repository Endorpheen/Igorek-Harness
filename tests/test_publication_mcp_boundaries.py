"""MCP discovery cannot replace built-ins or retain revoked registrations."""
import json

import pytest

from factory.core import mcp_bridge as module
from factory.core.tool_registry import ToolRegistry


@pytest.fixture
def registry(monkeypatch):
    registry = ToolRegistry()
    monkeypatch.setattr(module, 'registry', registry)
    monkeypatch.setattr(module, '_MCP_CLIENTS', [])
    monkeypatch.setattr(module, '_MCP_TOOL_OWNERS', {}, raising=False)
    return registry


class Client:
    env = {}

    def __init__(self, name, tools):
        self.name = name
        self.tools = [{'name': name, 'inputSchema': {'type': 'object'}} for name in tools]
        self.stopped = False

    def make_handler(self, name):
        return lambda args, **kwargs: self.name + ':' + name

    def stop(self):
        self.stopped = True


def test_mcp_tool_cannot_replace_a_builtin(registry):
    registry.register(name='Read', toolset='files', schema={'name': 'Read'},
                      handler=lambda *args, **kwargs: 'trusted fixture', description='fixture')
    with pytest.raises(ValueError):
        module._register_server_tools(Client('untrusted', ['Read']))
    assert registry.dispatch('Read', {}) == 'trusted fixture'


def test_two_servers_cannot_replace_each_others_tools(registry):
    module._register_server_tools(Client('first', ['shared']))
    with pytest.raises(ValueError):
        module._register_server_tools(Client('second', ['shared']))
    assert registry.dispatch('shared', {}) == 'first:shared'


def test_disabling_server_removes_schema_and_denies_captured_handler(registry):
    client = Client('fixture', ['fixture_read'])
    module._register_server_tools(client)
    module._register_client(client)
    captured = registry._tools['fixture_read'].handler
    assert captured({}) == 'fixture:fixture_read'
    result = module.reload_mcp_server_from_registry('fixture', {'disabled': True})
    assert result['status'] == 'skipped'
    assert not registry.has_tool('fixture_read')
    assert client.stopped
    assert json.loads(captured({}))['error'] == 'mcp_access_denied'


def test_successful_reload_replaces_matching_tools_and_retires_removed_ones(registry):
    old = Client('fixture', ['same', 'removed'])
    module._register_server_tools(old)
    module._register_client(old)
    new = Client('fixture', ['same', 'added'])
    module._register_server_tools(new)
    module._register_client(new)
    assert old.stopped
    assert registry.get_all_tool_names() == ['added', 'same']
    assert registry.dispatch('same', {}) == 'fixture:same'


def test_eviction_never_deregisters_a_trusted_replacement(registry):
    client = Client('fixture', ['replacement'])
    module._register_server_tools(client)
    module._register_client(client)
    registry.register(name='replacement', toolset='files', schema={'name': 'replacement'},
                      handler=lambda *args, **kwargs: 'trusted fixture')
    module._stop_and_evict_clients('fixture')
    assert registry.dispatch('replacement', {}) == 'trusted fixture'
