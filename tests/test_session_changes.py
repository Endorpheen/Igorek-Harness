"""Tests for per-task changed-file stats and per-session aggregation."""

import json
import os
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from factory.core.cc_backend_tools import execute_tool
from factory.core.checkpoint_manager import CheckpointManager
from factory.core.cli_chat import _session_path
from factory.core.cli_tasks import create_task
from factory.core.isolation.artifact_map import ARTIFACT_DIRNAME
from factory.core.isolation.persistence import DURABLE_DATA_DIRNAME
from factory.core.isolation.status import ISOLATION_KVM, ISOLATION_NONE
from factory.core.mobile_bridge.service import MobileNotFound, MobileService
from factory.core.runtime import get_db_path
from factory.core.schema import initialize_database
from factory.core.session_changes import (
    TaskChangeTracker,
    aggregate_changes,
    count_line_changes,
    is_internal_path,
    load_task_changes,
    write_task_changes,
)
from factory.core.session_diff import MAX_DIFF_LINES, build_diff_hunks


def _write_file(workdir: str, relative: str, content: str) -> str:
    path = Path(workdir) / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return str(path)


class CountLineChangesTest(unittest.TestCase):
    def test_counts_insertions_and_deletions(self):
        self.assertEqual(count_line_changes("a\nb\n", "a\nb\nc\n"), (1, 0))
        self.assertEqual(count_line_changes("a\nb\n", "a\n"), (0, 1))
        self.assertEqual(count_line_changes("a\nb\n", "a\nc\n"), (1, 1))

    def test_identical_text_has_no_changes(self):
        self.assertEqual(count_line_changes("same\n", "same\n"), (0, 0))


class BuildDiffHunksTest(unittest.TestCase):
    def test_single_line_change_keeps_explicit_range_counts(self):
        hunks, truncated = build_diff_hunks("x\n", "y\n")

        self.assertFalse(truncated)
        self.assertEqual(hunks[0]["header"], "@@ -1,1 +1,1 @@")
        self.assertEqual(
            hunks[0]["lines"],
            [{"type": "-", "text": "x"}, {"type": "+", "text": "y"}],
        )

    def test_identical_text_produces_no_hunks(self):
        hunks, truncated = build_diff_hunks("same\n", "same\n")

        self.assertEqual(hunks, [])
        self.assertFalse(truncated)

    def test_body_is_clipped_to_max_lines(self):
        old_text = "".join(f"a{index}\n" for index in range(10))
        new_text = "".join(f"b{index}\n" for index in range(2500))

        hunks, truncated = build_diff_hunks(old_text, new_text, max_lines=100)

        self.assertTrue(truncated)
        self.assertEqual(sum(len(hunk["lines"]) for hunk in hunks), 100)


class InternalPathTest(unittest.TestCase):
    def test_runtime_artifact_dirs_are_internal(self):
        self.assertTrue(is_internal_path(".checkpoints/snap.json"))
        self.assertTrue(is_internal_path(".tool_results/tool.txt"))
        self.assertTrue(is_internal_path("nested/.codex/log.json"))
        self.assertFalse(is_internal_path("src/main.py"))


