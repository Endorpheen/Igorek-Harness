import json
import urllib.error

import pytest

from test_desktop_docs_worker import setup
from factory.core import mcp_bridge
from factory.core.mcp_tasks.plugins import PluginSettings
from factory.core.tool_registry import registry

NAMES={'calendar_info','calendar_list_events','calendar_find_free_time','calendar_create_event','calendar_move_event'}


class Calendar:
    def __init__(self):self.writes=[];self.connected=True;self.etag='"one"';self.ids=set()
    def status(self):return {'connected':self.connected}
    def binding_digest(self):return 'fixture-binding'
    def calendar(self):return {'id':'fixture','time_zone':'Europe/Moscow','name':'Тестовый календарь'}
    def request(self,path,*,method='GET',data=None,headers=None):
        old={'id':'fixture-event','summary':'Тестовое событие','start':{'dateTime':'2026-10-08T09:00:00+03:00'},'end':{'dateTime':'2026-10-08T10:00:00+03:00'},'etag':self.etag}
        if method=='GET':return {'items':[old]} if '?' in path else old
        if method=='POST' and data['id'] in self.ids:raise urllib.error.HTTPError('https://www.googleapis.com/calendar/v3/',409,'Conflict',{},None)
        if method=='POST':self.ids.add(data['id'])
        self.writes.append({'method':method,'data':data,'headers':headers,'path':path})
        return {'id':data.get('id','fixture-event'),**data}


@pytest.fixture
def connected(setup):
    from factory.core.worker import _reload_enabled_mcp_servers_for_cc_backend
    owner,p,access=setup;connection=Calendar()
    PluginSettings(owner.root).update('google_calendar',enabled=True)
    access.save('google_calendar',p['credential_id'],'allow','fixture-password')
    mcp_bridge.set_desktop_access_check(lambda server:access.authorize(server,p['config']),access.registry,connection)
    assert _reload_enabled_mcp_servers_for_cc_backend('fixture-model')[0]['status']=='ok'
    yield owner,p,access,connection
    mcp_bridge.shutdown_mcp_servers()
    for name in NAMES:registry.deregister(name)


def test_enabled_connected_granted_only_tools_and_immediate_disconnect(connected):
    owner,p,access,connection=connected
    registry.dispatch('calendar_info',{},task_id='fixture-task',current_session='fixture-session')
    assert NAMES <= {t['name'] for t in registry.get_definitions('mcp')}
    assert 'calendar_info' not in {t['name'] for t in registry.get_definitions('file')}
    assert json.loads(registry.dispatch('calendar_create_event',{},task_id='fixture-task'))['error']=='calendar_approval_required'
    owner.plugins.settings.update('google_calendar',enabled=False)
    assert not NAMES & {t['name'] for t in registry.get_definitions('mcp')}
    assert json.loads(registry.dispatch('calendar_info',{}))['error']=='mcp_access_denied'
    owner.plugins.settings.update('google_calendar',enabled=True)
    access.save('google_calendar',p['credential_id'],'deny','fixture-password')
    assert not NAMES & {t['name'] for t in registry.get_definitions('mcp')}
    assert not connection.writes


