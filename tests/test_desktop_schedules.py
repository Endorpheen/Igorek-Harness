import json
import os
import sqlite3
import time
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

from factory.core.desktop_schedules import DesktopSchedules
from factory.core.mobile_bridge.service import MobileInvalid, MobileNotFound


class QueueService:
    def __init__(self, workdir):
        self.workdir = str(workdir); self.profile = 'demo'; self.active = ['demo']
        self.available_profiles = lambda: list(self.active)
        self.sessions = {}; self.tasks = {}; self.session_keys = {}; self.task_keys = {}

    def _require_available_profile(self, name):
        if name not in self.active: raise MobileInvalid('profile unavailable')

    def create_session(self, *, profile, cwd, idempotency_key):
        self._require_available_profile(profile)
        if idempotency_key in self.session_keys:return self.sessions[self.session_keys[idempotency_key]]
        session_id='chat-'+str(len(self.sessions)+1)
        self.sessions[session_id]={'session_id':session_id,'profile':profile,'cwd':cwd}
        self.session_keys[idempotency_key]=session_id
        return self.sessions[session_id]

    def update_session(self, session_id, title):self.sessions[session_id]['title']=title

    def send_message(self, session_id, text, *, idempotency_key):
        if idempotency_key in self.task_keys:return self.tasks[self.task_keys[idempotency_key]]
        task_id='task-'+str(len(self.tasks)+1)
        self.tasks[task_id]={'task_id':task_id,'session_id':session_id,'text':text,'status':'queued'}
        self.task_keys[idempotency_key]=task_id
        return self.tasks[task_id]

    def get_task(self, task_id):
        if task_id not in self.tasks:raise MobileNotFound('task missing')
        return self.tasks[task_id]


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setenv('IGOREK_FACTORY_HOME',str(tmp_path));monkeypatch.setenv('FACTORY_DATA_DIR',str(tmp_path))
    from factory.core.scheduler import TaskScheduler
    from factory.core.schema import initialize_database
    database=tmp_path/'factory.db';initialize_database(database)
    service=QueueService(tmp_path)
    scheduler=TaskScheduler(str(database))
    controller=DesktopSchedules(service,scheduler)
    fields={'name':'Проверить проект','payload':'Скажи, что задание выполнено.','profile':'demo','cwd':str(tmp_path),
            'schedule_type':'daily','time_value':'09:00','timezone':'Europe/Moscow'}
    yield service,scheduler,controller,fields
    if scheduler.running:scheduler.stop()


def due(scheduler, identity):
    with sqlite3.connect(scheduler.db_path) as conn:conn.execute('UPDATE schedules SET next_run_at=? WHERE schedule_id=?',(int(time.time())-10,identity))


def test_crud_and_manual_launch_use_existing_queue_profile_and_project(setup):
    service,scheduler,controller,fields=setup
    identity=controller.save(fields)['schedule_id']
    row=controller.list()['schedules'][0]
    assert row['profile']=='demo' and row['cwd']==fields['cwd'] and row['ready']
    assert row['next_run_at']>time.time()
    controller.save(fields|{'name':'Новое название'},identity)
    launched=controller.action(identity,'run',{})['schedules'][0]
    assert launched['run_count']==1 and launched['last_status']=='queued'
    assert service.sessions[launched['last_session_id']]['title']=='Новое название'
    assert service.sessions[launched['last_session_id']]['cwd']==fields['cwd']
    assert service.tasks[launched['last_task_id']]['text']==fields['payload']
    assert not launched['ready']
    with pytest.raises(MobileInvalid):controller.action(identity,'run',{})
    controller.action(identity,'enabled',{'enabled':False})
    assert not scheduler.get_schedule(identity).enabled
    controller.action(identity,'delete')
    assert controller.list()['schedules']==[] and len(service.tasks)==1


def test_due_schedule_waits_for_worker_without_consuming_occurrence(setup):
    service,scheduler,controller,fields=setup
    identity=controller.save(fields)['schedule_id'];due(scheduler,identity)
    before=scheduler.get_schedule(identity).next_run_at
    service.active=[];scheduler._check_and_run_schedules()
    assert scheduler.get_schedule(identity).run_count==0 and scheduler.get_schedule(identity).next_run_at==before
    assert service.tasks=={}
    with pytest.raises(MobileInvalid):controller.action(identity,'run',{})
    service.active=['demo'];scheduler._check_and_run_schedules()
    assert scheduler.get_schedule(identity).run_count==1 and len(service.tasks)==1
    assert scheduler.get_schedule(identity).next_run_at>time.time()