class TrackerFallbackTest(unittest.TestCase):
    """No checkpoint available — stats derive from Write/Edit tool input."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._env = patch.dict(os.environ, {"IGOREK_FACTORY_HOME": self._tmp.name})
        self._env.start()
        self.workdir = tempfile.mkdtemp()

    def tearDown(self):
        self._env.stop()
        self._tmp.cleanup()

    def test_write_reports_all_lines_added(self):
        path = _write_file(self.workdir, "note.txt", "stale")
        tracker = TaskChangeTracker(task_id="task-fallback", workdir=self.workdir, checkpoint_manager=None)

        execute_tool(
            "Write",
            {"file_path": path, "content": "one\ntwo\nthree\n"},
            self.workdir,
            filesystem_mode="workspace",
            change_tracker=tracker,
        )

        record = load_task_changes("task-fallback", data_root=self._tmp.name)
        self.assertEqual(
            record["files"],
            [{"path": "note.txt", "additions": 3, "deletions": 0, "status": "added"}],
        )

    def test_edit_accumulates_from_old_and_new_string(self):
        path = _write_file(self.workdir, "a.py", "l1\nl2\nl3\n")
        tracker = TaskChangeTracker(task_id="task-fallback", workdir=self.workdir, checkpoint_manager=None)

        execute_tool(
            "Write",
            {"file_path": path, "content": "l1\nl2\nl3\n"},
            self.workdir,
            filesystem_mode="workspace",
            change_tracker=tracker,
        )
        execute_tool(
            "Edit",
            {"file_path": path, "old_string": "l2\n", "new_string": "l2\nl2b\n"},
            self.workdir,
            filesystem_mode="workspace",
            change_tracker=tracker,
        )

        record = load_task_changes("task-fallback", data_root=self._tmp.name)
        self.assertEqual(
            record["files"],
            [{"path": "a.py", "additions": 4, "deletions": 0, "status": "modified"}],
        )

    def test_internal_artifacts_are_not_recorded(self):
        path = _write_file(self.workdir, ".tool_results/tool_out.txt", "x")
        tracker = TaskChangeTracker(task_id="task-fallback", workdir=self.workdir, checkpoint_manager=None)

        execute_tool(
            "Write",
            {"file_path": path, "content": "x\ny\n"},
            self.workdir,
            filesystem_mode="workspace",
            change_tracker=tracker,
        )

        self.assertIsNone(load_task_changes("task-fallback", data_root=self._tmp.name))


class TrackerCheckpointTest(unittest.TestCase):
    """A real CheckpointManager supplies the pre-change baseline via git."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._env = patch.dict(os.environ, {"IGOREK_FACTORY_HOME": self._tmp.name})
        self._env.start()
        self.workdir = tempfile.mkdtemp()

    def tearDown(self):
        self._env.stop()
        self._tmp.cleanup()

    def test_existing_file_is_modified_against_snapshot(self):
        path = _write_file(self.workdir, "app.txt", "one\ntwo\n")
        checkpoint = CheckpointManager()
        tracker = TaskChangeTracker(
            task_id="task-cp", workdir=self.workdir, checkpoint_manager=checkpoint
        )

        execute_tool(
            "Write",
            {"file_path": path, "content": "one\ntwo\nthree\n"},
            self.workdir,
            filesystem_mode="workspace",
            checkpoint_manager=checkpoint,
            change_tracker=tracker,
        )

        record = load_task_changes("task-cp", data_root=self._tmp.name)
        self.assertEqual(
            record["files"],
            [{"path": "app.txt", "additions": 1, "deletions": 0, "status": "modified"}],
        )

    def test_new_file_is_added_and_repeated_write_stays_net(self):
        checkpoint = CheckpointManager()
        tracker = TaskChangeTracker(
            task_id="task-cp", workdir=self.workdir, checkpoint_manager=checkpoint
        )
        new_path = str(Path(self.workdir) / "created.txt")

        execute_tool(
            "Write",
            {"file_path": new_path, "content": "a\nb\n"},
            self.workdir,
            filesystem_mode="workspace",
            checkpoint_manager=checkpoint,
            change_tracker=tracker,
        )
        execute_tool(
            "Write",
            {"file_path": new_path, "content": "a\nb\nc\nd\n"},
            self.workdir,
            filesystem_mode="workspace",
            checkpoint_manager=checkpoint,
            change_tracker=tracker,
        )

        record = load_task_changes("task-cp", data_root=self._tmp.name)
        self.assertEqual(
            record["files"],
            [{"path": "created.txt", "additions": 4, "deletions": 0, "status": "added"}],
        )


