import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from factory.core import cc_backend_executor as executor
from factory.core.prompt_assembly import SESSION_SUMMARY_HEADINGS
from factory.core.session_compaction import summarize_history
from factory.core.session_history import build_working_history
from factory.core.mobile_bridge.events import normalize_trace_event

SUMMARY = "\n".join(heading + "\n- carried evidence" for heading in SESSION_SUMMARY_HEADINGS)


class SessionMemoryTest(unittest.TestCase):
    def test_history_includes_tool_arguments_results_and_more_than_twenty_messages(self):
        entries = [{"role": "user" if i % 2 == 0 else "assistant", "text": str(i), "task_id": "t", "ts": i, "message_id": str(i)} for i in range(40)]
        events = [
            {"seq": 1, "task_id": "t", "kind": "tool.request_batch", "payload": {"tools": [{"id": "a", "name": "Bash", "input": {"command": "pytest"}}]}},
            {"seq": 2, "task_id": "t", "kind": "tool.result", "payload": {"tool_use_id": "a", "tool_name": "Bash", "tool_input": {"command": "pytest"}, "context_output": "3 failed", "status": "error"}},
        ]
        history = build_working_history(entries, events)
        self.assertEqual(len(history), 41)
        tool = next(item for item in history if item["history_id"] == "tool:t:a")
        self.assertIn("pytest", tool["content"])
        self.assertIn("3 failed", tool["content"])
        self.assertEqual(history[0]["content"], "0")

    def test_repeated_compaction_does_not_reintroduce_covered_history(self):
        entries = [{"role": "user", "text": str(i), "history_id": str(i)} for i in range(4)]
        events = [
            {"seq": 10, "kind": "compaction.completed", "payload": {"summary_text": "first", "covered_history_ids": ["0"]}},
            {"seq": 20, "kind": "compaction.completed", "payload": {"summary_text": "combined", "covered_history_ids": ["1", "summary:10"]}},
        ]
        history = build_working_history(entries, events)
        self.assertEqual([item["history_id"] for item in history], ["summary:20", "2", "3"])
        self.assertIn("combined", history[0]["content"])

    def test_partial_summary_never_replaces_history(self):
        history = build_working_history([{"role": "user", "text": "original"}], [
            {"seq": 1, "kind": "compaction.progress", "payload": {"summary_text": "partial"}},
            {"seq": 2, "kind": "compaction.failed", "payload": {"reason": "error"}},
        ])
        self.assertEqual(history[0]["content"], "original")

    def test_summary_chunks_carry_previous_memory_and_do_not_drop_evidence(self):
        prompts = []
        def request(prompt):
            prompts.append(prompt)
            return SUMMARY
        result = summarize_history([{"role": "user", "content": "markerA" + "x" * 26000 + "markerB"}], request=request, input_budget_tokens=10000)
        self.assertGreater(len(prompts), 1)
        self.assertIn("markerA", prompts[0])
        self.assertIn("markerB", prompts[-1])
        self.assertIn(SUMMARY, prompts[1])
        self.assertEqual(result, SUMMARY)

    def test_invalid_or_empty_summary_is_rejected(self):
        for candidate in ("", "done", "## Цель\nonly one section"):
            with self.subTest(candidate=candidate), self.assertRaises(ValueError):
                summarize_history([{"role": "user", "content": "old"}], request=lambda _: candidate, input_budget_tokens=10000)

    def test_compaction_events_preserve_full_summary_for_replay(self):
        text = SUMMARY + "x" * 9000
        normalized = normalize_trace_event({"kind": "compaction.completed", "summary_text": text, "covered_history_ids": ["a"]})
        self.assertEqual(normalized["payload"]["summary_text"], text)

    def test_tail_budget_does_not_pull_an_oversized_previous_turn_back_in(self):
        from factory.core.session_compaction import build_compaction_dry_run_plan
        messages = [
            {"role": "user", "content": "old " + "x" * 50000},
            {"role": "assistant", "content": "short answer"},
            {"role": "user", "content": "current"},
        ]
        plan = build_compaction_dry_run_plan(messages=messages, keep_tail_tokens=1000)
        self.assertEqual(plan.summarize_message_indexes, [0, 1])
        self.assertEqual(plan.preserve_message_indexes, [2])

    def test_unresolved_guard_blocks_compaction_until_allowed_verdict(self):
        from factory.core.session_compaction import read_compaction_guards, build_compaction_dry_run_plan
        messages = [{"role": "user", "content": "old"}, {"role": "assistant", "content": "answer"}, {"role": "user", "content": "new"}]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "cc_backend_trace.jsonl"
            path.write_text(json.dumps({"kind": "post_turn.retry_required"}) + "\n")
            plan = build_compaction_dry_run_plan(messages=messages, trace_events=read_compaction_guards(directory), keep_tail_messages=1)
            self.assertTrue(plan.blocked)
            with path.open("a") as handle:
                handle.write(json.dumps({"kind": "post_turn.verdict", "action": "completion_allowed"}) + "\n")
            plan = build_compaction_dry_run_plan(messages=messages, trace_events=read_compaction_guards(directory), keep_tail_messages=1)
            self.assertTrue(plan.can_compact)

    def test_executed_tool_clears_only_its_own_pending_approval(self):
        from factory.core.session_compaction import read_compaction_guards
        with tempfile.TemporaryDirectory() as directory:
            events = [
                {"kind": "tool.approval_required", "tool_use_id": "a", "approval_status": "pending"},
                {"kind": "tool.approval_required", "tool_use_id": "b", "approval_status": "pending"},
                {"kind": "tool.result", "tool_use_id": "a", "status": "ok"},
            ]
            (Path(directory) / "cc_backend_trace.jsonl").write_text("\n".join(json.dumps(e) for e in events))
            pending = [e["tool_use_id"] for e in read_compaction_guards(directory) if e["kind"] == "tool.approval_required"]
            self.assertEqual(pending, ["b"])

    def test_truncated_summary_splits_input_without_losing_evidence(self):
        prompts = []
        def request(prompt):
            prompts.append(prompt)
            piece = prompt.split("<conversation>\n", 1)[1].split("\n</conversation>", 1)[0]
            if len(piece) > 9000:
                raise ValueError("summary_output_truncated")
            return SUMMARY
        result = summarize_history([{"role": "user", "content": "markerA" + "x" * 26000 + "markerB"}], request=request, input_budget_tokens=20000)
        self.assertEqual(result, SUMMARY)
        self.assertTrue(any("markerA" in p for p in prompts))
        self.assertTrue(any("markerB" in p for p in prompts))
        self.assertTrue(any(SUMMARY in p for p in prompts[1:]))

    def test_malformed_summary_is_repaired_before_commit(self):
        responses = iter(["incorrect", SUMMARY])
        prompts = []
        def request(prompt):
            prompts.append(prompt)
            return next(responses)
        self.assertEqual(summarize_history([{"role": "user", "content": "evidence"}], request=request, input_budget_tokens=20000), SUMMARY)
        self.assertIn("summary_structure_invalid", prompts[-1])

    def _run_executor(self, api_format, fail_summary=False, fail_persistence=False, truncated_first=False, oversized=False, context_window=20000):
        payloads = []
        def response(text, summary=False):
            if api_format == "openai":
                return {"choices": [{"message": {"role": "assistant", "content": text}, "finish_reason": "stop"}], "usage": {"prompt_tokens": 2, "completion_tokens": 3}}, 1
            return {"content": [{"type": "text", "text": text}], "stop_reason": "end_turn", "usage": {"input_tokens": 2, "output_tokens": 3}}, 1
        def post(url, payload, headers, *args, **kwargs):
            payloads.append(json.loads(json.dumps(payload)))
            body, latency = response("bad summary" if fail_summary else SUMMARY, True)
            if truncated_first and len(payloads) == 1:
                if api_format == "openai":
                    body["choices"][0]["finish_reason"] = "length"
                else:
                    body["stop_reason"] = "max_tokens"
            return body, latency
        def main(url, payload, headers, *args, **kwargs):
            payloads.append(json.loads(json.dumps(payload)))
            return response("Продолжение")
        with tempfile.TemporaryDirectory() as directory:
            history = [
                {"role": "user", "content": "verified_error " + "x" * (100000 if oversized else (180000 if context_window == 100000 else 38000)), "history_id": "old-user"},
                {"role": "assistant", "content": "failed approach " + "y" * (200000 if context_window == 100000 else 28000), "history_id": "old-answer"},
            ]
            def anthropic_post(url, payload, headers, *args, **kwargs):
                return post(url, payload, headers, *args, **kwargs) if "tools" not in payload else main(url, payload, headers, *args, **kwargs)
            with patch.object(executor, "_resolve_backend_context_window", return_value=(context_window, "test")), patch.object(executor, "_http_post", side_effect=post if api_format == "openai" else anthropic_post), patch.object(executor, "_http_post_streaming_openai", side_effect=main):
                if fail_persistence:
                    with patch.object(executor, "write_manual_compaction_artifact", side_effect=OSError("disk full")):
                        result = executor.execute_cc_backend_task(base_url="http://localhost/v1", model="deepseek-v4.1-flash", system_prompt="test", user_message="continue", workdir=directory, api_format=api_format, history_messages=history, max_tokens=1024, enabled_tools=[])
                else:
                    result = executor.execute_cc_backend_task(base_url="http://localhost/v1", model="deepseek-v4.1-flash", system_prompt="test", user_message="continue", workdir=directory, api_format=api_format, history_messages=history, max_tokens=1024, enabled_tools=[])
            trace = [json.loads(line) for line in (Path(directory) / "cc_backend_trace.jsonl").read_text().splitlines()]
        return result, payloads, trace

    def test_executor_compacts_both_provider_formats_with_usable_memory(self):
        for api in ("openai", "anthropic"):
            with self.subTest(api=api):
                result, payloads, trace = self._run_executor(api)
                self.assertEqual(result["status"], "ok")
                completed = next(event for event in trace if event["kind"] == "compaction.completed")
                self.assertTrue(completed["summary_text"].startswith(SUMMARY))
                self.assertIn("_history.jsonl", completed["summary_text"])
                self.assertEqual(completed["covered_history_ids"], ["old-answer", "old-user"])
                self.assertIn("## Цель", json.dumps(payloads[-1], ensure_ascii=False))
                self.assertIn("carried evidence", json.dumps(payloads[-1]))
                self.assertNotIn("x" * 1000, json.dumps(payloads[-1]))
                self.assertGreater(result["usage"]["prompt"], 2)

    def test_executor_summary_failure_preserves_original_messages(self):
        for persist in (False, True):
            with self.subTest(persist=persist):
                result, payloads, trace = self._run_executor("openai", fail_summary=not persist, fail_persistence=persist)
                self.assertEqual(result["status"], "ok")
                self.assertTrue(any(e["kind"] == "compaction.failed" for e in trace))
                self.assertFalse(any(e["kind"] == "compaction.completed" for e in trace))
                self.assertIn("x" * 1000, json.dumps(payloads[-1]))


    def test_executor_recovers_output_limit_for_both_api_formats(self):
        for api in ("openai", "anthropic"):
            with self.subTest(api=api):
                result, payloads, trace = self._run_executor(api, truncated_first=True, context_window=100000)
                self.assertEqual(result["status"], "ok")
                self.assertTrue(any(e["kind"] == "compaction.completed" for e in trace))
                if api == "openai":
                    self.assertEqual(payloads[0]["thinking"], {"type": "disabled"})
                    self.assertNotIn("thinking", payloads[-1])
                else:
                    self.assertNotIn("thinking", payloads[0])
                self.assertEqual(payloads[0]["messages"], payloads[1]["messages"])
                self.assertEqual(payloads[0]["max_tokens"], 8192)
                self.assertEqual(payloads[1]["max_tokens"], 16384)

    def test_unrecoverable_compaction_never_sends_oversized_original_context(self):
        result, payloads, trace = self._run_executor("openai", fail_summary=True, oversized=True)
        self.assertEqual(result["status"], "fail")
        self.assertEqual(result["failure_reason"], "compaction_required")
        self.assertTrue(all("tools" not in p for p in payloads))
        self.assertFalse(any(e["kind"] == "compaction.completed" for e in trace))
        self.assertTrue(any(e["kind"] == "task.finish" and e.get("reason") == "compaction_required" for e in trace))
