"""MCP profile materialization and bounded external CLI synchronization."""
import json
import os
import subprocess
from types import SimpleNamespace

import pytest

from factory.core import mcp_config as mcp


@pytest.fixture
def config(tmp_path):
    root = tmp_path / 'data'
    profiles = root / 'profiles'
    profiles.mkdir(parents=True)
    def write(backend='codex', enabled=None, registry=None):
        (profiles / 'fixture.json').write_text(json.dumps({'backend': backend, 'mcp_servers': enabled or []}))
        mcp.save_mcp_registry({'mcpServers': registry or {}}, str(root))
        return str(root)
    return SimpleNamespace(root=root, write=write)


def proc(stdout='', stderr='', code=0):
    return subprocess.CompletedProcess(['fixture-codex'], code, stdout, stderr)


@pytest.mark.parametrize('backend', ['claude', 'cc-backend'])
def test_sync_other_backend_does_not_invoke_external_process(config, monkeypatch, backend):
    data = config.write(backend=backend, enabled=['one'])
    monkeypatch.setattr(mcp.subprocess, 'run', lambda *a, **k: pytest.fail('Unexpected process'))
    result = mcp.sync_codex_mcp_for_profile('fixture', data)
    assert result['status'] == 'skipped' and result['reason'] == 'backend_not_codex'


def test_empty_selection_does_not_probe_or_modify_codex(config, monkeypatch):
    data = config.write()
    monkeypatch.setattr(mcp.subprocess, 'run', lambda *a, **k: pytest.fail('Unexpected process'))
    assert mcp.sync_codex_mcp_for_profile('fixture', data)['enabled'] == []


@pytest.mark.parametrize('timeout,expected', [(7, 7), (1, 5), (0, 5)])
def test_catalog_listing_obeys_configured_sync_timeout(config, monkeypatch, timeout, expected):
    data = config.write(enabled=['one'], registry={'one': {'type': 'stdio', 'command': 'worker'}})
    calls = []
    def run(argv, **kwargs):
        calls.append(argv)
        assert kwargs.get('timeout') == expected, 'Initial MCP listing must have the configured deadline too'
        return proc('[]')
    monkeypatch.setattr(mcp.subprocess, 'run', run)
    result = mcp.sync_codex_mcp_for_profile('fixture', data, codex_bin='fixture-codex', timeout_s=timeout)
    assert result['added'] == ['one'] and len(calls) == 2


@pytest.mark.parametrize('payload,expected', [
    ('[" alpha ","",{"name":" beta "},{"name":"alpha"},{},null,9]', {'alpha', 'beta'}),
    ('{"alpha":{}," beta ":{},"":{}}', {'alpha', 'beta'}),
    ('', set()), ('null', set()), ('7', set()), ('[]', set()),
])
def test_catalog_shapes_preserve_only_real_unique_names(monkeypatch, payload, expected):
    monkeypatch.setattr(mcp, 'resolve_codex_bin', lambda: 'fixture-codex')
    def run(argv, **kwargs):
        assert argv == ['fixture-codex', 'mcp', 'list', '--json']
        assert kwargs['capture_output'] and kwargs['text']
        return proc(payload)
    monkeypatch.setattr(mcp.subprocess, 'run', run)
    assert mcp._codex_mcp_list_names() == expected


@pytest.mark.parametrize('stdout,stderr,reason', [('out', 'err', 'err'), ('out', '', 'out'), ('', '', 'exit 8')])
def test_catalog_process_failure_has_actionable_reason(monkeypatch, stdout, stderr, reason):
    monkeypatch.setattr(mcp.subprocess, 'run', lambda *a, **k: proc(stdout, stderr, 8))
    with pytest.raises(RuntimeError, match=reason):
        mcp._codex_mcp_list_names(codex_bin='fixture-codex')


def test_corrupt_catalog_json_is_not_treated_as_empty_success(monkeypatch):
    monkeypatch.setattr(mcp.subprocess, 'run', lambda *a, **k: proc('{bad'))
    with pytest.raises(json.JSONDecodeError):
        mcp._codex_mcp_list_names(codex_bin='fixture-codex')


def test_catalog_timeout_aborts_before_any_addition(config, monkeypatch):
    data = config.write(enabled=['one'], registry={'one': {'type': 'stdio', 'command': 'worker'}})
    calls = []
    def run(argv, **kwargs):
        calls.append(argv)
        raise subprocess.TimeoutExpired(argv, 5)
    monkeypatch.setattr(mcp.subprocess, 'run', run)
    with pytest.raises(subprocess.TimeoutExpired):
        mcp.sync_codex_mcp_for_profile('fixture', data, codex_bin='fixture-codex')
    assert len(calls) == 1 and calls[0][2] == 'list'


def test_mixed_registry_keeps_existing_entries_and_continues_past_invalid_servers(config, monkeypatch):
    enabled = ['old', 'absent', 'disabled', 'bad', 'new', 'new']
    data = config.write(enabled=enabled, registry={
        'old': {'type': 'stdio', 'command': 'old'}, 'disabled': {'disabled': True},
        'bad': {'type': 'http', 'url': ''}, 'new': {'type': 'stdio', 'command': 'new'},
    })
    calls, progress = [], []
    def run(argv, **kwargs):
        calls.append(argv)
        return proc('["old"]' if argv[2] == 'list' else '')
    monkeypatch.setattr(mcp.subprocess, 'run', run)
    result = mcp.sync_codex_mcp_for_profile('fixture', data, codex_bin='fixture-codex', progress_cb=progress.append)
    assert result['status'] == 'partial' and result['added'] == ['new']
    assert result['skipped_existing'] == ['old', 'new']
    assert [x['name'] for x in result['errors']] == ['absent', 'disabled', 'bad']
    assert len(calls) == 2 and any('done status=partial' in x for x in progress)