class AggregateChangesTest(unittest.TestCase):
    def setUp(self):
        self.workdir = "/tmp/session-aggregate-workdir"

    def test_dedupe_sums_and_last_status_wins(self):
        records = [
            {
                "task_id": "t1",
                "workdir": self.workdir,
                "files": [
                    {"path": "a.txt", "additions": 5, "deletions": 0, "status": "added"},
                    {"path": "b.txt", "additions": 2, "deletions": 1, "status": "modified"},
                ],
            },
            {
                "task_id": "t2",
                "workdir": self.workdir,
                "files": [
                    {"path": "a.txt", "additions": 3, "deletions": 1, "status": "modified"},
                    {"path": "b.txt", "additions": 0, "deletions": 2, "status": "deleted"},
                    {"path": "sub/c.txt", "additions": 4, "deletions": 0, "status": "added"},
                ],
            },
        ]

        result = aggregate_changes(records, session_workdir=self.workdir)

        self.assertEqual(
            result,
            {
                "files": [
                    {"path": "a.txt", "additions": 8, "deletions": 1, "status": "modified"},
                    {"path": "b.txt", "additions": 2, "deletions": 3, "status": "deleted"},
                    {"path": "sub/c.txt", "additions": 4, "deletions": 0, "status": "added"},
                ],
                "totals": {"files": 3, "additions": 14, "deletions": 4},
            },
        )

    def test_internal_dirs_and_invalid_statuses_are_excluded(self):
        records = [
            {
                "task_id": "t1",
                "workdir": self.workdir,
                "files": [
                    {"path": ".tool_results/x.txt", "additions": 9, "deletions": 0, "status": "added"},
                    {"path": ".checkpoints/y.json", "additions": 9, "deletions": 0, "status": "modified"},
                    {"path": ".codex/z.log", "additions": 9, "deletions": 0, "status": "added"},
                    {"path": "keep.md", "additions": 1, "deletions": 0, "status": "added"},
                    {"path": "bad.txt", "additions": 9, "deletions": 0, "status": "nonsense"},
                ],
            }
        ]

        result = aggregate_changes(records, session_workdir=self.workdir)

        self.assertEqual(result["files"], [{"path": "keep.md", "additions": 1, "deletions": 0, "status": "added"}])
        self.assertEqual(result["totals"], {"files": 1, "additions": 1, "deletions": 0})

    def test_escaping_and_absolute_outside_paths_are_excluded(self):
        records = [
            {
                "task_id": "t1",
                "workdir": self.workdir,
                "files": [
                    {"path": "../escape.py", "additions": 10, "deletions": 0, "status": "added"},
                    {"path": "/tmp/outside/absolute.py", "additions": 20, "deletions": 0, "status": "added"},
                    {"path": "sub/file.py", "additions": 3, "deletions": 1, "status": "modified"},
                ],
            }
        ]

        result = aggregate_changes(records, session_workdir=self.workdir)

        self.assertEqual(
            result,
            {
                "files": [
                    {"path": "sub/file.py", "additions": 3, "deletions": 1, "status": "modified"},
                ],
                "totals": {"files": 1, "additions": 3, "deletions": 1},
            },
        )

    def test_nested_task_workdir_normalizes_relative_to_session(self):
        nested = os.path.join(self.workdir, "sub")
        records = [
            {
                "task_id": "t1",
                "workdir": nested,
                "files": [
                    {"path": "file.py", "additions": 4, "deletions": 0, "status": "added"},
                    {"path": "../../outside.py", "additions": 9, "deletions": 0, "status": "added"},
                ],
            }
        ]

        result = aggregate_changes(records, session_workdir=self.workdir)

        self.assertEqual(
            result,
            {
                "files": [
                    {"path": "sub/file.py", "additions": 4, "deletions": 0, "status": "added"},
                ],
                "totals": {"files": 1, "additions": 4, "deletions": 0},
            },
        )

    def test_empty_records_return_zero_totals(self):
        result = aggregate_changes([], session_workdir=self.workdir)

        self.assertEqual(result, {"files": [], "totals": {"files": 0, "additions": 0, "deletions": 0}})


