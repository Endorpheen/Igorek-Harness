import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from factory.core.cli_tasks import get_task
from factory.core.mobile_bridge.service import MobileService
from factory.core.scheduled_delivery import deliver_result, report_result
from test_scheduled_delivery import finish, launch, setup as setup


def _deliver_in_process(root, source, task_id, first, entered, release, started, queue):
    from factory.core import session_history as history
    from factory.core.cli_tasks import get_task as load_task
    from factory.core.mobile_bridge.service import MobileService as Service
    from factory.core.scheduled_delivery import deliver_result as deliver
    assert Path(deliver.__code__.co_filename).resolve() == Path(__file__).resolve().parents[1] / 'factory/core/scheduled_delivery.py'

    service = Service(profile='demo', workdir=str(root), available_profiles=lambda: ['demo'])
    original = history.read_history

    def read(path):
        result = original(path)
        entered.set()
        if first and not release.wait(10):
            raise TimeoutError('delivery rendezvous timed out')
        return result

    history.read_history = read
    original_get_session = service.get_session

    def ready_session(session_id):
        result = original_get_session(session_id)
        started.set()
        return result

    service.get_session = ready_session
    try:
        queue.put(('ok', deliver(service, load_task(task_id), source, quiet=True)))
    except Exception as exc:
        queue.put(('error', repr(exc)))


def test_processes_serialize_delivery_check_and_history_write(setup):
    import multiprocessing

    service, _, _, source, root = setup
    _, task_id = launch(setup)
    report_result(task_id, {'disposition': 'deliver', 'text': 'process result'})
    finish(task_id)
    context = multiprocessing.get_context('spawn')
    first_entered, second_entered, release = context.Event(), context.Event(), context.Event()
    first_started, second_started = context.Event(), context.Event()
    queue = context.Queue()
    first = context.Process(target=_deliver_in_process, args=(root, source, task_id, True, first_entered, release, first_started, queue))
    second = context.Process(target=_deliver_in_process, args=(root, source, task_id, False, second_entered, release, second_started, queue))
    try:
        first.start()
        assert first_started.wait(10) and first_entered.wait(10)
        second.start()
        assert second_started.wait(10)
        assert not second_entered.wait(2)
        release.set()
        first.join(10)
        second.join(10)
        assert first.exitcode == 0 and second.exitcode == 0
        assert sorted([queue.get(timeout=5), queue.get(timeout=5)]) == [('ok', 'delivered'), ('ok', 'delivered')]
        assert [message['text'] for message in service.get_session(source)['messages']] == ['process result']
        assert len(service.store.list_events(session_id=source)) == 1
    finally:
        release.set()
        for process in (first, second):
            if process.is_alive():
                process.terminate()
                process.join(10)
        queue.close()


def _crash_after_history_write(root, source, task_id):
    import os
    from factory.core.cli_tasks import get_task as load_task
    from factory.core.mobile_bridge.service import MobileService as Service
    from factory.core.scheduled_delivery import deliver_result as deliver
    assert Path(deliver.__code__.co_filename).resolve() == Path(__file__).resolve().parents[1] / 'factory/core/scheduled_delivery.py'

    service = Service(profile='demo', workdir=str(root), available_profiles=lambda: ['demo'])
    service.store.append_event = lambda **kwargs: os._exit(23)
    deliver(service, load_task(task_id), source, quiet=True)


def test_process_restart_after_history_write_completes_delivery_once(setup):
    import multiprocessing

    service, _, _, source, root = setup
    _, task_id = launch(setup)
    report_result(task_id, {'disposition': 'deliver', 'text': 'recover after crash'})
    finish(task_id)
    process = multiprocessing.get_context('spawn').Process(
        target=_crash_after_history_write, args=(root, source, task_id)
    )
    try:
        process.start()
        process.join(15)
        assert process.exitcode == 23
        assert [message['text'] for message in service.get_session(source)['messages']] == ['recover after crash']
        assert service.store.list_events(session_id=source) == []
        restarted = MobileService(profile='demo', workdir=str(root), available_profiles=lambda: ['demo'])
        assert deliver_result(restarted, get_task(task_id), source, quiet=True) == 'delivered'
        assert deliver_result(restarted, get_task(task_id), source, quiet=True) == 'delivered'
        assert [message['text'] for message in restarted.get_session(source)['messages']] == ['recover after crash']
        assert len(restarted.store.list_events(session_id=source)) == 1
    finally:
        if process.is_alive():
            process.terminate()
            process.join(10)
