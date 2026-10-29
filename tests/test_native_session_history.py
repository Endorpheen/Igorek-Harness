"""Persisted tool events must reach confirmed llama.cpp as protocol history."""
import copy
import json

import pytest

from factory.core import cc_backend_executor as executor
from factory.core.session_history import build_working_history
from factory.core.session_compaction import build_compaction_dry_run_plan
from test_executor_tool_loop_edges import loop  # noqa: F401
from test_executor_workflow_guards import response


def session(*, task='old', calls=None, status='done', output='stored result'):
    calls = calls or [{'id': 'same-id', 'name': 'Read', 'input': {'file_path': 'old.txt'}}]
    entries = [
        {'role': 'user', 'text': 'Read the fixture', 'task_id': task},
        {'role': 'assistant', 'text': 'Fixture complete', 'task_id': task},
    ]
    events = [
        {'seq': 1, 'task_id': task, 'kind': 'model.round_response', 'payload': {'round': 1, 'text': 'Checking fixture'}},
        {'seq': 2, 'task_id': task, 'kind': 'tool.request_batch', 'payload': {'tools': calls}},
    ]
    for index, call in enumerate(calls):
        events.append({'seq': 3 + index, 'task_id': task, 'kind': 'tool.result', 'payload': {
            'tool_use_id': call['id'], 'tool_name': call['name'], 'tool_input': call['input'],
            'context_output': output, 'status': status,
        }})
    return entries, events


def assemble(history, *, native=True, origins=None):
    return executor._build_initial_messages(
        history_messages=history, trailing_context_text='', user_message='Next fixture',
        input_files=None, native_tool_history=native, history_message_ids=origins,
    )


def native_blocks(messages, kind):
    return [block for message in messages if isinstance(message['content'], list)
            for block in message['content'] if block.get('type') == kind]


def test_real_history_shape_preserves_tool_pair_and_cloud_text_projection():
    entries, events = session()
    history = json.loads(json.dumps(build_working_history(entries, events)))
    original = copy.deepcopy(history)
    messages = assemble(history)
    wire = executor._anthropic_messages_to_openai(messages)
    call = next(m['tool_calls'][0] for m in wire if m.get('tool_calls'))
    result = next(m for m in wire if m['role'] == 'tool')
    assert call['function'] == {'name': 'Read', 'arguments': '{"file_path": "old.txt"}'}
    assert result == {'role': 'tool', 'tool_call_id': call['id'], 'content': 'stored result'}
    assert not any('[Historical tool evidence;' in str(m['content']) for m in wire)
    assert [m['content'] for m in wire if m['role'] == 'assistant' and not m.get('tool_calls')] == ['Checking fixture', 'Fixture complete']
    cloud = assemble(history, native=False)
    assert cloud[:-1] == [{'role': r['role'], 'content': r['content']} for r in history]
    assert history == original


def test_multi_tool_batch_preserves_order_errors_empty_output_and_compaction_identity():
    calls = [{'id': 'a', 'name': 'Read', 'input': {'file_path': 'a'}},
             {'id': 'b', 'name': 'Read', 'input': {'file_path': 'b'}}]
    entries, events = session(calls=calls, status='error', output='')
    history = build_working_history(entries, events)
    origins = {}
    messages = assemble(history, origins=origins)
    uses = native_blocks(messages, 'tool_use')
    results = native_blocks(messages, 'tool_result')
    assert [c['input'] for c in uses] == [c['input'] for c in calls]
    assert [r['tool_use_id'] for r in results] == [c['id'] for c in uses]
    assert all(r['is_error'] and r['content'] == '' for r in results)
    pair_messages = [m for m in messages if isinstance(m['content'], list) and any(b['type'] in {'tool_use', 'tool_result'} for b in m['content'])]
    assert len(pair_messages) == 2
    assert [origins[id(m)] for m in pair_messages] == [('tool:old:a', 'tool:old:b')] * 2
    plan = build_compaction_dry_run_plan(messages=messages, keep_tail_messages=3)
    preserved = set(plan.preserve_message_indexes)
    assert all(messages.index(m) in preserved for m in pair_messages)


def test_same_tool_ids_in_different_tasks_do_not_collide():
    entries, events = session(task='first')
    more_entries, more_events = session(task='second')
    for event in more_events:
        event['seq'] += 10
    messages = assemble(build_working_history(entries + more_entries, events + more_events))
    uses = native_blocks(messages, 'tool_use')
    assert len(uses) == 2 and len({b['id'] for b in uses}) == 2
    assert [b['tool_use_id'] for b in native_blocks(messages, 'tool_result')] == [b['id'] for b in uses]


@pytest.mark.parametrize('defect', ['missing_request', 'missing_result', 'duplicate_request', 'duplicate_result',
                                  'wrong_name', 'wrong_input', 'wrong_order', 'bad_input', 'duplicate_batch_id'])