class _SessionServiceHarness(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._env = patch.dict(os.environ, {"IGOREK_FACTORY_HOME": self._tmp.name})
        self._env.start()
        initialize_database(get_db_path())
        self.workdir = tempfile.mkdtemp()
        self.service = MobileService(profile="cc-local-8081", workdir=self.workdir)
        self.session_id = "chat-20260921-120000-changes"
        self._write_session(self.session_id)

    def tearDown(self):
        self._env.stop()
        self._tmp.cleanup()

    def _write_session(self, session_id: str) -> Path:
        path = _session_path(session_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "type": "meta",
                    "session_id": session_id,
                    "profile": "cc-local-8081",
                    "cwd": self.workdir,
                }
            )
            + "\n",
            encoding="utf-8",
        )
        return path

    def _task(self, *, created_at: int) -> str:
        created = create_task(
            text="измени файлы",
            profile="cc-local-8081",
            metadata={"session": self.session_id, "source": "mobile_bridge", "cwd": self.workdir},
        )
        task_id = created["task_id"]
        with sqlite3.connect(get_db_path()) as conn:
            conn.execute("UPDATE tasks SET created_at = ? WHERE task_id = ?", (created_at, task_id))
        return task_id

class ServiceSessionChangesTest(_SessionServiceHarness):
    def test_aggregates_across_session_tasks(self):
        first = self._task(created_at=100)
        second = self._task(created_at=200)
        write_task_changes(
            {
                "task_id": first,
                "workdir": self.workdir,
                "files": [
                    {"path": "a.txt", "additions": 2, "deletions": 0, "status": "added"},
                    {"path": "b.txt", "additions": 1, "deletions": 1, "status": "modified"},
                ],
            },
            data_root=self._tmp.name,
        )
        write_task_changes(
            {
                "task_id": second,
                "workdir": self.workdir,
                "files": [
                    {"path": "a.txt", "additions": 3, "deletions": 1, "status": "modified"},
                    {"path": ".tool_results/ignored.txt", "additions": 5, "deletions": 0, "status": "added"},
                ],
            },
            data_root=self._tmp.name,
        )

        result = self.service.session_changes(self.session_id)

        self.assertEqual(
            result,
            {
                "files": [
                    {"path": "a.txt", "additions": 5, "deletions": 1, "status": "modified"},
                    {"path": "b.txt", "additions": 1, "deletions": 1, "status": "modified"},
                ],
                "totals": {"files": 2, "additions": 6, "deletions": 2},
            },
        )

    def test_empty_session_returns_zero_totals(self):
        self._task(created_at=100)

        result = self.service.session_changes(self.session_id)

        self.assertEqual(result, {"files": [], "totals": {"files": 0, "additions": 0, "deletions": 0}})

    def test_unknown_session_raises_not_found(self):
        with self.assertRaises(MobileNotFound):
            self.service.session_changes("chat-does-not-exist")


