"""Stable tool-result routing and truthful terminal summaries."""
import json

import pytest

from factory.core import cc_backend_executor as executor
from factory.core import cc_backend_tools as tools


@pytest.mark.parametrize('command,expected', [
    ('curl -sL https://example.invalid/data', ('web_extract', {'urls': ['https://example.invalid/data']})),
    ('wget -q "https://example.invalid/data"', ('web_extract', {'urls': ['https://example.invalid/data']})),
    ('curl https://api.tavily.com/search -d \'{"query":"fixture topic"}\'', ('web_search', {'query': 'fixture topic', 'limit': 5})),
    ('curl https://api.tavily.com/search -d \'{"query":"read https://example.invalid/page"}\'', ('web_extract', {'urls': ['https://example.invalid/page']})),
    ('curl https://api.tavily.com/search -d \'{}\'', ('web_search', {'query': 'general', 'limit': 5})),
    ('printf fixture', None), ('curl file:///fixture', None),
    ('curl http://127.0.0.1:8123/fixture', None), ('wget http://localhost/fixture', None), ('curl http://0.0.0.0/fixture', None),
])
def test_available_web_tools_preserve_expected_fetch_routing(monkeypatch, command, expected):
    monkeypatch.setattr(tools, 'web_tools_available', lambda: True)
    assert executor._redirect_bash_web({'command': command}) == expected


@pytest.mark.parametrize('enabled,available', [(False, True), (True, False)])
def test_web_redirect_never_runs_without_enabled_available_tools(monkeypatch, enabled, available):
    monkeypatch.setattr(tools, 'web_tools_available', lambda: available)
    assert executor._redirect_bash_web({'command': 'curl https://example.invalid'}, enabled=enabled) is None


def test_pending_mcp_approval_preserves_request_identity_and_description():
    payload = {'decision': 'pending', 'request_id': ' req-one ', 'server_name': ' fixture ', 'profile': 'fixture', 'reason': 'needs fixture tool', 'description': 'Fixture provider'}
    result = executor._pending_mcp_enable_approval(json.dumps(payload), 'suggest_enable_mcp')
    assert result == {**{k:payload[k] for k in ['profile', 'reason', 'description']}, 'request_id': 'req-one', 'server_name': 'fixture'}


@pytest.mark.parametrize('payload', ['bad-json', '[]', '{}', '{"decision":"done"}', '{"decision":"pending","request_id":"r"}', '{"decision":"pending","server_name":"s"}'])
def test_invalid_mcp_approval_cannot_become_an_actionable_request(payload):
    assert executor._pending_mcp_enable_approval(payload, 'suggest_enable_mcp') is None


def test_other_tools_cannot_inject_a_mcp_approval_request():
    assert executor._pending_mcp_enable_approval('{"decision":"pending","request_id":"r","server_name":"s"}', 'Read') is None


@pytest.mark.parametrize('name', ['fixture-skill', '', '   '])
def test_deleted_skill_summary_only_claims_the_confirmed_operation(name):
    text = executor._terminal_skill_manage_output('skill_manage', {'action': 'delete', 'name': name}, '{"success":true}')
    assert text and 'удалён' in text
    assert 'создан' not in text and 'проверен' not in text and 'открыт' not in text


@pytest.mark.parametrize('result', ['bad-json', '[]', '{"success":false}', '{"success":"true"}', '{"success":1}'])
def test_unconfirmed_skill_deletion_does_not_report_success(result):
    assert executor._terminal_skill_manage_output('skill_manage', {'action': 'delete'}, result) is None


@pytest.mark.parametrize('name,value', [('Read', {'action':'delete'}), ('skill_manage', None), ('skill_manage', {'action':'create'})])
def test_other_skill_actions_do_not_trigger_terminal_delete_summary(name, value):
    assert executor._terminal_skill_manage_output(name, value, '{"success":true}') is None


@pytest.mark.parametrize('value,expected', [
    ('STATUS: COMPLETE\nSaved files:\n- /fixture/one.png\n- relative.png\n- /fixture/two.png', ['/fixture/one.png', '/fixture/two.png']),
    ('STATUS: PENDING\n- /fixture/one.png', []), ('STATUS: COMPLETE\n- relative.png', []),
])
def test_fal_terminal_outputs_require_complete_status_and_absolute_saved_paths(value, expected):
    assert executor._extract_terminal_fal_saved_files(value) == expected
    assert bool(executor._terminal_fal_output('fal_generate_image', value)) is bool(expected)


def test_fal_edit_and_generate_results_keep_their_distinct_descriptions():
    result = 'STATUS: COMPLETE\n- /fixture/image.png'
    assert 'отредактировал' in executor._terminal_fal_output('fal_edit_image', result)
    assert 'создано' in executor._terminal_fal_output('fal_generate_image', result)
