"""MCP failure recovery and replacement contracts; no actual server is launched."""
import io
import json
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from factory.core import mcp_bridge as m


@pytest.fixture(autouse=True)
def isolated_clients(monkeypatch):
    monkeypatch.setattr(m,'_MCP_CLIENTS',[])
    monkeypatch.setattr(m,'_DESKTOP_ACCESS_CHECK',None)
    monkeypatch.setattr(m.subprocess,'Popen',Mock(side_effect=AssertionError('no actual MCP process')))


def process(raw):
    return SimpleNamespace(stdin=io.BytesIO(),stdout=io.BytesIO(raw),stderr=io.BytesIO(),terminate=Mock(),kill=Mock(),wait=Mock(return_value=0))


@pytest.mark.parametrize('body',[[],None,True,4,'fixture'],ids=['array','null','bool','number','string'])
def test_nonobject_response_has_protocol_error_instead_of_attribute_failure(body):
    client=m.MCPClient('fixture','never-execute');client._process=process(json.dumps(body).encode()+b'\n')
    with pytest.raises(RuntimeError,match='invalid response'):
        client._send_request('fixture',{})


@pytest.mark.parametrize('error',[{'message':'fixture denied'},'fixture denied',None,[]],ids=['object','string','null','array'])
def test_provider_error_shape_never_becomes_an_attribute_error(error):
    client=m.MCPClient('fixture','never-execute');client._process=process(json.dumps({'error':error}).encode()+b'\n')
    with pytest.raises(RuntimeError,match='MCP error'):
        client._send_request('fixture',{})


def test_closed_stdout_is_explicit_failure():
    client=m.MCPClient('fixture','never-execute');client._process=process(b'')
    with pytest.raises(RuntimeError,match='closed stdout'):client._send_request('fixture',{})


@pytest.mark.parametrize('first_stop_fails,kill_fails,close_fails',[(False,False,False),(True,False,False),(True,True,True)])
def test_stop_detaches_client_and_attempts_all_cleanup(first_stop_fails,kill_fails,close_fails):
    client=m.MCPClient('fixture','never-execute');peer=process(b'')
    if first_stop_fails:peer.terminate.side_effect=OSError('fixture stop')
    if kill_fails:peer.kill.side_effect=OSError('fixture kill')
    if close_fails:
        peer.stdin=Mock(close=Mock(side_effect=OSError('fixture stdin')))
        peer.stdout=Mock(close=Mock(side_effect=OSError('fixture stdout')))
    client._process=peer;client.stop();client.stop()
    assert client._process is None
    peer.terminate.assert_called_once()
    assert peer.kill.called is first_stop_fails
    assert peer.stderr.closed


@pytest.mark.parametrize('when',['before','after','callback-error'])
def test_revocation_never_releases_tool_result(when):
    check=Mock(side_effect=[False] if when=='before' else [True,False] if when=='after' else RuntimeError('fixture access unavailable'))
    client=m.MCPClient('fixture','never-execute',access_check=check)
    request=Mock(return_value={'content':[{'type':'text','text':'fixture private result'}]});client._send_request=request
    assert json.loads(client.call_tool('fixture',{}))=={'error':'mcp_access_denied'}
    assert request.called is (when=='after')


@pytest.mark.parametrize('content,expected',[([{'type':'text','text':'one'},None,{'type':'image'},{'type':'text','text':'two'}],'one\ntwo'),([],None)])
def test_valid_tool_result_text_is_ordered_without_assuming_every_block_is_object(content,expected):
    client=m.MCPClient('fixture','never-execute');client._send_request=Mock(return_value={'content':content})
    result=client.call_tool('fixture',{})
    assert result==expected if expected else json.loads(result)=={'content':[]}


def test_start_strips_inherited_proxy_case_variants_but_preserves_explicit_override(monkeypatch):
    for name in ['HTTP_PROXY','http_proxy','HTTPS_PROXY','https_proxy','ALL_PROXY','all_proxy']:
        monkeypatch.setenv(name,'fixture inherited proxy')
    monkeypatch.setenv('FIXTURE_INHERITED','keep')
    reply=b'{"jsonrpc":"2.0","id":1,"result":{}}\n{"jsonrpc":"2.0","id":2,"result":{"tools":[]}}\n'
    peer=process(reply);launch=Mock(return_value=peer);monkeypatch.setattr(m.subprocess,'Popen',launch)
    client=m.MCPClient('fixture','never-execute',extra_env={'http_proxy':'explicit fixture proxy'})
    client.start()
    env=launch.call_args.kwargs['env']
    assert {key:value for key,value in env.items() if 'PROXY' in key.upper()}=={'http_proxy':'explicit fixture proxy'}
    assert env['FIXTURE_INHERITED']=='keep'
    client.stop()


@pytest.mark.parametrize('entry',[None,{}, {'type':'stdio','command':''},{'type':'http','url':'http://fixture'}],ids=['not-object','missing-type','empty-command','unsupported-http'])
def test_invalid_reload_does_not_stop_previous_client(entry,monkeypatch):
    previous=Mock(name='old');previous.name='fixture';monkeypatch.setattr(m,'_MCP_CLIENTS',[previous])
    result=m.reload_mcp_server_from_registry('fixture',entry)
    assert result['status'] in {'error','unsupported'}
    previous.stop.assert_not_called()


@pytest.mark.parametrize('stop_fails',[False,True])
def test_failed_replacement_handshake_preserves_old_client_and_cleans_new(monkeypatch,stop_fails):
    previous=Mock();previous.name='fixture';monkeypatch.setattr(m,'_MCP_CLIENTS',[previous])
    replacement=Mock();replacement.start.side_effect=RuntimeError('fixture handshake')
    if stop_fails:replacement.stop.side_effect=OSError('fixture cleanup')
    monkeypatch.setattr(m,'MCPClient',Mock(return_value=replacement))
    result=m.reload_mcp_server_from_registry('fixture',{'type':'stdio','command':'never-execute'})
    assert result['status']=='error' and 'fixture handshake' in result['error']
    replacement.stop.assert_called_once();previous.stop.assert_not_called()
    assert m._MCP_CLIENTS==[previous]
