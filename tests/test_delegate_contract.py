import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from factory.core import cc_backend_executor as executor, delegate_tool
from factory.core.cc_backend_tools import get_tool_schemas


class DelegateContractTest(unittest.TestCase):
    def capture(self, args, **providers):
        captured = []
        def child(**kwargs):
            captured.append(kwargs)
            return {"status": "ok", "output_text": "done", "tool_calls_count": 0}
        with tempfile.TemporaryDirectory() as tmp, patch.object(executor, "execute_cc_backend_task", child):
            result = json.loads(delegate_tool.delegate_task(args, workdir=tmp, **providers))
        return captured, result

    def test_goal_context_groups_and_parent_policy_reach_child(self):
        allowed = [s for s in get_tool_schemas() if s['name'] in {'Bash', 'Read', 'Write'}]
        provider = lambda: False
        calls, result = self.capture({'goal': 'Answer briefly', 'context': 'Compute 17 * 23', 'toolsets': ['terminal', 'file']},
            delegate_available_tools=allowed, approvals_enabled_provider=provider,
            delegate_metadata={'must_write_skills': ['parent-skill'], 'approved_tools': ['Write']})
        self.assertEqual(result['status'], 'ok')
        child = calls[0]
        self.assertIn('Compute 17 * 23', child['user_message'])
        self.assertEqual({s['name'] for s in child['enabled_tools']}, {'Bash', 'Read', 'Write'})
        self.assertNotIn('must_write_skills', child['metadata'])
        self.assertIs(child['approvals_enabled_provider'], provider)
        self.assertEqual(child['metadata']['approved_tools'], ['Write'])

    def test_explicit_names_defaults_unknown_and_unavailable_groups(self):
        allowed = [s for s in get_tool_schemas() if s['name'] == 'Bash']
        for selection in (None, ['Bash'], ['terminal'], ['all'], ['*']):
            with self.subTest(selection=selection):
                calls, _ = self.capture({'goal': 'test', 'toolsets': selection}, delegate_available_tools=allowed)
                self.assertEqual([s['name'] for s in calls[0]['enabled_tools']], ['Bash'])
        for selection, reason in ((['typo'], 'invalid_subagent_tools'), (['file'], 'unavailable_subagent_tools')):
            calls, result = self.capture({'goal': 'test', 'toolsets': selection}, delegate_available_tools=allowed)
            self.assertEqual(calls, [])
            self.assertEqual(result['status'], 'fail')
            self.assertEqual(result['failure_reason'], reason)

    def response(self, text=None, calls=None):
        return {'choices': [{'message': {'role': 'assistant', 'content': text, 'tool_calls': calls or []},
                             'finish_reason': 'tool_calls' if calls else 'stop'}], 'usage': {}}

    def test_real_executor_runs_native_bash_and_write_with_group_selection(self):
        seen_payloads = []
        def http(url, payload, *_args, **_kwargs):
            seen_payloads.append(payload)
            user = next(m['content'] for m in payload['messages'] if m['role'] == 'user')
            self.assertIn('Write report.txt and run python', str(user))
            tools = {t['function']['name'] for t in payload['tools']}
            self.assertIn('Bash', tools)
            self.assertIn('Write', tools)
            if len(seen_payloads) == 1:
                return self.response(calls=[
                    {'id': 'write', 'type': 'function', 'function': {'name': 'Write', 'arguments': json.dumps({'file_path': str(next(Path(tmp).glob('.delegate_*')) / 'report.txt'), 'content': 'checked'})}},
                    {'id': 'shell', 'type': 'function', 'function': {'name': 'Bash', 'arguments': json.dumps({'command': 'python3 -c "print(17 * 23)"'})}},
                ]), 1
            return self.response(text='Completed'), 1
        with tempfile.TemporaryDirectory() as tmp, \
                patch.object(executor, '_http_post_streaming_openai', http), \
                patch.object(executor, '_resolve_backend_context_window', return_value=(32768, 'test')):
            result = json.loads(delegate_tool.delegate_task({'goal': 'Execute the task', 'context': 'Write report.txt and run python',
                'toolsets': ['terminal', 'file']}, workdir=tmp, artifact_workdir=tmp, api_format='openai',
                base_url='http://127.0.0.1:1', model='test', approvals_enabled_provider=lambda: False,
                checkpoint_config={'enabled': False}))
            reports = list(Path(tmp).glob('.delegate_*/report.txt'))
            self.assertEqual([p.read_text() for p in reports], ['checked'])
            trace = [json.loads(line) for line in (Path(tmp) / 'cc_backend_trace.jsonl').read_text().splitlines()]
            bash = [e for e in trace if e.get('kind') == 'tool.result' and e.get('tool_name') == 'Bash']
            self.assertIn('391', bash[0]['context_output'])
        self.assertEqual(result['status'], 'ok')
        self.assertEqual(result['tool_calls_count'], 2)

    def test_real_dsml_fixture_fails_after_bounded_retry_but_native_recovery_succeeds(self):
        text = (Path(__file__).parent / 'fixtures/tool_salvage/deepseek_dsml_unexecuted.txt').read_text()
        for recover in (False, True):
            with self.subTest(recover=recover), tempfile.TemporaryDirectory() as tmp:
                rounds = []
                def http(url, payload, *_args, **_kwargs):
                    rounds.append(payload)
                    if recover and len(rounds) == 2:
                        return self.response(calls=[{'id': 'recovered', 'type': 'function', 'function': {'name': 'Bash', 'arguments': '{"command":"pwd"}'}}]), 1
                    if recover and len(rounds) == 3:
                        return self.response(text='Completed'), 1
                    return self.response(text=text), 1
                with patch.object(executor, '_http_post_streaming_openai', http), \
                        patch.object(executor, '_resolve_backend_context_window', return_value=(32768, 'test')):
                    result = json.loads(delegate_tool.delegate_task({'goal': 'Test tools', 'toolsets': ['terminal']}, workdir=tmp,
                        artifact_workdir=tmp, api_format='openai', base_url='http://127.0.0.1:1', model='test'))
                self.assertEqual(result['status'], 'ok' if recover else 'fail')
                self.assertEqual(result['tool_calls_count'], 1 if recover else 0)
                self.assertEqual(len(rounds), 3 if recover else 2)

    def test_quoted_dsml_report_completes_without_retry(self):
        text = (Path(__file__).parent / 'fixtures/tool_salvage/deepseek_dsml_quoted_report.txt').read_text()
        with tempfile.TemporaryDirectory() as tmp, \
                patch.object(executor, '_http_post_streaming_openai', return_value=(self.response(text=text), 1)) as http, \
                patch.object(executor, '_resolve_backend_context_window', return_value=(32768, 'test')):
            result = json.loads(delegate_tool.delegate_task({'goal': 'Report results', 'toolsets': ['terminal']},
                workdir=tmp, artifact_workdir=tmp, api_format='openai', base_url='http://127.0.0.1:1', model='test'))
            self.assertEqual(result['status'], 'ok')
            self.assertEqual(http.call_count, 1)
            self.assertEqual(result['tool_calls_count'], 0)
