import unittest
from unittest.mock import patch

from factory.core.worker import (
    _cc_backend_prompt_style,
    _default_cc_backend_toolsets,
    _filter_by_toolsets,
    _reload_enabled_mcp_servers_for_cc_backend,
    _task_supports_interactive_input,
    _effective_cc_backend_model,
)
from factory.core.tool_registry import registry


class WorkerOpenRouterPromptBudgetTest(unittest.TestCase):
    def test_local_request_uses_exact_served_id_without_duplicate_v1(self):
        from tests.test_local_model_display import _FakeResponse, _fake_payload

        with patch("factory.core.runtime.urllib.request.urlopen", return_value=_FakeResponse(_fake_payload("/models/Bolt.gguf"))) as request:
            model = _effective_cc_backend_model(provider="local", configured_model="old-model", task_metadata={}, base_url="http://localhost:8081/v1")
        self.assertEqual(model, "/models/Bolt.gguf")
        self.assertEqual(request.call_args.args[0], "http://localhost:8081/v1/models")

    def test_cloud_request_keeps_explicit_override_without_model_discovery(self):
        with patch("factory.core.worker.resolve_local_model_id") as resolve:
            model = _effective_cc_backend_model(provider="openrouter", configured_model="cloud-default", task_metadata={"model_override": "chosen-model"}, base_url="https://provider.example/v1")
        self.assertEqual(model, "chosen-model")
        resolve.assert_not_called()

    def test_openrouter_defaults_to_compact_prompt_and_lean_toolsets(self):
        config = {"backend": "cc-backend", "provider": "openrouter"}

        self.assertEqual(_cc_backend_prompt_style(config), "compact")
        self.assertEqual(
            _default_cc_backend_toolsets(config),
            ["file", "terminal", "search", "web", "session", "skills", "mcp", "interactive"],
        )

    def test_explicit_profile_settings_override_openrouter_defaults(self):
        config = {
            "backend": "cc-backend",
            "provider": "openrouter",
            "prompt_style": "full",
            "enabled_toolsets": ["all"],
        }

        self.assertEqual(_cc_backend_prompt_style(config), "full")
        self.assertEqual(_default_cc_backend_toolsets(config), ["all"])

    def test_interactive_input_requires_supported_chat_surface(self):
        self.assertTrue(_task_supports_interactive_input({"source": "cli_chat_rich"}))
        self.assertTrue(_task_supports_interactive_input({"source": "cli_chat_tui"}))
        self.assertTrue(_task_supports_interactive_input({"source": "mobile_bridge", "platform": "android"}))
        self.assertFalse(_task_supports_interactive_input({"source": "scheduler"}))
        self.assertFalse(_task_supports_interactive_input({}))
        self.assertFalse(_task_supports_interactive_input({"platform": "telegram"}))
        self.assertFalse(_task_supports_interactive_input({"source": "matrix"}))

    def test_lean_toolsets_keep_mcp_tools_for_image_generation(self):
        schemas = [
            {"name": "Read"},
            {"name": "Write"},
            {"name": "Edit"},
            {"name": "Bash"},
            {"name": "Glob"},
            {"name": "Grep"},
            {"name": "web_search"},
            {"name": "web_extract"},
            {"name": "SessionSearch"},
            {"name": "delegate_task"},
            {"name": "skill_manage"},
            {"name": "fal_generate_image"},
        ]

        filtered = _filter_by_toolsets(
            schemas, ["file", "terminal", "search", "web", "session", "skills", "mcp"]
        )
        names = {schema["name"] for schema in filtered}

        self.assertEqual(
            names,
            {
                "Read",
                "Write",
                "Edit",
                "Bash",
                "Glob",
                "Grep",
                "web_search",
                "web_extract",
                "SessionSearch",
                "skill_manage",
                "fal_generate_image",
            },
        )

    def test_registry_toolset_filter_keeps_dynamic_mcp_tools(self):
        schema = {
            "name": "test_dynamic_mcp_tool",
            "description": "test",
            "input_schema": {"type": "object", "properties": {}},
        }
        registry.register(
            name="test_dynamic_mcp_tool",
            toolset="mcp",
            schema=schema,
            handler=lambda *_args, **_kwargs: "{}",
        )
        try:
            filtered = _filter_by_toolsets(
                [{"name": "Read"}, {"name": "test_dynamic_mcp_tool"}],
                ["file", "mcp"],
            )
        finally:
            registry.deregister("test_dynamic_mcp_tool")

        names = {item["name"] for item in filtered}
        self.assertEqual(names, {"Read", "test_dynamic_mcp_tool"})

    def test_skills_toolset_keeps_full_skill_workflow(self):
        skill_tools = {
            "skills_categories",
            "skills_list",
            "skill_view",
            "skill_scan",
            "skill_manage",
        }
        schemas = [{"name": name} for name in sorted(skill_tools | {"Read", "Bash"})]

        filtered = _filter_by_toolsets(schemas, ["skills"])

        self.assertEqual({item["name"] for item in filtered}, skill_tools)

    def test_cc_backend_reload_enabled_mcp_servers_uses_profile_registry_entries(self):
        with patch(
            "factory.core.worker.get_profile_mcp_servers",
            return_value=["xsearch"],
        ), patch(
            "factory.core.worker.list_mcp_servers",
            return_value={
                "xsearch": {
                    "type": "stdio",
                    "command": "python3",
                    "args": ["factory/mcp_servers/xsearch_server.py"],
                }
            },
        ), patch(
            "factory.core.mcp_bridge.reload_mcp_server_from_registry",
            return_value={
                "status": "ok",
                "server_name": "xsearch",
                "tools": ["xsearch_query"],
            },
        ) as reload_mock:
            result = _reload_enabled_mcp_servers_for_cc_backend("cc-minimax")

        self.assertEqual(result[0]["status"], "ok")
        self.assertEqual(result[0]["tools"], ["xsearch_query"])
        reload_mock.assert_called_once()


if __name__ == "__main__":
    unittest.main()
