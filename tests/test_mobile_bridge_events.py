import json
import tempfile
import unittest
from pathlib import Path

from factory.core.mobile_bridge.events import (
    TraceJournaler,
    classify_tool_status,
    normalize_trace_event,
    trace_path_for_task,
)
from factory.core.mobile_bridge.store import MobileStore
from factory.core.schema import initialize_database


def _line(payload: dict) -> str:
    return json.dumps(payload, ensure_ascii=False) + "\n"


class EventsTestCase(unittest.TestCase):
    def test_task_start_preserves_context_window(self):
        event = normalize_trace_event({"kind": "task.start", "model_context_window": 200000})
        self.assertEqual(event["payload"]["model_context_window"], 200000)

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self.db_path = str(root / "factory.db")
        self.workspace = root / "workspace"
        self.workspace.mkdir(parents=True, exist_ok=True)
        initialize_database(self.db_path)
        self.store = MobileStore(db_path=self.db_path)
        self.task_id = "aaaaaaaa-1111-2222-3333-444444444444"
        self.session_id = "chat-20260916-000000-abc123"
        self.trace = trace_path_for_task(self.task_id, self.workspace)
        self.trace.parent.mkdir(parents=True, exist_ok=True)

    def tearDown(self):
        self._tmp.cleanup()

    def _write(self, *payloads: dict) -> None:
        with self.trace.open("a", encoding="utf-8") as handle:
            for payload in payloads:
                handle.write(_line(payload))

    def _journaler(self, **kwargs) -> TraceJournaler:
        tasks = kwargs.pop(
            "tasks",
            [{"task_id": self.task_id, "status": "processing", "metadata": {}, "created_at": 0}],
        )
        return TraceJournaler(
            self.store,
            workspace_root=self.workspace,
            active_task_provider=lambda: tasks,
            **kwargs,
        )

    def _kinds(self) -> list[str]:
        return [event["kind"] for event in self.store.list_events(limit=500)]


class ToolCallNormalizationTest(EventsTestCase):
    def test_tool_request_and_result_are_journaled(self):
        self._write(
            {"ts": 100, "kind": "task.start", "model": "m", "current_session": self.session_id},
            {"ts": 101, "kind": "tool.request_batch", "round": 1, "tools": [
                {"id": "call_1", "name": "Bash", "input": {"command": "pytest -q"}},
            ]},
            {"ts": 102, "kind": "tool.result", "round": 1, "tool_use_id": "call_1",
             "tool_name": "Bash", "tool_input": {"command": "pytest -q"},
             "raw_result_preview": "12 passed (exit code: 0)", "raw_result_len": 25,
             "duration_ms": 1500},
        )

        appended = self._journaler().poll()

        self.assertEqual(appended, 3)
        events = self.store.list_events(limit=50)
        self.assertEqual([e["kind"] for e in events], ["task.start", "tool.request_batch", "tool.result"])
        result = events[2]["payload"]
        self.assertEqual(result["tool_name"], "Bash")
        self.assertEqual(result["status"], "exit")
        self.assertEqual(result["duration_ms"], 1500)
        self.assertEqual(events[1]["payload"]["tools"][0]["input"]["command"], "pytest -q")
        self.assertEqual(events[0]["payload"]["session_id"], self.session_id)

    def test_isolation_recovery_events_are_journaled_for_the_mobile_timeline(self):
        self._write(
            {"ts": 100, "kind": "task.start", "current_session": self.session_id},
            {"ts": 101, "kind": "isolation.recovery_started", "attempt": 1, "max_attempts": 1,
             "reason": "approval_request_missing"},
            {"ts": 102, "kind": "isolation.recovery_finished", "attempt": 1, "status": "fail",
             "failure_reason": "approval_request_missing", "error_message": "approval record was missing"},
        )

        self._journaler().poll()

        events = self.store.list_events(limit=10)
        self.assertEqual(
            [event["kind"] for event in events],
            ["task.start", "isolation.recovery_started", "isolation.recovery_finished"],
        )
        self.assertEqual(events[1]["payload"]["attempt"], 1)
        self.assertEqual(events[2]["payload"]["status"], "fail")
        self.assertEqual(events[2]["payload"]["error_message"], "approval record was missing")

    def test_stored_output_path_detected(self):
        stored = self.trace.parent / ".tool_results" / "call_9.txt"
        stored.parent.mkdir(parents=True, exist_ok=True)
        stored.write_text("x" * 20_000, encoding="utf-8")
        self._write(
            {"ts": 1, "kind": "tool.request_batch", "round": 1, "tools": [{"id": "call_9", "name": "Bash", "input": {}}]},
            {"ts": 2, "kind": "tool.result", "round": 1, "tool_use_id": "call_9", "tool_name": "Bash",
             "tool_input": {}, "raw_result_preview": "ok", "raw_result_len": 20000, "duration_ms": 5},
        )

        self._journaler().poll()

        result = self.store.list_events(limit=10)[1]
        self.assertEqual(result["payload"]["stored_path"], ".tool_results/call_9.txt")

    def test_error_result_classified(self):
        self._write(
            {"ts": 1, "kind": "tool.request_batch", "round": 1, "tools": [{"id": "c", "name": "Read", "input": {}}]},
            {"ts": 2, "kind": "tool.result", "round": 1, "tool_use_id": "c", "tool_name": "Read", "tool_input": {},
             "raw_result_preview": "Error: file not found", "raw_result_len": 20, "duration_ms": 1},
        )

        self._journaler().poll()

        self.assertEqual(self.store.list_events(limit=10)[1]["payload"]["status"], "error")


