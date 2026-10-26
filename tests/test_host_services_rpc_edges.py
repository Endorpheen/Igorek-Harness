"""Host-service transport and publication contracts with owned/fake endpoints."""
import io
import json
import os
from pathlib import Path
import socketserver
import tempfile
import threading
from http.server import BaseHTTPRequestHandler
from unittest.mock import Mock

import pytest

from factory.core import host_services as h
from factory.core.mobile_bridge import files


@pytest.fixture
def enrolled(tmp_path,monkeypatch):
    output=tmp_path/'outputs';output.mkdir()
    cfg={'name':'fixture','label':'Fixture service','socket':'owned-unused.sock','token':'fixture-token','outputs':str(output),'output_extensions':['.wav'],'tools':{'generate':{'input_schema':{'type':'object','properties':{'seconds':{'type':'integer','minimum':1}},'required':['seconds']}}}}
    manager=h.HostServices(tmp_path/'config',allowed=lambda profile=None:True,runner=Mock(side_effect=AssertionError('no systemctl')))
    monkeypatch.setattr(manager,'config',lambda _:cfg)
    return manager,cfg


@pytest.mark.parametrize('raw,status,expected',[(b'{"result":{"ok":true}}',200,{'ok':True}),(b'{}',200,{}),(b'[]',200,None),(b'{"error":{"code":-1}}',200,None),(b'{',200,None),(b'{}',503,None)])
def test_rpc_reads_only_object_success_and_always_closes_connection(enrolled,monkeypatch,raw,status,expected):
    manager,cfg=enrolled;response=io.BytesIO(raw);response.status=status
    connection=Mock();connection.getresponse.return_value=response
    monkeypatch.setattr(h,'UnixHTTPConnection',lambda _:connection)
    if expected is None:
        with pytest.raises(ValueError):manager.rpc(cfg,'ping')
    else:assert manager.rpc(cfg,'ping')==expected
    connection.close.assert_called_once()
    request=connection.request.call_args.args
    assert request[:2]==('POST','/mcp') and json.loads(request[2])['method']=='ping'
    assert request[3]['Authorization']=='Bearer fixture-token'


def test_rpc_response_budget_is_checked_before_json(enrolled,monkeypatch):
    manager,cfg=enrolled;response=io.BytesIO(b' '*(h.MAX_RPC_BYTES+1));response.status=200
    connection=Mock();connection.getresponse.return_value=response
    monkeypatch.setattr(h,'UnixHTTPConnection',lambda _:connection)
    with pytest.raises(ValueError,match='service_api_unavailable'):manager.rpc(cfg,'ping')
    connection.close.assert_called_once()


def test_oversized_rpc_request_never_opens_connection(enrolled,monkeypatch):
    manager,cfg=enrolled;connection=Mock(side_effect=AssertionError('unexpected connection'))
    monkeypatch.setattr(h,'UnixHTTPConnection',connection)
    with pytest.raises(ValueError,match='service_request_too_large'):manager.rpc(cfg,'ping',{'text':'x'*1024*1024})
    connection.assert_not_called()


def test_rpc_network_error_still_closes_connection(enrolled,monkeypatch):
    manager,cfg=enrolled;connection=Mock();connection.request.side_effect=OSError('fixture offline')
    monkeypatch.setattr(h,'UnixHTTPConnection',lambda _:connection)
    with pytest.raises(OSError,match='fixture offline'):manager.rpc(cfg,'ping')
    connection.close.assert_called_once()


@pytest.mark.parametrize('active,ping_error,ready',[(False,False,False),(True,False,True),(True,True,False)])
def test_status_requires_active_process_and_successful_ping(enrolled,monkeypatch,active,ping_error,ready):
    manager,cfg=enrolled
    monkeypatch.setattr(manager,'systemctl',lambda *args:{'ActiveState':'active' if active else 'inactive','SubState':'running' if active else 'dead','MainPID':'42'})
    ping=Mock(side_effect=OSError('fixture offline') if ping_error else None,return_value={})
    monkeypatch.setattr(manager,'rpc',ping)
    status=manager.status('fixture')
    assert status['ready'] is ready and status['pid']==42 and status['label']=='Fixture service'
    assert ping.called is active


