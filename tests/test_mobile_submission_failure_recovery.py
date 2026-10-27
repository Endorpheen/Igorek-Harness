from unittest.mock import patch

import pytest

from factory.core.mobile_bridge import service as mobile_module
from factory.core.schema import open_db
from factory.core.runtime import get_db_path
from factory.core.steer_queue import pending_steer_count
from test_scheduled_delivery import setup as setup


def test_retry_after_history_write_failure_has_one_task_and_one_user_message(setup):
    service, _, _, session, _ = setup
    original = mobile_module._append_entry
    with patch.object(mobile_module, '_append_entry', side_effect=OSError('fixture disk failure')):
        with pytest.raises(OSError, match='fixture disk failure'):
            service.send_message(session, 'fixture request', idempotency_key='retry-key')
    with patch.object(mobile_module, '_append_entry', original):
        receipt = service.send_message(session, 'fixture request', idempotency_key='retry-key')
    assert receipt['task_id']
    assert pending_steer_count(receipt['task_id']) == 0
    messages = service.get_session(session)['messages']
    assert [row['text'] for row in messages if row['role'] == 'user'] == ['fixture request']
    with open_db(get_db_path()) as conn:
        rows = conn.execute('SELECT task_id FROM tasks WHERE profile=?', ('demo',)).fetchall()
    assert len(rows) == 1
    assert rows[0][0] == receipt['task_id']


@pytest.mark.parametrize('boundary', ['event_before', 'event_after', 'receipt'])
def test_retry_after_submission_persistence_failure_recovers_once(setup, boundary):
    service, _, _, session, _ = setup
    original_event = service.store.append_event
    original_receipt = service.store.remember_request

    def fail_event(**kwargs):
        if boundary == 'event_after':
            original_event(**kwargs)
        raise OSError('fixture event failure')

    def fail_receipt(*args, **kwargs):
        raise OSError('fixture receipt failure')

    target = 'remember_request' if boundary == 'receipt' else 'append_event'
    failure = fail_receipt if boundary == 'receipt' else fail_event
    with patch.object(service.store, target, side_effect=failure):
        with pytest.raises(OSError):
            service.send_message(session, 'fixture request', idempotency_key='retry-boundary')
    assert service.store.append_event == original_event
    assert service.store.remember_request == original_receipt
    receipt = service.send_message(session, 'fixture request', idempotency_key='retry-boundary')
    repeated = service.send_message(session, 'fixture request', idempotency_key='retry-boundary')
    assert repeated['task_id'] == receipt['task_id']
    assert pending_steer_count(receipt['task_id']) == 0
    messages = service.get_session(session)['messages']
    assert [row['text'] for row in messages if row['role'] == 'user'] == ['fixture request']
    events = [event for event in service.store.list_events(session_id=session) if event['kind'] == 'task.submitted']
    assert len(events) == 1
    assert events[0]['task_id'] == receipt['task_id']


def test_concurrent_independent_services_do_not_create_duplicate_tasks(setup, monkeypatch):
    import threading
    from concurrent.futures import ThreadPoolExecutor
    from factory.core.mobile_bridge.service import MobileService

    service, _, _, session, root = setup
    other = MobileService(profile='demo', workdir=str(root), available_profiles=lambda: ['demo'])
    original = mobile_module.MobileService._active_task_id_for_session
    barrier = threading.Barrier(2, timeout=1)

    def synchronized_active(instance, session_id):
        result = original(instance, session_id)
        try:
            barrier.wait()
        except threading.BrokenBarrierError:
            pass
        return result

    monkeypatch.setattr(mobile_module.MobileService, '_active_task_id_for_session', synchronized_active)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(instance.send_message, session, 'fixture request', idempotency_key='concurrent-key')
                   for instance in (service, other)]
        receipts = [future.result(timeout=15) for future in futures]
    assert len({receipt['task_id'] for receipt in receipts}) == 1
    assert [row['text'] for row in service.get_session(session)['messages'] if row['role'] == 'user'] == ['fixture request']
    assert pending_steer_count(receipts[0]['task_id']) == 0