class ApprovalNormalizationTest(EventsTestCase):
    def test_approval_required_keeps_signature_and_request_id(self):
        self._write(
            {"ts": 5, "kind": "tool.approval_required", "round": 3, "tool_use_id": "call_abc",
             "tool": "Bash", "input": {"command": "rm -rf build"}, "target": "rm -rf build",
             "signature": "Bash:{\"command\":\"rm -rf build\"}", "reason": "deletive",
             "session_rule": {"rule_type": "exact_tool_call", "tool": "Bash",
                              "signature": "Bash:{\"command\":\"rm -rf build\"}"}},
        )

        self._journaler().poll()

        approval = self.store.list_events()[0]["payload"]
        self.assertEqual(approval["request_id"], "call_abc")
        self.assertEqual(approval["tool"], "Bash")
        self.assertEqual(approval["signature"], "Bash:{\"command\":\"rm -rf build\"}")
        self.assertEqual(approval["session_rule"]["rule_type"], "exact_tool_call")


class StreamingTest(EventsTestCase):
    def test_text_deltas_are_live_only_but_round_text_is_journaled(self):
        live: list[dict] = []
        journaler = self._journaler()
        journaler.add_live_hook(live.append)
        self._write(
            {"ts": 1, "kind": "model.text_delta", "round": 1, "text": "При"},
            {"ts": 2, "kind": "model.text_delta", "round": 1, "text": "вет"},
            {"ts": 3, "kind": "model.round_response", "round": 1, "text_full": "Привет", "usage": {}, "latency_ms": 10},
        )

        journaler.poll()

        journaled_kinds = self._kinds()
        self.assertNotIn("model.text_delta", journaled_kinds)
        self.assertIn("model.round_response", journaled_kinds)
        round_payload = [e for e in self.store.list_events() if e["kind"] == "model.round_response"][0]["payload"]
        self.assertEqual(round_payload["text"], "Привет")
        live_kinds = [event["kind"] for event in live]
        self.assertEqual(live_kinds.count("model.text_delta"), 2)

    def test_memory_write_directive_is_not_a_success_confirmation(self):
        frame = normalize_trace_event(
            {
                "ts": 4,
                "kind": "model.round_response",
                "round": 1,
                "text_full": (
                    "Запрос передан на проверку.\n"
                    "[[MEMORY_WRITE]]\nscope: long\nfact: удалить факт\n[[/MEMORY_WRITE]]"
                ),
            },
            task_id=self.task_id,
            session_id=self.session_id,
        )

        self.assertEqual(frame["payload"]["text"], "Запрос передан на проверку.")
        self.assertNotIn("Память обновлена", frame["payload"]["text"])
        self.assertNotIn("MEMORY_WRITE", frame["payload"]["text"])

    def test_reasoning_deltas_are_coalesced_into_one_event(self):
        live: list[dict] = []
        journaler = self._journaler()
        journaler.add_live_hook(live.append)
        self._write(
            {"ts": 1, "kind": "model.reasoning_delta", "round": 1, "text": "Ду"},
            {"ts": 2, "kind": "model.reasoning_delta", "round": 1, "text": "маю"},
            {"ts": 3, "kind": "model.text_delta", "round": 1, "text": "Ответ"},
        )

        journaler.poll()

        reasoning = [e for e in self.store.list_events() if e["kind"] == "model.reasoning"]
        self.assertEqual(len(reasoning), 1)
        self.assertEqual(reasoning[0]["payload"]["text"], "Думаю")
        self.assertEqual([event["kind"] for event in live].count("model.reasoning_delta"), 2)

    def test_reasoning_is_flushed_when_task_finishes(self):
        journaler = self._journaler(
            tasks=[{"task_id": self.task_id, "status": "ok", "metadata": {}, "created_at": 0}]
        )
        self._write(
            {"ts": 1, "kind": "model.reasoning_delta", "round": 1, "text": "хвост"},
            {"ts": 2, "kind": "task.finish", "status": "ok", "rounds": 1, "tool_calls_count": 0, "output_preview": "готово"},
        )

        journaler.poll()

        kinds = self._kinds()
        self.assertIn("model.reasoning", kinds)
        self.assertIn("task.finish", kinds)


