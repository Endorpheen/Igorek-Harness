import unittest

from factory.core.external_sessions import _epoch_seconds


class TestExternalTimestampContracts(unittest.TestCase):
    def test_valid_epoch_seconds_and_iso_string(self):
        with self.subTest("seconds"):
            self.assertEqual(_epoch_seconds(1700000000), 1700000000)

        with self.subTest("milliseconds"):
            self.assertEqual(_epoch_seconds(1700000000000), 1700000000)

        with self.subTest("iso_string"):
            self.assertEqual(_epoch_seconds("2023-11-14T22:13:20Z"), 1700000000)

    def test_non_finite_floats_return_zero(self):
        with self.subTest("nan"):
            self.assertEqual(_epoch_seconds(float("nan")), 0)

        with self.subTest("inf"):
            self.assertEqual(_epoch_seconds(float("inf")), 0)

        with self.subTest("-inf"):
            self.assertEqual(_epoch_seconds(float("-inf")), 0)


if __name__ == "__main__":
    unittest.main()
