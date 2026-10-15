import tempfile
import threading
import unittest
from unittest.mock import patch

from factory.core.schema import db_tables_check, initialize_database
from factory.core.user_input_store import (
    create_user_input_request,
    get_user_input_request,
    list_pending_user_input_requests,
    resolve_user_input_request,
    wait_for_user_input_response,
)


class UserInputStoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db_path = f"{self.tmp.name}/factory.db"
        self.path_patch = patch(
            "factory.core.user_input_store.get_db_path", return_value=self.db_path
        )
        self.path_patch.start()
        self.addCleanup(self.path_patch.stop)

    def test_schema_contains_user_input_requests(self):
        initialize_database(self.db_path)
        import sqlite3

        with sqlite3.connect(self.db_path) as conn:
            self.assertTrue(db_tables_check(conn)["user_input_requests"])

    def test_create_list_submit_lifecycle(self):
        created = create_user_input_request(
            task_id="task-1", payload={"questions": [{"id": "name"}]}
        )
        self.assertEqual(created["status"], "pending")
        self.assertEqual(
            [item["request_id"] for item in list_pending_user_input_requests(task_id="task-1")],
            [created["request_id"]],
        )

        resolved = resolve_user_input_request(
            created["request_id"], response={"answers": {"name": {"text_value": "Alice"}}}
        )
        self.assertEqual(resolved["status"], "submitted")
        self.assertEqual(
            resolved["response"]["answers"]["name"]["text_value"], "Alice"
        )
        self.assertEqual(list_pending_user_input_requests(task_id="task-1"), [])

    def test_resolve_is_single_use(self):
        created = create_user_input_request(task_id="task-1", payload={})
        self.assertIsNotNone(
            resolve_user_input_request(created["request_id"], response={"answers": {}})
        )
        self.assertIsNone(
            resolve_user_input_request(created["request_id"], response={"answers": {}})
        )

    def test_wait_returns_submitted_response(self):
        created = create_user_input_request(task_id="task-1", payload={})

        def submit():
            resolve_user_input_request(
                created["request_id"], response={"answers": {"x": {"text_value": "ok"}}}
            )

        timer = threading.Timer(0.01, submit)
        timer.start()
        try:
            response = wait_for_user_input_response(
                created["request_id"], timeout_s=1, interval_s=0.01
            )
        finally:
            timer.join(timeout=1)
        self.assertEqual(response["answers"]["x"]["text_value"], "ok")

    def test_wait_returns_none_after_cancel(self):
        created = create_user_input_request(task_id="task-1", payload={})
        resolve_user_input_request(created["request_id"], response=None, cancelled=True)
        self.assertIsNone(wait_for_user_input_response(created["request_id"], timeout_s=1))

    def test_get_unknown_returns_none(self):
        self.assertIsNone(get_user_input_request("missing"))


if __name__ == "__main__":
    unittest.main()
