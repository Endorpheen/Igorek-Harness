"""Two tests for parse_completion_review control-flow on completion answers."""
import json
import unittest

from factory.core.completion_review import parse_completion_review


class CompletionReviewParseTests(unittest.TestCase):
    """Strictly two scenarios: unknown evidence ID, and failed tool evidence."""

    def test_complete_with_unknown_evidence_id_raises_valueerror(self):
        receipts = [
            {"id": "known-tool-1", "name": "read_log", "succeeded": True}
        ]
        review = {
            "status": "complete",
            "requires_tools": True,
            "evidence_ids": ["known-tool-1", "unknown-tool-99"],
        }
        with self.assertRaises(ValueError):
            parse_completion_review(json.dumps(review), receipts)

    def test_complete_with_failed_tool_returns_continue(self):
        receipts = [
            {"id": "known-tool-1", "name": "read_log", "succeeded": False}
        ]
        review = {
            "status": "complete",
            "requires_tools": True,
            "evidence_ids": ["known-tool-1"],
        }
        result = parse_completion_review(json.dumps(review), receipts)
        self.assertEqual(result.status, "continue")
        self.assertIs(result.requires_tools, True)


if __name__ == "__main__":
    unittest.main()
