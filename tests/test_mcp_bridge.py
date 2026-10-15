import unittest
from unittest.mock import patch

from factory.core import mcp_bridge
from factory.core.tool_registry import registry


class McpBridgeTest(unittest.TestCase):
    def test_tavily_mcp_server_requires_api_key(self):
        with patch.dict("os.environ", {}, clear=True):
            self.assertFalse(mcp_bridge._mcp_server_available("tavily"))

        with patch.dict("os.environ", {"TAVILY_API_KEY": "test"}, clear=True):
            self.assertTrue(mcp_bridge._mcp_server_available("tavily"))

        with patch.dict("os.environ", {"FACTORY_TAVILY_API_KEY": "test"}, clear=True):
            self.assertTrue(mcp_bridge._mcp_server_available("tavily"))

    def test_registered_tavily_mcp_tool_is_hidden_without_key(self):
        class FakeClient:
            name = "tavily"
            env = {}
            tools = [
                {
                    "name": "test_tavily_search_hidden_without_key",
                    "description": "test",
                    "inputSchema": {"type": "object", "properties": {}},
                }
            ]

            def make_handler(self, tool_name):
                return lambda args, **kwargs: "{}"

        tool_name = "test_tavily_search_hidden_without_key"
        registry.deregister(tool_name)
        try:
            mcp_bridge._register_server_tools(FakeClient())
            with patch.dict("os.environ", {}, clear=True):
                names = {tool["name"] for tool in registry.get_definitions("mcp")}
            self.assertNotIn(tool_name, names)

            with patch.dict("os.environ", {"TAVILY_API_KEY": "test"}, clear=True):
                names = {tool["name"] for tool in registry.get_definitions("mcp")}
            self.assertNotIn(tool_name, names)
        finally:
            registry.deregister(tool_name)

    def test_registered_tavily_mcp_tool_uses_server_start_env(self):
        class FakeClient:
            name = "tavily"
            env = {"TAVILY_API_KEY": "test"}
            tools = [
                {
                    "name": "test_tavily_search_visible_with_server_key",
                    "description": "test",
                    "inputSchema": {"type": "object", "properties": {}},
                }
            ]

            def make_handler(self, tool_name):
                return lambda args, **kwargs: "{}"

        tool_name = "test_tavily_search_visible_with_server_key"
        registry.deregister(tool_name)
        try:
            mcp_bridge._register_server_tools(FakeClient())
            with patch.dict("os.environ", {}, clear=True):
                names = {tool["name"] for tool in registry.get_definitions("mcp")}
            self.assertIn(tool_name, names)
        finally:
            registry.deregister(tool_name)

    def test_reload_mcp_server_from_registry_registers_discovered_tools(self):
        tool_name = "test_hot_reload_xsearch_query"
        registry.deregister(tool_name)

        class FakeClient:
            def __init__(self, name, command, args, extra_env):
                self.name = name
                self.command = command
                self.args = args
                self.extra_env = extra_env
                self.env = {}
                self.tools = [
                    {
                        "name": tool_name,
                        "description": "Search X",
                        "inputSchema": {"type": "object", "properties": {}},
                    }
                ]

            def start(self):
                self.env = {"XSEARCH_BEARER_TOKEN": "test"}

            def make_handler(self, tool_name):
                return lambda args, **kwargs: '{"ok": true}'

        try:
            with patch.object(mcp_bridge, "MCPClient", FakeClient):
                result = mcp_bridge.reload_mcp_server_from_registry(
                    "xsearch",
                    {
                        "type": "stdio",
                        "command": "python",
                        "args": ["server.py"],
                    },
                )

            self.assertEqual(result["status"], "ok")
            self.assertEqual(result["tools"], [tool_name])
            self.assertIn(tool_name, {item["name"] for item in registry.get_definitions("mcp")})
        finally:
            registry.deregister(tool_name)

    def test_reload_stops_previous_client_for_same_server(self):
        # Regression: per-task hot reload must not accumulate one live subprocess
        # per call for the same server name (leaked process + stdio pipes).
        stopped: list[str] = []

        class LiveClient:
            def __init__(self, name):
                self.name = name

            def stop(self):
                stopped.append(self.name)

        class FakeClient:
            def __init__(self, name, command, args, extra_env):
                self.name = name
                self.tools = []

            def start(self):
                pass

            def make_handler(self, tool_name):
                return lambda args, **kwargs: "{}"

        previous_fal = LiveClient("FalImage")
        previous_other = LiveClient("tavily")
        mcp_bridge._MCP_CLIENTS.extend([previous_fal, previous_other])
        try:
            with patch.object(mcp_bridge, "MCPClient", FakeClient):
                result = mcp_bridge.reload_mcp_server_from_registry(
                    "FalImage",
                    {"type": "stdio", "command": "python", "args": ["fal_server.py"]},
                )

            self.assertEqual(result["status"], "ok")
            self.assertIn("FalImage", stopped)
            self.assertNotIn(previous_fal, mcp_bridge._MCP_CLIENTS)
            self.assertIn(previous_other, mcp_bridge._MCP_CLIENTS)
            # Exactly one live client remains for the reloaded server name.
            self.assertEqual(
                [c.name for c in mcp_bridge._MCP_CLIENTS if c.name == "FalImage"],
                ["FalImage"],
            )
        finally:
            mcp_bridge._stop_and_evict_clients("FalImage")
            mcp_bridge._stop_and_evict_clients("tavily")

    def test_failed_start_keeps_previous_client_alive(self):
        # Regression: the replacement must be started before the previous live
        # client is evicted, otherwise a failed handshake takes down a server
        # that was working.
        stopped: list[str] = []

        class LiveClient:
            def __init__(self, name):
                self.name = name

            def stop(self):
                stopped.append(self.name)

        class FailingClient:
            instances: list = []

            def __init__(self, name, command, args, extra_env):
                self.name = name
                self.tools = []
                self.stopped = False
                FailingClient.instances.append(self)

            def start(self):
                raise RuntimeError("handshake failed")

            def stop(self):
                self.stopped = True

        previous = LiveClient("FalImage")
        mcp_bridge._MCP_CLIENTS.append(previous)
        try:
            with patch.object(mcp_bridge, "MCPClient", FailingClient):
                result = mcp_bridge.reload_mcp_server_from_registry(
                    "FalImage",
                    {"type": "stdio", "command": "python", "args": ["fal_server.py"]},
                )

            self.assertEqual(result["status"], "error")
            # Previous working client survives; it was not evicted or stopped.
            self.assertIn(previous, mcp_bridge._MCP_CLIENTS)
            self.assertNotIn("FalImage", stopped)
            # The half-started replacement is cleaned up instead of leaking.
            self.assertTrue(FailingClient.instances)
            self.assertTrue(FailingClient.instances[0].stopped)
        finally:
            mcp_bridge._stop_and_evict_clients("FalImage")

    def test_discover_registers_and_evicts_previous_client(self):
        # The startup path must enforce the same "one client per server name"
        # invariant as the hot-reload path.
        stopped: list[str] = []

        class LiveClient:
            def __init__(self, name):
                self.name = name

            def stop(self):
                stopped.append(self.name)

        class FakeClient:
            def __init__(self, name, command, args, extra_env):
                self.name = name
                self.tools = []

            def start(self):
                pass

            def make_handler(self, tool_name):
                return lambda args, **kwargs: "{}"

        previous = LiveClient("FalImage")
        mcp_bridge._MCP_CLIENTS.append(previous)
        try:
            with patch.object(
                mcp_bridge,
                "_load_mcp_config",
                return_value=[("FalImage", "python", ["fal_server.py"], {})],
            ), patch.object(mcp_bridge, "MCPClient", FakeClient):
                mcp_bridge.discover_and_register_mcp_tools()

            self.assertIn("FalImage", stopped)
            self.assertNotIn(previous, mcp_bridge._MCP_CLIENTS)
            self.assertEqual(
                [c.name for c in mcp_bridge._MCP_CLIENTS if c.name == "FalImage"],
                ["FalImage"],
            )
        finally:
            mcp_bridge._stop_and_evict_clients("FalImage")


if __name__ == "__main__":
    unittest.main()
