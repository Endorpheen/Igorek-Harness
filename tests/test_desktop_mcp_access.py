import importlib.util
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'desktop'))
from mcp_access import McpAccess
from factory.core.vault.lifecycle import VaultLifecycle, VaultAuthenticationError
from provider_setup import profile_config


def test_default_deny_exact_profile_binding_and_revoke(tmp_path):
    vault = VaultLifecycle(tmp_path / 'vault')
    vault.create('password')
    config = profile_config('first','model','https://example.test/v1','openai')
    record = vault.add_credential('desktop-profile:first','fixture','api_key',imported_from='desktop-provider-setup',secret_metadata={'desktop_profile':config})
    root = tmp_path / 'runtime'
    (root / 'mcp').mkdir(parents=True)
    (root / 'mcp/servers.json').write_text(json.dumps({'mcpServers':{'mail':{'command':'fixture'}}}))
    access = McpAccess(root,vault)
    try:
        assert not access.authorize('mail',config)
        with pytest.raises(VaultAuthenticationError): access.save('mail',record.id,'allow','wrong')
        access.save('mail',record.id,'allow','password')
        assert access.authorize('mail',config)
        assert not access.authorize('mail',{**config,'base_url':'https://another.test/v1'})
        assert not access.authorize('unknown',config)
        access.save('mail',record.id,'local_only','password')
        assert not access.authorize('mail',config)
        access.save('mail',record.id,'deny','password')
        assert not access.authorize('mail',config)
        access.save('mail',record.id,'allow','password')
        assert 'password' not in access.path.read_text() and 'fixture' not in access.path.read_text()
        vault.lock()
        assert not access.authorize('mail',config)
    finally: vault.lock()


def test_bridge_checks_before_start_call_and_after_response(monkeypatch):
    from factory.core import mcp_bridge
    client=mcp_bridge.MCPClient('mail','fixture',[])
    calls=[]
    monkeypatch.setattr(client,'_send_request',lambda *args: calls.append(args) or {'content':[{'type':'text','text':'private-fixture'}]})
    monkeypatch.setattr(mcp_bridge,'_DESKTOP_ACCESS_CHECK',lambda server:False)
    with pytest.raises(PermissionError):client.start()
    assert json.loads(client.call_tool('read',{}))['error']=='mcp_access_denied'
    assert not calls
    decisions=iter([True,False])
    monkeypatch.setattr(mcp_bridge,'_DESKTOP_ACCESS_CHECK',lambda server:next(decisions))
    assert 'private-fixture' not in client.call_tool('read',{})
    assert len(calls)==1
    monkeypatch.setattr(mcp_bridge,'_DESKTOP_ACCESS_CHECK',None)
    assert client.call_tool('read',{})=='private-fixture'


@pytest.mark.parametrize('inherit,expected',[(True,True),(False,False)])
def test_private_stdio_does_not_inherit_unrelated_secrets(monkeypatch,inherit,expected):
    from factory.core import mcp_bridge
    monkeypatch.setenv('UNRELATED_SECRET','fixture-secret')
    captured={}
    class Process:
        stdin=stdout=stderr=None
    def spawn(*args,**kwargs):
        captured.update(kwargs['env'])
        return Process()
    monkeypatch.setattr(mcp_bridge.subprocess,'Popen',spawn)
    monkeypatch.setattr(mcp_bridge,'_DESKTOP_ACCESS_CHECK',None)
    client=mcp_bridge.MCPClient('fixture','fixture',extra_env={'THIS_MCP_SECRET':'explicit'},inherit_environment=inherit)
    monkeypatch.setattr(client,'_send_request',lambda *args:{'tools':[]})
    monkeypatch.setattr(client,'_send_notification',lambda *args:None)
    client.start()
    assert ('UNRELATED_SECRET' in captured)==expected
    assert captured['THIS_MCP_SECRET']=='explicit'
