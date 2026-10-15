import unittest
from factory.core.tui.chatwidget import ChatWidget
from factory.core.tui.render.renderable import FlexRenderable, Rect, Renderable, RenderableItem
from factory.core.tui.cells.history_cell import HistoryCell


class FakeCell(HistoryCell):
    def __init__(self, lines): self._lines = lines
    def display_lines(self, width): return self._lines

class FakeBottomPane(Renderable):
    def __init__(self, height=5): self._h = height
    def render(self, area, buf): pass
    def desired_height(self, width): return self._h
    def cursor_pos(self, area): return (area.x + 2, area.y + 1)


class ChatWidgetTest(unittest.TestCase):
    def test_empty_widget(self):
        w = ChatWidget()
        flex = w.as_renderable()
        self.assertEqual(len(flex._children), 1)

    def test_with_bottom_pane(self):
        w = ChatWidget()
        w.set_bottom_pane(FakeBottomPane(5))
        flex = w.as_renderable()
        self.assertEqual(len(flex._children), 2)
        # Bottom pane should be flex=0 (fixed)
        self.assertEqual(flex._children[1].flex, 0)

    def test_with_active_cell(self):
        w = ChatWidget()
        w.set_active_cell(FakeCell(["line1", "line2"]))
        flex = w.as_renderable()
        # History area should be flex=1 (flexible)
        self.assertEqual(flex._children[0].flex, 1)

    def test_commit_active_cell(self):
        w = ChatWidget()
        w.set_active_cell(FakeCell(["active line"]))
        w.commit_active_cell()
        self.assertIsNone(w.active_cell)
        self.assertEqual(len(w.committed_cells), 1)

    def test_desired_height(self):
        w = ChatWidget()
        w.set_bottom_pane(FakeBottomPane(5))
        w.set_active_cell(FakeCell(["a", "b", "c"]))
        h = w.desired_height(80)
        self.assertGreaterEqual(h, 5)

    def test_cursor_pos(self):
        w = ChatWidget()
        w.set_bottom_pane(FakeBottomPane(5))
        area = Rect(0, 0, 80, 24)
        cp = w.cursor_pos(area)
        self.assertIsNotNone(cp)


if __name__ == "__main__":
    unittest.main()
