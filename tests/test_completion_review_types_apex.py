"""Four type-validation tests for parse_completion_review (Apex control data)."""
import json
import unittest

from factory.core.completion_review import parse_completion_review


BASE = {"status": "complete", "requires_tools": True, "evidence_ids": ["known"]}
RECEIPTS = [{"id": "known", "name": "Read", "succeeded": True}]


def broken(**mutated):
    """Return a base object with selected fields overridden."""
    obj = dict(BASE)
    obj.update(mutated)
    return obj


class TestCompletionReviewTypes(unittest.TestCase):

    def test_requires_tools_string_true(self):
        data = broken(requires_tools="true")
        with self.assertRaises(ValueError):
            parse_completion_review(json.dumps(data), RECEIPTS)

    def test_evidence_ids_is_string(self):
        data = broken(evidence_ids="known")
        with self.assertRaises(ValueError):
            parse_completion_review(json.dumps(data), RECEIPTS)

    def test_evidence_ids_list_of_ints(self):
        data = broken(evidence_ids=[123])
        with self.assertRaises(ValueError):
            parse_completion_review(json.dumps(data), RECEIPTS)

    def test_evidence_ids_duplicate_ids(self):
        data = broken(evidence_ids=["known", "known"])
        with self.assertRaises(ValueError):
            parse_completion_review(json.dumps(data), RECEIPTS)


if __name__ == "__main__":
    unittest.main()
