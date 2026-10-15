import tempfile
import unittest
from pathlib import Path

from factory.core.cc_backend_tools import _tool_read


class CcBackendReadToolTest(unittest.TestCase):
    def test_read_honors_offset_and_limit_lines(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "sample.txt"
            path.write_text("l0\nl1\nl2\nl3\n", encoding="utf-8")

            result = _tool_read(
                {"file_path": str(path), "offset": 1, "limit": 2},
                workdir=tmp,
                filesystem_mode="workspace",
            )

        self.assertEqual(result, "l1\nl2\n")

    def test_unbounded_large_read_is_blocked_with_range_hint(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "large.txt"
            path.write_text(("0123456789\n" * 6000), encoding="utf-8")

            result = _tool_read(
                {"file_path": str(path)},
                workdir=tmp,
                filesystem_mode="workspace",
            )

        self.assertIn("unbounded read limit", result)
        self.assertIn("offset/limit", result)
        self.assertLess(len(result), 300)

    def test_large_read_with_range_returns_requested_chunk(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "large.txt"
            path.write_text("".join(f"line-{idx}\n" for idx in range(6000)), encoding="utf-8")

            result = _tool_read(
                {"file_path": str(path), "offset": 5998, "limit": 2},
                workdir=tmp,
                filesystem_mode="workspace",
            )

        self.assertEqual(result, "line-5998\nline-5999\n")


if __name__ == "__main__":
    unittest.main()
