"""Owned OAuth callback/PKCE workflow with synthetic token replies and Vault data."""
import base64
import hashlib
import http.client
import json
import os
from types import SimpleNamespace
from urllib.parse import parse_qs, urlencode, urlsplit

import pytest

from factory.core.mcp_tasks import gmail
from factory.core.vault.lifecycle import VaultAuthenticationError
from test_desktop_gmail import vault as vault


@pytest.fixture
def connection(vault,monkeypatch):
    value=gmail.GmailConnection(vault)
    value._store({'client_id':'fixture.apps.googleusercontent.com','client_secret':'synthetic-client-secret'})
    calls=[]
    def request(url,**kwargs):
        assert url=='https://oauth2.googleapis.com/token','Unexpected external endpoint'
        calls.append(kwargs)
        return {'scope':gmail.SCOPE,'refresh_token':'synthetic-refresh','access_token':'synthetic-access'}
    monkeypatch.setattr(gmail,'request_json',request)
    yield value,calls
    value.cancel.set()
    if value.thread:value.thread.join(2)
    assert not value.thread or not value.thread.is_alive()


def begin(connection):
    value,_=connection
    url=value.begin('fixture-password')
    query=parse_qs(urlsplit(url).query)
    target=urlsplit(query['redirect_uri'][0])
    assert target.scheme=='http' and target.hostname=='127.0.0.1'
    assert query['scope']==[gmail.SCOPE] and query['code_challenge_method']==['S256']
    return query,target


def callback(target,query,**fields):
    client=http.client.HTTPConnection(target.hostname,target.port,timeout=3)
    try:
        client.request('GET',target.path+'?'+urlencode({'state':query['state'][0],**fields}))
        response=client.getresponse();data=response.read()
        assert response.headers['Cache-Control']=='no-store'
        return response.status,data
    finally:client.close()


def test_actual_local_callback_exchanges_matching_pkce_and_keeps_access_token_transient(connection,vault):
    value,calls=connection;query,target=begin(connection)
    status,_=callback(target,query,code='fixture-code');assert status==200
    value.thread.join(3);assert not value.thread.is_alive()
    assert value.status()=={'configured':True,'connected':True,'pending':False,'error':''}
    fields=calls[0]['data'];assert fields['code']=='fixture-code' and fields['redirect_uri']==query['redirect_uri'][0]
    challenge=base64.urlsafe_b64encode(hashlib.sha256(fields['code_verifier'].encode()).digest()).decode().rstrip('=')
    assert challenge==query['code_challenge'][0]
    assert 'synthetic-access' not in json.dumps(value._data())
    assert b'synthetic-refresh' not in vault.db_path.read_bytes()


def test_wrong_callback_state_cannot_exchange_code_but_later_valid_callback_can_succeed(connection):
    value,calls=connection;query,target=begin(connection)
    wrong={**query,'state':['not-the-request-state']}
    status,_=callback(target,wrong,code='wrong-code')
    assert status==400 and calls==[] and value.thread.is_alive()
    assert callback(target,query,code='fixture-code')[0]==200
    value.thread.join(3)
    assert value.status()['connected'] and len(calls)==1 and calls[0]['data']['code']=='fixture-code'


@pytest.mark.parametrize('token',[
    {'scope':'https://mail.google.com/','refresh_token':'synthetic','access_token':'synthetic'},
    {'scope':gmail.SCOPE,'access_token':'synthetic'},
    {'scope':gmail.SCOPE,'refresh_token':'synthetic'},
])
def test_incomplete_or_overbroad_token_reply_does_not_connect_or_replace_client_fields(connection,monkeypatch,token):
    value,_=connection;before=value._data()
    monkeypatch.setattr(gmail,'request_json',lambda *args,**kwargs:token)
    query,target=begin(connection);assert callback(target,query,code='fixture-code')[0]==200
    value.thread.join(3);status=value.status()
    assert not status['connected'] and not status['pending'] and status['error']
    assert value._data()==before


def test_cancelled_pending_callback_cannot_persist_a_refresh_token(connection):
    value,calls=connection;begin(connection)
    value.cancel.set();value.thread.join(2)
    assert not value.thread.is_alive() and not value.status()['connected'] and calls==[]


def test_existing_callback_thread_cannot_be_replaced_by_another_consent_flow(connection):
    value,calls=connection;begin(connection);current=value.thread
    with pytest.raises(ValueError):value.begin('fixture-password')
    assert value.thread is current and calls==[]


def test_client_configuration_authenticates_before_opening_any_file(connection,tmp_path):
    value,_=connection
    with pytest.raises(VaultAuthenticationError):value.configure(str(tmp_path/'missing.json'),'wrong-password')


def test_configuration_uses_portable_regular_file_access_without_a_unix_only_flag(connection,tmp_path,monkeypatch):
    value,_=connection;source=tmp_path/'client.json'
    source.write_text(json.dumps({'installed':{'client_id':'new.apps.googleusercontent.com','client_secret':'synthetic-new-client-secret'}}))
    native_os=SimpleNamespace(open=os.open,fdopen=os.fdopen,fstat=os.fstat,O_RDONLY=os.O_RDONLY,O_NONBLOCK=getattr(os,'O_NONBLOCK',0))
    monkeypatch.setattr(gmail,'os',native_os)
    status=value.configure(str(source),'fixture-password')
    assert status['configured'] and not status['connected'] and value._data()['client_id']=='new.apps.googleusercontent.com'


@pytest.mark.parametrize('payload',[{},[],{'installed':None},{'installed':{}},{'installed':{'client_id':'wrong','client_secret':'fixture'}},{'installed':{'client_id':'fixture.apps.googleusercontent.com','client_secret':''}}])
def test_invalid_client_config_does_not_replace_existing_encrypted_fields(connection,tmp_path,payload):
    value,_=connection;before=value._data();source=tmp_path/'client.json';source.write_text(json.dumps(payload))
    with pytest.raises(ValueError):value.configure(str(source),'fixture-password')
    assert value._data()==before


def test_relative_client_path_keeps_existing_owner_file_picker_semantics(connection,tmp_path,monkeypatch):
    value,_=connection;monkeypatch.chdir(tmp_path)
    source=tmp_path/'client.json';source.write_text(json.dumps({'installed':{'client_id':'relative.apps.googleusercontent.com','client_secret':'synthetic'}}))
    assert value.configure('client.json','fixture-password')['configured']
    assert value._data()['client_id']=='relative.apps.googleusercontent.com'


@pytest.mark.skipif(os.name!='posix',reason='POSIX symlink client-file descriptor checks')
def test_parent_directory_link_is_allowed_but_final_client_file_link_is_not_followed(connection,tmp_path):
    value,_=connection
    real=tmp_path/'real';real.mkdir();source=real/'client.json'
    source.write_text(json.dumps({'installed':{'client_id':'linked.apps.googleusercontent.com','client_secret':'synthetic'}}))
    parent=tmp_path/'parent-link';parent.symlink_to(real,target_is_directory=True)
    assert value.configure(str(parent/'client.json'),'fixture-password')['configured']
    before=value._data();final=tmp_path/'client-link.json';final.symlink_to(source)
    with pytest.raises((ValueError,OSError)):value.configure(str(final),'fixture-password')
    assert value._data()==before
