"""Spreadsheet bytes remain visible as files without fabricated source-line diffs."""

import json
import subprocess
import zipfile
from unittest.mock import Mock, patch

import pytest

from factory.core.checkpoint_manager import CheckpointManager
from factory.core.review_changes import ChangesReview
from factory.core.session_changes import (
    TaskChangeTracker,
    aggregate_changes,
    load_task_changes,
)


FORMATS = ["xlsx", "xls", "xlsm", "xlsb", "xltx", "xltm", "ods", "ots", "numbers"]


def workbook(path, text):
    with zipfile.ZipFile(path, "w", zipfile.ZIP_STORED) as archive:
        archive.writestr(
            "[Content_Types].xml",
            '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>',
        )
        archive.writestr(
            "xl/worksheets/sheet1.xml",
            f"<worksheet><sheetData>{text}</sheetData></worksheet>",
        )


@pytest.mark.parametrize("extension", FORMATS)
def test_tool_fallback_tracks_workbook_without_line_counts(tmp_path, extension):
    tracker = TaskChangeTracker(
        task_id="write", workdir=str(tmp_path), data_root=str(tmp_path / "data")
    )
    target = tmp_path / ("book." + extension)
    target.write_bytes(b"synthetic workbook bytes")
    tracker.record_write(str(target), "not\nsource\nlines\n")
    tracker.record_edit(str(target), "one\ntwo", "three")
    assert tracker.files() == [
        dict(path=target.name, status="modified", additions=0, deletions=0)
    ]
    assert (
        load_task_changes("write", data_root=str(tmp_path / "data"))["files"]
        == tracker.files()
    )


@pytest.mark.parametrize("status", ["added", "modified", "deleted"])
def test_checkpoint_workbook_does_not_decode_archived_bytes(tmp_path, status):
    target = tmp_path / "report.xlsx"
    baseline = None if status == "added" else "old\nserialized\nbytes"
    manager = Mock()
    manager.baseline_content.return_value = (True, baseline)
    manager.baseline_revision.return_value = "fixture-revision"
    tracker = TaskChangeTracker(
        task_id="checkpoint",
        workdir=str(tmp_path),
        checkpoint_manager=manager,
        data_root=str(tmp_path / "data"),
    )
    if status != "deleted":
        workbook(target, "new row")
    with patch(
        "factory.core.session_changes._read_text",
        side_effect=AssertionError("must not decode workbook"),
    ):
        tracker.record_write(str(target), "unused")
    assert tracker.files() == [
        dict(path=target.name, status=status, additions=0, deletions=0)
    ]


@pytest.mark.parametrize("status", ["added", "modified", "deleted"])
def test_shell_change_keeps_workbook_status_but_not_lines(tmp_path, status):
    root = tmp_path / "work"
    root.mkdir()
    target = root / "report.xlsx"
    if status != "added":
        workbook(target, "old")
    manager = CheckpointManager(data_root=str(tmp_path / "data"))
    assert manager.ensure_checkpoint(working_dir=str(root), reason="test baseline")
    tracker = TaskChangeTracker(
        task_id="shell",
        workdir=str(root),
        checkpoint_manager=manager,
        data_root=str(tmp_path / "data"),
    )
    before = tracker.workspace_snapshot()
    if status == "deleted":
        target.unlink()
    else:
        workbook(target, "new content with another row")
    tracker.record_shell_changes(before)
    assert tracker.files() == [
        dict(path="report.xlsx", status=status, additions=0, deletions=0)
    ]


@pytest.mark.parametrize("extension", FORMATS)
def test_legacy_records_exclude_workbooks_from_totals(tmp_path, extension):
    records = [
        dict(
            workdir=str(tmp_path),
            files=[
                dict(
                    path="report." + extension,
                    status="modified",
                    additions=2590,
                    deletions=2482,
                ),
                dict(path="main.py", status="modified", additions=2, deletions=1),
            ],
        )
    ]
    original = json.dumps(records)
    result = aggregate_changes(records, session_workdir=str(tmp_path))
    assert result["totals"] == dict(files=2, additions=2, deletions=1)
    assert next(f for f in result["files"] if f["path"].startswith("report.")) == dict(
        path="report." + extension, status="modified", additions=0, deletions=0
    )
    assert json.dumps(records) == original


