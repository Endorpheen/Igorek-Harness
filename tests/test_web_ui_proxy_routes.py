"""Real loopback HTTP routing and binary uploads against an owned echo bridge."""
import base64
import http.client
import json
import socket
import threading
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from types import SimpleNamespace
from urllib.parse import parse_qs,urlsplit

import pytest

from factory.web_ui import server as web

_WEBSOCKET_TEST_KEY = base64.b64encode(b"0123456789abcdef").decode("ascii")


class EchoBridge(BaseHTTPRequestHandler):
    protocol_version='HTTP/1.1'
    records=[]
    denied=False
    def log_message(self,*args):pass
    def handle_request(self):
        size=int(self.headers.get('Content-Length') or 0)
        data=self.rfile.read(size) if size else b''
        row={'method':self.command,'path':self.path,'raw':data,'headers':dict(self.headers)}
        self.records.append(row)
        status=401 if self.denied else 200
        payload={'echo_method':self.command,'echo_path':self.path,'current_device_id':'fixture-device'}
        if data and 'application/json' in self.headers.get('Content-Type',''):
            payload['echo_body']=json.loads(data)
        encoded=json.dumps(payload).encode()
        self.send_response(status);self.send_header('Content-Type','application/json');self.send_header('Content-Length',str(len(encoded)));self.end_headers();self.wfile.write(encoded)
    do_GET=handle_request
    do_POST=handle_request
    do_PATCH=handle_request
    do_DELETE=handle_request


@pytest.fixture(scope='module')
def servers():
    bridge=ThreadingHTTPServer(('127.0.0.1',0),EchoBridge)
    app=web._Server(0,f'http://127.0.0.1:{bridge.server_port}')
    threads=[threading.Thread(target=lambda server=server:server.serve_forever(poll_interval=.02),daemon=True) for server in [bridge,app]]
    for thread in threads:thread.start()
    def request(method,path,*,body=None,raw=None,headers=None,paired=True,truncate=False):
        request_headers={'Origin':f'https://127.0.0.1:{app.server_port}'}
        if paired:request_headers['Cookie']=web._COOKIE+'=fixture-token'
        if body is not None:
            raw=json.dumps(body).encode();request_headers['Content-Type']='application/json'
        request_headers.update(headers or {})
        connection=http.client.HTTPConnection('127.0.0.1',app.server_port,timeout=4)
        try:
            connection.request(method,path,body=raw,headers=request_headers)
            if truncate:connection.sock.shutdown(socket.SHUT_WR)
            response=connection.getresponse();data=response.read()
            return response.status,dict(response.headers),data
        finally:connection.close()
    yield SimpleNamespace(request=request,app=app,bridge=bridge)
    for server in [app,bridge]:server.shutdown();server.server_close()
    for thread in threads:thread.join(3);assert not thread.is_alive()


@pytest.fixture
def proxy(servers):
    EchoBridge.records=[];EchoBridge.denied=False
    yield servers
    EchoBridge.denied=False


ROUTES=[
    ('POST','/api/mcp','/mcp',{'enabled':True}),
    ('GET','/api/diagnostics','/diagnostics',None),
    ('GET','/api/operations/operation-123?wait=1','/operations/operation-123?wait=1',None),
    ('GET','/api/schedules','/schedules',None),
    ('POST','/api/schedules','/schedules',{'name':'fixture'}),
    ('PATCH','/api/schedules/schedule-123','/schedules/schedule-123',{'enabled':False}),
    ('DELETE','/api/schedules/schedule-123','/schedules/schedule-123',None),
    ('POST','/api/schedules/schedule-123/run','/schedules/schedule-123/run',{}),
    ('GET','/api/extensions?profile=fixture','/extensions?profile=fixture',None),
    ('POST','/api/devices/purge-revoked','/devices/purge-revoked',None),
    ('DELETE','/api/devices/device-123?purge=true','/devices/device-123?purge=1',None),
    ('DELETE','/api/devices/device-123','/devices/device-123',None),
    ('PATCH','/api/extensions/skills/skill-123','/extensions/skills/skill-123',{'enabled':True}),
    ('GET','/api/voice/config','/voice/config',None),
    ('POST','/api/voice/session','/voice/session',{'session':'fixture'}),
    ('POST','/api/voice/sdp','/voice/sdp',{'sdp':'fixture-data'}),
    ('POST','/api/voice/hangup','/voice/hangup',{}),
    ('POST','/api/tasks/task-123/user-input/request-123','/tasks/task-123/user-input/request-123',{'answers':{}}),
    ('POST','/api/sessions/session-123/profile','/sessions/session-123/profile',{'profile':'fixture'}),
    ('POST','/api/sessions/session-123/goal','/sessions/session-123/goal',{'action':'pause','goal_id':'fixture-goal'}),
    ('GET','/api/sessions/session-123/agents','/sessions/session-123/agents',None),
    ('GET','/api/sessions/session-123/sources','/sessions/session-123/sources',None),
    ('GET','/api/sessions/session-123/checkpoints/revision-123/changes','/sessions/session-123/checkpoints/revision-123/changes',None),
    ('PATCH','/api/sessions/session-123','/sessions/session-123',{'title':'Fixture title','pinned':True}),
    ('GET','/api/sessions/session-123','/sessions/session-123',None),
]


