"""Local completion recovery uses evidence and preserves cloud execution."""
import copy
import json
from collections import deque

import pytest

from factory.core import cc_backend_executor as e
from factory.core.completion_review import (
    CompletionRecovery, completion_review_enabled, parse_completion_review, review_transcript,
)
from factory.core.model_backend_capabilities import ModelBackendCapabilities
from factory.core.tool_result_outcome import ToolResultText


def response(api, text='', tool=None, *, limited=False):
    if api == 'openai':
        message = {'role': 'assistant', 'content': text}
        if tool:
            message['tool_calls'] = [{'id': tool['id'], 'type': 'function', 'function': {
                'name': tool['name'], 'arguments': json.dumps(tool['input']),
            }}]
        return {'choices': [{'message': message, 'finish_reason': 'length' if limited else 'tool_calls' if tool else 'stop'}],
                'usage': {'prompt_tokens': 2, 'completion_tokens': 3}}
    blocks = [{'type': 'text', 'text': text}] if text else []
    if tool:
        blocks.append({'type': 'tool_use', **tool})
    return {'content': blocks, 'usage': {'input_tokens': 2, 'output_tokens': 3},
            'stop_reason': 'max_tokens' if limited else 'tool_use' if tool else 'end_turn'}


def review(api, status='complete', requires=False, ids=()):
    return response(api, json.dumps({'status': status, 'requires_tools': requires, 'evidence_ids': list(ids)}))


def tool(name='Read', id='read-1', **args):
    return {'id': id, 'name': name, 'input': args or {'file_path': 'input.txt'}}


@pytest.fixture
def run(tmp_path, monkeypatch):
    monkeypatch.setenv('IGOREK_FACTORY_HOME', str(tmp_path / 'data'))
    monkeypatch.setenv('FACTORY_DATA_DIR', str(tmp_path / 'data'))
    monkeypatch.setenv('FACTORY_VAULT_ENABLED', 'false')
    monkeypatch.setattr(e, '_resolve_backend_context_window', lambda **k: (262144, 'fixture'))
    monkeypatch.setattr(e, 'task_should_stop', lambda _: False)
    monkeypatch.setattr(e, 'is_interrupt_requested', lambda: False)
    work, artifacts = tmp_path / 'work', tmp_path / 'artifacts'
    work.mkdir(); artifacts.mkdir()
    (work / 'input.txt').write_text('fixture')
    def execute(bodies, *, api='openai', backend='llama_cpp', url='http://127.0.0.1:1/v1',
                capability_url=None, user='В текущей странице добавь счётчик и нажми кнопку три раза.',
                names=('Read', 'desktop_browser'), raw=None, **kwargs):
        requests, calls, options, queue = [], [], [], deque(bodies)
        monkeypatch.setattr(e.LlamaImageInput, 'resolve', lambda _: ModelBackendCapabilities(
            backend, False, capability_url or url, 'fixture'))
        def post(url, payload, *args, **kw):
            requests.append(copy.deepcopy(payload)); options.append(kw)
            assert queue, 'Unexpected extra completion request'
            return queue.popleft(), 1
        def call(name, arguments, *args, **kw):
            calls.append((name, copy.deepcopy(arguments)))
            if name == 'Read':
                return ToolResultText('fixture input', True)
            return ToolResultText(raw or '{"value":3}', True)
        monkeypatch.setattr(e, '_http_post', post)
        monkeypatch.setattr(e, '_http_post_streaming_openai', post)
        monkeypatch.setattr(e, 'execute_tool', call)
        from factory.core.cc_backend_tools import get_tool_schemas
        from factory.core.desktop_browser import SCHEMA
        schemas = {item['name']: item for item in get_tool_schemas()}
        schemas['desktop_browser'] = SCHEMA
        result = e.execute_cc_backend_task(
            base_url=url, api_format=api, model='fixture', system_prompt='Fixture policy: no unrelated actions.',
            user_message=user, workdir=str(work), artifact_workdir=str(artifacts),
            enabled_tools=[schemas[name] for name in names],
            approvals_enabled_provider=kwargs.pop('approvals_enabled_provider', lambda: False),
            metadata={'model_context_window': 262144}, max_output_tokens=65536, **kwargs,
        )
        events = [json.loads(line) for line in (artifacts / 'cc_backend_trace.jsonl').read_text().splitlines()]
        return result, requests, calls, events, options
    return execute


