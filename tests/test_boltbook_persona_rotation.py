import unittest

from factory.core.boltbook_client import _resolve_active_persona


class TestBoltbookPersonaRotation(unittest.TestCase):
    """Tests for the real Boltbook persona rotation resolution logic."""

    def test_fixed_mode_selects_witty_persona(self):
        state = {
            "mode": "fixed",
            "fixed": "witty",
            "rotation": ["balanced", "witty"],
        }
        for now in (0, 86400):
            with self.subTest(now=now):
                result = _resolve_active_persona(state, now=now)
                self.assertEqual(result["key"], "witty")
                self.assertIsInstance(result["title"], str)
                self.assertTrue(result["title"])
                self.assertIsInstance(result["instructions"], str)
                self.assertTrue(result["instructions"])

    def test_round_robin_daily_rotation(self):
        state = {
            "mode": "round_robin_daily",
            "rotation": ["balanced", "witty"],
        }
        self.assertEqual(_resolve_active_persona(state, now=0)["key"], "balanced")
        self.assertEqual(_resolve_active_persona(state, now=86400)["key"], "witty")
        self.assertEqual(_resolve_active_persona(state, now=172800)["key"], "balanced")

    def test_round_robin_hourly_rotation(self):
        state = {
            "mode": "round_robin_hourly",
            "rotation": ["balanced", "witty"],
        }
        self.assertEqual(_resolve_active_persona(state, now=0)["key"], "balanced")
        self.assertEqual(_resolve_active_persona(state, now=3600)["key"], "witty")
        self.assertEqual(_resolve_active_persona(state, now=7200)["key"], "balanced")

    def test_fixed_unknown_persona_falls_back_to_balanced(self):
        state = {
            "mode": "fixed",
            "fixed": "unknown",
            "rotation": ["unknown"],
        }
        result = _resolve_active_persona(state, now=0)
        self.assertEqual(result["key"], "balanced")
        self.assertIsInstance(result["title"], str)
        self.assertTrue(result["title"])
        self.assertIsInstance(result["instructions"], str)
        self.assertTrue(result["instructions"])


if __name__ == "__main__":
    unittest.main()
