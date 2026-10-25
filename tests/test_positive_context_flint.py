import unittest

from factory.core.cc_backend_executor import _positive_int


class TestPositiveContextFlint(unittest.TestCase):
    """Context-window metadata must produce positive integers or unknown values."""

    def test_positive_int_and_digit_strings(self):
        """Preserve positive integers and trimmed digit strings."""
        with self.subTest("positive int"):
            self.assertEqual(_positive_int(1), 1)
            self.assertEqual(_positive_int(99), 99)
        with self.subTest("digit string"):
            self.assertEqual(_positive_int("1"), 1)
            self.assertEqual(_positive_int("42"), 42)
            self.assertEqual(_positive_int("  7  "), 7)
            self.assertEqual(_positive_int("1000"), 1000)
        with self.subTest("zero and negative are None"):
            self.assertIsNone(_positive_int(0))
            self.assertIsNone(_positive_int(-3))
            self.assertIsNone(_positive_int("-5"))

    def test_non_numeric_zero_negative_and_superscript(self):
        """Reject non-numeric, zero, negative and non-decimal digit values."""
        with self.subTest("non-numeric string"):
            self.assertIsNone(_positive_int("abc"))
            self.assertIsNone(_positive_int("12a"))
        with self.subTest("superscript two"):
            self.assertIsNone(_positive_int("²"))
        with self.subTest("negative int"):
            self.assertIsNone(_positive_int(-1))

    def test_bool_is_not_context_number(self):
        """Booleans are not context sizes."""
        with self.subTest("True"):
            self.assertIsNone(_positive_int(True))
        with self.subTest("False"):
            self.assertIsNone(_positive_int(False))

    def test_fractional_floats(self):
        """Truncate positive floats while rejecting a zero result."""
        with self.subTest("fraction truncates"):
            self.assertEqual(_positive_int(1.9), 1)
        with self.subTest("fraction below one is None"):
            self.assertIsNone(_positive_int(0.5))

    def test_non_finite_floats(self):
        """Reject non-finite floats without raising."""
        with self.subTest("positive infinity"):
            self.assertIsNone(_positive_int(float("inf")))
        with self.subTest("negative infinity"):
            self.assertIsNone(_positive_int(float("-inf")))
        with self.subTest("nan"):
            self.assertIsNone(_positive_int(float("nan")))


if __name__ == "__main__":
    unittest.main()
