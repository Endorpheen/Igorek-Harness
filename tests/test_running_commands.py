"""Real process lifecycle and cancellation for the session activity card."""
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from factory.core.running_commands import command_context, list_commands, request_stop, run_process
from factory.core.runtime import get_db_path
from factory.core.schema import initialize_database, open_db


class RunningCommandsTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.environment = patch.dict(os.environ, {"IGOREK_FACTORY_HOME": self.temp.name, "IGOREK_BASH_SANDBOX": "off"})
        self.environment.start()
        initialize_database(get_db_path())
        self.results = {}
        self.threads = []

    def tearDown(self):
        for session in ("first", "second"):
            for item in list_commands(session):
                request_stop(session, item["id"])
        for thread in self.threads:
            thread.join(4)
        self.environment.stop()
        self.temp.cleanup()

    def start(self, session="first", tool="call", *, delay=10, timeout=15):
        def worker():
            try:
                with command_context(session, "task-" + session, tool):
                    self.results[tool] = run_process([sys.executable, "-c", f"import time; print('started',flush=True); time.sleep({delay})"],
                                                      label="fixture command " + tool, cwd=self.temp.name,
                                                      capture_output=True, text=True, timeout=timeout)
            except BaseException as error:
                self.results[tool] = error
        thread = threading.Thread(target=worker)
        self.threads.append(thread)
        thread.start()
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            rows = [item for item in list_commands(session) if item["tool_use_id"] == tool]
            if rows:
                return rows[0], thread
            time.sleep(.02)
        self.fail("command did not register")

    def test_stop_targets_only_the_exact_live_command_and_session(self):
        row, thread = self.start()
        other, other_thread = self.start("second", "other", delay=.8)
        self.assertFalse(request_stop("second", row["id"]))
        self.assertTrue(request_stop("first", row["id"]))
        thread.join(3)
        self.assertFalse(thread.is_alive())
        self.assertLess(self.results["call"].returncode, 0)
        self.assertIn("Command stopped by user", self.results["call"].stderr)
        self.assertEqual(list_commands("first"), [])
        self.assertEqual(list_commands("second")[0]["id"], other["id"])
        other_thread.join(2)
        self.assertEqual(self.results["other"].returncode, 0)
        self.assertEqual(list_commands("second"), [])

    def test_completed_output_and_exit_code_are_unchanged(self):
        with command_context("first", "task", "done"):
            result = run_process([sys.executable, "-c", "import sys; print('output'); print('error',file=sys.stderr); sys.exit(3)"],
                                 label="fixture", capture_output=True, text=True, timeout=2)
        self.assertEqual((result.returncode, result.stdout, result.stderr), (3, "output\n", "error\n"))
        self.assertEqual(list_commands("first"), [])

    def test_timeout_removes_live_entry_and_reaps_process(self):
        _, thread = self.start(timeout=.3)
        thread.join(2)
        self.assertIsInstance(self.results["call"], subprocess.TimeoutExpired)
        self.assertEqual(list_commands("first"), [])

    def test_reused_pid_identity_and_finished_rows_are_not_exposed_or_stoppable(self):
        row, thread = self.start(delay=.4)
        with open_db(get_db_path()) as conn:
            conn.execute("UPDATE running_commands SET owner_start = 'wrong' WHERE id = ?", (row["id"],))
        self.assertEqual(list_commands("first"), [])
        self.assertFalse(request_stop("first", row["id"]))
        thread.join(2)
        self.assertEqual(self.results["call"].returncode, 0)

    def test_bash_dispatch_preserves_guard_and_output_for_task_context(self):
        from factory.core import cc_backend_tools as tools
        with patch.object(tools, "_bash_scope_denial", return_value="DENIED"), patch("factory.core.running_commands.subprocess.Popen") as spawn:
            result = tools.execute_tool("Bash", {"command": "printf denied"}, self.temp.name,
                                        current_session="first", task_id="task", tool_use_id="blocked")
        self.assertEqual(result, "DENIED")
        spawn.assert_not_called()
        with patch.object(tools, "_bash_scope_denial", return_value=None):
            result = tools.execute_tool("Bash", {"command": "printf allowed"}, self.temp.name,
                                        current_session="first", task_id="task", tool_use_id="allowed")
        self.assertEqual(result, "allowed")
        self.assertEqual(list_commands("first"), [])

    def test_calls_without_task_context_keep_the_legacy_runner(self):
        expected = subprocess.CompletedProcess("fixture", 0, "same", "")
        with patch("factory.core.running_commands.subprocess.run", return_value=expected) as run:
            self.assertIs(run_process("fixture", label="fixture", shell=True, capture_output=True, text=True), expected)
        self.assertEqual(run.call_args.kwargs, {"shell": True, "capture_output": True, "text": True})

    def test_guest_context_keeps_guest_execution_and_does_not_create_host_activity(self):
        expected = subprocess.CompletedProcess("fixture", 0, "guest", "")
        with command_context("first", "guest-task", "guest-call"), patch("factory.core.isolation.guest_bwrap.guest_mode_requested",return_value=True), patch("factory.core.running_commands.subprocess.run",return_value=expected) as run:
            self.assertIs(run_process("fixture", label="fixture", shell=True, capture_output=True, text=True),expected)
        self.assertTrue(run.called)
        self.assertEqual(list_commands("first"), [])