class IdempotencyTest(EventsTestCase):
    def test_second_journaler_does_not_duplicate_events(self):
        self._write(
            {"ts": 1, "kind": "task.start", "model": "m", "current_session": self.session_id},
            {"ts": 2, "kind": "tool.request_batch", "round": 1, "tools": [{"id": "c", "name": "Bash", "input": {}}]},
        )
        self.assertEqual(self._journaler().poll(), 2)

        restarted = self._journaler()

        self.assertEqual(restarted.poll(), 0)
        self.assertEqual(len(self.store.list_events(limit=50)), 2)

    def test_same_journaler_reads_only_new_lines(self):
        self._write({"ts": 1, "kind": "task.start", "model": "m", "current_session": self.session_id})
        journaler = self._journaler()
        journaler.poll()

        self._write({"ts": 2, "kind": "task.finish", "status": "ok", "rounds": 1, "tool_calls_count": 0, "output_preview": "ok"})
        appended = journaler.poll()

        self.assertEqual(appended, 1)
        self.assertEqual(self._kinds(), ["task.start", "task.finish"])

    def test_partial_last_line_is_retried(self):
        self._write({"ts": 1, "kind": "task.start", "model": "m", "current_session": self.session_id})
        with self.trace.open("a", encoding="utf-8") as handle:
            handle.write('{"ts": 2, "kind": "task.fin')
        journaler = self._journaler()
        journaler.poll()

        with self.trace.open("a", encoding="utf-8") as handle:
            handle.write('ish", "status": "ok"}\n')
        appended = journaler.poll()

        self.assertEqual(appended, 1)
        self.assertEqual(self._kinds(), ["task.start", "task.finish"])


