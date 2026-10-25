"""Cleanup contracts using only disposable tasks, SQLite and workspace files."""

import sqlite3
import sys
import time
from pathlib import Path
from uuid import uuid4

import pytest

from factory.core import workspace_cleaner as w
from factory.core.schema import initialize_database, open_db


@pytest.fixture
def cleaner(tmp_path):
    db = tmp_path / "db.sqlite"
    initialize_database(db)
    root = tmp_path / "workspace"
    root.mkdir()
    return w.WorkspaceCleaner(str(db), str(root))


def task(cleaner, status="ok", age=20, parent=None):
    identifier = str(uuid4())
    with open_db(cleaner.db_path) as conn:
        conn.execute(
            "INSERT INTO tasks(task_id,status,input_text,created_at,parent_id,profile) VALUES(?,?,?,?,?,?)",
            (
                identifier,
                status,
                "Disposable task",
                int(time.time()) - age * 86400,
                parent,
                "fixture",
            ),
        )
        conn.execute(
            "INSERT INTO events(event_id,task_id,event_type) VALUES(?,?,?)",
            (str(uuid4()), identifier, "fixture"),
        )
        conn.commit()
    path = Path(cleaner.workspace_root) / identifier
    path.mkdir()
    (path / "file").write_bytes(b"12345")
    return identifier


@pytest.mark.parametrize(
    "size,expected",
    [
        (1, "1.0 B"),
        (1024, "1.0 KB"),
        (1024**2, "1.0 MB"),
        (1024**3, "1.0 GB"),
        (1024**4, "1.0 TB"),
    ],
)
def test_human_sizes_do_not_confuse_units(cleaner, size, expected):
    assert cleaner.format_size(size) == expected


def test_lookup_missing_and_existing_tasks(cleaner):
    identifier = task(cleaner)
    assert cleaner.get_task_info(identifier)["profile"] == "fixture"
    assert cleaner.get_task_info("missing") is None


def test_recursive_size_counts_files_not_directories(cleaner):
    identifier = task(cleaner)
    path = Path(cleaner.workspace_root) / identifier
    (path / "nested").mkdir()
    (path / "nested" / "data").write_bytes(b"abcdef")
    assert cleaner.get_workspace_size(str(path)) == 11


def test_size_skips_disappearing_files(cleaner, monkeypatch):
    identifier = task(cleaner)
    monkeypatch.setattr(
        w.os.path, "getsize", lambda _: (_ for _ in ()).throw(OSError("fixture gone"))
    )
    assert (
        cleaner.get_workspace_size(str(Path(cleaner.workspace_root) / identifier)) == 0
    )


@pytest.mark.parametrize(
    "workspace,database", [(True, True), (True, False), (False, True), (False, False)]
)
def test_delete_honors_independent_storage_flags(cleaner, workspace, database):
    identifier = task(cleaner)
    assert cleaner.delete_task(
        identifier, delete_workspace=workspace, delete_db=database
    )
    assert (Path(cleaner.workspace_root) / identifier).exists() is (not workspace)
    assert (cleaner.get_task_info(identifier) is None) is database
    assert cleaner.deleted_count == int(workspace) and cleaner.freed_space == 5 * int(
        workspace
    )
    if database:
        with open_db(cleaner.db_path) as conn:
            assert (
                conn.execute(
                    "SELECT COUNT(*) FROM events WHERE task_id=?", (identifier,)
                ).fetchone()[0]
                == 0
            )


def test_missing_task_or_workspace_is_not_counted(cleaner):
    assert not cleaner.delete_task("missing")
    assert not cleaner.delete_task_workspace("missing")
    assert cleaner.deleted_count == cleaner.freed_space == 0


def test_deleting_parent_does_not_leave_child_events(cleaner):
    parent = task(cleaner)
    child = task(cleaner, parent=parent)
    other = task(cleaner)
    assert cleaner.delete_task_from_db(parent)
    with open_db(cleaner.db_path) as conn:
        assert conn.execute(
            "SELECT task_id FROM tasks ORDER BY task_id"
        ).fetchall() == [(other,)]
        assert conn.execute(
            "SELECT task_id FROM events ORDER BY task_id"
        ).fetchall() == [(other,)]
    assert (Path(cleaner.workspace_root) / child).exists()


