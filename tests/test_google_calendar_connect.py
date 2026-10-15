import io
import json
import sys
import threading
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'desktop'))
from vault_owner import OwnerVault
from factory.core.mcp_tasks.calendar import SCOPES
from factory.core.mcp_tasks.gmail import SCOPE, valid_gmail_scopes
from factory.core.mcp_tasks.access import McpAccess


@pytest.fixture
def setup(tmp_path,monkeypatch):
    owner=OwnerVault(tmp_path);owner.service.create('fixture-password')
    p=owner.providers.save('fixture-model','fixture','https://example.test/v1','openai','fixture-key')
    owner.lifecycle.check_provider_profile(p['credential_id'],lambda *_:{'status':'verified'})
    gmail={'client_id':'fixture.apps.googleusercontent.com','client_secret':'fixture-client-secret','refresh_token':'fixture-gmail-refresh','scope':SCOPE}
    owner.gmail._store(gmail)
    calls=[]
    def token(url,*,data=None,**kwargs):
        calls.append((url,data))
        assert not url.endswith('/revoke')
        return {'scope':' '.join(sorted(SCOPES|{SCOPE})),'refresh_token':'fixture-calendar-refresh','access_token':'fixture-calendar-access'}
    def api(path,**kwargs):
        if path.startswith('users/me/calendarList/'):
            return {'id':'fixture-calendar','summary':'Тестовый календарь','timeZone':'Europe/Moscow','accessRole':'owner'}
        return {'items':[]}
    monkeypatch.setattr('factory.core.mcp_tasks.calendar.request_json',token)
    monkeypatch.setattr('factory.core.mcp_tasks.calendar.calendar_json',api)
    yield owner,p,gmail,calls
    owner.close()


def start(owner):
    reply=owner.dispatch({'id':'x','action':'calendar_connect','profile':'fixture-model'})
    assert 'error' not in reply,reply
    url=reply['result']['authorization_url']
    assert url.startswith('https://accounts.google.com/o/oauth2/v2/auth?')
    query=urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
    assert set(query['scope'][0].split())==SCOPES
    return query


def callback(owner,query,**values):
    with urllib.request.urlopen(query['redirect_uri'][0]+'?'+urllib.parse.urlencode({'state':query['state'][0],**values}),timeout=3) as response:
        assert response.status==200
    owner.private_mcp.calendar.thread.join(3)
    assert not owner.private_mcp.calendar.thread.is_alive()


def test_one_click_reuses_vault_client_auto_checks_grants_and_persists(setup,tmp_path):
    owner,p,gmail,calls=setup;query=start(owner)
    assert owner.private_mcp.calendar._data()=={k:gmail[k] for k in ['client_id','client_secret']}
    assert not owner.plugins.settings.enabled('google_calendar')
    assert not McpAccess(tmp_path,owner.lifecycle).authorize('google_calendar',p['config'],private=True)
    callback(owner,query,code='fixture-code')
    assert owner.plugins.settings.enabled('google_calendar')
    assert owner.plugins.status()['plugins']['google_calendar']['time_zone']=='Europe/Moscow'
    assert McpAccess(tmp_path,owner.lifecycle).authorize('google_calendar',p['config'],private=True)
    assert owner.gmail._data()==gmail
    assert calls[0][1]['client_id']==gmail['client_id'] and 'code_verifier' in calls[0][1]
    public=json.dumps(owner.plugins.status())
    for secret in ['fixture-client-secret','fixture-calendar-refresh','fixture-gmail-refresh']:
        assert secret not in public and secret.encode() not in owner.lifecycle.db_path.read_bytes()
    owner.close();reopened=OwnerVault(tmp_path)
    try:
        reopened.service.unlock('fixture-password')
        assert reopened.plugins.settings.enabled('google_calendar')
        assert reopened.private_mcp.calendar.status()['connected']
        assert McpAccess(tmp_path,reopened.lifecycle).authorize('google_calendar',p['config'],private=True)
        assert reopened.gmail._data()==gmail
    finally:reopened.close()


