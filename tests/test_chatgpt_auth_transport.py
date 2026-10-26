"""OAuth transport bounds and real synthetic ECDSA identity verification."""
import base64
import io
import json
import threading
import urllib.error
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from unittest.mock import Mock

import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature

from factory.core import chatgpt_auth as auth


@pytest.mark.parametrize('raw,empty,valid',[(b'{}',False,True),(b'',True,True),(b'',False,False),(b'[]',False,False),(b'null',False,False),(b'\xff',False,False),(b'{',False,False)])
def test_transport_requires_bounded_json_object(monkeypatch,raw,empty,valid):
    opener=Mock();opener.open.return_value=io.BytesIO(raw)
    builder=Mock(return_value=opener);monkeypatch.setattr(auth.urllib.request,'build_opener',builder)
    if valid:assert auth.request('http://fixture',empty=empty)=={}
    else:
        with pytest.raises(auth.ChatGPTAuthError,match='chatgpt_invalid_response'):auth.request('http://fixture',empty=empty)
    builder.assert_called_once_with(auth.NoRedirect)
    assert opener.open.call_args.kwargs['timeout']==20


def test_transport_rejects_oversized_response_before_json_processing(monkeypatch):
    opener=Mock();opener.open.return_value=io.BytesIO(b' '*(2*1024*1024+1))
    monkeypatch.setattr(auth.urllib.request,'build_opener',lambda *_:opener)
    with pytest.raises(auth.ChatGPTAuthError,match='chatgpt_invalid_response'):auth.request('http://fixture')


def test_form_and_bearer_are_sent_only_to_explicit_fake_opener(monkeypatch):
    opener=Mock();opener.open.return_value=io.BytesIO(b'{"ok":true}')
    monkeypatch.setattr(auth.urllib.request,'build_opener',lambda *_:opener)
    assert auth.request('http://fixture',form={'query':'Привет & one'},token='fixture-bearer')=={'ok':True}
    request=opener.open.call_args.args[0]
    assert request.get_method()=='POST'
    assert request.get_header('Authorization')=='Bearer fixture-bearer'
    from urllib.parse import parse_qs
    assert parse_qs(request.data.decode())=={'query':['Привет & one']}


@pytest.mark.parametrize('status,body,code',[(400,b'{"error":"invalid_grant"}','chatgpt_login_required'),(401,b'{"error":{"code":"invalid_grant"}}','chatgpt_login_required'),(403,b'{"private":"fixture-secret"}','chatgpt_auth_failed'),(429,b'not-json','chatgpt_network'),(503,b'[]','chatgpt_network')])
def test_http_errors_have_stable_code_without_echoing_provider_body(monkeypatch,status,body,code):
    opener=Mock();opener.open.side_effect=urllib.error.HTTPError('http://fixture',status,'fixture-secret',{},io.BytesIO(body))
    monkeypatch.setattr(auth.urllib.request,'build_opener',lambda *_:opener)
    with pytest.raises(auth.ChatGPTAuthError) as error:auth.request('http://fixture',token='fixture-bearer')
    assert error.value.code==code and 'fixture-secret' not in str(error.value)


@pytest.mark.parametrize('error',[urllib.error.URLError('fixture host'),OSError('fixture socket'),TimeoutError('fixture timeout')])
def test_network_errors_do_not_expose_connection_details(monkeypatch,error):
    opener=Mock();opener.open.side_effect=error
    monkeypatch.setattr(auth.urllib.request,'build_opener',lambda *_:opener)
    with pytest.raises(auth.ChatGPTAuthError,match='chatgpt_network'):auth.request('http://fixture')


def test_redirect_never_reaches_target_with_bearer():
    records=[]
    class Target(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def do_GET(self):
            records.append(dict(self.headers));self.send_response(200);self.end_headers();self.wfile.write(b'{}')
    target=ThreadingHTTPServer(('127.0.0.1',0),Target)
    class Redirect(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def do_GET(self):
            self.send_response(302);self.send_header('Location',f'http://127.0.0.1:{target.server_port}/target');self.send_header('Content-Length','0');self.end_headers()
    source=ThreadingHTTPServer(('127.0.0.1',0),Redirect)
    threads=[threading.Thread(target=lambda server=s:server.serve_forever(poll_interval=.01)) for s in [source,target]]
    for thread in threads:thread.start()
    try:
        with pytest.raises(auth.ChatGPTAuthError,match='chatgpt_network'):auth.request(f'http://127.0.0.1:{source.server_port}/start',token='fixture-bearer')
        assert records==[]
    finally:
        for server in [source,target]:server.shutdown();server.server_close()
        for thread in threads:thread.join(3);assert not thread.is_alive()


def encode(value):
    raw=value if isinstance(value,bytes) else json.dumps(value).encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip('=')


@pytest.fixture
def ec_identity():
    private=ec.generate_private_key(ec.SECP256R1());public=private.public_key().public_numbers()
    key={'kty':'EC','kid':'fixture','alg':'ES256','use':'sig','crv':'P-256','x':encode(public.x.to_bytes(32)),'y':encode(public.y.to_bytes(32))}
    def sign(*,short=False,**changes):
        claims={'iss':auth.ISSUER,'aud':'fixture-client','sub':'fixture-subject','nonce':'fixture-nonce','exp':2000,**changes}
        signed=encode({'alg':'ES256','kid':'fixture'})+'.'+encode(claims)
        r,s=decode_dss_signature(private.sign(signed.encode(),ec.ECDSA(hashes.SHA256())))
        raw=r.to_bytes(32)+s.to_bytes(32)
        return signed+'.'+encode(raw[:-1] if short else raw)
    return sign,key


@pytest.mark.parametrize('case',['valid','multiple-aud','wrong-nbf','wrong-iat','short-signature','wrong-curve','duplicate-key'])
def test_real_ecdsa_signature_and_claim_bindings(ec_identity,case):
    sign,key=ec_identity;overrides={}
    if case=='multiple-aud':overrides={'aud':['other','fixture-client'],'azp':'fixture-client'}
    if case=='wrong-nbf':overrides={'nbf':1031}
    if case=='wrong-iat':overrides={'iat':1031}
    token=sign(short=case=='short-signature',**overrides)
    keys=[dict(key,crv='P-384')] if case=='wrong-curve' else [key,key] if case=='duplicate-key' else [key]
    if case in {'valid','multiple-aud'}:
        assert auth.validate_identity(token,'fixture-client','fixture-nonce',fetch=lambda _:{'keys':keys},now=1000)['sub']=='fixture-subject'
    else:
        with pytest.raises(auth.ChatGPTAuthError,match='chatgpt_identity_invalid'):auth.validate_identity(token,'fixture-client','fixture-nonce',fetch=lambda _:{'keys':keys},now=1000)
