import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from factory.core.terminal_render_log import terminal_render_log


class TerminalRenderLogTest(unittest.TestCase):
    def test_terminal_render_log_is_disabled_by_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "render.jsonl"
            with patch.dict("os.environ", {}, clear=True):
                terminal_render_log("test.event", line="hello")

            self.assertFalse(path.exists())

    def test_terminal_render_log_writes_jsonl_when_path_is_configured(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "render.jsonl"
            with patch.dict("os.environ", {"IGOREK_TERMINAL_RENDER_LOG": str(path)}, clear=True):
                terminal_render_log("test.event", line="hello")

            rows = path.read_text(encoding="utf-8").splitlines()
            self.assertEqual(len(rows), 1)
            payload = json.loads(rows[0])
            self.assertEqual(payload["event"], "test.event")
            self.assertEqual(payload["line"], "hello")


if __name__ == "__main__":
    unittest.main()
