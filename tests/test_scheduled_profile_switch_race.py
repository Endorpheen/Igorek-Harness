import sqlite3
import threading
import time

import pytest

from test_mobile_profile_switch import setup as setup
from factory.core.desktop_schedules import DesktopSchedules
from factory.core.mobile_bridge.service import MobileConflict
from factory.core.runtime import get_db_path
from factory.core.scheduler import TaskScheduler


@pytest.mark.parametrize('via', ['composer', 'settings'])
@pytest.mark.parametrize('first', ['profile_switch', 'scheduled_enqueue'])
def test_due_schedule_and_composer_switch_serialize_without_losing_vault_or_occurrence(setup, monkeypatch, first, via):
    owner, service, agent, saved, workdir = setup
    source = service.create_session(profile='cloud', cwd=str(workdir))['session_id']
    scheduler = TaskScheduler(get_db_path())
    controller = DesktopSchedules(service, scheduler)
    identity = scheduler.create_schedule(name='Fixture background job', agent_id='cloud', payload='Fixture work',
        schedule_type='hourly', time_value='00:00', tz='UTC',
        metadata={'cwd': str(workdir), 'deliver_session': source, 'quiet': False})
    due_at = int(time.time()) - 1
    with sqlite3.connect(get_db_path()) as connection:
        connection.execute('UPDATE schedules SET next_run_at=? WHERE schedule_id=?', (due_at, identity))
    reached, release = threading.Event(), threading.Event()
    if first == 'profile_switch':
        original = controller._ready
        def ready(info):
            result = original(info)
            if not reached.is_set():
                reached.set()
                assert release.wait(5)
            return result
        scheduler.set_readiness_check(ready)
    else:
        original = service.send_message
        def send(*args, **kwargs):
            result = original(*args, **kwargs)
            reached.set()
            assert release.wait(5)
            return result
        monkeypatch.setattr(service, 'send_message', send)
    thread = threading.Thread(target=scheduler._check_and_run_schedules, daemon=True)
    thread.start()
    try:
        assert reached.wait(5)
        if first == 'profile_switch':
            if via == 'settings':
                result=owner.dispatch({'id':'start','action':'start_agent','credential_id':saved['local']['credential_id']})
                assert not result.get('error') and result['result']['agent']['profile']=='local'
            else:
                assert service.switch_session_profile(source, 'local')['meta']['profile'] == 'local'
        else:
            assert not service.get_session(source)['running_task_id']
            if via == 'settings':
                result=owner.dispatch({'id':'start','action':'start_agent','credential_id':saved['local']['credential_id']})
                assert result.get('error')=='agent_busy'
            else:
                with pytest.raises(MobileConflict):
                    service.switch_session_profile(source, 'local')
            assert service.get_session(source)['meta']['profile'] == 'cloud'
        assert owner.dispatch({'id': 'during-race', 'action': 'status'})['result']['state'] == 'unlocked'
        assert len(owner.dispatch({'id': 'profiles', 'action': 'list_profiles'})['result']['profiles']) == 3
    finally:
        release.set()
        thread.join(5)
    assert not thread.is_alive()
    schedule = scheduler.get_schedule(identity)
    if first == 'profile_switch':
        assert schedule.run_count == 0 and schedule.next_run_at == due_at
        with sqlite3.connect(get_db_path()) as connection:
            assert connection.execute('SELECT COUNT(*) FROM tasks').fetchone()[0] == 0
        service.switch_session_profile(source, 'cloud')
        scheduler._check_and_run_schedules()
        schedule = scheduler.get_schedule(identity)
    assert schedule.run_count == 1 and schedule.next_run_at > due_at
    metadata = controller._metadata(schedule.to_dict())
    assert metadata['last_session_id'] != source
    task = service.get_task(metadata['last_task_id'])
    assert task['profile'] == 'cloud' and task['status'] == 'queued'
    assert task['metadata']['schedule_run']['source_session'] == source
    assert agent.active == 'cloud'
    assert owner.dispatch({'id': 'after-race', 'action': 'status'})['result']['state'] == 'unlocked'


def test_owner_control_stays_responsive_while_a_composer_worker_start_is_pending(setup, monkeypatch):
    owner, service, agent, saved, workdir = setup
    source = service.create_session(profile='cloud', cwd=str(workdir))['session_id']
    entered, release = threading.Event(), threading.Event()
    original = agent.start
    def start(identity):
        with agent.control_lock:
            if identity == saved['local']['credential_id']:
                entered.set()
                assert release.wait(5)
            return original(identity)
    monkeypatch.setattr(agent, 'start', start)
    result = {}
    composer = threading.Thread(target=lambda: result.update(service.switch_session_profile(source, 'local')), daemon=True)
    composer.start()
    try:
        assert entered.wait(5)
        reply = owner.dispatch({'id': 'conflicting-start', 'action': 'start_agent',
                                'credential_id': saved['cloud']['credential_id']})
        assert reply.get('error') == 'agent_busy'
        assert owner.dispatch({'id': 'responsive', 'action': 'status'})['result']['state'] == 'unlocked'
        assert owner.dispatch({'id': 'readable', 'action': 'list_profiles'})['result']['profiles']
    finally:
        release.set()
        composer.join(5)
    assert not composer.is_alive() and result['meta']['profile'] == 'local'
    assert owner.dispatch({'id': 'after', 'action': 'status'})['result']['state'] == 'unlocked'