@pytest.mark.parametrize('method,path,target,body',ROUTES)
def test_route_forwards_exact_method_path_body_and_cookie_token(proxy,method,path,target,body):
    status,headers,data=proxy.request(method,path,body=body)
    assert status==200,data
    result=json.loads(data)
    assert result['echo_method']==method and result['echo_path']=='/mobile/v1'+target
    record=EchoBridge.records[-1]
    assert record['headers']['Authorization']=='Bearer fixture-token'
    if body is not None:assert result['echo_body']==body
    assert b'fixture-token' not in data
    assert headers['X-Content-Type-Options']=='nosniff'


@pytest.mark.parametrize('method,path,target,body',ROUTES)
def test_route_without_connection_never_contacts_bridge(proxy,method,path,target,body):
    status,headers,data=proxy.request(method,path,body=body,paired=False)
    assert status==401,data
    assert not EchoBridge.records


@pytest.mark.parametrize('path,body',[
    ('/api/extensions/skills/skill-123',{'enabled':'true'}),
    ('/api/extensions/skills/skill-123',{}),
    ('/api/sessions/session-123',{'pinned':1}),
    ('/api/sessions/session-123',{'title':17}),
    ('/api/sessions/session-123/profile',{'profile':'../private'}),
    ('/api/sessions/session-123/profile',{'profile':None}),
    ('/api/sessions/session-123/profile',{'profile':'fixture','extra':True}),
    ('/api/sessions/session-123/cancel',{'mode':'unknown'}),
])
def test_invalid_mutation_is_rejected_before_upstream(proxy,path,body):
    method='POST' if path.endswith(('/profile','/cancel')) else 'PATCH'
    status,headers,data=proxy.request(method,path,body=body)
    assert status==400,data
    assert not EchoBridge.records


@pytest.mark.parametrize('path',[
    '/api/schedules/bad%2Fid','/api/devices/bad%2Fid',
    '/api/sessions/bad%2Fid',
    '/api/sessions/session-123/checkpoints/bad%2Fid/changes',
    '/api/tasks/bad%2Fid/user-input/request-123',
    '/api/tasks/task-123/user-input/bad%2Fid',
    '/api/extensions/skills/bad%2Fid',
])
def test_invalid_encoded_identifiers_are_not_forwarded(proxy,path):
    method='DELETE' if '/devices/' in path or '/schedules/' in path else 'GET'
    if '/user-input/' in path:method='POST'
    if '/skills/' in path:method='PATCH'
    status,headers,data=proxy.request(method,path,body={} if method in {'POST','PATCH'} else None)
    assert status in {400,404},data
    assert not EchoBridge.records


@pytest.mark.parametrize('limit,expected',[('0',1),('99999',5000),('17',17)])
def test_session_limit_is_clamped_before_forwarding(proxy,limit,expected):
    status,headers,data=proxy.request('GET','/api/sessions?limit='+limit)
    assert status==200
    assert json.loads(data)['echo_path']=='/mobile/v1/sessions?limit='+str(expected)


def test_invalid_session_limit_does_not_reach_bridge(proxy):
    assert proxy.request('GET','/api/sessions?limit=bad')[0]==400
    assert not EchoBridge.records


def test_review_forwards_only_supported_query_keys(proxy):
    status,headers,data=proxy.request('GET','/api/sessions/session-123/changes/review?mode=branch&path=folder%2Ffile.py&extra=ignored')
    assert status==200
    query=parse_qs(urlsplit(json.loads(data)['echo_path']).query,keep_blank_values=True)
    assert query=={'mode':['branch'],'path':['folder/file.py'],'base':[''],'revision':[''],'include_untracked':['0']}


@pytest.mark.parametrize('length',['0','-1','not-an-integer',str(web._MAX_UPLOAD+1)])
def test_bad_upload_length_is_rejected_without_forwarding(proxy,length):
    status,headers,data=proxy.request('POST','/api/sessions/session-123/uploads',raw=b'',headers={'Content-Length':length})
    assert status==413,data
    assert not EchoBridge.records


@pytest.mark.parametrize('payload',[b'\x00\xff\xfe\x00',bytes(range(256))*600],ids=['binary','multiple-chunks'])
def test_binary_upload_preserves_bytes_and_normalizes_metadata(proxy,payload):
    status,headers,data=proxy.request('POST','/api/sessions/session-123/uploads',raw=payload,
        headers={'Content-Type':'application/octet-stream; fixture=1','X-File-Name':'a\x01b\x7fc.bin'})
    assert status==200,data
    record=EchoBridge.records[-1]
    assert record['raw']==payload and record['path']=='/mobile/v1/sessions/session-123/uploads'
    assert record['headers']['Content-Type']=='application/octet-stream'
    assert record['headers']['X-File-Name']=='abc.bin'