def test_retry_after_receipt_failure_does_not_duplicate_session_or_task(setup,monkeypatch):
    service,scheduler,controller,fields=setup
    identity=controller.save(fields)['schedule_id'];due(scheduler,identity)
    original=scheduler.update_schedule;failed=[False]
    def update(*args,**kwargs):
        if 'metadata' in kwargs and not failed[0]:failed[0]=True;raise RuntimeError('fixture receipt failure')
        return original(*args,**kwargs)
    monkeypatch.setattr(scheduler,'update_schedule',update)
    scheduler._check_and_run_schedules()
    assert scheduler.get_schedule(identity).run_count==0 and len(service.tasks)==1
    scheduler._check_and_run_schedules()
    assert scheduler.get_schedule(identity).run_count==1 and len(service.sessions)==len(service.tasks)==1


def test_manual_one_shot_is_not_executed_again_at_original_deadline(setup):
    service,scheduler,controller,fields=setup
    identity=scheduler.create_schedule(name=fields['name'],agent_id='demo',payload=fields['payload'],schedule_type='once',time_value='23:59',tz='UTC',metadata={'cwd':fields['cwd']})
    controller.action(identity,'run',{})
    assert scheduler.get_schedule(identity).next_run_at is None
    service.tasks['task-1']['status']='ok';scheduler._check_and_run_schedules()
    assert len(service.tasks)==1


@pytest.mark.parametrize('changes',[{'name':''},{'payload':''},{'time_value':'25:00'},{'time_value':'9:00'},{'timezone':'Unknown/Zone'},
    {'cwd':'relative'},{'profile':'missing'},{'schedule_type':'cron'},{'enabled':True},{'metadata':{'auto_approve':True}}])
def test_invalid_schedule_fields_do_not_create_records(setup,changes):
    service,scheduler,controller,fields=setup
    with pytest.raises(MobileInvalid):controller.save(fields|changes)
    assert scheduler.list_schedules()==[] and service.tasks=={}


def test_past_one_shot_and_invalid_toggle_are_refused(setup):
    service,scheduler,controller,fields=setup
    with pytest.raises(MobileInvalid):controller.save(fields|{'schedule_type':'once','time_value':'00:00','timezone':'UTC'})
    identity=controller.save(fields)['schedule_id']
    for body in ({'enabled':'false'},{'enabled':True,'extra':True},{}):
        with pytest.raises(MobileInvalid):controller.action(identity,'enabled',body)
    assert scheduler.get_schedule(identity).enabled


def test_scheduler_without_desktop_readiness_keeps_legacy_callback_contract(setup):
    service,scheduler,controller,fields=setup
    calls=[];scheduler.set_readiness_check(None);scheduler.set_task_callback(calls.append)
    identity=controller.save(fields)['schedule_id'];due(scheduler,identity)
    scheduler._check_and_run_schedules()
    assert len(calls)==1 and calls[0]['agent_id']=='demo' and scheduler.get_schedule(identity).run_count==1


def test_agent_created_schedule_is_visible_to_same_desktop_controller(setup,monkeypatch):
    service,scheduler,controller,fields=setup
    import factory.core.scheduler as module
    monkeypatch.setattr(module,'_scheduler',scheduler)
    from factory.core.schedule_tool import _handle_create_schedule
    _handle_create_schedule({'name':'Создано агентом','schedule':'23:59','payload':'Проверить','profile':'demo'})
    rows=controller.list()['schedules']
    assert len(rows)==1 and rows[0]['name']=='Создано агентом' and rows[0]['cwd']==service.workdir


def test_stale_due_callback_after_manual_run_cannot_enqueue_again(setup):
    service,scheduler,controller,fields=setup
    identity=controller.save(fields)['schedule_id'];due(scheduler,identity)
    old=scheduler.get_schedule(identity)
    stale={'schedule_id':identity,'agent_id':'demo','payload':old.payload,'name':old.name,
           'metadata':{},'run_id':str(old.next_run_at),'due_at':old.next_run_at}
    controller.action(identity,'run',{})
    service.tasks['task-1']['status']='ok'
    with pytest.raises(MobileInvalid):controller._enqueue(stale)
    assert len(service.tasks)==len(service.sessions)==1


def test_enqueue_rechecks_current_active_task_inside_controller_lock(setup):
    service,scheduler,controller,fields=setup
    identity=controller.save(fields)['schedule_id']
    stale={'schedule_id':identity,'agent_id':'demo','payload':fields['payload'],'name':fields['name'],
           'metadata':{},'run_id':'fixture-stale-manual'}
    controller.action(identity,'run',{})
    with pytest.raises(MobileInvalid):controller._enqueue(stale)
    assert len(service.tasks)==len(service.sessions)==1


def at_time(instant):
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime.fromisoformat(instant).astimezone(tz)
    return patch('factory.core.scheduler.datetime', Clock)


