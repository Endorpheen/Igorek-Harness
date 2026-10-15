"""Tests for request_user_input form (data classes, form logic, handler).

Covers:
- Data classes (UserInputOption, UserInputQuestion, UserInputRequest, etc.)
- RequestUserInputForm construction, navigation, submit, cancel
- Select/multiselect toggle via select_option and handle_key
- Text/secret value management
- Render output formatting
- _handle_request_user_input handler (argument parsing, serialization, error handling)
"""
import json
import unittest
from unittest.mock import MagicMock, patch


# ---------------------------------------------------------------------------
# Tests for data classes
# ---------------------------------------------------------------------------

class TestUserInputDataClasses(unittest.TestCase):
    """UserInputOption, UserInputQuestion, UserInputRequest, etc."""

    def test_option_defaults(self):
        from factory.core.tui.bottom_pane.request_user_input_form import (
            UserInputOption,
        )
        opt = UserInputOption(label="Yes")
        self.assertEqual(opt.label, "Yes")
        self.assertEqual(opt.description, "")

    def test_option_with_description(self):
        from factory.core.tui.bottom_pane.request_user_input_form import (
            UserInputOption,
        )
        opt = UserInputOption(label="No", description="Negative choice")
        self.assertEqual(opt.label, "No")
        self.assertEqual(opt.description, "Negative choice")

    def test_question_text_type(self):
        from factory.core.tui.bottom_pane.request_user_input_form import (
            UserInputQuestion,
        )
        q = UserInputQuestion(id="name", header="Name", question="What is your name?")
        self.assertEqual(q.field_type, "text")

    def test_question_secret_type(self):
        from factory.core.tui.bottom_pane.request_user_input_form import (
            UserInputQuestion,
        )
        q = UserInputQuestion(id="pwd", header="Pass", question="Enter password", is_secret=True)
        self.assertEqual(q.field_type, "secret")

    def test_question_select_type(self):
        from factory.core.tui.bottom_pane.request_user_input_form import (
            UserInputOption, UserInputQuestion,
        )
        opts = [UserInputOption(label="A"), UserInputOption(label="B")]
        q = UserInputQuestion(id="choice", header="Pick", question="Pick one", options=opts)
        self.assertEqual(q.field_type, "select")

    def test_question_multiselect_type(self):
        from factory.core.tui.bottom_pane.request_user_input_form import (
            UserInputOption, UserInputQuestion,
        )
        opts = [UserInputOption(label="X"), UserInputOption(label="Y")]
        q = UserInputQuestion(id="multi", header="Multi", question="Pick many",
                              options=opts, is_multiselect=True)
        self.assertEqual(q.field_type, "multiselect")

    def test_request_creation(self):
        from factory.core.tui.bottom_pane.request_user_input_form import (
            UserInputRequest,
        )
        req = UserInputRequest(item_id="test", thread_id="t1", turn_id="turn1")
        self.assertEqual(req.item_id, "test")
        self.assertEqual(req.thread_id, "t1")
        self.assertEqual(req.turn_id, "turn1")
        self.assertEqual(req.questions, [])

    def test_answer_defaults(self):
        from factory.core.tui.bottom_pane.request_user_input_form import (
            UserInputAnswer,
        )
        ans = UserInputAnswer(question_id="q1")
        self.assertEqual(ans.question_id, "q1")
        self.assertEqual(ans.selected_options, [])
        self.assertEqual(ans.text_value, "")
        self.assertEqual(ans.notes, "")

    def test_response_creation(self):
        from factory.core.tui.bottom_pane.request_user_input_form import (
            UserInputResponse, UserInputAnswer,
        )
        ans = UserInputAnswer(question_id="q1", text_value="hello")
        resp = UserInputResponse(answers={"q1": ans})
        self.assertIn("q1", resp.answers)
        self.assertEqual(resp.answers["q1"].text_value, "hello")


# ---------------------------------------------------------------------------
# Tests for RequestUserInputForm
# ---------------------------------------------------------------------------

