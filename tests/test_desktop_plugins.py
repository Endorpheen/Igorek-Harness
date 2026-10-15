import json
import sys
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path

import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'desktop'))
from vault_owner import OwnerVault
from factory.core.mcp_tasks.access import McpAccess
from factory.core.mcp_tasks.calendar import CalendarClient, SCOPES, event_times
from factory.core.mcp_tasks.plugins import PluginSettings


@pytest.fixture
def owner(tmp_path):
    value=OwnerVault(tmp_path);value.service.create('fixture-password')
    yield value
    value.close()


class Docs:
    def __init__(self,check=lambda:True):self.check=check;self.tools=[{'name':'search_openai_docs','inputSchema':{'type':'object','properties':{'query':{'type':'string'}}}}]
    def start(self):assert self.check()
    def call_tool(self,*args):return json.dumps({'hits':[{'url':'https://developers.openai.com/api/docs/guides/streaming-responses','hierarchy':{'lvl1':'Streaming'}}]})
    def stop(self):pass


def provider(owner):
    value=owner.providers.save('fixture-model','fixture','https://example.test/v1','openai','fixture-model-key')
    owner.lifecycle.check_provider_profile(value['credential_id'],lambda *_:{'status':'verified'})
    return value


def test_docs_connect_check_disconnect_permissions_and_reopen(owner,tmp_path,monkeypatch):
    monkeypatch.setattr('factory.core.mcp_tasks.plugins.RemoteMcpClient',Docs)
    p=provider(owner);access=McpAccess(tmp_path,owner.lifecycle)
    assert 'openai_docs' not in access.registry()
    owner.plugins.action('openai_docs','check')
    assert not owner.plugins.status()['plugins']['openai_docs']['enabled']
    owner.plugins.action('openai_docs','connect')
    assert not access.authorize('openai_docs',p['config'],private=True)
    access.save('openai_docs',p['credential_id'],'allow','fixture-password')
    assert access.authorize('openai_docs',p['config'],private=True)
    owner.plugins.action('openai_docs','disconnect')
    assert not access.authorize('openai_docs',p['config'],private=True)
    owner.plugins.action('openai_docs','connect');owner.close()
    reopened=OwnerVault(tmp_path)
    try:
        reopened.service.unlock('fixture-password')
        state=reopened.plugins.status()['plugins']['openai_docs'];assert state['enabled'] and state['check']['ok']
        assert state['check']['links'][0]['url'].startswith('https://developers.openai.com/')
    finally:reopened.close()


def test_plugin_checks_never_claim_success_after_transport_failure(owner,monkeypatch):
    class Broken(Docs):
        def start(self):raise OSError('fixture-private-error')
    monkeypatch.setattr('factory.core.mcp_tasks.plugins.RemoteMcpClient',Broken)
    status=owner.plugins.action('openai_docs','connect')
    assert not status['plugins']['openai_docs']['enabled']
    assert status['plugins']['openai_docs']['check']['ok'] is False
    assert 'fixture-private-error' not in json.dumps(status)
    owner.service.lock()
    assert owner.dispatch({'id':'x','action':'plugin_action','plugin':'openai_docs','operation':'connect'})['error']=='vault_locked'
    assert owner.plugins.status()['plugins']['google_calendar']['state']=='vault_locked'


def test_calendar_oauth_real_loopback_pkce_state_encryption_and_reopen(owner,tmp_path,monkeypatch):
    calendar=owner.private_mcp.calendar
    source=tmp_path/'client.json';source.write_text(json.dumps({'installed':{'client_id':'fixture.apps.googleusercontent.com','client_secret':'fixture-client-secret'}}))
    calendar.configure(str(source),'fixture-password');captured=[]
    def token(url,*,data=None,**kwargs):
        captured.append(data)
        return {'scope':' '.join(sorted(SCOPES)),'refresh_token':'fixture-calendar-refresh','access_token':'fixture-calendar-access'}
    monkeypatch.setattr('factory.core.mcp_tasks.calendar.request_json',token)
    url=calendar.begin('fixture-password');query=urllib.parse.parse_qs(urllib.parse.urlsplit(url).query)
    assert set(query['scope'][0].split())==SCOPES and query['code_challenge_method']==['S256']
    callback=query['redirect_uri'][0]
    with pytest.raises(urllib.error.HTTPError):urllib.request.urlopen(callback+'?code=fixture-code&state=wrong',timeout=3)
    assert calendar.thread.is_alive() and not calendar.status()['connected']
    with urllib.request.urlopen(callback+'?'+urllib.parse.urlencode({'code':'fixture-code','state':query['state'][0]}),timeout=3) as response:assert response.status==200
    calendar.thread.join(3);assert calendar.status()['connected']
    import base64,hashlib
    verifier=captured[0]['code_verifier'];assert query['code_challenge'][0]==base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip('=')
    assert b'fixture-calendar-refresh' not in owner.lifecycle.db_path.read_bytes()
    assert 'fixture-calendar-refresh' not in json.dumps(owner.plugins.status())
    owner.close();reopened=OwnerVault(tmp_path)
    try:
        reopened.service.unlock('fixture-password');assert reopened.private_mcp.calendar.status()['connected']
        assert reopened.private_mcp.calendar._data()['refresh_token']=='fixture-calendar-refresh'
    finally:reopened.close()


