"""Real SSE terminal replies retain normal completion and reasoning channels."""
import json
import threading

from factory.core import cc_backend_executor as e
from factory.core.model_backend_capabilities import ModelBackendCapabilities
from test_cc_stream_wire_contracts import wire as wire, event, chunk


def invoke(wire, tmp_path, monkeypatch, cancelled):
    (tmp_path/'trace').mkdir(exist_ok=True)
    base = wire.url.split('?', 1)[0].rsplit('/chat/completions', 1)[0]
    monkeypatch.setenv('IGOREK_FACTORY_HOME', str(tmp_path/'data'))
    monkeypatch.setattr(e.LlamaImageInput, 'resolve', lambda _: ModelBackendCapabilities('llama_cpp', False, base, 'fixture-model'))
    monkeypatch.setattr(e, '_resolve_backend_context_window', lambda **kw: (262144, 'fixture'))
    monkeypatch.setattr(e, 'is_interrupt_requested', cancelled.is_set)
    monkeypatch.setattr(e, 'task_should_stop', lambda _: False)
    return e.execute_cc_backend_task(base_url=base, api_format='openai', model='fixture-model',
        system_prompt='Fixture policy.', user_message='Привет!', workdir=str(tmp_path),
        artifact_workdir=str(tmp_path/'trace'), max_output_tokens=65536,
        enabled_tools=[{'name':'Read','description':'Read a fixture.','input_schema':{'type':'object','properties':{}}}])


def test_recorded_greeting_shape_finishes_without_any_control_retry(wire, tmp_path, monkeypatch):
    answer = 'Привет! 👋 Как я могу тебе помочь сегодня?'
    wire.chunks = [event(chunk(reasoning='PRIVATE_REASONING')),
                   event(chunk(answer[:12])), event(chunk(answer[12:], finish='stop')),
                   event({'choices': [], 'usage': {'prompt_tokens': 7, 'completion_tokens': 5}}),
                   b'data: [DONE]\n\n']
    result = invoke(wire, tmp_path, monkeypatch, threading.Event())
    rows = [json.loads(x) for x in (tmp_path/'trace/cc_backend_trace.jsonl').read_text().splitlines()]
    assert result['status'] == 'ok' and result['output_text'] == answer
    assert result['tool_calls_count'] == 0 and len(wire.requests) == 1
    assert result['usage']['completion'] == 5
    assert ''.join(row.get('text','') for row in rows if row['kind'] == 'model.text_delta') == answer
    assert ''.join(row.get('text','') for row in rows if row['kind'] == 'model.reasoning_delta') == 'PRIVATE_REASONING'
    assert not any(row.get('reason') == 'missing_finish_signal' for row in rows)
    assert all(tool['function']['name'] != 'finish_task' for tool in wire.requests[0]['body']['tools'])


def test_tagged_reasoning_stays_separate_without_a_finish_protocol(wire, tmp_path, monkeypatch):
    wire.chunks = [event(chunk('<thi')), event(chunk('nk>PRIVATE_THOUGHT</think>Привет!', finish='stop')), b'data: [DONE]\n\n']
    result = invoke(wire, tmp_path, monkeypatch, threading.Event())
    rows = [json.loads(x) for x in (tmp_path/'trace/cc_backend_trace.jsonl').read_text().splitlines()]
    assert result['status'] == 'ok' and result['output_text'] == 'Привет!'
    assert ''.join(row.get('text','') for row in rows if row['kind'] == 'model.text_delta') == 'Привет!'
    assert ''.join(row.get('text','') for row in rows if row['kind'] == 'model.reasoning_delta') == 'PRIVATE_THOUGHT'
    assert len(wire.requests) == 1


def test_stalled_main_generation_is_still_cancelable(wire, tmp_path, monkeypatch):
    wire.stall = True
    cancelled = threading.Event(); results = []
    thread = threading.Thread(target=lambda: results.append(invoke(wire, tmp_path, monkeypatch, cancelled)), daemon=True)
    thread.start()
    try:
        assert wire.started.wait(2)
        cancelled.set(); thread.join(3)
        assert not thread.is_alive() and len(results) == 1
        assert results[0]['status'] == 'fail' and results[0]['failure_reason'] == 'interrupted'
    finally:
        cancelled.set(); wire.release.set(); thread.join(3)