class TestRequestUserInputForm(unittest.TestCase):
    """Form construction, navigation, submit, cancel, option handling."""

    def setUp(self):
        from factory.core.tui.bottom_pane.request_user_input_form import (
            UserInputOption,
            UserInputQuestion,
            UserInputRequest,
        )
        self.questions = [
            UserInputQuestion(id="name", header="Name", question="Your name?"),
            UserInputQuestion(
                id="color", header="Color", question="Pick a color",
                options=[UserInputOption("Red"), UserInputOption("Blue")],
            ),
            UserInputQuestion(
                id="tools", header="Tools", question="Choose tools",
                options=[UserInputOption("Hammer"), UserInputOption("Saw"), UserInputOption("Drill")],
                is_multiselect=True,
            ),
        ]
        self.request = UserInputRequest(
            item_id="test-form", thread_id="", turn_id="",
            questions=self.questions,
        )

    def _make_form(self, questions=None):
        from factory.core.tui.bottom_pane.request_user_input_form import (
            RequestUserInputForm,
        )
        req = self.request
        if questions is not None:
            from factory.core.tui.bottom_pane.request_user_input_form import (
                UserInputRequest,
            )
            req = UserInputRequest(item_id="test", thread_id="", turn_id="",
                                   questions=questions)
        return RequestUserInputForm(req)

    # -- Construction --

    def test_form_initialization(self):
        form = self._make_form()
        self.assertEqual(len(form.answers), 3)
        self.assertEqual(form.current_field_idx, 0)
        self.assertFalse(form.done)
        self.assertFalse(form.cancelled)
        self.assertIsNotNone(form.current_question)
        self.assertEqual(form.current_question.id, "name")
        self.assertEqual(form._width, 80)

    def test_form_set_width(self):
        form = self._make_form()
        form.set_width(120)
        self.assertEqual(form._width, 120)

    def test_form_no_questions(self):
        form = self._make_form(questions=[])
        self.assertIsNone(form.current_question)
        self.assertIsNone(form.current_answer)

    # -- Navigation --

    def test_next_field(self):
        form = self._make_form()
        self.assertEqual(form.current_question.id, "name")
        form.next_field()
        self.assertEqual(form.current_question.id, "color")
        form.next_field()
        self.assertEqual(form.current_question.id, "tools")
        form.next_field()  # stays at last
        self.assertEqual(form.current_question.id, "tools")

    def test_prev_field(self):
        form = self._make_form()
        form.current_field_idx = 2
        form._update_option_scroll()
        self.assertEqual(form.current_question.id, "tools")
        form.prev_field()
        self.assertEqual(form.current_question.id, "color")
        form.prev_field()
        self.assertEqual(form.current_question.id, "name")
        form.prev_field()  # stays at first
        self.assertEqual(form.current_question.id, "name")

    def test_current_answer_matches_question(self):
        form = self._make_form()
        self.assertEqual(form.current_answer.question_id, "name")
        form.next_field()
        self.assertEqual(form.current_answer.question_id, "color")

    # -- Text value --

    def test_set_text_value(self):
        form = self._make_form()
        form.set_text_value("Alice")
        self.assertEqual(form.current_answer.text_value, "Alice")

    def test_set_text_value_on_wrong_field_no_crash(self):
        form = self._make_form()
        form.current_field_idx = 99  # out of range
        form.set_text_value("x")  # should not crash
        # no assertion — just no exception

    def test_set_notes(self):
        form = self._make_form()
        form.current_field_idx = 1  # select field
        form.set_notes("This is my choice")
        self.assertEqual(form.current_answer.notes, "This is my choice")

    # -- Select option (single) --

    def test_select_single_option(self):
        form = self._make_form()
        form.current_field_idx = 1
        form._update_option_scroll()
        # First option is selected by default
        form.select_option()
        ans = form.current_answer
        self.assertEqual(ans.selected_options, ["Red"])

    def test_select_single_option_changes_selection(self):
        form = self._make_form()
        form.current_field_idx = 1
        form._update_option_scroll()
        form.select_option()  # select Red
        form.option_scroll.move(1)  # move to Blue
        form.select_option()  # select Blue, overwrites Red
        ans = form.current_answer
        self.assertEqual(ans.selected_options, ["Blue"])

    # -- Select option (multiselect) --

    def test_multiselect_toggle_add(self):
        form = self._make_form()
        form.current_field_idx = 2
        form._update_option_scroll()
        form.select_option()  # select Hammer
        ans = form.current_answer
        self.assertEqual(ans.selected_options, ["Hammer"])

    def test_multiselect_toggle_remove(self):
        form = self._make_form()
        form.current_field_idx = 2
        form._update_option_scroll()
        form.select_option()  # add Hammer
        form.select_option()  # remove Hammer
        ans = form.current_answer
        self.assertEqual(ans.selected_options, [])

    def test_multiselect_multiple(self):
        form = self._make_form()
        form.current_field_idx = 2
        form._update_option_scroll()
        form.select_option()  # Hammer
        form.option_scroll.move(1)  # Saw
        form.select_option()  # add Saw
        form.option_scroll.move(1)  # Drill
        form.select_option()  # add Drill
        ans = form.current_answer
        self.assertCountEqual(ans.selected_options, ["Hammer", "Saw", "Drill"])

    def test_select_option_no_question(self):
        form = self._make_form(questions=[])
        form.select_option()  # should not crash

    def test_select_option_no_options(self):
        form = self._make_form()
        form.current_field_idx = 0  # text field, no options
        form.select_option()  # should not crash
        self.assertEqual(form.current_answer.selected_options, [])

    # -- Submit --

    def test_submit_returns_response(self):
        form = self._make_form()
        form.set_text_value("Alice")
        result = form.submit()
        self.assertIsNotNone(result)
        self.assertIn("name", result.answers)
        self.assertEqual(result.answers["name"].text_value, "Alice")
        self.assertTrue(form.done)
        self.assertFalse(form.cancelled)

    def test_submit_sets_done(self):
        form = self._make_form()
        form.submit()
        self.assertTrue(form.done)

    # -- Cancel --

    def test_cancel_sets_cancelled(self):
        form = self._make_form()
        form.cancel()
        self.assertTrue(form.cancelled)
        self.assertTrue(form.done)

    def test_cancel_then_submit_still_cancelled(self):
        form = self._make_form()
        form.cancel()
        result = form.submit()
        # submit returns response, but cancelled is already True
        self.assertIsNotNone(result)
        self.assertTrue(form.cancelled)

    # -- Handle key --

    def test_handle_key_escape_cancels(self):
        form = self._make_form()
        ret = form.handle_key("escape")
        self.assertEqual(ret, "cancel")
        self.assertTrue(form.cancelled)

    def test_handle_key_c_c_cancels(self):
        form = self._make_form()
        ret = form.handle_key("c-c")
        self.assertEqual(ret, "cancel")
        self.assertTrue(form.cancelled)

    def test_handle_key_ctrl_s_submits(self):
        form = self._make_form()
        self.assertEqual(form.handle_key("c-s"), "submit")
        self.assertTrue(form.done)

    def test_handle_key_tab_navigates(self):
        form = self._make_form()
        self.assertEqual(form.current_field_idx, 0)
        form.handle_key("tab")
        self.assertEqual(form.current_field_idx, 1)

    def test_handle_key_backtab_navigates_back(self):
        form = self._make_form()
        form.current_field_idx = 2
        form._update_option_scroll()
        form.handle_key("s-tab")
        self.assertEqual(form.current_field_idx, 1)

    def test_handle_key_backtab_alt(self):
        form = self._make_form()
        form.current_field_idx = 2
        form._update_option_scroll()
        form.handle_key("backtab")
        self.assertEqual(form.current_field_idx, 1)

    def test_handle_key_enter_on_text_submits(self):
        form = self._make_form()
        form.set_text_value("Alice")
        ret = form.handle_key("enter")
        self.assertEqual(ret, "submit")
        self.assertTrue(form.done)

    def test_handle_key_enter_on_select_toggles(self):
        form = self._make_form()
        form.current_field_idx = 1
        form._update_option_scroll()
        ret = form.handle_key("enter")
        # For select, enter triggers select_option, not submit
        self.assertIsNone(ret)
        self.assertEqual(form.current_answer.selected_options, ["Red"])

    def test_handle_key_enter_on_multiselect_toggles(self):
        form = self._make_form()
        form.current_field_idx = 2
        form._update_option_scroll()
        ret = form.handle_key("enter")
        self.assertIsNone(ret)
        self.assertEqual(form.current_answer.selected_options, ["Hammer"])

    def test_handle_key_up_down_on_text_field_no_crash(self):
        form = self._make_form()
        form.handle_key("up")    # no options, should be no-op
        form.handle_key("down")  # same
        # nothing to assert — just no crash

    def test_handle_key_up_down_on_select_moves_selection(self):
        form = self._make_form()
        form.current_field_idx = 1
        form._update_option_scroll()
        self.assertEqual(form.option_scroll.selected, 0)
        form.handle_key("down")
        self.assertEqual(form.option_scroll.selected, 1)
        form.handle_key("up")
        self.assertEqual(form.option_scroll.selected, 0)

    def test_handle_key_kj_up_down_aliases(self):
        form = self._make_form()
        form.current_field_idx = 1
        form._update_option_scroll()
        self.assertEqual(form.option_scroll.selected, 0)
        form.handle_key("j")
        self.assertEqual(form.option_scroll.selected, 1)
        form.handle_key("k")
        self.assertEqual(form.option_scroll.selected, 0)

    def test_handle_key_space_on_text_noop(self):
        form = self._make_form()
        form.handle_key(" ")  # text field, noop
        # nothing to assert

    def test_handle_key_space_on_select_toggles(self):
        form = self._make_form()
        form.current_field_idx = 1
        form._update_option_scroll()
        form.handle_key(" ")
        self.assertEqual(form.current_answer.selected_options, ["Red"])

    def test_handle_key_space_on_multiselect_toggles(self):
        form = self._make_form()
        form.current_field_idx = 2
        form._update_option_scroll()
        form.handle_key(" ")
        self.assertEqual(form.current_answer.selected_options, ["Hammer"])
        form.handle_key(" ")  # toggle again
        self.assertEqual(form.current_answer.selected_options, [])

    def test_handle_key_unknown_noop(self):
        form = self._make_form()
        ret = form.handle_key("x")
        self.assertIsNone(ret)
        self.assertEqual(form.current_field_idx, 0)

    # -- Render --

    def test_render_lines_has_header(self):
        form = self._make_form()
        lines = form.render_lines()
        self.assertTrue(any("Request test-form" in l for l in lines))
        self.assertTrue(any("Your name?" in l for l in lines))
        self.assertTrue(any("Pick a color" in l for l in lines))

    def test_render_lines_marks_current_field(self):
        form = self._make_form()
        lines = form.render_lines()
        # first field should be marked with ►
        self.assertTrue(any("►" in l and "Name" in l for l in lines))

    def test_render_lines_multiselect_footer(self):
        form = self._make_form()
        lines = form.render_lines()
        self.assertTrue(any("Space: toggle" in l for l in lines))

    def test_render_lines_simple_footer(self):
        from factory.core.tui.bottom_pane.request_user_input_form import (
            UserInputQuestion, UserInputRequest,
        )
        q = UserInputQuestion(id="x", header="X", question="Just text")
        req = UserInputRequest(item_id="simple", thread_id="", turn_id="",
                               questions=[q])
        form = self._make_form(questions=[q])
        lines = form.render_lines()
        self.assertTrue(any("Enter: submit" in l for l in lines))
        self.assertFalse(any("Space: toggle" in l for l in lines))

    def test_render_lines_matches_line_specs(self):
        form = self._make_form()
        self.assertEqual(
            form.render_lines(),
            [text for text, _action in form.render_line_specs()],
        )

    def test_render_line_specs_expose_option_actions(self):
        form = self._make_form()
        actions = [a for _t, a in form.render_line_specs() if a]
        self.assertEqual(len(actions), 5)  # color(2) + tools(3)
        self.assertEqual(actions[0], ("option", 1, 0))
        self.assertEqual(actions[1], ("option", 1, 1))
        self.assertEqual(actions[2], ("option", 2, 0))
        self.assertEqual(actions[-1], ("option", 2, 2))

    def test_click_option_selects_single_choice(self):
        form = self._make_form()
        form.click_option(1, 1)  # Blue
        self.assertEqual(form.current_field_idx, 1)
        self.assertEqual(form.answers["color"].selected_options, ["Blue"])

    def test_click_option_toggles_multiselect(self):
        form = self._make_form()
        form.click_option(2, 0)  # Hammer
        form.click_option(2, 2)  # Drill
        self.assertEqual(
            form.answers["tools"].selected_options, ["Hammer", "Drill"]
        )
        form.click_option(2, 0)  # toggle Hammer off
        self.assertEqual(form.answers["tools"].selected_options, ["Drill"])

    def test_click_option_ignores_out_of_range(self):
        form = self._make_form()
        form.click_option(99, 0)
        form.click_option(1, 99)
        self.assertEqual(form.answers["color"].selected_options, ["Blue"])

    def test_render_lines_secret_masks_value(self):
        from factory.core.tui.bottom_pane.request_user_input_form import (
            UserInputQuestion, UserInputRequest,
        )
        q = UserInputQuestion(id="pwd", header="Pass", question="Password",
                              is_secret=True)
        req = UserInputRequest(item_id="test", thread_id="", turn_id="",
                               questions=[q])
        form = self._make_form(questions=[q])
        form.set_text_value("secret123")
        lines = form.render_lines()
        self.assertTrue(any("•••••••••" in l for l in lines))
        self.assertFalse(any("secret123" in l for l in lines))

    def test_render_lines_empty_questions(self):
        form = self._make_form(questions=[])
        lines = form.render_lines()
        self.assertTrue(any("No questions" in l for l in lines))

    def test_render_lines_shows_notes(self):
        from factory.core.tui.bottom_pane.request_user_input_form import (
            UserInputQuestion, UserInputRequest,
        )
        q = UserInputQuestion(id="q", header="Q", question="Question")
        req = UserInputRequest(item_id="test", thread_id="", turn_id="",
                               questions=[q])
        form = self._make_form(questions=[q])
        form.set_notes("Some note")
        lines = form.render_lines()
        self.assertTrue(any("Some note" in l for l in lines))


