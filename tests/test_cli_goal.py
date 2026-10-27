import asyncio
from unittest.mock import Mock

from factory.core import thread_goals
from test_chat_session_lifecycle_contracts import entries, prepare_turn, session as session


def test_terminal_goal_commands_create_pause_edit_resume_and_clear(session):
    session._handle_command("/goal Complete the work")
    goal = thread_goals.get_goal(session.session_name)
    assert goal["objective"] == "Complete the work" and goal["surface"] == "cli_chat_rich"
    session._handle_command("/goal pause")
    assert thread_goals.get_goal(session.session_name)["status"] == "paused"
    session._handle_command("/goal edit Verify the work")
    assert thread_goals.get_goal(session.session_name)["objective"] == "Verify the work"
    session._handle_command("/goal resume")
    assert thread_goals.get_goal(session.session_name)["status"] == "active"
    session._handle_command("/goal clear")
    assert thread_goals.get_goal(session.session_name) is None


def test_goal_continuation_is_not_saved_as_a_fake_user_message(session, monkeypatch):
    session._startup()
    goal = thread_goals.create_goal(session.session_name, "Finish the task", surface="cli_chat_rich")
    calls = prepare_turn(session, monkeypatch, [{"task_id": "turn-1", "status": "ok", "output_text": "Progress",
                                                 "usage_prompt": 3, "usage_completion": 2, "started_at": 10, "completed_at": 12}])
    session._execute_user_turn("internal goal prompt", goal_turn=True)
    assert [item["role"] for item in entries(session)] == ["assistant"]
    assert calls[0][1]["goal_id"] == goal["goal_id"]
    assert thread_goals.get_goal(session.session_name)["tokens_used"] == 5


def test_classic_goal_continues_and_user_input_takes_priority(session, monkeypatch):
    session._tui_fullscreen = False
    session.classic_style = "legacy"
    session.exit_on_interrupt = False
    goal = thread_goals.create_goal(session.session_name, "Work", surface="cli_chat_rich")
    session.pending_inputs = ["user input"]
    calls = []
    monkeypatch.setattr(session, "_startup", lambda: None)
    monkeypatch.setattr(session, "_goodbye", lambda: None)
    monkeypatch.setattr(session, "_handle_input", lambda text: calls.append(("user", text)))
    def run_goal(text, goal_turn=False):
        calls.append(("goal", text))
        thread_goals.update_goal(session.session_name, "complete", expected_goal_id=goal["goal_id"])
        session.pending_inputs.append("/exit")
    monkeypatch.setattr(session, "_execute_user_turn", run_goal)
    assert session.run() == 0
    assert calls[0] == ("user", "user input")
    assert calls[1][0] == "goal" and "Work" in calls[1][1]


def test_fullscreen_goal_turn_uses_internal_execution_path(session, monkeypatch):
    session._tui_fullscreen = True
    session._fs_loop = None
    session._fs_submitted = []
    execute = Mock()
    monkeypatch.setattr(session, "_execute_user_turn", execute)
    app = Mock()
    async def run():
        loop = asyncio.get_running_loop()
        def execute_inline(_executor, func, *args):
            result = loop.create_future()
            result.set_result(func(*args))
            return result
        monkeypatch.setattr(loop, "run_in_executor", execute_inline)
        await session._fs_run_turn("goal prompt", {}, app, goal_turn=True)
    asyncio.run(run())
    execute.assert_called_once_with("goal prompt", True)
