"""Planner recovery requires actual numeric Read ranges and known agent aliases."""

import asyncio
import importlib
import json

import pytest

from inference_api_support import READ, body, state, inference_app as inference_app, inference_server as inference_server


@pytest.fixture
def planner(inference_server):
    return inference_server, importlib.import_module('factory.api_server_core.tool_planner_runtime')


@pytest.mark.parametrize('offset,limit,expected', [(1, 400, True), (2, 1, True), (0, 20, False), (-1, 20, False), (1, 0, False), ('1', 2, False), (1, '2', False), (True, 2, False), (1, True, False), (None, 2, False), (1, [], False)])
def test_bounded_retry_rejects_boolean_string_and_missing_read_ranges(planner, offset, limit, expected):
    _, runtime = planner
    assert runtime._read_plan_has_explicit_bounds({'tool': 'Read', 'input': {'offset': offset, 'limit': limit}}) is expected


@pytest.mark.parametrize('plan', [None, [], {}, {'tool': 'Bash', 'input': {'offset': 1, 'limit': 2}}, {'tool': 'Read', 'input': None}])
def test_non_read_shapes_do_not_count_as_bounded_retry(planner, plan):
    _, runtime = planner
    assert not runtime._read_plan_has_explicit_bounds(plan)


@pytest.mark.parametrize('alias', ['general-purpose', 'statusline-setup', 'Explore', 'Plan', 'claude-code-guide'])
def test_supported_agent_alias_preserves_description_and_prompt(planner, alias):
    env, runtime = planner
    current = state(env, body(env, 'Inspect fixture', [READ]))
    current.tool_map['Agent'] = {'name': 'Agent'}
    plan = {'tool': alias, 'input': {'description': 'Fixture', 'prompt': 'Inspect only owned fixture'}}
    assert runtime._canonicalize_agent_subtype_plan(current, plan) == {'tool': 'Agent', 'input': {'subagent_type': alias, 'description': 'Fixture', 'prompt': 'Inspect only owned fixture'}}
    assert plan['tool'] == alias


@pytest.mark.parametrize('plan', [None, [], {'tool': 7, 'input': {}}, {'tool': 'Explore', 'input': []}, {'tool': 'Explore', 'input': {'description': 7, 'prompt': 'fixture'}}, {'tool': 'Read', 'input': {'file_path': 'fixture'}}])
def test_unsupported_or_incomplete_alias_does_not_invent_an_agent_call(planner, plan):
    env, runtime = planner
    current = state(env, body(env, 'Inspect fixture', [READ]))
    current.tool_map['Agent'] = {'name': 'Agent'}
    assert runtime._canonicalize_agent_subtype_plan(current, plan) is plan


def test_agent_alias_requires_agent_in_the_available_tool_map(planner):
    env, runtime = planner
    current = state(env, body(env, 'Inspect fixture', [READ]))
    plan = {'tool': 'Explore', 'input': {'description': 'fixture', 'prompt': 'fixture'}}
    assert runtime._canonicalize_agent_subtype_plan(current, plan) is plan


@pytest.mark.parametrize('first_input', [{'offset': True, 'limit': 2}, {'offset': 1, 'limit': True}, {}])
def test_oversized_file_retry_replans_boolean_or_unbounded_ranges(planner, first_input):
    env, _ = planner
    path = env.root / 'fixture.py'
    path.write_text('untouched fixture')
    current = state(env, body(env, 'Read fixture.py', [READ]))
    current.current_workflow = 'file_investigation'
    current.last_workflow_error = 'exceeds maximum allowed tokens. Use offset and limit parameters'
    current.collected_targets = [str(path)]
    env.generator.outputs.extend([json.dumps({'tool': 'Read', 'input': {'file_path': str(path), **first_input}}), json.dumps({'tool': 'Read', 'input': {'file_path': str(path), 'offset': 401, 'limit': 400}})])
    result = asyncio.run(env.api._maybe_model_tool_use(None, current, require_tool=True))
    assert len(env.generator.calls) == 2
    assert result['name'] == 'Read' and result['input'] == {'file_path': str(path), 'offset': 401, 'limit': 400}
    assert 'CRITICAL RETRY INSTRUCTION' in env.generator.calls[1]['prompt']
    assert path.read_text() == 'untouched fixture'


def test_no_available_tools_does_not_invoke_the_model(planner):
    env, _ = planner
    current = state(env, body(env, 'Inspect fixture', [READ]))
    current.tools = []
    current.tool_map.clear()
    assert asyncio.run(env.api._maybe_model_tool_use(None, current, require_tool=True)) is None
    assert env.generator.calls == []