class ServiceSessionChangesDurableRootTest(_SessionServiceHarness):
    """Caller wiring: the session feeds its durable root into aggregation (todo 44)."""

    def _service_for_status(self, status: str) -> MobileService:
        profiles = Path(self._tmp.name) / "profiles"
        profiles.mkdir(exist_ok=True)
        (profiles / "cc-local-8081.json").write_text(
            json.dumps({"backend": "cc-backend"}), encoding="utf-8"
        )
        return MobileService(
            profile="cc-local-8081",
            workdir=self.workdir,
            isolation_status=lambda: status,
        )

    def _durable_dir(self) -> str:
        return str(Path(self.workdir) / DURABLE_DATA_DIRNAME)

    def _record_durable(self, task_id: str, files: list[dict[str, object]]) -> None:
        write_task_changes(
            {"task_id": task_id, "workdir": "/workspace", "files": files},
            data_root=self._durable_dir(),
        )

    def _record_durable_and_user_files(self) -> None:
        task = self._task(created_at=100)
        write_task_changes(
            {
                "task_id": task,
                "workdir": self.workdir,
                "files": [
                    {
                        "path": f"{DURABLE_DATA_DIRNAME}/session_changes/leak.json",
                        "additions": 3,
                        "deletions": 0,
                        "status": "added",
                    },
                    {
                        "path": "src/main.py",
                        "additions": 2,
                        "deletions": 1,
                        "status": "modified",
                    },
                ],
            },
            data_root=self._tmp.name,
        )

    def test_kvm_session_drops_durable_root_paths_from_persisted_records(self):
        # Given: an operational kvm session with a persisted durable-root record
        self._record_durable_and_user_files()
        service = self._service_for_status(ISOLATION_KVM)

        # When: the session folds its records for the mobile client
        result = service.session_changes(self.session_id)

        # Then: the runtime artifact is filtered, the user file stays
        self.assertEqual(
            [item["path"] for item in result["files"]],
            ["src/main.py"],
        )

    def test_off_session_keeps_durable_dirname_paths_from_persisted_records(self):
        # Given: an off-mode session with the same persisted record
        self._record_durable_and_user_files()
        service = self._service_for_status(ISOLATION_NONE)

        # When: the session folds its records
        result = service.session_changes(self.session_id)

        # Then: off aggregation is unchanged - the directory name is user data
        self.assertEqual(
            [item["path"] for item in result["files"]],
            [f"{DURABLE_DATA_DIRNAME}/session_changes/leak.json", "src/main.py"],
        )

    def test_kvm_task_record_inside_durable_root_is_aggregated(self):
        # Given: a kvm task whose record only exists in the workspace durable
        # root, written by the guest tracker with the guest workdir (/workspace)
        task = self._task(created_at=100)
        self._record_durable(
            task,
            [
                {"path": "web-ui-impressions.txt", "additions": 45, "deletions": 0, "status": "added"},
                {
                    "path": f"{DURABLE_DATA_DIRNAME}/session_changes/leak.json",
                    "additions": 3,
                    "deletions": 0,
                    "status": "added",
                },
            ],
        )
        service = self._service_for_status(ISOLATION_KVM)

        # When: the host folds the session records
        result = service.session_changes(self.session_id)

        # Then: the guest record surfaces with correct stats, its durable
        # artifact path stays filtered, and the host data root is not required
        self.assertEqual(
            result,
            {
                "files": [
                    {
                        "path": "web-ui-impressions.txt",
                        "additions": 45,
                        "deletions": 0,
                        "status": "added",
                    }
                ],
                "totals": {"files": 1, "additions": 45, "deletions": 0},
            },
        )

    def test_host_record_wins_over_durable_record_for_the_same_task(self):
        # Given: the same task has a host record and a durable-root record
        task = self._task(created_at=100)
        self._record_durable(
            task,
            [{"path": "durable.txt", "additions": 1, "deletions": 0, "status": "added"}],
        )
        write_task_changes(
            {
                "task_id": task,
                "workdir": self.workdir,
                "files": [{"path": "host.txt", "additions": 2, "deletions": 0, "status": "added"}],
            },
            data_root=self._tmp.name,
        )
        service = self._service_for_status(ISOLATION_KVM)

        # When: the session folds the records
        result = service.session_changes(self.session_id)

        # Then: the host-side record is authoritative, the durable one is ignored
        self.assertEqual([item["path"] for item in result["files"]], ["host.txt"])

    def test_off_session_ignores_durable_root_records(self):
        # Given: a durable-root record and an off-mode runtime (no kvm)
        task = self._task(created_at=100)
        self._record_durable(
            task,
            [{"path": "guest.txt", "additions": 4, "deletions": 0, "status": "added"}],
        )
        service = self._service_for_status(ISOLATION_NONE)

        # When: the session folds the records
        result = service.session_changes(self.session_id)

        # Then: off mode stays byte-identical - only host records are read
        self.assertEqual(result, {"files": [], "totals": {"files": 0, "additions": 0, "deletions": 0}})

    def test_kvm_durable_added_record_yields_full_add_diff(self):
        # Given: a file created by a kvm task, recorded only in the durable root
        task = self._task(created_at=100)
        _write_file(self.workdir, "new.txt", "alpha\nbeta\n")
        self._record_durable(
            task,
            [{"path": "new.txt", "additions": 2, "deletions": 0, "status": "added"}],
        )
        service = self._service_for_status(ISOLATION_KVM)

        # When: the client asks for the diff
        result = service.session_changes_diff(self.session_id, "new.txt")

        # Then: without a host baseline a new file still renders as full addition
        self.assertEqual(result["source"], "added")
        self.assertEqual((result["additions"], result["deletions"]), (2, 0))
        lines = [line for hunk in result["hunks"] for line in hunk["lines"]]
        self.assertEqual([line["text"] for line in lines], ["alpha", "beta"])
        self.assertTrue(all(line["type"] == "+" for line in lines))


