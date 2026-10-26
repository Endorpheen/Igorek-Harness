import unittest

from factory.core.tui.render.renderable import Insets, Rect


class TestTerminalRectContracts(unittest.TestCase):
    def test_intersection_contract(self):
        rect = Rect(2, 3, 10, 8)
        other = Rect(7, 1, 10, 5)

        self.assertEqual(rect.intersection(other), Rect(7, 3, 5, 3))
        self.assertEqual(rect.intersection(Rect(40, 40, 1, 1)), Rect(0, 0, 0, 0))
        self.assertTrue(rect.intersection(Rect(40, 40, 1, 1)).is_empty())

        self.assertEqual(rect, Rect(2, 3, 10, 8))
        self.assertEqual(other, Rect(7, 1, 10, 5))

    def test_inset_contract(self):
        rect = Rect(10, 20, 8, 6)

        self.assertEqual(rect.inset(Insets.vh(1, 2)), Rect(12, 21, 4, 4))
        self.assertEqual(rect.inset(Insets.tlbr(9, 9, 9, 9)), Rect(19, 29, 0, 0))

        self.assertEqual(rect, Rect(10, 20, 8, 6))


if __name__ == "__main__":
    unittest.main()
