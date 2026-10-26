import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch

from factory.core import checkpoint_manager as subject


class TestCheckpointBlobContracts(unittest.TestCase):
    def test_checkpoint_blob_returns_preview(self):
        oid = "a" * 40

        with patch.object(subject, "_git_executable", lambda: "fixture-git"), \
             patch.object(subject, "_git_command") as mock_git_command:
            def handler(argv, **kwargs):
                argv = list(argv)
                if argv[1] == "rev-parse":
                    return subprocess.CompletedProcess(argv, 0, oid, "")
                if argv[1] == "cat-file" and argv[2] == "-s":
                    return subprocess.CompletedProcess(argv, 0, "3", "")
                if argv[1] == "cat-file" and argv[2] == "blob":
                    return subprocess.CompletedProcess(argv, 0, "abc", "")
                raise AssertionError("unexpected git argv: %r" % (argv,))

            mock_git_command.side_effect = handler

            result = subject._checkpoint_text("deadbeef", root=Path("."), env={})

        self.assertEqual(result, (True, "abc"))
        self.assertEqual(mock_git_command.call_count, 3)
        third_argv = mock_git_command.call_args_list[2].args[0]
        self.assertEqual(third_argv[-3:-1], ["cat-file", "blob"])
        self.assertEqual(third_argv[-1], oid)

    def test_checkpoint_blob_too_big(self):
        oid = "a" * 40

        with patch.object(subject, "_git_executable", lambda: "fixture-git"), \
             patch.object(subject, "_git_command") as mock_git_command:
            def handler(argv, **kwargs):
                argv = list(argv)
                if argv[1] == "rev-parse":
                    return subprocess.CompletedProcess(argv, 0, oid, "")
                if argv[1] == "cat-file" and argv[2] == "-s":
                    return subprocess.CompletedProcess(
                        argv, 0, str(subject.MAX_CHECKPOINT_PREVIEW_BYTES + 1), ""
                    )
                raise AssertionError("unexpected git argv: %r" % (argv,))

            mock_git_command.side_effect = handler

            result = subject._checkpoint_text("deadbeef", root=Path("."), env={})

        self.assertEqual(result, (False, None))
        self.assertEqual(mock_git_command.call_count, 2)


if __name__ == "__main__":
    unittest.main()
