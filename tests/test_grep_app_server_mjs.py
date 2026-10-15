#!/usr/bin/env python3
"""Stdio handshake + live search test for the Node grep.app MCP server.

The server is ``factory/mcp_servers/grep_app_server.mjs`` (dependency-free Node
stdio MCP bridging to the official https://mcp.grep.app endpoint). The
handshake test is deterministic; the live search test hits the real upstream
and is skipped (not failed) when the network is unavailable.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SERVER_PATH = REPO_ROOT / "factory" / "mcp_servers" / "grep_app_server.mjs"


def _rpc(proc: subprocess.Popen, message: dict, *, expect_response: bool = True, timeout: float = 45.0):
    proc.stdin.write(json.dumps(message) + "\n")
    proc.stdin.flush()
    if not expect_response:
        return None
    line = proc.stdout.readline()
    if not line:
        stderr = proc.stderr.read() if proc.stderr else ""
        raise AssertionError(f"server closed stdout; stderr={stderr!r}")
    return json.loads(line)


class GrepAppNodeStdioTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if shutil.which("node") is None:
            raise unittest.SkipTest("node is not installed")
        if not SERVER_PATH.exists():
            raise AssertionError(f"missing server file: {SERVER_PATH}")

    def setUp(self):
        self.proc = subprocess.Popen(
            ["node", str(SERVER_PATH)],
            cwd=str(REPO_ROOT),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

    def tearDown(self):
        self.proc.terminate()
        try:
            self.proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait(timeout=5)
        for stream in (self.proc.stdin, self.proc.stdout, self.proc.stderr):
            if stream:
                stream.close()

    def _handshake(self):
        init = _rpc(
            self.proc,
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "test", "version": "0"},
                },
            },
        )
        self.assertEqual(init["result"]["serverInfo"]["name"], "grep_app")
        _rpc(
            self.proc,
            {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}},
            expect_response=False,
        )
        return init

    def test_initialize_ping_and_tools_list(self):
        init = self._handshake()
        self.assertEqual(init["result"]["protocolVersion"], "2024-11-05")

        ping = _rpc(self.proc, {"jsonrpc": "2.0", "id": 2, "method": "ping", "params": {}})
        self.assertEqual(ping["result"], {})

        tools = _rpc(self.proc, {"jsonrpc": "2.0", "id": 3, "method": "tools/list", "params": {}})
        names = [tool["name"] for tool in tools["result"]["tools"]]
        self.assertEqual(names, ["search_github"])
        schema = tools["result"]["tools"][0]["inputSchema"]
        self.assertEqual(schema["required"], ["query"])

    def test_unknown_tool_returns_error(self):
        self._handshake()
        response = _rpc(
            self.proc,
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {"name": "nope", "arguments": {}},
            },
        )
        self.assertEqual(response["error"]["code"], -32602)

    def test_missing_query_returns_tool_error(self):
        self._handshake()
        response = _rpc(
            self.proc,
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {"name": "search_github", "arguments": {}},
            },
        )
        self.assertTrue(response["result"]["isError"])
        self.assertIn("query", response["result"]["content"][0]["text"])

    def test_live_search_returns_real_hits(self):
        self._handshake()
        response = _rpc(
            self.proc,
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {
                    "name": "search_github",
                    "arguments": {"query": "requests.get", "language": "Python"},
                },
            },
            timeout=60.0,
        )
        result = response.get("result", {})
        text = result.get("content", [{}])[0].get("text", "")
        if result.get("isError"):
            self.skipTest(f"upstream unavailable: {text}")
        self.assertIn("URL_COPY_CONTRACT", text)
        self.assertIn("Repository:", text)
        self.assertIn("URL: https://github.com/", text)


if __name__ == "__main__":
    unittest.main()
