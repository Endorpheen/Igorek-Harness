import unittest

from factory.core import security_levels as sl


class SecurityLevelsTest(unittest.TestCase):
    def test_levels_and_default(self):
        self.assertEqual(sl.LEVELS, ("strict", "standard", "trusted", "full"))
        self.assertEqual(sl.DEFAULT_LEVEL, "standard")

    def test_normalize_level_is_case_and_space_insensitive(self):
        self.assertEqual(sl.normalize_level("FULL"), "full")
        self.assertEqual(sl.normalize_level("  Trusted "), "trusted")
        self.assertEqual(sl.normalize_level("standard"), "standard")

    def test_normalize_level_rejects_unknown_values(self):
        for bad in ("nope", "", None, "user-read", "root", "full-dangerous"):
            with self.subTest(bad=bad):
                with self.assertRaises(ValueError):
                    sl.normalize_level(bad)

    def test_level_to_runtime_full_mapping(self):
        self.assertEqual(
            sl.level_to_runtime("strict"),
            {
                "security_mode": "enforce",
                "guard_mode": "on",
                "filesystem_mode": "workspace",
            },
        )
        self.assertEqual(
            sl.level_to_runtime("standard"),
            {
                "security_mode": "observe",
                "guard_mode": "on",
                "filesystem_mode": "workspace",
            },
        )
        self.assertEqual(
            sl.level_to_runtime("trusted"),
            {
                "security_mode": "observe",
                "guard_mode": "warn",
                "filesystem_mode": "workspace",
            },
        )
        self.assertEqual(
            sl.level_to_runtime("full"),
            {
                "security_mode": "disabled",
                "guard_mode": "off",
                "filesystem_mode": "full-dangerous",
            },
        )

    def test_level_to_runtime_accepts_only_the_four_levels(self):
        with self.assertRaises(ValueError):
            sl.level_to_runtime("user-read")
        with self.assertRaises(ValueError):
            sl.level_to_runtime("root")
        with self.assertRaises(ValueError):
            sl.level_to_runtime("full-dangerous")

    def test_level_to_runtime_returns_fresh_dict(self):
        mutated = sl.level_to_runtime("full")
        mutated["guard_mode"] = "on"
        self.assertEqual(sl.level_to_runtime("full")["guard_mode"], "off")

    def test_level_rank_ordering(self):
        ranks = [sl.level_rank(level) for level in sl.LEVELS]
        self.assertEqual(ranks, sorted(ranks))
        self.assertEqual(len(set(ranks)), len(sl.LEVELS))

    def test_level_rank_rejects_non_level(self):
        with self.assertRaises(ValueError):
            sl.level_rank("user-read")

    def test_is_weakened_detects_more_permissive_level(self):
        self.assertTrue(sl.is_weakened("full", "standard"))
        self.assertTrue(sl.is_weakened("trusted", "strict"))
        self.assertTrue(sl.is_weakened("standard", "strict"))

    def test_is_weakened_false_for_equal_or_stricter(self):
        self.assertFalse(sl.is_weakened("standard", "standard"))
        self.assertFalse(sl.is_weakened("strict", "standard"))
        self.assertFalse(sl.is_weakened("trusted", "full"))

    def test_is_weakened_rejects_non_level(self):
        with self.assertRaises(ValueError):
            sl.is_weakened("user-read", "standard")


if __name__ == "__main__":
    unittest.main()
