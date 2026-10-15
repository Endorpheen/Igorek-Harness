import unittest
from factory.core.tui.bottom_pane.approval_overlay import (
    ApprovalOverlay, ApprovalRequest
)

class ApprovalTest(unittest.TestCase):
    def setUp(self):
        self.ov = ApprovalOverlay()
        self.ov.set_width(80)

    def test_empty_not_visible(self):
        self.ov.set_requests([])
        self.assertFalse(self.ov.visible)

    def test_with_requests_visible(self):
        self.ov.set_requests([ApprovalRequest("Bash", "ls -la")])
        self.assertTrue(self.ov.visible)

    def test_render_lines(self):
        self.ov.set_requests([ApprovalRequest("Write", "/tmp/x.py", reasoning="Creating file")])
        lines = self.ov.render_lines()
        self.assertGreater(len(lines), 3)
        self.assertTrue(any("Would you like to make the following edits?" in line for line in lines))
        self.assertTrue(any("Reason:" in line and "Creating file" in line for line in lines))
        self.assertTrue(any("Write: /tmp/x.py" in line for line in lines))
        self.assertTrue(any("Yes, proceed (y)" in line for line in lines))
        self.assertTrue(any("Press enter to confirm or esc to cancel" in line for line in lines))
        self.assertFalse(any("╭" in line or "╰" in line or "│" in line for line in lines))
        self.assertTrue(all(line.startswith("\033[48;2;") for line in lines))

    def test_render_uses_codex_like_menu_surface(self):
        self.ov.set_requests([ApprovalRequest("Bash", "echo hello")])
        lines = self.ov.render_lines()
        clean = [self.ov._strip_ansi(line) for line in lines]

        self.assertEqual(clean[0].strip(), "")
        self.assertTrue(any(
            line.rstrip() == "  Would you like to run the following command?"
            for line in clean
        ))
        self.assertTrue(any(line.startswith("› 1. Yes, proceed (y)") for line in clean))
        self.assertFalse(any("╭" in line or "╰" in line or "│" in line for line in clean))
        self.assertTrue(all(len(line) == len(clean[0]) for line in clean))

    def test_render_reason_uses_inline_markdown(self):
        self.ov.set_requests([
            ApprovalRequest(
                "Write",
                "/tmp/x.py",
                reasoning="**Зачем нужен файл:** проверить `reason`.",
            )
        ])

        lines = self.ov.render_lines()
        joined = "\n".join(lines)
        clean = self.ov._strip_ansi(joined)

        self.assertIn("Reason:", clean)
        self.assertIn("Зачем нужен файл:", clean)
        self.assertIn("reason", clean)
        self.assertIn("**", clean)
        self.assertIn("\033[1m", joined)
        self.assertIn("\033[1;36m", joined)

    def test_render_long_reason_is_truncated_to_fixed_height(self):
        self.ov.set_requests([
            ApprovalRequest(
                "Bash",
                "find . -type f",
                reasoning=" ".join(f"reason-{index}" for index in range(120)),
            )
        ])

        lines = self.ov._wrapped_reason_lines(self.ov.current().reasoning, 40)
        clean = [self.ov._strip_ansi(line) for line in lines]

        self.assertEqual(len(lines), self.ov.MAX_REASON_LINES)
        self.assertTrue(clean[-1].endswith("…"))
        self.assertTrue(clean[0].lstrip().startswith("Reason: "))

    def test_render_bash_approval_matches_codex_like_shape(self):
        self.ov.set_requests([ApprovalRequest("Bash", "echo hello")])
        lines = self.ov.render_lines()

        self.assertTrue(any("Would you like to run the following command?" in line for line in lines))
        self.assertTrue(any("$ echo hello" in line for line in lines))
        self.assertTrue(any("›" in line and "1. Yes, proceed (y)" in line for line in lines))
        self.assertTrue(any("2. No, and tell Igorek what to do differently (esc)" in line for line in lines))
        self.assertTrue(any("this command in this session" in line for line in lines))

    def test_approval_body_does_not_render_diff_content_for_write(self):
        self.ov.set_requests([
            ApprovalRequest(
                "Write",
                "/tmp/demo.txt",
                tool_input={"file_path": "/tmp/demo.txt", "content": "hello\nworld\n"},
            )
        ])

        lines = self.ov.render_lines()
        clean = "\n".join(self.ov._strip_ansi(line) for line in lines)

        self.assertNotIn("--- /tmp/demo.txt", clean)
        self.assertNotIn("+++ /tmp/demo.txt", clean)
        self.assertNotIn("+hello", clean)
        self.assertNotIn("-world", clean)
        self.assertNotIn("@@", clean)
        self.assertIn("Write: /tmp/demo.txt", clean)
        self.assertIn("don't ask again for these files", clean)

    def test_approval_body_does_not_render_diff_content_for_edit(self):
        self.ov.set_requests([
            ApprovalRequest(
                "Edit",
                "/tmp/demo.py",
                tool_input={
                    "file_path": "/tmp/demo.py",
                    "old_string": "line1\nline2\n",
                    "new_string": "line1-changed\nline2\n",
                },
            )
        ])

        lines = self.ov.render_lines()
        clean = "\n".join(self.ov._strip_ansi(line) for line in lines)

        self.assertNotIn("--- /tmp/demo.py", clean)
        self.assertNotIn("+++ /tmp/demo.py", clean)
        self.assertNotIn("+line1-changed", clean)
        self.assertNotIn("-line1", clean)
        self.assertNotIn("@@", clean)
        self.assertIn("Edit: /tmp/demo.py", clean)

    def test_render_long_bash_command_does_not_overflow_surface(self):
        long_command = "cat /home/example/.bashrc 2>/dev/null | grep -i '" + ("llama\\|" * 80) + "model'"
        self.ov.set_width(240)
        self.ov.set_requests([ApprovalRequest("Bash", long_command)])

        lines = self.ov.render_lines()
        surface_width = len(self.ov._strip_ansi(lines[0]))

        for line in lines:
            self.assertEqual(len(self.ov._strip_ansi(line)), surface_width)
        self.assertGreater(len(lines), 10)

    def test_render_multiline_reason_does_not_break_surface(self):
        # A long reason with embedded newlines (`\n`) should collapse into standard lines,
        # otherwise the terminal surface would split into multiple physical rows.
        reason = (
            "1) success: прочитал /home/example/Downloads/[Hindi (auto-generated)]"
            "            New Song  C N G Driver  Abdul Mojed [DownSub.com].txt,"
            " первые 2 строки:\n"
            "ल लि स्ट सुना ओ\n"
            "[संगी त]\n"
            "2)\n\n"
            "Write: /home/example/ObsidianVault/_test_vault_write.md"
        )
        self.ov.set_width(160)
        self.ov.set_requests([
            ApprovalRequest(
                "Write",
                "/home/example/ObsidianVault/_test_vault_write.md",
                reasoning=reason,
            )
        ])

        lines = self.ov.render_lines()
        self.assertGreater(len(lines), 0)
        surface_width = len(self.ov._strip_ansi(lines[0]))
        for line in lines:
            plain = self.ov._strip_ansi(line)
            self.assertEqual(
                len(plain), surface_width,
                f"row width {len(plain)} != surface width {surface_width}: "
                f"{plain!r}",
            )
        for line in lines:
            self.assertNotIn("\n", self.ov._strip_ansi(line))

    def test_render_long_reason_does_not_break_long_unbreakable_token(self):
        # A long path without spaces should be line-breaked without breaking the surface.
        long_path = "/" + ("very_long_path_segment/" * 40) + "file.md"
        reason = "I will write to " + long_path
        self.ov.set_width(120)
        self.ov.set_requests([
            ApprovalRequest("Write", long_path, reasoning=reason)
        ])

        lines = self.ov.render_lines()
        surface_width = len(self.ov._strip_ansi(lines[0]))
        for line in lines:
            plain = self.ov._strip_ansi(line)
            self.assertEqual(len(plain), surface_width)

    def test_action_labels_wrap_on_narrow_terminal(self):
        # On a narrow terminal, a long action label should be line-wrapped instead of being
        # truncated to a single line.
        self.ov.set_width(60)
        self.ov.set_requests([ApprovalRequest("Write", "out.md")])
        lines = self.ov.render_lines()
        surface_width = len(self.ov._strip_ansi(lines[0]))
        for line in lines:
            plain = self.ov._strip_ansi(line)
            self.assertEqual(len(plain), surface_width)

    def test_allow(self):
        allowed = []
        self.ov.set_on_allow(lambda s: allowed.append(s))
        self.ov.set_requests([ApprovalRequest("Bash", "ls")])
        result = self.ov.allow()
        self.assertIsNotNone(result)
        self.assertEqual(len(allowed), 1)

    def test_reject(self):
        rejected = [False]
        self.ov.set_on_reject(lambda: rejected.__setitem__(0, True))
        self.ov.set_requests([ApprovalRequest("Bash", "ls")])
        self.ov.reject()
        self.assertTrue(rejected[0])

    def test_allow_session(self):
        self.ov.set_requests([ApprovalRequest("Bash", "ls")])
        self.ov.allow_session()
        self.assertTrue(self.ov.is_session_allowed("Bash"))

    def test_handle_key_allow(self):
        self.ov.set_requests([ApprovalRequest("Bash", "ls")])
        r = self.ov.handle_key("y")
        self.assertIsNotNone(r)

    def test_handle_key_reject(self):
        self.ov.set_requests([ApprovalRequest("Bash", "ls")])
        r = self.ov.handle_key("n")
        self.assertEqual(r, "reject")

    def test_handle_key_session(self):
        self.ov.set_requests([ApprovalRequest("Bash", "ls")])
        r = self.ov.handle_key("s")
        self.assertTrue(self.ov.is_session_allowed("Bash"))

    def test_enter_confirms_selected_action(self):
        self.ov.set_requests([ApprovalRequest("Bash", "ls")])
        self.ov.move_action_selection(1)

        r = self.ov.handle_key("enter")

        self.assertEqual(r, "reject")

    def test_move_selection(self):
        self.ov.set_requests([
            ApprovalRequest("Bash", "a"),
            ApprovalRequest("Read", "b"),
        ])
        self.ov.move_selection(1)
        req = self.ov.current()
        self.assertEqual(req.tool_name, "Read")

    def test_desired_height(self):
        self.ov.set_requests([ApprovalRequest("Bash", "ls")])
        self.assertGreater(self.ov.desired_height(), 3)

    def test_multiple_requests_render(self):
        self.ov.set_requests([
            ApprovalRequest("Bash", "ls"),
            ApprovalRequest("Write", "/tmp/x"),
        ])
        lines = self.ov.render_lines()
        self.assertGreater(len(lines), 5)
