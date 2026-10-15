import unittest
from pathlib import Path

from factory.core.tool_call_salvage import (
    contains_textual_tool_artifact,
    get_salvage_meta,
    strip_salvage_meta,
    try_salvage_textual_tool_call,
)


FIXTURES = Path(__file__).parent / "fixtures" / "tool_salvage"
WORKDIR = "/home/example/.igorek-agent-factory/workspace/temp_execution_test"


TOOLS = [
    {
        "name": "Bash",
        "input_schema": {
            "type": "object",
            "properties": {
                "command": {"type": "string"},
                "timeout": {"type": "integer"},
            },
            "required": ["command"],
        },
    },
    {
        "name": "Write",
        "input_schema": {
            "type": "object",
            "properties": {
                "file_path": {"type": "string"},
                "content": {"type": "string"},
            },
            "required": ["file_path", "content"],
        },
    },
    {
        "name": "Read",
        "input_schema": {
            "type": "object",
            "properties": {
                "file_path": {"type": "string"},
                "offset": {"type": "integer"},
                "limit": {"type": "integer"},
            },
            "required": ["file_path"],
        },
    },
    {
        "name": "Grep",
        "input_schema": {
            "type": "object",
            "properties": {
                "pattern": {"type": "string"},
                "path": {"type": "string"},
            },
            "required": ["pattern"],
        },
    },
    {
        "name": "skill_manage",
        "input_schema": {
            "type": "object",
            "properties": {
                "action": {"type": "string"},
                "name": {"type": "string"},
                "content": {"type": "string"},
                "category": {"type": "string"},
                "file_path": {"type": "string"},
                "file_content": {"type": "string"},
                "old_string": {"type": "string"},
                "new_string": {"type": "string"},
                "replace_all": {"type": "boolean"},
            },
            "required": ["action", "name"],
        },
    },
    {
        "name": "skill_scan",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "source": {"type": "string"},
            },
            "required": ["name"],
        },
    },
    {
        "name": "skill_view",
        "input_schema": {
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "file_path": {"type": "string"},
            },
            "required": ["name"],
        },
    },
    {
        "name": "skills_list",
        "input_schema": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
]


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def salvage(name: str):
    return try_salvage_textual_tool_call(fixture(name), TOOLS, WORKDIR)