# ---------------------------------------------------------------------------
# Tests for _handle_request_user_input
# ---------------------------------------------------------------------------

class TestHandleRequestUserInput(unittest.TestCase):
    """Handler logic: argument parsing, answer serialization, error handling."""

    def setUp(self):
        self.maxDiff = None

    def test_handler_is_registered_after_cross_process_ipc_exists(self):
        from factory.core.cc_backend_tools import get_tool_schemas

        self.assertIn(
            "request_user_input",
            {schema["name"] for schema in get_tool_schemas()},
        )

    @patch("factory.core.user_input_store.wait_for_user_input_response")
    @patch("factory.core.user_input_store.create_user_input_request")
    def test_handler_uses_ipc_store_when_task_id_is_present(self, mock_create, mock_wait):
        from factory.core.cc_backend_tools import _handle_request_user_input

        mock_create.return_value = {"request_id": "request-1"}
        mock_wait.return_value = {
            "cancelled": False,
            "answers": {"name": {"question_id": "name", "text_value": "Alice"}},
        }

        result = json.loads(
            _handle_request_user_input(
                {
                    "questions": [
                        {"id": "name", "header": "Name", "question": "Your name?"}
                    ]
                },
                task_id="task-1",
                timeout_s=30,
            )
        )

        self.assertEqual(result["answers"]["name"]["text_value"], "Alice")
        mock_create.assert_called_once()
        self.assertEqual(mock_create.call_args.kwargs["task_id"], "task-1")
        mock_wait.assert_called_once_with("request-1", timeout_s=30.0)

    @patch("factory.core.cli_chat_user_input._ask_user_input")
    def test_handler_parses_questions_and_returns_answers(self, mock_ask):
        from factory.core.cc_backend_tools import _handle_request_user_input
        from factory.core.tui.bottom_pane.request_user_input_form import (
            UserInputResponse, UserInputAnswer,
        )

        # Setup mock to return a response
        response = UserInputResponse(answers={
            "name": UserInputAnswer(question_id="name", text_value="Alice"),
            "color": UserInputAnswer(
                question_id="color", selected_options=["Blue"],
                notes="I like blue",
            ),
        })
        mock_ask.return_value = response

        args = {
            "title": "Test Form",
            "questions": [
                {"id": "name", "header": "Name", "question": "Your name?"},
                {
                    "id": "color", "header": "Color", "question": "Pick",
                    "options": [
                        {"label": "Red"},
                        {"label": "Blue", "description": "Nice color"},
                    ],
                },
            ],
        }
        result_str = _handle_request_user_input(args)
        result = json.loads(result_str)

        self.assertFalse(result["cancelled"])
        self.assertIn("name", result["answers"])
        self.assertEqual(result["answers"]["name"]["text_value"], "Alice")
        self.assertEqual(result["answers"]["name"]["selected_options"], [])

        self.assertIn("color", result["answers"])
        self.assertEqual(result["answers"]["color"]["selected_options"], ["Blue"])
        self.assertEqual(result["answers"]["color"]["notes"], "I like blue")

    @patch("factory.core.cli_chat_user_input._ask_user_input")
    def test_handler_cancelled_returns_error(self, mock_ask):
        from factory.core.cc_backend_tools import _handle_request_user_input

        mock_ask.return_value = None  # user cancelled

        args = {
            "questions": [
                {"id": "x", "header": "X", "question": "Question"},
            ],
        }
        result_str = _handle_request_user_input(args)
        result = json.loads(result_str)

        self.assertTrue(result["cancelled"])
        self.assertIn("error", result)

    @patch("factory.core.cli_chat_user_input._ask_user_input")
    def test_handler_empty_questions(self, mock_ask):
        from factory.core.cc_backend_tools import _handle_request_user_input

        args = {"questions": []}
        result_str = _handle_request_user_input(args)
        result = json.loads(result_str)

        self.assertIn("error", result)
        self.assertIn("non-empty list", result["error"])
        mock_ask.assert_not_called()

    @patch("factory.core.cli_chat_user_input._ask_user_input")
    def test_handler_handles_runtime_exception(self, mock_ask):
        from factory.core.cc_backend_tools import _handle_request_user_input

        mock_ask.side_effect = RuntimeError("Something went wrong")

        args = {
            "questions": [
                {"id": "x", "header": "X", "question": "?"},
            ],
        }
        result_str = _handle_request_user_input(args)
        result = json.loads(result_str)

        self.assertIn("error", result)
        self.assertIn("RuntimeError", result["error"])
        self.assertIn("Something went wrong", result["error"])

    @patch("factory.core.cli_chat_user_input._ask_user_input")
    def test_handler_multiselect_serialization(self, mock_ask):
        from factory.core.cc_backend_tools import _handle_request_user_input
        from factory.core.tui.bottom_pane.request_user_input_form import (
            UserInputResponse, UserInputAnswer,
        )

        response = UserInputResponse(answers={
            "tools": UserInputAnswer(
                question_id="tools",
                selected_options=["Hammer", "Drill"],
            ),
        })
        mock_ask.return_value = response

        args = {
            "questions": [
                {
                    "id": "tools", "header": "Tools", "question": "Pick",
                    "options": [
                        {"label": "Hammer"},
                        {"label": "Saw"},
                        {"label": "Drill"},
                    ],
                    "is_multiselect": True,
                },
            ],
        }
        result_str = _handle_request_user_input(args)
        result = json.loads(result_str)

        self.assertCountEqual(
            result["answers"]["tools"]["selected_options"],
            ["Hammer", "Drill"],
        )

    @patch("factory.core.cli_chat_user_input._ask_user_input")
    def test_handler_secret_question(self, mock_ask):
        from factory.core.cc_backend_tools import _handle_request_user_input
        from factory.core.tui.bottom_pane.request_user_input_form import (
            UserInputResponse, UserInputAnswer,
        )

        response = UserInputResponse(answers={
            "pwd": UserInputAnswer(question_id="pwd", text_value="s3cret"),
        })
        mock_ask.return_value = response

        args = {
            "questions": [
                {
                    "id": "pwd", "header": "Pass", "question": "Password",
                    "is_secret": True,
                },
            ],
        }
        result_str = _handle_request_user_input(args)
        result = json.loads(result_str)

        self.assertEqual(result["answers"]["pwd"]["text_value"], "s3cret")
        self.assertEqual(result["answers"]["pwd"]["selected_options"], [])

    def test_handler_missing_args_does_not_crash(self):
        from factory.core.cc_backend_tools import _handle_request_user_input

        # No 'questions' key at all
        result_str = _handle_request_user_input({})
        result = json.loads(result_str)

        # Should return an error rather than crash
        self.assertIn("error", result)

    def test_handler_invalid_question_format_does_not_crash(self):
        from factory.core.cc_backend_tools import _handle_request_user_input

        args = {
            "questions": [
                {"id": "x", "header": "X"},  # missing "question" key
            ],
        }
        result_str = _handle_request_user_input(args)
        result = json.loads(result_str)

        # KeyError should be caught and returned as error
        self.assertIn("error", result)
        # The underlying code uses q["question"] which will raise KeyError
        # since "question" is missing in the dict
        self.assertIn("KeyError", result["error"])