class StatusChangeTest(EventsTestCase):
    def test_failure_detail_is_emitted_even_when_trace_already_finished(self):
        tasks = [{
            "task_id": self.task_id,
            "status": "processing",
            "metadata": {"session": self.session_id},
            "created_at": 0,
            "error_message": "",
        }]
        journaler = self._journaler(tasks=tasks)
        journaler.poll()
        self._write({"ts": 9, "kind": "task.finish", "status": "fail", "rounds": 1,
                     "tool_calls_count": 1, "output_preview": "x"})
        journaler.poll()

        tasks[0]["status"] = "fail"
        tasks[0]["error_message"] = "approval recovery stopped after the bounded retry"
        journaler.poll()
        journaler.poll()

        errors = [event for event in self.store.list_events() if event["kind"] == "task.error"]
        self.assertEqual(len(errors), 1)
        self.assertEqual(errors[0]["payload"]["task_id"], self.task_id)
        self.assertEqual(
            errors[0]["payload"]["error_message"],
            "approval recovery stopped after the bounded retry",
        )
        statuses = [event for event in self.store.list_events() if event["kind"] == "task.status"]
        self.assertEqual([event["payload"]["status"] for event in statuses], [])

    def test_changed_error_message_emits_a_second_task_error(self):
        tasks = [{
            "task_id": self.task_id,
            "status": "processing",
            "metadata": {"session": self.session_id},
            "created_at": 0,
            "error_message": "",
        }]
        journaler = self._journaler(tasks=tasks)
        journaler.poll()

        tasks[0]["status"] = "fail"
        tasks[0]["error_message"] = "first infrastructure error"
        journaler.poll()
        journaler.poll()

        tasks[0]["error_message"] = "second infrastructure error"
        journaler.poll()

        errors = [event for event in self.store.list_events() if event["kind"] == "task.error"]
        self.assertEqual(
            [event["payload"]["error_message"] for event in errors],
            ["first infrastructure error", "second infrastructure error"],
        )

    def test_silent_statuses_are_not_reported(self):
        tasks = [{"task_id": self.task_id, "status": "queued", "metadata": {}, "created_at": 0}]
        journaler = self._journaler(tasks=tasks)

        journaler.poll()
        tasks[0]["status"] = "processing"
        journaler.poll()

        self.assertEqual(self._kinds(), [])

    def test_final_status_is_reported(self):
        tasks = [{"task_id": self.task_id, "status": "processing", "metadata": {}, "created_at": 0}]
        journaler = self._journaler(tasks=tasks)
        journaler.poll()

        tasks[0]["status"] = "ok"
        journaler.poll()

        statuses = [e["payload"]["status"] for e in self.store.list_events() if e["kind"] == "task.status"]
        self.assertEqual(statuses, ["ok"])

    def test_fast_success_is_reported_on_first_poll_without_replay_duplicate(self):
        tasks = [{"task_id": self.task_id, "status": "ok", "metadata": {}, "created_at": 0}]
        self._journaler(tasks=tasks).poll()
        self._journaler(tasks=tasks).poll()
        statuses = [e["payload"]["status"] for e in self.store.list_events() if e["kind"] == "task.status"]
        self.assertEqual(statuses, ["ok"])

    def test_persisted_success_is_emitted_after_executor_finish_once(self):
        tasks = [{"task_id": self.task_id, "status": "processing", "metadata": {}, "created_at": 0}]
        journaler = self._journaler(tasks=tasks)
        journaler.poll()
        self._write({"ts": 9, "kind": "task.finish", "status": "ok"})
        journaler.poll()
        self.assertEqual([e for e in self.store.list_events() if e["kind"] == "task.status"], [])
        tasks[0]["status"] = "ok"
        journaler.poll()
        journaler.poll()
        statuses = [e["payload"]["status"] for e in self.store.list_events() if e["kind"] == "task.status"]
        self.assertEqual(statuses, ["ok"])

    def test_final_status_is_not_repeated_after_task_finish(self):
        tasks = [{"task_id": self.task_id, "status": "processing", "metadata": {}, "created_at": 0}]
        journaler = self._journaler(tasks=tasks)
        journaler.poll()
        self._write({"ts": 9, "kind": "task.finish", "status": "fail", "rounds": 1,
                     "tool_calls_count": 1, "output_preview": "x"})
        journaler.poll()

        tasks[0]["status"] = "fail"
        journaler.poll()

        self.assertEqual([e["kind"] for e in self.store.list_events() if e["kind"] == "task.status"], [])

    def test_waiting_approval_is_reported(self):
        tasks = [{"task_id": self.task_id, "status": "processing", "metadata": {}, "created_at": 0}]
        journaler = self._journaler(tasks=tasks)
        journaler.poll()

        tasks[0]["status"] = "waiting_approval"
        journaler.poll()

        statuses = [e["payload"]["status"] for e in self.store.list_events() if e["kind"] == "task.status"]
        self.assertEqual(statuses, ["waiting_approval"])

    def test_status_change_emits_event(self):
        tasks = [{"task_id": self.task_id, "status": "queued", "metadata": {}, "created_at": 0}]
        journaler = self._journaler(tasks=tasks)

        journaler.poll()
        self.assertEqual(self._kinds(), [])

        tasks[0]["status"] = "cancelling"
        journaler.poll()
        tasks[0]["status"] = "fail"
        journaler.poll()

        statuses = [e["payload"]["status"] for e in self.store.list_events() if e["kind"] == "task.status"]
        self.assertEqual(statuses, ["cancelling", "fail"])

    def test_multiple_status_changes_are_all_recorded(self):
        tasks = [{"task_id": self.task_id, "status": "queued", "metadata": {}, "created_at": 0}]
        journaler = self._journaler(tasks=tasks)
        journaler.poll()

        for status in ("waiting_approval", "cancelling", "fail"):
            tasks[0]["status"] = status
            journaler.poll()

        statuses = [e["payload"]["status"] for e in self.store.list_events() if e["kind"] == "task.status"]
        self.assertEqual(statuses, ["waiting_approval", "cancelling", "fail"])


