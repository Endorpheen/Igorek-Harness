"""Typed guest-message failures and accept-loop cleanup, without native VSOCK."""
import json
import struct
import threading
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from factory.core.isolation import channel as c

RID='12345678-1234-1234-1234-123456789abc'


@pytest.fixture
def endpoint():
    sock=Mock()
    return c.PeerSession(sock,c.ChannelConfig()),sock


@pytest.mark.parametrize('operation,args,reason',[
    ('search',{},'web_request_invalid'),('search',{'query':''},'web_request_invalid'),
    ('search',{'query':False},'web_request_invalid'),('search',{'query':'x'*4097},'web_request_invalid'),
    ('search',{'query':'ok','limit':True},'web_request_invalid'),('search',{'query':'ok','limit':0},'web_request_invalid'),
    ('search',{'query':'ok','limit':11},'web_request_invalid'),('search',{'query':'ok','command':'never'},'web_request_invalid'),
    ('extract',{},'web_request_invalid'),('extract',{'urls':[]},'web_request_invalid'),
    ('extract',{'urls':[1]},'web_request_invalid'),('extract',{'urls':['x'*2049]},'web_request_invalid'),
    ('extract',{'urls':['https://example.test']*11},'web_request_invalid'),('unsupported',{},'web_request_invalid'),
],ids=['missing-query','empty-query','boolean-query','long-query','boolean-limit','zero-limit','large-limit','extra-command','missing-urls','empty-urls','typed-url','long-url','many-urls','unsupported'])
def test_invalid_web_request_never_writes_frame(endpoint,operation,args,reason):
    peer,sock=endpoint
    with pytest.raises(c.ChannelError) as error:peer.send(c.MessageKind.WEB_REQUEST,{'request_id':RID,'operation':operation,'args':args})
    assert error.value.reason==reason and not error.value.fatal
    sock.sendall.assert_not_called()
    peer.send_error('fixture rejected')
    packet=sock.sendall.call_args.args[0]
    assert struct.unpack('!I',packet[:4])[0]==len(packet[4:])
    assert json.loads(packet[4:])['seq']==1


@pytest.mark.parametrize('args',[
    {'query':'x'*4097},{'query':False},{'limit':True},{'limit':0},{'limit':6},
    {'role_filter':True},{'role_filter':'x'*65},{'mode':'unknown'},{'mode':[]},{'mode':{}},
    {'command':'never-execute'},
],ids=['long-query','typed-query','boolean-limit','zero-limit','large-limit','typed-role','long-role','unknown-mode','list-mode','object-mode','extra-field'])
def test_invalid_session_search_is_always_typed_nonfatal(endpoint,args):
    peer,sock=endpoint
    with pytest.raises(c.ChannelError) as error:peer.send(c.MessageKind.SESSION_SEARCH_REQUEST,{'request_id':RID,'args':args})
    assert error.value.reason=='session_search_request_invalid' and not error.value.fatal
    sock.sendall.assert_not_called()


@pytest.mark.parametrize('kind,payload',[
    (c.MessageKind.WEB_REQUEST,{'request_id':'bad','operation':'search','args':{'query':'ok'}}),
    (c.MessageKind.WEB_REQUEST,{'request_id':RID,'operation':'search','args':{'query':'ok'},'extra':True}),
    (c.MessageKind.WEB_REPLY,{'request_id':RID,'result':4}),
    (c.MessageKind.SESSION_SEARCH_REQUEST,{'request_id':RID,'args':[]} ),
],ids=['request-id','extra-field','reply-type','search-args'])
def test_bad_envelope_is_rejected_without_transport_write(endpoint,kind,payload):
    peer,sock=endpoint
    with pytest.raises(c.ChannelError):peer.send(kind,payload)
    sock.sendall.assert_not_called()


@pytest.mark.parametrize('operation,args',[('search',{'query':'fixture','limit':10}),('extract',{'urls':['https://example.test']} )])
def test_valid_web_request_roundtrips_without_external_execution(endpoint,operation,args):
    peer,sock=endpoint;payload={'request_id':RID,'operation':operation,'args':args}
    peer.send(c.MessageKind.WEB_REQUEST,payload)
    wire=sock.sendall.call_args.args[0]
    message=c.parse_message(json.loads(wire[4:]),c.ChannelConfig())
    assert message.kind==c.MessageKind.WEB_REQUEST and message.payload==payload


@pytest.mark.parametrize('too_large',[False,True])
def test_web_reply_limit_counts_utf8_bytes(endpoint,too_large):
    peer,sock=endpoint
    text='я'*(256*1024+int(too_large))
    if too_large:
        with pytest.raises(c.ChannelError,match='web_reply_invalid'):peer.send(c.MessageKind.WEB_REPLY,{'request_id':RID,'result':text})
        sock.sendall.assert_not_called()
    else:
        peer.send(c.MessageKind.WEB_REPLY,{'request_id':RID,'result':text})
        assert json.loads(sock.sendall.call_args.args[0][4:])['payload']['result']==text


@pytest.mark.parametrize('scenario',['normal','invalid-peer','peer-error','saturated','timeout-first','accept-error','closed-error'])
def test_accept_loop_closes_listener_and_rejects_unusable_peer(monkeypatch,scenario):
    server=c.ChannelServer(c.ChannelConfig(),c.SessionMap());stop=threading.Event();listener=Mock();peer=Mock();started=[];calls=[]
    peer.getpeername.return_value=(3,5005)
    if scenario=='invalid-peer':peer.getpeername.return_value='not-a-kernel-cid'
    if scenario=='peer-error':peer.getpeername.side_effect=OSError('fixture peer gone')
    if scenario=='saturated':server._handshake_slots=Mock(acquire=Mock(return_value=False))
    def accept():
        calls.append(True)
        if scenario in {'accept-error','closed-error'}:
            if scenario=='closed-error':server._closed.set()
            raise OSError('fixture accept')
        if scenario=='timeout-first' and len(calls)==1:raise TimeoutError
        if len(calls)>(2 if scenario=='timeout-first' else 1):stop.set();raise TimeoutError
        return peer,None
    listener.accept.side_effect=accept
    opener=Mock(return_value=listener);monkeypatch.setattr(c,'open_vsock_listener',opener)
    def thread(**kwargs):started.append(kwargs);return Mock()
    monkeypatch.setattr(c,'threading',SimpleNamespace(Thread=thread))
    if scenario=='accept-error':
        with pytest.raises(c.ChannelError,match='vsock_accept_failed'):server.serve_forever(stop)
    else:server.serve_forever(stop)
    opener.assert_called_once_with(server._config.port)
    listener.close.assert_called_once()
    assert server._listener is None
    if scenario in {'normal','timeout-first'}:
        assert len(started)==1 and started[0]['args']==(peer,c.PeerCid(3))
    else:assert started==[]
    if scenario in {'invalid-peer','peer-error','saturated'}:peer.close.assert_called_once()