class Calendar:
    def __init__(self):self.cancel=threading.Event();self.requests=[];self.busy=[];self.previous={'id':'actual-event','summary':'Fixture appointment','etag':'"revision-1"','start':{'dateTime':'2026-10-08T10:00:00+03:00'},'end':{'dateTime':'2026-10-08T11:00:00+03:00'}}
    def status(self):return {'connected':True}
    def calendar(self):return {'id':'owned@example.test','name':'Fixture calendar','time_zone':'Europe/Moscow'}
    def request(self,path,*,method='GET',data=None,headers=None):
        self.requests.append({'path':path,'method':method,'data':data,'headers':headers})
        if path=='freeBusy':return {'calendars':{'owned@example.test':{'busy':self.busy}}}
        if method in {'POST','PATCH'}:return {**self.previous,**data}
        if '/actual-event' in path:return self.previous
        return {'items':[self.previous]}


def test_calendar_list_and_free_time_use_explicit_range_and_calendar_timezone():
    connection=Calendar();client=CalendarClient(connection,lambda:True,lambda *_:pytest.fail('Read requested write approval'))
    info=json.loads(client.call_tool('calendar_info',{}));assert info['time_zone']=='Europe/Moscow' and info['now'].endswith('+03:00')
    bounds={'start':'2026-10-08T09:00:00+03:00','end':'2026-10-08T13:00:00+03:00'}
    listed=json.loads(client.call_tool('calendar_list_events',bounds));assert listed['events'][0]['id']=='actual-event'
    connection.busy=[{'start':'2026-10-08T10:00:00+03:00','end':'2026-10-08T11:00:00+03:00'}]
    free=json.loads(client.call_tool('calendar_find_free_time',bounds|{'duration_minutes':60}))
    assert [(v['start'][11:16],v['end'][11:16]) for v in free['slots']]==[('09:00','10:00'),('11:00','13:00')]
    assert connection.requests[-1]['data']['items']==[{'id':'owned@example.test'}]


@pytest.mark.parametrize('approved',[False,True])
def test_calendar_create_requires_confirmation_and_no_implicit_attendees(approved):
    connection=Calendar();previews=[];client=CalendarClient(connection,lambda:True,lambda value:previews.append(value) or approved)
    args={'summary':'Fixture','start':'2026-10-08T10:00:00+03:00','end':'2026-10-08T11:00:00+03:00'}
    result=json.loads(client.call_tool('calendar_create_event',args));writes=[r for r in connection.requests if r['method']=='POST']
    assert len(previews)==1 and len(writes)==int(approved)
    assert result.get('changed',False)==approved
    if writes:assert 'attendees' not in writes[0]['data'] and 'sendUpdates=none' in writes[0]['path']
    previews.clear();connection.requests.clear()
    result=json.loads(client.call_tool('calendar_create_event',args|{'attendees':['friend@example.test']}))
    assert result['error']=='invalid_calendar_arguments' and not previews and not connection.requests


