"""Read-only Gmail boundaries and MIME limits; no account or real mail is contacted."""
import base64
import copy
import io
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.error import HTTPError
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from factory.core.mcp_tasks import gmail
from test_desktop_gmail import vault as vault, connection_data

REAL_BUILD_OPENER=gmail.urllib.request.build_opener


def encoded(text):return base64.urlsafe_b64encode(text.encode()).decode().rstrip('=')


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    call=Mock(side_effect=AssertionError('Unexpected external Gmail request'))
    monkeypatch.setattr(gmail.urllib.request,'build_opener',call)
    return call


@pytest.mark.parametrize('body',[b'[]',b'null',b'4',b'not JSON',b'x'*(1024*1024+2)],ids=['array','null','number','bad-json','oversize'])
def test_json_transport_refuses_invalid_or_oversize_payload_without_redirects(monkeypatch,body):
    response=io.BytesIO(body);open_=Mock(return_value=response);build=Mock(return_value=SimpleNamespace(open=open_))
    monkeypatch.setattr(gmail.urllib.request,'build_opener',build)
    with pytest.raises(ValueError):gmail.request_json('https://example.com/fixture',token='synthetic')
    handler=build.call_args.args[0]
    assert handler is gmail.NoRedirect or isinstance(handler,gmail.NoRedirect)
    assert gmail.NoRedirect().redirect_request(None,None,None,None,None,None) is None
    assert open_.call_args.kwargs=={'timeout':12}


def test_json_transport_preserves_utf8_query_values_and_explicit_auth(monkeypatch):
    open_=Mock(return_value=io.BytesIO(b'{"ok":true}'))
    monkeypatch.setattr(gmail.urllib.request,'build_opener',lambda *args:SimpleNamespace(open=open_))
    assert gmail.request_json('https://example.com/fixture',data={'query':'Привет 🐙'},token='synthetic')=={'ok':True}
    request=open_.call_args.args[0]
    assert request.get_header('Authorization')=='Bearer synthetic'
    assert gmail.urllib.parse.parse_qs(request.data.decode())=={'query':['Привет 🐙']}


@pytest.mark.parametrize('path',['../profile','messages/a/trash','https://example.com','profile','messages/'+'a'*129])
def test_connection_rejects_non_read_paths_before_token_refresh(vault,monkeypatch,path):
    connection=gmail.GmailConnection(vault);connection._store(connection_data())
    request=Mock(side_effect=AssertionError('Unexpected provider request'));monkeypatch.setattr(gmail,'request_json',request)
    with pytest.raises(ValueError):connection.get(path)
    assert not request.called


def test_scope_escalation_is_rejected_before_reading_mail(vault,monkeypatch):
    connection=gmail.GmailConnection(vault);connection._store(connection_data())
    request=Mock(return_value={'access_token':'synthetic-access','scope':gmail.SCOPE+' https://mail.google.com/'})
    monkeypatch.setattr(gmail,'request_json',request)
    with pytest.raises(ValueError):connection.get('messages')
    assert request.call_count==1 and request.call_args.args[0].endswith('/token')


def test_connection_refreshes_transient_access_token_without_exposing_it_in_status(vault,monkeypatch):
    connection=gmail.GmailConnection(vault);connection._store(connection_data())
    request=Mock(side_effect=[{'access_token':'synthetic-transient','scope':gmail.SCOPE},{'messages':[{'id':'abc'}]}])
    monkeypatch.setattr(gmail,'request_json',request)
    assert connection.get('messages',{'q':'Привет 🐙','maxResults':2})=={'messages':[{'id':'abc'}]}
    url=request.call_args.args[0]
    assert gmail.urllib.parse.parse_qs(gmail.urllib.parse.urlsplit(url).query)['q']==['Привет 🐙']
    assert request.call_args.kwargs['token']=='synthetic-transient'
    assert 'synthetic-transient' not in json.dumps(connection.status())


class Mail:
    def __init__(self,message=None):self.message=message or {};self.calls=[]
    def status(self):return {'connected':True}
    def get(self,path,params):self.calls.append((path,params));return copy.deepcopy(self.message)


def read(mail):return json.loads(gmail.GmailClient(mail,lambda name:True).call_tool('gmail_read',{'id':'abc123'}))