@pytest.mark.parametrize("extension", ["csv", "tsv", "md", "py", "xlsx.py", "svg"])
def test_text_formats_keep_real_line_diffs(tmp_path, extension):
    tracker = TaskChangeTracker(
        task_id="text", workdir=str(tmp_path), data_root=str(tmp_path / "data")
    )
    target = tmp_path / ("report." + extension)
    tracker.record_edit(str(target), "old\n", "new\nsecond\n")
    assert tracker.files()[0]["additions"] == 2
    assert tracker.files()[0]["deletions"] == 1


@pytest.mark.parametrize("status", ["added", "modified", "deleted"])
def test_git_review_workbook_has_no_hunks_or_counts(tmp_path, status):
    def git(*args):
        return subprocess.check_output(["git", *args], cwd=tmp_path)

    git("init", "-b", "main")
    git("config", "user.name", "Fixture")
    git("config", "user.email", "fixture@example.invalid")
    target = tmp_path / "report.xlsx"
    if status != "added":
        target.write_text("old serialized data without a NUL")
    (tmp_path / "keep.txt").write_text("keep")
    git("add", ".")
    git("commit", "-m", "Fixture baseline")
    if status == "deleted":
        target.unlink()
    else:
        target.write_text("new\nserialized\ndata without a NUL")
    review = ChangesReview(
        tmp_path, session_files=[], repositories=[], include_untracked=True
    )
    review.prepare("working")
    result = review.result()
    assert result["totals"]["additions"] == result["totals"]["deletions"] == 0
    entry = next(f for f in result["files"] if f["path"] == target.name)
    assert entry["unavailable_reason"] == "binary"
    detailed = review.result(target.name)
    assert detailed["hunks"] == []
    assert detailed["additions"] is None and detailed["deletions"] is None


@pytest.mark.parametrize("route", ["session", "checkpoint"])
@pytest.mark.parametrize("extension", ["xlsx", "xlsm", "png", "mp4"])
def test_mobile_diff_routes_do_not_read_known_non_text_files(
    tmp_path, route, extension
):
    from factory.core.mobile_bridge import service as mobile

    target = tmp_path / ("report." + extension)
    target.write_text("serialized\ncontent without a NUL")
    session_file = tmp_path / "chat.jsonl"
    session_file.touch()
    service = mobile.MobileService.__new__(mobile.MobileService)
    service.workdir = service.data_dir = str(tmp_path)
    service.session_changes = Mock(
        return_value={
            "files": [
                dict(
                    path=target.name, additions=2590, deletions=2482, status="modified"
                )
            ]
        }
    )
    service._session_artifact_root = Mock(return_value=None)
    service._session_workdir = Mock(return_value=str(tmp_path))
    manager = Mock()
    manager.has_revision.return_value = True
    manager.content_at.side_effect = AssertionError("must not read archived baseline")
    with (
        patch.object(mobile, "_session_path", return_value=session_file),
        patch.object(mobile, "_read_session_meta", return_value={"cwd": str(tmp_path)}),
        patch.object(mobile, "CheckpointManager", return_value=manager),
        patch.object(
            mobile,
            "open_regular_file",
            side_effect=AssertionError("must not decode workbook"),
        ),
    ):
        result = (
            service.session_changes_diff("session", target.name)
            if route == "session"
            else service.session_checkpoint_diff(
                "session", "fixture-revision", target.name
            )
        )
    assert result["source"] == "unavailable"
    assert result["hunks"] == []
    assert not result.get("additions") and not result.get("deletions")


@pytest.mark.parametrize(
    ("old_name", "new_name"), [("book.xlsx", "book.txt"), ("book.txt", "book.xlsx")]
)
def test_renamed_workbook_is_not_counted_as_replaced_text(tmp_path, old_name, new_name):
    def git(*args):
        return subprocess.check_output(["git", *args], cwd=tmp_path)

    git("init", "-b", "main")
    git("config", "user.name", "Fixture")
    git("config", "user.email", "fixture@example.invalid")
    (tmp_path / old_name).write_text("serialized\nworkbook\nbytes")
    git("add", old_name)
    git("commit", "-m", "Fixture baseline")
    git("mv", old_name, new_name)
    review = ChangesReview(
        tmp_path, session_files=[], repositories=[], include_untracked=True
    )
    review.prepare("working")
    result = review.result()
    assert result["totals"]["additions"] == result["totals"]["deletions"] == 0
    assert result["files"][0]["unavailable_reason"] == "binary"