@pytest.mark.parametrize('condition',['denied','wrong_scope','disabled_api','profile_changed','interface_locked','cancelled'])
def test_failed_or_cancelled_consent_never_enables_or_grants(setup,tmp_path,monkeypatch,condition):
    owner,p,gmail,calls=setup;query=start(owner)
    if condition=='wrong_scope':
        monkeypatch.setattr('factory.core.mcp_tasks.calendar.request_json',lambda *a,**k:{'scope':SCOPE,'refresh_token':'fixture-calendar-refresh','access_token':'fixture'})
    if condition=='disabled_api':
        def disabled(*args,**kwargs):
            raise urllib.error.HTTPError('https://www.googleapis.com/calendar/v3/',403,'Forbidden',{},io.BytesIO(json.dumps({'error':{'details':[{'reason':'SERVICE_DISABLED','metadata':{'consumer':'projects/123456789','service':'calendar-json.googleapis.com'}}]}}).encode()))
        monkeypatch.setattr('factory.core.mcp_tasks.calendar.calendar_json',disabled)
    if condition=='profile_changed':owner.lifecycle.check_provider_profile(p['credential_id'],lambda *_:{'status':'failed'})
    if condition=='interface_locked':owner.interface_locked=True
    if condition=='cancelled':
        reply=owner.dispatch({'id':'x','action':'calendar_disable'});assert 'error' not in reply
    if condition=='cancelled':owner.private_mcp.calendar.thread.join(2)
    else:callback(owner,query,**({'error':'access_denied'} if condition=='denied' else {'code':'fixture-code'}))
    assert not owner.plugins.settings.enabled('google_calendar')
    assert not McpAccess(tmp_path,owner.lifecycle).grants().get('google_calendar')
    assert owner.gmail._data()==gmail
    if condition=='disabled_api':
        assert owner.private_mcp.calendar.status()['error']=='calendar_api_disabled'
        assert owner.private_mcp.calendar.status()['activation_url']=='https://console.cloud.google.com/apis/library/calendar-json.googleapis.com?project=123456789'
    if condition=='wrong_scope':assert owner.private_mcp.calendar.status()['error']=='calendar_permissions_missing'


def test_one_click_disconnect_does_not_revoke_shared_google_project(setup):
    owner,p,gmail,calls=setup;query=start(owner);callback(owner,query,code='fixture-code')
    assert 'error' not in owner.dispatch({'id':'x','action':'calendar_disable'})
    assert not owner.private_mcp.calendar.status()['connected']
    assert not owner.plugins.settings.enabled('google_calendar')
    assert owner.gmail._data()==gmail
    assert not any(url.endswith('/revoke') for url,_ in calls)
    query=start(owner);callback(owner,query,code='fixture-reconnect')
    owner.private_mcp.calendar.disconnect('fixture-password')
    assert owner.gmail._data()==gmail and not any(url.endswith('/revoke') for url,_ in calls)


def test_connect_is_closed_to_missing_google_unknown_model_and_locked_vault(setup):
    owner,p,gmail,calls=setup
    assert owner.dispatch({'id':'x','action':'calendar_connect','profile':'unknown'})['error']=='model_profile_required'
    owner.gmail._store({})
    assert owner.dispatch({'id':'x','action':'calendar_connect','profile':'fixture-model'})['error']=='google_not_configured'
    owner.service.lock()
    assert owner.dispatch({'id':'x','action':'calendar_connect','profile':'fixture-model'})['error']=='vault_locked'
    assert not calls


def test_gmail_accepts_shared_calendar_scopes_but_keeps_mail_scope_boundary(setup,monkeypatch):
    owner,p,gmail,calls=setup
    assert valid_gmail_scopes(' '.join(SCOPES|{SCOPE}))
    assert not valid_gmail_scopes(' '.join(SCOPES))
    assert not valid_gmail_scopes(SCOPE+' https://mail.google.com/')
    def request(url,**kwargs):
        if url.endswith('/token'):return {'access_token':'fixture-access','scope':' '.join(SCOPES|{SCOPE})}
        return {'messages':[]}
    monkeypatch.setattr('factory.core.mcp_tasks.gmail.request_json',request)
    assert owner.gmail.get('messages')=={'messages':[]}
    with pytest.raises(ValueError):owner.gmail.get('messages/a/trash')


def test_connect_preserves_existing_local_only_model_permission(setup,tmp_path):
    owner,p,gmail,calls=setup
    owner.plugins.settings.update('google_calendar',enabled=False)
    access=McpAccess(tmp_path,owner.lifecycle)
    access.save('google_calendar',p['credential_id'],'local_only','fixture-password')
    query=start(owner);callback(owner,query,code='fixture-code')
    assert access.grants()['google_calendar'][p['credential_id']]['access']=='local_only'
    assert not access.authorize('google_calendar',p['config'],private=True)


def test_full_vault_lock_during_token_exchange_cannot_commit_connection(setup,tmp_path,monkeypatch):
    owner,p,gmail,calls=setup;started=threading.Event();release=threading.Event()
    def token(*args,**kwargs):
        started.set();assert release.wait(3)
        return {'scope':' '.join(SCOPES),'refresh_token':'fixture-late-refresh','access_token':'fixture'}
    monkeypatch.setattr('factory.core.mcp_tasks.calendar.request_json',token)
    query=start(owner)
    with urllib.request.urlopen(query['redirect_uri'][0]+'?'+urllib.parse.urlencode({'code':'fixture','state':query['state'][0]}),timeout=3) as response:assert response.status==200
    assert started.wait(2)
    try:assert owner.dispatch({'id':'x','action':'lock'})['result']['state']=='locked'
    finally:release.set()
    owner.private_mcp.calendar.thread.join(3);owner.service.unlock('fixture-password')
    assert not owner.private_mcp.calendar.status()['connected']
    assert not owner.plugins.settings.enabled('google_calendar')
    assert not McpAccess(tmp_path,owner.lifecycle).grants().get('google_calendar')
    assert owner.gmail._data()==gmail
