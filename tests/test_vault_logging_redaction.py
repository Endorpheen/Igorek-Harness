import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from factory.core.cc_backend_executor import (
    _approval_signature,
    _approved_tool_call,
    _matches_approval_session_rule,
    resync_approval_signatures,
)
from factory.core.schema import initialize_database, open_db
from factory.core.vault.logging_redaction import redact_mapping, redact_text
from factory.core.worker import Worker


class VaultLoggingRedactionTest(unittest.TestCase):
    def test_redact_text_does_not_corrupt_python_secret_identifiers(self):
        source = (
            "is_secret: bool = False\n"
            "if self.is_secret:\n"
            "    return text\n"
        )

        self.assertEqual(redact_text(source), source)

    def test_redact_text_still_redacts_bare_and_env_secret_assignments(self):
        text = "token=plain-secret-value OPENAI_API_KEY=sk-secretvalue1234567890"

        redacted = redact_text(text)

        self.assertNotIn("plain-secret-value", redacted)
        self.assertNotIn("sk-secretvalue1234567890", redacted)
        self.assertEqual(redacted.count("[REDACTED]"), 2)

    def test_redacted_write_approval_signature_remains_resumable(self):
        tool_input = {
            "file_path": "/tmp/request_user_input_form.py",
            "content": (
                "@dataclass\n"
                "class Question:\n"
                "    is_secret: bool = False\n\n"
                "def answer(self):\n"
                "    if self.is_secret:\n"
                "        return text\n"
            ),
        }
        signature = _approval_signature("Write", tool_input)

        persisted = redact_mapping({
            "metadata": {"approved_tool_calls": [signature]},
            "approval_blocked_tools": [{
                "tool": "Write",
                "input": tool_input,
                "signature": signature,
            }],
        })

        [stored_signature] = persisted["metadata"]["approved_tool_calls"]
        [blocked] = persisted["approval_blocked_tools"]
        self.assertEqual(blocked["input"], tool_input)
        self.assertEqual(stored_signature, signature)
        self.assertEqual(
            stored_signature,
            _approval_signature(blocked["tool"], blocked["input"]),
        )


    def test_redacted_signature_stays_consistent_for_secret_shaped_content(self):
        tool_input = {
            "file_path": "/tmp/test_mobile_bridge_server.py",
            "new_string": 'token=[REDACTED]"MyPhone")',
        }
        signature = _approval_signature("Edit", tool_input)

        persisted = redact_mapping({
            "approval_blocked_tools": [{
                "tool": "Edit",
                "input": tool_input,
                "signature": signature,
            }],
        })
        [blocked] = resync_approval_signatures(persisted["approval_blocked_tools"])

        self.assertEqual(blocked["input"], tool_input)
        self.assertEqual(blocked["signature"], _approval_signature("Edit", blocked["input"]))
        self.assertTrue(
            _approved_tool_call(
                metadata={"approved_tool_calls": [blocked["signature"]]},
                tool_name="Edit",
                tool_input=blocked["input"],
            )
        )

    def test_worker_persists_resynced_approval_signature(self):
        tool_input = {
            "file_path": "/tmp/test_mobile_bridge_server.py",
            "new_string": 'token=[REDACTED]"MyPhone")',
        }
        result = {
            "status": "fail",
            "output_text": "",
            "approval_blocked_tools": [{
                "tool": "Edit",
                "input": tool_input,
                "signature": _approval_signature("Edit", tool_input),
            }],
        }

        with tempfile.TemporaryDirectory() as tmp:
            db_path = str(Path(tmp) / "tasks.db")
            initialize_database(db_path)
            worker_stub = SimpleNamespace(
                profile="test-profile",
                pid=0,
                backend="",
                model="",
                config={"env": {}},
            )
            with open_db(db_path) as conn:
                conn.execute("INSERT INTO tasks (task_id, status) VALUES ('task-1', 'processing')")
                Worker.update_task(worker_stub, conn, "task-1", result)
                stored = conn.execute(
                    "SELECT approval_blocked_tools FROM tasks WHERE task_id = 'task-1'"
                ).fetchone()[0]

        [blocked] = json.loads(stored)
        self.assertEqual(blocked["input"], tool_input)
        self.assertEqual(blocked["signature"], _approval_signature("Edit", blocked["input"]))
        self.assertTrue(
            _approved_tool_call(
                metadata={"approved_tool_calls": [blocked["signature"]]},
                tool_name="Edit",
                tool_input=blocked["input"],
            )
        )

    def test_resynced_session_rule_matches_redacted_bash_call(self):
        tool_input = {"command": 'curl -H "token=[REDACTED]" https://example.com'}
        signature = _approval_signature("Bash", tool_input)
        persisted = redact_mapping({
            "approval_blocked_tools": [{
                "tool": "Bash",
                "input": tool_input,
                "signature": signature,
                "session_rule": {
                    "rule_type": "exact_tool_call",
                    "tool": "Bash",
                    "signature": signature,
                },
            }],
        })

        [blocked] = resync_approval_signatures(persisted["approval_blocked_tools"])

        self.assertTrue(
            _matches_approval_session_rule(
                blocked["session_rule"],
                tool_name="Bash",
                tool_input=blocked["input"],
            )
        )

    def test_benign_blocked_tool_payload_is_left_unchanged(self):
        tool_input = {"file_path": "/tmp/report.md", "content": "hello\n"}
        signature = _approval_signature("Write", tool_input)

        persisted = redact_mapping({
            "approval_blocked_tools": [{
                "tool": "Write",
                "input": tool_input,
                "signature": signature,
            }],
        })
        [blocked] = resync_approval_signatures(persisted["approval_blocked_tools"])

        self.assertEqual(blocked["input"], tool_input)
        self.assertEqual(blocked["signature"], signature)


if __name__ == "__main__":
    unittest.main()