@pytest.mark.parametrize('status', ['processing', 'ok'])
def test_retry_preserves_already_claimed_or_completed_task(setup, status):
    service, _, _, session, _ = setup
    with patch.object(mobile_module, '_append_entry', side_effect=OSError('fixture disk failure')):
        with pytest.raises(OSError):
            service.send_message(session, 'fixture request', idempotency_key='claimed-key')
    with open_db(get_db_path()) as conn:
        task_id, = conn.execute('SELECT task_id FROM tasks WHERE profile=?', ('demo',)).fetchone()
        conn.execute('UPDATE tasks SET status=? WHERE task_id=?', (status, task_id))
    receipt = service.send_message(session, 'fixture request', idempotency_key='claimed-key')
    assert receipt['task_id'] == task_id
    assert service.get_task(task_id)['status'] == status
    assert pending_steer_count(task_id) == 0
    assert [row['text'] for row in service.get_session(session)['messages'] if row['role'] == 'user'] == ['fixture request']


def _crash_before_history(root, session):
    import os
    from factory.core.mobile_bridge import service as module
    service = module.MobileService(profile='demo', workdir=str(root), available_profiles=lambda: ['demo'])
    module._append_entry = lambda *args, **kwargs: os._exit(23)
    service.send_message(session, 'fixture request', idempotency_key='crash-key')


def test_process_death_after_enqueue_recovers_same_submission(setup):
    import multiprocessing

    service, _, _, session, root = setup
    process = multiprocessing.get_context('spawn').Process(target=_crash_before_history, args=(root, session))
    try:
        process.start()
        process.join(15)
        assert process.exitcode == 23
        with open_db(get_db_path()) as conn:
            task_id, = conn.execute('SELECT task_id FROM tasks WHERE profile=?', ('demo',)).fetchone()
        receipt = service.send_message(session, 'fixture request', idempotency_key='crash-key')
        assert receipt['task_id'] == task_id
        assert pending_steer_count(task_id) == 0
        assert [row['text'] for row in service.get_session(session)['messages'] if row['role'] == 'user'] == ['fixture request']
        assert len([event for event in service.store.list_events(session_id=session) if event['kind'] == 'task.submitted']) == 1
    finally:
        if process.is_alive():
            process.terminate()
            process.join(10)


def test_retry_after_steer_history_failure_does_not_enqueue_duplicate(setup):
    service, _, _, session, _ = setup
    original = service.send_message(session, 'first request', idempotency_key='first-key')
    with patch.object(mobile_module, '_append_entry', side_effect=OSError('fixture disk failure')):
        with pytest.raises(OSError):
            service.send_message(session, 'follow-up', idempotency_key='follow-key')
    retry = service.send_message(session, 'follow-up', idempotency_key='follow-key')
    assert retry['task_id'] == original['task_id']
    assert pending_steer_count(original['task_id']) == 1
    assert [row['text'] for row in service.get_session(session)['messages'] if row['role'] == 'user'] == ['first request', 'follow-up']


@pytest.mark.parametrize('boundary', ['event_before', 'event_after', 'receipt'])
def test_steer_retry_recovers_after_persistence_failure_without_duplicate(setup, boundary):
    service, _, _, session, _ = setup
    first = service.send_message(session, 'first', idempotency_key='first-steer')
    original_event = service.store.record_keyed_steer_event

    def fail_event(*args, **kwargs):
        if boundary == 'event_after':
            original_event(*args, **kwargs)
        raise OSError('fixture event failure')

    def fail_receipt(*args, **kwargs):
        raise OSError('fixture receipt failure')

    name = 'remember_request' if boundary == 'receipt' else 'record_keyed_steer_event'
    failure = fail_receipt if boundary == 'receipt' else fail_event
    with patch.object(service.store, name, side_effect=failure):
        with pytest.raises(OSError):
            service.send_message(session, 'follow-up', idempotency_key='follow-boundary')
    retry = service.send_message(session, 'different retry text', idempotency_key='follow-boundary')
    assert retry['task_id'] == first['task_id']
    assert pending_steer_count(first['task_id']) == 1
    assert [row['text'] for row in service.get_session(session)['messages'] if row['role'] == 'user'] == ['first', 'follow-up']
    events = [event for event in service.store.list_events(session_id=session) if event['kind'] == 'task.message_added']
    assert len(events) == 1 and events[0]['payload']['text'] == 'follow-up'
    assert 'entry_text' not in events[0]['payload']


