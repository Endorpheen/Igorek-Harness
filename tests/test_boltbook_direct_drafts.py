"""Direct draft transport contracts with fake executors and temporary outputs."""
import json
import subprocess
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from factory.core import boltbook_client as b
from factory.core import mcp_config
from factory.core.vault import runtime as vault


@pytest.fixture
def draft(tmp_path, monkeypatch):
    root = tmp_path / 'draft'
    root.mkdir()
    monkeypatch.setattr(b.tempfile, 'mkdtemp', lambda **k: str(root))
    config = {'backend': 'claude', 'provider': 'fixture-provider', 'env': {'FIXTURE_PROFILE': 'yes'}}
    monkeypatch.setattr(b, 'get_profile_config', lambda _: config)
    command = Mock(return_value=['fixture-agent-never-executed'])
    monkeypatch.setattr(b, 'build_backend_command', command)
    decision = SimpleNamespace(allowed=True, env={'FIXTURE_EPHEMERAL': 'not-a-key'})
    broker = Mock()
    broker.get_ephemeral_env.return_value = decision
    monkeypatch.setattr(vault, 'build_services', Mock(return_value=SimpleNamespace(broker=broker)))
    sync = Mock()
    monkeypatch.setattr(mcp_config, 'sync_codex_mcp_for_profile', sync)
    materialize = Mock(return_value=tmp_path / 'fixture-mcp.json')
    monkeypatch.setattr(mcp_config, 'materialize_profile_mcp_config', materialize)
    executor = Mock(return_value=subprocess.CompletedProcess([], 0, '{"type":"result","result":"answer"}\n', ''))
    monkeypatch.setattr(b.subprocess, 'run', executor)
    return SimpleNamespace(root=root, config=config, command=command, broker=broker, executor=executor, sync=sync, materialize=materialize)


def call():
    return b._run_profile_prompt_direct(profile='fixture', prompt='draft prompt', timeout_s=7)


def test_claude_receives_structured_input_and_ephemeral_env(draft):
    assert call() == 'answer'
    kwargs = draft.executor.call_args.kwargs
    payload = json.loads(kwargs['input'])
    assert payload['stage'] == 'boltbook_draft' and payload['input']['text'] == 'draft prompt'
    assert payload['limits']['timeout_s'] == kwargs['timeout'] == 7
    assert kwargs['cwd'] == str(draft.root)
    assert kwargs['env']['FIXTURE_PROFILE'] == 'yes' and kwargs['env']['FIXTURE_EPHEMERAL'] == 'not-a-key'
    assert draft.command.call_args.kwargs['mcp_config_path'].endswith('fixture-mcp.json')
    draft.broker.get_ephemeral_env.assert_called_once_with(service='fixture-provider', agent_id='boltbook', ttl=900)
    assert not draft.root.exists()


@pytest.mark.parametrize('backend', ['claude', 'codex'])
def test_vault_unavailable_does_not_mask_public_output(draft, monkeypatch, backend):
    draft.config['backend'] = backend
    monkeypatch.setattr(vault, 'build_services', Mock(side_effect=RuntimeError('fixture locked')))
    draft.executor.return_value = subprocess.CompletedProcess([], 0, '{"type":"result","result":"answer"}' if backend == 'claude' else 'answer', '')
    assert call() == 'answer'
    assert 'FIXTURE_EPHEMERAL' not in draft.executor.call_args.kwargs['env']
    assert not draft.root.exists()


@pytest.mark.parametrize('mcp_sync_error', [True, False])
def test_codex_plain_input_survives_mcp_sync_failure(draft, mcp_sync_error):
    draft.config['backend'] = 'codex'
    draft.sync.side_effect = RuntimeError('fixture sync') if mcp_sync_error else None
    draft.executor.return_value = subprocess.CompletedProcess([], 0, '  comment  ', '')
    assert call() == 'comment'
    text = draft.executor.call_args.kwargs['input']
    assert 'draft prompt' in text and 'fixture' in text and str(draft.root) in text
    draft.sync.assert_called_once_with('fixture')
    draft.materialize.assert_not_called()
    assert not draft.root.exists()


@pytest.mark.parametrize('stdout,expected', [
    ('noise\n{bad}\n{"type":"result","result":" result "}', 'result'),
    ('{"type":"assistant","message":{"content":[null,{"type":"image"},{"type":"text","text":"one"},{"type":"text","text":"two"},{"type":"text","text":null}]}}', 'one\ntwo'),
    ('{"type":"assistant","message":null}\n{"type":"result","result":"final"}', 'final'),
    ('{"type":"assistant","message":{"content":"bad"}}\n{"type":"result","result":"final"}', 'final'),
])
def test_claude_uses_only_text_output_blocks(draft, stdout, expected):
    draft.executor.return_value = subprocess.CompletedProcess([], 0, stdout, '')
    assert call() == expected
    assert not draft.root.exists()


def test_result_file_is_authoritative_and_nested_scratch_is_removed(draft):
    def execute(*a, **k):
        (draft.root / 'result.json').write_text(json.dumps({'output': {'text': 'file answer'}}))
        nested = draft.root / 'nested' / 'deeper'
        nested.mkdir(parents=True)
        (nested / 'scratch.txt').write_text('fixture')
        return subprocess.CompletedProcess([], 0, '{"type":"result","result":"stdout answer"}', '')
    draft.executor.side_effect = execute
    assert call() == 'file answer'
    assert not draft.root.exists()


@pytest.mark.parametrize('returncode,stdout,error_type,error_text', [(0, '', ValueError, 'empty draft'), (3, '', RuntimeError, 'fixture stderr'), (0, '{"type":"result","result":8}', ValueError, 'empty draft')])
def test_absent_output_has_truthful_failure_and_cleanup(draft, returncode, stdout, error_type, error_text):
    draft.executor.return_value = subprocess.CompletedProcess([], returncode, stdout, 'fixture stderr')
    with pytest.raises(error_type, match=error_text):
        call()
    assert not draft.root.exists()


def test_nonzero_executor_with_usable_result_preserves_result(draft):
    draft.executor.return_value = subprocess.CompletedProcess([], 2, '{"type":"result","result":"usable"}', 'fixture warning')
    assert call() == 'usable'
    assert not draft.root.exists()


def test_executor_timeout_cleans_owned_scratch(draft):
    draft.executor.side_effect = subprocess.TimeoutExpired(['fixture'], 7)
    with pytest.raises(subprocess.TimeoutExpired):
        call()
    assert not draft.root.exists()


def test_unsupported_backend_never_executes_and_cleans_directory(draft):
    draft.config['backend'] = 'cc-backend'
    with pytest.raises(ValueError, match='Unsupported backend'):
        call()
    draft.executor.assert_not_called()
    assert not draft.root.exists()


def test_malformed_result_file_fails_without_using_unrelated_stdout(draft):
    def execute(*a, **k):
        (draft.root / 'result.json').write_text('{')
        return subprocess.CompletedProcess([], 0, '{"type":"result","result":"other"}', '')
    draft.executor.side_effect = execute
    with pytest.raises(json.JSONDecodeError):
        call()
    assert not draft.root.exists()