class ToolCallSalvageFixturesTest(unittest.TestCase):
    def test_salvages_confirmed_bash_colon_trace_shape(self):
        tool_call, ok, error = salvage("bash_colon_grok.txt")

        self.assertTrue(ok, error)
        self.assertIsNotNone(tool_call)
        assert tool_call is not None
        self.assertEqual(tool_call["name"], "Bash")
        self.assertTrue(tool_call["input"]["command"].startswith("cat "))
        self.assertEqual(get_salvage_meta(tool_call)["parser"], "bash_pseudo")
        self.assertEqual(
            get_salvage_meta(tool_call)["source_shape"], "bash_colon_line"
        )

    def test_salvages_bash_colon_fenced_block_without_language_tag_as_command(self):
        tool_call, ok, error = salvage("bash_colon_fenced_git_c.txt")

        self.assertTrue(ok, error)
        self.assertIsNotNone(tool_call)
        assert tool_call is not None
        self.assertEqual(tool_call["name"], "Bash")
        self.assertEqual(
            tool_call["input"]["command"],
            "git -C /home/example/AI/paroquant-test diff 261a702 -- factory/core/tool_call_salvage.py",
        )
        self.assertNotEqual(tool_call["input"]["command"], "bash")
        meta = get_salvage_meta(tool_call)
        self.assertEqual(meta["parser"], "bash_pseudo")
        self.assertEqual(meta["source_shape"], "bash_colon_fenced_code_block")

    def test_salvages_shell_command_line_as_bash(self):
        tool_call, ok, error = salvage("shell_command_git_c.txt")

        self.assertTrue(ok, error)
        self.assertIsNotNone(tool_call)
        assert tool_call is not None
        self.assertEqual(tool_call["name"], "Bash")
        self.assertEqual(
            tool_call["input"]["command"],
            "git -C /home/example/AI/paroquant-test diff 261a702 -- factory/core/tool_call_salvage.py tests/test_tool_call_salvage.py",
        )
        meta = get_salvage_meta(tool_call)
        self.assertEqual(meta["parser"], "bash_pseudo")
        self.assertEqual(meta["source_shape"], "shell_command_line")

    def test_salvages_bash_label_newline_command_as_bash(self):
        tool_call, ok, error = salvage("bash_label_newline_git_c.txt")

        self.assertTrue(ok, error)
        self.assertIsNotNone(tool_call)
        assert tool_call is not None
        self.assertEqual(tool_call["name"], "Bash")
        self.assertEqual(
            tool_call["input"]["command"],
            "git -C /home/example/AI/paroquant-test diff 261a702^..261a702 -- factory/core/tool_call_salvage.py tests/test_tool_call_salvage.py",
        )
        meta = get_salvage_meta(tool_call)
        self.assertEqual(meta["parser"], "bash_pseudo")
        self.assertEqual(meta["source_shape"], "bash_label_newline_command")

    def test_marks_fenced_bash_as_unconfirmed_log_evidence_without_removing_behavior(self):
        tool_call, ok, error = salvage("bash_fenced_unconfirmed.txt")

        self.assertTrue(ok, error)
        self.assertIsNotNone(tool_call)
        assert tool_call is not None
        self.assertEqual(tool_call["name"], "Bash")
        meta = get_salvage_meta(tool_call)
        self.assertEqual(meta["parser"], "bash_code_block")
        self.assertEqual(meta["source_shape"], "fenced_bash_code_block")
        self.assertTrue(meta["evidence"].startswith("Unconfirmed"))

    def test_salvages_confirmed_loose_gemma_skill_create_shape(self):
        tool_call, ok, error = salvage("skill_loose_gemma_create.txt")

        self.assertTrue(ok, error)
        self.assertIsNotNone(tool_call)
        assert tool_call is not None
        self.assertEqual(tool_call["name"], "skill_manage")
        self.assertEqual(tool_call["input"]["action"], "create")
        self.assertEqual(tool_call["input"]["name"], "igorek-smoke-skill")
        self.assertEqual(get_salvage_meta(tool_call)["parser"], "gemma_pseudo")

    def test_salvages_confirmed_loose_gemma_skill_scan_and_normalizes_name(self):
        tool_call, ok, error = salvage("skill_loose_gemma_scan.txt")

        self.assertTrue(ok, error)
        self.assertIsNotNone(tool_call)
        assert tool_call is not None
        self.assertEqual(tool_call["name"], "skill_scan")
        self.assertEqual(tool_call["input"], {"name": "igorek-smoke-skill"})
        self.assertEqual(get_salvage_meta(tool_call)["parser"], "gemma_pseudo")

    def test_salvages_confirmed_multiline_tool_code_and_ignores_hallucinated_tail(self):
        tool_call, ok, error = salvage("skill_tool_code_multiline.txt")

        self.assertTrue(ok, error)
        self.assertIsNotNone(tool_call)
        assert tool_call is not None
        self.assertEqual(tool_call["name"], "skill_manage")
        self.assertEqual(tool_call["input"]["action"], "create")
        self.assertEqual(tool_call["input"]["name"], "igorek-smoke-skill")
        self.assertNotIn("skill_scan", tool_call["input"]["content"])
        meta = get_salvage_meta(tool_call)
        self.assertEqual(meta["parser"], "function_style")
        self.assertEqual(meta["source_shape"], "tool_code_colon_function_call")

    def test_salvages_plain_single_line_function_style_tool_call(self):
        tool_call, ok, error = salvage("skill_plain_function.txt")

        self.assertTrue(ok, error)
        self.assertIsNotNone(tool_call)
        assert tool_call is not None
        self.assertEqual(tool_call["name"], "skill_manage")
        self.assertEqual(tool_call["input"]["action"], "delete")
        self.assertEqual(
            get_salvage_meta(tool_call)["source_shape"], "plain_function_call"
        )

    def test_salvages_confirmed_bracket_tool_use_shape(self):
        tool_call, ok, error = salvage("bracket_read_tool_use.txt")

        self.assertTrue(ok, error)
        self.assertIsNotNone(tool_call)
        assert tool_call is not None
        self.assertEqual(tool_call["name"], "Read")
        self.assertEqual(
            tool_call["input"]["file_path"],
            "/home/example/AI/paroquant-test/Ledger.md",
        )
        self.assertEqual(tool_call["input"]["limit"], 80)
        meta = get_salvage_meta(tool_call)
        self.assertEqual(meta["parser"], "bracket_tool_use")
        self.assertEqual(meta["source_shape"], "anthropic_bracket_tool_use_text")

    def test_salvages_grep_file_path_alias_as_path(self):
        tool_call, ok, error = try_salvage_textual_tool_call(
            'Grep(pattern="Factory source", file_path="/home/example/AI/paroquant-test/Ledger.md")',
            TOOLS,
            WORKDIR,
        )

        self.assertTrue(ok, error)
        self.assertIsNotNone(tool_call)
        assert tool_call is not None
        self.assertEqual(tool_call["name"], "Grep")
        self.assertEqual(
            tool_call["input"],
            {
                "pattern": "Factory source",
                "path": "/home/example/AI/paroquant-test/Ledger.md",
            },
        )

    def test_detects_confirmed_path_prefixed_function_artifact_for_retry(self):
        text = fixture("skill_path_prefixed.txt")

        self.assertTrue(contains_textual_tool_artifact(text, TOOLS))

    def test_does_not_treat_normal_markdown_indentation_as_tool_artifact(self):
        text = fixture("arxiv_markdown_list_not_tool_artifact.txt")

        self.assertIn("   ", text)
        self.assertFalse(contains_textual_tool_artifact(text, TOOLS))

    def test_strip_salvage_meta_removes_local_trace_fields_before_tool_use_message(self):
        tool_call, ok, error = salvage("bash_colon_grok.txt")

        self.assertTrue(ok, error)
        self.assertIsNotNone(tool_call)
        assert tool_call is not None
        self.assertTrue(get_salvage_meta(tool_call))
        stripped = strip_salvage_meta(tool_call)
        self.assertNotIn("_salvage", stripped)
        self.assertEqual(stripped["name"], tool_call["name"])
        self.assertEqual(stripped["input"], tool_call["input"])

    def test_salvages_read_path_alias_as_file_path(self):
        tool_call, ok, error = try_salvage_textual_tool_call(
            'Read(path="/home/example/AI/paroquant-test/handoff.md")',
            TOOLS,
            WORKDIR,
        )

        self.assertTrue(ok, error)
        self.assertIsNotNone(tool_call)
        assert tool_call is not None
        self.assertEqual(tool_call["name"], "Read")
        self.assertEqual(
            tool_call["input"],
            {"file_path": "/home/example/AI/paroquant-test/handoff.md"},
        )

    def test_salvages_read_file_alias_as_file_path(self):
        tool_call, ok, error = try_salvage_textual_tool_call(
            'Read(file="/home/example/AI/paroquant-test/Ledger.md")',
            TOOLS,
            WORKDIR,
        )

        self.assertTrue(ok, error)
        self.assertIsNotNone(tool_call)
        assert tool_call is not None
        self.assertEqual(tool_call["name"], "Read")
        self.assertEqual(
            tool_call["input"],
            {"file_path": "/home/example/AI/paroquant-test/Ledger.md"},
        )

    def test_salvages_json_object_bash_rev_parse(self):
        tool_call, ok, error = try_salvage_textual_tool_call(
            'Bash{ "command": "git rev-parse HEAD" }',
            TOOLS,
            WORKDIR,
        )

        self.assertTrue(ok, error)
        self.assertIsNotNone(tool_call)
        assert tool_call is not None
        self.assertEqual(tool_call["name"], "Bash")
        self.assertEqual(tool_call["input"], {"command": "git rev-parse HEAD"})

    def test_salvages_foreign_excalidraw_write_file_as_synthetic_write(self):
        tool_call, ok, error = salvage("foreign_excalidraw_write_file.txt")

        self.assertTrue(ok, error)
        self.assertIsNotNone(tool_call)
        assert tool_call is not None
        self.assertEqual(tool_call["name"], "Write")
        self.assertIn("test_excalidraw_runtime.excalidraw", tool_call["input"]["file_path"])
        import json
        parsed = json.loads(tool_call["input"]["content"])
        self.assertEqual(parsed["type"], "excalidraw")
        self.assertIsInstance(parsed["elements"], list)
        meta = get_salvage_meta(tool_call)
        self.assertEqual(meta["parser"], "foreign_file_write")
        self.assertEqual(meta["source_shape"], "skill_write_file_triple_quoted")

    def test_rejects_foreign_write_file_with_invalid_json(self):
        text = (
            'tool_code\n'
            'print(excalidraw.write_file(file_path="/tmp/test.excalidraw", content="""not valid json {"""))\n'
        )
        tool_call, ok, error = try_salvage_textual_tool_call(text, TOOLS, WORKDIR)

        self.assertIsNone(tool_call)

    def test_rejects_foreign_write_file_without_excalidraw_envelope(self):
        text = (
            'tool_code\n'
            'print(excalidraw.write_file(file_path="/tmp/test.excalidraw", content="""{"hello": "world"}"""))\n'
        )
        tool_call, ok, error = try_salvage_textual_tool_call(text, TOOLS, WORKDIR)

        # Missing "type": "excalidraw" and "elements" — should not salvage
        self.assertIsNone(tool_call)

    def test_detects_foreign_write_file_as_textual_artifact(self):
        text = (
            'tool_code\n'
            'print(excalidraw.write_file(file_path="/tmp/test.excalidraw", content="""{}"""))\n'
        )
        self.assertTrue(contains_textual_tool_artifact(text, TOOLS))