def test_steer_retry_does_not_reenqueue_message_already_consumed(setup):
    from factory.core.steer_queue import drain_steer

    service, _, _, session, _ = setup
    first = service.send_message(session, 'first', idempotency_key='first-consumed')
    with patch.object(mobile_module, '_append_entry', side_effect=OSError('fixture disk failure')):
        with pytest.raises(OSError):
            service.send_message(session, 'follow-up', idempotency_key='consumed-key')
    assert [item['text'] for item in drain_steer(first['task_id'])] == ['follow-up']
    retry = service.send_message(session, 'follow-up', idempotency_key='consumed-key')
    assert retry['task_id'] == first['task_id']
    assert drain_steer(first['task_id']) == []
    assert [row['text'] for row in service.get_session(session)['messages'] if row['role'] == 'user'] == ['first', 'follow-up']


def test_steer_enqueue_rolls_back_if_recovery_record_cannot_be_written(setup):
    service, _, _, session, _ = setup
    first = service.send_message(session, 'first', idempotency_key='first-rollback')
    with open_db(get_db_path()) as conn:
        conn.execute("CREATE TRIGGER fixture_recovery_failure BEFORE INSERT ON mobile_steer_recovery BEGIN SELECT RAISE(ABORT, 'fixture recovery failure'); END")
    with pytest.raises(Exception, match='fixture recovery failure'):
        service.send_message(session, 'follow-up', idempotency_key='rollback-key')
    assert pending_steer_count(first['task_id']) == 0
    with open_db(get_db_path()) as conn:
        conn.execute('DROP TRIGGER fixture_recovery_failure')
    receipt = service.send_message(session, 'follow-up', idempotency_key='rollback-key')
    assert receipt['task_id'] == first['task_id']
    assert pending_steer_count(first['task_id']) == 1


def _crash_after_steer_enqueue(root, session):
    import os
    from factory.core.mobile_bridge import service as module
    service = module.MobileService(profile='demo', workdir=str(root), available_profiles=lambda: ['demo'])
    module._append_entry = lambda *args, **kwargs: os._exit(24)
    service.send_message(session, 'follow-up', idempotency_key='steer-crash')


def test_process_death_after_steer_enqueue_recovers_one_message(setup):
    import multiprocessing

    service, _, _, session, root = setup
    first = service.send_message(session, 'first', idempotency_key='first-steer-crash')
    process = multiprocessing.get_context('spawn').Process(target=_crash_after_steer_enqueue, args=(root, session))
    try:
        process.start()
        process.join(15)
        assert process.exitcode == 24
        retry = service.send_message(session, 'follow-up', idempotency_key='steer-crash')
        assert retry['task_id'] == first['task_id']
        assert pending_steer_count(first['task_id']) == 1
        assert [row['text'] for row in service.get_session(session)['messages'] if row['role'] == 'user'] == ['first', 'follow-up']
        assert len([event for event in service.store.list_events(session_id=session) if event['kind'] == 'task.message_added']) == 1
    finally:
        if process.is_alive():
            process.terminate()
            process.join(10)


def test_keyed_followup_reaches_worker_when_mobile_store_is_separate(setup):
    from factory.core.mobile_bridge.service import MobileService
    from factory.core.mobile_bridge.store import MobileStore

    _, _, _, session, root = setup
    store = MobileStore(db_path=str(root / 'separate-mobile.db'))
    service = MobileService(profile='demo', workdir=str(root), store=store, available_profiles=lambda: ['demo'])
    initial = service.send_message(session, 'first', idempotency_key='separate-first')
    followup = service.send_message(session, 'follow-up', idempotency_key='separate-follow')
    assert followup['task_id'] == initial['task_id']
    assert pending_steer_count(initial['task_id']) == 1


