"""A running executor alone must not mark the first-result step complete."""
import importlib.util
from pathlib import Path
import sqlite3

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("desktop_onboarding", ROOT / "desktop/onboarding.py")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


@pytest.fixture
def history(tmp_path):
    path = tmp_path / "database/factory.db"
    path.parent.mkdir()
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE tasks(profile TEXT, status TEXT, output_text TEXT, completed_at TEXT)")
        db.executemany("INSERT INTO tasks VALUES (?, ?, ?, ?)", [
            ("verified-profile", "ok", "fixture answer", "2026-10-15"),
            ("verified-profile", "ok", "another answer", "2026-10-15"),
            ("still-running", "processing", "partial answer", None),
            ("failed-profile", "fail", "failure description", "2026-10-15"),
            ("empty-result", "ok", "  ", "2026-10-15"),
            ("not-committed", "ok", "partial answer", None),
        ])
    return tmp_path


def test_only_completed_success_with_persisted_output_counts(history):
    assert module.onboarding_progress(history, unlocked=True) == {
        "state": "ready", "successful_profiles": ["verified-profile"]
    }


def test_locked_settings_do_not_expose_task_profiles_or_answers(history):
    assert module.onboarding_progress(history, unlocked=False) == {
        "state": "locked", "successful_profiles": []
    }


def test_new_installation_is_ready_without_creating_a_database(tmp_path):
    assert module.onboarding_progress(tmp_path, unlocked=True) == {
        "state": "ready", "successful_profiles": []
    }
    assert not (tmp_path / "database").exists()


def test_unreadable_history_is_not_reported_as_a_success(history):
    path = history / "database/factory.db"
    path.write_bytes(b"not a sqlite database")
    assert module.onboarding_progress(history, unlocked=True)["state"] == "unavailable"


def test_symlinked_database_is_not_read(history, tmp_path):
    database = history / "database/factory.db"
    external = tmp_path / "external.db"
    database.rename(external)
    database.symlink_to(external)
    assert module.onboarding_progress(history, unlocked=True)["state"] == "unavailable"