def test_calendar_explicit_invitation_approval_and_move_etag():
    connection=Calendar();previews=[];client=CalendarClient(connection,lambda:True,lambda value:previews.append(value) or True)
    args={'summary':'Fixture','start':'2026-10-08','end':'2026-10-09','attendees':['friend@example.test'],'allow_invitations':True}
    assert json.loads(client.call_tool('calendar_create_event',args))['changed']
    assert previews[-1]['attendees']==['friend@example.test'] and 'sendUpdates=all' in connection.requests[-1]['path']
    assert connection.requests[-1]['data']['end']=={'date':'2026-10-09'}
    bounds={'start':'2026-10-08T13:00:00+03:00','end':'2026-10-08T14:00:00+03:00'}
    assert json.loads(client.call_tool('calendar_move_event',bounds|{'event_id':'actual-event'}))['error']=='invalid_calendar_arguments'
    client.call_tool('calendar_list_events',bounds)
    assert json.loads(client.call_tool('calendar_move_event',bounds|{'event_id':'actual-event'}))['changed']
    write=connection.requests[-1];assert write['method']=='PATCH' and write['headers']=={'If-Match':'"revision-1"'}
    assert set(write['data'])=={'start','end'} and 'sendUpdates=none' in write['path']


@pytest.mark.parametrize('changes',[{'start':'tomorrow'},{'start':'2026-10-08T10:00:00'},{'end':'2026-10-07T10:00:00+03:00'},{'unknown':'x'},{'attendees':['bad'] ,'allow_invitations':True}])
def test_calendar_refuses_ambiguous_or_invalid_write(changes):
    connection=Calendar();client=CalendarClient(connection,lambda:True,lambda *_:pytest.fail('Invalid write confirmation'))
    args={'summary':'Fixture','start':'2026-10-08T10:00:00+03:00','end':'2026-10-08T11:00:00+03:00'}
    assert json.loads(client.call_tool('calendar_create_event',args|changes))['error']=='invalid_calendar_arguments'
    assert not connection.requests


def test_calendar_revocation_after_confirmation_prevents_write():
    connection=Calendar();allowed=[True]
    def approve(_):allowed[0]=False;return True
    client=CalendarClient(connection,lambda:allowed[0],approve)
    with pytest.raises(PermissionError):client.call_tool('calendar_create_event',{'summary':'Fixture','start':'2026-10-08','end':'2026-10-09'})
    assert not connection.requests


@pytest.mark.parametrize('decision',['approve','reject'])
def test_calendar_uses_existing_model_loop_and_waits_for_owner(owner,tmp_path,monkeypatch,decision):
    profile=provider(owner);PluginSettings(tmp_path).update('google_calendar',enabled=True)
    McpAccess(tmp_path,owner.lifecycle).save('google_calendar',profile['credential_id'],'allow','fixture-password')
    runner=owner.private_mcp;connection=Calendar();runner.calendar=connection;calls=[]
    def model(url,payload,headers):
        calls.append(payload)
        if len(calls)==1:return {'choices':[{'message':{'content':None,'tool_calls':[{'id':'create-1','type':'function','function':{'name':'calendar_create_event','arguments':json.dumps({'summary':'Fixture','start':'2026-10-08','end':'2026-10-09'})}}]}}]}
        result=json.loads(payload['messages'][-1]['content']);assert result['changed']==(decision=='approve')
        return {'choices':[{'message':{'content':'Created' if result['changed'] else 'Declined'}}]}
    monkeypatch.setattr('factory.core.mcp_tasks.private.post_json',model)
    runner.start(profile['credential_id'],'google_calendar','Create fixture event')
    deadline=time.monotonic()+3
    while not runner.status().get('approval') and time.monotonic()<deadline:time.sleep(.01)
    approval=runner.status()['approval'];assert not connection.requests
    with pytest.raises(ValueError):runner.approve('wrong-id',decision)
    runner.approve(approval['request_id'],decision);runner.thread.join(3)
    assert runner.status()['error']=='' and len(calls)==2
    assert len([r for r in connection.requests if r['method']=='POST'])==int(decision=='approve')
    assert 'calendar_info first' in calls[0]['messages'][0]['content']


def test_calendar_disconnect_revoke_failure_disables_plugin_without_losing_token(owner,monkeypatch):
    connection=owner.private_mcp.calendar
    connection._store({'client_id':'fixture.apps.googleusercontent.com','client_secret':'fixture','refresh_token':'fixture-refresh','scope':' '.join(SCOPES)})
    owner.plugins.settings.update('google_calendar',enabled=True)
    def revoke(*args,**kwargs):raise OSError('private-error')
    monkeypatch.setattr('factory.core.mcp_tasks.calendar.request_json',revoke)
    reply=owner.dispatch({'id':'x','action':'calendar_disconnect','password':'fixture-password'})
    assert reply['error'] and not owner.plugins.settings.enabled('google_calendar') and connection.status()['connected']
    monkeypatch.setattr('factory.core.mcp_tasks.calendar.request_json',lambda *args,**kwargs:{})
    owner.dispatch({'id':'x','action':'calendar_disconnect','password':'fixture-password'})
    assert not connection.status()['connected']


