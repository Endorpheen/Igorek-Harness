"""Schedule confirmations distinguish created tasks from failed tool results."""

import json
import pytest
import unittest

from factory.core.cc_backend_executor import _schedule_created_message


class TestScheduleConfirmationFlint(unittest.TestCase):
    def test_damaged_json_returns_none(self):
        with self.subTest("invalid json"):
            self.assertIsNone(_schedule_created_message("{not valid json"))

    def test_valid_json_non_object_returns_none(self):
        with self.subTest("json array"):
            self.assertIsNone(_schedule_created_message("[1, 2, 3]"))
        with self.subTest("json string"):
            self.assertIsNone(_schedule_created_message('"hello"'))
        with self.subTest("json number"):
            self.assertIsNone(_schedule_created_message("42"))
        with self.subTest("json null"):
            self.assertIsNone(_schedule_created_message("null"))

    def test_status_not_created_returns_none(self):
        with self.subTest("failed status"):
            self.assertIsNone(
                _schedule_created_message(
                    json.dumps({"status": "failed", "message": "boom"})
                )
            )
        with self.subTest("pending status"):
            self.assertIsNone(
                _schedule_created_message(json.dumps({"status": "pending"}))
            )

    def test_created_with_message_returns_trimmed_message(self):
        with self.subTest("regular spaces trimmed"):
            self.assertEqual(
                _schedule_created_message(
                    json.dumps(
                        {"status": "created", "message": "  Задача выполнена ✅  "}
                    )
                ),
                "Задача выполнена ✅",
            )
        with self.subTest("unicode whitespace trimmed"):
            self.assertEqual(
                _schedule_created_message(
                    json.dumps({"status": "created", "message": " \u00a0Тест✅\u00a0 "})
                ),
                "Тест✅",
            )

    def test_created_without_message_returns_schedule_confirmation(self):
        with self.subTest("schedule given"):
            self.assertEqual(
                _schedule_created_message(
                    json.dumps({"status": "created", "schedule": "14:30"})
                ),
                "Ок, задача создана и выполнится в 14:30 (московское время).",
            )

    def test_created_minimal_returns_general_confirmation(self):
        with self.subTest("only status created"):
            self.assertEqual(
                _schedule_created_message(json.dumps({"status": "created"})),
                "Ок, отложенная задача создана.",
            )


if __name__ == "__main__":
    unittest.main()


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("  first second  ", "first second"),
        ("\u00a0first\u00a0second\u00a0", "first\u00a0second"),
        ("\tfirst\tsecond\t", "first\tsecond"),
        ("  first\nsecond  ", "first\nsecond"),
        ("  ✅ ready  ", "✅ ready"),
    ],
)
def test_confirmation_preserves_internal_message_whitespace(message, expected):
    assert (
        _schedule_created_message(json.dumps({"status": "created", "message": message}))
        == expected
    )


@pytest.mark.parametrize("message", [None, "", " \t ", "\u00a0"])
def test_empty_message_falls_back_to_reported_schedule(message):
    result = _schedule_created_message(
        json.dumps({"status": "created", "message": message, "schedule": "18:45"})
    )
    assert "18:45" in result
