"""Tests for session metadata enrichment in cli_chat.py."""

import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

from factory.core.cli_chat import (
    _append_entry,
    _first_user_message,
    _last_entry_ts,
    _load_entries,
    _read_session_meta,
    _session_entry_count,
    _write_session_meta,
    fork_session,
    generate_session_name,
    list_sessions,
)


class SessionMetaTest(unittest.TestCase):
    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.sp = Path(self.tmpdir) / "test-session.jsonl"

    def tearDown(self):
        for f in Path(self.tmpdir).glob("*"):
            f.unlink()
        Path(self.tmpdir).rmdir()

    # -- _write_session_meta --

    def test_write_meta_creates_file_with_header(self):
        _write_session_meta(
            self.sp,
            session_id="test-session",
            profile="cc-openrouter",
            model="deepseek/deepseek-v4-pro",
            provider="openrouter",
            cwd="/home/user/project",
            git_branch="main",
        )
        self.assertTrue(self.sp.exists())
        lines = self.sp.read_text().strip().splitlines()
        self.assertEqual(len(lines), 1)
        meta = json.loads(lines[0])
        self.assertEqual(meta["type"], "meta")
        self.assertEqual(meta["session_id"], "test-session")
        self.assertEqual(meta["profile"], "cc-openrouter")
        self.assertEqual(meta["model"], "deepseek/deepseek-v4-pro")
        self.assertEqual(meta["provider"], "openrouter")
        self.assertEqual(meta["cwd"], "/home/user/project")
        self.assertEqual(meta["git_branch"], "main")
        self.assertIn("created_at", meta)

    def test_write_meta_does_not_overwrite_existing_file(self):
        _append_entry(self.sp, role="user", text="hello")
        _write_session_meta(
            self.sp,
            session_id="test-session",
            profile="cc-openrouter",
            model="test-model",
        )
        lines = self.sp.read_text().strip().splitlines()
        # Only the original entry, no meta added
        self.assertEqual(len(lines), 1)
        entry = json.loads(lines[0])
        self.assertEqual(entry["role"], "user")

    def test_write_meta_idempotent_on_empty_file(self):
        # Write meta to non-existent file, then again
        _write_session_meta(self.sp, session_id="test", profile="p1")
        _write_session_meta(self.sp, session_id="test", profile="p2")
        lines = self.sp.read_text().strip().splitlines()
        # First write succeeds, second sees file has content
        self.assertEqual(len(lines), 1)

    # -- _read_session_meta --

    def test_read_meta_from_file_with_meta(self):
        _write_session_meta(
            self.sp,
            session_id="test-session",
            profile="cc-local",
            model="gemma4",
        )
        meta = _read_session_meta(self.sp)
        self.assertIsNotNone(meta)
        self.assertEqual(meta["session_id"], "test-session")
        self.assertEqual(meta["profile"], "cc-local")
        self.assertEqual(meta["model"], "gemma4")

    def test_read_meta_returns_none_for_file_without_meta(self):
        _append_entry(self.sp, role="user", text="hello")
        meta = _read_session_meta(self.sp)
        self.assertIsNone(meta)

    def test_read_meta_returns_none_for_nonexistent_file(self):
        meta = _read_session_meta(Path("/nonexistent/file.jsonl"))
        self.assertIsNone(meta)

    # -- Backward compatibility --

    def test_load_entries_ignores_meta_line(self):
        _write_session_meta(
            self.sp,
            session_id="test",
            profile="cc-openrouter",
            model="deepseek-v4",
        )
        _append_entry(self.sp, role="user", text="hello")
        _append_entry(self.sp, role="assistant", text="world")
        entries = _load_entries(self.sp)
        self.assertEqual(len(entries), 2)
        self.assertEqual(entries[0]["role"], "user")
        self.assertEqual(entries[1]["role"], "assistant")

    def test_load_entries_works_without_meta(self):
        _append_entry(self.sp, role="user", text="hello")
        _append_entry(self.sp, role="assistant", text="world")
        entries = _load_entries(self.sp)
        self.assertEqual(len(entries), 2)

    # -- _first_user_message --

    def test_first_user_message_extracts_preview(self):
        _append_entry(self.sp, role="user", text="create a file hello.py")
        _append_entry(self.sp, role="assistant", text="done")
        preview = _first_user_message(self.sp)
        self.assertEqual(preview, "create a file hello.py")

    def test_first_user_message_truncates_long_text(self):
        long_text = "x" * 300
        _append_entry(self.sp, role="user", text=long_text)
        preview = _first_user_message(self.sp)
        self.assertEqual(len(preview), 200)

    def test_first_user_message_empty_for_new_file(self):
        preview = _first_user_message(self.sp)
        self.assertEqual(preview, "")

    # -- _session_entry_count --

    def test_entry_count_counts_user_and_assistant(self):
        _append_entry(self.sp, role="user", text="a")
        _append_entry(self.sp, role="assistant", text="b")
        _append_entry(self.sp, role="user", text="c")
        self.assertEqual(_session_entry_count(self.sp), 3)

    def test_entry_count_ignores_meta(self):
        _write_session_meta(self.sp, session_id="test", profile="p")
        _append_entry(self.sp, role="user", text="a")
        self.assertEqual(_session_entry_count(self.sp), 1)

    # -- _last_entry_ts --

    def test_last_entry_ts_returns_last_timestamp(self):
        before = int(time.time())
        _append_entry(self.sp, role="user", text="a")
        _append_entry(self.sp, role="assistant", text="b")
        ts = _last_entry_ts(self.sp)
        self.assertGreaterEqual(ts, before)

    # -- list_sessions --

    def test_list_sessions_scans_directory(self):
        # Create a session with meta
        sp1 = Path(self.tmpdir) / "session-one.jsonl"
        _write_session_meta(sp1, session_id="session-one", profile="p1", model="m1")
        _append_entry(sp1, role="user", text="hello from session one")
        _append_entry(sp1, role="assistant", text="hi")

        # Create a session without meta (old format)
        sp2 = Path(self.tmpdir) / "session-two.jsonl"
        _append_entry(sp2, role="user", text="old session msg")
        _append_entry(sp2, role="assistant", text="ok")

        with mock.patch(
            "factory.core.cli_chat.get_chat_sessions_dir",
            return_value=self.tmpdir,
        ):
            sessions = list_sessions()

        self.assertEqual(len(sessions), 2)
        # Both should have session_id
        ids = {s["session_id"] for s in sessions}
        self.assertIn("session-one", ids)
        self.assertIn("session-two", ids)

        # session-one should have metadata
        s1 = next(s for s in sessions if s["session_id"] == "session-one")
        self.assertEqual(s1["profile"], "p1")
        self.assertEqual(s1["model"], "m1")
        self.assertEqual(s1["first_user_message"], "hello from session one")
        self.assertEqual(s1["message_count"], 2)

        # session-two (old format) should still work
        s2 = next(s for s in sessions if s["session_id"] == "session-two")
        self.assertEqual(s2["profile"], "")  # no meta
        self.assertEqual(s2["first_user_message"], "old session msg")
        self.assertEqual(s2["message_count"], 2)

    def test_list_sessions_returns_empty_for_missing_dir(self):
        with mock.patch(
            "factory.core.cli_chat.get_chat_sessions_dir",
            return_value="/nonexistent/path",
        ):
            sessions = list_sessions()
        self.assertEqual(sessions, [])