@pytest.mark.parametrize('api', ['openai', 'anthropic'])
@pytest.mark.parametrize('promise', [
    'Три вызова console для регистрации сообщений с разными уровнями:',
    'Добавляю видимый виджет счётчика на страницу через console (DOM-элементы с кнопкой и обработчиком):',
    'I will perform the requested actions now.',
])
def test_preamble_recovers_in_same_task_before_success(run, api, promise):
    result, requests, calls, events, options = run([
        response(api, promise), review(api, 'continue', True),
        response(api, tool=tool('desktop_browser', 'console-1', action='console', expression='3')),
        response(api, 'Фактический результат проверен: 3.'), review(api, 'complete', True, ['console-1']),
    ], api=api)
    assert result['status'] == 'ok' and result['tool_calls_count'] == 1
    assert len(requests) == 5 and len(calls) == 1
    assert all(request['max_tokens'] == 65536 for request in requests)
    assert result['usage']['prompt'] == 10 and result['usage']['completion'] == 15
    assert sum(row['kind'] == 'post_turn.retry_required' and row.get('reason') == 'completion_blocked_unfinished_action' for row in events) == 1
    assert events[-1]['kind'] == 'task.finish' and events[-1]['status'] == 'ok'
    for index in (1, 4):
        assert 'tools' not in requests[index] and 'tool_choice' not in requests[index]
        assert not options[index].get('trace_dir')


def test_preparation_does_not_discharge_remaining_action_and_is_not_repeated(run):
    result, requests, calls, events, _ = run([
        response('openai', tool=tool()), response('openai', 'Прочитал. Теперь добавлю счётчик.'),
        review('openai', 'continue', True, ['read-1']),
        response('openai', tool=tool('desktop_browser', 'console-1', action='console', expression='3')),
        response('openai', 'Счётчик проверен.'), review('openai', 'complete', True, ['console-1']),
    ])
    assert result['status'] == 'ok' and [name for name, args in calls] == ['Read', 'desktop_browser']
    assert len(requests) == 6
    assert any(row.get('reason') == 'completion_blocked_unfinished_action' for row in events)
    assert 'do not repeat an already successful action' in json.dumps(requests[3])


@pytest.mark.parametrize('api', ['openai', 'anthropic'])
@pytest.mark.parametrize('backend,url,cap_url', [
    ('unknown', 'http://127.0.0.1:1/v1', None),
    ('cloud', 'http://127.0.0.1:1/v1', None),
    ('unknown', 'https://cloud.example.test/v1', None),
    ('llama_cpp', 'https://cloud.example.test/v1', None),
    ('llama_cpp', 'http://127.0.0.1:1/v1', 'http://127.0.0.1:2/v1'),
])
def test_cloud_and_unconfirmed_backends_have_no_extra_prompt_or_request(run, api, backend, url, cap_url):
    result, requests, calls, events, _ = run([response(api, 'Ordinary cloud answer.')],
        api=api, backend=backend, url=url, capability_url=cap_url)
    assert result['status'] == 'ok' and result['output_text'] == 'Ordinary cloud answer.'
    assert len(requests) == 1 and calls == []
    system = requests[0]['messages'][0]['content'] if api == 'openai' else requests[0]['system']
    assert system == 'Fixture policy: no unrelated actions.'
    assert not any(row['kind'] == 'model.completion_review' for row in events)


@pytest.mark.parametrize('text,status,requires', [
    ('Привет!', 'complete', False),
    ('Для счётчика обновляй текст отдельного span. Ничего не менял.', 'complete', False),
    ('Уточни, какую из двух вкладок нужно закрыть.', 'blocked', True),
    ('Доступ к браузеру отсутствует. Изменения не выполнены.', 'blocked', True),
])
def test_questions_drafts_and_honest_blockers_do_not_force_actions(run, text, status, requires):
    result, requests, calls, _, _ = run([response('openai', text), review('openai', status, requires)],
        user='Объясни, как исправить счётчик. Ничего не делай.')
    assert result['status'] == 'ok' and result['output_text'] == text
    assert len(requests) == 2 and calls == []