@pytest.mark.parametrize('reply,expected',[({'isError':True,'content':[{'type':'text','text':'fixture failure'}]}, {'error':'service_call_failed','details':[{'type':'text','text':'fixture failure'}]}),({'content':[{'type':'text','text':'{"job":"fixture"}'}]}, {'job':'fixture'}),({'content':[{'type':'text','text':'plain fixture'}]}, {'content':[{'type':'text','text':'plain fixture'}]}),({'content':[{'type':'image','data':'fixture'},{'type':'text','text':'second'}]}, {'content':[{'type':'image','data':'fixture'},{'type':'text','text':'second'}]})])
def test_tool_result_formats_preserve_failure_and_content(enrolled,monkeypatch,reply,expected):
    manager,cfg=enrolled;rpc=Mock(return_value=reply);monkeypatch.setattr(manager,'rpc',rpc)
    result=manager.execute({'action':'invoke','service':'fixture','method':'generate','arguments':{'seconds':1}})
    assert result==expected
    rpc.assert_called_once_with(cfg,'tools/call',{'name':'generate','arguments':{'seconds':1}})
    manager.runner.assert_not_called()


@pytest.mark.parametrize('fields',[None,[],{'action':'list','service':'fixture'},{'action':'describe','service':'fixture','extra':1},{'action':'invoke','service':'fixture','method':'missing'},{'action':'export','service':'fixture','file_id':'bad'},{'action':'unsupported','service':'fixture'}])
def test_invalid_owner_fields_never_start_process(enrolled,fields):
    manager,_=enrolled
    with pytest.raises(ValueError):manager.execute(fields)
    manager.runner.assert_not_called()


@pytest.mark.skipif(os.name!='posix',reason='Owned Unix HTTP socket transport')
def test_owned_unix_http_roundtrip_does_not_use_host_network(enrolled):
    manager,cfg=enrolled;seen=[]
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def do_POST(self):
            seen.append((self.path,self.headers.get('Authorization'),json.loads(self.rfile.read(int(self.headers['Content-Length'])))))
            body=b'{"jsonrpc":"2.0","id":"igorek","result":{"job":"fixture"}}'
            self.send_response(200);self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
    with tempfile.TemporaryDirectory(prefix='ig-hs39-') as directory:
        path=Path(directory)/'api.sock';server=socketserver.UnixStreamServer(str(path),Handler);thread=threading.Thread(target=lambda:server.serve_forever(poll_interval=.01));thread.start()
        try:
            config={**cfg,'socket':str(path)}
            assert manager.rpc(config,'tools/call',{'name':'generate','arguments':{'seconds':1}})=={'job':'fixture'}
            assert seen==[('/mcp','Bearer fixture-token',{'jsonrpc':'2.0','id':'igorek','method':'tools/call','params':{'name':'generate','arguments':{'seconds':1}}})]
        finally:server.shutdown();server.server_close();thread.join(3);assert not thread.is_alive()


@pytest.mark.skipif(os.name!='posix',reason='POSIX descriptor-relative atomic file export')
@pytest.mark.parametrize('changed',[b'',b'longer'])
def test_changed_export_cannot_replace_previous_good_copy(enrolled,tmp_path,monkeypatch,changed):
    manager,cfg=enrolled;source=Path(cfg['outputs'])/'fixture.wav';source.write_bytes(b'x')
    info=files.describe_file(source);work=tmp_path/'work';work.mkdir()
    first=manager.export(cfg,info['id'],work)
    monkeypatch.setattr(files,'open_file_safely',lambda *args:io.BytesIO(changed))
    with pytest.raises(ValueError,match='service_result_changed'):manager.export(cfg,info['id'],work)
    target=Path(first['path']);assert target.read_bytes()==b'x'
    assert list(target.parent.iterdir())==[target]
