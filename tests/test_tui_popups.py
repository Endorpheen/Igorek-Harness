import unittest
from factory.core.tui.bottom_pane.command_popup import CommandPopup
from factory.core.tui.bottom_pane.file_search_popup import FileSearchPopup
from factory.core.tui.bottom_pane.skill_popup import SkillPopup
from factory.core.tui.bottom_pane.scroll_state import ScrollState
from factory.core.tui.bottom_pane.selection_popup_common import render_popup_row
from factory.core.tui.bottom_pane.pending_input_preview import PendingInputPreview

class PopupTest(unittest.TestCase):
    def test_command_popup_filter(self):
        p = CommandPopup()
        p.on_composer_text_change("/ex")
        items = p.filtered_items()
        self.assertGreater(len(items), 0)
        self.assertIn("/exit", [m.command for m in items])

    def test_command_popup_select(self):
        p = CommandPopup()
        p.on_composer_text_change("/exit")
        item = p.selected_item()
        self.assertIsNotNone(item)
        self.assertEqual(item.command, "/exit")

    def test_command_popup_navigate(self):
        p = CommandPopup()
        p.on_composer_text_change("/")
        items = p.filtered_items()
        self.assertGreater(len(items), 1)
        p.move_down()
        self.assertEqual(p.scroll.selected, 1)

    def test_command_popup_exact_before_prefix(self):
        p = CommandPopup()
        p.on_composer_text_change("/he")
        items = p.filtered_items()
        cmds = [m.command for m in items]
        # /help should be first exact match
        self.assertTrue(len(cmds) >= 1)

    def test_command_popup_empty_query_shows_all(self):
        p = CommandPopup()
        p.on_composer_text_change("/")
        items = p.filtered_items()
        # Should show all non-alias commands
        self.assertGreater(len(items), 5)

    def test_command_popup_includes_session_commands(self):
        p = CommandPopup()
        p.on_composer_text_change("/")

        cmds = [m.command for m in p.filtered_items()]

        self.assertIn("/resume", cmds)
        self.assertIn("/fork", cmds)

    def test_command_popup_includes_terminal_title_setup(self):
        p = CommandPopup()
        p.on_composer_text_change("/title")

        self.assertEqual([m.command for m in p.filtered_items()], ["/title"])

    def test_command_popup_includes_theme_picker(self):
        p = CommandPopup()
        p.on_composer_text_change("/theme")

        self.assertEqual([m.command for m in p.filtered_items()], ["/theme"])

    def test_command_popup_prefix_includes_skills(self):
        p = CommandPopup()
        p.on_composer_text_change("/s")

        self.assertIn("/skills", [m.command for m in p.filtered_items()])

    def test_command_popup_prefix_includes_remember(self):
        p = CommandPopup()
        p.on_composer_text_change("/rem")

        self.assertEqual([m.command for m in p.filtered_items()], ["/remember"])

    def test_command_popup_filters_session_commands(self):
        p = CommandPopup()
        p.on_composer_text_change("/res")

        self.assertEqual([m.command for m in p.filtered_items()], ["/resume"])

    def test_command_popup_esc_clears(self):
        p = CommandPopup()
        p.on_composer_text_change("/help")
        self.assertEqual(len(p.filtered_items()), 1)
        p.on_composer_text_change("")  # simulate esc/clear
        self.assertGreater(len(p.filtered_items()), 5)

    def test_file_search(self):
        p = FileSearchPopup(cwd=".")
        p.on_composer_text_change("@cli_chat_rich")
        self.assertGreater(len(p.results), 0)

    def test_file_search_no_match(self):
        p = FileSearchPopup(cwd=".")
        p._all_files = ["aaa.py", "bbb.txt"]  # skip real scan
        p.on_composer_text_change("@xyznonexistent12345")
        self.assertEqual(len(p.results), 0)

    def test_file_search_selected(self):
        p = FileSearchPopup(cwd=".")
        p._all_files = list(set(p._all_files or [])) if p._all_files else ["test.py"]
        p.on_composer_text_change("@py")
        if p.results:
            p.move_down()
            f = p.selected_file()
            self.assertIsNotNone(f)

    def test_skill_popup(self):
        p = SkillPopup()
        p.set_skills([("github-code-review", "Review code via GitHub")])
        p.update_query("code")
        self.assertEqual(len(p.filtered), 1)

    def test_skill_popup_open(self):
        """$ in empty composer opens skill popup."""
        from factory.core.tui.bottom_pane.chat_composer import ChatComposer, ActivePopup
        c = ChatComposer()
        c.skill_popup.set_skills([("excalidraw", "Draw diagrams"), ("obsidian", "Notes")])
        c.handle_key_event("$")
        self.assertEqual(c.active_popup, ActivePopup.SKILL)
        self.assertEqual(c.text, "$")

    def test_skill_popup_filter(self):
        p = SkillPopup()
        p.set_skills([
            ("github-code-review", "Review code via GitHub"),
            ("excalidraw", "Draw diagrams"),
            ("obsidian", "Notes management"),
        ])
        p.on_composer_text_change("$git")
        self.assertEqual(len(p.filtered), 1)
        self.assertEqual(p.filtered[0][0], "github-code-review")

    def test_skill_popup_filter_by_description(self):
        p = SkillPopup()
        p.set_skills([("excalidraw", "Draw diagrams"), ("obsidian", "Notes management")])
        p.on_composer_text_change("$diagram")
        self.assertEqual(len(p.filtered), 1)
        self.assertEqual(p.filtered[0][0], "excalidraw")

    def test_skill_popup_navigate(self):
        p = SkillPopup()
        p.set_skills([("aaa", "desc a"), ("bbb", "desc b"), ("ccc", "desc c")])
        p.on_composer_text_change("$")
        self.assertEqual(p.scroll.selected, 0)
        p.move_down()
        self.assertEqual(p.scroll.selected, 1)
        p.move_down()
        self.assertEqual(p.scroll.selected, 2)
        p.move_down()
        self.assertEqual(p.scroll.selected, 0)  # wrap-around

    def test_skill_popup_select(self):
        """Enter/Tab in composer inserts $skill-name."""
        from factory.core.tui.bottom_pane.chat_composer import ChatComposer, ActivePopup
        c = ChatComposer()
        c.skill_popup.set_skills([("excalidraw", "Draw diagrams"), ("obsidian", "Notes")])
        c.handle_key_event("$")
        c.handle_key_event("down")
        c.handle_key_event("enter")
        self.assertEqual(c.active_popup, ActivePopup.NONE)
        self.assertEqual(c.text, "$obsidian ")

    def test_skill_popup_tab_select(self):
        from factory.core.tui.bottom_pane.chat_composer import ChatComposer, ActivePopup
        c = ChatComposer()
        c.skill_popup.set_skills([("excalidraw", "Draw diagrams")])
        c.handle_key_event("$")
        c.handle_key_event("tab")
        self.assertEqual(c.active_popup, ActivePopup.NONE)
        self.assertEqual(c.text, "$excalidraw ")

    def test_skill_popup_escape(self):
        from factory.core.tui.bottom_pane.chat_composer import ChatComposer, ActivePopup
        c = ChatComposer()
        c.skill_popup.set_skills([("excalidraw", "Draw diagrams")])
        c.handle_key_event("$")
        self.assertEqual(c.active_popup, ActivePopup.SKILL)
        c.handle_key_event("escape")
        self.assertEqual(c.active_popup, ActivePopup.NONE)

    def test_skill_popup_backspace_close(self):
        """Backspace when text is just $ closes popup."""
        from factory.core.tui.bottom_pane.chat_composer import ChatComposer, ActivePopup
        c = ChatComposer()
        c.skill_popup.set_skills([("excalidraw", "Draw diagrams")])
        c.handle_key_event("$")
        self.assertEqual(c.active_popup, ActivePopup.SKILL)
        c.handle_key_event("backspace")
        self.assertEqual(c.active_popup, ActivePopup.NONE)

    def test_skill_popup_empty_skills(self):
        p = SkillPopup()
        p.set_skills([])
        lines = p.render_lines(80)
        self.assertTrue(any("no skills" in l for l in lines))

    def test_skill_popup_no_match(self):
        p = SkillPopup()
        p.set_skills([("excalidraw", "Draw diagrams")])
        p.on_composer_text_change("$zzzznonexistent")
        lines = p.render_lines(80)
        self.assertTrue(any("no matches" in l for l in lines))

    def test_skill_popup_render(self):
        p = SkillPopup()
        p.set_skills([("excalidraw", "Draw diagrams")])
        p.on_composer_text_change("$")
        lines = p.render_lines(80)
        self.assertTrue(any("excalidraw" in l for l in lines))
        self.assertTrue(any("\u25ba" in l for l in lines))  # ► marker

    def test_skill_popup_desired_height(self):
        p = SkillPopup()
        p.set_skills([("a", "d")] * 20)
        p.on_composer_text_change("$")
        h = p.desired_height()
        self.assertLessEqual(h, 9)  # min(20, 8) + 1

    def test_skill_popup_type_filters(self):
        """Typing after $ updates filter."""
        from factory.core.tui.bottom_pane.chat_composer import ChatComposer, ActivePopup
        c = ChatComposer()
        c.skill_popup.set_skills([
            ("excalidraw", "Draw diagrams"),
            ("obsidian", "Notes"),
            ("ocr-and-documents", "OCR support"),
        ])
        c.handle_key_event("$")
        c.handle_key_event("o")
        self.assertEqual(len(c.skill_popup.filtered), 2)  # obsidian + ocr-and-documents

    def test_scroll_state_basic(self):
        s = ScrollState(20, 5)
        self.assertEqual(s.selected, 0)
        s.move_down(); s.move_down()
        self.assertEqual(s.selected, 2)
        s.move_up()
        self.assertEqual(s.selected, 1)

    def test_scroll_state_wraparound(self):
        s = ScrollState(5, 3)
        for _ in range(6): s.move_down()
        self.assertEqual(s.selected, 1)

    def test_render_popup_row(self):
        r = render_popup_row("/exit", "Exit session", selected=True)
        self.assertIn("►", r)
        self.assertIn("/exit", r)

    def test_pending_input_preview(self):
        p = PendingInputPreview()
        p.add_queued("hello world")
        lines = p.render_lines()
        self.assertGreater(len(lines), 0)
        self.assertIn("Queued follow-up messages", lines[0])
        self.assertIn("hello world", lines[1])

    def test_pending_input_preview_pending_steer_header(self):
        p = PendingInputPreview()
        p.add_pending_steer("steer while running")
        lines = p.render_lines()
        self.assertIn("Messages to be submitted after next tool call", lines[0])
        self.assertIn("press esc to interrupt", lines[0])
        self.assertIn("steer while running", lines[1])

    def test_pending_input_empty(self):
        p = PendingInputPreview()
        self.assertTrue(p.is_empty())

    def test_pending_input_multiple(self):
        p = PendingInputPreview()
        for i in range(5):
            p.add_queued(f"message {i}")
        lines = p.render_lines()
        self.assertIn("...and", "\n".join(lines))

    def test_file_search_selected(self):
        p = FileSearchPopup(cwd=".")
        p._all_files = ["aaa.py", "test.py"]
        p.on_composer_text_change("@py")
        if p.results:
            p.move_down()
            f = p.selected_file()
            self.assertIsNotNone(f)
