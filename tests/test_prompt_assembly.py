import unittest

from factory.api_server_core import prompts as api_prompts
from factory.core.cc_backend_tools import execute_tool, get_tool_schemas
from factory.core.prompt_assembly import (
    build_cc_backend_system_prompt,
    build_codex_backend_input,
)


class PromptAssemblyTest(unittest.TestCase):
    def test_codex_backend_input_contains_post_tool_completion_contract(self):
        prompt = build_codex_backend_input(
            workdir="/tmp/work",
            profile="codex-openrouter",
            stage="main",
            task_text="Привет",
        )

        self.assertIn("Completion Contract", prompt)
        self.assertIn("После каждого результата инструмента", prompt)

    def test_cc_backend_prompt_forbids_textual_write_approval(self):
        prompt = build_cc_backend_system_prompt(
            workdir="/tmp/work",
            filesystem_mode="workspace",
            model_name="deepseek/deepseek-v4-pro",
        )

        self.assertIn("НЕ спрашивай разрешение текстом", prompt)
        self.assertIn("сразу вызывай настоящий tool_use", prompt)
        self.assertIn("Backend сам остановит опасный tool_use", prompt)
        self.assertIn("reason/justification", prompt)
        self.assertIn("видимую причину", prompt)
        self.assertNotIn("Перед вызовом Write или Edit СПРОСИ разрешение", prompt)

    def test_cc_backend_prompt_routes_external_readonly_files_to_bash(self):
        prompt = build_cc_backend_system_prompt(
            workdir="/tmp/work",
            filesystem_mode="workspace",
            model_name="deepseek/deepseek-v4-pro",
        )

        self.assertIn("Read/Grep/Glob предназначены для файлов внутри workdir", prompt)
        self.assertIn("пользовательского файла вне workdir", prompt)
        self.assertIn("outside working directory", prompt)
        self.assertIn("Bash с безопасной read-only командой", prompt)

    def test_cc_backend_compact_prompt_keeps_core_contracts(self):
        full = build_cc_backend_system_prompt(
            workdir="/tmp/work",
            filesystem_mode="workspace",
            model_name="tencent/hy3-preview:free",
            prompt_style="full",
        )
        compact = build_cc_backend_system_prompt(
            workdir="/tmp/work",
            filesystem_mode="workspace",
            model_name="tencent/hy3-preview:free",
            prompt_style="compact",
        )

        self.assertLess(len(compact), len(full) * 0.55)
        self.assertIn("Всегда отвечай по-русски", compact)
        self.assertIn("Tool result", compact)
        self.assertIn("только через Write", compact)
        self.assertIn("backend сам покажет approval UI", compact)
        self.assertIn("reason/justification", compact)
        self.assertIn("пользовательские файлы вне workdir", compact)

    def test_full_and_compact_prompts_include_readonly_opencode_observation(self):
        for style in ("full", "compact"):
            prompt = build_cc_backend_system_prompt(workdir="/tmp/work", filesystem_mode="workspace",
                                                   model_name="fixture", prompt_style=style)
            for term in ("opencode_task status", "freshness", "completed", "владелец"):
                self.assertIn(term, prompt, style)

    def test_cc_backend_prompt_contains_codex_plan_contract(self):
        prompt = build_cc_backend_system_prompt(
            workdir="/tmp/work",
            filesystem_mode="workspace",
            model_name="deepseek/deepseek-v4-pro",
        )

        self.assertIn("Планирование через update_plan", prompt)
        self.assertIn("Одновременно может быть только один in_progress", prompt)
        self.assertIn("не повторяй весь план текстом", prompt)

    def test_cc_backend_prompt_prefers_markdown_pipe_tables(self):
        prompt = build_cc_backend_system_prompt(
            workdir="/tmp/work",
            filesystem_mode="workspace",
            model_name="minimax-m3",
        )

        self.assertIn("markdown pipe table", prompt)
        self.assertIn("|---|---|", prompt)
        self.assertIn("Не рисуй таблицы box-символами", prompt)

    def test_cc_backend_prompt_contains_repo_safety_contracts(self):
        prompt = build_cc_backend_system_prompt(
            workdir="/tmp/work",
            filesystem_mode="workspace",
            model_name="minimax-m3",
        )

        self.assertIn("git status", prompt)
        self.assertIn("dirty git worktree", prompt)
        self.assertIn("не используй `git add .`", prompt)
        self.assertIn("git reset --hard", prompt)
        self.assertIn("git checkout --", prompt)
        self.assertIn("git clean", prompt)
        self.assertIn("rg --files", prompt)
        self.assertIn("Bash heredoc", prompt)
        self.assertIn("формате code review", prompt)
        self.assertIn("что нужно перезапустить", prompt)

    def test_cc_backend_compact_prompt_keeps_repo_safety_contracts(self):
        prompt = build_cc_backend_system_prompt(
            workdir="/tmp/work",
            filesystem_mode="workspace",
            model_name="minimax-m3",
            prompt_style="compact",
        )

        self.assertIn("git status", prompt)
        self.assertIn("dirty worktree", prompt)
        self.assertIn("git add .", prompt)
        self.assertIn("git reset --hard", prompt)
        self.assertIn("rg --files", prompt)
        self.assertIn("review/ревью", prompt)
        self.assertIn("нужный перезапуск", prompt)

    def test_api_server_prompt_keeps_repo_safety_contracts(self):
        prompt = api_prompts.system_prompt()

        self.assertIn("git status", prompt)
        self.assertIn("git worktree dirty", prompt)
        self.assertIn("git add .", prompt)
        self.assertIn("git reset --hard", prompt)
        self.assertIn("git checkout --", prompt)
        self.assertIn("rg --files", prompt)
        self.assertIn("Bash heredoc", prompt)
        self.assertIn("review или ревью", prompt)

    def test_cc_backend_prompt_only_advertises_interactive_input_when_enabled(self):
        disabled = build_cc_backend_system_prompt(
            workdir="/tmp/work",
            filesystem_mode="workspace",
        )
        enabled = build_cc_backend_system_prompt(
            workdir="/tmp/work",
            filesystem_mode="workspace",
            interactive_input_enabled=True,
        )

        self.assertNotIn("request_user_input", disabled)
        self.assertIn("request_user_input", enabled)

    def test_android_prompt_explains_automatic_file_delivery(self):
        prompt = build_cc_backend_system_prompt(
            workdir="/tmp/work",
            filesystem_mode="workspace",
            source_platform="android",
        )

        self.assertIn("Android-приложение", prompt)
        self.assertIn("автоматически", prompt)
        self.assertIn("точный абсолютный путь", prompt)
        self.assertIn("не ищи отдельный skill", prompt.lower())
        self.assertIn("не поднимай HTTP-сервер", prompt)

    def test_non_android_prompt_does_not_include_mobile_delivery_contract(self):
        prompt = build_cc_backend_system_prompt(
            workdir="/tmp/work",
            filesystem_mode="workspace",
            source_platform="telegram",
        )

        self.assertNotIn("Android-приложение", prompt)
        self.assertNotIn("не ищи отдельный skill", prompt)

    def test_cc_backend_plan_mode_block_appears_only_for_plan_tasks(self):
        execute = build_cc_backend_system_prompt(
            workdir="/tmp/work",
            filesystem_mode="workspace",
            model_name="minimax-m3",
        )
        execute_explicit = build_cc_backend_system_prompt(
            workdir="/tmp/work",
            filesystem_mode="workspace",
            model_name="minimax-m3",
            plan_mode=False,
        )
        plan = build_cc_backend_system_prompt(
            workdir="/tmp/work",
            filesystem_mode="workspace",
            model_name="minimax-m3",
            plan_mode=True,
        )

        self.assertEqual(execute, execute_explicit)
        self.assertNotIn("Plan Mode", execute)
        self.assertIn("Plan Mode", plan)
        self.assertIn("РЕЖИМ ПЛАНИРОВКИ", plan)
        self.assertIn("не вызывай изменяющие инструменты", plan)
        self.assertIn("пошаговый план", plan)

    def test_cc_backend_compact_plan_mode_block_appears_only_for_plan_tasks(self):
        execute = build_cc_backend_system_prompt(
            workdir="/tmp/work",
            filesystem_mode="workspace",
            prompt_style="compact",
        )
        plan = build_cc_backend_system_prompt(
            workdir="/tmp/work",
            filesystem_mode="workspace",
            prompt_style="compact",
            plan_mode=True,
        )

        self.assertNotIn("PLAN MODE", execute)
        self.assertIn("PLAN MODE", plan)
        self.assertIn("только план", plan)

    def test_update_plan_schema_and_handler_follow_structural_contract(self):
        schemas = {tool["name"]: tool["input_schema"] for tool in get_tool_schemas()}
        schema = schemas["update_plan"]
        item_schema = schema["properties"]["plan"]["items"]
        self.assertEqual(item_schema["required"], ["step", "status"])
        self.assertEqual(
            item_schema["properties"]["status"]["enum"],
            ["pending", "in_progress", "completed"],
        )
        result = execute_tool(
            "update_plan",
            {"plan": [{"step": "Inspect", "status": "in_progress"}]},
            "/tmp/work",
        )
        self.assertEqual(result, "Plan updated")

    def test_update_plan_rejects_multiple_in_progress_steps(self):
        result = execute_tool(
            "update_plan",
            {
                "plan": [
                    {"step": "Inspect", "status": "in_progress"},
                    {"step": "Implement", "status": "in_progress"},
                ]
            },
            "/tmp/work",
        )
        self.assertIn("At most one step can be in_progress", result)

    def test_approval_risk_tools_accept_optional_reason_fields(self):
        schemas = {tool["name"]: tool["input_schema"] for tool in get_tool_schemas()}

        for name in ("Write", "Edit", "Bash"):
            with self.subTest(tool=name):
                schema = schemas[name]
                properties = schema["properties"]
                self.assertIn("reason", properties)
                self.assertIn("justification", properties)
                self.assertEqual(properties["reason"]["type"], "string")
                self.assertEqual(properties["justification"]["type"], "string")
                self.assertNotIn("reason", schema["required"])
                self.assertNotIn("justification", schema["required"])


if __name__ == "__main__":
    unittest.main()