class ForkSessionTest(unittest.TestCase):
    def test_fork_does_not_inherit_pinned_state(self):
        from factory.core.cli_chat import update_session_meta
        _write_session_meta(self.sp,session_id='original',profile='p1',cwd='/tmp/project')
        update_session_meta(self.sp,{'pinned':True})
        with mock.patch('factory.core.cli_chat._session_path',side_effect=lambda name:Path(self.tmpdir)/f'{name}.jsonl'):
            name=fork_session(self.sp)
        self.assertFalse(_read_session_meta(Path(self.tmpdir)/f'{name}.jsonl')['pinned'])
        self.assertTrue(_read_session_meta(self.sp)['pinned'])

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.sp = Path(self.tmpdir) / "original-session.jsonl"

    def tearDown(self):
        for f in Path(self.tmpdir).glob("*"):
            f.unlink()
        Path(self.tmpdir).rmdir()

    def test_fork_copies_content(self):
        _write_session_meta(self.sp, session_id="original", profile="p1", model="m1")
        _append_entry(self.sp, role="user", text="hello")
        _append_entry(self.sp, role="assistant", text="world")

        with mock.patch("factory.core.cli_chat._session_path",
                        side_effect=lambda n: Path(self.tmpdir) / f"{n}.jsonl"):
            new_name = fork_session(self.sp)

        self.assertIsNotNone(new_name)
        self.assertTrue(new_name.startswith("fork-"))

        # Original unchanged
        orig_entries = _load_entries(self.sp)
        self.assertEqual(len(orig_entries), 2)

        # Fork has same entries
        new_path = Path(self.tmpdir) / f"{new_name}.jsonl"
        new_entries = _load_entries(new_path)
        self.assertEqual(len(new_entries), 2)
        self.assertEqual(new_entries[0]["text"], "hello")

    def test_fork_updates_meta_session_id(self):
        _write_session_meta(self.sp, session_id="original", profile="p1")
        _append_entry(self.sp, role="user", text="test")

        with mock.patch("factory.core.cli_chat._session_path",
                        side_effect=lambda n: Path(self.tmpdir) / f"{n}.jsonl"):
            new_name = fork_session(self.sp)

        new_path = Path(self.tmpdir) / f"{new_name}.jsonl"
        meta = _read_session_meta(new_path)
        self.assertIsNotNone(meta)
        self.assertEqual(meta["session_id"], new_name)
        self.assertNotEqual(meta["session_id"], "original")

    def test_fork_returns_none_for_empty(self):
        new_name = fork_session(self.sp)
        self.assertIsNone(new_name)

    def test_fork_returns_none_for_nonexistent(self):
        phantom = Path(self.tmpdir) / "phantom.jsonl"
        new_name = fork_session(phantom)
        self.assertIsNone(new_name)


if __name__ == "__main__":
    unittest.main()
