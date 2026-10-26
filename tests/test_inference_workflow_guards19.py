"""Tool plans must respect the active workflow even if a model proposes writes."""

import asyncio
from unittest.mock import AsyncMock

import pytest

from inference_api_support import WRITE, body, state, inference_app as inference_app


@pytest.mark.parametrize('workflow,step', [
    ('project_analysis', 'scan_subdirs'),
    ('file_investigation', 'read_related'),
    ('tavily_research', 'fetch_sources'),
    ('session_recall', 'search'),
])
def test_model_cannot_write_during_read_only_workflow(inference_app, monkeypatch, workflow, step):
    env = inference_app
    monkeypatch.setattr(env.api, 'GENERATOR', env.generator)
    victim = env.root / 'victim.txt'
    victim.write_text('unchanged', encoding='utf-8')
    current = state(env, body(env, 'Review the current project', [WRITE]))
    current.current_workflow = workflow
    current.workflow_step = step
    current.remaining_directories = ['src']
    current.candidate_related_files = [str(victim)]
    current.collected_targets = [str(victim)]
    plan = {'id': 'fixture-write', 'name': 'Write', 'input': {'file_path': str(victim), 'content': 'changed'}}
    planner = AsyncMock(return_value=plan)
    monkeypatch.setattr(env.api, '_maybe_model_tool_use', planner)
    events = []
    monkeypatch.setattr(env.api, '_trace_event', lambda request, event, **data: events.append(event))

    decision = asyncio.run(env.api._resolve_tool_turn(None, current))

    assert planner.await_count == 1
    assert decision is None
    assert 'workflow_invalid_tool_rejected' in events
    assert victim.read_text(encoding='utf-8') == 'unchanged'
