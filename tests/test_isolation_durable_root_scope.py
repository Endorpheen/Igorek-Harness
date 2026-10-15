#!/usr/bin/env python3
"""Off-mode parity for the durable-root exclusion (plan todo 43).

Todo 26 scoped the runtime's own artifacts to the durable data root and, to
keep them out of user change bookkeeping, added the directory name
``.igorek-host-data`` to two *global* exclusion lists. That broke the plan's
hard invariant (``off`` mode is byte-identical): in off mode a write under
``<workspace>/.igorek-host-data/...`` was silently dropped from
``session_changes`` and excluded from checkpoint snapshots, while before todo 26
it was recorded like any other user file.

This suite pins the A/B contract:

* OFF mode - the executor data root is outside the workdir (or absent), so
  nothing is excluded and the hidden directory behaves exactly as before;
* KVM mode - the executor receives ``data_root=<workspace>/.igorek-host-data``
  (``factory.core.isolation.persistence.durable_data_root``), so exactly that
  configured root stays out of change tracking and checkpoint snapshots.

The kvm half of the A/B must keep passing before and after the fix; the off
half must fail before it.
"""

from __future__ import annotations

import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from factory.core.checkpoint_manager import (
    DEFAULT_EXCLUDES,
    CheckpointManager,
    _shadow_repo_path,
)
from factory.core.durable_root_scope import (
    durable_root_within_workdir,
    is_within_relative,
)
from factory.core.isolation.persistence import (
    DURABLE_DATA_DIRNAME,
    durable_data_root,
)
from factory.core.session_changes import (
    TaskChangeTracker,
    aggregate_changes,
    is_internal_path,
)


