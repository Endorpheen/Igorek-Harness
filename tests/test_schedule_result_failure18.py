"""Failure-path test for durable schedule result replacement."""

import json

import pytest

from factory.core import scheduled_delivery


TASK_ID = "11111111-1111-1111-1111-111111111111"


def _task():
    return {
        "task_id": TASK_ID,
        "status": "ok",
        "metadata": {
            "schedule_run": {"source_session": "session-123"},
            "cwd": "/tmp",
        },
    }


def test_report_result_failure18_keeps_first_recorded_result(monkeypatch, tmp_path):
    task = _task()
    monkeypatch.setattr("factory.core.scheduled_delivery.get_task", lambda task_id: task)
    monkeypatch.setattr("factory.core.scheduled_delivery.get_data_root", lambda: str(tmp_path))

    first = scheduled_delivery.report_result(TASK_ID, {"disposition": "deliver", "text": "first result", "files": []})
    assert first["status"] == "recorded"

    def fake_replace(src, dst):
        raise OSError("simulated os.replace failure")

    monkeypatch.setattr("os.replace", fake_replace)

    with pytest.raises(OSError):
        scheduled_delivery.report_result(TASK_ID, {"disposition": "deliver", "text": "second result", "files": []})

    saved = json.loads((tmp_path / "schedule-results" / (TASK_ID + ".json")).read_text(encoding="utf-8"))
    assert saved["text"] == "first result"
