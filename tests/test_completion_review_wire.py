"""Completion-control requests use real isolated SSE transport without UI output."""
import json
import threading

from factory.core import cc_backend_executor as e
from test_cc_stream_wire_contracts import wire as wire, event, chunk


def call_review(wire, cancel_check=lambda: False):
    return e._review_local_completion(
        url=wire.url, model='fixture-model', system_prompt='No unrelated actions.',
        messages=[{'role': 'user', 'content': 'Explain the existing result.'},
                  {'role': 'assistant', 'content': 'The requested explanation.'}],
        receipts=[], tools=[{'name': 'Read', 'description': 'Read a file.'}],
        headers={'Content-Type': 'application/json'}, timeout_s=3, max_tokens=65536,
        context_window_tokens=262144, is_openai_api=True, reasoning_format='auto',
        cancel_check=cancel_check,
    )


def test_real_sse_review_preserves_budget_accounts_usage_and_keeps_control_data_out_of_ui(wire, monkeypatch):
    emitted = []
    monkeypatch.setattr(e, 'append_cc_backend_trace', lambda *args: emitted.append(args))
    decision = json.dumps({'status': 'complete', 'requires_tools': False, 'evidence_ids': []})
    wire.chunks = [event(chunk(reasoning='Fixture internal reasoning.')),
                   event(chunk(decision[:15])), event(chunk(decision[15:], finish='stop')),
                   event({'choices': [], 'usage': {'prompt_tokens': 7, 'completion_tokens': 5}}),
                   b'data: [DONE]\n\n']
    review, error, add_in, add_out = call_review(wire)
    assert review.status == 'complete' and error == '' and (add_in, add_out) == (7, 5)
    assert emitted == []
    payload = wire.requests[0]['body']
    assert payload['max_tokens'] == 65536 and payload['reasoning_format'] == 'auto'
    assert payload['response_format']['json_schema']['strict'] is True
    assert 'tools' not in payload and 'tool_choice' not in payload


def test_real_stalled_review_is_cancelable_without_becoming_success(wire):
    wire.stall = True
    cancelled, results = threading.Event(), []
    thread = threading.Thread(target=lambda: results.append(call_review(wire, cancelled.is_set)), daemon=True)
    thread.start()
    try:
        assert wire.started.wait(2)
        cancelled.set()
        thread.join(3)
        assert not thread.is_alive() and len(results) == 1
        assert results[0][0] is None and results[0][1] == 'interrupted'
    finally:
        cancelled.set()
        wire.release.set()
        thread.join(3)
