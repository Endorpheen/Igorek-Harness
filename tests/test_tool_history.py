import unittest
from types import SimpleNamespace

from factory.api_server_core.tool_history import extract_last_tool_file


def flatten_block_content(blocks, **_):
    parts = []
    for block in blocks:
        if isinstance(block, dict):
            parts.append(str(block.get("content") or block.get("text") or ""))
    return "\n".join(parts)


class ToolHistoryTest(unittest.TestCase):
    def test_persisted_tool_result_path_becomes_last_tool_file_before_bash_target(self):
        persisted_path = (
            "/home/example/.igorek-agent-factory/workspace/"
            "temp_execution_e1475e90/.tool_results/toolu_abc.txt"
        )
        req = SimpleNamespace(
            messages=[
                SimpleNamespace(
                    role="assistant",
                    content=[
                        {
                            "type": "tool_use",
                            "id": "toolu_abc",
                            "name": "Bash",
                            "input": {
                                "command": (
                                    "git -C /home/example/AI/paroquant-test show 261a702 "
                                    "-- factory/core/tool_call_salvage.py"
                                )
                            },
                        }
                    ],
                ),
                SimpleNamespace(
                    role="user",
                    content=[
                        {
                            "type": "tool_result",
                            "tool_use_id": "toolu_abc",
                            "content": (
                                "<persisted-output>\n"
                                "Tool `Bash` returned a large result and it was saved to disk.\n"
                                f"Full output saved to: {persisted_path}\n"
                                "If you need more detail, use the Read tool on this file.\n"
                            ),
                        }
                    ],
                ),
            ]
        )

        result = extract_last_tool_file(
            req,
            flatten_block_content=flatten_block_content,
            looks_like_tool_error_payload=lambda _text: False,
            extract_bash_file_target=lambda _command: "factory/core/tool_call_salvage.py",
        )

        self.assertEqual(result, persisted_path)


if __name__ == "__main__":
    unittest.main()