@pytest.mark.parametrize('boundary', ['history', 'event_after', 'receipt'])
def test_separate_mobile_store_recovers_followup_once(setup, boundary):
    from factory.core.mobile_bridge.service import MobileService
    from factory.core.mobile_bridge.store import MobileStore
    from factory.core.steer_queue import drain_steer

    _, _, _, session, root = setup
    service = MobileService(profile='demo', workdir=str(root), store=MobileStore(db_path=str(root / 'mobile-other.db')), available_profiles=lambda: ['demo'])
    first = service.send_message(session, 'first', idempotency_key='separate-recovery-first')
    event = service.store.record_keyed_steer_event

    def fail(*args, **kwargs):
        if boundary == 'event_after':
            event(*args, **kwargs)
        raise OSError('fixture failure')

    target, name = (mobile_module, '_append_entry') if boundary == 'history' else (service.store, 'record_keyed_steer_event' if boundary == 'event_after' else 'remember_request')
    with patch.object(target, name, side_effect=fail):
        with pytest.raises(OSError, match='fixture failure'):
            service.send_message(session, 'follow-up', idempotency_key='separate-recovery-follow')
    assert [entry['text'] for entry in drain_steer(first['task_id'])] == ['follow-up']
    receipt = service.send_message(session, 'changed retry', idempotency_key='separate-recovery-follow')
    assert receipt['task_id'] == first['task_id']
    assert drain_steer(first['task_id']) == []
    assert [row['text'] for row in service.get_session(session)['messages'] if row['role'] == 'user'] == ['first', 'follow-up']
    events = [event for event in service.store.list_events(session_id=session) if event['kind'] == 'task.message_added']
    assert len(events) == 1 and events[0]['payload']['text'] == 'follow-up'


@pytest.mark.parametrize('separate', [False, True])
def test_steer_event_and_dedup_marker_commit_atomically(setup, separate):
    from factory.core.mobile_bridge.service import MobileService
    from factory.core.mobile_bridge.store import MobileStore

    service, _, _, session, root = setup
    if separate:
        service = MobileService(profile='demo', workdir=str(root), store=MobileStore(db_path=str(root / 'event-other.db')), available_profiles=lambda: ['demo'])
    first = service.send_message(session, 'first', idempotency_key='event-atomic-first')
    with open_db(service.store.db_path) as conn:
        conn.execute("CREATE TRIGGER fixture_event_marker_failure BEFORE INSERT ON mobile_steer_event_keys BEGIN SELECT RAISE(ABORT, 'fixture event marker failure'); END")
    with pytest.raises(Exception, match='fixture event marker failure'):
        service.send_message(session, 'follow-up', idempotency_key='event-atomic-follow')
    assert pending_steer_count(first['task_id']) == 1
    assert not [event for event in service.store.list_events(session_id=session) if event['kind'] == 'task.message_added']
    with open_db(service.store.db_path) as conn:
        conn.execute('DROP TRIGGER fixture_event_marker_failure')
    receipt = service.send_message(session, 'follow-up', idempotency_key='event-atomic-follow')
    assert receipt['task_id'] == first['task_id']
    assert pending_steer_count(first['task_id']) == 1
    assert len([event for event in service.store.list_events(session_id=session) if event['kind'] == 'task.message_added']) == 1


def test_concurrent_different_messages_share_one_active_task(setup, monkeypatch):
    import threading
    from concurrent.futures import ThreadPoolExecutor
    from factory.core.mobile_bridge.service import MobileService
    from factory.core.steer_queue import drain_steer

    service, _, _, session, root = setup
    other = MobileService(profile='demo', workdir=str(root), available_profiles=lambda: ['demo'])
    original = mobile_module.MobileService._active_task_id_for_session
    barrier = threading.Barrier(2, timeout=1)

    def synchronized_active(instance, session_id):
        result = original(instance, session_id)
        try:
            barrier.wait()
        except threading.BrokenBarrierError:
            pass
        return result

    monkeypatch.setattr(mobile_module.MobileService, '_active_task_id_for_session', synchronized_active)
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(instance.send_message, session, text, idempotency_key=key)
                   for instance, text, key in ((service, 'message-a', 'key-a'), (other, 'message-b', 'key-b'))]
        receipts = [future.result(timeout=20) for future in futures]
    assert receipts[0]['task_id'] == receipts[1]['task_id']
    assert sorted(receipt['steered'] for receipt in receipts) == [False, True]
    assert len(drain_steer(receipts[0]['task_id'])) == 1
    assert sorted(row['text'] for row in service.get_session(session)['messages'] if row['role'] == 'user') == ['message-a', 'message-b']


