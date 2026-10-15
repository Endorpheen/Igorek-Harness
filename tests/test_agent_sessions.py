import json
import os
import sqlite3
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from factory.core.cc_backend_trace import append_cc_backend_trace, scoped_agent_trace, agent_trace_context
from factory.core.cli_chat import _session_path
from factory.core.cli_tasks import create_task
from factory.core.mobile_bridge.events import TraceJournaler, trace_path_for_task
from factory.core.mobile_bridge.service import MobileService
from factory.core.runtime import get_db_path, get_workspace_root
from factory.core.schema import initialize_database
from factory.core.subagents import SubagentReporter
from factory.core.user_input_store import create_user_input_request


class AgentSessionsTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = patch.dict(os.environ, {"IGOREK_FACTORY_HOME": self.tmp.name})
        self.env.start()
        self.addCleanup(self.tmp.cleanup)
        self.addCleanup(self.env.stop)
        initialize_database(get_db_path())
        self.root = "chat-agent-tests"
        path = _session_path(self.root)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"type": "meta", "session_id": self.root, "profile": "test", "cwd": self.tmp.name}) + "\n")
        self.service = MobileService(profile="test", workdir=self.tmp.name)
        self.task = create_task(text="Main", profile="test", metadata={"session": self.root, "source": "mobile_bridge", "cwd": self.tmp.name})["task_id"]
        self.artifact = trace_path_for_task(self.task).parent
        self.artifact.mkdir(parents=True, exist_ok=True)
        self.reporter = SubagentReporter(artifact_workdir=self.artifact, session_id=self.root, parent_task_id=self.task)
        self.provider = lambda: [{"task_id": self.task, "status": "processing", "metadata": {"session": self.root}}]
        self.journal = TraceJournaler(self.service.store, workspace_root=get_workspace_root(), active_task_provider=self.provider)
        self.addCleanup(patch.stopall)
        patch("factory.web_ui.server._model_context_window", return_value=100000).start()
        patch("factory.web_ui.server._profile_model", return_value="test").start()

    def start(self, sub, parent=None):
        self.service.store.append_event(kind="subagent.start", session_id=self.root, task_id=self.task,
            payload={"subagent_id": sub, "parent_task_id": self.task, "agent_session_id": "agent-" + sub,
                     "parent_session_id": parent or self.root, "root_session_id": self.root,
                     "goal_title": sub, "goal_text": "Goal " + sub, "status": "running"})

    def trace(self, sub, kind, **extra):
        append_cc_backend_trace(str(self.artifact), {"kind": kind, "subagent_id": sub,
            "agent_session_id": "agent-" + sub, "root_session_id": self.root,
            "parent_session_id": self.root, **extra})

    def test_running_child_opens_with_standard_session_and_replayable_stream(self):
        self.start("sub_a")
        self.trace("sub_a", "task.start", current_session=self.root)
        self.trace("sub_a", "model.reasoning_delta", text="Thinking ")
        self.trace("sub_a", "model.text_delta", text="Hello ", round=1)
        self.journal.poll()
        child = self.service.get_session("agent-sub_a")
        self.assertEqual(child["running_task_id"], self.task)
        self.assertEqual(child["meta"]["parent_session_id"], self.root)
        events = self.service.session_events("agent-sub_a")
        self.assertEqual([event["kind"] for event in events], ["task.start", "model.reasoning_delta", "model.text_delta"])
        self.assertEqual(self.service.session_events("agent-sub_a"), events)
        self.assertTrue(all(event["seq"] for event in events))
        self.assertEqual([event["kind"] for event in self.service.session_events(self.root)], ["subagent.start"])

    def test_parallel_siblings_switching_and_nested_tree_do_not_create_tasks(self):
        for sub in ("sub_a", "sub_b", "sub_c"):
            self.start(sub)
            self.trace(sub, "model.round_response", text_full=sub, round=1)
            self.trace(sub, "tool.request_batch", tools=[{"id": "same-call", "name": "Read", "input": {"file_path": sub}}])
        self.start("sub_nested", "agent-sub_a")
        self.trace("sub_nested", "model.round_response", text_full="nested", round=1)
        self.journal.poll()
        before = self.service.session_events("agent-sub_a")
        self.service.get_session("agent-sub_b")
        self.service.get_session(self.root)
        self.assertEqual(self.service.session_events("agent-sub_a"), before)
        self.assertEqual(before[0]["payload"]["text"], "sub_a")
        tree = self.service.agent_sessions("agent-sub_nested")
        nested = next(node for node in tree["sessions"] if node["session_id"] == "agent-sub_nested")
        self.assertEqual(nested["parent_session_id"], "agent-sub_a")
        with sqlite3.connect(get_db_path()) as conn:
            self.assertEqual(conn.execute("SELECT count(*) FROM tasks").fetchone()[0], 1)

    def test_child_finish_does_not_suppress_root_failure(self):
        self.start("sub_a")
        self.trace("sub_a", "task.finish", status="fail", reason="backend_error")
        self.journal.poll()
        self.assertNotIn(self.task, self.journal._finished_tasks)
        self.service.store.append_event(kind="subagent.finish", session_id=self.root, task_id=self.task,
            payload={"subagent_id": "sub_a", "status": "error", "error": "backend_error"})
        child = self.service.get_session("agent-sub_a")
        self.assertIsNone(child["running_task_id"])
        self.assertEqual(self.service.agent_sessions(self.root)["sessions"][1]["status"], "failed")

    def test_restart_replays_child_deltas_once(self):
        self.start("sub_a")
        self.trace("sub_a", "model.text_delta", text="one", round=1)
        self.journal.poll()
        restarted = TraceJournaler(self.service.store, workspace_root=get_workspace_root(), active_task_provider=self.provider)
        restarted.poll()
        self.assertEqual(len(self.service.session_events("agent-sub_a")), 1)

    def test_child_questions_are_visible_on_main_and_child_during_processing(self):
        self.start("sub_a")
        request = create_user_input_request(task_id=self.task, payload={"title": "Question", "questions": [], "context": {"agent_session_id": "agent-sub_a"}})
        with sqlite3.connect(get_db_path()) as conn:
            conn.execute("UPDATE tasks SET status = 'processing' WHERE task_id = ?", (self.task,))
        for sid in (self.root, "agent-sub_a"):
            approvals = self.service.list_approvals(session_id=sid)
            self.assertEqual(approvals[0]["request_id"], request["request_id"])
            self.assertEqual(approvals[0]["session_id"], "agent-sub_a")

    def test_child_approval_resumes_delegate_with_same_identity(self):
        self.start("sub_a")
        blocked = {"tool": "Write", "input": {"file_path": "file.txt", "content": "text"},
            "tool_use_id": "child-call", "signature": "write-signature", "agent_session_id": "agent-sub_a",
            "delegate_resume": {"goal": "Goal", "_resume": {"subagent_id": "sub_a", "child_id": "1234abcd"}}}
        with sqlite3.connect(get_db_path()) as conn:
            conn.execute("UPDATE tasks SET status = 'waiting_approval', approval_blocked_tools = ? WHERE task_id = ?", (json.dumps([blocked]), self.task))
        self.assertEqual(self.service.list_approvals(session_id="agent-sub_a")[0]["task_id"], self.task)
        self.service.resolve_approval(self.task, "child-call", "allow_once")
        with sqlite3.connect(get_db_path()) as conn:
            status, raw = conn.execute("SELECT status, metadata FROM tasks WHERE task_id = ?", (self.task,)).fetchone()
        metadata = json.loads(raw)
        self.assertEqual(status, "queued")
        self.assertEqual(metadata["forced_tool_call"]["name"], "delegate_task")
        self.assertEqual(metadata["forced_tool_call"]["input"]["_resume"]["subagent_id"], "sub_a")
        self.assertIn("write-signature", metadata["approved_tool_calls"])


    def test_child_artifacts_are_visible_without_exposing_hidden_files(self):
        self.start("sub_a")
        childdir = Path(self.tmp.name) / ".delegate_1234abcd"
        childdir.mkdir()
        (childdir / "report.txt").write_text("report")
        (childdir / ".secret").write_text("secret")
        self.service.store.append_event(kind="subagent.start", session_id=self.root, task_id=self.task,
            payload={"subagent_id": "sub_a", "agent_session_id": "agent-sub_a", "root_session_id": self.root,
                     "parent_session_id": self.root, "goal_title": "Research", "workdir": str(childdir)})
        for name in ("report.txt", ".secret"):
            self.service.store.append_event(kind="tool.result", session_id="agent-sub_a", task_id=self.task,
                payload={"tool_name": "Write", "tool_input": {"file_path": str(childdir / name)}})
        files = self.service.list_session_files("agent-sub_a")
        self.assertEqual([item["name"] for item in files], ["report.txt"])
        handle = self.service.open_session_file("agent-sub_a", files[0]["id"])
        if isinstance(handle, tuple):
            handle = handle[-1]
        handle.close()