@pytest.mark.parametrize('api_format',['openai','anthropic'])
@pytest.mark.parametrize('operation',['calendar_create_event','calendar_move_event'])
def test_main_loop_requires_one_shot_then_executes_approved_calendar_write(connected,tmp_path,monkeypatch,api_format,operation):
    from factory.core import cc_backend_executor as executor
    from factory.core.chat_session import _forced_tool_call_for_resume
    owner,p,access,connection=connected
    registry.dispatch('calendar_info',{},task_id='fixture-task',current_session='fixture-session')
    args={'start':'2026-10-08T10:00:00+03:00','end':'2026-10-08T11:00:00+03:00'}
    args.update({'summary':'Проверка'} if operation=='calendar_create_event' else {'event_id':'fixture-event'})
    if operation=='calendar_move_event':
        registry.dispatch('calendar_list_events',{'start':'2026-10-08T00:00:00+03:00','end':'2026-10-09T00:00:00+03:00'},task_id='fixture-task',current_session='fixture-session')
    final=[False];calls=[]
    def post(url,payload,headers,*unused,**kwargs):
        calls.append(payload)
        if api_format=='openai':
            answer={'role':'assistant','content':'Готово.'} if final[0] else {'role':'assistant','content':None,'tool_calls':[{'id':'calendar-write','type':'function','function':{'name':operation,'arguments':json.dumps(args)}}]}
            return {'choices':[{'message':answer,'finish_reason':'stop' if final[0] else 'tool_calls'}],'usage':{'prompt_tokens':1,'completion_tokens':1}},1
        content=[{'type':'text','text':'Готово.'}] if final[0] else [{'type':'tool_use','id':'calendar-write','name':operation,'input':args}]
        return {'content':content,'stop_reason':'end_turn' if final[0] else 'tool_use','usage':{'input_tokens':1,'output_tokens':1}},1
    monkeypatch.setattr(executor,'_http_post_streaming_openai',post);monkeypatch.setattr(executor,'_http_post',post)
    monkeypatch.setattr(executor,'_resolve_backend_context_window',lambda **kwargs:(32768,'fixture'))
    monkeypatch.setattr(executor,'is_interrupt_requested',lambda:False);monkeypatch.setattr(executor,'task_should_stop',lambda task_id:False)
    def run(metadata):
        return executor.execute_cc_backend_task(base_url='http://127.0.0.1:1/v1',model='fixture',system_prompt='fixture',user_message='Измени тестовое событие в Google Календаре.',
            workdir=str(tmp_path),artifact_workdir=str(tmp_path),api_format=api_format,task_id='fixture-task',current_session='fixture-session',source_platform='android',
            metadata=metadata,approvals_enabled_provider=lambda:False,enabled_tools=[t for t in registry.get_definitions('mcp') if t['name'] in NAMES],checkpoint_config={'enabled':False})
    blocked=run({'auto_approve':True,'approved_session_tools':[operation],'approved_tools':[operation]})
    assert blocked['status']=='waiting_approval' and not connection.writes
    tool=blocked['approval_blocked_tools'][0];assert '2026-10-08T10:00:00+03:00' in tool['target']
    assert 'Europe/Moscow' in tool['target']
    if operation=='calendar_move_event':assert 'Тестовое событие' in tool['target'] and 'Было:' in tool['target']
    final[0]=True
    metadata={'auto_approve':True,'approved_tool_calls':[tool['signature']],'forced_tool_call':_forced_tool_call_for_resume(tool,[])}
    result=run(metadata)
    assert result['status']=='ok',result
    assert len(connection.writes)==1
    write=connection.writes[0]
    assert 'sendUpdates=none' in write['path'] and not write['data'].get('attendees')
    if operation=='calendar_move_event':assert write['headers']=={'If-Match':'"one"'}
    else:
        assert len(write['data']['id'])==64
        run({'approved_tool_calls':[tool['signature']],'forced_tool_call':_forced_tool_call_for_resume(tool,[])})
        assert len(connection.writes)==1,'replaying an approved create must not create a second event'
    assert 'changed' in json.dumps(calls[-1])


def test_changed_event_cannot_be_moved_after_approval_for_old_version(connected):
    owner,p,access,connection=connected
    registry.dispatch('calendar_list_events',{'start':'2026-10-08T00:00:00+03:00','end':'2026-10-09T00:00:00+03:00'},task_id='task',current_session='session')
    connection.etag='"changed"'
    value=registry.dispatch('calendar_move_event',{'event_id':'fixture-event','start':'2026-10-08T12:00:00+03:00','end':'2026-10-08T13:00:00+03:00'},task_id='task',current_session='session',tool_use_id='call',mcp_write_approval=lambda *args:True)
    assert json.loads(value)['error']=='calendar_event_changed'
    assert not connection.writes


def test_calendar_confirmation_keeps_full_guest_and_description_preview():
    from factory.core.mobile_bridge.events import normalize_trace_event
    from factory.core.trace_taxonomy import CcTraceEvent
    target='Описание: '+'x'*3000+'\nПриглашения: friend@example.test'
    value=normalize_trace_event({'kind':CcTraceEvent.TOOL_APPROVAL_REQUIRED,'tool':'calendar_create_event','target':target},task_id='fixture')
    assert value['payload']['target']==target
    ordinary=normalize_trace_event({'kind':CcTraceEvent.TOOL_APPROVAL_REQUIRED,'tool':'Write','target':target},task_id='fixture')
    assert len(ordinary['payload']['target'])<=1001


def test_calendar_appears_after_connection_without_restarting_worker(setup):
    from factory.core.worker import _reload_enabled_mcp_servers_for_cc_backend
    owner,p,access=setup;connection=Calendar()
    mcp_bridge.set_desktop_access_check(lambda server:access.authorize(server,p['config']),access.registry,connection)
    assert 'google_calendar' not in access.registry()
    assert not NAMES & {t['name'] for t in registry.get_definitions('mcp')}
    owner.plugins.settings.update('google_calendar',enabled=True)
    access.save('google_calendar',p['credential_id'],'allow','fixture-password')
    assert _reload_enabled_mcp_servers_for_cc_backend('fixture-model')[0]['status']=='ok'
    assert NAMES <= {t['name'] for t in registry.get_definitions('mcp')}
    connection.connected=False
    assert not NAMES & {t['name'] for t in registry.get_definitions('mcp')}
    for name in NAMES:registry.deregister(name)
