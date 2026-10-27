import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

from factory.core.cli_chat import _session_path
from factory.core.cli_tasks import get_task
from factory.core.desktop_schedules import DesktopSchedules
from factory.core.mobile_bridge.service import MobileService
from factory.core.runtime import get_db_path, get_profile_path
from factory.core.schedule_tool import _handle_create_schedule, _handle_schedule_result
from factory.core.scheduler import Schedule, TaskScheduler
from factory.core.schema import initialize_database
from factory.core.scheduled_delivery import deliver_result, report_result
from factory.core.session_history import read_history


@pytest.fixture
def setup(tmp_path, monkeypatch):
    monkeypatch.setenv('IGOREK_FACTORY_HOME', str(tmp_path))
    monkeypatch.setenv('FACTORY_DATA_DIR', str(tmp_path))
    from factory.core import opencode_models, openrouter_pricing
    monkeypatch.setattr(opencode_models, '_fetch_models', lambda: None)
    monkeypatch.setattr(openrouter_pricing, '_fetch_models', lambda timeout_s: None)
    initialize_database(get_db_path())
    profile = Path(get_profile_path('demo')); profile.parent.mkdir(parents=True, exist_ok=True)
    profile.write_text(json.dumps({'backend': 'cc', 'provider': 'openai', 'model': 'test'}))
    service = MobileService(profile='demo', workdir=str(tmp_path), available_profiles=lambda: ['demo'])
    scheduler = TaskScheduler(get_db_path())
    import factory.core.scheduler as module
    monkeypatch.setattr(module, '_scheduler', scheduler)
    controller = DesktopSchedules(service, scheduler)
    session = service.create_session(profile='demo', cwd=str(tmp_path))['session_id']
    return service, scheduler, controller, session, tmp_path


def launch(setup, quiet=True):
    service, scheduler, controller, source, root = setup
    value = _handle_create_schedule({'name': 'eNews', 'schedule': '00:15', 'repeat': 'hourly',
        'quiet': quiet, 'payload': 'Проверить X'}, current_session=source, profile='demo', workdir=str(root))
    assert 'error' not in json.loads(value)
    schedule = scheduler.list_schedules()[0]
    assert schedule.schedule_type == 'hourly'
    controller.action(schedule.schedule_id, 'run', {})
    task_id = scheduler.get_schedule(schedule.schedule_id).to_dict()['metadata']['last_task_id']
    return schedule.schedule_id, task_id


def finish(task_id, status='ok', text='Финальный ответ рабочего запуска'):
    with sqlite3.connect(get_db_path()) as conn:
        conn.execute('UPDATE tasks SET status=?,output_text=? WHERE task_id=?', (status, text, task_id))


@pytest.mark.parametrize('now,expected', [
    ('2026-10-11T06:14:59+00:00', '2026-10-11T06:15:00+00:00'),
    ('2026-10-11T06:15:00+00:00', '2026-10-11T07:15:00+00:00'),
    ('2026-10-11T23:59:00+00:00', '2026-10-12T00:15:00+00:00'),
])
def test_hourly_next_slot_and_not_daily(now, expected):
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None): return datetime.fromisoformat(now).astimezone(tz)
    with patch('factory.core.scheduler.datetime', Clock):
        value = Schedule({'enabled': True, 'schedule_type': 'hourly', 'time_value': '00:15', 'timezone': 'Europe/Moscow'}).calculate_next_run()
    assert value == int(datetime.fromisoformat(expected).timestamp())


def test_background_run_keeps_profile_cwd_and_isolated_context(setup):
    service, scheduler, controller, source, root = setup
    identity, task_id = launch(setup)
    task = get_task(task_id)
    assert task['profile'] == 'demo'
    assert task['metadata']['cwd'] == str(root)
    assert task['metadata']['session'] != source
    assert task['metadata']['schedule_run'] == {'schedule_id': identity, 'source_session': source, 'quiet': True}
    assert 'desktop_browser' in task['input_text'] and 'schedule_result' in task['input_text']
    assert service.get_session(source)['messages'] == []
    with pytest.raises(Exception): controller.action(identity, 'run', {})


def test_delivers_text_file_and_live_event_once_after_success(setup):
    service, scheduler, controller, source, root = setup
    identity, task_id = launch(setup)
    media = root / 'post-123.png'; media.write_bytes(b'\x89PNG\r\n\x1a\nfixture')
    report_result(task_id, {'disposition': 'deliver', 'text': 'Готовый черновик', 'files': [str(media)]})
    controller.deliver_pending()
    assert service.get_session(source)['messages'] == []
    finish(task_id)
    live = []; service.add_event_hook(live.append)
    controller.deliver_pending(); controller.deliver_pending()
    message, = service.get_session(source)['messages']
    assert message['text'] == 'Готовый черновик' and len(message['files']) == 1
    info, stream = service.open_session_file(source, message['files'][0]['id'])
    with stream: assert stream.read() == media.read_bytes()
    assert len(live) == 1 and live[0]['kind'] == 'schedule.delivered'
    assert scheduler.get_schedule(identity).to_dict()['metadata']['delivery_status'] == 'delivered'
    deliver_result(service, get_task(task_id), source, quiet=True)
    assert len(read_history(_session_path(source))) == len(service.store.list_events(session_id=source)) == 1


