import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from factory.cli import main as cli_main
from factory.core.cc_backend_tools import execute_tool, get_tool_schemas
from factory.core.mcp_config import (
    add_stdio_server,
    enable_mcp_server_for_profile,
    list_mcp_capabilities_for_profile,
    list_mcp_servers,
)
from factory.core.prompt_assembly import build_cc_backend_system_prompt
from factory.core.worker import _filter_by_toolsets


def _write_profile(data_dir: str, name: str = "cc-test") -> None:
    profiles_dir = Path(data_dir) / "profiles"
    profiles_dir.mkdir(parents=True, exist_ok=True)
    (profiles_dir / f"{name}.json").write_text(
        json.dumps(
            {
                "backend": "cc-backend",
                "provider": "minimax",
                "model": "MiniMax-M3",
                "mcp_servers": [],
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )


class McpRegistryTest(unittest.TestCase):
    def test_registry_capabilities_are_filtered_by_profile_enabled_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            _write_profile(tmp)
            add_stdio_server(
                "xsearch",
                ["python", "server.py"],
                description="Search X/Twitter",
                capabilities=["twitter search", "post lookup"],
                tools_hint=["xsearch_query"],
                data_dir=tmp,
            )

            disabled = list_mcp_capabilities_for_profile(
                "cc-test", data_dir=tmp, enabled=False
            )
            self.assertEqual(len(disabled), 1)
            self.assertEqual(disabled[0]["name"], "xsearch")
            self.assertFalse(disabled[0]["enabled"])
            self.assertEqual(disabled[0]["description"], "Search X/Twitter")
            self.assertEqual(disabled[0]["capabilities"], ["twitter search", "post lookup"])
            self.assertEqual(disabled[0]["tools_hint"], ["xsearch_query"])

            enable_mcp_server_for_profile("cc-test", "xsearch", data_dir=tmp)
            enabled = list_mcp_capabilities_for_profile(
                "cc-test", data_dir=tmp, enabled=True
            )
            self.assertEqual([item["name"] for item in enabled], ["xsearch"])
            self.assertEqual(
                list_mcp_capabilities_for_profile("cc-test", data_dir=tmp, enabled=False),
                [],
            )

    def test_prompt_advertises_disabled_mcp_capability_as_suggestion_only(self):
        prompt = build_cc_backend_system_prompt(
            workdir="/tmp/work",
            filesystem_mode="workspace",
            disabled_mcp_capabilities=[
                {
                    "name": "xsearch",
                    "description": "Search X/Twitter",
                    "capabilities": ["twitter search"],
                    "tools_hint": ["xsearch_query"],
                }
            ],
        )

        self.assertIn("Disabled MCP Capabilities", prompt)
        self.assertIn("xsearch", prompt)
        self.assertIn("Search X/Twitter", prompt)
        self.assertIn("suggest_enable_mcp", prompt)
        self.assertIn("Не утверждай, что можешь использовать их прямо сейчас", prompt)
        self.assertIn("Не выдумывай MCP-серверы вне этого списка", prompt)

    def test_compact_prompt_keeps_disabled_mcp_suggestion_contract(self):
        prompt = build_cc_backend_system_prompt(
            workdir="/tmp/work",
            filesystem_mode="workspace",
            prompt_style="compact",
            disabled_mcp_capabilities=[
                {"name": "xsearch", "description": "Search X/Twitter"}
            ],
        )

        self.assertIn("Disabled MCP Capabilities", prompt)
        self.assertIn("xsearch", prompt)
        self.assertIn("suggest_enable_mcp", prompt)
        self.assertIn("do not invent servers", prompt)

    def test_suggest_enable_mcp_tool_records_registered_disabled_capability(self):
        with tempfile.TemporaryDirectory() as tmp:
            _write_profile(tmp)
            add_stdio_server(
                "xsearch",
                ["python", "server.py"],
                description="Search X/Twitter",
                capabilities=["twitter search"],
                data_dir=tmp,
            )
            with patch.dict(os.environ, {"IGOREK_FACTORY_HOME": tmp}, clear=False), patch(
                "factory.core.cc_backend_tools._ask_enable_mcp_approval",
                return_value={"approved": False, "decision": "reject"},
            ):
                raw = execute_tool(
                    "suggest_enable_mcp",
                    {
                        "server_name": "xsearch",
                        "reason": "Нужен поиск по X/Twitter для задачи",
                    },
                    "/tmp/work",
                    profile="cc-test",
                )

        payload = json.loads(raw)
        self.assertEqual(payload["status"], "suggestion_recorded")
        self.assertEqual(payload["type"], "mcp_enable_suggestion")
        self.assertEqual(payload["server_name"], "xsearch")
        self.assertEqual(payload["profile"], "cc-test")
        self.assertEqual(payload["description"], "Search X/Twitter")
        self.assertEqual(payload["capabilities"], ["twitter search"])

    def test_suggest_enable_mcp_accept_enables_profile_and_clears_global_disabled(self):
        with tempfile.TemporaryDirectory() as tmp:
            _write_profile(tmp)
            add_stdio_server(
                "xsearch",
                ["python", "server.py"],
                disabled=True,
                description="Search X/Twitter",
                capabilities=["twitter search"],
                data_dir=tmp,
            )
            with patch.dict(os.environ, {"IGOREK_FACTORY_HOME": tmp}, clear=False), patch(
                "factory.core.cc_backend_tools._ask_enable_mcp_approval",
                return_value={"approved": True, "decision": "accept"},
            ), patch(
                "factory.core.cc_backend_tools._hot_reload_mcp_server",
                return_value={
                    "status": "ok",
                    "server_name": "xsearch",
                    "tools": ["xsearch_query"],
                    "tool_count": 1,
                },
            ):
                raw = execute_tool(
                    "suggest_enable_mcp",
                    {
                        "server_name": "xsearch",
                        "reason": "Нужен поиск по X/Twitter для задачи",
                    },
                    "/tmp/work",
                    profile="cc-test",
                )
                servers = list_mcp_servers(tmp)
                enabled = list_mcp_capabilities_for_profile(
                    "cc-test", data_dir=tmp, enabled=True
                )

        payload = json.loads(raw)
        self.assertEqual(payload["status"], "enabled")
        self.assertEqual(payload["server_name"], "xsearch")
        self.assertEqual(payload["hot_reload"]["status"], "ok")
        self.assertEqual(payload["hot_reload"]["tools"], ["xsearch_query"])
        self.assertNotIn("disabled", servers["xsearch"])
        self.assertEqual([item["name"] for item in enabled], ["xsearch"])

    def test_suggest_enable_mcp_rejects_unregistered_server(self):
        with tempfile.TemporaryDirectory() as tmp:
            _write_profile(tmp)
            with patch.dict(os.environ, {"IGOREK_FACTORY_HOME": tmp}, clear=False):
                raw = execute_tool(
                    "suggest_enable_mcp",
                    {"server_name": "missing", "reason": "Need it"},
                    "/tmp/work",
                    profile="cc-test",
        )

        payload = json.loads(raw)
        self.assertEqual(payload["error"], "MCP server is not registered")
        self.assertEqual(payload["server_name"], "missing")

    def test_suggest_enable_mcp_schema_is_kept_by_mcp_toolset(self):
        schemas = get_tool_schemas()
        names = {schema["name"] for schema in schemas}
        self.assertIn("suggest_enable_mcp", names)

        filtered = _filter_by_toolsets(schemas, ["mcp"])
        self.assertIn("suggest_enable_mcp", {schema["name"] for schema in filtered})

    def test_cli_add_stdio_accepts_metadata_flags_after_server_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            argv = [
                "igorek-factory",
                "mcp",
                "--data-dir",
                tmp,
                "add-stdio",
                "xsearch",
                "--description",
                "Search X/Twitter",
                "--capability",
                "twitter search",
                "--tool-hint",
                "xsearch_query",
                "--",
                "python",
                "server.py",
            ]
            with patch.object(sys, "argv", argv):
                self.assertEqual(cli_main(), 0)

            registry_path = Path(tmp) / "mcp" / "servers.json"
            payload = json.loads(registry_path.read_text(encoding="utf-8"))
            server = payload["mcpServers"]["xsearch"]

        self.assertEqual(server["command"], "python")
        self.assertEqual(server["args"], ["server.py"])
        self.assertEqual(server["description"], "Search X/Twitter")
        self.assertEqual(server["capabilities"], ["twitter search"])
        self.assertEqual(server["tools_hint"], ["xsearch_query"])


if __name__ == "__main__":
    unittest.main()
