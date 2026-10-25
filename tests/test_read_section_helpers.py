import unittest

from factory.api_server_core.text_utils import extract_read_sections


ARROW = "\u2192"


class TestExtractReadSections(unittest.TestCase):
    def test_numbered_read_sections_order(self):
        # Numbered Read output with class, method and async method.
        result_text = (
            "1\u2192class Worker:\n"
            "2\u2192    def run(self):\n"
            "3\u2192        pass\n"
            "4\u2192async def fetch():\n"
            "5\u2192    pass"
        )
        self.assertEqual(extract_read_sections(result_text), ["Worker", "run", "fetch"])

    def test_repeated_definitions_are_deduplicated(self):
        # Same names repeated must appear only once, first occurrence order kept.
        result_text = (
            "1\u2192class Worker:\n"
            "2\u2192    def run(self):\n"
            "3\u2192        pass\n"
            "4\u2192async def fetch():\n"
            "5\u2192    pass\n"
            "6\u2192class Worker:\n"
            "7\u2192    def run(self):\n"
            "8\u2192        pass"
        )
        self.assertEqual(extract_read_sections(result_text), ["Worker", "run", "fetch"])

    def test_empty_text_and_prose_return_empty_list(self):
        self.assertEqual(extract_read_sections(""), [])
        self.assertEqual(extract_read_sections("Just some prose without definitions."), [])

    def test_only_first_twelve_sections_are_returned(self):
        lines = []
        number = 1
        for i in range(14):
            lines.append(f"{number}{ARROW}def func_{i}():")
            number += 1
            lines.append(f"{number}{ARROW}    pass")
            number += 1
        result_text = "\n".join(lines)
        expected = ["func_" + str(i) for i in range(12)]
        self.assertEqual(extract_read_sections(result_text), expected)


if __name__ == "__main__":
    unittest.main()
