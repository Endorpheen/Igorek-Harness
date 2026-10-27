import multiprocessing
import time
from contextlib import contextmanager

import pytest

from factory.core import worker as worker_module
from factory.core.schema import initialize_database, open_db
from factory.core.worker import Worker
from test_worker_stuck_task_recovery import _insert_task


def _cleanup_process(db_path, selected, resume):
    from factory.core import worker as module

    module.DB_PATH = db_path
    original = module.open_db

    class Cursor:
        def __init__(self, inner):
            self.inner = inner
            self.selection = False

        def execute(self, statement, parameters=()):
            self.selection = 'SELECT task_id, profile, status, input_text, created_at' in statement
            self.inner.execute(statement, parameters)
            return self

        def fetchall(self):
            rows = self.inner.fetchall()
            if self.selection:
                selected.set()
                if not resume.wait(10):
                    raise TimeoutError('heartbeat rendezvous timed out')
            return rows

        def __getattr__(self, name):
            return getattr(self.inner, name)

    class Connection:
        def __init__(self, inner):
            self.inner = inner

        def cursor(self):
            return Cursor(self.inner.cursor())

        def __getattr__(self, name):
            return getattr(self.inner, name)

    @contextmanager
    def instrumented_open(path):
        with original(path) as conn:
            yield Connection(conn)

    module.open_db = instrumented_open
    cleaner = Worker.__new__(Worker)
    cleaner.profile = 'fixture'
    cleaner.cleanup_stuck_tasks(timeout_seconds=300)


@pytest.mark.parametrize('status', ['processing', 'cancelling'])
def test_heartbeat_after_stale_selection_prevents_finalization(tmp_path, monkeypatch, status):
    monkeypatch.setenv('IGOREK_FACTORY_HOME', str(tmp_path))
    monkeypatch.setenv('FACTORY_DATA_DIR', str(tmp_path))
    db_path = tmp_path / 'database' / 'factory.db'
    initialize_database(db_path)
    monkeypatch.setattr(worker_module, 'DB_PATH', str(db_path))
    with open_db(db_path) as conn:
        _insert_task(conn, 'live-task', 'fixture', status=status, updated_at=int(time.time()) - 400)
    context = multiprocessing.get_context('spawn')
    selected, resume = context.Event(), context.Event()
    process = context.Process(target=_cleanup_process, args=(str(db_path), selected, resume))
    try:
        process.start()
        assert selected.wait(10)
        worker = Worker.__new__(Worker)
        worker.profile = 'fixture'
        assert worker._touch_task('live-task')
        with open_db(db_path) as conn:
            updated_at, = conn.execute('SELECT updated_at FROM tasks WHERE task_id=?', ('live-task',)).fetchone()
        assert updated_at >= int(time.time()) - 5
        resume.set()
        process.join(10)
        assert process.exitcode == 0
        with open_db(db_path) as conn:
            row = conn.execute('SELECT status, error_message, completed_at FROM tasks WHERE task_id=?', ('live-task',)).fetchone()
        assert row == (status, None, None)
    finally:
        resume.set()
        if process.is_alive():
            process.terminate()
            process.join(10)