if __name__ == "__main__":
    unittest.main()


class DeepSeekDsmlArtifactTest(unittest.TestCase):
    def test_real_dsml_response_is_not_accepted_as_completed_text(self):
        text = (FIXTURES / "deepseek_dsml_unexecuted.txt").read_text()
        self.assertTrue(contains_textual_tool_artifact(text, TOOLS))
        self.assertTrue(contains_textual_tool_artifact(text, []))

    def test_ordinary_mentions_of_dsml_remain_text(self):
        self.assertFalse(contains_textual_tool_artifact("DSML is a tool call protocol.", TOOLS))

    def test_quoted_dsml_report_is_not_a_tool_attempt(self):
        text = (FIXTURES / "deepseek_dsml_quoted_report.txt").read_text()
        self.assertFalse(contains_textual_tool_artifact(text, TOOLS))

    def test_standalone_and_mixed_dsml_remain_blocked(self):
        for text in (
            '`<｜｜DSML｜｜ invoke name="Bash">`',
            '```\n<｜｜DSML｜｜ invoke name="Bash">\n```',
            'Example: `<｜｜DSML｜｜ invoke ...>`\n<｜｜DSML｜｜ invoke name="Bash">',
        ):
            with self.subTest(text=text):
                self.assertTrue(contains_textual_tool_artifact(text, TOOLS))
