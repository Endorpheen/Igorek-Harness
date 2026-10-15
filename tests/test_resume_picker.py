"""Tests for resume picker — rendering and state logic."""
import unittest
from factory.core.tui.resume_picker import (
    PickerState, PickerRow, SessionPickerAction, SessionSelection,
    SortKey, _render_picker, _format_ts, _truncate, _single_line,
    _enter_alt_screen, _leave_alt_screen,
)
from unittest import mock


class _FakeStdout:
    def __init__(self, is_tty=True):
        self.is_tty = is_tty
        self.output = ""

    def isatty(self):
        return self.is_tty

    def write(self, text):
        self.output += text

    def flush(self):
        pass


def _make_row(session_id="test-session", preview="hello world", model="deepseek-v4",
              updated_at=1715073600, created_at=1715070000, message_count=5,
              cwd="/home/user/proj", git_branch="main", profile="cc-openrouter"):
    return PickerRow(
        session_id=session_id, path=f"/tmp/{session_id}.jsonl",
        preview=preview, model=model, profile=profile, cwd=cwd,
        git_branch=git_branch, message_count=message_count,
        created_at=created_at, updated_at=updated_at,
    )


class PickerStateTest(unittest.TestCase):
    def test_apply_filter_no_query_returns_all(self):
        state = PickerState()
        state.set_rows([_make_row("a"), _make_row("b")])
        self.assertEqual(len(state.filtered_rows), 2)

    def test_apply_filter_matches_session_id(self):
        state = PickerState()
        state.set_rows([_make_row("alpha"), _make_row("beta")])
        state.set_query("alp")
        self.assertEqual(len(state.filtered_rows), 1)
        self.assertEqual(state.filtered_rows[0].session_id, "alpha")

    def test_apply_filter_matches_preview(self):
        state = PickerState()
        state.set_rows([_make_row("a", preview="create file"), _make_row("b", preview="list files")])
        state.set_query("create")
        self.assertEqual(len(state.filtered_rows), 1)

    def test_apply_filter_matches_model(self):
        state = PickerState()
        state.set_rows([_make_row("a", model="deepseek-v4"), _make_row("b", model="gemma4")])
        state.set_query("gemma")
        self.assertEqual(len(state.filtered_rows), 1)

    def test_apply_filter_resets_selection(self):
        state = PickerState()
        state.set_rows([_make_row("a"), _make_row("b")])
        state.selected = 1
        state.set_query("b")
        self.assertEqual(state.selected, 0)

    def test_toggle_sort_key(self):
        state = PickerState()
        self.assertEqual(state.sort_key, SortKey.UPDATED)
        state.toggle_sort_key()
        self.assertEqual(state.sort_key, SortKey.CREATED)
        state.toggle_sort_key()
        self.assertEqual(state.sort_key, SortKey.UPDATED)

    def test_sort_by_updated_desc(self):
        state = PickerState()
        rows = [_make_row("old", updated_at=100), _make_row("new", updated_at=200)]
        state.set_rows(rows)
        self.assertEqual(state.filtered_rows[0].session_id, "new")

    def test_sort_by_created_desc(self):
        state = PickerState()
        rows = [_make_row("old", created_at=100), _make_row("new", created_at=200)]
        state.set_rows(rows)
        state.sort_key = SortKey.CREATED
        state.apply_filter()
        self.assertEqual(state.filtered_rows[0].session_id, "new")

    def test_selected_clamped_after_filter(self):
        state = PickerState()
        state.set_rows([_make_row("a"), _make_row("b"), _make_row("c")])
        state.selected = 2
        state.set_query("a")
        # Only 1 result, selected clamped to 0
        self.assertEqual(state.selected, 0)

    def test_ensure_selected_visible(self):
        state = PickerState()
        state.set_rows([_make_row(f"row-{i}") for i in range(20)])
        state.set_size(80, 10)
        state.selected = 15
        state.ensure_selected_visible()
        # scroll_top should be adjusted so selected is visible
        self.assertLessEqual(state.selected, state.scroll_top + state.view_height - 1)
        self.assertGreaterEqual(state.selected, state.scroll_top)


