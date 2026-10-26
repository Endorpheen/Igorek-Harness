"""Layout contracts preserve viewport bounds and cursor ownership, without a TTY."""

import pytest

from factory.core.tui.render import renderable as r


class Probe(r.Renderable):
    def __init__(self, name, height=1, cursor=False):
        self.name = name
        self.height = height
        self.cursor = cursor
        self.areas = []
        self.widths = []

    def render(self, area, buf):
        self.areas.append(area)
        buf.extend(
            self.name + ":" + str(i) for i in range(min(self.height, area.height))
        )

    def desired_height(self, width):
        self.widths.append(width)
        return self.height

    def cursor_pos(self, area):
        return (area.x, area.y) if self.cursor and not area.is_empty() else None

    def cursor_style(self, area):
        return "bar"


@pytest.mark.parametrize("width,height", [(0, 4), (4, 0), (0, 0)])
def test_empty_column_does_not_render_or_expose_a_cursor(width, height):
    column = r.ColumnRenderable()
    child = Probe("hidden", cursor=True)
    column.push(child)
    buf = []
    area = r.Rect(5, 7, width, height)
    column.render(area, buf)
    assert (
        buf == []
        and child.areas == []
        and column.cursor_pos(area) is None
        and column.cursor_style(area) == "default"
    )


def test_column_clips_tail_and_keeps_first_visible_cursor_style():
    children = [Probe("A", 2), Probe("B", 2, True), Probe("C", 1, True)]
    column = r.ColumnRenderable.with_children(
        [r.renderable_item_from(child) for child in children]
    )
    buf = []
    area = r.Rect(5, 4, 8, 3)
    column.render(area, buf)
    assert [child.areas for child in children] == [
        [r.Rect(5, 4, 8, 2)],
        [r.Rect(5, 6, 8, 1)],
        [],
    ]
    assert buf == ["A:0", "A:1", "B:0"] and column.desired_height(8) == 5
    assert column.cursor_pos(area) == (5, 6) and column.cursor_style(area) == "bar"


def test_row_measures_only_visible_children_and_clips_right_edge():
    row = r.RowRenderable()
    children = [Probe("A", 1), Probe("B", 2, True), Probe("C", 1, True)]
    for width, child in zip([4, 6, 3], children):
        row.push(width, child)
    area = r.Rect(5, 4, 8, 3)
    buf = []
    row.render(area, buf)
    assert [child.areas for child in children] == [
        [r.Rect(5, 4, 4, 3)],
        [r.Rect(9, 4, 4, 3)],
        [],
    ]
    assert (
        row.desired_height(8) == 2
        and row.cursor_pos(area) == (9, 4)
        and row.cursor_style(area) == "bar"
    )
    assert row.desired_height(0) == 0


@pytest.mark.parametrize(
    "kind", [r.RowRenderable, r.ColumnRenderable, r.FlexRenderable]
)
def test_empty_layout_has_no_content_or_cursor(kind):
    layout = kind()
    area = r.Rect(0, 0, 20, 10)
    buf = []
    layout.render(area, buf)
    assert (
        layout.desired_height(20) == 0
        and buf == []
        and layout.cursor_pos(area) is None
        and layout.cursor_style(area) == "default"
    )


def test_fixed_footer_keeps_its_rows_when_body_needs_more_than_viewport():
    flex = r.FlexRenderable()
    body = Probe("body", 99)
    footer = Probe("footer", 2, True)
    flex.push(1, body)
    flex.push(0, footer)
    area = r.Rect(3, 2, 12, 6)
    buf = []
    flex.render(area, buf)
    assert flex.allocate(area) == [r.Rect(3, 2, 12, 4), r.Rect(3, 6, 12, 2)]
    assert (
        len(buf) == 6
        and flex.cursor_pos(area) == (3, 6)
        and flex.cursor_style(area) == "bar"
    )
    assert flex.desired_height(12) == 101


def test_weighted_flex_assigns_the_remainder_without_overflow():
    flex = r.FlexRenderable()
    flex.push(1, Probe("A", 100))
    flex.push(2, Probe("B", 100, True))
    area = r.Rect(3, 2, 8, 7)
    assert flex.allocate(area) == [r.Rect(3, 2, 8, 2), r.Rect(3, 4, 8, 5)]
    assert flex.cursor_pos(area) == (3, 4) and flex.cursor_style(area) == "bar"


@pytest.mark.parametrize(
    "factory",
    [
        lambda child: r.inset(child, r.Insets.vh(1, 2)),
        lambda child: r.renderable_item_from(child).inset(r.Insets.vh(1, 2)),
    ],
)
def test_inset_measurement_and_rendering_use_same_content_area(factory):
    child = Probe("inside", 2, True)
    outer = factory(child)
    area = r.Rect(5, 7, 10, 6)
    buf = []
    assert outer.desired_height(10) == 4 and child.widths == [6]
    outer.render(area, buf)
    assert child.areas == [r.Rect(7, 8, 6, 4)]
    assert outer.cursor_pos(area) == (7, 8) and outer.cursor_style(area) == "bar"


def test_narrow_inset_never_measures_a_negative_content_width():
    child = Probe("inside")
    outer = r.inset(child, r.Insets.vh(1, 2))
    assert outer.desired_height(1) == 3
    outer.render(r.Rect(0, 0, 1, 4), [])
    assert child.widths == [0] and child.areas[0].width == 0


@pytest.mark.parametrize(
    "wrapper", [r._StringRenderable, r._SpanRenderable, r._LineRenderable]
)
def test_public_text_wrappers_skip_empty_viewports(wrapper):
    text = wrapper("owned text")
    buf = []
    text.render(r.Rect(0, 0, 0, 2), buf)
    text.render(r.Rect(0, 0, 8, 0), buf)
    assert buf == []
    text.render(r.Rect(0, 0, 8, 1), buf)
    assert buf == ["owned text"] and text.desired_height(8) == 1
    assert (
        text.cursor_pos(r.Rect(0, 0, 8, 1)) is None
        and text.cursor_style(r.Rect(0, 0, 8, 1)) == "default"
    )


def test_multiline_wrapper_clips_rows_and_unit_wrapper_is_empty():
    text = r._ParagraphRenderable(["one", "two", "three"])
    buf = []
    text.render(r.Rect(0, 0, 8, 2), buf)
    assert buf == ["one", "two"] and text.desired_height(0) == 3
    text.render(r.Rect(0, 0, 0, 8), buf)
    assert buf == ["one", "two"]
    empty = r._UnitRenderable()
    empty.render(r.Rect(0, 0, 8, 2), buf)
    assert empty.desired_height(8) == 0