class SteerDeliveryEventTest(EventsTestCase):
    def test_steer_delivered_reaches_the_mobile_journal(self):
        """Trace delivery acknowledgements must reach the client.

        Otherwise a pending message remains queued in the interface after the task
        has already consumed it in an active turn.
        """
        live: list[dict] = []
        journaler = self._journaler()
        journaler.add_live_hook(live.append)
        self._write(
            {
                "ts": 1,
                "kind": "task.start",
                "model": "m",
                "filesystem_mode": "bwrap",
                "current_session": self.session_id,
            },
            {
                "ts": 2,
                "kind": "steer.delivered",
                "round": 2,
                "message_id": "m1",
                "text_preview": "5 + 47 ?",
                "input_files": 0,
            },
        )

        journaler.poll()

        delivered = [e for e in self.store.list_events() if e["kind"] == "steer.delivered"]
        self.assertEqual(len(delivered), 1)
        self.assertEqual(delivered[0]["payload"]["message_id"], "m1")
        self.assertEqual(delivered[0]["session_id"], self.session_id)
        self.assertEqual(delivered[0]["task_id"], self.task_id)
        self.assertIn("steer.delivered", [event["kind"] for event in live])


class ClassifyToolStatusTest(unittest.TestCase):
    def test_structured_tool_error_is_error_without_matching_human_text(self):
        self.assertEqual(classify_tool_status('{"error":"browser_needs_owner","reason":"manual_login"}'), "error")
        self.assertEqual(classify_tool_status('{"error":null,"open":true}'), "done")
        self.assertEqual(classify_tool_status('{"text":"error is just document text"}'), "done")

    def test_exit_code_is_exit(self):
        self.assertEqual(classify_tool_status("12 passed (exit code: 0)"), "exit")

    def test_error_prefix_is_error(self):
        self.assertEqual(classify_tool_status("Error: boom"), "error")

    def test_timeout_is_error(self):
        self.assertEqual(classify_tool_status("command timed out after 30s"), "error")

    def test_plain_output_is_done(self):
        self.assertEqual(classify_tool_status("hello"), "done")


class NormalizeUnknownKindTest(unittest.TestCase):
    def test_unknown_kind_passes_through(self):
        frame = normalize_trace_event({"ts": 1, "kind": "brand.new_event", "detail": "x"})

        self.assertIsNotNone(frame)
        self.assertEqual(frame["kind"], "brand.new_event")
        self.assertEqual(frame["payload"]["detail"], "x")

    def test_event_without_kind_is_ignored(self):
        self.assertIsNone(normalize_trace_event({"ts": 1}))


class MemoryWriteNoteTest(unittest.TestCase):
    def _round_text(self, text_full: str) -> str:
        frame = normalize_trace_event(
            {
                "ts": 1,
                "kind": "model.round_response",
                "round": 1,
                "text_full": text_full,
                "usage": {},
                "latency_ms": 1,
            }
        )
        return frame["payload"]["text"]

    def test_single_line_memory_block_is_stripped_without_success_note(self):
        text = self._round_text(
            "Готово.\n[[MEMORY_WRITE]] scope: long fact: Вес Игоря 106.7 кг [[/MEMORY_WRITE]]"
        )

        self.assertEqual(text, "Готово.")
        self.assertNotIn("MEMORY_WRITE", text)
        self.assertNotIn("Память обновлена", text)

    def test_multiline_memory_block_is_stripped_without_success_note(self):
        text = self._round_text(
            "Готово.\n[[MEMORY_WRITE]]\nscope: daily\nfact: Съел харчо\n[[/MEMORY_WRITE]]"
        )

        self.assertEqual(text, "Готово.")
        self.assertNotIn("MEMORY_WRITE", text)
        self.assertNotIn("Память обновлена", text)

    def test_two_memory_blocks_are_stripped_without_success_note(self):
        text = self._round_text(
            "[[MEMORY_WRITE]] scope: long fact: Факт один [[/MEMORY_WRITE]] "
            "[[MEMORY_WRITE]] scope: long fact: Факт два [[/MEMORY_WRITE]]"
        )

        self.assertEqual(text, "")
        self.assertNotIn("Память обновлена", text)

    def test_round_text_without_memory_block_is_unchanged(self):
        text = self._round_text("Обычный ответ без служебных блоков.")

        self.assertEqual("Обычный ответ без служебных блоков.", text)


if __name__ == "__main__":
    unittest.main()
