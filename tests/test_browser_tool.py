"""Tests for the `browser` tool (agent-browser CLI wrapper, Hermes pattern).

Covers:
- the tool is registered in the registry with a schema (available when the
  agent-browser CLI resolves, unavailable otherwise);
- action -> argv mapping for the supported actions;
- argument validation (missing action / url / target) returns tool_error;
- CLI failures surface a JSON error, not an exception.
"""

import os
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from factory.core import cc_backend_tools as tools


class _FakeCompleted:
    def __init__(self, returncode=0, stdout="", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class BrowserImageRecipeTest(unittest.TestCase):
    def test_guest_recipe_pins_node_and_checks_browser_cli(self):
        script = (Path(__file__).resolve().parents[1] / "factory/scripts/build_isolation_image.sh").read_text()
        self.assertIn('NODE_VERSION=24.21.0', script)
        self.assertIn('sha256sum --check --status', script)
        self.assertIn('export PATH="/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"', script)
        self.assertIn('npm install --global --prefix /usr/local --allow-scripts=agent-browser', script)
        self.assertIn('bubblewrap passwd sudo apparmor', script)
        self.assertNotIn('bubblewrap nodejs npm passwd', script)
        self.assertIn('test "$(node --version)" = "v${NODE_VERSION}"', script)
        self.assertIn('command -v agent-browser >/dev/null', script)
        self.assertIn('agent-browser --version', script)
        self.assertIn('BUILDER="${IGOREK_ISOLATION_BUILDER:-auto}"', script)
        self.assertIn('debootstrap)', script)
        self.assertIn('exec /usr/bin/curl --retry 5 --retry-all-errors --retry-delay 2', script)
        self.assertIn('Acquire::Retries=5', script)
        self.assertIn('template)', script)
        self.assertIn('IGOREK_ISOLATION_TEMPLATE_IMAGE:-', script)


class BrowserToolTest(unittest.TestCase):
    def setUp(self):
        home = tempfile.TemporaryDirectory(prefix="igorek-browser-test-")
        self.addCleanup(home.cleanup)
        environment = patch.dict(tools.os.environ, {"HOME": home.name})
        environment.start()
        self.addCleanup(environment.stop)
        self.workdir = Path(home.name) / 'work'
        self.workdir.mkdir()

    def _run(self, args, *, returncode=0, stdout="ok", stderr=""):
        captured = {}

        def fake_run(argv, **kwargs):
            captured["argv"] = list(argv)
            captured["kwargs"] = kwargs
            if 'screenshot' in argv:
                Path(argv[argv.index('screenshot') + 1]).write_bytes(b'image fixture')
            return _FakeCompleted(returncode, stdout, stderr)

        with patch.object(tools, "_agent_browser_path", return_value="/usr/bin/agent-browser"), \
             patch.object(tools.subprocess, "run", side_effect=fake_run):
            result = tools._handle_browser(args, current_session="s1", workdir=str(self.workdir))
        return result, captured

    def test_registered_schema(self):
        schema = tools.registry.get_schema("browser")
        self.assertIsNotNone(schema)
        self.assertEqual(schema["name"], "browser")
        self.assertIn("action", schema["input_schema"]["properties"])

    def test_listed_when_available(self):
        with patch.object(tools, "browser_tools_available", return_value=True):
            names = [t["name"] for t in tools.get_tool_schemas()]
        self.assertIn("browser", names)

    def test_availability_follows_cli(self):
        with patch.object(tools, "_agent_browser_path", return_value=""):
            self.assertFalse(tools.browser_tools_available())
        with patch.object(tools, "_agent_browser_path", return_value="/usr/bin/agent-browser"):
            self.assertTrue(tools.browser_tools_available())

    def test_open_builds_argv(self):
        result, cap = self._run({"action": "open", "url": "https://example.com"})
        self.assertEqual(
            cap["argv"][:3], ["/usr/bin/agent-browser", "--session", tools._browser_session_name("s1")]
        )
        self.assertEqual(cap["argv"][3:], ["open", "https://example.com"])
        self.assertEqual(result, "ok")

    def test_snapshot_interactive(self):
        _r, cap = self._run({"action": "snapshot", "interactive_only": True})
        self.assertEqual(cap["argv"][-2:], ["snapshot", "-i"])

    def test_click_by_ref(self):
        _r, cap = self._run({"action": "click", "target": "@e3"})
        self.assertEqual(cap["argv"][-2:], ["click", "@e3"])

    def test_fill(self):
        _r, cap = self._run({"action": "fill", "target": "@e1", "text": "hi"})
        self.assertEqual(cap["argv"][-3:], ["fill", "@e1", "hi"])

    def test_type(self):
        _r, cap = self._run({"action": "type", "target": "@e2", "text": "yo"})
        self.assertEqual(cap["argv"][-3:], ["type", "@e2", "yo"])

    def test_press_key(self):
        _r, cap = self._run({"action": "press", "key": "Enter"})
        self.assertEqual(cap["argv"][-2:], ["press", "Enter"])

    def test_select_value(self):
        _r, cap = self._run({"action": "select", "target": "@e4", "value": "Two"})
        self.assertEqual(cap["argv"][-3:], ["select", "@e4", "Two"])

    def test_get_title(self):
        _r, cap = self._run({"action": "get", "option": "title"})
        self.assertEqual(cap["argv"][-2:], ["get", "title"])

    def test_get_text_with_target(self):
        _r, cap = self._run({"action": "get", "option": "text", "target": "@e1"})
        self.assertEqual(cap["argv"][-3:], ["get", "text", "@e1"])

    def test_screenshot_full(self):
        result, cap = self._run({"action": "screenshot", "path": "capture.png", "full": True})
        self.assertEqual(cap["argv"][-3], "screenshot")
        self.assertEqual(cap["argv"][-1], "--full")
        self.assertIn(str(self.workdir / 'capture.png'), result)
        self.assertEqual((self.workdir / 'capture.png').read_bytes(), b'image fixture')

    def test_default_screenshot_is_published_inside_the_selected_workspace(self):
        result, _ = self._run({'action': 'screenshot'})
        files = list(self.workdir.glob('*.png'))
        self.assertEqual(len(files), 1)
        self.assertIn(str(files[0]), result)
        self.assertEqual(files[0].read_bytes(), b'image fixture')

    def test_scroll_direction(self):
        _r, cap = self._run({"action": "scroll", "direction": "down", "pixels": 300})
        self.assertEqual(cap["argv"][-3:], ["scroll", "down", "300"])

    def test_close_all(self):
        _r, cap = self._run({"action": "close", "all": True})
        self.assertEqual(cap["argv"][-2:], ["close", "--all"])

    def test_back(self):
        _r, cap = self._run({"action": "back"})
        self.assertEqual(cap["argv"][-1], "back")

    def test_missing_action(self):
        with patch.object(tools, "_agent_browser_path", return_value="/usr/bin/agent-browser"):
            result = tools._handle_browser({}, current_session="s1")
        self.assertIn("action is required", result)

    def test_open_requires_url(self):
        with patch.object(tools, "_agent_browser_path", return_value="/usr/bin/agent-browser"):
            result = tools._handle_browser({"action": "open"}, current_session="s1")
        self.assertIn("url is required", result)

    def test_click_requires_target(self):
        with patch.object(tools, "_agent_browser_path", return_value="/usr/bin/agent-browser"):
            result = tools._handle_browser({"action": "click"}, current_session="s1")
        self.assertIn("target is required", result)

    def test_missing_cli(self):
        with patch.object(tools, "_agent_browser_path", return_value=""):
            result = tools._handle_browser({"action": "snapshot"}, current_session="s1")
        self.assertIn("not found", result)

    def test_cli_failure_returns_json_error(self):
        result, _cap = self._run(
            {"action": "open", "url": "https://x"}, returncode=1, stderr="boom"
        )
        self.assertIn("failed", result)
        self.assertIn("boom", result)

    def test_console_action_runs_javascript_in_current_page(self):
        _result, cap = self._run({"action": "console", "text": "document.title"})
        self.assertEqual(cap["argv"][-2:], ["eval", "document.title"])

    def test_guest_browser_runs_directly_inside_vm_and_drops_secret_environment(self):
        source_env = {
            "IGOREK_ISOLATION_GUEST": "1",
            "HOME": "/home/agent",
            "PATH": "/usr/bin:/bin",
            "TAVILY_API_KEY": "canary-must-not-pass",
            "FACTORY_TOKEN": "canary-must-not-pass",
        }
        argv_in = ["/usr/bin/agent-browser", "--session", "i-test", "snapshot"]
        argv, env = tools._browser_subprocess(argv_in, source_env)
        self.assertEqual(argv, argv_in)
        self.assertNotIn("TAVILY_API_KEY", env)
        self.assertNotIn("FACTORY_TOKEN", env)
        self.assertEqual(env["IGOREK_ISOLATION_GUEST"], "1")
        self.assertEqual(env["HOME"], "/home/agent")

    def test_guest_browser_runtime_is_private_and_scoped_to_the_current_session(self):
        with tempfile.TemporaryDirectory() as home, patch.dict(
            tools.os.environ, {"IGOREK_ISOLATION_GUEST": "1", "HOME": home}
        ), patch.object(
            tools, "_agent_browser_path", return_value="/usr/bin/agent-browser"
        ), patch.object(tools.shutil, "which", side_effect=lambda name: f"/usr/bin/{name}"), patch.object(
            tools.subprocess, "run", return_value=SimpleNamespace(returncode=0, stdout="ok", stderr="")
        ) as run, patch.object(tools.shutil, "which", return_value="/usr/bin/setpriv"):
            tools._handle_browser({"action": "snapshot"}, current_session="session-2")
            env = run.call_args.kwargs["env"]
            expected_session = tools._browser_session_name("session-2")
            self.assertEqual(env["AGENT_BROWSER_SESSION"], expected_session)
            self.assertEqual(env["AGENT_BROWSER_NAMESPACE"], expected_session)
            self.assertTrue(expected_session.startswith("i-"))
            self.assertEqual(env["AGENT_BROWSER_SOCKET_DIR"], env["XDG_RUNTIME_DIR"])
            runtime_dir = Path(env["XDG_RUNTIME_DIR"])
            for name, child in (("XDG_CONFIG_HOME", "config"), ("XDG_CACHE_HOME", "cache"), ("XDG_DATA_HOME", "data")):
                self.assertEqual(env[name], str(runtime_dir / child))
                self.assertTrue((runtime_dir / child).is_dir())
                self.assertEqual((runtime_dir / child).stat().st_mode & 0o777, 0o700)
            self.assertEqual(runtime_dir, Path(home) / ".r" / expected_session)
            self.assertLess(len(str(runtime_dir)), 48)
            self.assertTrue(runtime_dir.is_dir())
            self.assertEqual(runtime_dir.stat().st_mode & 0o777, 0o700)
            self.assertTrue(str(runtime_dir).startswith(home + os.sep))

    def test_guest_browser_does_not_block_private_url_before_vm_network_policy(self):
        with patch.dict(tools.os.environ, {"IGOREK_ISOLATION_GUEST": "1"}), \
             patch.object(tools, "_agent_browser_path", return_value="/usr/bin/agent-browser"), \
             patch.object(tools.subprocess, "run", return_value=_FakeCompleted(stdout="queued")) as run:
            result = tools._handle_browser({"action": "open", "url": "http://127.0.0.1/"}, current_session="s1")
        self.assertEqual(result, "queued")
        self.assertIn("http://127.0.0.1/", run.call_args.args[0])

    def test_guest_browser_allows_file_outputs_within_vm(self):
        with patch.dict(tools.os.environ, {"IGOREK_ISOLATION_GUEST": "1"}), \
             patch.object(tools, "_agent_browser_path", return_value="/usr/bin/agent-browser"), \
             patch.object(tools.subprocess, "run", return_value=_FakeCompleted(stdout="ok")):
            screenshot = tools._handle_browser({"action": "screenshot", "path": "/workspace/test.png"}, current_session="s1")
            pdf = tools._handle_browser({"action": "pdf", "path": "/workspace/test.pdf"}, current_session="s1")
        self.assertEqual(screenshot, "ok")
        self.assertEqual(pdf, "ok")
