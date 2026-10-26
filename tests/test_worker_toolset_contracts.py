import unittest

from factory.core.worker import _only_tools, _task_supports_interactive_input, _without_tool


class TestWorkerToolsetContracts(unittest.TestCase):
    def test_without_tool_removes_schemas_with_trimmed_name(self):
        """Remove schemas whose trimmed name matches the given tool name."""
        schemas = [
            {"name": "Read"},
            {"name": "Write"},
            {"name": "  Read  "},
        ]
        result = _without_tool(schemas, "Read")
        self.assertEqual(result, [{"name": "Write"}])

    def test_without_tool_preserves_order_and_other_fields(self):
        """Preserve order and extra schema fields; do not mutate the input list."""
        schemas = [
            {"name": "Write", "description": "Writes a file."},
            {"name": "Read", "description": "Reads a file."},
            {"name": "Grep"},
        ]
        result = _without_tool(schemas, "Read")
        self.assertEqual(result, [
            {"name": "Write", "description": "Writes a file."},
            {"name": "Grep"},
        ])
        self.assertIsNot(result, schemas)
        self.assertEqual(schemas, [
            {"name": "Write", "description": "Writes a file."},
            {"name": "Read", "description": "Reads a file."},
            {"name": "Grep"},
        ])

    def test_only_tools_keeps_only_read_and_grep_in_order(self):
        """Keep only Read and Grep from a mixed list, preserving original order."""
        schemas = [
            {"name": "Read"},
            {"name": "Write"},
            {"name": "Grep"},
            {"name": "delegate_task"},
        ]
        result = _only_tools(schemas, {"Read", "Grep"})
        self.assertEqual(result, [{"name": "Read"}, {"name": "Grep"}])

    def test_only_tools_with_empty_allowed_names_or_schemas(self):
        """Empty allowed-name set or empty schema list yields an empty result."""
        self.assertEqual(_only_tools([], set()), [])
        self.assertEqual(_only_tools([], {"Read", "Grep"}), [])
        self.assertEqual(_only_tools([{"name": "Read"}], set()), [])

    def test_task_supports_interactive_input_allows_supported_sources(self):
        """Interactive input is allowed for supported sources, including edge spaces."""
        allowed_sources = ("cli_chat_rich", "cli_chat_tui", "mobile_bridge")
        for source in allowed_sources:
            with self.subTest(source=source):
                self.assertTrue(_task_supports_interactive_input({"source": source}))
        for source in allowed_sources:
            with self.subTest(source=f"edge_spaces:{source}"):
                self.assertTrue(_task_supports_interactive_input({"source": f"  {source}  "}))

    def test_task_supports_interactive_input_rejects_unsupported_sources(self):
        """Interactive input is rejected for missing, None, empty, or unknown sources."""
        cases = (
            ({}, "missing_source"),
            ({"source": None}, "none_source"),
            ({"source": ""}, "empty_source"),
            ({"source": "unknown_source"}, "unknown_source"),
        )
        for task_metadata, case_name in cases:
            with self.subTest(source=case_name):
                self.assertFalse(_task_supports_interactive_input(task_metadata))


if __name__ == "__main__":
    unittest.main()
