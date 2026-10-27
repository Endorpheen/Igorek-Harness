from unittest.mock import patch
import subprocess
import sys
import sqlite3

import pytest

from test_scheduled_delivery import finish, setup as setup


def test_retry_after_response_saved_but_finalize_marker_failed_does_not_duplicate(setup):
    service, _, _, session, _ = setup
    task_id = service.send_message(session, 'question')['task_id']
    finish(task_id, text='fixture answer')
    with patch.object(service, '_merge_task_metadata', side_effect=OSError('fixture metadata failure')):
        with pytest.raises(OSError, match='fixture metadata failure'):
            service.finalize_turns()
    assert [row['text'] for row in service.get_session(session)['messages'] if row['role'] == 'assistant'] == ['fixture answer']
    service.finalize_turns()
    assert [row['text'] for row in service.get_session(session)['messages'] if row['role'] == 'assistant'] == ['fixture answer']


def test_same_response_in_follow_up_round_is_preserved(setup):
    service, _, _, session, _ = setup
    task_id = service.send_message(session, 'first question')['task_id']
    service.send_message(session, 'follow-up question')
    finish(task_id, text='same answer')
    service.finalize_turns()
    assert service.resume_pending_steers() == 1
    finish(task_id, text='same answer')
    service.finalize_turns()
    messages = service.get_session(session)['messages']
    assert [row['text'] for row in messages if row['role'] == 'assistant'] == ['same answer', 'same answer']


def test_process_death_after_response_write_recovers_once(setup):
    service, _, _, session, _ = setup
    task_id = service.send_message(session, 'question')['task_id']
    finish(task_id, text='answer before process death')

    script = '''
import os
from unittest.mock import patch
from factory.core.mobile_bridge.service import MobileService
service = MobileService(profile="demo", workdir=os.environ["IGOREK_FACTORY_HOME"], available_profiles=lambda: ["demo"])
with patch.object(service, "_merge_task_metadata", side_effect=lambda *args: os._exit(23)):
    service.finalize_turns()
'''
    process = subprocess.run([sys.executable, '-c', script], timeout=10, check=False)
    assert process.returncode == 23
    service.finalize_turns()
    messages = service.get_session(session)['messages']
    assert [row['text'] for row in messages if row['role'] == 'assistant'] == ['answer before process death']


def test_attachment_event_is_recovered_after_response_write(setup):
    service, _, _, session, root = setup
    attachment = root / 'result.txt'
    attachment.write_text('fixture contents')
    task_id = service.send_message(session, 'question')['task_id']
    finish(task_id, text=f'Result: `{attachment}`')
    append_event = service.store.append_event

    def fail_attachment_event(**kwargs):
        if kwargs.get('kind') == 'message.files':
            raise OSError('fixture event failure')
        return append_event(**kwargs)

    with patch.object(service.store, 'append_event', side_effect=fail_attachment_event):
        with pytest.raises(OSError, match='fixture event failure'):
            service.finalize_turns()
    service.finalize_turns()
    messages = [row for row in service.get_session(session)['messages'] if row['role'] == 'assistant']
    events = service.store.list_events(session_id=session, kinds=('message.files',))
    assert len(messages) == len(events) == 1
    assert events[0]['payload']['files'] == messages[0]['files']


def test_resume_failure_keeps_follow_up_pending_for_retry(setup):
    from factory.core.cli_tasks import get_task
    from factory.core.steer_queue import pending_steer_count

    service, _, _, session, _ = setup
    task_id = service.send_message(session, 'first question')['task_id']
    service.send_message(session, 'follow-up question')
    finish(task_id, text='first answer')
    service.finalize_turns()
    with patch('factory.core.mobile_bridge.service._build_prompt', side_effect=OSError('fixture interruption')):
        with pytest.raises(OSError, match='fixture interruption'):
            service.resume_pending_steers()
    assert pending_steer_count(task_id) == 1
    assert service.resume_pending_steers() == 1
    task = get_task(task_id)
    assert task['status'] == 'queued'
    assert 'follow-up question' in task['input_text']
    assert pending_steer_count(task_id) == 0