def test_read_retains_plaintext_and_allowed_headers_but_never_returns_html_or_attachments():
    message={'payload':{'mimeType':'multipart/mixed','headers':[None,{'name':'Subject','value':'s'*600},{'name':'FROM','value':'fixture@example.invalid'},{'name':'X-Private-Fixture','value':'omit'}],'parts':[{'mimeType':'text/html','body':{'data':encoded('<script>ignore</script>')}},{'mimeType':'text/plain','body':{'data':encoded('Привет 🐙')}},{'mimeType':'text/plain','filename':'attachment.txt','body':{'data':encoded('attachment bytes')}}]},'snippet':'n'*3000}
    original=copy.deepcopy(message);result=read(Mail(message))
    assert result['text']=='Привет 🐙' and len(result['snippet'])==2000
    assert result['headers']=={'subject':'s'*500,'from':'fixture@example.invalid'}
    assert 'attachment bytes' not in json.dumps(result) and '<script>' not in json.dumps(result)
    assert message==original


@pytest.mark.parametrize('case',['deep','oversize','many-parts'])
def test_mime_recursion_count_and_encoded_body_limits_are_enforced(case):
    leaf={'mimeType':'text/plain','body':{'data':encoded('fixture body')}}
    if case=='deep':
        payload=leaf
        for _ in range(10):payload={'mimeType':'multipart/mixed','parts':[payload]}
        expected=''
    elif case=='oversize':
        payload={'mimeType':'text/plain','body':{'data':'eA'*40000}};expected=''
    else:
        payload={'mimeType':'multipart/mixed','parts':[leaf]*35};expected='\n'.join(['fixture body']*30)
    assert read(Mail({'payload':payload}))['text']==expected


@pytest.mark.parametrize('tool,args',[
    ('gmail_send',{}),('gmail_read',{'id':'../private'}),('gmail_read',{'id':''}),('gmail_read',{'id':'a','extra':1}),
    ('gmail_search',{'query':'x'*501}),('gmail_search',{'query':'fixture','extra':1}),
    ('gmail_latest',{'count':False}),('gmail_latest',{'count':11}),('gmail_latest',{'count':0}),
])
def test_tool_schema_limits_refuse_invalid_calls_without_fetching_mail(tool,args):
    mail=Mail();client=gmail.GmailClient(mail,lambda name:True)
    with pytest.raises(ValueError):client.call_tool(tool,args)
    assert mail.calls==[]


def test_access_revocation_during_read_suppresses_result_and_unsupported_start_is_denied():
    mail=Mail({'payload':{'mimeType':'text/plain','body':{'data':encoded('fixture')}}})
    allowed=iter([True,False]);client=gmail.GmailClient(mail,lambda name:next(allowed))
    with pytest.raises(PermissionError):client.call_tool('gmail_read',{'id':'abc'})
    assert len(mail.calls)==1
    with pytest.raises(PermissionError):gmail.GmailClient(mail,lambda name:False).start()
    mail.status=lambda:{'connected':False}
    with pytest.raises(PermissionError):gmail.GmailClient(mail,lambda name:True).start()


def test_real_owned_redirect_is_not_followed_with_a_bearer_header(monkeypatch):
    received=[]
    class Target(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def do_GET(self):
            received.append(self.headers.get('Authorization'))
            self.send_response(200);self.end_headers();self.wfile.write(b'{}')
    target=ThreadingHTTPServer(('127.0.0.1',0),Target)
    class Redirect(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def do_GET(self):
            self.send_response(302);self.send_header('Location',f'http://127.0.0.1:{target.server_port}/fixture');self.end_headers()
    redirect=ThreadingHTTPServer(('127.0.0.1',0),Redirect)
    servers=[target,redirect];threads=[threading.Thread(target=lambda server=server:server.serve_forever(poll_interval=.01),daemon=True) for server in servers]
    for thread in threads:thread.start()
    monkeypatch.setattr(gmail.urllib.request,'build_opener',REAL_BUILD_OPENER)
    try:
        with pytest.raises(HTTPError) as exc:
            gmail.request_json(f'http://127.0.0.1:{redirect.server_port}/fixture',token='synthetic-fixture')
        assert exc.value.code==302 and received==[]
    finally:
        for server in servers:server.shutdown();server.server_close()
        for thread in threads:thread.join(2);assert not thread.is_alive()