def test_no_tools_keeps_plain_text_path(run):
    result, requests, calls, _, _ = run([response('openai', 'Привет!')], names=())
    assert result['status'] == 'ok' and len(requests) == 1 and calls == []


def test_repeated_promises_stop_with_explicit_failure(run):
    bodies = [item for _ in range(3) for item in
              (response('openai', 'Начинаю.'), review('openai', 'continue', True))]
    result, requests, calls, events, _ = run(bodies)
    assert result['status'] == 'fail' and result['failure_reason'] == 'unfinished_action_repeated'
    assert len(requests) == 6 and calls == []
    assert sum(row['kind'] == 'post_turn.retry_required' for row in events) == 2
    assert not any(row['kind'] == 'task.finish' and row['status'] == 'ok' for row in events)


@pytest.mark.parametrize('bad', ['not JSON', '{}', '{"status":"complete","requires_tools":true,"evidence_ids":["invented"]}'])
def test_invalid_review_is_explicit_failure_without_executing_or_accepting_candidate(run, bad):
    result, requests, calls, _, _ = run([response('openai', 'Сейчас выполню.'), response('openai', bad)])
    assert result['status'] == 'fail' and result['failure_reason'] == 'completion_review_invalid'
    assert len(requests) == 2 and calls == []


def test_review_cannot_execute_a_tool(run):
    result, requests, calls, _, _ = run([
        response('openai', 'Начинаю.'), response('openai', tool=tool()),
    ])
    assert result['status'] == 'fail' and calls == []


def test_cancellation_during_review_is_not_completion(run):
    result, _, calls, events, _ = run([response('openai', 'Начинаю.'), {'__interrupted__': True}])
    assert result['failure_reason'] == 'interrupted' and calls == []
    assert events[-1]['reason'] == 'interrupted'


def test_main_output_limit_is_preserved_without_review_or_retry(run):
    result, requests, calls, _, _ = run([response('openai', 'Partial answer', limited=True)])
    assert result['failure_reason'] == 'model_output_limit'
    assert len(requests) == 1 and calls == []


def test_new_user_scope_after_review_takes_priority(run):
    invocations = []
    def steer():
        invocations.append(1)
        return [{'text': 'Стоп, ничего не делай; только объясни.', 'message_id': 'scope'}] if len(invocations) == 2 else []
    result, requests, calls, _, _ = run([
        response('openai', 'Начинаю.'), review('openai', 'continue', True),
        response('openai', 'Остановился. Вот объяснение.'), review('openai'),
    ], pending_steer_provider=steer)
    assert result['status'] == 'ok' and calls == []
    assert 'Стоп, ничего не делай' in json.dumps(requests[2], ensure_ascii=False)


def test_claimed_action_with_no_execution_evidence_cannot_complete():
    decision = parse_completion_review(json.dumps({
        'status': 'complete', 'requires_tools': True, 'evidence_ids': [],
    }), [])
    assert decision.status == 'continue'


def test_retry_budget_resets_only_when_tools_advance():
    state = CompletionRecovery()
    assert state.request_continuation() and state.request_continuation()
    assert not state.request_continuation()
    state.record('failed', 'Bash', False)
    assert not state.request_continuation()
    state.record('read', 'Read', True)
    assert state.request_continuation()


def test_review_projection_does_not_mutate_history_or_include_reasoning():
    messages = [{'role': 'assistant', 'content': [
        {'type': '__reasoning', 'text': 'private chain'},
        {'type': 'text', 'text': 'visible answer'},
    ]}, {'role': 'user', 'content': [{'type': 'image', 'source': {'data': 'private image'}},
        {'type': 'tool_result', 'tool_use_id': 'read', 'content': 'actual result'}]}]
    original = copy.deepcopy(messages)
    projected = json.dumps(review_transcript(messages))
    assert 'private chain' not in projected and 'private image' not in projected
    assert 'actual result' in projected and 'visible answer' in projected and messages == original


