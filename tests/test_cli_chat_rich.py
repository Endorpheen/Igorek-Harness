import io
import inspect
import os
import queue
import re
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from rich.console import Console

from factory.core import (
    cli_chat_active,
    cli_chat_approval,
    cli_chat_idle,
    cli_chat_rich,
    cli_chat_rich_shared,
    cli_chat_terminal,
)
from factory.core.message_queue import MessageQueue
from factory.core.status_line import StatusLineConfig, load_status_line_config
from factory.core.trace_taxonomy import CcTraceEvent
from factory.core.tui.bottom_pane import BottomPane
from factory.core.tui.chatwidget import ChatWidget
from factory.core.tui.cells.history_cell import MarkdownCell, TextCell, ToolCallGroupCell
from factory.core.tui.status_indicator_widget import StatusIndicatorWidget
from factory.core.tui.trace_reducer import TraceEventReducer


class CliChatRichRenderingTest(unittest.TestCase):
    def test_active_turn_has_no_artificial_delay_before_application_exit(self):
        source = inspect.getsource(cli_chat_active._ask_task_with_live_trace)
        self.assertNotIn("await asyncio.sleep(0.3)", source)

    def test_empty_active_screen_does_not_suppress_final_error_render(self):
        self.assertFalse(
            cli_chat_active._has_streaming_output(
                {
                    "app_was_used": True,
                    "deferred_scrollback_lines": [],
                    "expanded_deferred_scrollback_lines": [],
                    "expanded_deferred_reasoning_lines": [],
                }
            )
        )
        self.assertTrue(
            cli_chat_active._has_streaming_output(
                {
                    "app_was_used": True,
                    "deferred_scrollback_lines": ["  visible output"],
                    "expanded_deferred_scrollback_lines": [],
                    "expanded_deferred_reasoning_lines": [],
                }
            )
        )

    def test_live_ansi_colors_use_terminal_theme_named_styles(self):
        self.assertEqual(cli_chat_rich_shared._ANSI_STYLE_MAP["32"], "fg:ansigreen")
        self.assertEqual(cli_chat_rich_shared._ANSI_STYLE_MAP["36"], "fg:ansicyan")
        self.assertEqual(cli_chat_rich_shared._ANSI_STYLE_MAP["96"], "fg:ansibrightcyan")

    def test_live_ansi_parser_combines_modifier_and_color(self):
        self.assertEqual(
            cli_chat_rich_shared._parse_ansi_style("1;36", ""),
            "bold fg:ansicyan",
        )

    def test_live_ansi_parser_keeps_standard_256_colors_theme_driven(self):
        self.assertEqual(
            cli_chat_rich_shared._parse_ansi_style("38;5;3", ""),
            "fg:ansiyellow",
        )

    def test_live_ansi_formatted_text_strips_escape_bytes(self):
        result = cli_chat_rich_shared._ansi_to_formatted_text(
            "\x1b[32mRan\x1b[0m"
        )
        self.assertEqual(result, [("fg:ansigreen", "Ran")])
        self.assertNotIn("\x1b", "".join(fragment for _, fragment in result))

    def test_rich_console_does_not_convert_path_line_to_emoji(self):
        stream = io.StringIO()
        console = cli_chat_rich._make_console(
            file=stream,
            force_terminal=True,
            color_system=None,
            width=100,
        )

        console.print(
            "factory/core/fact_checker.py:100:def check_git_status(",
            markup=False,
        )

        output = stream.getvalue()
        self.assertIn("factory/core/fact_checker.py:100:def", output)
        self.assertNotIn("💯", output)

    def test_assistant_msg_renders_code_fences_without_copy_hostile_boxes(self):
        stream = io.StringIO()
        console = Console(file=stream, force_terminal=True, width=100, color_system="truecolor")

        cli_chat_rich_shared._assistant_msg(
            console,
            "Команда:\n\n```bash\nhuggingface-cli download model --local-dir .\n```",
        )

        output = stream.getvalue()
        self.assertIn("huggingface-cli download model --local-dir .", output)
        self.assertNotIn("╭", output)
        self.assertNotIn("╰", output)
        self.assertNotIn("┌", output)
        self.assertNotIn("└", output)
        self.assertNotIn("│ huggingface-cli", output)
        self.assertNotIn("bash──", output)

    def test_completion_to_apply_prefers_current_completion(self):
        current = object()
        first = object()

        class State:
            current_completion = current
            completions = [first]

        class Buffer:
            complete_state = State()

        self.assertIs(cli_chat_idle._completion_to_apply(Buffer()), current)

    def test_completion_to_apply_falls_back_to_first_completion(self):
        first = object()

        class State:
            current_completion = None
            completions = [first]

        class Buffer:
            complete_state = State()

        self.assertIs(cli_chat_idle._completion_to_apply(Buffer()), first)

    def test_completion_to_apply_returns_none_without_matches(self):
        class State:
            current_completion = None
            completions = []

        class Buffer:
            complete_state = State()

        self.assertIsNone(cli_chat_idle._completion_to_apply(Buffer()))

    def test_completion_to_apply_starts_completion_when_needed(self):
        first = object()

        class State:
            current_completion = None
            completions = [first]

        class Buffer:
            complete_state = None

            def start_completion(self, select_first=True):
                self.complete_state = State()

        self.assertIs(cli_chat_idle._completion_to_apply(Buffer()), first)

    def test_idle_help_lists_skills_command(self):
        commands = {cmd for cmd, _desc in cli_chat_idle._SLASH_COMMANDS}
        self.assertIn("/skills", commands)

    def test_idle_help_lists_remember_command(self):
        commands = {cmd for cmd, _desc in cli_chat_idle._SLASH_COMMANDS}
        self.assertIn("/remember", commands)

    def test_rich_chat_orchestrator_delegates_tui_boundaries(self):
        self.assertIsNot(cli_chat_rich._make_prompt_session, cli_chat_idle._make_prompt_session)
        self.assertIsNot(cli_chat_rich._create_running_application, cli_chat_active._create_running_application)
        self.assertIsNot(cli_chat_rich._ask_approval, cli_chat_approval._ask_approval)
        self.assertIsNot(cli_chat_rich._task_terminal_title, cli_chat_terminal._task_terminal_title)

        self.assertEqual(cli_chat_rich._running_elapsed_label(65), cli_chat_active._running_elapsed_label(65))
        self.assertEqual(cli_chat_rich._blocked_tool_target({"tool": "Bash", "input": {"command": "pwd"}}), "pwd")
        self.assertEqual(cli_chat_rich._task_terminal_title("Сделай smoke\n\nподробности"), "Igorek · Сделай smoke")

    def test_prompt_session_erases_input_line_after_enter(self):
        captured = {}

        class FakePromptSession:
            def __init__(self, **kwargs):
                captured.update(kwargs)

        class FakeStyle:
            @staticmethod
            def from_dict(value):
                return value

        class FakeValidator:
            pass

        with (
            patch.object(cli_chat_rich, "PromptSession", FakePromptSession),
            patch.object(cli_chat_rich, "Style", FakeStyle),
            patch.object(cli_chat_rich, "Validator", FakeValidator, create=True),
            patch.object(cli_chat_rich, "ValidationError", ValueError, create=True),
        ):
            session = cli_chat_rich._make_prompt_session()

        self.assertIsInstance(session, FakePromptSession)
        self.assertIs(captured.get("erase_when_done"), True)
        self.assertIs(captured.get("multiline"), True)
        self.assertNotIn("mouse_support", captured)
        self.assertEqual(captured.get("prompt_continuation"), "  ")
        # Codex-like composer uses _InputFieldProcessor + BeforeInput("› ")
        self.assertGreaterEqual(len(captured.get("input_processors") or []), 1)

    def test_idle_ctrl_o_toggles_last_reasoning_in_prompt_region(self):
        captured = {}
        handlers = {}
        invalidated = []

        class FakePromptSession:
            def __init__(self, **kwargs):
                captured.update(kwargs)

        class FakeKeyBindings:
            def add(self, key):
                def decorator(func):
                    handlers[key] = func
                    return func
                return decorator

        class FakeStyle:
            @staticmethod
            def from_dict(value):
                return value

        class FakeValidator:
            pass

        class FakeApp:
            def invalidate(self):
                invalidated.append(True)

        class FakeEvent:
            app = FakeApp()

        reasoning_state = {
            "visible": False,
            "lines": ["  reasoning summary"],
            "expanded_lines": ["  reasoning detail"],
        }
        with (
            patch.object(cli_chat_idle, "PromptSession", FakePromptSession),
            patch.object(cli_chat_idle, "KeyBindings", FakeKeyBindings),
            patch.object(cli_chat_idle, "Style", FakeStyle),
            patch.object(cli_chat_idle, "Validator", FakeValidator),
            patch.object(cli_chat_idle, "ANSI", lambda text: text),
        ):
            cli_chat_idle._make_prompt_session(reasoning_state=reasoning_state)
            handlers["c-o"](FakeEvent())
            self.assertTrue(reasoning_state["visible"])
            self.assertIn("reasoning detail", captured["message"]())
            handlers["c-o"](FakeEvent())
            self.assertFalse(reasoning_state["visible"])
        self.assertEqual(len(invalidated), 2)

    def test_new_task_replaces_idle_reasoning_overlay(self):
        reasoning_state = {"visible": True, "lines": ["old"]}

        cli_chat_rich._update_reasoning_state(
            reasoning_state,
            {
                "_deferred_scrollback_lines": ["new"],
                "_expanded_deferred_scrollback_lines": ["expanded"],
            },
        )

        self.assertFalse(reasoning_state["visible"])
        self.assertEqual(reasoning_state["lines"], ["new"])
        self.assertEqual(reasoning_state["expanded_lines"], ["expanded"])

    def test_long_completed_reasoning_tail_freezes_before_idle_prompt(self):
        stream = io.StringIO()
        console = Console(file=stream, force_terminal=False, width=80)
        reasoning_state = {"visible": True, "lines": ["old"]}
        collapsed = ["  reasoning summary", "  tool", "  final answer"]

        with patch.object(cli_chat_rich, "_running_output_visible_line_budget", return_value=2):
            cli_chat_rich._update_reasoning_state(
                reasoning_state,
                {
                    "_deferred_scrollback_lines": collapsed,
                    "_expanded_deferred_scrollback_lines": [
                        "  private thought",
                        "  visible final answer",
                    ],
                    "_expanded_deferred_reasoning_lines": ["  private thought"],
                },
                console=console,
            )

        output = stream.getvalue()
        self.assertIn("reasoning summary", output)
        self.assertIn("final answer", output)
        self.assertNotIn("private thought", output)
        self.assertEqual(reasoning_state, {
            "visible": False,
            "lines": [],
            "expanded_lines": ["  private thought"],
            "frozen_scrollback": True,
        })

    def test_pre_frozen_reasoning_tail_is_not_repainted_by_idle_prompt(self):
        reasoning_state = {"visible": True, "lines": ["old"]}

        cli_chat_rich._update_reasoning_state(
            reasoning_state,
            {
                "_deferred_scrollback_lines": [],
                "_expanded_deferred_scrollback_lines": [
                    "  private thought",
                    "  visible final answer",
                ],
                "_expanded_deferred_reasoning_lines": ["  private thought"],
                "_deferred_scrollback_frozen": True,
            },
        )

        self.assertEqual(reasoning_state, {
            "visible": False,
            "lines": [],
            "expanded_lines": ["  private thought"],
            "frozen_scrollback": True,
        })

    def test_idle_reasoning_state_keeps_expanded_reasoning_after_printed_scrollback(self):
        reasoning_state = {"visible": True, "lines": ["old"]}

        cli_chat_rich._update_reasoning_state(
            reasoning_state,
            {
                "_deferred_scrollback_lines": [],
                "_expanded_deferred_scrollback_lines": [],
                "_expanded_deferred_reasoning_lines": ["  private thought"],
            },
        )

        self.assertEqual(reasoning_state, {
            "visible": False,
            "lines": [],
            "expanded_lines": ["  private thought"],
        })

    def test_approval_continuation_does_not_erase_previous_reasoning(self):
        reasoning_state = {
            "visible": False,
            "lines": [],
            "expanded_lines": ["  private thought before approval"],
            "frozen_scrollback": True,
        }

        cli_chat_rich._update_reasoning_state(
            reasoning_state,
            {
                "_deferred_scrollback_lines": [],
                "_expanded_deferred_scrollback_lines": [
                    "  Ran Bash(...)",
                    "  final visible answer",
                ],
                "_expanded_deferred_reasoning_lines": [],
                "_deferred_scrollback_frozen": True,
            },
            preserve_existing_reasoning=True,
        )

        self.assertEqual(reasoning_state, {
            "visible": False,
            "lines": [],
            "expanded_lines": ["  private thought before approval"],
            "frozen_scrollback": True,
        })

    def test_new_task_without_reasoning_clears_previous_reasoning(self):
        reasoning_state = {
            "visible": False,
            "lines": [],
            "expanded_lines": ["  private thought from old task"],
        }

        cli_chat_rich._update_reasoning_state(
            reasoning_state,
            {
                "_deferred_scrollback_lines": ["  new visible answer"],
                "_expanded_deferred_scrollback_lines": ["  new visible answer"],
                "_expanded_deferred_reasoning_lines": [],
            },
        )

        self.assertEqual(reasoning_state, {
            "visible": False,
            "lines": ["  new visible answer"],
            "expanded_lines": ["  new visible answer"],
        })

    def test_idle_ctrl_o_toggles_reasoning_after_long_tail_was_frozen(self):
        captured = {}
        handlers = {}

        class FakePromptSession:
            def __init__(self, **kwargs):
                captured.update(kwargs)

        class FakeKeyBindings:
            def add(self, key):
                def decorator(func):
                    handlers[key] = func
                    return func
                return decorator

        class FakeStyle:
            @staticmethod
            def from_dict(value):
                return value

        class FakeValidator:
            pass

        class FakeApp:
            def invalidate(self):
                pass

        class FakeEvent:
            app = FakeApp()

        reasoning_state = {
            "visible": False,
            "lines": [],
            "expanded_lines": ["  reasoning detail"],
        }
        with (
            patch.object(cli_chat_idle, "PromptSession", FakePromptSession),
            patch.object(cli_chat_idle, "KeyBindings", FakeKeyBindings),
            patch.object(cli_chat_idle, "Style", FakeStyle),
            patch.object(cli_chat_idle, "Validator", FakeValidator),
            patch.object(cli_chat_idle, "ANSI", lambda text: text),
        ):
            cli_chat_idle._make_prompt_session(reasoning_state=reasoning_state)
            self.assertIn("Размышления модели доступны", captured["message"]())
            handlers["c-o"](FakeEvent())
            self.assertTrue(reasoning_state["visible"])
            self.assertIn("reasoning detail", captured["message"]())


    def test_flushing_idle_reasoning_freezes_collapsed_lines(self):
        stream = io.StringIO()
        console = Console(file=stream, force_terminal=False, width=80)
        reasoning_state = {
            "visible": True,
            "lines": ["  collapsed"],
            "expanded_lines": ["  detail"],
            "frozen_scrollback": True,
        }

        cli_chat_rich._flush_deferred_scrollback(console, reasoning_state)

        self.assertIn("collapsed", stream.getvalue())
        self.assertNotIn("detail", stream.getvalue())
        self.assertEqual(reasoning_state, {
            "visible": False,
            "lines": [],
            "expanded_lines": [],
        })

    def test_user_msg_renders_submitted_text(self):
        stream = io.StringIO()
        console = Console(file=stream, force_terminal=False, width=80)

        cli_chat_rich._user_msg(console, "привет")

        output = stream.getvalue()
        self.assertIn("привет", output)

    def test_replay_initial_history_separates_assistant_from_next_user(self):
        stream = io.StringIO()
        console = Console(file=stream, force_terminal=False, width=80)

        cli_chat_rich._replay_initial_history(
            console,
            [
                {"role": "assistant", "text": "answer"},
                {"role": "user", "text": "question"},
            ],
        )

        lines = stream.getvalue().splitlines()
        answer_idx = lines.index("  answer")
        self.assertEqual(lines[answer_idx + 1], "")
        self.assertIn("question", "\n".join(lines[answer_idx + 2:]))

    def test_replay_initial_history_replays_trace_for_task_id(self):
        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            import json
            f.write(json.dumps({
                "kind": CcTraceEvent.TOOL_REQUEST_BATCH,
                "tools": [{
                    "id": "call_1",
                    "name": "Bash",
                    "input": {"command": "echo hi"},
                }],
            }) + "\n")
            f.write(json.dumps({
                "kind": CcTraceEvent.TOOL_RESULT,
                "tool_use_id": "call_1",
                "tool_name": "Bash",
                "tool_input": {"command": "echo hi"},
                "raw_result_preview": "hi\n",
            }) + "\n")
            f.write(json.dumps({
                "kind": CcTraceEvent.TASK_FINISH,
                "status": "ok",
                "output_preview": "Готово через trace.",
            }) + "\n")
            f.flush()
            trace_path = Path(f.name)

        try:
            stream = io.StringIO()
            console = Console(file=stream, force_terminal=False, width=120)

            with patch.object(
                cli_chat_rich,
                "_trace_path_for_task",
                return_value=trace_path,
            ):
                cli_chat_rich._replay_initial_history(
                    console,
                    [{
                        "role": "assistant",
                        "text": "Fallback text",
                        "task_id": "task-12345678",
                    }],
                )

            output = stream.getvalue()
            self.assertIn("Ran", output)
            self.assertIn("Bash", output)
            self.assertIn("echo hi", output)
            self.assertIn("Готово через trace.", output)
            self.assertNotIn("Fallback text", output)
        finally:
            trace_path.unlink(missing_ok=True)

    def test_replay_initial_history_does_not_pad_before_first_user(self):
        stream = io.StringIO()
        console = Console(file=stream, force_terminal=False, width=80)

        cli_chat_rich._replay_initial_history(
            console,
            [{"role": "user", "text": "question"}],
        )

        first_non_empty = next(line for line in stream.getvalue().splitlines() if line.strip())
        self.assertTrue(first_non_empty.strip().startswith("╭"))

    def test_resume_history_header_has_blank_line_before_first_message(self):
        stream = io.StringIO()
        console = Console(file=stream, force_terminal=False, width=80)

        cli_chat_rich._print_resume_history_header(
            console,
            "chat-demo",
            ["4 messages", "MiniMax-M3", "/tmp/project"],
        )
        cli_chat_rich._replay_initial_history(
            console,
            [{"role": "user", "text": "question"}],
        )

        lines = stream.getvalue().splitlines()
        header_idx = next(i for i, line in enumerate(lines) if "Resuming chat-demo" in line)
        self.assertEqual(lines[header_idx + 1], "")
        first_message_line = next(line for line in lines[header_idx + 2:] if line.strip())
        self.assertTrue(first_message_line.strip().startswith("╭"))

    def test_prompt_separator_renders_after_shared_helper_split(self):
        stream = io.StringIO()
        console = Console(file=stream, force_terminal=False, width=80)

        cli_chat_rich._prompt(console)

        output = stream.getvalue()
        self.assertIn("─", output)

    def test_logo_renders_profile_fields_without_octopus_art(self):
        stream = io.StringIO()
        console = Console(file=stream, force_terminal=True, color_system=None, width=100)

        cli_chat_rich._logo(
            console,
            profile="cc-opencode-go",
            model="test-model",
            branch="test-branch",
            backend="gguf",
        )

        output = stream.getvalue()
        self.assertNotIn("████", output)
        self.assertIn("Profile", output)
        self.assertIn("Provider", output)
        self.assertIn("YOUR_PC", output)
        self.assertIn("test-model", output)
        self.assertIn("test-branch", output)

    def test_message_queue_drains_fifo(self):
        queue = MessageQueue()

        queue.enqueue("первое")
        queue.enqueue("второе")

        self.assertEqual(queue.pending_count, 2)
        self.assertEqual(queue.drain_all(), ["первое", "второе"])
        self.assertEqual(queue.pending_count, 0)
        self.assertFalse(queue.has_pending)


    def test_statusline_setup_reset_restores_builtin_default(self):
        class FakeStatusLine:
            def __init__(self, settings_path):
                self.settings_path = settings_path
                self.config = StatusLineConfig(items=("model",))
                self.last_input = {
                    "backend": {"profile": "cc-local"},
                    "model": {"display_name": "gemma"},
                    "cwd": "/tmp/project",
                    "workspace": {"current_dir": "/tmp/project"},
                    "git": {"branch": "main", "is_clean": True},
                    "context_window": {},
                }
                self.text = ""

            def refresh(self):
                self.config = load_status_line_config(self.settings_path)
                self.text = cli_chat_rich.render_configured_status_line(
                    self.last_input,
                    self.config.items,
                )

            def plain_text(self):
                return self.text

        with tempfile.TemporaryDirectory() as tmp:
            stream = io.StringIO()
            console = Console(file=stream, force_terminal=False, width=100)
            settings = Path(tmp) / "settings.json"
            settings.write_text(
                '{"statusLine":{"type":"builtin","items":["model"]}}\n',
                encoding="utf-8",
            )
            sl = FakeStatusLine(settings)

            updated = cli_chat_rich._statusline_setup(
                console,
                sl,
                input_fn=lambda: "reset",
            )

            data = load_status_line_config(settings)

        self.assertTrue(updated)
        self.assertEqual(data.items, ())
        self.assertIn("reset to builtin default", stream.getvalue())

    def test_running_status_lines_include_queued_preview(self):
        with patch.object(cli_chat_rich.time, "monotonic", return_value=10.0):
            lines = cli_chat_rich._running_status_lines({
                "t0": 5.0,
                "pending_count": 2,
                "preview_items": ["первое сообщение", "второе сообщение"],
        })

        self.assertIn("• Working (5s", lines[0])
        self.assertIn("Messages to be submitted after next tool call", lines[1])
        self.assertIn("↳ первое сообщение", lines[2])
        self.assertIn("↳ второе сообщение", lines[3])

    def test_running_status_lines_omit_spinner_when_bottom_pane_owns_status(self):
        with patch.object(cli_chat_rich.time, "monotonic", return_value=10.0):
            lines = cli_chat_rich._running_status_lines({
                "t0": 5.0,
                "status_in_bottom_pane": True,
                "pending_count": 1,
                "preview_items": ["queued follow-up"],
            })

        self.assertFalse(any("Working" in line for line in lines))
        self.assertIn("Messages to be submitted after next tool call", lines[0])
        self.assertIn("↳ queued follow-up", lines[1])

    def test_running_status_lines_omit_preview_after_task_done_clear(self):
        with patch.object(cli_chat_rich.time, "monotonic", return_value=10.0):
            lines = cli_chat_rich._running_status_lines({
                "t0": 5.0,
                "task_done": True,
                "pending_count": 0,
                "preview_items": [],
            })

        rendered = "\n".join(lines)
        self.assertIn("Worked for 5s", rendered)
        self.assertNotIn("Messages to be submitted after next tool call", rendered)
        self.assertNotIn("↳", rendered)

    def test_running_output_lines_prefers_chat_widget_provider(self):
        original = ["old"]
        lines = cli_chat_rich._running_output_lines(
            original,
            output_provider=lambda: ["frame", 123],
        )

        self.assertEqual(lines, ["frame", "123"])
        self.assertEqual(original, ["old"])

    def test_running_activity_has_blank_line_after_content(self):
        lines = ["  • Explored", "  └ Read file"]

        cli_chat_active._append_running_activity_with_spacing(
            lines,
            ["  * Working (27s · esc to interrupt)"],
        )

        self.assertEqual(lines[-2].strip(), "")
        self.assertIn("Working", lines[-1])

    def test_running_activity_has_no_leading_blank_on_empty_output(self):
        lines = []

        cli_chat_active._append_running_activity_with_spacing(
            lines,
            ["  * Working (1s · esc to interrupt)"],
        )

        self.assertEqual(len(lines), 1)
        self.assertIn("Working", lines[0])

    def test_running_activity_can_force_gap_after_inserted_scrollback(self):
        lines = []

        cli_chat_active._append_running_activity_with_spacing(
            lines,
            ["  * Working (19s · esc to interrupt)"],
            force_leading_blank=True,
        )

        self.assertEqual(lines[0].strip(), "")
        self.assertIn("Working", lines[1])

    def test_live_viewport_has_gap_after_inserted_scrollback_before_tool_cell(self):
        lines = ["\033[2m•\033[0m \033[1mExplored\033[0m", "\033[2m  └ \033[0mRead file"]

        result = cli_chat_active._prepend_inline_scrollback_gap(
            lines,
            {"inline_scrollback_needs_activity_gap": True},
        )

        self.assertEqual(result[0].strip(), "")
        self.assertIn("Explored", result[1])
        self.assertEqual(result[2], lines[1])

    def test_live_viewport_gap_after_inserted_scrollback_is_not_duplicated(self):
        lines = [" ", "\033[2m•\033[0m \033[1mExplored\033[0m"]

        result = cli_chat_active._prepend_inline_scrollback_gap(
            lines,
            {"inline_scrollback_needs_activity_gap": True},
        )

        self.assertEqual(result, lines)

    def test_running_activity_spacer_is_preserved_as_visible_blank_row(self):
        lines = [
            "  \033[32mRan\033[0m \033[1mBash\033[0m(cat long-output)",
            "  \033[2m└\033[0m \033[32mdone\033[0m \033[2m· <0.1s\033[0m",
            "    " + ("x" * 260),
        ]

        cli_chat_active._append_running_activity_with_spacing(
            lines,
            ["  ✢ Working (55s · esc to interrupt)"],
        )

        self.assertEqual(lines[-2], " ")
        self.assertIn("Working", lines[-1])

    def test_working_activity_gap_uses_output_or_inserted_scrollback_state(self):
        self.assertTrue(
            cli_chat_active._working_activity_needs_leading_gap(
                ["  \033[32mRan\033[0m Bash"],
                {"task_active": True},
            )
        )
        self.assertTrue(
            cli_chat_active._working_activity_needs_leading_gap(
                [],
                {
                    "task_active": True,
                    "inline_scrollback_needs_activity_gap": True,
                },
            )
        )
        self.assertFalse(
            cli_chat_active._working_activity_needs_leading_gap(
                [],
                {"task_active": True},
            )
        )

    def test_chat_application_renders_working_as_separate_bottom_window(self):
        source = inspect.getsource(cli_chat_active._create_chat_application)

        self.assertIn("FormattedTextControl(_working_text)", source)
        self.assertIn("height=lambda: 1 + len(_working_lines())", source)
        self.assertIn("_format_ansi_lines([_LIVE_SPACER_LINE, *lines])", source)
        self.assertNotIn("activity_lines = _working_lines()", source)

    def test_chat_composer_height_matches_codex_padding_contract(self):
        class FakeBuffer:
            text = "one line"

        total = cli_chat_active._chat_input_height(FakeBuffer())
        textarea = cli_chat_active._chat_textarea_height(FakeBuffer())

        total_value = getattr(total, "preferred", total)
        textarea_value = getattr(textarea, "preferred", textarea)
        self.assertEqual(
            total_value,
            textarea_value + cli_chat_active._CHAT_COMPOSER_VERTICAL_PADDING,
        )
        self.assertGreaterEqual(total_value, 3)

    def test_chat_application_uses_codex_like_composer_surface(self):
        source = inspect.getsource(cli_chat_active._create_chat_application)

        self.assertEqual(
            cli_chat_active._CHAT_COMPOSER_STYLE["composer-surface"],
            "bg:#303030",
        )
        self.assertIn('BeforeInput("› ", style="class:input-prompt")', source)
        self.assertIn("height=lambda: _chat_textarea_height(buffer)", source)
        self.assertIn('style="class:composer-surface"', source)

    def _escape_handler(self, app):
        from prompt_toolkit.keys import Keys

        bindings = app.key_bindings.get_bindings_for_keys((Keys.Escape,))
        self.assertTrue(bindings)
        return bindings[-1].handler

    def test_fullscreen_escape_cancels_active_task_in_place(self):
        from types import SimpleNamespace
        from unittest import mock

        rs = {
            "task_active": True,
            "escape_interrupts": True,
            "current_task_id": "task-1",
            "interrupted": False,
            "reasoning_state": {"visible": False},
        }
        app, _buffer, _lines = cli_chat_active._create_chat_application(
            running_state=rs,
            fullscreen=True,
            transcript_provider=lambda: [],
            persistent=True,
            on_submit=lambda text: None,
        )
        if app is None:
            self.skipTest("prompt_toolkit unavailable")

        class _FakeApp:
            def __init__(self):
                self.exited = "unset"
                self.invalidated = 0
            def exit(self, result=None):
                self.exited = result
            def invalidate(self):
                self.invalidated += 1

        fake = _FakeApp()
        with mock.patch.object(cli_chat_active, "cancel_task") as cancel:
            self._escape_handler(app)(SimpleNamespace(app=fake))
        cancel.assert_called_once_with("task-1")
        self.assertEqual(fake.exited, "unset")  # did NOT exit the app
        self.assertTrue(rs["interrupted"])
        self.assertEqual(fake.invalidated, 1)

    def test_classic_escape_exits_with_interrupt_sentinel(self):
        from types import SimpleNamespace

        rs = {
            "task_active": True,
            "escape_interrupts": True,
            "reasoning_state": {"visible": False},
        }
        app, _buffer, _lines = cli_chat_active._create_chat_application(
            running_state=rs,
            fullscreen=False,
            persistent=False,
        )
        if app is None:
            self.skipTest("prompt_toolkit unavailable")

        class _FakeApp:
            def __init__(self):
                self.exited = "unset"
            def exit(self, result=None):
                self.exited = result
            def invalidate(self):
                pass

        fake = _FakeApp()
        self._escape_handler(app)(SimpleNamespace(app=fake))
        self.assertEqual(fake.exited, "__INTERRUPT__")

    def test_queued_input_lines_render_preview(self):
        lines = cli_chat_active._queued_input_lines(
            {"queued_input_items": ["привет   мир"]}
        )
        self.assertEqual(len(lines), 1)
        self.assertIn("В очереди", lines[0])
        self.assertIn("привет мир", lines[0])

    def test_queued_input_lines_empty(self):
        self.assertEqual(cli_chat_active._queued_input_lines({}), [])
        self.assertEqual(
            cli_chat_active._queued_input_lines({"queued_input_items": ["  "]}),
            [],
        )

    def test_queued_input_lines_caps_at_three_with_remainder(self):
        lines = cli_chat_active._queued_input_lines(
            {"queued_input_items": [f"q{i}" for i in range(5)]}
        )
        self.assertEqual(len(lines), 4)
        self.assertTrue(any("ещё 2" in line for line in lines))

    def test_idle_prompt_session_uses_codex_like_composer_surface(self):
        """Idle prompt must visually match the live Codex-like composer:
        same chevron ›, same bg:#303030, same composer-surface/input-prompt styles."""
        source = inspect.getsource(cli_chat_idle._make_prompt_session)

        # Same background as live composer (#303030, not #3a3a3a)
        self.assertIn('"composer-surface": "bg:#303030"', source)
        self.assertIn('"input-field": "bg:#303030 #ffffff"', source)
        self.assertIn('"input-prompt": "bg:#303030 bold #ffffff"', source)
        # Codex chevron via BeforeInput, not plain "> " message
        self.assertIn('BeforeInput("› ", style="class:input-prompt")', source)
        # No legacy plain prompt arrow in message
        self.assertNotIn('parts.append("> ")', source)

    def test_idle_composer_height_matches_codex_wrapped_textarea_contract(self):
        class FakeBuffer:
            text = "x" * 100

        with patch.object(
            cli_chat_idle.shutil,
            "get_terminal_size",
            return_value=os.terminal_size((40, 24)),
        ):
            height = cli_chat_idle._idle_composer_height(FakeBuffer())

        height_value = getattr(height, "preferred", height)
        # Codex computes textarea desired_height from wrapped visual lines:
        # width - LIVE_PREFIX_COLS - right_margin = 40 - 2 - 1 = 37.
        self.assertEqual(height_value, 3)

    def test_idle_composer_application_keeps_inline_completion_surface(self):
        source = inspect.getsource(cli_chat_idle._create_idle_composer_application)

        self.assertIn("CompletionsMenu(max_height=12, scroll_offset=1)", source)
        self.assertIn('filter=Condition(lambda: getattr(buffer, "complete_state", None) is not None)', source)
        self.assertIn("full_screen=False", source)
        self.assertIn("height=lambda: _idle_composer_height(buffer)", source)
        self.assertIn('BeforeInput("› ", style="class:input-prompt")', source)

    def test_idle_composer_completion_triggers_start_completion(self):
        app, _buffer = cli_chat_idle._create_idle_composer_application()
        self.assertIsNotNone(app)

        def find_binding(key_name):
            for binding in app.key_bindings.bindings:
                for key in binding.keys:
                    if getattr(key, "value", str(key)) == key_name:
                        return binding
            return None

        class FakeBuffer:
            def __init__(self):
                self.text = ""
                self.started = []

            def insert_text(self, text, fire_event=True):
                self.text += text

            def start_completion(self, select_first=True):
                self.started.append(select_first)

        class FakeApp:
            def __init__(self):
                self.invalidated = False

            def invalidate(self):
                self.invalidated = True

        for trigger in ("/", "@", "$"):
            binding = find_binding(trigger)
            self.assertIsNotNone(binding)
            fake_buffer = FakeBuffer()
            fake_app = FakeApp()

            binding.handler(type("E", (), {
                "current_buffer": fake_buffer,
                "app": fake_app,
            })())

            self.assertEqual(fake_buffer.text, trigger)
            self.assertEqual(fake_buffer.started, [False])
            self.assertTrue(fake_app.invalidated)

    def test_idle_composer_ctrl_c_exits_without_keyboard_interrupt_exception(self):
        app, _buffer = cli_chat_idle._create_idle_composer_application()
        self.assertIsNotNone(app)

        c_c = None
        for binding in app.key_bindings.bindings:
            for key in binding.keys:
                if getattr(key, "value", str(key)) == "c-c":
                    c_c = binding
                    break
            if c_c is not None:
                break

        self.assertIsNotNone(c_c)

        class FakeApp:
            def __init__(self):
                self.result = "not-called"
                self.exception = "not-called"

            def exit(self, result=None, exception=None):
                self.result = result
                self.exception = exception

        fake_app = FakeApp()
        c_c.handler(type("E", (), {"app": fake_app})())

        self.assertIsNone(fake_app.result)
        self.assertIsNone(fake_app.exception)

    def test_idle_composer_footer_uses_active_ansi_color_parser(self):
        class FakeStatusLine:
            class Config:
                padding = 0

            config = Config()

            def toolbar_text(self):
                return (
                    "\033[38;5;222mcc-glm\033[0m · "
                    "\033[38;5;114m~/AI/IgorekAgentFactory\033[0m · "
                    "\033[38;5;213m0.1.650\033[0m"
                )

        result = cli_chat_idle._idle_composer_footer_text(FakeStatusLine())
        flat = "".join(fragment for _, fragment in result)
        styles = [style for style, _fragment in result]

        self.assertIn("cc-glm", flat)
        self.assertIn("~/AI/IgorekAgentFactory", flat)
        self.assertIn("0.1.650", flat)
        self.assertNotIn("\033[", flat)
        self.assertTrue(any(style.startswith("fg:") for style in styles), styles)

    def test_chat_session_idle_input_uses_composer_application(self):
        from factory.core.chat_session import ChatSession

        source = inspect.getsource(ChatSession._read_input)

        self.assertIn("_create_idle_composer_application", source)
        self.assertIn("app.run()", source)
        self.assertIn("_make_prompt_session", source)





    def test_chat_widget_frame_lines_include_completed_tool_from_reducer(self):
        widget = ChatWidget(width=80)
        widget.set_bottom_pane(BottomPane())
        reducer = TraceEventReducer(widget=widget)
        reducer.apply_many([
            {
                "kind": CcTraceEvent.TOOL_REQUEST_BATCH,
                "round": 1,
                "tools": [
                    {"id": "a", "name": "Bash", "input": {"command": "pwd"}},
                ],
            },
            {
                "kind": CcTraceEvent.TOOL_RESULT,
                "tool_use_id": "a",
                "raw_result_preview": "/home/example",
                "duration_ms": 120,
            },
        ])

        lines = cli_chat_rich._chat_widget_frame_lines(widget, width=80, height=12)
        # Strip ANSI codes for text matching
        clean = [re.sub(r'\033\[[0-9;]*m', '', l) for l in lines]
        self.assertTrue(any("Ran Bash" in l for l in clean))
        self.assertTrue(any("done" in l for l in clean))

    def test_chat_widget_frame_lines_include_bottom_pane_status(self):
        widget = ChatWidget(width=80)
        bottom_pane = BottomPane()
        status = StatusIndicatorWidget()
        status.t0 = 0.0
        bottom_pane.set_status(status)
        widget.set_bottom_pane(bottom_pane)

        with patch.object(cli_chat_rich.time, "monotonic", return_value=5.0):
            lines = cli_chat_rich._chat_widget_frame_lines(widget, width=80, height=12)

        self.assertTrue(any("Working" in line for line in lines))
        self.assertTrue(any("esc to interrupt" in line for line in lines))

    def test_sync_chat_composer_from_buffer_updates_bottom_pane_composer(self):
        widget = ChatWidget(width=80)
        widget.set_bottom_pane(BottomPane())

        cli_chat_rich._sync_chat_composer_from_buffer(widget, "follow up")

        self.assertEqual(widget.bottom_pane.composer.text, "follow up")
        lines = cli_chat_rich._chat_widget_frame_lines(widget, width=80, height=12)
        self.assertTrue(any("follow up" in line for line in lines))

    def test_running_composer_callbacks_enqueue_and_update_pending_preview(self):
        widget = ChatWidget(width=80)
        widget.set_bottom_pane(BottomPane())
        queue = MessageQueue()

        composer = cli_chat_rich._attach_running_composer_callbacks(widget, queue)
        composer.set_text("queued follow-up")
        composer.submit()

        self.assertEqual(queue.drain_all(), ["queued follow-up"])
        lines = cli_chat_rich._chat_widget_frame_lines(widget, width=80, height=12)
        self.assertTrue(any("Messages to be submitted after next tool call" in line for line in lines))
        self.assertTrue(any("↳ queued follow-up" in line for line in lines))
        self.assertEqual(composer.history.local[-1]["text"], "queued follow-up")

    def test_clear_running_preview_preserves_real_message_queue(self):
        widget = ChatWidget(width=80)
        widget.set_bottom_pane(BottomPane())
        queue = MessageQueue()
        running_state = {"pending_count": 1, "preview_items": ["queued follow-up"]}

        composer = cli_chat_rich._attach_running_composer_callbacks(widget, queue)
        composer.set_text("queued follow-up")
        composer.submit()
        cli_chat_rich._clear_running_preview(widget, running_state)

        self.assertEqual(queue.pending_count, 1)
        self.assertEqual(queue.drain_all(), ["queued follow-up"])
        self.assertEqual(running_state["pending_count"], 0)
        self.assertEqual(running_state["preview_items"], [])
        self.assertTrue(widget.bottom_pane.pending_preview.is_empty())

    def test_queued_task_notice_status_cell_is_yellow(self):
        widget = ChatWidget(width=80)
        widget.set_bottom_pane(BottomPane())

        widget.add_status_cell(cli_chat_rich._QUEUED_TASK_NOTICE)

        lines = cli_chat_rich._chat_widget_frame_lines(widget, width=80, height=12)
        self.assertTrue(any("\033[33mTask is still queued" in line for line in lines))
        self.assertTrue(any(line.rstrip().endswith("\033[0m") for line in lines if "Task is still queued" in line))

    def test_task_terminal_title_uses_user_task_summary(self):
        title = cli_chat_rich._task_terminal_title("Создай файл report_builder.py.\n\nСкрипт должен:")

        self.assertEqual(title, "Igorek · Создай файл report_builder.py.")

    def test_terminal_title_sanitizer_strips_control_sequences(self):
        title = cli_chat_rich._sanitize_terminal_title("hello\x1b]0;bad\x07\nworld")

        self.assertEqual(title, "hello]0;bad world")

    def test_filter_control_chars_strips_screenshot_hotkey_csi(self):
        self.assertEqual(
            cli_chat_rich_shared._filter_control_chars("\x1b[23;5~hello"),
            "hello",
        )
        self.assertEqual(
            cli_chat_rich_shared._filter_control_chars("^[[23;5~hello"),
            "hello",
        )

    def test_ask_approval_fallback_uses_codex_like_overlay(self):
        stream = io.StringIO()
        console = Console(file=stream, force_terminal=False, width=100)

        with (
            patch.object(cli_chat_rich, "PromptSession", None),
            patch.object(cli_chat_rich, "KeyBindings", None),
            patch.object(cli_chat_rich, "HTML", None),
            patch("builtins.input", return_value=""),
        ):
            result = cli_chat_rich._ask_approval(
                console,
                "Bash",
                "echo hello",
                reason="Need to inspect the workspace",
            )

        output = stream.getvalue()
        self.assertEqual(result, "allow")
        self.assertIn("Would you like to run the following command?", output)
        self.assertIn("Reason:", output)
        self.assertIn("Need to inspect the workspace", output)
        self.assertIn("$ echo hello", output)
        self.assertIn("1. Yes, proceed (y)", output)
        self.assertIn("Press enter to confirm or esc to cancel", output)
        self.assertNotIn("Подтверждение", output)

    def test_ask_approval_clears_only_its_own_prompt_after_decision(self):
        clear_counts = []

        class FakePromptSession:
            init_kwargs = {}
            rendered_text = ""

            def __init__(self, **kwargs):
                type(self).init_kwargs = kwargs
                self.kwargs = kwargs

            def prompt(self, message, **kwargs):
                self.message = message
                self.prompt_kwargs = kwargs
                type(self).rendered_text = message()
                return "allow"

        stream = io.StringIO()
        console = Console(file=stream, force_terminal=False, width=100)
        terminal = io.StringIO()

        with (
            patch.object(cli_chat_approval, "PromptSession", FakePromptSession),
            patch.object(cli_chat_approval, "KeyBindings", cli_chat_rich.KeyBindings),
            patch.object(cli_chat_approval, "HTML", cli_chat_rich.HTML),
            patch.object(cli_chat_approval, "ANSI", lambda text: text),
            patch.object(cli_chat_approval.sys, "stdout", terminal),
            patch.object(
                cli_chat_approval,
                "_clear_recent_terminal_lines",
                lambda count: clear_counts.append(count),
            ),
        ):
            result = cli_chat_approval._ask_approval(console, "Bash", "echo hello")

        self.assertEqual(result, "allow")
        self.assertFalse(FakePromptSession.init_kwargs["erase_when_done"])
        self.assertEqual(clear_counts, [FakePromptSession.rendered_text.count("\n") + 1])

    def test_ask_approval_ctrl_o_does_not_reject(self):
        calls = {"exit": [], "invalidate": 0}

        class FakeApp:
            def exit(self, result=None):
                calls["exit"].append(result)

            def invalidate(self):
                calls["invalidate"] += 1

        class FakePromptSession:
            init_kwargs = {}

            def __init__(self, **kwargs):
                type(self).init_kwargs = kwargs

            def prompt(self, message, **kwargs):
                key_bindings = type(self).init_kwargs["key_bindings"]
                for binding in key_bindings.bindings:
                    if tuple(getattr(key, "value", str(key)) for key in binding.keys) == ("c-o",):
                        binding.handler(type("Event", (), {"app": FakeApp()})())
                        break
                return "allow"

        stream = io.StringIO()
        console = Console(file=stream, force_terminal=False, width=100)

        with (
            patch.object(cli_chat_approval, "PromptSession", FakePromptSession),
            patch.object(cli_chat_approval, "KeyBindings", cli_chat_rich.KeyBindings),
            patch.object(cli_chat_approval, "HTML", cli_chat_rich.HTML),
            patch.object(cli_chat_approval, "ANSI", lambda text: text),
            patch.object(cli_chat_approval, "_clear_recent_terminal_lines", lambda count: None),
        ):
            result = cli_chat_approval._ask_approval(console, "Bash", "echo hello")

        self.assertEqual(result, "allow")
        self.assertEqual(calls["exit"], [])
        self.assertEqual(calls["invalidate"], 1)

    def test_clear_recent_terminal_lines_keeps_clear_bounded(self):
        terminal = io.StringIO()
        with patch.object(cli_chat_approval.sys, "stdout", terminal):
            cli_chat_approval._clear_recent_terminal_lines(3)

        output = terminal.getvalue()
        self.assertIn("\x1b[3A", output)
        self.assertEqual(output.count("\x1b[2K"), 3)
        self.assertNotIn("\x1b[J", output)
        self.assertNotIn("\x1b[2J", output)

    def test_structured_approval_request_keeps_payload_for_diff_details(self):
        request = cli_chat_approval._approval_request({
            "tool": "Edit",
            "target": "/tmp/demo.txt",
            "signature": "Edit:sig",
            "source": "model_tool_use",
            "reason": "Update the report.",
            "input": {
                "file_path": "/tmp/demo.txt",
                "old_string": "before",
                "new_string": "after",
            },
        })

        self.assertEqual(request.tool_name, "Edit")
        self.assertEqual(request.target, "/tmp/demo.txt")
        self.assertEqual(request.signature, "Edit:sig")
        self.assertEqual(request.source, "model_tool_use")
        self.assertEqual(request.reasoning, "Update the report.")
        self.assertEqual(request.tool_input["old_string"], "before")

    def test_blocked_tool_session_rule_falls_back_to_file_scope(self):
        rule = cli_chat_approval._blocked_tool_session_rule({
            "tool": "Write",
            "signature": "Write:sig",
            "input": {"file_path": "/tmp/demo.txt", "content": "first"},
        })

        self.assertEqual(rule, {
            "rule_type": "file_path",
            "tool": "Write",
            "file_path": "/tmp/demo.txt",
        })

    def test_prompt_session_accepts_erase_when_done_in_constructor_not_prompt(self):
        from prompt_toolkit import PromptSession

        self.assertIn("erase_when_done", inspect.signature(PromptSession).parameters)
        self.assertNotIn("erase_when_done", inspect.signature(PromptSession.prompt).parameters)

    def test_approval_history_line_matches_codex_like_shape(self):
        self.assertEqual(
            cli_chat_approval._approval_history_line("Bash", "echo hello", "allow"),
            "✔ You approved Igorek to run echo hello this time",
        )
        self.assertEqual(
            cli_chat_approval._approval_history_line("Write", "/tmp/demo.txt", "allow_session"),
            "✔ You approved Igorek to make edits to /tmp/demo.txt every time this session",
        )
        self.assertEqual(
            cli_chat_approval._approval_history_line("Bash", "echo hello", "reject"),
            "✗ You rejected Igorek's request to run echo hello",
        )

    def test_approval_history_line_handles_empty_target(self):
        self.assertEqual(
            cli_chat_approval._approval_history_line("Write", "", "allow"),
            "✔ You approved this request this time",
        )
        self.assertEqual(
            cli_chat_approval._approval_history_line("Bash", "", "allow_session"),
            "✔ You approved this request every time this session",
        )
        self.assertEqual(
            cli_chat_approval._approval_history_line("Write", "", "reject"),
            "✗ You rejected this request",
        )

    def test_print_approval_history_adds_blank_line_before_next_tool_cell(self):
        stream = io.StringIO()
        console = Console(file=stream, force_terminal=False, width=100)

        cli_chat_approval._print_approval_history(console, "Write", "/tmp/demo.txt", "allow")

        self.assertEqual(
            stream.getvalue(),
            "  ✔ You approved Igorek to make edits to /tmp/demo.txt this time\n\n",
        )

    def test_prompt_buffer_key_route_uses_composer_history(self):
        class FakeBuffer:
            def __init__(self):
                self.text = ""
                self.cursor_position = 0

        widget = ChatWidget(width=80)
        widget.set_bottom_pane(BottomPane())
        composer = widget.bottom_pane.composer
        composer.history.add_local({"text": "previous message"})
        buffer = FakeBuffer()

        cli_chat_rich._route_prompt_buffer_key_to_composer(buffer, composer, "up")

        self.assertEqual(buffer.text, "previous message")
        self.assertEqual(buffer.cursor_position, len("previous message"))

    def test_ctrl_j_routes_through_composer(self):
        class FakeBuffer:
            def __init__(self):
                self.text = "hello"
                self.cursor_position = 5

        widget = ChatWidget(width=80)
        widget.set_bottom_pane(BottomPane())
        composer = widget.bottom_pane.composer

        buffer = FakeBuffer()
        cli_chat_rich._route_prompt_buffer_key_to_composer(buffer, composer, "c-j")

        self.assertIn("\n", buffer.text)
        self.assertEqual(composer.text, "hello\n")

    def test_shift_enter_routes_through_composer(self):
        class FakeBuffer:
            def __init__(self):
                self.text = "line1"
                self.cursor_position = 5

        widget = ChatWidget(width=80)
        widget.set_bottom_pane(BottomPane())
        composer = widget.bottom_pane.composer

        buffer = FakeBuffer()
        cli_chat_rich._route_prompt_buffer_key_to_composer(buffer, composer, "shift-enter")

        self.assertIn("\n", buffer.text)
        self.assertEqual(composer.text, "line1\n")

    def test_up_mid_multiline_does_not_recall_history(self):
        class FakeBuffer:
            def __init__(self):
                self.text = "line1\nline2\nline3"
                self.cursor_position = 10

        widget = ChatWidget(width=80)
        widget.set_bottom_pane(BottomPane())
        composer = widget.bottom_pane.composer
        composer.history.add_local({"text": "old message"})

        buffer = FakeBuffer()
        # Cursor is in middle of text, should NOT recall history
        can_nav = composer.history.can_navigate(buffer.text, buffer.cursor_position)
        self.assertFalse(can_nav)

    def test_composer_callback_propagates_to_queue(self):
        widget = ChatWidget(width=80)
        widget.set_bottom_pane(BottomPane())
        mq = MessageQueue()
        composer = cli_chat_rich._attach_running_composer_callbacks(widget, mq)

        composer.set_text("follow-up question")
        composer.submit()

        self.assertEqual(mq.drain_all(), ["follow-up question"])

    # --- Trace rendering completeness tests (points 1-4) ---

    def test_default_interval_is_half_second(self):
        """Point 4: interval_s default should be 0.5, not 2.0."""
        import inspect
        sig = inspect.signature(cli_chat_rich.run_rich_chat)
        self.assertEqual(sig.parameters["interval_s"].default, 0.5)

    def test_task_finish_applied_flag_in_reducer(self):
        """Point 1: reducer tracks whether TASK_FINISH was applied."""
        reducer = TraceEventReducer()
        self.assertFalse(reducer._task_finish_applied)

        reducer.apply({"kind": CcTraceEvent.MODEL_TEXT_DELTA, "text": "Hello"})
        self.assertFalse(reducer._task_finish_applied)

        reducer.apply({"kind": CcTraceEvent.TASK_FINISH, "status": "ok", "output_preview": "done"})
        self.assertTrue(reducer._task_finish_applied)


    def test_trace_fallback_not_needed_after_non_streaming_task_finish(self):
        running_state = {
            "app_was_used": True,
            "task_finish_applied": True,
            "had_streaming_output": False,
        }

        self.assertFalse(cli_chat_active._should_fallback_render_trace(running_state))

    def test_trace_fallback_needed_when_task_finish_missing(self):
        running_state = {
            "app_was_used": True,
            "task_finish_applied": False,
            "had_streaming_output": True,
        }

        self.assertTrue(cli_chat_active._should_fallback_render_trace(running_state))

    def test_app_transcript_printed_only_after_applied_task_finish(self):
        rendered = {
            "app_was_used": True,
            "task_finish_applied": True,
            "final_trace_lines": ["tool", "answer"],
        }
        missing_finish = {
            "app_was_used": True,
            "task_finish_applied": False,
            "final_trace_lines": ["partial"],
        }
        no_lines = {
            "app_was_used": True,
            "task_finish_applied": True,
            "final_trace_lines": [],
        }
        inline_scrollback = {
            "app_was_used": True,
            "task_finish_applied": True,
            "final_trace_lines": ["already permanent"],
            "inline_scrollback_used": True,
        }
        deferred_scrollback = {
            "app_was_used": True,
            "task_finish_applied": True,
            "final_trace_lines": ["kept mutable"],
            "has_deferred_scrollback": True,
        }

        self.assertTrue(cli_chat_active._should_print_app_transcript(rendered))
        self.assertFalse(cli_chat_active._should_print_app_transcript(missing_finish))
        self.assertFalse(cli_chat_active._should_print_app_transcript(no_lines))
        self.assertFalse(cli_chat_active._should_print_app_transcript(inline_scrollback))
        self.assertFalse(cli_chat_active._should_print_app_transcript(deferred_scrollback))

    def test_inline_scrollback_projector_uses_history_not_bottom_pane(self):
        widget = ChatWidget()
        pane = BottomPane()
        pane.set_status(StatusIndicatorWidget())
        pane.update_task_running(True)
        widget.set_bottom_pane(pane)
        widget.committed_cells.append(MarkdownCell("stable answer", marker=True))

        projector = cli_chat_active._InlineScrollbackProjector()
        lines = projector.take_new_lines(widget, 80)
        text = "\n".join(lines)

        self.assertIn("stable answer", text)
        self.assertNotIn("Working", text)
        self.assertNotIn("Enter", text)
        self.assertNotIn("Ctrl+J", text)

    def test_inline_scrollback_projector_keeps_active_cell_in_viewport(self):
        widget = ChatWidget()
        widget.committed_cells.append(MarkdownCell("already committed", marker=True))
        widget.active_cell = MarkdownCell("mutable live tail")
        projector = cli_chat_active._InlineScrollbackProjector()

        inserted = projector.take_new_lines(widget, 80)
        viewport = projector.viewport_lines(widget, 80)

        self.assertTrue(any("already committed" in line for line in inserted))
        self.assertFalse(any("mutable live tail" in line for line in inserted))
        self.assertTrue(any("mutable live tail" in line for line in viewport))
        self.assertFalse(any("already committed" in line for line in viewport))

    def test_inline_scrollback_projector_expanded_reasoning_is_separate(self):
        from factory.core.tui.cells.history_cell import ReasoningCell

        widget = ChatWidget()
        widget.committed_cells.extend(
            [
                TextCell("stable"),
                ReasoningCell("private thought"),
                MarkdownCell("visible answer"),
            ]
        )
        projector = cli_chat_active._InlineScrollbackProjector()

        collapsed = projector.deferred_lines(widget, 80)
        expanded = projector.deferred_lines(widget, 80, expand_reasoning=True)
        reasoning = projector.expanded_reasoning_lines(widget, 80)

        self.assertTrue(any("Ctrl+O раскрыть" in line for line in collapsed))
        self.assertTrue(any("private thought" in line for line in expanded))
        self.assertTrue(any("visible answer" in line for line in expanded))
        self.assertTrue(any("private thought" in line for line in reasoning))
        self.assertFalse(any("visible answer" in line for line in reasoning))




    def test_reasoning_visibility_toggle_is_reversible(self):
        running_state = {}

        self.assertTrue(cli_chat_active._toggle_reasoning_visibility(running_state))
        self.assertTrue(running_state["show_reasoning"])
        self.assertFalse(cli_chat_active._toggle_reasoning_visibility(running_state))
        self.assertFalse(running_state["show_reasoning"])

    def test_poll_grace_period_reads_late_events(self):
        """Point 2: grace period catches events written after initial read."""
        import json
        import threading

        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            f.write(json.dumps({"kind": CcTraceEvent.MODEL_TEXT_DELTA, "text": "round 1"}) + "\n")
            f.flush()
            trace_path = Path(f.name)

        trace_queue = queue.Queue()
        done_event = threading.Event()

        def write_late_event():
            # Simulate worker writing TASK_FINISH after a short delay
            time.sleep(0.2)
            with open(trace_path, "a") as tf:
                tf.write(json.dumps({"kind": CcTraceEvent.TASK_FINISH, "status": "ok"}) + "\n")

        try:
            writer = threading.Thread(target=write_late_event, daemon=True)
            writer.start()

            seen = 0
            grace_deadline = time.monotonic() + 1.0
            while time.monotonic() < grace_deadline:
                evs, seen = cli_chat_active._read_new_trace_events(trace_path, seen)
                if evs:
                    trace_queue.put(evs)
                    grace_deadline = time.monotonic() + 1.0
                else:
                    time.sleep(0.05)
            done_event.set()

            writer.join(timeout=2)

            # Should have gotten both events
            all_events = []
            while not trace_queue.empty():
                all_events.extend(trace_queue.get_nowait())
            kinds = [e["kind"] for e in all_events]
            self.assertIn(CcTraceEvent.MODEL_TEXT_DELTA, kinds)
            self.assertIn(CcTraceEvent.TASK_FINISH, kinds)
        finally:
            trace_path.unlink(missing_ok=True)

    def test_read_new_trace_events_reads_from_file(self):
        """_read_new_trace_events correctly reads and parses JSONL trace file."""
        import json

        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            f.write(json.dumps({"kind": CcTraceEvent.MODEL_TEXT_DELTA, "text": "Hello"}) + "\n")
            f.write(json.dumps({"kind": CcTraceEvent.MODEL_ROUND_RESPONSE, "text_preview": "Hello world"}) + "\n")
            f.write(json.dumps({"kind": CcTraceEvent.TASK_FINISH, "status": "ok", "output_preview": "Done!"}) + "\n")
            f.flush()
            trace_path = Path(f.name)

        try:
            # Simulate: queue is empty but trace file has events
            reducer = TraceEventReducer()
            direct_evs, _ = cli_chat_active._read_new_trace_events(trace_path, 0)
            reducer.apply_many(direct_evs)

            self.assertTrue(reducer._task_finish_applied)
            lines = reducer.display_lines(120)
            combined = "\n".join(lines)
            self.assertIn("Hello", combined)
        finally:
            trace_path.unlink(missing_ok=True)

    def test_read_new_trace_events_keeps_partial_last_json_line_unseen(self):
        import json

        with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
            f.write(json.dumps({"kind": CcTraceEvent.MODEL_TEXT_DELTA, "text": "Hello"}) + "\n")
            f.write('{"kind": "task.finish", "status"')
            f.flush()
            trace_path = Path(f.name)

        try:
            evs, seen = cli_chat_active._read_new_trace_events(trace_path, 0)
            self.assertEqual(len(evs), 1)
            self.assertEqual(seen, 1)

            with trace_path.open("a", encoding="utf-8") as f:
                f.write(': "ok", "output_preview": "Done"}\n')

            evs, seen = cli_chat_active._read_new_trace_events(trace_path, seen)
            self.assertEqual(seen, 2)
            self.assertEqual(evs, [{
                "kind": CcTraceEvent.TASK_FINISH,
                "status": "ok",
                "output_preview": "Done",
            }])
        finally:
            trace_path.unlink(missing_ok=True)

    def test_extract_rejection_context_returns_assistant_texts(self):
        """Rejection context extracts assistant text parts from approval_context_messages."""
        approval_blocked = [{
            "tool": "Bash",
            "approval_context_messages": [
                {"role": "assistant", "content": [
                    {"type": "text", "text": "Модель загружена на порту 8094."},
                    {"type": "tool_use", "id": "call_1", "name": "Bash", "input": {"command": "ls"}},
                ]},
                {"role": "user", "content": [
                    {"type": "tool_result", "tool_use_id": "call_1", "content": "file.txt"},
                ]},
                {"role": "assistant", "content": [
                    {"type": "text", "text": "Давай глянем breakdown."},
                ]},
            ],
        }]
        result = cli_chat_rich._extract_rejection_context(approval_blocked)
        self.assertIn("Модель загружена на порту 8094.", result)
        self.assertIn("Давай глянем breakdown.", result)
        # tool_use and tool_result should NOT appear
        self.assertNotIn("ls", result)
        self.assertNotIn("file.txt", result)

    def test_extract_rejection_context_includes_current_assistant_text(self):
        """Rejected approval preserves the current pre-approval answer."""
        approval_blocked = [{
            "tool": "Bash",
            "assistant_text": "Полный текущий разбор перед запросом Bash approval.",
            "approval_context_messages": [
                {"role": "assistant", "content": [
                    {"type": "text", "text": "Старый короткий preamble."},
                ]},
            ],
        }]
        result = cli_chat_rich._extract_rejection_context(approval_blocked)
        self.assertIn("Старый короткий preamble.", result)
        self.assertIn("Полный текущий разбор перед запросом Bash approval.", result)

    def test_extract_rejection_context_empty_on_no_messages(self):
        """Returns empty string when no approval_context_messages."""
        approval_blocked = [{"tool": "Bash"}]
        result = cli_chat_rich._extract_rejection_context(approval_blocked)
        self.assertEqual(result, "")

    def test_extract_rejection_context_truncates_long_output(self):
        """Truncates context to max_chars to avoid bloating session."""
        long_text = "x" * 5000
        approval_blocked = [{
            "tool": "Bash",
            "approval_context_messages": [
                {"role": "assistant", "content": long_text},
            ],
        }]
        result = cli_chat_rich._extract_rejection_context(approval_blocked, max_chars=1000)
        self.assertEqual(len(result), 1000)
        self.assertTrue(result.startswith("x"))

    def test_extract_rejection_context_string_content(self):
        """Handles content as plain string (not list of parts)."""
        approval_blocked = [{
            "tool": "Bash",
            "approval_context_messages": [
                {"role": "assistant", "content": "Запустил сервер на :8096."},
            ],
        }]
        result = cli_chat_rich._extract_rejection_context(approval_blocked)
        self.assertEqual(result, "Запустил сервер на :8096.")

    def test_terminal_title_setup_saves_selected_items_in_order(self):
        class FakeStatusLine:
            def __init__(self, settings_path):
                self.settings_path = settings_path
                self.last_input = {
                    "session_id": "session-1",
                    "version": "0.1.test",
                    "cwd": "/tmp/project",
                    "workspace": {"current_dir": "/tmp/project", "project_dir": "/tmp/project"},
                    "model": {"display_name": "gemma"},
                    "git": {"branch": "main", "is_clean": True},
                    "context_window": {},
                }

            def refresh(self):
                return None

        with tempfile.TemporaryDirectory() as tmp:
            stream = io.StringIO()
            console = Console(file=stream, force_terminal=False, width=100)
            sl = FakeStatusLine(Path(tmp) / "settings.json")

            updated = cli_chat_rich._terminal_title_setup(console, sl, input_fn=lambda: "10,2,1")
            config = cli_chat_terminal.load_terminal_title_config(sl.settings_path)

        self.assertTrue(updated)
        self.assertEqual(config.items, ("model", "project-name", "app-name"))
        self.assertIn("Terminal title updated.", stream.getvalue())
        self.assertIn("gemma | project | Igorek", stream.getvalue())

    def test_terminal_title_setup_reset_restores_legacy_default(self):
        class FakeStatusLine:
            def __init__(self, settings_path):
                self.settings_path = settings_path
                self.last_input = {}

            def refresh(self):
                return None

        with tempfile.TemporaryDirectory() as tmp:
            stream = io.StringIO()
            console = Console(file=stream, force_terminal=False, width=100)
            settings = Path(tmp) / "settings.json"
            cli_chat_terminal.save_terminal_title_items(["model"], settings_path=settings)
            sl = FakeStatusLine(settings)

            updated = cli_chat_rich._terminal_title_setup(console, sl, input_fn=lambda: "reset")
            config = cli_chat_terminal.load_terminal_title_config(settings)

        self.assertTrue(updated)
        self.assertEqual(config.items, ())
        self.assertIn("legacy task title default", stream.getvalue())

    def test_theme_setup_saves_selected_bundled_theme(self):
        class FakeStatusLine:
            def __init__(self, settings_path):
                self.settings_path = settings_path

        with tempfile.TemporaryDirectory() as tmp:
            stream = io.StringIO()
            console = Console(file=stream, force_terminal=False, width=100)
            sl = FakeStatusLine(Path(tmp) / "settings.json")

            updated = cli_chat_rich._theme_setup(console, sl, input_fn=lambda: "2")
            theme = cli_chat_rich.load_theme_name(sl.settings_path)

        self.assertTrue(updated)
        self.assertEqual(theme, "light")
        self.assertIn("Theme updated:", stream.getvalue())
        self.assertIn("preview.py", stream.getvalue())

    def test_theme_setup_reset_restores_dark_default(self):
        class FakeStatusLine:
            def __init__(self, settings_path):
                self.settings_path = settings_path

        with tempfile.TemporaryDirectory() as tmp:
            stream = io.StringIO()
            console = Console(file=stream, force_terminal=False, width=100)
            settings = Path(tmp) / "settings.json"
            cli_chat_rich.save_theme_name("light", settings_path=settings)
            sl = FakeStatusLine(settings)

            updated = cli_chat_rich._theme_setup(console, sl, input_fn=lambda: "reset")
            theme = cli_chat_rich.load_theme_name(settings)

        self.assertTrue(updated)
        self.assertEqual(theme, "dark")
        self.assertIn("Theme reset to dark.", stream.getvalue())

    def test_skills_manage_toggles_and_saves_selected_entries(self):
        stream = io.StringIO()
        console = Console(file=stream, force_terminal=False, width=100)
        payload = {
            "status": "ok",
            "skills_root": "/tmp/skills",
            "count": 2,
            "skills": [
                {"skill_id": "alpha", "name": "alpha", "description": "First", "enabled": True},
                {"skill_id": "beta", "name": "beta", "description": "Second", "enabled": False},
            ],
        }
        calls = []
        inputs = iter(["1", ""])
        with (
            patch("factory.core.cli_skills.list_skills", return_value=payload),
            patch("factory.core.cli_skills.set_skill_enabled", side_effect=lambda skill_id, *, enabled, data_dir=None, path=None: calls.append((skill_id, enabled))),
        ):
            updated = cli_chat_rich._skills_manage(console, input_fn=lambda: next(inputs))

        self.assertTrue(updated)
        self.assertEqual(calls, [("alpha", False)])
        self.assertIn("Skills updated.", stream.getvalue())
        self.assertIn("Skills management saved.", stream.getvalue())






    def test_model_recommendation_prompt_accepts_catalog_default(self):
        stream = io.StringIO()
        console = Console(file=stream, force_terminal=False, width=100)

        selected = cli_chat_rich._model_recommendation_prompt(
            console, current_model="deepseek-v4-flash", provider="opencode-go", api_format="auto", input_fn=lambda: "1",
        )

        self.assertEqual(selected, "deepseek-v4-pro")
        self.assertIn("Try recommended model (deepseek-v4-pro)", stream.getvalue())

    def test_model_recommendation_prompt_keeps_existing_model_on_reject(self):
        stream = io.StringIO()
        console = Console(file=stream, force_terminal=False, width=100)

        selected = cli_chat_rich._model_recommendation_prompt(
            console, current_model="deepseek-v4-flash", provider="opencode-go", api_format="auto", input_fn=lambda: "2",
        )

        self.assertEqual(selected, "deepseek-v4-flash")
        self.assertIn("Keeping model for this session", stream.getvalue())

    def test_model_recommendation_prompt_skips_when_catalog_default_is_active(self):
        stream = io.StringIO()
        console = Console(file=stream, force_terminal=False, width=100)

        selected = cli_chat_rich._model_recommendation_prompt(
            console, current_model="deepseek-v4-pro", provider="opencode-go", api_format="auto", input_fn=lambda: "1",
        )

        self.assertIsNone(selected)
        self.assertIn("No newer recommended model", stream.getvalue())

    def test_apply_session_model_uses_override_only_away_from_profile_default(self):
        class FakeStatusLine:
            def __init__(self):
                self.model = "deepseek-v4-flash"
                self.refresh_count = 0

            def refresh(self):
                self.refresh_count += 1

        sl = FakeStatusLine()
        override = cli_chat_rich._apply_session_model(
            sl, profile_model="deepseek-v4-flash", selected_model="deepseek-v4-pro",
        )
        self.assertEqual(override, "deepseek-v4-pro")
        self.assertEqual(sl.model, "deepseek-v4-pro")

        override = cli_chat_rich._apply_session_model(
            sl, profile_model="deepseek-v4-flash", selected_model="deepseek-v4-flash",
        )
        self.assertIsNone(override)
        self.assertEqual(sl.model, "deepseek-v4-flash")
        self.assertEqual(sl.refresh_count, 2)

    def test_active_turn_interrupt_drains_queued_followups(self):
        stream = io.StringIO()
        console = Console(file=stream, force_terminal=False, width=80)
        mq = MessageQueue()
        mq.enqueue("follow-up")
        pending_inputs = []

        cli_chat_rich._handle_active_turn_interrupt(console, mq, pending_inputs)

        self.assertEqual(pending_inputs, ["follow-up"])
        self.assertFalse(mq.has_pending)





    def test_clipboard_image_wl_uses_wl_paste_and_writes_temp_file(self):
        fake_png = b"\x89PNG\r\n\x1a\n" + b"fake-png-bytes"
        list_proc = Mock(returncode=0, stdout="image/png\ntext/plain\n")

        def _run_side_effect(*args, **kwargs):
            cmd = args[0] if args else kwargs.get("args", [])
            if "--list-types" in cmd:
                return list_proc
            # Otherwise, simulate wl-paste writing image bytes to the
            # provided stdout file.
            out = kwargs.get("stdout")
            if out is not None:
                out.write(fake_png)
                out.flush()
            return Mock(returncode=0)

        with patch.object(
            cli_chat_rich.subprocess, "run",
            side_effect=_run_side_effect,
        ) as _run, tempfile.TemporaryDirectory():
            with patch.dict(os.environ, {"WAYLAND_DISPLAY": "wayland-0"}, clear=False):
                path = cli_chat_rich._clipboard_image_wl()
                self.assertIsNotNone(path)
                with open(path, "rb") as f:
                    self.assertEqual(f.read(), fake_png)
                Path(path).unlink(missing_ok=True)
        # First call: list types
        self.assertEqual(_run.call_args_list[0].args[0][:1], ["wl-paste"])
        # Second call: read the image
        self.assertEqual(_run.call_args_list[1].args[0][:3], ["wl-paste", "--type", "image/png"])

    def test_clipboard_image_wl_returns_none_when_no_image_type(self):
        list_proc = unittest.mock.Mock(returncode=0, stdout="text/plain\nUTF8_STRING\n")
        with patch.object(
            cli_chat_rich.subprocess, "run", return_value=list_proc,
        ):
            with patch.dict(os.environ, {"WAYLAND_DISPLAY": "wayland-0"}, clear=False):
                self.assertIsNone(cli_chat_rich._clipboard_image_wl())

    def test_clipboard_image_wl_returns_none_when_wl_paste_missing(self):
        err = FileNotFoundError("no such file")
        with patch.object(
            cli_chat_rich.subprocess, "run", side_effect=err,
        ):
            with patch.dict(os.environ, {"WAYLAND_DISPLAY": "wayland-0"}, clear=False):
                self.assertIsNone(cli_chat_rich._clipboard_image_wl())

    def test_clipboard_image_x11_writes_temp_file(self):
        paste_proc = Mock(returncode=0)
        version_proc = Mock(returncode=0)

        def _run_side_effect(*args, **kwargs):
            cmd = args[0] if args else kwargs.get("args", [])
            if "version" in cmd:
                return version_proc
            out = kwargs.get("stdout")
            if out is not None:
                out.write(b"x11-fake-png")
                out.flush()
            return paste_proc

        with patch.object(
            cli_chat_rich.subprocess, "run",
            side_effect=_run_side_effect,
        ) as _run, tempfile.TemporaryDirectory():
            with patch.dict(os.environ, {"DISPLAY": ":0"}, clear=False):
                path = cli_chat_rich._clipboard_image_x11()
                self.assertIsNotNone(path)
                self.assertTrue(os.path.getsize(path) > 0)
                Path(path).unlink(missing_ok=True)
        self.assertEqual(_run.call_args_list[1].args[0][:5],
                         ["xclip", "-selection", "clipboard", "-t", "image/png"])

    def test_clipboard_image_x11_returns_none_when_xclip_missing(self):
        err = FileNotFoundError("no such file")
        with patch.object(
            cli_chat_rich.subprocess, "run", side_effect=err,
        ):
            with patch.dict(os.environ, {"DISPLAY": ":0"}, clear=False):
                self.assertIsNone(cli_chat_rich._clipboard_image_x11())

    def test_resolve_image_path_from_text_file_uri(self, ):
        # Create a real image file to point at
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
            f.write(b"\x89PNG\r\n\x1a\nreal-png")
            real_path = f.name
        try:
            uri = f"file://{real_path}"
            resolved = cli_chat_rich._resolve_image_path_from_text(uri)
            self.assertEqual(resolved, real_path)
        finally:
            Path(real_path).unlink(missing_ok=True)

    def test_resolve_image_path_from_text_plain_path(self):
        with tempfile.NamedTemporaryFile(suffix=".jpg", delete=False) as f:
            f.write(b"fake-jpg")
            real_path = f.name
        try:
            resolved = cli_chat_rich._resolve_image_path_from_text(real_path)
            self.assertEqual(resolved, real_path)
        finally:
            Path(real_path).unlink(missing_ok=True)

    def test_resolve_image_path_from_text_rejects_non_image(self):
        with tempfile.NamedTemporaryFile(suffix=".txt", delete=False) as f:
            f.write(b"plain text")
            real_path = f.name
        try:
            self.assertIsNone(
                cli_chat_rich._resolve_image_path_from_text(real_path)
            )
        finally:
            Path(real_path).unlink(missing_ok=True)

    def test_resolve_image_path_from_text_rejects_missing(self):
        self.assertIsNone(
            cli_chat_rich._resolve_image_path_from_text("/no/such/file.png")
        )

    def test_resolve_image_path_from_text_rejects_empty(self):
        self.assertIsNone(cli_chat_rich._resolve_image_path_from_text(""))
        self.assertIsNone(cli_chat_rich._resolve_image_path_from_text("   "))

    def test_clipboard_image_wl_falls_back_to_uri_list(self):
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
            f.write(b"\x89PNG\r\n\x1a\nuri-list-png")
            real_path = f.name
        try:
            uri = f"file://{real_path}"
            list_proc = Mock(
                returncode=0,
                stdout="text/uri-list\ntext/plain\nUTF8_STRING\n",
            )
            uri_proc = Mock(returncode=0, stdout=uri + "\n")
            with patch.object(
                cli_chat_rich.subprocess, "run",
                side_effect=[list_proc, uri_proc],
            ):
                with patch.dict(
                    os.environ, {"WAYLAND_DISPLAY": "wayland-0"}, clear=False,
                ):
                    path = cli_chat_rich._clipboard_image_wl()
                    self.assertEqual(path, real_path)
        finally:
            Path(real_path).unlink(missing_ok=True)

    def test_clipboard_image_wl_falls_back_to_text_plain_path(self):
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
            f.write(b"\x89PNG\r\n\x1a\nplain-png")
            real_path = f.name
        try:
            list_proc = Mock(
                returncode=0,
                stdout="text/plain;charset=utf-8\nUTF8_STRING\n",
            )
            plain_proc = Mock(returncode=0, stdout=real_path + "\n")
            with patch.object(
                cli_chat_rich.subprocess, "run",
                side_effect=[list_proc, plain_proc],
            ):
                with patch.dict(
                    os.environ, {"WAYLAND_DISPLAY": "wayland-0"}, clear=False,
                ):
                    path = cli_chat_rich._clipboard_image_wl()
                    self.assertEqual(path, real_path)
        finally:
            Path(real_path).unlink(missing_ok=True)

    def test_clipboard_image_wl_returns_none_when_no_image_or_path(self):
        list_proc = Mock(
            returncode=0,
            stdout="text/plain;charset=utf-8\nUTF8_STRING\n",
        )
        plain_proc = Mock(returncode=0, stdout="just some text\n")
        with patch.object(
            cli_chat_rich.subprocess, "run",
            side_effect=[list_proc, plain_proc],
        ):
            with patch.dict(
                os.environ, {"WAYLAND_DISPLAY": "wayland-0"}, clear=False,
            ):
                self.assertIsNone(cli_chat_rich._clipboard_image_wl())

    def test_clipboard_image_x11_falls_back_to_uri_list(self):
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
            f.write(b"\x89PNG\r\n\x1a\nx11-uri-png")
            real_path = f.name
        try:
            uri = f"file://{real_path}"
            version_proc = Mock(returncode=0)
            png_proc = Mock(returncode=1)  # no image bytes
            uri_proc = Mock(returncode=0, stdout=uri + "\n")
            with patch.object(
                cli_chat_rich.subprocess, "run",
                side_effect=[version_proc, png_proc, uri_proc],
            ):
                with patch.dict(os.environ, {"DISPLAY": ":0"}, clear=False):
                    path = cli_chat_rich._clipboard_image_x11()
                    self.assertEqual(path, real_path)
        finally:
            Path(real_path).unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
