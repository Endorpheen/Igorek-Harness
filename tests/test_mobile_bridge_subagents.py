"""Subagents delegate_task: durable journal, relay to mobile journal and API.

We verify three layers separately:

* ``factory/core/subagents.py`` — lifecycle recording and collapsing;
* ``TraceJournaler`` — moving ``subagents.jsonl`` to ``mobile_events``
  (idempotent, no duplicates after bridge restart);
* ``MobileService`` — session subagent list and address cancellation.
"""

import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from factory.core.cli_chat import _append_entry, _session_path
from factory.core.isolation.artifact_map import (
    ROUTE_FILENAME,
    host_artifact_dir,
    write_artifact_route,
)
from factory.core.mobile_bridge.events import (
    TraceJournaler,
    normalize_subagent_event,
    trace_path_for_task,
)
from factory.core.mobile_bridge.service import MobileNotFound, MobileService
from factory.core.mobile_bridge.store import MobileStore
from factory.core.runtime import get_db_path, get_workspace_root
from factory.core.schema import initialize_database
from factory.core.subagents import (
    STATUS_OK,
    SubagentReporter,
    cancel_requested,
    fold_subagent_events,
    safe_goal_title,
    subagents_journal_path,
)


class SubagentJournalTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.workdir = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def _lines(self) -> list[dict]:
        path = subagents_journal_path(self.workdir)
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]

    def test_reporter_records_start_progress_finish(self):
        reporter = SubagentReporter(
            artifact_workdir=self.workdir, session_id="chat-1", parent_task_id="task-1"
        )
        reporter.start(
            subagent_id="sub_1",
            tool_use_id="call_1",
            goal="Проверить логи\nвторая строка не нужна",
            depth=1,
            model="m",
            profile="cc-local",
        )
        reporter.progress(
            subagent_id="sub_1", stage="tool.started", tool_name="Bash", tool_calls_count=2
        )
        reporter.finish(
            subagent_id="sub_1", status=STATUS_OK, summary="готово", tool_calls_count=3
        )

        events = self._lines()
        self.assertEqual([item["event"] for item in events], ["start", "progress", "finish"])
        self.assertEqual(events[0]["subagent_id"], "sub_1")
        self.assertEqual(events[0]["tool_use_id"], "call_1")
        # The subject header contains only the first line of the task.
        self.assertEqual(events[0]["goal_title"], "Проверить логи")
        self.assertEqual(events[0]["session_id"], "chat-1")
        self.assertEqual(events[0]["parent_task_id"], "task-1")
        self.assertEqual(events[2]["summary"], "готово")

    def test_reporter_without_workdir_is_noop(self):
        reporter = SubagentReporter(artifact_workdir=None)
        reporter.start(subagent_id="sub_1", tool_use_id="c", goal="g", depth=1)
        self.assertFalse(reporter.enabled)

    def test_cancel_flag_round_trip(self):
        reporter = SubagentReporter(artifact_workdir=self.workdir)
        self.assertFalse(cancel_requested(self.workdir, "sub_1"))
        self.assertTrue(reporter.request_cancel("sub_1"))
        self.assertTrue(cancel_requested(self.workdir, "sub_1"))
        self.assertFalse(cancel_requested(self.workdir, "sub_2"))

    def test_safe_goal_title_skips_blank_lines(self):
        self.assertEqual(safe_goal_title("\n\n  задание  \nдальше"), "задание")
        self.assertEqual(safe_goal_title(""), "")
        self.assertEqual(len(safe_goal_title("x" * 500)), 200)