# ---------------------------------------------------------------------------
# Integration-style: form end-to-end with handle_key sequence
# ---------------------------------------------------------------------------

class TestFormWorkflow(unittest.TestCase):
    """End-to-end form workflows simulating user interaction."""

    def test_simple_text_then_submit(self):
        from factory.core.tui.bottom_pane.request_user_input_form import (
            UserInputQuestion, UserInputRequest, RequestUserInputForm,
        )
        req = UserInputRequest(
            item_id="test", thread_id="", turn_id="",
            questions=[
                UserInputQuestion(id="name", header="Name", question="Your name?"),
            ],
        )
        form = RequestUserInputForm(req)
        form.set_text_value("Alice")
        ret = form.handle_key("enter")
        self.assertEqual(ret, "submit")
        self.assertTrue(form.done)
        self.assertEqual(form.answers["name"].text_value, "Alice")

    def test_select_one_option_and_submit(self):
        from factory.core.tui.bottom_pane.request_user_input_form import (
            UserInputOption, UserInputQuestion, UserInputRequest,
            RequestUserInputForm,
        )
        req = UserInputRequest(
            item_id="test", thread_id="", turn_id="",
            questions=[
                UserInputQuestion(
                    id="color", header="Color", question="Pick",
                    options=[UserInputOption("Red"), UserInputOption("Blue")],
                ),
            ],
        )
        form = RequestUserInputForm(req)
        # Navigate to option Blue
        form.handle_key("down")
        form.handle_key("enter")  # select Blue
        # Now submit (enter on select toggles, not submits — need to move to submit)
        # On a single select field, enter toggles. But there's no text field to submit after.
        # Actually after select_option we can force-call submit.
        result = form.submit()
        self.assertIsNotNone(result)
        self.assertEqual(form.answers["color"].selected_options, ["Blue"])

    def test_multiselect_mixed_with_text(self):
        from factory.core.tui.bottom_pane.request_user_input_form import (
            UserInputOption, UserInputQuestion, UserInputRequest,
            RequestUserInputForm,
        )
        req = UserInputRequest(
            item_id="test", thread_id="", turn_id="",
            questions=[
                UserInputQuestion(id="name", header="Name", question="Your name?"),
                UserInputQuestion(
                    id="tools", header="Tools", question="Pick",
                    options=[
                        UserInputOption("Hammer"),
                        UserInputOption("Saw"),
                        UserInputOption("Drill"),
                    ],
                    is_multiselect=True,
                ),
            ],
        )
        form = RequestUserInputForm(req)
        # Fill text
        form.set_text_value("Alice")
        # Go to multiselect
        form.handle_key("tab")
        self.assertEqual(form.current_question.id, "tools")

        # Select Saw (move down, then space)
        form.handle_key("down")
        form.handle_key(" ")
        # Select Drill (move down, then space)
        form.handle_key("down")
        form.handle_key(" ")

        # Verify selections so far
        self.assertCountEqual(
            form.answers["tools"].selected_options,
            ["Saw", "Drill"],
        )

        # Toggle Saw off
        form.handle_key("up")
        form.handle_key(" ")  # remove Saw
        self.assertCountEqual(
            form.answers["tools"].selected_options,
            ["Drill"],
        )

        # Submit
        result = form.submit()
        self.assertIsNotNone(result)
        self.assertEqual(form.answers["name"].text_value, "Alice")
        self.assertEqual(form.answers["tools"].selected_options, ["Drill"])

    def test_cancel_workflow(self):
        from factory.core.tui.bottom_pane.request_user_input_form import (
            UserInputQuestion, UserInputRequest, RequestUserInputForm,
        )
        req = UserInputRequest(
            item_id="test", thread_id="", turn_id="",
            questions=[
                UserInputQuestion(id="x", header="X", question="Any?"),
            ],
        )
        form = RequestUserInputForm(req)
        ret = form.handle_key("escape")
        self.assertEqual(ret, "cancel")
        self.assertTrue(form.cancelled)
        self.assertTrue(form.done)