def test_resume_rolls_back_task_if_queue_commit_fails(setup):
    from factory.core.cli_tasks import get_task
    from factory.core.runtime import get_db_path
    from factory.core.steer_queue import pending_steer_count

    service, _, _, session, _ = setup
    task_id = service.send_message(session, 'first question')['task_id']
    service.send_message(session, 'follow-up question')
    finish(task_id, text='first answer')
    service.finalize_turns()
    with sqlite3.connect(get_db_path()) as connection:
        connection.execute("CREATE TRIGGER fail_steer_delivery BEFORE UPDATE OF consumed_at "
                           "ON session_steer_messages BEGIN SELECT RAISE(ABORT, 'fixture failure'); END")
    with pytest.raises(sqlite3.IntegrityError, match='fixture failure'):
        service.resume_pending_steers()
    assert get_task(task_id)['status'] == 'ok'
    assert pending_steer_count(task_id) == 1
    with sqlite3.connect(get_db_path()) as connection:
        connection.execute('DROP TRIGGER fail_steer_delivery')
    assert service.resume_pending_steers() == 1
    assert get_task(task_id)['status'] == 'queued'
    assert pending_steer_count(task_id) == 0


def test_cancel_during_resume_does_not_run_cancelled_message(setup):
    from factory.core.cli_tasks import get_task
    from factory.core.mobile_bridge import service as service_module
    from factory.core.steer_queue import cancel_steer, pending_steer_count

    service, _, _, session, _ = setup
    task_id = service.send_message(session, 'first question')['task_id']
    follow_up = service.send_message(session, 'cancel this follow-up')
    finish(task_id, text='first answer')
    service.finalize_turns()
    build_prompt = service_module._build_prompt

    def cancel_before_commit(*args, **kwargs):
        assert cancel_steer(task_id, follow_up['message_id'], session)
        return build_prompt(*args, **kwargs)

    with patch.object(service_module, '_build_prompt', side_effect=cancel_before_commit):
        assert service.resume_pending_steers() == 0
    assert get_task(task_id)['status'] == 'ok'
    assert pending_steer_count(task_id) == 0


def test_orphan_recovery_does_not_create_duplicate_tasks_after_journal_failure(setup):
    from factory.core.runtime import get_db_path

    service, _, _, session, _ = setup
    task_id = service.send_message(session, 'first question')['task_id']
    service.send_message(session, 'orphaned follow-up')
    with sqlite3.connect(get_db_path()) as connection:
        connection.execute('DELETE FROM tasks WHERE task_id = ?', (task_id,))
    with patch('factory.core.mobile_bridge.service._reassign_journal_task_id',
               side_effect=OSError('fixture journal failure')):
        with pytest.raises(OSError, match='fixture journal failure'):
            service.resume_pending_steers()
    assert service.resume_pending_steers() == 1
    with sqlite3.connect(get_db_path()) as connection:
        tasks = connection.execute('SELECT task_id, input_text FROM tasks').fetchall()
    assert len(tasks) == 1
    assert 'orphaned follow-up' in tasks[0][1]


def test_orphan_recovery_preserves_message_enqueued_after_snapshot(setup):
    from factory.core.mobile_bridge import service as service_module
    from factory.core.runtime import get_db_path
    from factory.core.steer_queue import enqueue_steer, pending_steer_sessions

    service, _, _, session, _ = setup
    task_id = service.send_message(session, 'first question')['task_id']
    service.send_message(session, 'first follow-up')
    with sqlite3.connect(get_db_path()) as connection:
        connection.execute('DELETE FROM tasks WHERE task_id = ?', (task_id,))
    reassign = service_module._reassign_journal_task_id

    def enqueue_after_snapshot(*args):
        reassign(*args)
        enqueue_steer(session_id=session, task_id=task_id, text='later follow-up', message_id='late')

    with patch.object(service_module, '_reassign_journal_task_id', side_effect=enqueue_after_snapshot):
        assert service.resume_pending_steers() == 1
    assert [item['text'] for item in pending_steer_sessions()[task_id]] == ['later follow-up']


def test_orphan_recovery_after_process_death_reuses_created_task(setup):
    from factory.core.runtime import get_db_path

    service, _, _, session, _ = setup
    task_id = service.send_message(session, 'first question')['task_id']
    service.send_message(session, 'orphaned follow-up')
    with sqlite3.connect(get_db_path()) as connection:
        connection.execute('DELETE FROM tasks WHERE task_id = ?', (task_id,))
    script = '''
import os
from unittest.mock import patch
from factory.core.mobile_bridge.service import MobileService
service = MobileService(profile="demo", workdir=os.environ["IGOREK_FACTORY_HOME"], available_profiles=lambda: ["demo"])
with patch("factory.core.mobile_bridge.service._reassign_journal_task_id", side_effect=lambda *args: os._exit(23)):
    service.resume_pending_steers()
'''
    process = subprocess.run([sys.executable, '-c', script], timeout=10, check=False)
    assert process.returncode == 23
    assert service.resume_pending_steers() == 1
    with sqlite3.connect(get_db_path()) as connection:
        tasks = connection.execute('SELECT task_id FROM tasks').fetchall()
    assert len(tasks) == 1
