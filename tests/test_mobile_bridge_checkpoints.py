"""Read-only checkpoint history API for the mobile bridge (Phase 1).

Covers the three endpoints and the ``CheckpointManager`` read-only helpers they
build on, using real temp git shadow repos via ``CheckpointManager``.
"""

import http.client
import json
import os
import shutil
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.parse import quote

from factory.core.checkpoint_manager import CheckpointManager
from factory.core.cli_chat import _session_path
from factory.core.mobile_bridge.server import API_PREFIX, BridgeConfig, MobileBridge, build_server
from factory.core.mobile_bridge.service import MobileNotFound, MobileService
from factory.core.runtime import get_db_path
from factory.core.schema import initialize_database
from factory.core.session_diff import MAX_DIFF_FILE_BYTES


def _write_file(workdir: str, relative: str, content: str) -> Path:
    path = Path(workdir) / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


class _CheckpointWorkdirTestCase(unittest.TestCase):
    """Real shadow repo in an isolated data root, one temp workdir."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._env = patch.dict(os.environ, {"IGOREK_FACTORY_HOME": self._tmp.name})
        self._env.start()
        initialize_database(get_db_path())
        self.workdir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.workdir, ignore_errors=True)

    def tearDown(self):
        self._env.stop()
        self._tmp.cleanup()

    def _checkpoint(self, reason: str) -> CheckpointManager:
        manager = CheckpointManager()
        self.assertTrue(manager.ensure_checkpoint(working_dir=self.workdir, reason=reason))
        return manager

    def _latest_revision(self) -> str:
        return CheckpointManager().list_checkpoints(self.workdir)[0]["hash"]


class CheckpointManagerReadOnlyTest(_CheckpointWorkdirTestCase):
    def test_has_revision_accepts_real_hex_and_rejects_junk(self):
        _write_file(self.workdir, "app.py", "x\n")
        self._checkpoint("turn-1")
        revision = self._latest_revision()
        manager = CheckpointManager()

        self.assertTrue(manager.has_revision(working_dir=self.workdir, revision=revision))
        self.assertFalse(manager.has_revision(working_dir=self.workdir, revision="0" * 40))
        self.assertFalse(manager.has_revision(working_dir=self.workdir, revision="HEAD"))
        self.assertFalse(manager.has_revision(working_dir=self.workdir, revision=""))

    def test_content_at_returns_revision_content_and_absence(self):
        _write_file(self.workdir, "app.py", "one\ntwo\n")
        self._checkpoint("turn-1")
        revision = self._latest_revision()
        _write_file(self.workdir, "app.py", "one\nTWO\n")
        _write_file(self.workdir, "later.py", "new\n")
        manager = CheckpointManager()

        self.assertEqual(
            manager.content_at(
                target_path=str(Path(self.workdir) / "app.py"),
                workdir=self.workdir,
                revision=revision,
            ),
            (True, "one\ntwo\n"),
        )
        self.assertEqual(
            manager.content_at(
                target_path=str(Path(self.workdir) / "later.py"),
                workdir=self.workdir,
                revision=revision,
            ),
            (True, None),
        )
        self.assertEqual(
            manager.content_at(
                target_path=str(Path(self.workdir) / "app.py"),
                workdir=self.workdir,
                revision="not-a-hash",
            ),
            (False, None),
        )

    def test_changed_paths_since_maps_status_and_sorts(self):
        _write_file(self.workdir, "app.py", "one\ntwo\nthree\n")
        _write_file(self.workdir, "keep.py", "k\n")
        self._checkpoint("turn-1")
        revision = self._latest_revision()
        _write_file(self.workdir, "app.py", "one\nTWO\nthree\nfour\n")
        (Path(self.workdir) / "keep.py").unlink()
        _write_file(self.workdir, "new.py", "alpha\nbeta\n")
        self._checkpoint("turn-2")

        changed = CheckpointManager().changed_paths_since(
            working_dir=self.workdir, revision=revision
        )

        self.assertIsNotNone(changed)
        self.assertEqual([item["path"] for item in changed], ["app.py", "keep.py", "new.py"])
        self.assertEqual(
            [(item["status"], item["additions"], item["deletions"]) for item in changed],
            [("modified", 2, 1), ("deleted", 0, 1), ("added", 2, 0)],
        )


class _ServiceCheckpointHarness(_CheckpointWorkdirTestCase):
    def setUp(self):
        super().setUp()
        self.session_id = "chat-20260921-120000-checkpoints"
        path = _session_path(self.session_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(
                {
                    "type": "meta",
                    "session_id": self.session_id,
                    "profile": "cc-local-8081",
                    "cwd": self.workdir,
                }
            )
            + "\n",
            encoding="utf-8",
        )
        self.service = MobileService(profile="cc-local-8081", workdir=self.workdir)


class ServiceSessionCheckpointsTest(_ServiceCheckpointHarness):
    def test_list_shape_pagination_and_availability(self):
        _write_file(self.workdir, "app.py", "one\n")
        self._checkpoint("turn-1")
        _write_file(self.workdir, "app.py", "two\n")
        self._checkpoint("turn-2")

        result = self.service.session_checkpoints(self.session_id)

        self.assertTrue(result["available"])
        self.assertEqual(result["limit"], 50)
        self.assertEqual(result["offset"], 0)
        self.assertFalse(result["has_more"])
        self.assertEqual(result["workdir"], self.workdir)
        self.assertEqual(len(result["checkpoints"]), 2)
        first = result["checkpoints"][0]
        self.assertEqual(
            set(first), {"hash", "short_hash", "timestamp", "reason"}
        )
        self.assertRegex(first["hash"], r"^[0-9a-f]{40}$")
        self.assertRegex(first["short_hash"], r"^[0-9a-f]{7}$")
        self.assertEqual(first["reason"], "checkpoint: turn-2")

        page = self.service.session_checkpoints(self.session_id, limit=1, offset=0)

        self.assertEqual(len(page["checkpoints"]), 1)
        self.assertTrue(page["has_more"])
        tail = self.service.session_checkpoints(self.session_id, limit=1, offset=1)
        self.assertEqual(len(tail["checkpoints"]), 1)
        self.assertFalse(tail["has_more"])

    def test_missing_shadow_repo_degrades_without_raising(self):
        result = self.service.session_checkpoints(self.session_id)

        self.assertFalse(result["available"])
        self.assertEqual(result["checkpoints"], [])
        self.assertFalse(result["has_more"])

    def test_unknown_session_is_not_found(self):
        with self.assertRaises(MobileNotFound):
            self.service.session_checkpoints("chat-does-not-exist")


class ServiceSessionCheckpointChangesTest(_ServiceCheckpointHarness):
    def _fixture(self) -> str:
        _write_file(self.workdir, "app.py", "one\ntwo\nthree\n")
        _write_file(self.workdir, "keep.py", "k\n")
        self._checkpoint("turn-1")
        revision = self._latest_revision()
        _write_file(self.workdir, "app.py", "one\nTWO\nthree\nfour\n")
        (Path(self.workdir) / "keep.py").unlink()
        _write_file(self.workdir, "new.py", "alpha\nbeta\n")
        self._checkpoint("turn-2")
        return revision

    def test_changes_shape_statuses_and_totals(self):
        revision = self._fixture()

        result = self.service.session_checkpoint_changes(self.session_id, revision)

        self.assertEqual(set(result), {"files", "totals"})
        by_path = {item["path"]: item for item in result["files"]}
        self.assertEqual(set(by_path), {"app.py", "keep.py", "new.py"})
        self.assertEqual(
            (by_path["app.py"]["additions"], by_path["app.py"]["deletions"]),
            (2, 1),
        )
        self.assertEqual(by_path["app.py"]["status"], "modified")
        self.assertEqual(by_path["keep.py"]["status"], "deleted")
        self.assertEqual(by_path["new.py"]["status"], "added")
        self.assertEqual(
            result["totals"], {"files": 3, "additions": 4, "deletions": 2}
        )
        for item in result["files"]:
            self.assertEqual(set(item), {"path", "additions", "deletions", "status"})

    def test_unknown_revision_is_not_found(self):
        self._fixture()

        with self.assertRaises(MobileNotFound):
            self.service.session_checkpoint_changes(self.session_id, "0" * 40)
        with self.assertRaises(MobileNotFound):
            self.service.session_checkpoint_changes(self.session_id, "nope")


class ServiceSessionCheckpointDiffTest(_ServiceCheckpointHarness):
    def test_added_and_deleted_files_and_from_field(self):
        _write_file(self.workdir, "keep.py", "k\n")
        self._checkpoint("turn-1")
        revision = self._latest_revision()
        _write_file(self.workdir, "new.py", "alpha\nbeta\n")
        self._checkpoint("turn-2")
        (Path(self.workdir) / "keep.py").unlink()

        added = self.service.session_checkpoint_diff(self.session_id, revision, "new.py")

        self.assertEqual(added["path"], "new.py")
        self.assertEqual(added["source"], "checkpoint")
        self.assertEqual(added["from"], revision)
        self.assertFalse(added["truncated"])
        self.assertEqual((added["additions"], added["deletions"]), (2, 0))
        added_lines = [line for hunk in added["hunks"] for line in hunk["lines"]]
        self.assertTrue(added_lines)
        self.assertTrue(all(line["type"] == "+" for line in added_lines))
        self.assertEqual([line["text"] for line in added_lines], ["alpha", "beta"])
        for hunk in added["hunks"]:
            self.assertRegex(hunk["header"], r"^@@ -\d+,\d+ \+\d+,\d+ @@$")

        deleted = self.service.session_checkpoint_diff(self.session_id, revision, "keep.py")

        self.assertEqual(deleted["source"], "checkpoint")
        self.assertEqual(deleted["from"], revision)
        self.assertEqual((deleted["additions"], deleted["deletions"]), (0, 1))
        deleted_lines = [line for hunk in deleted["hunks"] for line in hunk["lines"]]
        self.assertTrue(all(line["type"] == "-" for line in deleted_lines))
        self.assertEqual([line["text"] for line in deleted_lines], ["k"])

    def test_binary_and_oversized_are_unavailable(self):
        _write_file(self.workdir, "base.txt", "x\n")
        self._checkpoint("turn-1")
        revision = self._latest_revision()
        (Path(self.workdir) / "blob.bin").write_bytes(b"\x00\x01binary")
        (Path(self.workdir) / "big.txt").write_bytes(b"x" * (MAX_DIFF_FILE_BYTES + 1))

        binary = self.service.session_checkpoint_diff(self.session_id, revision, "blob.bin")

        self.assertEqual(binary["source"], "unavailable")
        self.assertEqual(binary["from"], revision)
        self.assertEqual(binary["hunks"], [])
        self.assertFalse(binary["truncated"])

        oversized = self.service.session_checkpoint_diff(self.session_id, revision, "big.txt")

        self.assertEqual(oversized["source"], "unavailable")
        self.assertEqual(oversized["from"], revision)
        self.assertTrue(oversized["truncated"])
        self.assertEqual(oversized["hunks"], [])

    def test_unknown_revision_and_traversal_are_not_found(self):
        _write_file(self.workdir, "app.py", "one\n")
        self._checkpoint("turn-1")
        revision = self._latest_revision()

        with self.assertRaises(MobileNotFound):
            self.service.session_checkpoint_diff(self.session_id, "0" * 40, "app.py")
        with self.assertRaises(MobileNotFound):
            self.service.session_checkpoint_diff(self.session_id, revision, "../escape.py")
        with self.assertRaises(MobileNotFound):
            self.service.session_checkpoint_diff(self.session_id, revision, "/etc/passwd")
        with self.assertRaises(MobileNotFound):
            self.service.session_checkpoint_diff(self.session_id, revision, "")

    def test_unchanged_file_is_not_found(self):
        _write_file(self.workdir, "stable.txt", "same\n")
        self._checkpoint("turn-1")
        revision = self._latest_revision()

        with self.assertRaises(MobileNotFound):
            self.service.session_checkpoint_diff(self.session_id, revision, "stable.txt")


class _ServerCheckpointHarness(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._env = patch.dict(
            os.environ,
            {"IGOREK_FACTORY_HOME": self._tmp.name, "FACTORY_MOBILE_PROFILE": "cc-local-8081"},
        )
        self._env.start()
        initialize_database(get_db_path())
        self.workdir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.workdir, ignore_errors=True)
        self.bridge = MobileBridge(
            config=BridgeConfig(profile="cc-local-8081", poll_interval_s=0.2)
        )
        self.server = build_server(self.bridge, host="127.0.0.1", port=0)
        self.port = int(self.server.server_address[1])
        self._thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self._thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self._thread.join(timeout=5)
        self._env.stop()
        self._tmp.cleanup()

    def _request(self, method: str, path: str, *, body: dict | None = None, token: str | None = None):
        connection = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        payload = json.dumps(body or {}) if body is not None else None
        connection.request(method, path, body=payload, headers=headers)
        response = connection.getresponse()
        raw = response.read().decode("utf-8")
        connection.close()
        parsed = json.loads(raw) if raw else {}
        return response.status, parsed

    def _pair(self) -> str:
        code = self.bridge.store.create_pairing_code()["code"]
        status, payload = self._request(
            "POST", f"{API_PREFIX}/pair/complete", body={"code": code, "name": "Pixel"}
        )
        self.assertEqual(status, 201, payload)
        return payload["token"]

    def _session(self, token: str) -> str:
        status, created = self._request(
            "POST", f"{API_PREFIX}/sessions", body={"cwd": self.workdir}, token=token
        )
        self.assertEqual(status, 201, created)
        return created["session_id"]

    def _checkpoint(self, reason: str) -> None:
        self.assertTrue(
            CheckpointManager().ensure_checkpoint(working_dir=self.workdir, reason=reason)
        )

    def _revision(self) -> str:
        return CheckpointManager().list_checkpoints(self.workdir)[0]["hash"]


class ServerCheckpointRoutesTest(_ServerCheckpointHarness):
    def test_checkpoints_requires_auth_and_unknown_session_is_404(self):
        status, _ = self._request("GET", f"{API_PREFIX}/sessions/chat-nope/checkpoints")
        self.assertEqual(status, 401)

        token = self._pair()
        status, _ = self._request(
            "GET", f"{API_PREFIX}/sessions/chat-nope/checkpoints", token=token
        )
        self.assertEqual(status, 404)

    def test_checkpoints_route_degrades_and_lists(self):
        token = self._pair()
        session_id = self._session(token)

        status, payload = self._request(
            "GET", f"{API_PREFIX}/sessions/{session_id}/checkpoints", token=token
        )

        self.assertEqual(status, 200, payload)
        self.assertFalse(payload["available"])
        self.assertEqual(payload["checkpoints"], [])
        self.assertFalse(payload["has_more"])

        _write_file(self.workdir, "app.py", "one\n")
        self._checkpoint("turn-1")

        status, payload = self._request(
            "GET",
            f"{API_PREFIX}/sessions/{session_id}/checkpoints?limit=10&offset=0",
            token=token,
        )

        self.assertEqual(status, 200, payload)
        self.assertTrue(payload["available"])
        self.assertEqual(payload["limit"], 10)
        self.assertEqual(payload["offset"], 0)
        self.assertEqual(len(payload["checkpoints"]), 1)
        self.assertEqual(
            set(payload["checkpoints"][0]), {"hash", "short_hash", "timestamp", "reason"}
        )

    def test_changes_and_diff_routes_contract(self):
        token = self._pair()
        session_id = self._session(token)
        _write_file(self.workdir, "app.py", "one\ntwo\nthree\n")
        self._checkpoint("turn-1")
        revision = self._revision()
        _write_file(self.workdir, "app.py", "one\nTWO\nthree\nfour\n")

        status, changes = self._request(
            "GET",
            f"{API_PREFIX}/sessions/{session_id}/checkpoints/{revision}/changes",
            token=token,
        )

        self.assertEqual(status, 200, changes)
        self.assertEqual(changes["totals"], {"files": 1, "additions": 2, "deletions": 1})
        self.assertEqual(changes["files"][0]["status"], "modified")

        status, diff = self._request(
            "GET",
            f"{API_PREFIX}/sessions/{session_id}/checkpoints/{revision}/diff"
            f"?path={quote('app.py')}",
            token=token,
        )

        self.assertEqual(status, 200, diff)
        self.assertEqual(diff["path"], "app.py")
        self.assertEqual(diff["source"], "checkpoint")
        self.assertEqual(diff["from"], revision)
        self.assertFalse(diff["truncated"])
        types = {line["type"] for hunk in diff["hunks"] for line in hunk["lines"]}
        self.assertIn("-", types)
        self.assertIn("+", types)

    def test_unknown_revision_and_traversal_are_404(self):
        token = self._pair()
        session_id = self._session(token)
        _write_file(self.workdir, "app.py", "one\n")
        self._checkpoint("turn-1")
        revision = self._revision()

        status, _ = self._request(
            "GET",
            f"{API_PREFIX}/sessions/{session_id}/checkpoints/{'0' * 40}/changes",
            token=token,
        )
        self.assertEqual(status, 404)

        status, _ = self._request(
            "GET",
            f"{API_PREFIX}/sessions/{session_id}/checkpoints/{revision}/diff"
            f"?path={quote('../escape.py')}",
            token=token,
        )
        self.assertEqual(status, 404)

    def test_binary_diff_is_unavailable(self):
        token = self._pair()
        session_id = self._session(token)
        _write_file(self.workdir, "base.txt", "x\n")
        self._checkpoint("turn-1")
        revision = self._revision()
        (Path(self.workdir) / "blob.bin").write_bytes(b"\x00\x01binary")

        status, diff = self._request(
            "GET",
            f"{API_PREFIX}/sessions/{session_id}/checkpoints/{revision}/diff"
            f"?path={quote('blob.bin')}",
            token=token,
        )

        self.assertEqual(status, 200, diff)
        self.assertEqual(diff["source"], "unavailable")
        self.assertEqual(diff["from"], revision)
        self.assertEqual(diff["hunks"], [])


if __name__ == "__main__":
    unittest.main()
