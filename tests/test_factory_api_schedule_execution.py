"""Scheduled gateway execution and delivery with no external messaging."""
import json
from types import SimpleNamespace
from unittest.mock import Mock
from urllib import request as urllib_request

import pytest

from factory import factory_api as api


@pytest.fixture
def scheduled(tmp_path,monkeypatch):
    for key in ['IGOREK_FACTORY_HOME','FACTORY_DATA_DIR']:monkeypatch.setenv(key,str(tmp_path))
    db=api.get_db_path();api.initialize_database(db);monkeypatch.setattr(api,'DB_PATH',db)
    from core.scheduler import TaskScheduler
    scheduler=TaskScheduler(db_path=db)
    identifier=scheduler.create_schedule(name='fixture schedule',agent_id='fixture',payload='fixture task',time_value='12:00',enabled=False)
    with api.open_db(db) as connection:connection.execute('UPDATE schedules SET last_output=? WHERE schedule_id=?',('previous output',identifier))
    runtime=SimpleNamespace(handle_message=Mock(return_value=SimpleNamespace(task={'output_text':'fixture output'},status='ok',task_id='fixture-task')))
    import core.gateway_runtime as gateway
    profiles=[]
    def create(profile):profiles.append(profile);return runtime
    monkeypatch.setattr(gateway,'GatewayRuntime',create)
    logger=Mock();monkeypatch.setattr(api,'LOGGER',logger)
    monkeypatch.setattr(urllib_request,'urlopen',lambda *a,**k:pytest.fail('Unexpected external messaging'))
    info={'schedule_id':identifier,'agent_id':'fixture','payload':'fixture task','name':'fixture schedule','metadata':{}}
    def stored():
        with api.open_db(db) as connection:return connection.execute('SELECT last_output FROM schedules WHERE schedule_id=?',(identifier,)).fetchone()[0]
    return SimpleNamespace(info=info,runtime=runtime,profiles=profiles,stored=stored,logger=logger)


@pytest.mark.parametrize('task,expected',[
    ({'output_text':'primary','result_text':'secondary'},'primary'),
    ({'result_text':'secondary'},'secondary'),
    ({'error_message':'fixture failure'},'fixture failure'),
    (None,''),({},''),(['not task metadata'],''),
])
def test_schedule_executes_through_gateway_and_persists_output(scheduled,task,expected):
    scheduled.runtime.handle_message.return_value=SimpleNamespace(task=task,status='ok',task_id='fixture-task')
    api._execute_schedule_via_gateway(scheduled.info)
    assert scheduled.profiles==['fixture'] and scheduled.stored()==expected
    kwargs=scheduled.runtime.handle_message.call_args.kwargs
    assert kwargs['profile']=='fixture' and kwargs['timeout_s']==600
    assert kwargs['source'].platform=='cron' and kwargs['source'].chat_id==scheduled.info['schedule_id']
    assert kwargs['message'].text=='fixture task'
    scheduled.logger.info.assert_called_once()


def test_schedule_stores_bounded_history_but_delivers_full_result(scheduled,monkeypatch):
    text='x'*15000;scheduled.runtime.handle_message.return_value.task={'output_text':text}
    scheduled.info['metadata']={'deliver':{'platform':'telegram','chat_id':'fixture-chat'}}
    deliver=Mock();monkeypatch.setattr(api,'_deliver_schedule_output',deliver)
    api._execute_schedule_via_gateway(scheduled.info)
    assert scheduled.stored()==text[:10000] and deliver.call_args.args[1]==text


@pytest.mark.parametrize('metadata',[None,'invalid',[],{'deliver':None},{'deliver':'invalid'}])
def test_schedule_without_valid_delivery_metadata_does_not_send(scheduled,monkeypatch,metadata):
    scheduled.info['metadata']=metadata;deliver=Mock();monkeypatch.setattr(api,'_deliver_schedule_output',deliver)
    api._execute_schedule_via_gateway(scheduled.info)
    assert scheduled.stored()=='fixture output' and not deliver.called


def test_schedule_failure_keeps_previous_output_and_records_cause(scheduled,monkeypatch):
    scheduled.runtime.handle_message.side_effect=RuntimeError('fixture runtime unavailable')
    deliver=Mock();monkeypatch.setattr(api,'_deliver_schedule_output',deliver)
    api._execute_schedule_via_gateway(scheduled.info)
    assert scheduled.stored()=='previous output' and not deliver.called
    scheduled.logger.error.assert_called_once()


@pytest.mark.parametrize('deliver,output',[
    ({},'fixture'),({'platform':'telegram'},'fixture'),({'platform':'telegram','chat_id':None},'fixture'),
    ({'platform':'telegram','chat_id':'  '},'fixture'),({'platform':'unknown','chat_id':'fixture'},'fixture'),
    ({'platform':'telegram','chat_id':'fixture'},''),
])
def test_missing_delivery_target_or_output_never_contacts_external_service(monkeypatch,deliver,output):
    monkeypatch.setenv('FACTORY_TELEGRAM_BOT_TOKEN','fixture-token')
    monkeypatch.setattr(urllib_request,'urlopen',lambda *a,**k:pytest.fail('Unexpected external messaging'))
    api._deliver_schedule_output(deliver,output,'fixture','schedule-fixture')


def test_missing_bot_credential_does_not_attempt_delivery(monkeypatch):
    monkeypatch.delenv('FACTORY_TELEGRAM_BOT_TOKEN',raising=False)
    monkeypatch.setattr(urllib_request,'urlopen',lambda *a,**k:pytest.fail('Unexpected external messaging'))
    api._deliver_schedule_output({'platform':'telegram','chat_id':'fixture'},'output','fixture','schedule')


@pytest.mark.parametrize('fallback',[False,True])
def test_delivery_uses_expected_payload_and_bounded_text(monkeypatch,fallback):
    monkeypatch.setenv('FACTORY_TELEGRAM_BOT_TOKEN','fixture-token');calls=[]
    if fallback:
        from factory.core import telegram_bridge
        monkeypatch.setattr(telegram_bridge,'_convert_markdown_to_telegram_html',lambda value:'')
    class Response:
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def read(self):return b'{"ok":true}'
    def deliver(request,**kwargs):calls.append((request,kwargs));return Response()
    monkeypatch.setattr(urllib_request,'urlopen',deliver)
    api._deliver_schedule_output({'platform':' TELEGRAM ','chat_id':' fixture '},'<unsafe> & '+ 'x'*5000,'fixture name','schedule')
    assert len(calls)==1
    request,kwargs=calls[0];payload=json.loads(request.data)
    assert request.get_method()=='POST' and kwargs['timeout']==30
    assert payload['chat_id']=='fixture' and payload['parse_mode']=='HTML' and payload['disable_web_page_preview'] is True
    assert len(payload['text'])<=4000 and '&lt;unsafe&gt;' in payload['text']


def test_delivery_transport_failure_is_reported_without_raising(monkeypatch,capsys):
    monkeypatch.setenv('FACTORY_TELEGRAM_BOT_TOKEN','fixture-token')
    def fail(*a,**k):raise OSError('fixture delivery unavailable')
    monkeypatch.setattr(urllib_request,'urlopen',fail)
    api._deliver_schedule_output({'platform':'telegram','chat_id':'fixture'},'fixture output','fixture','schedule')
    assert 'delivery failed' in capsys.readouterr().out