@pytest.mark.parametrize('failure,reason', [('timeout', 'timeout_after_9s'), ('stderr', 'fixture error'), ('stdout', 'fixture output'), ('empty', 'exit 4')])
def test_add_failure_does_not_prevent_later_server(config, monkeypatch, failure, reason):
    data = config.write(enabled=['first', 'second'], registry={x: {'type': 'stdio', 'command': x} for x in ['first', 'second']})
    def run(argv, **kwargs):
        if argv[2] == 'list': return proc('[]')
        if argv[3] == 'second': return proc()
        if failure == 'timeout': raise subprocess.TimeoutExpired(argv, 9)
        return proc('fixture output' if failure == 'stdout' else '', 'fixture error' if failure == 'stderr' else '', 4)
    monkeypatch.setattr(mcp.subprocess, 'run', run)
    result = mcp.sync_codex_mcp_for_profile('fixture', data, codex_bin='fixture-codex', timeout_s=9)
    assert result['added'] == ['second'] and result['errors'] == [{'name': 'first', 'error': reason}]
    assert result['status'] == 'partial'


def test_all_invalid_selection_reports_error_without_adding(config, monkeypatch):
    data = config.write(enabled=['bad'], registry={'bad': {'type': 'unsupported'}})
    monkeypatch.setattr(mcp.subprocess, 'run', lambda *a, **k: proc('[]'))
    assert mcp.sync_codex_mcp_for_profile('fixture', data, codex_bin='fixture-codex', progress_cb=42)['status'] == 'error'


def test_codex_environment_is_copied_and_stringified(config, monkeypatch):
    data = config.write(enabled=['one'], registry={'one': {'type': 'stdio', 'command': 'worker'}})
    monkeypatch.setenv('MCP_FIXTURE_ORIGINAL', 'retained')
    def run(argv, **kwargs):
        assert kwargs['env']['MCP_FIXTURE_ORIGINAL'] == 'retained'
        assert kwargs['env']['MCP_FIXTURE_OVERRIDE'] == '7'
        return proc('[]')
    monkeypatch.setattr(mcp.subprocess, 'run', run)
    result = mcp.sync_codex_mcp_for_profile('fixture', data, codex_bin='fixture-codex', codex_env={'MCP_FIXTURE_OVERRIDE': 7})
    assert result['status'] == 'ok' and 'MCP_FIXTURE_OVERRIDE' not in os.environ


@pytest.mark.parametrize('backend,enabled,registry', [
    ('codex', ['one'], {'one': {'type': 'stdio', 'command': 'worker'}}),
    ('claude', [], {}), ('claude', ['missing'], {}),
    ('claude', ['disabled'], {'disabled': {'disabled': True}}),
    ('claude', ['invalid'], {'invalid': 'not a configuration'}),
])
def test_materialization_never_creates_usable_config_for_unavailable_servers(config, backend, enabled, registry):
    data = config.write(backend=backend, enabled=enabled, registry=registry)
    assert mcp.materialize_profile_mcp_config('fixture', data) is None
    assert not (config.root / 'mcp/generated').exists()


def test_materialized_config_contains_only_enabled_available_entries(config):
    entry = {'type': 'http', 'url': 'https://example.invalid/mcp', 'headers': {'X-Fixture': 'value'}}
    data = config.write(backend='claude', enabled=['good', 'missing', 'bad'], registry={'good': entry, 'bad': None, 'unused': entry})
    before = (config.root / 'mcp/servers.json').read_bytes()
    path = mcp.materialize_profile_mcp_config('fixture', data)
    assert path == config.root / 'mcp/generated/fixture.json'
    assert json.loads(path.read_text()) == {'mcpServers': {'good': entry}}
    assert (config.root / 'mcp/servers.json').read_bytes() == before


def test_initial_catalog_deadline_terminates_real_child(config, monkeypatch):
    import sys
    import time
    data = config.write(enabled=['one'], registry={'one': {'type': 'stdio', 'command': 'worker'}})
    real_run = subprocess.run
    calls = []
    def isolated_run(argv, **kwargs):
        calls.append(argv)
        assert kwargs.get('timeout') == 5
        return real_run([sys.executable, '-I', '-c', 'import time; time.sleep(30)'], **kwargs)
    monkeypatch.setattr(mcp.subprocess, 'run', isolated_run)
    started = time.monotonic()
    with pytest.raises(subprocess.TimeoutExpired):
        mcp.sync_codex_mcp_for_profile('fixture', data, codex_bin='fixture-codex', timeout_s=1)
    assert time.monotonic() - started < 15
    assert len(calls) == 1 and calls[0][2] == 'list'


@pytest.mark.parametrize('bearer,expected', [(' FIXTURE_TOKEN ', ['--bearer-token-env-var', 'FIXTURE_TOKEN']), (' ', [])])
def test_http_command_normalizes_transport_url_and_bearer_name(bearer, expected):
    cmd = mcp._codex_add_command_for_server('fixture', {'type': ' HTTP ', 'url': ' https://example.invalid/mcp ', 'bearer_token_env_var': bearer}, codex_bin='fixture-codex')
    assert cmd == ['fixture-codex', 'mcp', 'add', 'fixture', '--url', 'https://example.invalid/mcp', *expected]
