"""Persistent requeue of session message steering queue has regressions."""

import os
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from factory.core.runtime import get_db_path
from factory.core.schema import initialize_database
from factory.core import steer_queue


class SteerQueueTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._env = patch.dict(os.environ, {"IGOREK_FACTORY_HOME": self._tmp.name})
        self._env.start()
        initialize_database(get_db_path())

    def tearDown(self):
        self._env.stop()
        self._tmp.cleanup()

    def test_table_exists_in_schema(self):
        with sqlite3.connect(get_db_path()) as conn:
            row = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='session_steer_messages'"
            ).fetchone()
        self.assertIsNotNone(row)

    def test_drain_returns_messages_in_order_once(self):
        steer_queue.enqueue_steer(
            session_id="chat-1", task_id="task-1", text="первое", message_id="m1"
        )
        steer_queue.enqueue_steer(
            session_id="chat-1",
            task_id="task-1",
            text="второе",
            message_id="m2",
            input_files=["/tmp/a.png"],
            selected_skills=["news_rss"],
        )

        drained = steer_queue.drain_steer("task-1")
        self.assertEqual([item["text"] for item in drained], ["первое", "второе"])
        self.assertEqual(drained[1]["input_files"], ["/tmp/a.png"])
        self.assertEqual(drained[1]["selected_skills"], ["news_rss"])
        self.assertEqual(drained[1]["message_id"], "m2")
        # Repeated calls do not return messages that have already been delivered.
        self.assertEqual(steer_queue.drain_steer("task-1"), [])

    def test_pending_count_and_sessions_grouping(self):
        steer_queue.enqueue_steer(session_id="chat-1", task_id="task-1", text="a")
        steer_queue.enqueue_steer(session_id="chat-1", task_id="task-2", text="b")

        self.assertEqual(steer_queue.pending_steer_count("task-1"), 1)
        grouped = steer_queue.pending_steer_sessions()
        self.assertEqual(sorted(grouped), ["task-1", "task-2"])
        self.assertEqual(grouped["task-2"][0]["text"], "b")

        steer_queue.drain_steer("task-1")
        self.assertEqual(steer_queue.pending_steer_count("task-1"), 0)
        self.assertEqual(sorted(steer_queue.pending_steer_sessions()), ["task-2"])

    def test_provider_returns_callable_and_never_raises(self):
        steer_queue.enqueue_steer(session_id="chat-1", task_id="task-1", text="го")
        provider = steer_queue.make_steer_provider("task-1")

        self.assertEqual([item["text"] for item in provider()], ["го"])
        self.assertEqual(provider(), [])
        # An error in the storage should not disrupt the core execution flow of the task.
        broken = steer_queue.make_steer_provider("task-1")
        with patch.object(steer_queue, "drain_steer", side_effect=RuntimeError("boom")):
            self.assertEqual(broken(), [])

    def test_cancel_steer_removes_only_selected_message(self):
        steer_queue.enqueue_steer(
            session_id="chat-1", task_id="task-1", text="первое", message_id="m1"
        )
        steer_queue.enqueue_steer(
            session_id="chat-1", task_id="task-1", text="второе", message_id="m2"
        )
        steer_queue.enqueue_steer(
            session_id="chat-1", task_id="task-2", text="чужое", message_id="m3"
        )

        self.assertTrue(steer_queue.cancel_steer("task-1", "m2", "chat-1"))
        drained = steer_queue.drain_steer("task-1")
        # Cancellation removes exactly the selected message; the adjacent one remains unchanged.
        self.assertEqual([item["message_id"] for item in drained], ["m1"])
        # Adjacent task is unaffected.
        self.assertEqual(steer_queue.pending_steer_count("task-2"), 1)

    def test_cancel_steer_rejects_foreign_session_and_delivered_message(self):
        steer_queue.enqueue_steer(
            session_id="chat-1", task_id="task-1", text="го", message_id="m1"
        )
        # A foreign session cannot cancel a task message.
        self.assertFalse(steer_queue.cancel_steer("task-1", "m1", "chat-9"))
        # A message already delivered cannot be cancelled—it returns `False`.
        steer_queue.drain_steer("task-1")
        self.assertFalse(steer_queue.cancel_steer("task-1", "m1", "chat-1"))
        self.assertFalse(steer_queue.cancel_steer("", "m1", "chat-1"))

    def test_prioritize_after_delivery_does_not_collide_on_negative_id(self):
        steer_queue.enqueue_steer(
            session_id="chat-1", task_id="task-1", text="первое", message_id="m1"
        )
        steer_queue.enqueue_steer(
            session_id="chat-1", task_id="task-1", text="второе", message_id="m2"
        )
        self.assertTrue(steer_queue.prioritize_steer("task-1", "m1", "chat-1"))
        self.assertEqual(
            [item["message_id"] for item in steer_queue.drain_steer("task-1")],
            ["m1", "m2"],
        )
        # A delivered high-priority message leaves a negative ID in
        # the table: re-prioritization should not fall back on UNIQUE(id).
        steer_queue.enqueue_steer(
            session_id="chat-1", task_id="task-1", text="третье", message_id="m3"
        )
        self.assertTrue(steer_queue.prioritize_steer("task-1", "m3", "chat-1"))
        self.assertEqual(
            [item["message_id"] for item in steer_queue.drain_steer("task-1")],
            ["m3"],
        )

    def test_cancel_steer_reevaluates_priority_of_remaining(self):
        steer_queue.enqueue_steer(
            session_id="chat-1", task_id="task-1", text="первое", message_id="m1"
        )
        steer_queue.enqueue_steer(
            session_id="chat-1", task_id="task-1", text="второе", message_id="m2"
        )
        steer_queue.prioritize_steer("task-1", "m2", "chat-1")

        self.assertTrue(steer_queue.cancel_steer("task-1", "m2", "chat-1"))
        drained = steer_queue.drain_steer("task-1")
        self.assertEqual([item["message_id"] for item in drained], ["m1"])