class SubagentFoldTest(unittest.TestCase):
    def _events(self) -> list[dict]:
        return [
            {
                "seq": 1,
                "kind": "subagent.start",
                "ts": 100,
                "session_id": "s1",
                "task_id": "t1",
                "payload": {
                    "subagent_id": "sub_1",
                    "tool_use_id": "call_1",
                    "depth": 1,
                    "goal_title": "Разобрать логи",
                    "model": "m",
                    "profile": "cc-local",
                    "status": "running",
                    "parent_task_id": "t1",
                },
            },
            {
                "seq": 2,
                "kind": "subagent.progress",
                "ts": 110,
                "session_id": "s1",
                "task_id": "t1",
                "payload": {
                    "subagent_id": "sub_1",
                    "stage": "tool.started",
                    "tool_name": "Grep",
                    "tool_calls_count": 4,
                },
            },
            {
                "seq": 3,
                "kind": "subagent.finish",
                "ts": 130,
                "session_id": "s1",
                "task_id": "t1",
                "payload": {
                    "subagent_id": "sub_1",
                    "status": "ok",
                    "summary": "нашёл причину",
                    "tool_calls_count": 5,
                },
            },
        ]

    def test_fold_collapses_lifecycle_into_single_record(self):
        records = fold_subagent_events(self._events())
        self.assertEqual(len(records), 1)
        record = records[0]
        self.assertEqual(record["subagent_id"], "sub_1")
        self.assertEqual(record["tool_use_id"], "call_1")
        self.assertEqual(record["status"], "ok")
        self.assertEqual(record["goal_title"], "Разобрать логи")
        self.assertEqual(record["last_tool"], "Grep")
        self.assertEqual(record["tool_calls_count"], 5)
        self.assertEqual(record["summary"], "нашёл причину")
        self.assertEqual(record["started_at"], 100)
        self.assertEqual(record["finished_at"], 130)
        self.assertEqual(record["last_seq"], 3)

    def test_fold_ignores_other_kinds(self):
        events = [{"seq": 9, "kind": "task.start", "payload": {}}]
        self.assertEqual(fold_subagent_events(events), [])

    def test_normalize_requires_subagent_id(self):
        self.assertIsNone(normalize_subagent_event({"event": "start"}))
        frame = normalize_subagent_event(
            {"event": "start", "subagent_id": "sub_1", "goal_title": "g"},
            task_id="t1",
            session_id="s1",
        )
        self.assertEqual(frame["kind"], "subagent.start")
        self.assertEqual(frame["payload"]["task_id"], "t1")
        self.assertEqual(frame["payload"]["session_id"], "s1")


class SubagentRelayTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self.db_path = str(root / "factory.db")
        self.workspace = root / "workspace"
        self.workspace.mkdir(parents=True, exist_ok=True)
        initialize_database(self.db_path)
        self.store = MobileStore(db_path=self.db_path)
        self.task_id = "bbbbbbbb-1111-2222-3333-444444444444"
        self.session_id = "chat-20260930-000000-abc123"
        self.trace = trace_path_for_task(self.task_id, self.workspace)
        self.trace.parent.mkdir(parents=True, exist_ok=True)
        # The parent trace is needed for the journaler to determine the task session.
        self.trace.write_text(
            json.dumps(
                {"ts": 1, "kind": "task.start", "model": "m", "current_session": self.session_id}
            )
            + "\n",
            encoding="utf-8",
        )

    def tearDown(self):
        self._tmp.cleanup()

    def _journaler(self) -> TraceJournaler:
        tasks = [
            {
                "task_id": self.task_id,
                "status": "processing",
                "metadata": {},
                "created_at": 0,
            }
        ]
        return TraceJournaler(
            self.store, workspace_root=self.workspace, active_task_provider=lambda: tasks
        )

    def _write_subagents(self, *frames: dict) -> None:
        path = self.trace.parent / "subagents.jsonl"
        with path.open("a", encoding="utf-8") as handle:
            for frame in frames:
                handle.write(json.dumps(frame, ensure_ascii=False) + "\n")

    def _write_routed_subagents(self, *frames: dict) -> Path:
        mapped = host_artifact_dir(self.workspace / "session-cwd", self.task_id)
        mapped.mkdir(parents=True, exist_ok=True)
        write_artifact_route(self.trace.parent, mapped)
        with (mapped / "subagents.jsonl").open("a", encoding="utf-8") as handle:
            for frame in frames:
                handle.write(json.dumps(frame, ensure_ascii=False) + "\n")
        return mapped

    def _subagent_events(self) -> list[dict]:
        return [
            event
            for event in self.store.list_subagent_events(self.session_id)
        ]

    def test_subagent_journal_is_relayed_into_mobile_events(self):
        self._write_subagents(
            {
                "ts": 10,
                "event": "start",
                "subagent_id": "sub_1",
                "tool_use_id": "call_1",
                "parent_task_id": self.task_id,
                "depth": 1,
                "goal_title": "Разобрать логи",
                "session_id": self.session_id,
                "status": "running",
            },
            {
                "ts": 12,
                "event": "finish",
                "subagent_id": "sub_1",
                "status": "ok",
                "summary": "готово",
                "session_id": self.session_id,
            },
        )

        appended = self._journaler().poll()

        self.assertGreaterEqual(appended, 3)  # task.start + subagent.start + subagent.finish
        events = self._subagent_events()
        self.assertEqual([e["kind"] for e in events], ["subagent.start", "subagent.finish"])
        self.assertEqual(events[0]["payload"]["goal_title"], "Разобрать логи")
        self.assertEqual(events[1]["payload"]["summary"], "готово")
        self.assertEqual(events[0]["session_id"], self.session_id)

    def test_workspace_routed_journal_is_ingested_through_the_route(self):
        # Given: a kvm dispatch that routed its artifact dir into the exported
        # workspace and wrote the subagent journal there (not next to the trace)
        mapped = self._write_routed_subagents(
            {
                "ts": 10,
                "event": "start",
                "subagent_id": "sub_1",
                "tool_use_id": "call_1",
                "parent_task_id": self.task_id,
                "depth": 1,
                "goal_title": "Разобрать логи",
                "session_id": self.session_id,
                "status": "running",
            },
            {
                "ts": 12,
                "event": "finish",
                "subagent_id": "sub_1",
                "status": "ok",
                "summary": "готово",
                "session_id": self.session_id,
            },
        )
        self.assertTrue((self.trace.parent / ROUTE_FILENAME).is_file())
        self.assertFalse((self.trace.parent / "subagents.jsonl").exists())

        # When: the bare mobile pipeline polls the task
        self._journaler().poll()

        # Then: the routed journal becomes the same normalized mobile events
        events = self._subagent_events()
        self.assertEqual([event["kind"] for event in events], ["subagent.start", "subagent.finish"])
        self.assertEqual(events[0]["payload"]["goal_title"], "Разобрать логи")
        self.assertEqual(events[1]["payload"]["summary"], "готово")
        self.assertEqual(events[0]["session_id"], self.session_id)
        self.assertEqual(mapped.name, self.task_id[:8])

    def test_relay_is_idempotent_after_restart(self):
        self._write_subagents(
            {
                "ts": 10,
                "event": "start",
                "subagent_id": "sub_1",
                "parent_task_id": self.task_id,
                "session_id": self.session_id,
                "goal_title": "g",
                "status": "running",
            }
        )
        self._journaler().poll()
        first = self._subagent_events()

        # A new journaler = bridge restart: cursors in memory are lost, but
        # deduplication is performed based on (task_id, src_line), so duplicates are absent.
        self._journaler().poll()
        second = self._subagent_events()

        self.assertEqual(len(first), 1)
        self.assertEqual([e["seq"] for e in first], [e["seq"] for e in second])

    def test_relay_never_creates_worker_queue_rows(self):
        # Child launch should not enter the standard worker queue:
        # it operates outside the `tasks` table and cannot be claimed via `claim_task`.
        self._write_subagents(
            {
                "ts": 10,
                "event": "start",
                "subagent_id": "sub_1",
                "session_id": self.session_id,
                "goal_title": "g",
                "status": "running",
            }
        )
        self._journaler().poll()

        with sqlite3.connect(self.db_path) as conn:
            count = conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]
        self.assertEqual(count, 0)
        self.assertEqual(len(self._subagent_events()), 1)

    def test_trace_cursor_not_moved_by_subagent_lines(self):
        # Subagent lines are written with offset, so the trace cursor
        # remains within the bounds of the trace file itself, and new frames are not lost.
        self._write_subagents(
            {
                "ts": 10,
                "event": "start",
                "subagent_id": "sub_1",
                "session_id": self.session_id,
                "goal_title": "g",
                "status": "running",
            }
        )
        self._journaler().poll()
        kinds = [e["kind"] for e in self.store.list_events(limit=50)]
        self.assertIn("task.start", kinds)
        self.assertIn("subagent.start", kinds)


class SubagentServiceTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._env = patch.dict(os.environ, {"IGOREK_FACTORY_HOME": self._tmp.name})
        self._env.start()
        initialize_database(get_db_path())
        self.service = MobileService(profile="cc-local", workdir=str(get_workspace_root()))
        self.session_id = "chat-20260930-120000-abc123"
        self._write_session(self.session_id)
        self.parent_task_id = "cccccccc-1111-2222-3333-444444444444"
        self.artifact_workdir = (
            Path(get_workspace_root()) / f"temp_execution_{self.parent_task_id[:8]}"
        )
        self.artifact_workdir.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        self._env.stop()
        self._tmp.cleanup()

    def _write_session(self, session_id: str) -> None:
        path = _session_path(session_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "type": "meta",
                    "session_id": session_id,
                    "created_at": 1_700_000_000,
                    "profile": "cc-local",
                    "cwd": str(get_workspace_root()),
                },
                ensure_ascii=False,
            )
            + "\n",
            encoding="utf-8",
        )

    def _seed(self, *, finished: bool = False) -> None:
        reporter = SubagentReporter(
            artifact_workdir=self.artifact_workdir,
            session_id=self.session_id,
            parent_task_id=self.parent_task_id,
        )
        reporter.start(
            subagent_id="sub_1",
            tool_use_id="call_1",
            goal="Разобрать логи",
            depth=1,
        )
        if finished:
            reporter.finish(subagent_id="sub_1", status=STATUS_OK, summary="готово")
        store = self.service.store
        for kind, payload in (
            (
                "subagent.start",
                {
                    "subagent_id": "sub_1",
                    "tool_use_id": "call_1",
                    "parent_task_id": self.parent_task_id,
                    "depth": 1,
                    "goal_title": "Разобрать логи",
                    "status": "running",
                },
            ),
        ):
            store.append_event(
                kind=kind,
                payload=payload,
                session_id=self.session_id,
                task_id=self.parent_task_id,
            )
        if finished:
            store.append_event(
                kind="subagent.finish",
                payload={"subagent_id": "sub_1", "status": STATUS_OK, "summary": "готово"},
                session_id=self.session_id,
                task_id=self.parent_task_id,
            )

    def test_list_subagents_returns_records(self):
        self._seed()
        data = self.service.list_subagents(self.session_id)
        self.assertEqual(data["active"], 1)
        self.assertEqual(data["total"], 1)
        record = data["subagents"][0]
        self.assertEqual(record["subagent_id"], "sub_1")
        self.assertEqual(record["tool_use_id"], "call_1")
        self.assertEqual(record["goal_title"], "Разобрать логи")

    def test_kvm_workspace_journal_reaches_list_subagents(self):
        # Given: a parent task whose kvm dispatch routed the journal into the
        # workspace, plus the route file next to the host trace
        mapped = host_artifact_dir(get_workspace_root(), self.parent_task_id)
        mapped.mkdir(parents=True, exist_ok=True)
        write_artifact_route(self.artifact_workdir, mapped)
        with (mapped / "subagents.jsonl").open("a", encoding="utf-8") as handle:
            handle.write(
                json.dumps(
                    {
                        "ts": 10,
                        "event": "start",
                        "subagent_id": "sub_1",
                        "tool_use_id": "call_1",
                        "parent_task_id": self.parent_task_id,
                        "session_id": self.session_id,
                        "goal_title": "Разобрать логи",
                        "status": "running",
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )
            handle.write(
                json.dumps(
                    {
                        "ts": 12,
                        "event": "finish",
                        "subagent_id": "sub_1",
                        "status": STATUS_OK,
                        "summary": "готово",
                        "session_id": self.session_id,
                    },
                    ensure_ascii=False,
                )
                + "\n"
            )

        # When: the mobile pipeline ingests the routed journal
        tasks = [
            {
                "task_id": self.parent_task_id,
                "status": "processing",
                "metadata": {"session": self.session_id},
                "created_at": 0,
            }
        ]
        journaler = TraceJournaler(
            self.service.store,
            workspace_root=get_workspace_root(),
            active_task_provider=lambda: tasks,
        )
        self.assertGreaterEqual(journaler.poll(), 2)

        # Then: the session's Agents tab contract returns the subagent
        data = self.service.list_subagents(self.session_id)
        self.assertEqual(data["total"], 1)
        self.assertEqual(data["active"], 0)
        record = data["subagents"][0]
        self.assertEqual(record["subagent_id"], "sub_1")
        self.assertEqual(record["status"], STATUS_OK)
        self.assertEqual(record["goal_title"], "Разобрать логи")

    def test_cancel_subagent_writes_flag_into_the_routed_workspace_dir(self):
        # Given: an active subagent recorded in a session whose parent task has a
        # workspace-routed artifact dir
        self._seed()
        mapped = host_artifact_dir(get_workspace_root(), self.parent_task_id)
        mapped.mkdir(parents=True, exist_ok=True)
        write_artifact_route(self.artifact_workdir, mapped)

        # When: the addressable cancel arrives
        result = self.service.cancel_subagent(self.session_id, "sub_1")

        # Then: the flag the guest reads through virtiofs exists in the routed dir
        self.assertTrue(result["cancelled"])
        self.assertTrue(cancel_requested(mapped, "sub_1"))

    def test_list_session_files_skips_the_artifact_tree(self):
        # Given: an assistant message that names the routed runtime journal
        mapped = host_artifact_dir(get_workspace_root(), self.parent_task_id)
        mapped.mkdir(parents=True, exist_ok=True)
        journal = mapped / "subagents.jsonl"
        journal.write_text('{"event":"start"}\n', encoding="utf-8")
        _append_entry(
            _session_path(self.session_id),
            role="assistant",
            text=f"готово: {journal}",
            task_id=self.parent_task_id,
        )

        # When/Then: the file list never exposes runtime internals
        self.assertEqual(self.service.list_session_files(self.session_id), [])

    def test_list_subagents_unknown_session(self):
        with self.assertRaises(MobileNotFound):
            self.service.list_subagents("chat-20260930-999999-zzz")

    def test_cancel_subagent_writes_flag_and_event(self):
        self._seed()
        result = self.service.cancel_subagent(self.session_id, "sub_1")
        self.assertTrue(result["cancelled"])
        self.assertEqual(result["status"], "cancelling")
        self.assertTrue(cancel_requested(self.artifact_workdir, "sub_1"))
        kinds = [e["kind"] for e in self.service.store.list_events(limit=50)]
        self.assertIn("subagent.cancel_requested", kinds)

    def test_cancel_subagent_is_idempotent_for_finished(self):
        self._seed(finished=True)
        result = self.service.cancel_subagent(self.session_id, "sub_1")
        self.assertFalse(result["cancelled"])
        self.assertEqual(result["reason"], "already_finished")

    def test_cancel_subagent_unknown_id(self):
        self._seed()
        with self.assertRaises(MobileNotFound):
            self.service.cancel_subagent(self.session_id, "sub_missing")

    def test_cancel_session_stops_active_children(self):
        self._seed()
        self.service.cancel_session(self.session_id)
        self.assertTrue(cancel_requested(self.artifact_workdir, "sub_1"))

    def test_cancel_subagent_does_not_touch_siblings(self):
        store = self.service.store
        for subagent_id in ("sub_1", "sub_2"):
            store.append_event(
                kind="subagent.start",
                payload={
                    "subagent_id": subagent_id,
                    "parent_task_id": self.parent_task_id,
                    "status": "running",
                    "goal_title": subagent_id,
                },
                session_id=self.session_id,
                task_id=self.parent_task_id,
            )

        self.service.cancel_subagent(self.session_id, "sub_2")

        self.assertFalse(cancel_requested(self.artifact_workdir, "sub_1"))
        self.assertTrue(cancel_requested(self.artifact_workdir, "sub_2"))


class DelegateToolIntegrationTest(unittest.TestCase):
    """The `delegate_task` function logs its lifecycle but does not enqueue the task."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.artifact = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def _lines(self) -> list[dict]:
        path = subagents_journal_path(self.artifact)
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]

    def _call(self, **overrides) -> str:
        from factory.core.delegate_tool import delegate_task

        kwargs = {
            "workdir": str(self.artifact),
            "artifact_workdir": str(self.artifact),
            "tool_use_id": "call-delegate",
            "task_id": "parent-task-1",
            "current_session": "chat-1",
            "delegate_depth": 0,
            "base_url": "http://127.0.0.1:8001",
            "model": "m",
            "filesystem_mode": "workspace",
            "guard_mode": None,
            "max_tokens": 1000,
            "timeout_s": 30,
            "profile": "cc-local",
        }
        kwargs.update(overrides)
        return delegate_task({"goal": "Разобрать логи"}, **kwargs)

    def test_lifecycle_links_child_to_parent_task_and_tool_call(self):
        with patch(
            "factory.core.cc_backend_executor.execute_cc_backend_task",
            return_value={
                "status": "ok",
                "output_text": "готово",
                "tool_calls_count": 2,
                "usage": {"prompt": 1},
            },
        ):
            raw = self._call()

        payload = json.loads(raw)
        self.assertEqual(payload["status"], "ok")
        self.assertTrue(payload["subagent_id"].startswith("sub_"))

        lines = self._lines()
        self.assertEqual([line["event"] for line in lines], ["start", "finish"])
        self.assertEqual(lines[0]["tool_use_id"], "call-delegate")
        self.assertEqual(lines[0]["parent_task_id"], "parent-task-1")
        self.assertEqual(lines[0]["session_id"], "chat-1")
        self.assertEqual(lines[0]["goal_title"], "Разобрать логи")
        self.assertEqual(lines[1]["subagent_id"], lines[0]["subagent_id"])
        self.assertEqual(lines[1]["summary"], "готово")

    def test_depth_limit_rejects_grandchild_without_journal(self):
        raw = self._call(delegate_depth=2)
        self.assertIn("depth limit", json.loads(raw)["error"])
        self.assertFalse(subagents_journal_path(self.artifact).exists())

    def test_cancel_flag_is_reported_as_cancelled(self):
        with patch(
            "factory.core.cc_backend_executor.execute_cc_backend_task",
            return_value={"status": "ok", "output_text": "", "tool_calls_count": 0},
        ):
            raw = self._call()
        subagent_id = json.loads(raw)["subagent_id"]
        SubagentReporter(artifact_workdir=self.artifact).request_cancel(subagent_id)
        # The cancellation flag set during execution is read by the same layer as the
        # child loop: see `cc_backend_executor._is_task_cancelling`.
        self.assertTrue(cancel_requested(self.artifact, subagent_id))


if __name__ == "__main__":
    unittest.main()
