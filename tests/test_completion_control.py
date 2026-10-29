"""Same-turn completion avoids hidden requests and preserves cloud execution."""
import copy
import json
from collections import deque

import pytest

from factory.core import cc_backend_executor as e
from factory.core.completion_review import (
    CompletionRecovery,
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




def finish(api, answer='Проверенный результат.', *, requires=False, ids=(), status='complete', remaining=()):
    return response(api, tool=tool('finish_task', 'finish-signal', status=status, answer=answer,
        requires_tools=requires, evidence_ids=list(ids), remaining_steps=list(remaining)))


@pytest.mark.parametrize('api', ['openai', 'anthropic'])
def test_valid_finish_returns_without_any_followup_model_request(run, api):
    result, requests, calls, events, _ = run([
        response(api, tool=tool()), finish(api, requires=True, ids=['read-1']),
    ], api=api, names=('Read',))
    assert result['status'] == 'ok' and result['tool_calls_count'] == 1
    assert len(requests) == 2 and len(calls) == 1
    assert result['usage']['prompt'] == 4 and result['usage']['completion'] == 6
    assert events[-1]['reason'] == 'explicit_completion'
    assert not any(row['kind'] == 'model.completion_review' for row in events)
    assert all(item['max_tokens'] == 65536 for item in requests)


@pytest.mark.parametrize('promise', [
    'Три вызова console для регистрации сообщений с разными уровнями:',
    'Добавляю видимый виджет счётчика на страницу через console (DOM-элементы с кнопкой и обработчиком):',
    'I will perform the requested actions now.',
])
def test_original_preamble_recovers_without_a_separate_judge(run, promise):
    result, requests, calls, events, _ = run([
        response('openai', promise), response('openai', tool=tool()),
        finish('openai', requires=True, ids=['read-1']),
    ])
    assert result['status'] == 'ok' and len(requests) == 3 and len(calls) == 1
    assert any(row.get('reason') == 'missing_finish_signal' for row in events)
    assert all('response_format' not in item for item in requests)


def test_preparation_does_not_discharge_remaining_work_or_replay_its_first_action(run):
    result, requests, calls, _, _ = run([
        response('openai', tool=tool('desktop_browser', 'effect', action='console', expression='3')),
        response('openai', 'Теперь проверю журнал.'),
        response('openai', tool=tool('desktop_browser', 'verify', action='console_messages')),
        finish('openai', requires=True, ids=['effect', 'verify']),
    ])
    assert result['status'] == 'ok' and len(requests) == 4
    assert [args['action'] for name, args in calls] == ['console', 'console_messages']


@pytest.mark.parametrize('api', ['openai', 'anthropic'])
@pytest.mark.parametrize('backend,url,cap_url', [
    ('unknown', 'http://127.0.0.1:1/v1', None),
    ('cloud', 'http://127.0.0.1:1/v1', None),
    ('unknown', 'https://cloud.example.test/v1', None),
    ('llama_cpp', 'https://cloud.example.test/v1', None),
    ('llama_cpp', 'http://127.0.0.1:1/v1', 'http://127.0.0.1:2/v1'),
])
def test_cloud_and_unknown_backends_keep_original_prompt_tools_and_request_count(run, api, backend, url, cap_url):
    result, requests, calls, _, _ = run([response(api, 'Ordinary cloud answer.')],
        api=api, backend=backend, url=url, capability_url=cap_url)
    assert result['status'] == 'ok' and len(requests) == 1 and calls == []
    system = requests[0]['messages'][0]['content'] if api == 'openai' else requests[0]['system']
    assert system == 'Fixture policy: no unrelated actions.'
    schemas = requests[0]['tools']
    assert 'finish_task' not in {item.get('name', item.get('function', {}).get('name')) for item in schemas}


@pytest.mark.parametrize('text,status,requires,remaining', [
    ('Привет!', 'complete', False, []),
    ('```python\nprint("example")\n```', 'complete', False, []),
    ('Уточни, какую вкладку закрыть.', 'blocked', True, ['Уточнить вкладку']),
    ('Нужен вход владельца.', 'blocked', True, ['Войти']),
])
def test_answers_and_honest_blockers_finish_in_one_model_request(run, text, status, requires, remaining):
    result, requests, calls, _, _ = run([finish('openai', text, status=status, requires=requires, remaining=remaining)])
    assert result['status'] == 'ok' and result['output_text'] == text
    assert len(requests) == 1 and calls == []


def test_tool_free_backend_request_keeps_plain_text_path(run):
    result, requests, calls, _, _ = run([response('openai', 'Привет!')], names=())
    assert result['status'] == 'ok' and len(requests) == 1 and calls == []


def test_repeated_missing_signals_fail_without_unbounded_generation(run):
    result, requests, calls, events, _ = run([response('openai', 'Начинаю.')] * 3)
    assert result['failure_reason'] == 'unfinished_action_repeated'
    assert len(requests) == 3 and calls == []
    assert not any(row['kind'] == 'task.finish' and row['status'] == 'ok' for row in events)


@pytest.mark.parametrize('changes', [
    {'evidence_ids': ['invented']}, {'answer': ''}, {'requires_tools': 'true'},
    {'remaining_steps': ['Ещё проверить']}, {'status': 'invented'},
])
def test_invalid_signal_is_rejected_without_dispatching_it_as_a_work_tool(run, changes):
    bad=finish('openai')
    arguments=json.loads(bad['choices'][0]['message']['tool_calls'][0]['function']['arguments'])
    arguments.update(changes)
    bad['choices'][0]['message']['tool_calls'][0]['function']['arguments']=json.dumps(arguments)
    result, requests, calls, events, _=run([bad, finish('openai', 'Текстовый ответ готов.')])
    assert result['status'] == 'ok' and len(requests) == 2 and calls == []
    assert any(row['kind'] == 'task.completion_signal' and not row['accepted'] for row in events)
    results=json.dumps(requests[1], ensure_ascii=False)
    assert 'rejected' in results and 'tools_executed' in results


@pytest.mark.parametrize('api', ['openai', 'anthropic'])
def test_finish_mixed_with_actions_never_swallows_or_executes_that_batch(run, api):
    batch=response(api, tool=tool())
    control=finish(api)
    if api=='openai':
        batch['choices'][0]['message']['tool_calls']+=control['choices'][0]['message']['tool_calls']
    else:
        batch['content']+=control['content']
    result, requests, calls, events, _=run([batch, finish(api, status='blocked', answer='Требуется уточнение.')], api=api)
    assert result['status'] == 'ok' and len(requests) == 2 and calls == []
    assert any(row.get('reason') == 'mixed_finish_batch' for row in events)


def test_incomplete_plan_rejects_finish_until_plan_is_closed(run):
    pending=tool('update_plan', 'plan-1', plan=[{'step':'Read', 'status':'in_progress'}])
    complete=tool('update_plan', 'plan-2', plan=[{'step':'Read', 'status':'completed'}])
    result, requests, calls, events, _=run([
        response('openai', tool=pending), response('openai', tool=tool()),
        finish('openai', requires=True, ids=['read-1']), response('openai', tool=complete),
        finish('openai', requires=True, ids=['read-1','plan-2']),
    ], names=('Read','update_plan'))
    assert result['status']=='ok' and len(requests)==5 and len(calls)==3
    assert any(row.get('reason')=='unfinished_steps' for row in events)


def test_new_user_scope_supersedes_a_finish_signal_before_task_completion(run):
    calls=[]
    def steer():
        calls.append(1)
        return [{'text':'Стоп. Только объясни.', 'message_id':'scope'}] if len(calls)==1 else []
    result, requests, work_calls, events, _=run([
        finish('openai','Первый ответ.'), finish('openai','Объяснение без действий.'),
    ], pending_steer_provider=steer)
    assert result['output_text']=='Объяснение без действий.' and len(requests)==2 and work_calls==[]
    assert sum(row['kind']=='task.finish' for row in events)==1
    assert 'Стоп. Только объясни.' in json.dumps(requests[1],ensure_ascii=False)


def test_pending_write_approval_is_not_bypassed_by_control_protocol(run):
    result, requests, calls, events, _=run([
        response('openai', tool=tool('Write','write-1',file_path='out.txt',content='fixture')),
    ],names=('Write',),approvals_enabled_provider=lambda:True,source_platform='android')
    assert result['status']=='waiting_approval' and result['failure_reason']=='approval_required'
    assert len(requests)==1 and calls==[]


def test_existing_iteration_budget_counts_only_main_turns_without_hidden_review(run):
    from factory.core.iteration_budget import IterationBudget
    budget=IterationBudget(1)
    result, requests, calls, _, _=run([finish('openai','Привет!')],iteration_budget=budget)
    assert result['status']=='ok' and budget.used==1 and len(requests)==1 and calls==[]


def test_provider_output_limit_stays_explicit_without_control_retries(run):
    result, requests, calls, _, _=run([response('openai','Partial answer',limited=True)])
    assert result['failure_reason']=='model_output_limit' and len(requests)==1 and calls==[]


def test_standalone_tool_cancellation_keeps_its_failure_state(run):
    result, requests, calls, _, _=run([{'__interrupted__':True}])
    assert result['failure_reason']=='interrupted' and len(requests)==1 and calls==[]


def test_retry_allowance_resets_on_progress_but_not_failed_calls():
    state=CompletionRecovery()
    assert state.request_continuation() and state.request_continuation()
    assert not state.request_continuation()
    state.record('failure','Read',False)
    assert not state.request_continuation()
    state.record('success','Read',True)
    assert state.request_continuation()


def test_current_receipt_alias_is_visible_in_tool_result_and_task_bound(run):
    result, requests, calls, _, _ = run([
        response('openai', tool=tool()), finish('openai', requires=True, ids=['read-1']),
    ], names=('Read',))
    history = requests[-1]['messages']
    results = [item['content'] for item in history if item.get('role') == 'tool']
    assert len(results) == 1
    assert results[0].startswith('[Runtime execution receipt: rt_')
    assert '; tool=Read; succeeded=True]' in results[0]
    assert 'fixture input' in results[0]


def test_alias_from_another_task_cannot_prove_completion():
    from factory.core.completion_protocol import validate_finish
    first = CompletionRecovery(receipt_token='aaaaaa')
    second = CompletionRecovery(receipt_token='bbbbbb')
    for state in (first, second):
        state.record('same-native-id', 'Read', True)
    value = dict(status='complete', answer='Verified.', requires_tools=True,
                 evidence_ids=[first.receipts[0]['alias']], remaining_steps=[])
    assert validate_finish(value, first).evidence_ids == ('same-native-id',)
    with pytest.raises(ValueError, match='unbound_completion_evidence'):
        validate_finish(value, second)