class TraceIdentityTest(unittest.TestCase):
    def test_nested_scope_restores_parent_and_threads_do_not_share_identity(self):
        @scoped_agent_trace
        def run(**kwargs):
            before = agent_trace_context()
            if kwargs["subagent_id"] == "sub_parent":
                child = run(current_session="main", subagent_id="sub_nested")
                self.assertEqual(child["parent_session_id"], "agent-sub_parent")
                self.assertEqual(agent_trace_context(), before)
            return before
        with ThreadPoolExecutor(3) as pool:
            results = list(pool.map(lambda sub: run(current_session="main", subagent_id=sub), ["sub_parent", "sub_a", "sub_b"]))
        self.assertEqual([item["agent_session_id"] for item in results], ["agent-sub_parent", "agent-sub_a", "agent-sub_b"])
        self.assertEqual(agent_trace_context(), {})


class DelegationNavigationTest(unittest.TestCase):
    def test_nested_delegation_uses_existing_executor_and_depth_limit(self):
        from factory.core import delegate_tool
        captured = []
        def child(**kwargs):
            captured.append(kwargs)
            return {"status": "ok", "output_text": "done"}
        with tempfile.TemporaryDirectory() as tmp, patch("factory.core.cc_backend_executor.execute_cc_backend_task", child):
            delegate_tool.delegate_task({"goal": "Research"}, workdir=tmp, delegate_depth=0)
            delegate_tool.delegate_task({"goal": "Web Research"}, workdir=tmp, delegate_depth=1)
            rejected = json.loads(delegate_tool.delegate_task({"goal": "too deep"}, workdir=tmp, delegate_depth=2))
        self.assertIn("delegate_task", {tool["name"] for tool in captured[0]["enabled_tools"]})
        self.assertNotIn("delegate_task", {tool["name"] for tool in captured[1]["enabled_tools"]})
        self.assertEqual([1, 2], [item["delegate_depth"] for item in captured])
        self.assertIn("error", rejected)

    def test_resumed_child_preserves_nested_approval_target_and_identity(self):
        from factory.core import delegate_tool
        captured = []
        nested = {"goal": "grandchild", "_resume": {"child_id": "87654321", "subagent_id": "sub_grand"}}
        blocked = {"tool": "Write", "input": {"file_path": "/tmp/test"}, "signature": "approved",
                   "agent_session_id": "agent-sub_grand", "delegate_resume": nested,
                   "parent_approval_context_messages": []}
        def child(**kwargs):
            captured.append(kwargs)
            return {"status": "waiting_approval", "failure_reason": "approval_required", "approval_blocked_tools": [blocked]}
        with tempfile.TemporaryDirectory() as tmp, patch("factory.core.cc_backend_executor.execute_cc_backend_task", child):
            result = json.loads(delegate_tool.delegate_task({"goal": "Research", "_resume": {
                "subagent_id": "sub_parent", "child_id": "12345678", "blocked": blocked}}, workdir=tmp,
                delegate_metadata={"approved_tool_calls": ["approved"]}))
        self.assertEqual(captured[0]["subagent_id"], "sub_parent")
        self.assertEqual(captured[0]["metadata"]["forced_tool_call"]["name"], "delegate_task")
        self.assertEqual(captured[0]["metadata"]["forced_tool_call"]["input"], nested)
        self.assertEqual(result["approval_blocked_tools"][0]["agent_session_id"], "agent-sub_grand")

    def test_repeated_child_approval_stays_paused_without_overwriting_main_changes(self):
        from factory.core import cc_backend_executor as executor
        blocked = {"tool": "Write", "input": {"file_path": "report.txt"}, "tool_use_id": "next-call"}
        for api_format in ("anthropic", "openai"):
            with self.subTest(api_format=api_format), tempfile.TemporaryDirectory() as tmp, \
                    patch.object(executor, "TaskChangeTracker") as tracker, \
                    patch.object(executor, "task_should_stop", return_value=False), \
                    patch.object(executor, "_resolve_backend_context_window", return_value=(32768, "test")), \
                    patch.object(executor, "execute_tool", return_value=json.dumps({"status": "waiting_approval", "failure_reason": "approval_required", "approval_blocked_tools": [blocked]})):
                result = executor.execute_cc_backend_task(base_url="http://127.0.0.1:1", model="test", system_prompt="system",
                    user_message="Research", workdir=tmp, artifact_workdir=tmp, task_id="main-task",
                    subagent_id="sub_research", current_session="main", api_format=api_format,
                    metadata={"forced_tool_call": {"name": "delegate_task", "input": {"goal": "nested"}}})
                self.assertEqual(result["status"], "waiting_approval")
                self.assertEqual(result["approval_blocked_tools"][0]["tool_use_id"], "next-call")
                tracker.assert_not_called()