class RenderPickerTest(unittest.TestCase):
    def test_render_shows_header_with_action(self):
        state = PickerState(action=SessionPickerAction.RESUME)
        state.set_rows([_make_row()])
        state.set_size(80, 24)
        lines = _render_picker(state)
        self.assertTrue(any("Resume session" in l for l in lines))

    def test_render_shows_fork_header(self):
        state = PickerState(action=SessionPickerAction.FORK)
        state.set_rows([_make_row()])
        state.set_size(80, 24)
        lines = _render_picker(state)
        self.assertTrue(any("Fork session" in l for l in lines))

    def test_render_shows_search_placeholder(self):
        state = PickerState()
        state.set_rows([_make_row()])
        state.set_size(80, 24)
        lines = _render_picker(state)
        self.assertTrue(any("Type to search" in l for l in lines))

    def test_render_shows_query(self):
        state = PickerState()
        state.set_rows([_make_row()])
        state.set_size(80, 24)
        state.query = "deep"
        lines = _render_picker(state)
        self.assertTrue(any("Search: deep" in l for l in lines))

    def test_render_shows_sessions(self):
        state = PickerState()
        state.set_rows([_make_row("alpha", preview="hello alpha"), _make_row("beta", preview="hello beta")])
        state.set_size(80, 24)
        lines = _render_picker(state)
        text = "\n".join(lines)
        self.assertIn("hello alpha", text)
        self.assertIn("hello beta", text)

    def test_render_selected_row_has_marker(self):
        state = PickerState()
        state.set_rows([_make_row("alpha"), _make_row("beta")])
        state.set_size(80, 24)
        state.selected = 1
        lines = _render_picker(state)
        # Selected row should have the › marker (contains "›" or "32m" for green)
        selected_line = lines[3 + 1]  # header + search + columns + idx
        self.assertIn("›", selected_line)

    def test_render_empty_state(self):
        state = PickerState()
        state.set_rows([])
        state.set_size(80, 24)
        lines = _render_picker(state)
        self.assertTrue(any("No sessions found" in l for l in lines))

    def test_render_shows_hints(self):
        state = PickerState()
        state.set_rows([_make_row()])
        state.set_size(80, 24)
        lines = _render_picker(state)
        last_line = lines[-1]
        self.assertIn("Enter", last_line)
        self.assertIn("Esc", last_line)

    def test_render_inline_error(self):
        state = PickerState()
        state.set_rows([_make_row()])
        state.set_size(80, 24)
        state.inline_error = "Test error"
        lines = _render_picker(state)
        self.assertTrue(any("Test error" in l for l in lines))


class HelperTest(unittest.TestCase):
    def test_format_ts(self):
        result = _format_ts(1715073600)
        self.assertIn("2024", result)  # sanity: contains year
        self.assertTrue(len(result) > 5)

    def test_format_ts_zero(self):
        self.assertEqual(_format_ts(0), "")

    def test_truncate_short(self):
        self.assertEqual(_truncate("hello", 10), "hello")

    def test_truncate_long(self):
        result = _truncate("hello world this is long", 10)
        self.assertEqual(len(result), 10)
        self.assertTrue(result.endswith("…"))

    def test_truncate_one_column(self):
        self.assertEqual(_truncate("hello", 1), "…")

    def test_truncate_zero_columns(self):
        self.assertEqual(_truncate("hello", 0), "")

    def test_truncate_collapses_multiline_preview(self):
        self.assertEqual(_truncate("one\n\ntwo\tthree", 20), "one two three")

    def test_single_line_collapses_whitespace(self):
        self.assertEqual(_single_line("  one\n two\tthree  "), "one two three")

    def test_alt_screen_enters_and_leaves_for_tty(self):
        stdout = _FakeStdout(is_tty=True)

        with mock.patch("factory.core.tui.resume_picker.sys.stdout", stdout):
            active = _enter_alt_screen()
            _leave_alt_screen(active)

        self.assertTrue(active)
        self.assertIn("\033[?1049h", stdout.output)
        self.assertIn("\033[?1049l", stdout.output)

    def test_alt_screen_skips_non_tty(self):
        stdout = _FakeStdout(is_tty=False)

        with mock.patch("factory.core.tui.resume_picker.sys.stdout", stdout):
            active = _enter_alt_screen()
            _leave_alt_screen(active)

        self.assertFalse(active)
        self.assertEqual(stdout.output, "")


if __name__ == "__main__":
    unittest.main()