class ServiceSessionChangesArtifactDirTest(_SessionServiceHarness):
    """Caller wiring: the kvm guest artifact tree never reaches the client."""

    def _service_for_status(self, status: str) -> MobileService:
        profiles = Path(self._tmp.name) / "profiles"
        profiles.mkdir(exist_ok=True)
        (profiles / "cc-local-8081.json").write_text(
            json.dumps({"backend": "cc-backend"}), encoding="utf-8"
        )
        return MobileService(
            profile="cc-local-8081",
            workdir=self.workdir,
            isolation_status=lambda: status,
        )

    def _record_with_artifact(self) -> None:
        task = self._task(created_at=100)
        write_task_changes(
            {
                "task_id": task,
                "workdir": self.workdir,
                "files": [
                    {
                        "path": f"{ARTIFACT_DIRNAME}/12345678/subagents.jsonl",
                        "additions": 3,
                        "deletions": 0,
                        "status": "added",
                    },
                    {
                        "path": "src/main.py",
                        "additions": 2,
                        "deletions": 1,
                        "status": "modified",
                    },
                ],
            },
            data_root=self._tmp.name,
        )

    def test_kvm_session_drops_artifact_tree_paths_and_recounts_totals(self):
        # Given: an operational kvm session with a persisted artifact path
        self._record_with_artifact()
        service = self._service_for_status(ISOLATION_KVM)

        # When: the session folds its records for the mobile client
        result = service.session_changes(self.session_id)

        # Then: the runtime artifact is filtered and the totals are recomputed
        self.assertEqual([item["path"] for item in result["files"]], ["src/main.py"])
        self.assertEqual(
            result["totals"], {"files": 1, "additions": 2, "deletions": 1}
        )

    def test_off_session_keeps_the_artifact_dirname_as_user_data(self):
        # Given: an off-mode session with the same persisted record
        self._record_with_artifact()
        service = self._service_for_status(ISOLATION_NONE)

        # When: the session folds its records
        result = service.session_changes(self.session_id)

        # Then: off aggregation is unchanged - the directory name is user data
        self.assertEqual(
            [item["path"] for item in result["files"]],
            [f"{ARTIFACT_DIRNAME}/12345678/subagents.jsonl", "src/main.py"],
        )

    def test_kvm_session_diff_refuses_an_artifact_path(self):
        # Given: a kvm session and a runtime artifact path inside the workspace
        self._record_with_artifact()
        service = self._service_for_status(ISOLATION_KVM)

        # When/Then: the path is not a user file even before a diff lookup
        with self.assertRaises(MobileNotFound):
            service.session_changes_diff(
                self.session_id, f"{ARTIFACT_DIRNAME}/12345678/subagents.jsonl"
            )


