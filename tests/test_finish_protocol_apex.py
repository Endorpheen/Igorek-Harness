"""Eight exact-scenario unittest tests for validate_finish."""
import unittest

from factory.core.completion_protocol import validate_finish
from factory.core.completion_review import CompletionRecovery


class TestFinishProtocolApex(unittest.TestCase):

    def make_state(self, record_succeeded=True):
        state = CompletionRecovery()
        state.record("read-1", "Read", record_succeeded)
        return state

    def test_01_correct_action_with_successful_read_evidence(self):
        value = {
            "status": "complete",
            "answer": "Проверенный результат",
            "requires_tools": True,
            "evidence_ids": ["read-1"],
            "remaining_steps": [],
        }
        state = self.make_state(record_succeeded=True)
        request = validate_finish(value, state)
        self.assertEqual(request.status, "complete")
        self.assertEqual(request.answer, "Проверенный результат")

    def test_02_plain_text_answer_without_tools(self):
        value = {
            "status": "complete",
            "answer": "Текстовый ответ готов",
            "requires_tools": False,
            "evidence_ids": [],
            "remaining_steps": [],
        }
        state = CompletionRecovery()
        request = validate_finish(value, state)
        self.assertFalse(request.requires_tools)
        self.assertEqual(request.evidence_ids, ())

    def test_03_action_without_evidence_and_empty_state(self):
        value = {
            "status": "complete",
            "answer": "Результат",
            "requires_tools": True,
            "evidence_ids": [],
            "remaining_steps": [],
        }
        state = CompletionRecovery()
        with self.assertRaises(ValueError):
            validate_finish(value, state)

    def test_04_failed_read_evidence(self):
        value = {
            "status": "complete",
            "answer": "Результат",
            "requires_tools": True,
            "evidence_ids": ["read-1"],
            "remaining_steps": [],
        }
        state = self.make_state(record_succeeded=False)
        with self.assertRaises(ValueError):
            validate_finish(value, state)

    def test_05_unknown_evidence_id(self):
        value = {
            "status": "complete",
            "answer": "Результат",
            "requires_tools": True,
            "evidence_ids": ["read-1", "old-id"],
            "remaining_steps": [],
        }
        state = self.make_state(record_succeeded=True)
        with self.assertRaises(ValueError):
            validate_finish(value, state)

    def test_06_remaining_steps_with_complete_status(self):
        value = {
            "status": "complete",
            "answer": "Результат",
            "requires_tools": True,
            "evidence_ids": ["read-1"],
            "remaining_steps": ["Ещё проверить результат"],
        }
        state = self.make_state(record_succeeded=True)
        with self.assertRaises(ValueError):
            validate_finish(value, state)

    def test_07_plan_pending_with_complete_status(self):
        value = {
            "status": "complete",
            "answer": "Результат",
            "requires_tools": True,
            "evidence_ids": ["read-1"],
            "remaining_steps": [],
        }
        state = self.make_state(record_succeeded=True)
        state.plan_pending = True
        with self.assertRaises(ValueError):
            validate_finish(value, state)

    def test_08_blocker_with_plan_pending_is_allowed(self):
        value = {
            "status": "blocked",
            "answer": "Нужен вход владельца",
            "requires_tools": True,
            "evidence_ids": [],
            "remaining_steps": ["Войти в аккаунт"],
        }
        state = CompletionRecovery()
        state.plan_pending = True
        request = validate_finish(value, state)
        self.assertEqual(request.status, "blocked")
        self.assertEqual(request.answer, "Нужен вход владельца")


if __name__ == "__main__":
    unittest.main()
