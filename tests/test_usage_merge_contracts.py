import unittest

from factory.core.worker import _combine_usage


class TestCombineUsageContracts(unittest.TestCase):
    def test_combine_usage_sums_all_seven_keys(self):
        first = {
            "prompt": 10,
            "completion": 10,
            "input_tokens": 10,
            "cached_input_tokens": 10,
            "output_tokens": 10,
            "reasoning_output_tokens": 10,
            "total_tokens": 10,
        }
        second = {key: 3 for key in first}
        result = _combine_usage(first, second)
        for key in first:
            self.assertEqual(result[key], 13)

    def test_combine_usage_returns_separate_dict_without_mutating_inputs(self):
        first = {"prompt": 1, "total_tokens": 2}
        second = {"prompt": 3, "total_tokens": 4}
        result = _combine_usage(first, second)
        self.assertEqual(first, {"prompt": 1, "total_tokens": 2})
        self.assertEqual(second, {"prompt": 3, "total_tokens": 4})
        self.assertIsNot(result, first)
        self.assertIsNot(result, second)

    def test_combine_usage_empty_first_returns_independent_copy_of_second(self):
        second = {"prompt": 5, "total_tokens": 7}
        result = _combine_usage({}, second)
        self.assertEqual(result, second)
        self.assertIsNot(result, second)

    def test_combine_usage_empty_second_returns_independent_copy_of_first(self):
        first = {"prompt": 5, "total_tokens": 7}
        result = _combine_usage(first, {})
        self.assertEqual(result, first)
        self.assertIsNot(result, first)

    def test_combine_usage_model_context_window_priority(self):
        with self.subTest(old_window=128000, new_window=256000):
            result = _combine_usage(
                {"model_context_window": 128000},
                {"model_context_window": 256000},
            )
            self.assertEqual(result["model_context_window"], 256000)

        with self.subTest(old_window=128000, new_window=0):
            result = _combine_usage(
                {"model_context_window": 128000},
                {"model_context_window": 0},
            )
            self.assertEqual(result["model_context_window"], 128000)

        with self.subTest(old_window=0, new_window=256000):
            result = _combine_usage(
                {"model_context_window": 0},
                {"model_context_window": 256000},
            )
            self.assertEqual(result["model_context_window"], 256000)

    def test_combine_usage_handles_missing_none_and_string_counters(self):
        first = {"input_tokens": None, "output_tokens": "3"}
        second = {"input_tokens": 4, "output_tokens": "2"}
        result = _combine_usage(first, second)
        self.assertEqual(result["input_tokens"], 4)
        self.assertEqual(result["output_tokens"], 5)


if __name__ == "__main__":
    unittest.main()
