"""Host-side artifact route: per-task path layout and route file contract."""

from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(os.environ.get("IGOREK_FACTORY_REPO", Path(__file__).resolve().parents[1]))
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from factory.core.isolation.artifact_map import (  # noqa: E402
    ARTIFACT_DIRNAME,
    ROUTE_FILENAME,
    ROUTE_VERSION,
    guest_artifact_dir,
    host_artifact_dir,
    read_artifact_route,
    task_segment,
    write_artifact_route,
)


class ArtifactPathTest(unittest.TestCase):
    def test_guest_artifact_dir_sits_under_the_guest_workdir(self):
        path = guest_artifact_dir("/workspace", "12345678-aaaa-bbbb-cccc-dddddddddddd")
        self.assertEqual(path, f"/workspace/{ARTIFACT_DIRNAME}/12345678")
        self.assertTrue(path.startswith("/workspace/"))

    def test_task_segment_is_stable_and_sanitized(self):
        self.assertEqual(task_segment(""), "task")
        self.assertEqual(task_segment(".."), "task")
        self.assertEqual(task_segment("ab/cd ef gh ij"), "ab_cd_ef")
        self.assertEqual(
            task_segment("12345678-aaaa"), task_segment("12345678-bbbb")
        )

    def test_host_artifact_dir_mirrors_the_guest_layout(self):
        mapped = host_artifact_dir("/home/user/project", "task-1")
        self.assertEqual(mapped, Path("/home/user/project") / ARTIFACT_DIRNAME / "task-1")


class ArtifactRouteTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)

    def tearDown(self):
        self._tmp.cleanup()

    def test_round_trip(self):
        mapped = self.root / "workspace" / ARTIFACT_DIRNAME / "task-1"
        write_artifact_route(self.root / "host-artifacts", mapped)

        route = self.root / "host-artifacts" / ROUTE_FILENAME
        payload = json.loads(route.read_text(encoding="utf-8"))
        self.assertEqual(payload["version"], ROUTE_VERSION)
        self.assertEqual(read_artifact_route(self.root / "host-artifacts"), mapped)

    def test_missing_or_malformed_route_falls_back_to_none(self):
        directory = self.root / "host-artifacts"
        self.assertIsNone(read_artifact_route(directory))

        directory.mkdir()
        route = directory / ROUTE_FILENAME
        self.assertIsNone(read_artifact_route(directory))

        route.write_text("{not json", encoding="utf-8")
        self.assertIsNone(read_artifact_route(directory))

        route.write_text(json.dumps({"version": 99, "host_artifact_workdir": "/x"}), encoding="utf-8")
        self.assertIsNone(read_artifact_route(directory))

        route.write_text(
            json.dumps({"version": ROUTE_VERSION, "host_artifact_workdir": "relative/path"}),
            encoding="utf-8",
        )
        self.assertIsNone(read_artifact_route(directory))


if __name__ == "__main__":
    unittest.main()
