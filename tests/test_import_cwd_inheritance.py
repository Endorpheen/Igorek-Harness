"""Regression tests for workspace inheritance during external session import."""

from __future__ import annotations

import json
import os
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from factory.core.cli_chat import _session_path
from factory.core.external_sessions import ExternalSessionRecord
from factory.core.isolation.errors import IsolationError
from factory.core.isolation.workspace import resolve_workspace_cwd
from factory.core.mobile_bridge.service import MobileService
from factory.core.runtime import get_data_root, get_db_path
from factory.core.schema import initialize_database


class ResolveWorkspaceCwdTest(unittest.TestCase):
    def test_accepts_a_plain_directory_and_returns_realpath(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp).resolve()
            self.assertEqual(resolve_workspace_cwd(str(target)), str(target))
            # Admission checks the resolved path, regardless of its spelling.
            self.assertEqual(resolve_workspace_cwd(target / ".." / target.name), str(target))

    def test_refuses_empty_and_relative_values(self):
        for value, code in (("", "invalid_cwd_empty"), ("relative/dir", "invalid_cwd_relative")):
            with self.assertRaises(IsolationError) as ctx:
                resolve_workspace_cwd(value)
            self.assertEqual(ctx.exception.code, code)

    def test_refuses_missing_directory(self):
        missing = Path(tempfile.gettempdir()) / "igorek-cwd-inheritance-missing-probe"
        with self.assertRaises(IsolationError) as ctx:
            resolve_workspace_cwd(str(missing))
        self.assertEqual(ctx.exception.code, "invalid_cwd_missing")

    def test_refuses_home_and_data_root(self):
        with self.assertRaises(IsolationError) as ctx:
            resolve_workspace_cwd(str(Path.home()))
        self.assertEqual(ctx.exception.code, "sensitive_path_denied")
        with self.assertRaises(IsolationError) as ctx:
            resolve_workspace_cwd(str(get_data_root()))
        self.assertEqual(ctx.exception.code, "data_root_denied")

    def test_same_verdict_as_the_vm_start_check(self):
        from factory.core.isolation.workspace import resolve_workspace_export

        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp).resolve()
            spec = resolve_workspace_export(str(target))
            self.assertEqual(spec["host_path"], resolve_workspace_cwd(str(target)))
            self.assertEqual(spec["guest_path"], "/workspace")