def test_incomplete_or_ambiguous_events_keep_existing_evidence_without_inventing_calls(defect):
    entries, events = session()
    if defect == 'missing_request':
        events.pop(1)
    elif defect == 'missing_result':
        events.pop()
    elif defect == 'duplicate_request':
        events.insert(2, copy.deepcopy(events[1]))
        events[2]['seq'] = 2
    elif defect == 'duplicate_result':
        events.append(copy.deepcopy(events[-1]))
    elif defect == 'wrong_name':
        events[-1]['payload']['tool_name'] = 'Write'
    elif defect == 'wrong_input':
        events[-1]['payload']['tool_input'] = {'file_path': 'different'}
    elif defect == 'wrong_order':
        events[-1]['seq'] = 1
    elif defect == 'bad_input':
        events[1]['payload']['tools'][0]['input'] = []
    elif defect == 'duplicate_batch_id':
        other = {'seq': 2, 'task_id': 'old', 'kind': 'tool.request_batch', 'payload': {'tools': [
            {'id': 'other', 'name': 'Read', 'input': {}}]}}
        events.insert(2, other)
    history = build_working_history(entries, events)
    assert not native_blocks(assemble(history), 'tool_use')
    assert assemble(history) == assemble(history, native=False)


def test_partial_batch_never_creates_a_dangling_call():
    calls = [{'id': 'a', 'name': 'Read', 'input': {}}, {'id': 'b', 'name': 'Read', 'input': {}}]
    entries, events = session(calls=calls)
    events.pop()
    history = build_working_history(entries, events)
    assert assemble(history) == assemble(history, native=False)


def test_user_steer_during_execution_retains_actual_event_order_as_evidence():
    entries, events = session()
    entries.insert(1, {'role': 'user', 'text': 'Steer fixture', 'message_id': 'steer', 'steer': True, 'task_id': 'old'})
    events.insert(2, {'seq': 3, 'task_id': 'old', 'kind': 'steer.delivered', 'payload': {'message_id': 'steer'}})
    events[-1]['seq'] = 4
    history = build_working_history(entries, events)
    assert not native_blocks(assemble(history), 'tool_use')


def test_committed_compaction_does_not_resurrect_a_covered_member_of_a_batch():
    calls = [{'id': 'a', 'name': 'Read', 'input': {}}, {'id': 'b', 'name': 'Read', 'input': {}}]
    entries, events = session(calls=calls)
    events.append({'seq': 10, 'kind': 'compaction.completed', 'payload': {
        'summary_text': 'Carried fixture evidence', 'covered_history_ids': ['tool:old:a']}})
    history = build_working_history(entries, events)
    assert 'tool:old:a' not in [r['history_id'] for r in history]
    assert not native_blocks(assemble(history), 'tool_use')
    assert 'stored result' in str(assemble(history))


def test_assistant_text_and_journal_tool_entries_cannot_create_native_calls():
    entries, events = session()
    history = build_working_history(entries, events)
    text = next(r['content'] for r in history if r['history_id'] == 'tool:old:same-id')
    fake = [{'role': 'user', 'content': 'Fixture'}, {'role': 'assistant', 'content': text}]
    assert not native_blocks(assemble(fake), 'tool_use')
    old_entries = [{'role': 'user', 'text': 'Fixture'}, {'role': 'tool', 'text': text}]
    assert not native_blocks(assemble(build_working_history(old_entries, [])), 'tool_use')


@pytest.mark.parametrize('api', ['openai', 'anthropic'])
@pytest.mark.parametrize('backend,url', [('llama_cpp', 'http://127.0.0.1:1'),
                                       ('unknown', 'http://127.0.0.1:1'),
                                       ('cloud_proxy', 'http://127.0.0.1:1'),
                                       ('llama_cpp', 'https://api.example.test/v1')])
def test_executor_replays_only_confirmed_local_native_history_and_never_executes_it(loop, monkeypatch, api, backend, url):
    from factory.core.llama_image_input import LlamaImageInput
    def probe(policy):
        policy.backend = backend
        return None
    monkeypatch.setattr(LlamaImageInput, '_probe', probe)
    history = build_working_history(*session())
    current_call = {'id': 'current', 'name': 'Read', 'input': {'file_path': 'input.txt'}}
    is_native = backend == 'llama_cpp' and url.startswith('http://127.0.0.1')
    bodies = [response(api, tool=current_call), response(api, 'Fixture answer')]
    if is_native:
        bodies.append(response(api, json.dumps({'status':'complete','requires_tools':True,'evidence_ids':['current']})))
    run = loop(api, bodies, names=['Read'], history_messages=history, base_url=url)
    assert len(run.requests) == (3 if is_native else 2)
    assert run.result['status'] == 'ok' and run.result['output_text'] == 'Fixture answer'
    assert run.calls == [('Read', {'file_path': 'input.txt'})]
    first = run.requests[0]['messages']
    if api == 'openai':
        old_calls = [c for m in first for c in m.get('tool_calls', [])]
        assert len(old_calls) == int(is_native)
        if is_native:
            assert next(m for m in first if m['role'] == 'tool')['tool_call_id'] == old_calls[0]['id']
    else:
        assert len(native_blocks(first, 'tool_use')) == int(is_native)
    assert ('[Historical tool evidence;' in json.dumps(first)) is not is_native


