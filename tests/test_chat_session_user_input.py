import tempfile
import unittest
from unittest.mock import Mock, patch

from rich.console import Console

from factory.core.chat_session import ChatSession
from factory.core.user_input_store import (
    create_user_input_request,
    list_pending_user_input_requests,
    wait_for_user_input_response,
)


class ChatSessionUserInputTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db_path = f"{self.tmp.name}/factory.db"
        self.path_patch = patch(
            "factory.core.user_input_store.get_db_path", return_value=self.db_path
        )
        self.path_patch.start()
        self.addCleanup(self.path_patch.stop)

        self.session = object.__new__(ChatSession)
        self.session.console = Console(file=Mock(), force_terminal=False)

    def test_pending_user_input_request_returns_first_pending_request(self):
        first = create_user_input_request(
            task_id="task-1",
            payload={
                "questions": [
                    {"id": "choice", "header": "Choice", "question": "Pick one"}
                ]
            },
        )
        create_user_input_request(task_id="task-2", payload={"questions": []})

        pending = self.session._pending_user_input_request("task-1")

        self.assertIsNotNone(pending)
        self.assertEqual(pending["request_id"], first["request_id"])

    @patch("factory.core.cli_chat_user_input._ask_user_input")
    def test_resolve_user_input_request_submits_form_response(self, mock_ask):
        from factory.core.tui.bottom_pane.request_user_input_form import (
            UserInputAnswer,
            UserInputResponse,
        )

        created = create_user_input_request(
            task_id="task-1",
            payload={
                "questions": [
                    {"id": "choice", "header": "Choice", "question": "Pick one"}
                ]
            },
        )
        mock_ask.return_value = UserInputResponse(
            answers={"choice": UserInputAnswer(question_id="choice", text_value="A")}
        )

        self.session._resolve_user_input_request(created)

        response = wait_for_user_input_response(
            created["request_id"], timeout_s=1, interval_s=0.01
        )
        self.assertFalse(response["cancelled"])
        self.assertEqual(response["answers"]["choice"]["text_value"], "A")
        self.assertEqual(list_pending_user_input_requests(task_id="task-1"), [])

    @patch("factory.core.cli_chat_user_input._ask_user_input")
    def test_resolve_user_input_request_cancels_when_form_returns_none(self, mock_ask):
        created = create_user_input_request(
            task_id="task-1",
            payload={
                "questions": [
                    {"id": "choice", "header": "Choice", "question": "Pick one"}
                ]
            },
        )
        mock_ask.return_value = None

        self.session._resolve_user_input_request(created)

        response = wait_for_user_input_response(
            created["request_id"], timeout_s=1, interval_s=0.01
        )
        self.assertIsNone(response)
        self.assertEqual(list_pending_user_input_requests(task_id="task-1"), [])


if __name__ == "__main__":
    unittest.main()
