import multiprocessing
import time

from factory.core import worker as worker_module
from factory.core.schema import initialize_database, open_db
from factory.core.worker import Worker
from test_worker_stuck_task_recovery import _insert_task


def _claim_then_wait(db_path, ready):
    from factory.core import worker as module
    from factory.core.schema import open_db as connect

    module.DB_PATH = db_path
    worker = module.Worker.__new__(module.Worker)
    worker.profile = 'fixture'
    with connect(db_path) as conn:
        task = worker.claim_task(conn)
    if not task or task['task_id'] != 'owned':
        raise RuntimeError('fixture task not claimed')
    ready.set()
    while True:
        time.sleep(1)


def test_dead_worker_task_is_preserved_and_finalized_without_touching_another_profile(tmp_path, monkeypatch):
    monkeypatch.setenv('IGOREK_FACTORY_HOME', str(tmp_path))
    monkeypatch.setenv('FACTORY_DATA_DIR', str(tmp_path))
    db_path = tmp_path / 'database' / 'factory.db'
    initialize_database(db_path)
    monkeypatch.setattr(worker_module, 'DB_PATH', str(db_path))
    with open_db(db_path) as conn:
        _insert_task(conn, 'owned', 'fixture', status='queued', input_text='preserve request')
        _insert_task(conn, 'foreign', 'other', status='processing', updated_at=int(time.time()) - 400)
    context = multiprocessing.get_context('spawn')
    ready = context.Event()
    process = context.Process(target=_claim_then_wait, args=(str(db_path), ready))
    try:
        process.start()
        assert ready.wait(10)
        process.terminate()
        process.join(10)
        assert process.exitcode is not None and process.exitcode != 0
        with open_db(db_path) as conn:
            row = conn.execute('SELECT status, input_text FROM tasks WHERE task_id=?', ('owned',)).fetchone()
            assert row == ('processing', 'preserve request')
            conn.execute('UPDATE tasks SET updated_at=? WHERE task_id=?', (int(time.time()) - 400, 'owned'))
            conn.commit()
        worker = Worker.__new__(Worker)
        worker.profile = 'fixture'
        worker.cleanup_stuck_tasks(timeout_seconds=300)
        with open_db(db_path) as conn:
            row = conn.execute('SELECT status, input_text, error_message FROM tasks WHERE task_id=?', ('owned',)).fetchone()
            foreign = conn.execute('SELECT status FROM tasks WHERE task_id=?', ('foreign',)).fetchone()
        assert row[0:2] == ('dead_letter', 'preserve request')
        assert 'no heartbeat' in row[2]
        assert foreign == ('processing',)
    finally:
        if process.is_alive():
            process.terminate()
            process.join(10)