@pytest.mark.parametrize('case,status', [('skip','skipped'),('missing','missing_result'),('failed','failed')])
def test_quiet_run_does_not_post_empty_or_failed_output(setup, case, status):
    service, scheduler, controller, source, root = setup
    identity, task_id = launch(setup)
    if case == 'skip': report_result(task_id, {'disposition': 'skip'})
    finish(task_id, 'fail' if case == 'failed' else 'ok')
    controller.deliver_pending()
    assert service.get_session(source)['messages'] == []
    assert service.store.list_events(session_id=source) == []
    assert scheduler.get_schedule(identity).to_dict()['metadata']['delivery_status'] == status


@pytest.mark.parametrize('fields', [
    {'disposition':'deliver'}, {'disposition':'skip','text':'nothing'}, {'disposition':'skip','files':['/tmp/x']},
    {'disposition':'unknown'}, {'disposition':'deliver','text':1}, {'disposition':'deliver','files':['/etc/passwd']},
    {'disposition':'deliver','files':['relative.txt']}, {'disposition':'deliver','text':'x','extra':True},
])
def test_invalid_results_rejected(setup, fields):
    identity, task_id = launch(setup)
    with pytest.raises(ValueError): report_result(task_id, fields)


def test_result_rejects_external_symlink_and_mutated_attachment(setup):
    service, scheduler, controller, source, root = setup
    identity, task_id = launch(setup)
    media = root/'media.txt'; media.write_text('original')
    external = root.parent / (root.name + '-external.txt'); external.write_text('outside')
    link = root/'link.txt'; link.symlink_to(external)
    with pytest.raises(ValueError): report_result(task_id, {'disposition':'deliver','files':[str(link)]})
    report_result(task_id, {'disposition':'deliver','files':[str(media)]})
    media.write_text('changed content')
    finish(task_id); controller.deliver_pending()
    assert service.get_session(source)['messages'] == []
    assert scheduler.get_schedule(identity).to_dict()['metadata']['delivery_status'] == 'delivery_failed'


def test_regular_legacy_schedule_still_delivers_final_text(setup):
    service, scheduler, controller, source, root = setup
    identity, task_id = launch(setup, quiet=False)
    finish(task_id, text='Обычное напоминание')
    controller.deliver_pending()
    assert service.get_session(source)['messages'][0]['text'] == 'Обычное напоминание'


def test_schedule_result_tool_refuses_non_schedule_task(setup):
    service, scheduler, controller, source, root = setup
    task = service.send_message(source, 'Обычная задача')
    assert json.loads(_handle_schedule_result({'disposition':'skip'}, task_id=task['task_id']))['error']


def test_restart_recovers_after_journal_write_without_duplicate_delivery(setup, monkeypatch):
    service, scheduler, controller, source, root = setup
    identity, task_id = launch(setup)
    report_result(task_id, {'disposition':'deliver','text':'Новость'})
    finish(task_id)
    original = service.store.append_event
    def interrupted(**kwargs): raise OSError('fixture journal interruption')
    monkeypatch.setattr(service.store, 'append_event', interrupted)
    controller.deliver_pending()
    assert len(service.get_session(source)['messages']) == 1
    assert not controller._ready(scheduler.get_schedule(identity).to_dict())
    monkeypatch.setattr(service.store, 'append_event', original)
    meta = scheduler.get_schedule(identity).to_dict()['metadata']; meta['delivery_retry_at'] = 0
    scheduler.update_schedule(identity, metadata=meta)
    DesktopSchedules(service, scheduler).deliver_pending()
    assert len(service.get_session(source)['messages']) == 1
    assert len(service.store.list_events(session_id=source)) == 1
    assert scheduler.get_schedule(identity).to_dict()['metadata']['delivery_status'] == 'delivered'


def test_hourly_tool_rejects_quiet_without_source_chat(setup):
    result = _handle_create_schedule({'name':'Check','schedule':'00:00','repeat':'hourly','quiet':True,'payload':'Read'})
    assert json.loads(result)['error']
    assert setup[1].list_schedules() == []


@pytest.mark.parametrize('size,accepted', [(101 * 1024 * 1024, True), (500 * 1024 * 1024, True), (500 * 1024 * 1024 + 1, False)])
def test_scheduled_delivery_attachment_limit_500_mib(setup, size, accepted):
    service, scheduler, controller, source, root = setup
    identity, task_id = launch(setup)
    media = root / 'large.mp4'
    with media.open('wb') as stream: stream.truncate(size)
    if accepted:
        report_result(task_id, {'disposition': 'deliver', 'text': 'Медиа', 'files': [str(media)]})
        finish(task_id); controller.deliver_pending()
        info = service.get_session(source)['messages'][0]['files'][0]
        assert info['size'] == size
        description, stream = service.open_session_file(source, info['id'])
        with stream: assert len(stream.read(65536)) == 65536
    else:
        with pytest.raises(ValueError, match='500'):
            report_result(task_id, {'disposition': 'deliver', 'text': 'Медиа', 'files': [str(media)]})
        assert service.get_session(source)['messages'] == []