class DurableRootScopeHelperTest(unittest.TestCase):
    """The scoping helper is the structural signal: a data root inside a workdir."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="igorek-scope-helper-")
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.workdir = self.root / "workspace"
        self.workdir.mkdir()

    def test_data_root_inside_the_workdir_yields_the_relative_root(self) -> None:
        # Given/When: an isolation-style data root nested in the workdir
        relative = durable_root_within_workdir(
            workdir=str(self.workdir),
            data_root=durable_data_root(str(self.workdir)),
        )

        # Then: the workdir-relative durable root is reported
        self.assertEqual(relative, DURABLE_DATA_DIRNAME)

    def test_data_root_outside_or_absent_yields_nothing(self) -> None:
        # Given/When/Then: off mode (process data root) and no data root are both inert
        self.assertIsNone(
            durable_root_within_workdir(
                workdir=str(self.workdir), data_root=str(self.root / "data-root")
            )
        )
        self.assertIsNone(
            durable_root_within_workdir(workdir=str(self.workdir), data_root=None)
        )
        # And: a data root equal to the workdir never excludes the whole tree
        self.assertIsNone(
            durable_root_within_workdir(
                workdir=str(self.workdir), data_root=str(self.workdir)
            )
        )

    def test_is_within_relative_is_path_precise(self) -> None:
        self.assertTrue(is_within_relative(f"{DURABLE_DATA_DIRNAME}/x", DURABLE_DATA_DIRNAME))
        self.assertTrue(is_within_relative(DURABLE_DATA_DIRNAME, DURABLE_DATA_DIRNAME))
        self.assertFalse(is_within_relative(f"src/{DURABLE_DATA_DIRNAME}/x", DURABLE_DATA_DIRNAME))
        self.assertFalse(is_within_relative("src/main.py", DURABLE_DATA_DIRNAME))
        self.assertFalse(is_within_relative("x", ""))


class OffModeParityTest(unittest.TestCase):
    """Isolation OFF: the hidden directory is ordinary user data (pre-todo-26)."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="igorek-off-parity-")
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.workdir = self.root / "workspace"
        self.workdir.mkdir()
        # The off-mode data root lives outside every workdir (the process data root).
        self.data_root = self.root / "data-root"

    def test_internal_path_keeps_the_host_data_dirname_out_of_the_reserved_set(self) -> None:
        # Then: without a configured in-workdir durable root the name is user data
        self.assertFalse(is_internal_path(f"{DURABLE_DATA_DIRNAME}/notes.txt"))
        self.assertNotIn(f"{DURABLE_DATA_DIRNAME}/", DEFAULT_EXCLUDES)

    def test_session_changes_records_a_write_under_the_host_data_dirname(self) -> None:
        # Given: an off-mode tracker and a write under the hidden directory
        target = self.workdir / DURABLE_DATA_DIRNAME / "notes.txt"
        target.parent.mkdir(parents=True)
        target.write_text("one\n", encoding="utf-8")
        tracker = TaskChangeTracker(
            task_id="off-parity",
            workdir=str(self.workdir),
            checkpoint_manager=None,
            data_root=str(self.data_root),
        )

        # When: the write is recorded
        tracker.record_write(str(target), "one\n")

        # Then: it is a normal added file, exactly as before todo 26
        self.assertEqual(
            tracker.files(),
            [
                {
                    "path": f"{DURABLE_DATA_DIRNAME}/notes.txt",
                    "additions": 1,
                    "deletions": 0,
                    "status": "added",
                }
            ],
        )
        persisted = json.loads(
            (self.data_root / "session_changes" / "off-parity.json").read_text(encoding="utf-8")
        )
        self.assertEqual(persisted["files"], tracker.files())

    def test_aggregate_changes_keeps_a_record_under_the_host_data_dirname(self) -> None:
        # Given: a persisted off-mode record for a file under the hidden directory
        records = [
            {
                "task_id": "off-parity",
                "workdir": str(self.workdir),
                "files": [
                    {
                        "path": f"{DURABLE_DATA_DIRNAME}/notes.txt",
                        "additions": 1,
                        "deletions": 0,
                        "status": "added",
                    }
                ],
            }
        ]

        # When: the session folds its records
        result = aggregate_changes(records, session_workdir=str(self.workdir))

        # Then: the file is still reported to the user
        self.assertEqual(
            [item["path"] for item in result["files"]],
            [f"{DURABLE_DATA_DIRNAME}/notes.txt"],
        )

    def test_checkpoint_snapshots_a_file_under_the_host_data_dirname(self) -> None:
        # Given: an off-mode checkpoint manager with a checkpoint taken
        (self.workdir / "base.txt").write_text("base\n", encoding="utf-8")
        manager = CheckpointManager(data_root=str(self.data_root))
        self.assertTrue(manager.ensure_checkpoint(working_dir=str(self.workdir)))
        shadow_exclude = (
            _shadow_repo_path(str(self.workdir), str(self.data_root)) / "info" / "exclude"
        ).read_text(encoding="utf-8")
        self.assertNotIn(f"{DURABLE_DATA_DIRNAME}/", shadow_exclude)

        # When: a file appears under the hidden directory and a new checkpoint runs
        hidden = self.workdir / DURABLE_DATA_DIRNAME / "tracked.txt"
        hidden.parent.mkdir(parents=True)
        hidden.write_text("tracked\n", encoding="utf-8")
        manager.new_turn()

        # Then: the hidden file is snapshotted exactly like any user file
        self.assertTrue(manager.ensure_checkpoint(working_dir=str(self.workdir)))
        shadow_repo = _shadow_repo_path(str(self.workdir), str(self.data_root))
        tracked = subprocess.run(
            ["git", f"--git-dir={shadow_repo}", "ls-files"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.split()
        self.assertIn(f"{DURABLE_DATA_DIRNAME}/tracked.txt", tracked)


class KvmDurableRootScopeTest(unittest.TestCase):
    """Isolation KVM: the configured durable root stays out of user bookkeeping."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="igorek-kvm-scope-")
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.workdir = self.root / "workspace"
        self.workdir.mkdir()
        self.data_root = durable_data_root(str(self.workdir))

    def test_internal_path_reports_the_configured_durable_root(self) -> None:
        # Given: the workdir-relative durable root the guest runner configures
        relative = durable_root_within_workdir(
            workdir=str(self.workdir), data_root=self.data_root
        )
        self.assertEqual(relative, DURABLE_DATA_DIRNAME)

        # Then: the durable root and everything below it is internal
        self.assertTrue(
            is_internal_path(
                f"{DURABLE_DATA_DIRNAME}/session_changes/task-1.json",
                durable_root=relative,
            )
        )
        self.assertTrue(is_internal_path(DURABLE_DATA_DIRNAME, durable_root=relative))
        # And: a same-named directory elsewhere is user data, not the durable root
        self.assertFalse(
            is_internal_path(f"src/{DURABLE_DATA_DIRNAME}/x", durable_root=relative)
        )
        self.assertFalse(is_internal_path("src/main.py", durable_root=relative))

    def test_session_changes_drops_writes_under_the_durable_root(self) -> None:
        # Given: an isolation-mode tracker
        tracker = TaskChangeTracker(
            task_id="kvm-scope",
            workdir=str(self.workdir),
            checkpoint_manager=None,
            data_root=self.data_root,
        )

        # When: a write lands under the durable root
        target = self.workdir / DURABLE_DATA_DIRNAME / "session_changes" / "x.json"
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("{}", encoding="utf-8")
        tracker.record_write(str(target), "{}")

        # Then: the runtime artifact is never reported as a user change
        self.assertEqual(tracker.files(), [])

    def test_aggregate_changes_drops_a_persisted_durable_root_path(self) -> None:
        # Given: a persisted record whose entry points into the durable root
        relative = durable_root_within_workdir(
            workdir=str(self.workdir), data_root=self.data_root
        )
        records = [
            {
                "task_id": "kvm-aggregate",
                "workdir": str(self.workdir),
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
            }
        ]

        # When: the session folds its records with the kvm durable root
        result = aggregate_changes(
            records,
            session_workdir=str(self.workdir),
            durable_root=relative,
        )

        # Then: the runtime artifact stays out at aggregation time as well
        self.assertEqual(
            result,
            {
                "files": [
                    {
                        "path": "src/main.py",
                        "additions": 2,
                        "deletions": 1,
                        "status": "modified",
                    },
                ],
                "totals": {"files": 1, "additions": 2, "deletions": 1},
            },
        )

    def test_checkpoint_excludes_the_durable_root_from_snapshots(self) -> None:
        # Given: an isolation-mode checkpoint manager whose shadow repo lives
        # inside the work tree (under the durable root)
        (self.workdir / "base.txt").write_text("base\n", encoding="utf-8")
        manager = CheckpointManager(data_root=self.data_root)
        self.assertTrue(manager.ensure_checkpoint(working_dir=str(self.workdir)))

        # Then: the durable root is excluded, so the shadow repo never snapshots itself
        shadow_repo = _shadow_repo_path(str(self.workdir), self.data_root)
        shadow_exclude = (shadow_repo / "info" / "exclude").read_text(encoding="utf-8")
        self.assertIn(f"{DURABLE_DATA_DIRNAME}/", shadow_exclude)
        tracked = subprocess.run(
            ["git", f"--git-dir={shadow_repo}", "ls-files"],
            capture_output=True,
            text=True,
            check=True,
        ).stdout.split()
        self.assertEqual(tracked, ["base.txt"])

        # And: a write under the durable root never produces a checkpoint
        hidden = self.workdir / DURABLE_DATA_DIRNAME / "checkpoints" / "scratch"
        hidden.parent.mkdir(parents=True, exist_ok=True)
        hidden.write_text("scratch\n", encoding="utf-8")
        manager.new_turn()
        self.assertFalse(manager.ensure_checkpoint(working_dir=str(self.workdir)))


if __name__ == "__main__":
    unittest.main()
