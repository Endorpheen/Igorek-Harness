"""Manual cleanup retains rows and rechecks heartbeats before terminalizing tasks."""

from contextlib import contextmanager
import importlib.util
from pathlib import Path
import sqlite3
import sys
import time
from unittest.mock import Mock

import pytest

from factory.core.schema import initialize_database, open_db


@pytest.fixture
def cleanup(tmp_path, monkeypatch):
    root = Path(__import__('factory').__file__).resolve().parent
    spec = importlib.util.spec_from_file_location('_owned_manual_cleanup', root / 'cleanup_stuck_tasks.py')
    module = importlib.util.module_from_spec(spec)
    saved_path = sys.path[:]
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path[:] = saved_path
    db = str(tmp_path / 'owned.db')
    initialize_database(db)
    monkeypatch.setattr(module, 'DB_PATH', db)
    return module


def add(module, identifier, *, profile='a', status='processing', age=1000, text='owned fixture'):
    now = int(time.time())
    with open_db(module.DB_PATH) as conn:
        conn.execute('INSERT INTO tasks(task_id,profile,status,input_text,created_at,updated_at) VALUES(?,?,?,?,?,?)', (identifier, profile, status, text, now - age, now - age))


def rows(module):
    with open_db(module.DB_PATH) as conn:
        return {row[0]: row[1:] for row in conn.execute('SELECT task_id,status,error_message,completed_at FROM tasks')}


def test_cleanup_marks_only_old_processing_rows_in_requested_profile(cleanup):
    add(cleanup, 'old-a', text='x' * 101)
    add(cleanup, 'old-b', profile='b')
    add(cleanup, 'fresh', age=0)
    add(cleanup, 'queued', status='queued')
    add(cleanup, 'finished', status='ok')
    cleanup.cleanup_stuck_tasks(300, 'a')
    result = rows(cleanup)
    assert len(result) == 5 and result['old-a'][0] == 'dead_letter'
    assert 'profile a' in result['old-a'][1] and result['old-a'][2]
    assert result['old-b'][0] == result['fresh'][0] == 'processing'
    assert result['queued'][0] == 'queued' and result['finished'][0] == 'ok'


def test_no_match_and_sql_metacharacter_profile_do_not_touch_other_rows(cleanup, capsys):
    add(cleanup, 'old-a')
    cleanup.cleanup_stuck_tasks(300, "a' OR 1=1 --")
    assert rows(cleanup)['old-a'][0] == 'processing'
    assert 'не найдено' in capsys.readouterr().out


def test_legacy_missing_input_text_does_not_abort_cleanup(cleanup):
    add(cleanup, 'legacy', text=None)
    cleanup.cleanup_stuck_tasks(300, 'a')
    assert rows(cleanup)['legacy'][0] == 'dead_letter'


@pytest.mark.parametrize('changed', ['heartbeat', 'completed'])
def test_heartbeat_after_scan_prevents_stale_cleanup_update(cleanup, monkeypatch, capsys, changed):
    add(cleanup, 'revived')
    actual = open_db

    class Cursor:
        def __init__(self, cursor):
            self.cursor = cursor
        def execute(self, *args):
            self.cursor.execute(*args)
            return self
        def fetchall(self):
            result = self.cursor.fetchall()
            with actual(cleanup.DB_PATH) as heartbeat:
                if changed == "heartbeat":
                    heartbeat.execute("UPDATE tasks SET updated_at=strftime('%s','now') WHERE task_id='revived'")
                else:
                    heartbeat.execute("UPDATE tasks SET status='ok' WHERE task_id='revived'")
            return result
        @property
        def rowcount(self):
            return self.cursor.rowcount

    @contextmanager
    def racing(path):
        with actual(path) as connection:
            yield __import__('types').SimpleNamespace(cursor=lambda: Cursor(connection.cursor()), commit=connection.commit)

    monkeypatch.setattr(cleanup, 'open_db', racing)
    cleanup.cleanup_stuck_tasks(300, 'a')
    assert rows(cleanup)['revived'][0] == ('processing' if changed == 'heartbeat' else 'ok')
    assert '0 зависших задач помечено' in capsys.readouterr().out


def test_database_failure_is_reported_with_nonzero_exit(cleanup, monkeypatch, capsys):
    monkeypatch.setattr(cleanup, 'open_db', Mock(side_effect=sqlite3.OperationalError('fixture database unavailable')))
    with pytest.raises(SystemExit) as caught:
        cleanup.cleanup_stuck_tasks()
    assert caught.value.code == 1 and 'fixture database unavailable' in capsys.readouterr().out


@pytest.mark.parametrize('arguments,expected', [([], (300, None)), (['90'], (90, None)), (['90', 'owned-profile'], (90, 'owned-profile'))])
def test_main_passes_timeout_and_profile_without_using_real_database(cleanup, monkeypatch, arguments, expected):
    action = Mock()
    monkeypatch.setattr(cleanup, 'cleanup_stuck_tasks', action)
    monkeypatch.setattr(sys, 'argv', ['cleanup-stuck', *arguments])
    cleanup.main()
    action.assert_called_once_with(*expected)
