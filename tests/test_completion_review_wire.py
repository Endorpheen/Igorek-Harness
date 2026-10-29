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