def test_upload_filename_is_bounded_and_empty_mime_gets_default(proxy):
    status,headers,data=proxy.request('POST','/api/sessions/session-123/uploads',raw=b'fixture',headers={'X-File-Name':'a'*1200,'Content-Type':''})
    assert status==200,data
    assert len(EchoBridge.records[-1]['headers']['X-File-Name'])==web._MAX_UPLOAD_HEADER
    assert EchoBridge.records[-1]['headers']['Content-Type']=='application/octet-stream'


def test_incomplete_upload_body_is_reported_instead_of_forwarding_partial_bytes(proxy):
    status,headers,data=proxy.request('POST','/api/sessions/session-123/uploads',raw=b'partial',headers={'Content-Length':'50'},truncate=True)
    assert status==400,data
    assert not EchoBridge.records


def test_revoked_upload_token_clears_browser_cookie(proxy):
    EchoBridge.denied=True
    status,headers,data=proxy.request('POST','/api/sessions/session-123/uploads',raw=b'fixture')
    assert status==401 and 'Max-Age=0' in headers['Set-Cookie']
    assert b'fixture-token' not in data


@pytest.mark.parametrize('headers,expected',[
    ({'Origin':''},403),
    ({'Sec-Fetch-Site':'cross-site'},403),
    ({'Upgrade':'wrong'},400),
    ({'Connection':'keep-alive'},400),
    ({'Sec-WebSocket-Version':'12'},400),
    ({'Sec-WebSocket-Key':'invalid-key'},400),
])
def test_terminal_handshake_rejections_happen_before_upgrade(proxy,headers,expected):
    base={'Upgrade':'websocket','Connection':'Upgrade','Sec-WebSocket-Version':'13','Sec-WebSocket-Key':_WEBSOCKET_TEST_KEY}
    base.update(headers)
    status,response_headers,data=proxy.request('GET','/api/terminal',headers=base)
    assert status==expected,data
    assert not EchoBridge.records


@pytest.mark.parametrize('payload,reason,close_code',[
    ({'kind':'open','seq':1,'payload':{'ticket':'f'*32,'rows':24,'cols':80}},'ticket_invalid',4401),
    ({'kind':'resize','seq':1,'payload':{'rows':24,'cols':80}},'open_expected',4400),
    ({'kind':'open','seq':1,'payload':{'ticket':'malformed','rows':24,'cols':80}},'ticket_invalid',4400),
])
def test_real_http_upgrade_and_masked_frame_refuse_unauthorized_terminal_open(proxy,payload,reason,close_code):
    import struct
    from factory.web_ui.websocket import OP_TEXT,OP_CLOSE,encode_client_frame
    from test_web_ui_websocket import read_server_frames
    client=socket.create_connection(('127.0.0.1',proxy.app.server_port),timeout=3)
    client.settimeout(3)
    try:
        request=(f'GET /api/terminal HTTP/1.1\r\nHost: 127.0.0.1:{proxy.app.server_port}\r\n'
            f'Origin: https://127.0.0.1:{proxy.app.server_port}\r\n'
            f'Cookie: {web._COOKIE}=fixture-token\r\n'
            'Upgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Version: 13\r\n'
            f'Sec-WebSocket-Key: {_WEBSOCKET_TEST_KEY}\r\n\r\n')
        client.sendall(request.encode());received=b''
        while b'\r\n\r\n' not in received:
            chunk=client.recv(65536);assert chunk;received+=chunk
        headers,frames=received.split(b'\r\n\r\n',1)
        assert headers.startswith(b'HTTP/1.1 101') or headers.startswith(b'HTTP/1.0 101')
        client.sendall(encode_client_frame(OP_TEXT,json.dumps(payload).encode(),mask=b'1234'))
        while True:
            chunk=client.recv(65536)
            if not chunk:break
            frames+=chunk
        decoded=read_server_frames(frames)
        errors=[json.loads(data) for fin,opcode,data in decoded if opcode==OP_TEXT]
        closes=[data for fin,opcode,data in decoded if opcode==OP_CLOSE]
        assert errors and errors[0]['payload']['reason']==reason
        assert closes and struct.unpack('>H',closes[0][:2])[0]==close_code
        assert not EchoBridge.records
    finally:client.close()


def test_session_options_forwards_only_a_valid_model_profile(proxy):
    status, _, body = proxy.request('GET', '/api/session-options?model_profile=codexpro')
    assert status == 200
    assert json.loads(body)['echo_path'] == '/mobile/v1/session-options?model_profile=codexpro'
    status, _, _ = proxy.request('GET', '/api/session-options?model_profile=%2Foutside')
    assert status == 400
    assert len(EchoBridge.records) == 1
