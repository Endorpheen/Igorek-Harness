import unittest

from factory.core.mcp_config import _codex_add_command_for_server


FIXTURE_CODEX_BIN = "fixture-codex"
SERVER_NAME = "fixture-mcp-server"


class TestCodexAddCommandForServer(unittest.TestCase):
    """Contract tests for the Codex MCP add command builder."""

    def _build_command(self, entry):
        return _codex_add_command_for_server(
            SERVER_NAME,
            entry,
            codex_bin=FIXTURE_CODEX_BIN,
        )

    def test_http_add_command_with_bearer_token_env_var(self):
        cmd = self._build_command({
            "type": "http",
            "url": "https://example.invalid/mcp",
            "bearer_token_env_var": "FIXTURE_TOKEN",
        })
        self.assertEqual(
            cmd,
            [
                FIXTURE_CODEX_BIN,
                "mcp",
                "add",
                SERVER_NAME,
                "--url",
                "https://example.invalid/mcp",
                "--bearer-token-env-var",
                "FIXTURE_TOKEN",
            ],
        )

    def test_http_add_command_without_bearer_token_env_var(self):
        cmd = self._build_command({
            "type": "http",
            "url": "https://example.invalid/mcp",
        })
        self.assertEqual(
            cmd,
            [
                FIXTURE_CODEX_BIN,
                "mcp",
                "add",
                SERVER_NAME,
                "--url",
                "https://example.invalid/mcp",
            ],
        )

    def test_stdio_add_command_keeps_args_as_separate_argv(self):
        cmd = self._build_command({
            "type": "stdio",
            "command": " worker ",
            "args": ["a b", "$(do-not-run)", 7],
        })
        self.assertEqual(
            cmd,
            [
                FIXTURE_CODEX_BIN,
                "mcp",
                "add",
                SERVER_NAME,
                "--",
                "worker",
                "a b",
                "$(do-not-run)",
                "7",
            ],
        )

    def test_stdio_add_command_skips_empty_env_key(self):
        cmd = self._build_command({
            "type": "stdio",
            "command": "worker",
            "env": {" KEY ": "a=b c", "": "skip", 4: False},
        })
        self.assertEqual(
            cmd,
            [
                FIXTURE_CODEX_BIN,
                "mcp",
                "add",
                SERVER_NAME,
                "--env",
                "KEY=a=b c",
                "--env",
                "4=False",
                "--",
                "worker",
            ],
        )

    def test_stdio_add_command_ignores_invalid_env_and_args_types(self):
        cmd = self._build_command({
            "type": "stdio",
            "command": "worker",
            "env": ["not-a-dict"],
            "args": "not-a-list",
        })
        self.assertEqual(
            cmd,
            [
                FIXTURE_CODEX_BIN,
                "mcp",
                "add",
                SERVER_NAME,
                "--",
                "worker",
            ],
        )

    def test_http_add_command_with_empty_url_raises_value_error(self):
        with self.assertRaises(ValueError):
            self._build_command({
                "type": "http",
                "url": "",
            })

    def test_stdio_add_command_with_empty_command_raises_value_error(self):
        with self.assertRaises(ValueError):
            self._build_command({
                "type": "stdio",
                "command": "   ",
            })

    def test_add_command_rejects_unknown_server_type(self):
        with self.subTest("unknown type"):
            with self.assertRaises(ValueError):
                self._build_command({
                    "type": "grpc",
                })
        with self.subTest("missing type"):
            with self.assertRaises(ValueError):
                self._build_command({
                    "entry": {},
                })