@pytest.mark.parametrize('api', ['openai', 'anthropic'])
def test_confirmed_local_text_final_is_allowed_without_forcing_tools(loop, monkeypatch, api):
    from factory.core.llama_image_input import LlamaImageInput
    def probe(policy):
        policy.backend = 'llama_cpp'
        return None
    monkeypatch.setattr(LlamaImageInput, '_probe', probe)
    run = loop(api, [response(api, 'Fixture answer'), response(api, json.dumps({'status':'complete','requires_tools':False,'evidence_ids':[]}))], names=['Read'], history_messages=build_working_history(*session()))
    assert run.result['status'] == 'ok' and run.calls == [] and len(run.requests) == 2
    assert not any(request.get('tool_choice') == 'required' for request in run.requests)


@pytest.mark.parametrize('api', ['openai', 'anthropic'])
def test_reactive_compaction_covers_all_native_batch_origins_without_resurrection(loop, monkeypatch, api):
    from factory.core.llama_image_input import LlamaImageInput
    from factory.core.prompt_assembly import SESSION_SUMMARY_HEADINGS
    from factory.core.mobile_bridge.events import normalize_trace_event
    def probe(policy):
        policy.backend = 'llama_cpp'
        return None
    monkeypatch.setattr(LlamaImageInput, '_probe', probe)
    calls = [{'id': 'a', 'name': 'Read', 'input': {'file_path': 'a'}},
             {'id': 'b', 'name': 'Read', 'input': {'file_path': 'b'}}]
    entries, events = session(calls=calls, output='stored fixture ' + 'x' * 80000)
    history = json.loads(json.dumps(build_working_history(entries, events)))
    summary = '\n'.join(heading + '\n- carried fixture' for heading in SESSION_SUMMARY_HEADINGS)
    run = loop(api, [{'error': 'prompt too long'}, response(api, summary), response(api, 'Fixture answer'), response(api, json.dumps({'status':'complete','requires_tools':False,'evidence_ids':[]}))],
               history_messages=history, names=['Read'], max_tokens=1024, task_id='current')
    assert run.result['status'] == 'ok' and run.calls == [] and len(run.requests) == 4
    completed = next(row for row in run.trace if row['kind'] == 'compaction.completed')
    assert {'tool:old:a', 'tool:old:b'} <= set(completed['covered_history_ids'])
    assert not any(origin.startswith('tool:current:hist_') for origin in completed['covered_history_ids'])
    archives = list((run.work.parent / 'artifacts' / '.session_compaction').glob('*_history.jsonl'))
    archived = [json.loads(line) for line in archives[0].read_text().splitlines()]
    batch_origins = [row['history_ids'] for row in archived if 'history_ids' in row]
    assert batch_origins == [['tool:old:a', 'tool:old:b']] * 2
    frame = normalize_trace_event(completed)
    frame.update(seq=20, task_id='current')
    restored = build_working_history(entries, events + [frame])
    assert not native_blocks(assemble(restored), 'tool_use')
    assert not any(row['history_id'].startswith('tool:old:') for row in restored)
    assert 'carried fixture' in json.dumps(restored)
    assert not any(m.get('tool_calls') for m in run.requests[-1]['messages'])


@pytest.mark.parametrize('field,value', [('version', 2), ('batch_size', 0), ('batch_size', True),
                                        ('batch_index', 5), ('name', ''), ('input', []),
                                        ('output', None), ('is_error', 'false')])
def test_invalid_typed_sidecar_falls_back_to_existing_text(field, value):
    history = build_working_history(*session())
    tool = next(row for row in history if row['history_id'].startswith('tool:'))
    tool['native_tool_exchange'][field] = value
    assert assemble(history) == assemble(history, native=False)


@pytest.mark.parametrize('succeeded,preview', [(None, 'fixture file content'),
                                               (False, 'fixture file content'), (True, 'error fixture text')])
def test_event_normalization_and_journal_reload_preserve_native_pair(tmp_path, succeeded, preview):
    from factory.core.mobile_bridge.events import normalize_trace_event
    from factory.core.session_history import read_history
    entries, _ = session()
    trace = [
        {'kind': 'tool.request_batch', 'tools': [{'id': 'same-id', 'name': 'Read', 'input': {'file_path': 'old.txt'}}]},
        {'kind': 'tool.result', 'tool_use_id': 'same-id', 'tool_name': 'Read',
         'tool_input': {'file_path': 'old.txt'}, 'context_output': 'fixture file content',
         'raw_result_preview': preview, 'succeeded': succeeded},
    ]
    events = []
    for seq, row in enumerate(trace, 1):
        event = normalize_trace_event(row)
        event.update(seq=seq, task_id='old')
        events.append(event)
    path = tmp_path / 'history.jsonl'
    path.write_text(''.join(json.dumps(row) + '\n' for row in entries))
    messages = assemble(json.loads(json.dumps(build_working_history(read_history(path), events))))
    assert len(native_blocks(messages, 'tool_use')) == 1
    assert native_blocks(messages, 'tool_result')[0]['content'] == 'fixture file content'
    assert native_blocks(messages, 'tool_result')[0]['is_error'] is (succeeded is False)