class ImportCwdInheritanceTest(unittest.TestCase):
    """Verify the workspace selected for an imported external session."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self._env = patch.dict(
            os.environ,
            {"IGOREK_FACTORY_HOME": self._tmp.name, "FACTORY_DATA_DIR": ""},
        )
        self._env.start()
        initialize_database(get_db_path())
        # Keep both workspaces outside data_root so isolation can admit them.
        self.service_workdir = tempfile.mkdtemp(prefix="igorek-service-workdir-")
        self.origin_dir = tempfile.mkdtemp(prefix="igorek-origin-workdir-")
        self.service = MobileService(profile="cc-local-8081", workdir=self.service_workdir)

    def tearDown(self):
        self._env.stop()
        self._tmp.cleanup()
        shutil.rmtree(self.service_workdir, ignore_errors=True)
        shutil.rmtree(self.origin_dir, ignore_errors=True)

    def _import(self, origin_cwd: str, *, cwd: str = "", key: str = "import-1") -> dict:
        class FakeCatalog:
            def import_messages(self, source, session_id):
                return (
                    ExternalSessionRecord(
                        source=source,
                        session_id=session_id,
                        title="Previous session",
                        cwd=origin_cwd,
                        created_at=1,
                        updated_at=2,
                    ),
                    [
                        {"role": "user", "text": "old question", "ts": 1},
                        {"role": "assistant", "text": "old answer", "ts": 2},
                    ],
                    0,
                )

        self.service.external_sessions_catalog = FakeCatalog()
        return self.service.import_external_session(
            "opencode",
            "external-1",
            profile="cc-local-8081",
            cwd=cwd,
            idempotency_key=key,
        )

    def _meta(self, session_id: str) -> dict:
        path = _session_path(session_id)
        return json.loads(path.read_text(encoding="utf-8").splitlines()[0])

    def test_inherits_origin_cwd_when_isolation_allows_it(self):
        result = self._import(self.origin_dir)

        expected = str(Path(self.origin_dir).resolve())
        self.assertEqual(result["cwd_source"], "origin")
        self.assertEqual(result["cwd"], expected)
        self.assertEqual(result["cwd_fallback_reason"], "")
        self.assertEqual(result["origin_cwd"], self.origin_dir)
        meta = self._meta(result["session"]["session_id"])
        self.assertEqual(meta["cwd"], expected)
        self.assertEqual(meta["cwd_source"], "origin")
        self.assertNotIn("cwd_fallback_reason", meta)

    def test_explicit_client_cwd_wins_over_origin(self):
        result = self._import(self.origin_dir, cwd=self.service_workdir)

        self.assertEqual(result["cwd_source"], "requested")
        self.assertEqual(result["cwd"], str(Path(self.service_workdir).resolve()))
        meta = self._meta(result["session"]["session_id"])
        self.assertEqual(meta["cwd"], str(Path(self.service_workdir).resolve()))

    def test_falls_back_when_origin_cwd_is_a_denied_host_location(self):
        result = self._import(str(Path.home()))

        self.assertEqual(result["cwd_source"], "default")
        self.assertTrue(
            result["cwd_fallback_reason"].startswith("origin_cwd_rejected:sensitive_path_denied"),
            result["cwd_fallback_reason"],
        )
        self.assertEqual(result["cwd"], str(Path(self.service_workdir).resolve()))
        meta = self._meta(result["session"]["session_id"])
        self.assertEqual(meta["cwd"], str(Path(self.service_workdir).resolve()))
        self.assertEqual(
            meta["cwd_fallback_reason"], result["cwd_fallback_reason"]
        )

    def test_falls_back_when_origin_cwd_no_longer_exists(self):
        result = self._import(str(Path(self.origin_dir) / "gone"))

        self.assertEqual(result["cwd_source"], "default")
        self.assertEqual(
            result["cwd_fallback_reason"], "origin_cwd_rejected:invalid_cwd_missing"
        )
        self.assertEqual(result["cwd"], str(Path(self.service_workdir).resolve()))

    def test_empty_origin_cwd_leaves_the_service_workdir(self):
        result = self._import("")

        self.assertEqual(result["cwd_source"], "default")
        self.assertEqual(result["cwd_fallback_reason"], "origin_cwd_empty")
        self.assertEqual(result["cwd"], str(Path(self.service_workdir).resolve()))

    def test_the_runtime_data_root_never_becomes_a_session_workdir(self):
        result = self._import(self._tmp.name)

        self.assertEqual(result["cwd_source"], "default")
        self.assertTrue(
            result["cwd_fallback_reason"].startswith("origin_cwd_rejected:data_root_denied"),
            result["cwd_fallback_reason"],
        )
        meta = self._meta(result["session"]["session_id"])
        self.assertEqual(meta["cwd"], str(Path(self.service_workdir).resolve()))
        self.assertNotEqual(meta["cwd"], self._tmp.name)

    def test_repeated_import_reports_the_stored_workdir(self):
        first = self._import(self.origin_dir, key="dup-import")
        second = self._import(self.origin_dir, key="dup-import")

        self.assertFalse(first["duplicate"])
        self.assertTrue(second["duplicate"])
        self.assertEqual(second["cwd"], first["cwd"])
        self.assertEqual(second["cwd_source"], "origin")
        self.assertEqual(second["imported_messages"], 0)

    def test_imported_history_is_still_copied(self):
        result = self._import(self.origin_dir)

        self.assertEqual(result["imported_messages"], 2)
        self.assertEqual(
            [item["text"] for item in result["session"]["messages"]],
            ["old question", "old answer"],
        )
        self.assertEqual(result["session"]["meta"]["origin_session_id"], "external-1")


if __name__ == "__main__":
    unittest.main()