def test_different_sessions_can_submit_without_waiting_for_each_other(setup, monkeypatch):
    import threading
    from concurrent.futures import ThreadPoolExecutor
    from factory.core.mobile_bridge.service import MobileService

    service, _, _, session, root = setup
    other = MobileService(profile='demo', workdir=str(root), available_profiles=lambda: ['demo'])
    other_session = other.create_session(profile='demo', cwd=str(root))['session_id']
    entered, release = threading.Event(), threading.Event()
    original = mobile_module.MobileService._active_task_id_for_session

    def delayed_active(instance, session_id):
        if session_id == session:
            entered.set()
            assert release.wait(10)
        return original(instance, session_id)

    monkeypatch.setattr(mobile_module.MobileService, '_active_task_id_for_session', delayed_active)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            first = pool.submit(service.send_message, session, 'one', idempotency_key='session-one')
            assert entered.wait(10)
            second = pool.submit(other.send_message, other_session, 'two', idempotency_key='session-two')
            result = second.result(timeout=8)
            assert result['steered'] is False
            release.set()
            assert first.result(timeout=10)['steered'] is False
    finally:
        release.set()


def _send_raced_process(root, session, text, key, entered, release, started, results):
    from pathlib import Path
    from factory.core.mobile_bridge import service as module
    from factory.core.mobile_bridge.service import MobileService
    assert Path(module.__file__).resolve() == Path(__file__).resolve().parents[1] / 'factory/core/mobile_bridge/service.py'
    service = MobileService(profile='demo', workdir=str(root), available_profiles=lambda: ['demo'])
    original = service._active_task_id_for_session

    def delayed(session_id):
        value = original(session_id)
        entered.set()
        if text == 'message-a' and not release.wait(10):
            raise TimeoutError('fixture race timed out')
        return value

    service._active_task_id_for_session = delayed
    started.set()
    try:
        results.put(('ok', service.send_message(session, text, idempotency_key=key)))
    except Exception as exc:
        results.put(('error', repr(exc)))


def test_processes_serialize_different_messages_for_one_session(setup):
    import multiprocessing

    service, _, _, session, root = setup
    context = multiprocessing.get_context('spawn')
    first_entered, second_entered, release = context.Event(), context.Event(), context.Event()
    first_started, second_started = context.Event(), context.Event()
    results = context.Queue()
    first = context.Process(target=_send_raced_process, args=(root, session, 'message-a', 'process-a', first_entered, release, first_started, results))
    second = context.Process(target=_send_raced_process, args=(root, session, 'message-b', 'process-b', second_entered, release, second_started, results))
    try:
        first.start()
        assert first_started.wait(10) and first_entered.wait(10)
        second.start()
        assert second_started.wait(10)
        assert not second_entered.wait(0.5)
        release.set()
        first.join(15)
        second.join(15)
        assert first.exitcode == 0 and second.exitcode == 0
        outcomes = [results.get(timeout=5), results.get(timeout=5)]
        assert all(item[0] == 'ok' for item in outcomes), outcomes
        receipts = [item[1] for item in outcomes]
        assert len({item['task_id'] for item in receipts}) == 1
        assert sorted(item['steered'] for item in receipts) == [False, True]
        assert pending_steer_count(receipts[0]['task_id']) == 1
    finally:
        release.set()
        for process in (first, second):
            if process.is_alive():
                process.terminate()
                process.join(10)
        results.close()


@pytest.mark.parametrize('followup', [False, True])
@pytest.mark.parametrize('fragment', [b'{"role":"user","text":"incomplete', b'{"role":"user","text":"\xd0'])
def test_retry_after_partial_history_write_remains_readable(setup, followup, fragment):
    from factory.core.cli_chat import _session_path

    service, _, _, session, _ = setup
    if followup:
        first = service.send_message(session, 'before', idempotency_key='partial-first')
    else:
        first = None
    path = _session_path(session)
    before = path.read_bytes()

    def partial(path, **kwargs):
        with path.open('ab') as stream:
            stream.write(fragment)
        raise OSError('fixture interrupted write')

    with patch.object(mobile_module, '_append_entry', side_effect=partial):
        with pytest.raises(OSError, match='fixture interrupted write'):
            service.send_message(session, 'recovered', idempotency_key='partial-retry')
    receipt = service.send_message(session, 'recovered', idempotency_key='partial-retry')
    messages = [row['text'] for row in service.get_session(session)['messages'] if row['role'] == 'user']
    assert messages == (['before', 'recovered'] if followup else ['recovered'])
    assert path.read_bytes().startswith(before + fragment)
    assert pending_steer_count(receipt['task_id']) == (1 if followup else 0)
    if first:
        assert receipt['task_id'] == first['task_id']