@pytest.mark.parametrize('hours', [4, 8, 5, 24, 48])
def test_hour_interval_round_trip_and_due_launch_keep_fixed_spacing(setup, hours):
    service, scheduler, controller, fields = setup
    fields = fields | {'schedule_type': 'hourly', 'interval_hours': hours, 'time_value': '23:15'}
    with at_time('2026-10-13T18:10:00+00:00'):
        identity = controller.save(fields)['schedule_id']
    first = int(datetime.fromisoformat('2026-10-13T20:15:00+00:00').timestamp())
    row = controller.list()['schedules'][0]
    assert row['interval_hours'] == hours and row['next_run_at'] == first
    assert row['time_value'] == '23:15' and row['schedule_type'] == 'hourly'
    with patch('factory.core.scheduler.time.time', return_value=first + 1), at_time('2026-10-13T20:15:01+00:00'):
        scheduler._check_and_run_schedules()
    saved = scheduler.get_schedule(identity)
    assert len(service.tasks) == 1 and saved.run_count == 1
    assert saved.next_run_at == first + hours * 3600
    restarted = type(scheduler)(scheduler.db_path).get_schedule(identity)
    with at_time('2026-10-13T20:15:01+00:00'):
        assert restarted.calculate_next_run() == first + hours * 3600


def test_interval_edit_pause_resume_and_legacy_save_preserve_cadence(setup):
    service, scheduler, controller, fields = setup
    fields = fields | {'schedule_type': 'hourly', 'time_value': '23:15', 'interval_hours': 4}
    with at_time('2026-10-13T18:10:00+00:00'):
        identity = controller.save(fields)['schedule_id']
    anchor = scheduler.get_schedule(identity).to_dict()['metadata']['interval_anchor_at']
    with at_time('2026-10-14T01:00:00+00:00'):
        legacy = {key: value for key, value in fields.items() if key != 'interval_hours'}
        controller.save(legacy | {'name': 'Новое название'}, identity)
        controller.action(identity, 'enabled', {'enabled': False})
        assert scheduler.get_schedule(identity).next_run_at is None
        controller.action(identity, 'enabled', {'enabled': True})
    row = controller.list()['schedules'][0]
    assert row['interval_hours'] == 4 and row['next_run_at'] == anchor + 8 * 3600
    assert scheduler.get_schedule(identity).to_dict()['metadata']['interval_anchor_at'] == anchor
    with at_time('2026-10-14T01:00:00+00:00'):
        controller.save(fields | {'interval_hours': 8}, identity)
    assert controller.list()['schedules'][0]['interval_hours'] == 8
    assert scheduler.get_schedule(identity).next_run_at == int(datetime.fromisoformat('2026-10-14T20:15:00+00:00').timestamp())
    controller.save({key: value for key, value in fields.items() if key != 'interval_hours'} | {'schedule_type': 'daily'}, identity)
    assert 'interval_anchor_at' not in scheduler.get_schedule(identity).to_dict()['metadata']
    assert controller.list()['schedules'][0]['interval_hours'] == 1


@pytest.mark.parametrize('hours', [0, -1, 1.5, True, '4', None, 8761])
def test_invalid_hour_intervals_do_not_create_jobs(setup, hours):
    service, scheduler, controller, fields = setup
    with pytest.raises(MobileInvalid):
        controller.save(fields | {'schedule_type': 'hourly', 'interval_hours': hours})
    assert scheduler.list_schedules() == [] and service.tasks == {}


def test_interval_field_is_not_silently_accepted_for_daily_jobs(setup):
    service, scheduler, controller, fields = setup
    with pytest.raises(MobileInvalid):
        controller.save(fields | {'interval_hours': 4})
    assert scheduler.list_schedules() == []


def test_interval_clock_skips_missed_slots_and_keeps_elapsed_hours_across_dst():
    from factory.core.scheduler import Schedule
    anchor = int(datetime.fromisoformat('2026-10-24T23:15:00+00:00').timestamp())
    job = Schedule({'enabled': True, 'schedule_type': 'hourly', 'time_value': '01:15',
                    'timezone': 'Europe/Berlin', 'metadata': {'interval_hours': 4, 'interval_anchor_at': anchor}})
    with at_time('2026-10-24T23:15:00+00:00'):
        assert job.calculate_next_run() == anchor + 4 * 3600
    with at_time('2026-10-26T01:15:00+00:00'):
        assert job.calculate_next_run() == anchor + 28 * 3600


def test_old_hourly_records_and_one_hour_field_keep_minute_based_behavior(setup):
    service, scheduler, controller, fields = setup
    with at_time('2026-10-13T18:10:00+00:00'):
        for extra in ({}, {'interval_hours': 1}):
            controller.save(fields | {'schedule_type': 'hourly', 'time_value': '23:15'} | extra)
    assert all(row['interval_hours'] == 1 and row['next_run_at'] == int(datetime.fromisoformat('2026-10-13T18:15:00+00:00').timestamp()) for row in controller.list()['schedules'])
