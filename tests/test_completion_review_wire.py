"""Real SSE completion signals finish without a second provider request."""
import json
import threading

from factory.core import cc_backend_executor as e
from factory.core.model_backend_capabilities import ModelBackendCapabilities
from test_cc_stream_wire_contracts import wire as wire, event, chunk


def invoke(wire, tmp_path, monkeypatch, cancelled):
    (tmp_path/'trace').mkdir(exist_ok=True)
    base=wire.url.split('?',1)[0].rsplit('/chat/completions',1)[0]
    monkeypatch.setenv('IGOREK_FACTORY_HOME',str(tmp_path/'data'))
    monkeypatch.setattr(e.LlamaImageInput,'resolve',lambda _:ModelBackendCapabilities('llama_cpp',False,base,'fixture-model'))
    monkeypatch.setattr(e,'_resolve_backend_context_window',lambda **kw:(262144,'fixture'))
    monkeypatch.setattr(e,'is_interrupt_requested',cancelled.is_set)
    monkeypatch.setattr(e,'task_should_stop',lambda _:False)
    return e.execute_cc_backend_task(base_url=base,api_format='openai',model='fixture-model',
        system_prompt='Fixture policy.',user_message='Say hello.',workdir=str(tmp_path),
        artifact_workdir=str(tmp_path/'trace'),max_output_tokens=65536,
        enabled_tools=[{'name':'Read','description':'Read a fixture.','input_schema':{'type':'object','properties':{}}}])


def test_fragmented_real_sse_finish_returns_answer_in_one_request(wire,tmp_path,monkeypatch):
    arguments=json.dumps({'status':'complete','answer':'Привет!','requires_tools':False,'evidence_ids':[],'remaining_steps':[]})
    wire.chunks=[event(chunk(tools=[{'index':0,'id':'finish-1','type':'function','function':{'name':'finish_task','arguments':arguments[:29]}}])),
                 event(chunk(tools=[{'index':0,'function':{'arguments':arguments[29:]}}],finish='tool_calls')),
                 event({'choices':[],'usage':{'prompt_tokens':7,'completion_tokens':5}}),b'data: [DONE]\n\n']
    result=invoke(wire,tmp_path,monkeypatch,threading.Event())
    assert result['status']=='ok' and result['output_text']=='Привет!' and result['tool_calls_count']==0
    assert len(wire.requests)==1 and result['usage']['completion']==5
    payload=wire.requests[0]['body']
    assert payload['max_tokens']==65536 and 'response_format' not in payload
    assert any(t['function']['name']=='finish_task' for t in payload['tools'])
    rows=[json.loads(x) for x in (tmp_path/'trace/cc_backend_trace.jsonl').read_text().splitlines()]
    assert rows[-1]['reason']=='explicit_completion'
    assert not any(row['kind'] in ('model.text_delta','model.completion_review') for row in rows)


def test_stalled_main_generation_is_still_cancelable(wire,tmp_path,monkeypatch):
    wire.stall=True
    cancelled=threading.Event();results=[]
    thread=threading.Thread(target=lambda:results.append(invoke(wire,tmp_path,monkeypatch,cancelled)),daemon=True)
    thread.start()
    try:
        assert wire.started.wait(2)
        cancelled.set();thread.join(3)
        assert not thread.is_alive() and len(results)==1
        assert results[0]['status']=='fail' and results[0]['failure_reason']=='interrupted'
    finally:
        cancelled.set();wire.release.set();thread.join(3)


def visible_round_text(rows):
    from factory.core.mobile_bridge.events import normalize_trace_event
    return [frame['payload']['text'] for row in rows
            if (frame := normalize_trace_event(row, task_id='fixture-task'))
            and frame['kind'] == 'model.round_response' and frame['payload']['text']]


def test_mixed_local_stream_only_delivers_the_validated_finish_answer(wire, tmp_path, monkeypatch):
    arguments = json.dumps(dict(status='complete', answer='Привет!', requires_tools=False,
                                evidence_ids=[], remaining_steps=[]))
    wire.chunks = [event(chunk(reasoning='PRIVATE_REASONING')),
                   event(chunk('DRAFT_RESPONSE </arg_value> untagged private reflection')),
                   event(chunk(tools=[{'index': 0, 'id': 'finish-1', 'type': 'function',
                                      'function': {'name': 'finish_task', 'arguments': arguments}}], finish='tool_calls')),
                   b'data: [DONE]\n\n']
    result = invoke(wire, tmp_path, monkeypatch, threading.Event())
    rows = [json.loads(line) for line in (tmp_path/'trace/cc_backend_trace.jsonl').read_text().splitlines()]
    assert result['status'] == 'ok' and result['output_text'] == 'Привет!'
    assert visible_round_text(rows) == ['Привет!']
    assert not any(row['kind'] == 'model.text_delta' for row in rows)
    assert ''.join(row.get('text', '') for row in rows if row['kind'] == 'model.reasoning_delta') == 'PRIVATE_REASONING'
    assert 'tool_choice' not in wire.requests[0]['body']
    assert len(wire.requests) == 1


def test_tagged_reasoning_inside_finish_answer_stays_out_of_visible_text(wire, tmp_path, monkeypatch):
    arguments = json.dumps(dict(status='complete', answer='<think>PRIVATE_FINISH_REASONING</think>Привет!',
                                requires_tools=False, evidence_ids=[], remaining_steps=[]))
    wire.chunks = [event(chunk(tools=[{'index': 0, 'id': 'finish-1', 'type': 'function',
                                     'function': {'name': 'finish_task', 'arguments': arguments}}], finish='tool_calls')),
                   b'data: [DONE]\n\n']
    result = invoke(wire, tmp_path, monkeypatch, threading.Event())
    rows = [json.loads(line) for line in (tmp_path/'trace/cc_backend_trace.jsonl').read_text().splitlines()]
    assert result['output_text'] == 'Привет!'
    assert visible_round_text(rows) == ['Привет!']
    assert any(row.get('text') == 'PRIVATE_FINISH_REASONING' and row['kind'] == 'model.reasoning_delta' for row in rows)


def test_unfinished_plain_local_output_never_becomes_a_final_chat_message(wire, tmp_path, monkeypatch):
    wire.chunks = [event(chunk('UNVALIDATED_DRAFT </arg_value>', finish='stop')), b'data: [DONE]\n\n']
    result = invoke(wire, tmp_path, monkeypatch, threading.Event())
    rows = [json.loads(line) for line in (tmp_path/'trace/cc_backend_trace.jsonl').read_text().splitlines()]
    assert result['status'] == 'fail' and result['failure_reason'] == 'unfinished_action_repeated'
    assert visible_round_text(rows) == []
    assert not any(row['kind'] == 'model.text_delta' for row in rows)
    assert len(wire.requests) == 3


def test_limited_local_draft_is_preserved_in_diagnostics_not_as_an_answer(wire, tmp_path, monkeypatch):
    wire.chunks = [event(chunk('UNVALIDATED_PRIVATE_DRAFT', finish='length')), b'data: [DONE]\n\n']
    result = invoke(wire, tmp_path, monkeypatch, threading.Event())
    rows = [json.loads(line) for line in (tmp_path/'trace/cc_backend_trace.jsonl').read_text().splitlines()]
    assert result['status'] == 'fail' and result['failure_reason'] == 'model_output_limit'
    assert 'UNVALIDATED_PRIVATE_DRAFT' not in result['output_text']
    assert visible_round_text(rows) == []
    assert any('UNVALIDATED_PRIVATE_DRAFT' in row.get('draft_text', '') for row in rows)