class ServiceSessionChangesDiffTest(_SessionServiceHarness):
    def test_large_sparse_file_degrades_without_loading_its_whole_body(self):
        import tracemalloc
        from factory.core.session_diff import MAX_DIFF_FILE_BYTES
        path = _write_file(self.workdir, 'large.txt', 'old fixture\n')
        self._snapshot()
        with open(path, 'wb') as stream:
            stream.write(b'x' * (MAX_DIFF_FILE_BYTES + 1))
            stream.truncate(64 * 1024 * 1024)
        task = self._task(created_at=100)
        self._record(task, path='large.txt', additions=1, deletions=1)
        tracemalloc.start()
        try:
            result = self.service.session_changes_diff(self.session_id, 'large.txt')
            _, peak = tracemalloc.get_traced_memory()
        finally:
            tracemalloc.stop()
        self.assertEqual(result['source'], 'unavailable')
        self.assertTrue(result['truncated'])
        self.assertLess(peak, 8 * 1024 * 1024)

    def _snapshot(self) -> None:
        self.assertTrue(
            CheckpointManager().ensure_checkpoint(working_dir=self.workdir, reason="baseline")
        )

    def _record(
        self,
        task_id: str,
        *,
        path: str,
        additions: int,
        deletions: int,
        status: str = "modified",
    ) -> None:
        write_task_changes(
            {
                "task_id": task_id,
                "workdir": self.workdir,
                "files": [
                    {
                        "path": path,
                        "additions": additions,
                        "deletions": deletions,
                        "status": status,
                    }
                ],
            },
            data_root=self._tmp.name,
        )

    def test_deleted_file_diff_and_review_show_saved_old_content(self):
        target = Path(_write_file(self.workdir, "gone.txt", "old\n"))
        self._snapshot()
        target.unlink()
        task = self._task(created_at=100)
        self._record(task, path="gone.txt", additions=0, deletions=9, status="deleted")
        diff = self.service.session_changes_diff(self.session_id, "gone.txt")
        self.assertEqual(diff["deletions"], 1)
        review = self.service.session_changes_review(self.session_id)
        self.assertEqual(review["files"][0]["deletions"], 1)
        self.assertEqual(self.service.session_changes_review(self.session_id, path="gone.txt")["hunks"], diff["hunks"])

    def test_modified_file_has_added_and_deleted_hunks(self):
        _write_file(self.workdir, "app.py", "one\ntwo\nthree\n")
        self._snapshot()
        _write_file(self.workdir, "app.py", "one\nTWO\nthree\nfour\n")
        task = self._task(created_at=100)
        self._record(task, path="app.py", additions=2, deletions=1)

        result = self.service.session_changes_diff(self.session_id, "app.py")

        self.assertEqual(result["path"], "app.py")
        self.assertEqual(result["source"], "checkpoint")
        self.assertFalse(result["truncated"])
        self.assertEqual(
            (result["additions"], result["deletions"]),
            count_line_changes("one\ntwo\nthree\n", "one\nTWO\nthree\nfour\n"),
        )
        types = {line["type"] for hunk in result["hunks"] for line in hunk["lines"]}
        self.assertIn("-", types)
        self.assertIn("+", types)
        for hunk in result["hunks"]:
            self.assertRegex(hunk["header"], r"^@@ -\d+,\d+ \+\d+,\d+ @@$")

    def test_added_file_has_only_added_lines(self):
        self._snapshot()
        _write_file(self.workdir, "new.txt", "alpha\nbeta\n")
        task = self._task(created_at=100)
        self._record(task, path="new.txt", additions=2, deletions=0, status="added")

        result = self.service.session_changes_diff(self.session_id, "new.txt")

        self.assertEqual(result["source"], "checkpoint")
        self.assertEqual((result["additions"], result["deletions"]), (2, 0))
        lines = [line for hunk in result["hunks"] for line in hunk["lines"]]
        self.assertTrue(lines)
        self.assertTrue(all(line["type"] == "+" for line in lines))
        self.assertEqual([line["text"] for line in lines], ["alpha", "beta"])

    def test_without_checkpoint_returns_unavailable_with_recorded_stats(self):
        _write_file(self.workdir, "app.py", "x\n")
        task = self._task(created_at=100)
        self._record(task, path="app.py", additions=7, deletions=3)

        result = self.service.session_changes_diff(self.session_id, "app.py")

        self.assertEqual(
            result,
            {
                "path": "app.py",
                "additions": 7,
                "deletions": 3,
                "source": "unavailable",
                "truncated": False,
                "hunks": [],
            },
        )

    def test_added_file_without_checkpoint_gets_full_add_diff(self):
        _write_file(self.workdir, "new.txt", "alpha\nbeta\n")
        task = self._task(created_at=100)
        self._record(task, path="new.txt", additions=2, deletions=0, status="added")

        result = self.service.session_changes_diff(self.session_id, "new.txt")

        self.assertEqual(result["source"], "added")
        self.assertEqual((result["additions"], result["deletions"]), (2, 0))
        lines = [line for hunk in result["hunks"] for line in hunk["lines"]]
        self.assertEqual([line["text"] for line in lines], ["alpha", "beta"])
        self.assertTrue(all(line["type"] == "+" for line in lines))

    def test_path_outside_workdir_is_not_found(self):
        _write_file(self.workdir, "app.py", "x\n")
        task = self._task(created_at=100)
        self._record(task, path="app.py", additions=1, deletions=0, status="added")

        with self.assertRaises(MobileNotFound):
            self.service.session_changes_diff(self.session_id, "../escape.py")
        with self.assertRaises(MobileNotFound):
            self.service.session_changes_diff(self.session_id, "/etc/passwd")

    def test_unrecorded_path_is_not_found(self):
        _write_file(self.workdir, "app.py", "x\n")
        task = self._task(created_at=100)
        self._record(task, path="app.py", additions=1, deletions=0, status="added")

        with self.assertRaises(MobileNotFound):
            self.service.session_changes_diff(self.session_id, "other.py")

    def test_unknown_session_is_not_found(self):
        with self.assertRaises(MobileNotFound):
            self.service.session_changes_diff("chat-does-not-exist", "app.py")

    def test_binary_file_is_unavailable(self):
        self._snapshot()
        (Path(self.workdir) / "blob.bin").write_bytes(b"\x00\x01\x02binary")
        task = self._task(created_at=100)
        self._record(task, path="blob.bin", additions=1, deletions=0, status="added")

        result = self.service.session_changes_diff(self.session_id, "blob.bin")

        self.assertEqual(result["source"], "unavailable")
        self.assertEqual(result["hunks"], [])
        self.assertFalse(result["truncated"])

    def test_large_diff_is_truncated(self):
        _write_file(self.workdir, "big.txt", "".join(f"old-{index}\n" for index in range(5)))
        self._snapshot()
        new_text = "".join(f"new-{index}\n" for index in range(3000))
        _write_file(self.workdir, "big.txt", new_text)
        task = self._task(created_at=100)
        self._record(task, path="big.txt", additions=3000, deletions=5)

        result = self.service.session_changes_diff(self.session_id, "big.txt")

        self.assertEqual(result["source"], "checkpoint")
        self.assertTrue(result["truncated"])
        self.assertEqual(
            (result["additions"], result["deletions"]),
            count_line_changes("".join(f"old-{index}\n" for index in range(5)), new_text),
        )
        self.assertEqual(
            sum(len(hunk["lines"]) for hunk in result["hunks"]),
            MAX_DIFF_LINES,
        )


if __name__ == "__main__":
    unittest.main()
