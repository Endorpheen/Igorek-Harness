"""Incomplete thinking must recover or fail, never masquerade as completed work."""
import json
from collections import deque

import pytest

from factory.core import cc_backend_executor as executor
from factory.core.cc_backend_tools import get_tool_schemas


def body(api_format, text, *, thinking=False, tool=None):
    if api_format == 'openai':
        message = {'role': 'assistant', 'content': text}
        if thinking: message['reasoning_content'] = 'Native fixture reasoning.'
        if tool: message['tool_calls'] = [{'id': 'fixture-write', 'type': 'function', 'function': {'name': 'Write', 'arguments': json.dumps(tool)}}]
        return {'choices': [{'message': message, 'finish_reason': 'tool_calls' if tool else 'stop'}], 'usage': {}}
    blocks = [{'type': '__reasoning', 'text': 'Native fixture reasoning.'}] if thinking else []
    if text: blocks.append({'type': 'text', 'text': text})
    if tool: blocks.append({'type': 'tool_use', 'id': 'fixture-write', 'name': 'Write', 'input': tool})
    return {'content': blocks, 'usage': {}}


@pytest.fixture
def execute(tmp_path, monkeypatch):
    monkeypatch.setenv('IGOREK_FACTORY_HOME', str(tmp_path / 'home'))
    work = tmp_path / 'workspace';work.mkdir()
    artifact = tmp_path / 'artifacts';artifact.mkdir()
    def run(api_format, responses, *, tools=False, before_request=None):
        queue, requests = deque(responses), []
        def post(url, payload, *args, **kwargs):
            if before_request: before_request(len(requests), work)
            requests.append(payload)
            assert queue, 'Unexpected unbounded model retry'
            return queue.popleft(), 1
        monkeypatch.setattr(executor, '_http_post', post)
        monkeypatch.setattr(executor, '_http_post_streaming_openai', post)
        schemas = [x for x in get_tool_schemas() if x['name'] == 'Write'] if tools else []
        result = executor.execute_cc_backend_task(base_url='http://127.0.0.1:1', api_format=api_format, model='fixture', system_prompt='fixture', user_message='Write the fixture result' if tools else 'Respond to fixture', workdir=str(work), artifact_workdir=str(artifact), enabled_tools=schemas, metadata={'approved_session_tools': ['Write'], 'model_context_window': 65536})
        events = [json.loads(line) for line in (artifact / 'cc_backend_trace.jsonl').read_text().splitlines()]
        return result, requests, events, work
    return run


@pytest.mark.parametrize('api_format', ['openai', 'anthropic'])
@pytest.mark.parametrize('text', ['<think>Still preparing the fixture', '<think>First.</think><think>Second.'])
def test_unclosed_thinking_retries_before_success(execute, api_format, text):
    result, requests, events, work = execute(api_format, [body(api_format, text, thinking=True), body(api_format, 'Actual final answer.')])
    assert result['status'] == 'ok' and result['output_text'] == 'Actual final answer.'
    assert len(requests) == 2 and result['tool_calls_count'] == 0
    assert any(x.get('reason') == 'reasoning_only_response' for x in events)
    assert not list(work.iterdir())


@pytest.mark.parametrize('api_format', ['openai', 'anthropic'])
def test_repeated_unclosed_thinking_is_explicit_failure(execute, api_format):
    result, requests, events, work = execute(api_format, [body(api_format, '<think>First unfinished thought'), body(api_format, '<think>Another unfinished thought')])
    assert result['status'] == 'fail' and result['failure_reason'] == 'reasoning_only_response_repeated'
    assert len(requests) == 2 and result['tool_calls_count'] == 0
    assert not list(work.iterdir())
    assert not any(x['kind'] == 'model.round_response' and '<think>' in x.get('text_full', '') for x in events)


@pytest.mark.parametrize('api_format', ['openai', 'anthropic'])
def test_recovery_only_reports_file_success_after_real_write(execute, api_format):
    tool = {'file_path': 'result.txt', 'content': 'verified fixture content'}
    def before(index, work):
        assert (work / 'result.txt').exists() is (index == 2)
    result, requests, events, work = execute(api_format, [body(api_format, '<think>Drafting tests without executing anything'), body(api_format, '', tool=tool), body(api_format, 'Created result.txt.')], tools=True, before_request=before)
    assert result['status'] == 'ok' and result['tool_calls_count'] == 1
    assert (work / 'result.txt').read_text() == 'verified fixture content'
    assert len(requests) == 3
    assert sum(x['kind'] == 'tool.result' for x in events) == 1


@pytest.mark.parametrize('api_format', ['openai', 'anthropic'])
@pytest.mark.parametrize('text', ['Ordinary final answer.', 'An example: `<think>literal</think>`.', '```xml\n<think>literal</think>\n```'])
def test_normal_answers_and_code_examples_do_not_trigger_retries(execute, api_format, text):
    result, requests, events, work = execute(api_format, [body(api_format, text)])
    assert result['status'] == 'ok' and result['output_text'] == text
    assert len(requests) == 1
    assert not any(x.get('reason') == 'reasoning_only_response' for x in events)
