import os
import sqlite3
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from factory.core.cli_tasks import cancel_task, create_task, get_task
from factory.core.runtime import get_db_path
from factory.core.schema import initialize_database
from factory.core.tool_runtime_context import (
    clear_runtime_context,
    get_runtime_context,
    is_interrupt_requested,
    request_interrupt,
    set_runtime_context,
)

_TMP_DIR: tempfile.TemporaryDirectory | None = None
_ENV_PATCH = None


def setUpModule():
    """Isolate the data root: tests should not write tasks to the production database.

    Previously, the module created tasks with the ``test`` profile directly in
    ``~/.igorek-agent-factory/database/factory.db`` and left them in the ``cancelling`` status
    (since the ``test`` profile has no worker, they hung indefinitely and appeared in diagnostics
    as «zombie tasks").
    """
    global _TMP_DIR, _ENV_PATCH
    _TMP_DIR = tempfile.TemporaryDirectory()
    _ENV_PATCH = patch.dict(os.environ, {"IGOREK_FACTORY_HOME": _TMP_DIR.name})
    _ENV_PATCH.start()
    initialize_database(get_db_path())


def tearDownModule():
    global _TMP_DIR, _ENV_PATCH
    if _ENV_PATCH is not None:
        _ENV_PATCH.stop()
        _ENV_PATCH = None
    if _TMP_DIR is not None:
        _TMP_DIR.cleanup()
        _TMP_DIR = None


class CancelTaskTest(unittest.TestCase):
    def test_cancel_task_returns_true_for_processing_task(self):
        created = create_task(text="test cancel", profile="test")
        task_id = created["task_id"]
        # Simulate worker claiming the task
        db_path = initialize_database(get_db_path())
        with sqlite3.connect(db_path) as conn:
            conn.execute(
                "UPDATE tasks SET status = 'processing' WHERE task_id = ?",
                (task_id,),
            )
            conn.commit()

        result = cancel_task(task_id)
        self.assertTrue(result)
        task = get_task(task_id)
        self.assertEqual(task["status"], "cancelling")

    def test_cancel_task_finalizes_queued_task_directly(self):
        # A queued task was never claimed by the worker; it must be finalized
        # (not left in "cancelling"), otherwise waiters block forever.
        created = create_task(text="test cancel queued", profile="test")
        task_id = created["task_id"]

        result = cancel_task(task_id)
        self.assertTrue(result)
        task = get_task(task_id)
        self.assertEqual(task["status"], "fail")
        self.assertIn("cancelled", str(task.get("error_message") or "").lower())
        self.assertIsNotNone(task.get("completed_at"))

    def test_cancel_task_returns_false_for_completed_task(self):
        created = create_task(text="test cancel completed", profile="test")
        task_id = created["task_id"]
        db_path = initialize_database(get_db_path())
        with sqlite3.connect(db_path) as conn:
            conn.execute(
                "UPDATE tasks SET status = 'ok' WHERE task_id = ?",
                (task_id,),
            )
            conn.commit()

        result = cancel_task(task_id)
        self.assertFalse(result)

    def test_cancel_task_returns_false_for_unknown_task(self):
        result = cancel_task("nonexistent-task-id-00000000")
        self.assertFalse(result)

    def test_cancel_task_idempotent(self):
        created = create_task(text="test cancel idempotent", profile="test")
        task_id = created["task_id"]
        self.assertTrue(cancel_task(task_id))
        self.assertFalse(cancel_task(task_id))


class TaskIdRuntimeContextTest(unittest.TestCase):
    def setUp(self):
        clear_runtime_context()

    def tearDown(self):
        clear_runtime_context()

    def test_task_id_in_get_runtime_context(self):
        ctx = get_runtime_context()
        self.assertEqual(ctx["task_id"], "")

    def test_set_runtime_context_stores_task_id(self):
        set_runtime_context(task_id="abc-123")
        ctx = get_runtime_context()
        self.assertEqual(ctx["task_id"], "abc-123")

    def test_clear_runtime_context_removes_task_id(self):
        set_runtime_context(task_id="abc-123")
        clear_runtime_context()
        ctx = get_runtime_context()
        self.assertEqual(ctx["task_id"], "")

    def test_interrupt_event_works_with_task_id(self):
        event = threading.Event()
        set_runtime_context(task_id="t1", interrupt_event=event)
        self.assertFalse(is_interrupt_requested())
        request_interrupt()
        self.assertTrue(is_interrupt_requested())
        self.assertEqual(get_runtime_context()["task_id"], "t1")


class IsTaskCancellingTest(unittest.TestCase):
    """Test the logic that the executor uses to check for cancellation."""

    def test_is_task_cancelling_returns_false_when_no_task_id(self):
        from factory.core.cc_backend_executor import execute_cc_backend_task
        # _is_task_cancelling is a closure inside execute_cc_backend_task,
        # so we test the underlying building blocks instead.

        # No task_id set → should never detect cancellation
        clear_runtime_context()
        set_runtime_context(task_id="")
        task = get_task("nonexistent")
        self.assertIsNone(task)

    def test_cancelled_task_has_cancelling_status(self):
        created = create_task(text="test cancelling detection", profile="test")
        task_id = created["task_id"]
        db_path = initialize_database(get_db_path())
        with sqlite3.connect(db_path) as conn:
            conn.execute(
                "UPDATE tasks SET status = 'processing' WHERE task_id = ?",
                (task_id,),
            )
            conn.commit()
        cancel_task(task_id)
        task = get_task(task_id)
        self.assertIsNotNone(task)
        self.assertEqual(task["status"], "cancelling")


if __name__ == "__main__":
    unittest.main()
