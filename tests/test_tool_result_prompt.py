import unittest
from types import SimpleNamespace

from factory.api_server_core.anthropic_messages import extract_anthropic_messages
from factory.api_server_core.schemas import PromptMode


class ToolResultPromptTest(unittest.TestCase):
    def test_tool_result_prompt_requires_exact_bash_stdout_evidence(self):
        req = SimpleNamespace(
            messages=[
                SimpleNamespace(
                    role="user",
                    content="Покажи текущую ветку git.",
                ),
                SimpleNamespace(
                    role="assistant",
                    content=[
                        {
                            "type": "tool_use",
                            "id": "toolu_1",
                            "name": "Bash",
                            "input": {"command": "git branch --show-current"},
                        }
                    ],
                ),
                SimpleNamespace(
                    role="user",
                    content=[
                        {
                            "type": "tool_result",
                            "tool_use_id": "toolu_1",
                            "content": "hermes-implementation\n",
                        }
                    ],
                ),
            ]
        )

        messages = extract_anthropic_messages(
            req,
            PromptMode.TOOL_RESULT,
            prompt_mode=PromptMode,
            tools_prompt="",
        )

        system_text = messages[0]["content"]
        user_text = messages[-1]["content"]

        self.assertIn("Tool-result evidence rules", system_text)
        self.assertIn("preserve exact factual tokens", system_text)
        self.assertIn("branch names", system_text)
        self.assertIn("do not fill it from memory or common defaults", system_text)
        self.assertIn("Tool result:", user_text)
        self.assertIn("hermes-implementation", user_text)


if __name__ == "__main__":
    unittest.main()