def test_default_age_filter_preserves_running_recent_and_other_states(cleaner):
    old = task(cleaner, "ok")
    failed = task(cleaner, "fail", age=10)
    active = task(cleaner, "processing")
    recent = task(cleaner, "ok", age=1)
    assert [row["task_id"] for row in cleaner.list_old_tasks()] == [failed, old]
    assert [
        row["task_id"] for row in cleaner.list_old_tasks(status_filter=["processing"])
    ] == [active]
    assert cleaner.list_old_tasks(status_filter=[]) == []
    cleaner.cleanup_old_tasks()
    assert cleaner.get_task_info(active) and cleaner.get_task_info(recent)
    assert cleaner.get_task_info(old) is None and cleaner.deleted_count == 2


def test_dry_run_preserves_database_and_all_workspace_bytes(cleaner, capsys):
    identifiers = [task(cleaner) for _ in range(12)]
    cleaner.cleanup_old_tasks(dry_run=True)
    assert all(cleaner.get_task_info(identifier) for identifier in identifiers)
    assert all(
        (Path(cleaner.workspace_root) / identifier / "file").read_bytes() == b"12345"
        for identifier in identifiers
    )
    assert cleaner.deleted_count == 0 and "еще 2" in capsys.readouterr().out


def test_orphan_cleanup_preserves_live_and_non_task_directories(cleaner):
    live = task(cleaner)
    orphan = str(uuid4())
    root = Path(cleaner.workspace_root)
    (root / orphan).mkdir()
    (root / orphan / "data").write_bytes(b"gone")
    (root / "project").mkdir()
    (root / "notes.txt").write_text("keep")
    assert cleaner.cleanup_orphaned_workspaces() == 1
    assert (
        not (root / orphan).exists()
        and (root / live).exists()
        and (root / "project").is_dir()
    )
    assert (root / "notes.txt").read_text() == "keep"


def test_orphan_cleanup_reports_deletion_failure(cleaner, monkeypatch):
    orphan = Path(cleaner.workspace_root) / str(uuid4())
    orphan.mkdir()
    monkeypatch.setattr(
        w.shutil,
        "rmtree",
        lambda _: (_ for _ in ()).throw(PermissionError("fixture denied")),
    )
    assert cleaner.cleanup_orphaned_workspaces() == 0 and orphan.is_dir()
    assert not cleaner.delete_task_workspace(orphan.name)


def test_broken_database_is_reported_without_false_success(cleaner, monkeypatch):
    monkeypatch.setattr(
        w,
        "open_db",
        lambda _: (_ for _ in ()).throw(
            sqlite3.OperationalError("fixture unavailable")
        ),
    )
    assert cleaner.get_task_info("fixture") is None
    assert not cleaner.delete_task_from_db("fixture")
    assert cleaner.list_old_tasks() == []
    assert cleaner.cleanup_orphaned_workspaces() == 0


def test_empty_cleanup_and_absent_root_are_safe(cleaner):
    cleaner.cleanup_old_tasks()
    Path(cleaner.workspace_root).rmdir()
    assert cleaner.cleanup_orphaned_workspaces() == 0


def cli(monkeypatch, cleaner, *args):
    monkeypatch.setattr(w, "WorkspaceCleaner", lambda: cleaner)
    monkeypatch.setattr(sys, "argv", ["workspace-cleaner", *args])
    w.main()


def test_cli_dry_run_does_not_delete_orphans(cleaner, monkeypatch):
    live = task(cleaner)
    orphan = Path(cleaner.workspace_root) / str(uuid4())
    orphan.mkdir()
    cli(monkeypatch, cleaner, "cleanup", "--dry-run")
    assert orphan.is_dir() and cleaner.get_task_info(live)


@pytest.mark.parametrize("command", ["cleanup", "orphaned"])
def test_cli_mutation_commands_only_touch_disposable_orphans(
    cleaner, monkeypatch, command
):
    orphan = Path(cleaner.workspace_root) / str(uuid4())
    orphan.mkdir()
    cli(monkeypatch, cleaner, command)
    assert not orphan.exists()


def test_cli_delete_keep_flags_and_failed_exit(cleaner, monkeypatch):
    identifier = task(cleaner)
    cli(monkeypatch, cleaner, "delete", identifier, "--keep-db", "--keep-workspace")
    assert (
        cleaner.get_task_info(identifier)
        and (Path(cleaner.workspace_root) / identifier).is_dir()
    )
    with pytest.raises(SystemExit) as exc:
        cli(monkeypatch, cleaner, "delete", "missing")
    assert exc.value.code == 1


def test_cli_list_limits_and_help_preserve_data(cleaner, monkeypatch, capsys):
    task(cleaner)
    task(cleaner)
    cli(monkeypatch, cleaner, "list", "--limit", "1")
    assert "еще 1" in capsys.readouterr().out
    cli(monkeypatch, cleaner)
    assert "usage:" in capsys.readouterr().out and len(cleaner.list_old_tasks()) == 2
