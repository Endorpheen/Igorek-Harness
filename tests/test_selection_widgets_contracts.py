"""Contract tests for selection widgets and input widgets."""
from factory.core.tui.bottom_pane.custom_prompt_view import CustomPromptView
from factory.core.tui.bottom_pane.list_selection_view import ListSelectionView, SelectionItem
from factory.core.tui.bottom_pane.multi_select_picker import MultiSelectPicker, MultiSelectItem
from factory.core.tui.bottom_pane.textarea import TextArea


def _long_items(count: int) -> list[SelectionItem]:
    return [SelectionItem(f"item-{i}", f"Description {i}") for i in range(count)]


def _long_keys(count: int) -> list[MultiSelectItem]:
    return [MultiSelectItem(str(i), f"Option {i}") for i in range(count)]


# ---------------------------------------------------------------- ListSelectionView


def test_set_items_resets_navigation_to_first_item_and_offset():
    view = ListSelectionView(title="Pick one")
    view.set_items(_long_items(12))
    for _ in range(9):
        view.scroll.move_down()
    assert view.scroll.selected == 9
    assert view.scroll.offset > 0

    view.set_items([SelectionItem("alpha", "Alpha desc"), SelectionItem("beta", "Beta desc")])

    assert view.selected_item() is not None
    assert view.selected_item().name == "alpha"
    assert view.scroll.selected == 0
    assert view.scroll.offset == 0

    view.scroll.move_down()
    assert view.selected_item() is not None
    assert view.selected_item().name == "beta"


def test_empty_items_give_no_selected_item():
    view = ListSelectionView()
    view.set_items([])
    assert view.selected_item() is None


def test_render_shows_title_marker_selection_and_description():
    view = ListSelectionView(title="Pick one")
    view.set_items([SelectionItem("alpha", "Alpha desc", selected=True), SelectionItem("beta", "Beta desc")])

    rendered = "\n".join(view.render_lines())

    assert "Pick one" in rendered
    assert "►" in rendered
    assert "●" in rendered
    assert "Alpha desc" in rendered
    assert "beta" in rendered

    assert view.selected_item() is not None
    assert view.selected_item().name == "alpha"
    assert view.scroll.selected == 0


# ---------------------------------------------------------------- MultiSelectPicker


def test_multi_set_items_resets_navigation_to_first_item():
    picker = MultiSelectPicker(title="Pick many")
    picker.set_items(_long_keys(5))
    picker.toggle_current()
    picker.scroll.move_down()

    picker.set_items(
        [MultiSelectItem("keep", "Keep", selected=True), MultiSelectItem("skip", "Skip")]
    )

    assert picker.scroll.selected == 0
    assert picker.scroll.offset == 0
    assert picker.selected_keys() == ["keep"]

    picker.toggle_current()
    assert picker.selected_keys() == []

    picker.toggle_current()
    assert picker.selected_keys() == ["keep"]


def test_multi_render_shows_title_marker_and_selection_flags():
    picker = MultiSelectPicker(title="Pick many")
    picker.set_items([MultiSelectItem("keep", "Keep", selected=True), MultiSelectItem("skip", "Skip")])

    rendered = "\n".join(picker.render_lines())

    assert "Pick many" in rendered
    assert "[x] Keep" in rendered
    assert "[ ] Skip" in rendered
    assert "►" in rendered

    assert picker.selected_keys() == ["keep"]


def test_empty_items_give_empty_selected_keys_and_safe_toggle():
    picker = MultiSelectPicker()
    picker.set_items([])
    assert picker.selected_keys() == []
    picker.toggle_current()
    assert picker.selected_keys() == []


def test_double_toggle_returns_to_previous_state():
    picker = MultiSelectPicker()
    picker.set_items(_long_keys(3))
    picker.toggle_current()
    assert picker.selected_keys() == ["0"]
    picker.toggle_current()
    assert picker.selected_keys() == []


# ---------------------------------------------------------------- TextArea


def test_textarea_insert_unicode_char_in_middle():
    area = TextArea()
    area.text = "ab"
    area.cursor = 1
    area.insert("é")
    assert area.text == "aéb"
    assert area.cursor == 2


def test_textarea_delete_before_cursor():
    area = TextArea()
    area.text = "abc"
    area.cursor = 3
    area.delete_before()
    assert area.text == "ab"
    assert area.cursor == 2


def test_textarea_move_and_clear_boundaries():
    area = TextArea()
    area.text = "abc"
    area.cursor = 2
    area.move_left()
    assert area.cursor == 1
    area.move_left()
    area.move_left()
    assert area.cursor == 0
    area.move_right()
    assert area.cursor == 1
    area.move_right()
    assert area.cursor == 2
    area.move_right()
    assert area.cursor == 3
    area.clear()
    assert area.text == ""
    assert area.cursor == 0


# ---------------------------------------------------------------- CustomPromptView


def test_custom_prompt_toggle_visibility_and_replacement():
    prompt = CustomPromptView()
    assert prompt.render_lines() == []

    prompt.show("Enter value", "default")
    rendered = "\n".join(prompt.render_lines(width=40))
    assert "Enter value" in rendered
    assert "default" in rendered

    prompt.hide()
    assert prompt.render_lines() == []

    prompt.show("Replace me", "new default")
    rendered = "\n".join(prompt.render_lines(width=40))
    assert "Replace me" in rendered
    assert "new default" in rendered
    assert "Enter value" not in rendered


def test_selected_keys_preserve_order_and_keep_flags():
    picker = MultiSelectPicker()
    picker.set_items([MultiSelectItem("b", "B", selected=True), MultiSelectItem("a", "A")])
    assert picker.selected_keys() == ["b"]

    picker.set_items([MultiSelectItem("z", "Z", selected=True), MultiSelectItem("y", "Y")])
    assert picker.selected_keys() == ["z"]