@pytest.mark.parametrize('api', ['google', 'openai-responses', 'opencode-responses'])
def test_other_protocols_do_not_opt_in_from_a_model_name(api):
    caps = ModelBackendCapabilities('llama_cpp', False, 'http://localhost:8081/v1', 'fixture')
    assert not completion_review_enabled(caps.base_url, api, caps, caps.model, ['Read'])


def test_pending_approval_returns_before_review_without_bypassing_it(run):
    result, requests, calls, events, _ = run([
        response('openai', tool=tool('Write', 'write-1', file_path='out.txt', content='fixture')),
    ], names=('Write',), approvals_enabled_provider=lambda: True, source_platform='android')
    assert result['status'] == 'waiting_approval' and result['failure_reason'] == 'approval_required'
    assert len(requests) == 1 and calls == []
    assert not any(row['kind'] == 'model.completion_review' for row in events)


def test_iteration_budget_also_counts_the_review_request(run):
    from factory.core.iteration_budget import IterationBudget
    budget = IterationBudget(1)
    result, requests, calls, _, _ = run([response('openai', 'Начинаю.')], iteration_budget=budget)
    assert result['failure_reason'] == 'iteration_budget_exhausted'
    assert len(requests) == 1 and calls == [] and budget.used == 1


@pytest.mark.parametrize('reply', [
    {'error': 'fixture transport failure'},
    response('openai', '{"status":', limited=True),
])
def test_review_transport_and_output_failures_do_not_accept_candidate(run, reply):
    result, requests, calls, _, _ = run([response('openai', 'Начинаю.'), reply])
    assert result['failure_reason'] == 'completion_review_failed'
    assert len(requests) == 2 and calls == []


def test_recovery_keeps_successful_side_effect_and_only_verifies_remaining_step(run):
    result, requests, calls, _, _ = run([
        response('openai', tool=tool('desktop_browser', 'effect', action='console', expression='3')),
        response('openai', 'Теперь проверю журнал.'), review('openai', 'continue', True, ['effect']),
        response('openai', tool=tool('desktop_browser', 'verify', action='console_messages')),
        response('openai', 'Проверено.'), review('openai', 'complete', True, ['effect', 'verify']),
    ])
    assert result['status'] == 'ok' and len(requests) == 6
    assert [args['action'] for name, args in calls] == ['console', 'console_messages']


def test_review_context_exhaustion_never_sends_an_oversized_request(monkeypatch):
    calls = []
    monkeypatch.setattr(e, '_http_post_streaming_openai', lambda *a, **kw: calls.append(a))
    review, reason, add_in, add_out = e._review_local_completion(
        url='http://127.0.0.1:1/v1/chat/completions', model='fixture', system_prompt='fixture',
        messages=[{'role': 'assistant', 'content': 'x' * 5000}], receipts=[], tools=[], headers={},
        timeout_s=1, max_tokens=64, context_window_tokens=128, is_openai_api=True,
        reasoning_format=None, cancel_check=lambda: False,
    )
    assert review is None and reason == 'completion_review_context_exceeded'
    assert calls == [] and add_in == add_out == 0


def test_fenced_json_review_is_accepted_without_changing_its_meaning():
    text = json.dumps({'status': 'complete', 'requires_tools': False, 'evidence_ids': []})
    decision = parse_completion_review('```json\n' + text + '\n```', [])
    assert decision.status == 'complete' and decision.requires_tools is False


def test_nested_tool_images_and_reasoning_are_not_forwarded_to_text_review():
    messages = [{'role': 'user', 'content': [{'type': 'tool_result', 'tool_use_id': 'shot', 'content': [
        {'type': 'text', 'text': 'actual page summary'},
        {'type': 'image', 'source': {'data': 'private nested image'}},
        {'type': '__reasoning', 'text': 'private nested reasoning'},
    ]}]}]
    original = copy.deepcopy(messages)
    projected = json.dumps(review_transcript(messages))
    assert 'actual page summary' in projected
    assert 'private nested' not in projected and messages == original