class TestUserInputPromptFragments(unittest.TestCase):
    """Mouse wiring: option rows and submit/cancel become clickable fragments."""

    def _form(self):
        from factory.core.tui.bottom_pane.request_user_input_form import (
            RequestUserInputForm,
            UserInputOption,
            UserInputQuestion,
            UserInputRequest,
        )
        req = UserInputRequest(
            item_id="frag-form",
            thread_id="",
            turn_id="",
            questions=[
                UserInputQuestion(
                    id="color",
                    header="Color",
                    question="Pick a color",
                    options=[UserInputOption("Red"), UserInputOption("Blue")],
                )
            ],
        )
        return RequestUserInputForm(req)

    def test_fragments_mark_options_and_actions_clickable(self):
        from factory.core.cli_chat_user_input import _build_prompt_fragments

        form = self._form()
        calls = []

        def handler(action):
            def _h(_event=None):
                calls.append(action)
                return None
            return _h

        fragments = _build_prompt_fragments(form, handler)
        actionable = [f for f in fragments if len(f) == 3]
        # Red, Blue, Submit, Cancel
        self.assertEqual(len(actionable), 4)

        blue = next(f for f in actionable if "Blue" in f[1])
        blue[2]()
        self.assertEqual(calls, [("option", 0, 1)])

        submit = next(f for f in actionable if "Submit" in f[1])
        submit[2]()
        self.assertIn(("submit",), calls)

    def test_dispatch_option_action_selects_and_invalidates(self):
        from factory.core.cli_chat_user_input import _dispatch_form_action

        class App:
            def __init__(self):
                self.invalidated = 0
                self.exited = "unset"
            def invalidate(self):
                self.invalidated += 1
            def exit(self, result=None):
                self.exited = result

        form = self._form()
        app = App()
        _dispatch_form_action(form, ("option", 0, 1), app)  # Blue
        self.assertEqual(form.answers["color"].selected_options, ["Blue"])
        self.assertEqual(app.invalidated, 1)
        self.assertEqual(app.exited, "unset")

    def test_dispatch_submit_action_exits_with_response(self):
        from factory.core.cli_chat_user_input import _dispatch_form_action
        from factory.core.tui.bottom_pane.request_user_input_form import (
            UserInputResponse,
        )

        class App:
            def invalidate(self):
                pass
            def exit(self, result="unset"):
                self.exited = result

        form = self._form()
        app = App()
        _dispatch_form_action(form, ("submit",), app)
        self.assertIsInstance(app.exited, UserInputResponse)

    def test_dispatch_cancel_action_exits_none(self):
        from factory.core.cli_chat_user_input import _dispatch_form_action

        class App:
            def invalidate(self):
                pass
            def exit(self, result=None):
                self.exited = result

        form = self._form()
        app = App()
        _dispatch_form_action(form, ("cancel",), app)
        self.assertIsNone(app.exited)
        self.assertTrue(form.cancelled)

    def test_dispatch_ignores_missing_app(self):
        from factory.core.cli_chat_user_input import _dispatch_form_action

        form = self._form()
        _dispatch_form_action(form, ("option", 0, 1), None)
        self.assertEqual(form.answers["color"].selected_options, [])

    def test_form_application_erases_when_done(self):
        from prompt_toolkit.formatted_text import FormattedText
        from prompt_toolkit.key_binding import KeyBindings

        from factory.core.cli_chat_user_input import _create_form_application

        form = self._form()
        holder = {}
        app = _create_form_application(
            form,
            KeyBindings(),
            lambda: FormattedText([("", "x")]),
            holder,
        )
        self.assertIsNotNone(app)
        self.assertTrue(bool(app.erase_when_done))
        self.assertIs(holder.get("app"), app)

    def test_only_mouse_down_is_a_form_click(self):
        from prompt_toolkit.data_structures import Point
        from prompt_toolkit.mouse_events import (
            MouseButton,
            MouseEvent,
            MouseEventType,
        )

        from factory.core.cli_chat_user_input import _is_form_click

        def event(event_type):
            return MouseEvent(
                position=Point(x=0, y=0),
                event_type=event_type,
                button=MouseButton.LEFT,
                modifiers=0,
            )

        self.assertTrue(_is_form_click(event(MouseEventType.MOUSE_DOWN)))
        self.assertFalse(_is_form_click(event(MouseEventType.MOUSE_UP)))
        self.assertFalse(_is_form_click(event(MouseEventType.MOUSE_MOVE)))

    def test_form_click_without_event_type_is_allowed(self):
        from factory.core.cli_chat_user_input import _is_form_click

        self.assertTrue(_is_form_click(object()))


if __name__ == "__main__":
    unittest.main()