def test_calendar_disconnect_wrong_password_does_not_disable_plugin(owner,monkeypatch):
    owner.plugins.settings.update('google_calendar',enabled=True)
    reply=owner.dispatch({'id':'x','action':'calendar_disconnect','password':'wrong'})
    assert reply['error']=='authentication_failed' and owner.plugins.settings.enabled('google_calendar')


def test_calendar_check_reads_events_and_selection_commits_only_after_success(owner,monkeypatch):
    connection=owner.private_mcp.calendar
    connection._store({'client_id':'fixture.apps.googleusercontent.com','client_secret':'fixture','refresh_token':'fixture-refresh','scope':' '.join(SCOPES)})
    requests=[]
    def request(path,**kwargs):
        requests.append(path)
        if path.startswith('users/me/calendarList?'):return {'items':[{'id':'test-calendar','summary':'Fixture test','accessRole':'owner','timeZone':'Europe/Moscow'}]}
        if path.startswith('users/me/calendarList/'):return {'id':'test-calendar','summary':'Fixture test','accessRole':'owner','timeZone':'Europe/Moscow'}
        return {'items':[]}
    monkeypatch.setattr(connection,'request',request)
    assert connection.select('test-calendar','fixture-password')['read_checked']
    assert connection._data()['calendar_id']=='test-calendar' and any('/events?' in p for p in requests)
    def failed(path,**kwargs):
        if path.startswith('users/me/calendarList?'):return {'items':[{'id':'other','accessRole':'owner'}]}
        raise OSError()
    monkeypatch.setattr(connection,'request',failed)
    with pytest.raises(OSError):connection.select('other','fixture-password')
    assert connection._data()['calendar_id']=='test-calendar'


def test_pending_calendar_write_stops_when_plugin_is_disabled(owner,tmp_path,monkeypatch):
    p=provider(owner);PluginSettings(tmp_path).update('google_calendar',enabled=True)
    McpAccess(tmp_path,owner.lifecycle).save('google_calendar',p['credential_id'],'allow','fixture-password')
    runner=owner.private_mcp;connection=Calendar();runner.calendar=connection
    monkeypatch.setattr('factory.core.mcp_tasks.private.post_json',lambda *args:{'choices':[{'message':{'content':None,'tool_calls':[{'id':'call-1','type':'function','function':{'name':'calendar_create_event','arguments':json.dumps({'summary':'Fixture','start':'2026-10-08','end':'2026-10-09'})}}]}}]})
    runner.start(p['credential_id'],'google_calendar','Fixture create')
    deadline=time.monotonic()+3
    while not runner.status().get('approval') and time.monotonic()<deadline:time.sleep(.01)
    assert runner.status().get('approval') and not connection.requests
    PluginSettings(tmp_path).update('google_calendar',enabled=False);runner.thread.join(3)
    assert not runner.thread.is_alive() and not connection.requests
    assert runner.status()['error']=='mcp_access_denied'


def test_calendar_oauth_wrong_scope_is_not_saved(owner,tmp_path,monkeypatch):
    calendar=owner.private_mcp.calendar;source=tmp_path/'client.json'
    source.write_text(json.dumps({'installed':{'client_id':'fixture.apps.googleusercontent.com','client_secret':'fixture'}}));calendar.configure(str(source),'fixture-password')
    monkeypatch.setattr('factory.core.mcp_tasks.calendar.request_json',lambda *args,**kwargs:{'scope':'https://www.googleapis.com/auth/gmail.readonly','refresh_token':'fixture-wrong-refresh','access_token':'fixture-access'})
    query=urllib.parse.parse_qs(urllib.parse.urlsplit(calendar.begin('fixture-password')).query)
    with urllib.request.urlopen(query['redirect_uri'][0]+'?'+urllib.parse.urlencode({'code':'fixture','state':query['state'][0]}),timeout=3) as reply:assert reply.status==200
    calendar.thread.join(3)
    assert not calendar.status()['connected'] and calendar.status()['error']=='calendar_permissions_missing'
    assert 'refresh_token' not in calendar._data()
