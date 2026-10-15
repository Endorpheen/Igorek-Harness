"""Historical inference claims: protect paid work and visible control-file targets."""
import ast
import asyncio
import json
import time
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest

from factory.api_server_core import file_policy
from factory.api_server_core.request_auth import authentication_error


def middleware():
    source = Path(__file__).parents[1] / 'factory/api_server_core/server.py'
    tree = ast.parse(source.read_text())
    names = {'_error_payload', '_with_request_id', 'request_lifecycle_middleware'}
    nodes = [n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name in names]
    for node in nodes:
        node.decorator_list = []
    class Response:
        def __init__(self, status_code=200, content=None):
            self.status_code, self.content, self.headers = status_code, content, {}
    namespace = {'uuid': uuid, 'time': time, 'JSONResponse': Response,
                 'MODEL_BACKEND': 'auto', 'GENERATOR': SimpleNamespace(backend='openrouter'),
                 'SERVER_READY': True, 'PUBLIC_ENDPOINTS': {'/health'}, 'QUIET_LOG_PATHS': set()}
    module = ast.Module(body=[ast.ImportFrom(module='__future__', names=[ast.alias(name='annotations')], level=0), *nodes], type_ignores=[])
    exec(compile(ast.fix_missing_locations(module), str(source), 'exec'), namespace)
    return namespace['request_lifecycle_middleware'], namespace, Response


@pytest.mark.parametrize('headers', [{}, {'authorization': 'Bearer wrong'}, {'x-api-key': 'wrong'}])
def test_unauthorized_request_never_reaches_generation_or_compression(monkeypatch, headers):
    monkeypatch.setenv('IGOREK_INFERENCE_API_KEY', 'fixture-ingress-key')
    handler, _, Response = middleware()
    calls = []
    async def downstream(request):
        calls.append(request)
        return Response()
    request = SimpleNamespace(state=SimpleNamespace(), url=SimpleNamespace(path='/v1/messages'), headers=headers, method='POST')
    response = asyncio.run(handler(request, downstream))
    assert response.status_code == 401 and calls == []
    assert response.headers['x-request-id'].startswith('req_')
    assert 'fixture-ingress-key' not in json.dumps(response.content)


@pytest.mark.parametrize('backend,env', [
    ('openrouter', {}), ('gguf', {'CONTEXT_COMPRESSION_BASE_URL': 'https://fixture.invalid/v1'}),
    ('gguf', {'CONTEXT_COMPRESSION_BASE_URL': 'http://127.0.0.1:9999/v1'}),
    ('gguf', {'CONTEXT_COMPRESSION_API_KEY': 'fixture-upstream-key'}),
])
def test_unconfigured_paid_or_explicit_compression_upstream_fails_closed(backend, env):
    assert authentication_error({}, backend=backend, env=env)[0] == 503


@pytest.mark.parametrize('headers', [
    {'x-api-key': 'fixture'}, {'authorization': 'Bearer fixture'},
    {'x-api-key': 'fixture', 'authorization': 'bearer fixture'},
])
def test_authenticated_clients_remain_usable(headers):
    assert authentication_error(headers, backend='openrouter', env={'IGOREK_INFERENCE_API_KEY': 'fixture'}) is None


@pytest.mark.parametrize('headers', [
    {'x-api-key': 'fixture', 'authorization': 'Bearer wrong'},
    {'authorization': 'Basic fixture'}, {'authorization': 'Bearer '},
    {'x-api-key': 'x' * 5000}, {'x-api-key': '\u2603'},
])
def test_conflicting_or_malformed_credentials_are_refused(headers):
    assert authentication_error(headers, backend='openrouter', env={'IGOREK_INFERENCE_API_KEY': 'fixture'})[0] == 401


def test_anonymous_local_generation_without_external_compression_is_preserved():
    assert authentication_error({}, backend='gguf', env={}) is None
    assert authentication_error({}, backend='gguf', env={'CONTEXT_COMPRESSION_ENABLED': '0', 'CONTEXT_COMPRESSION_BASE_URL': 'https://fixture.invalid'}) is None


@pytest.mark.parametrize('prefix', [
    'env rm', '/usr/bin/env -i /usr/bin/rm', 'command rm', 'timeout 1s rm',
    'env LANG=C rm', 'python3', 'env -S "rm {path}"',
    'bash -c "rm {path}"', 'rm --output={path}',
])
def test_wrappers_cannot_authorize_control_file_mutations(tmp_path, monkeypatch, prefix):
    target = tmp_path / 'policy.yaml';target.write_text('version: 1\n')
    monkeypatch.setattr(file_policy, 'PROTECTED_POLICY_PATHS', {target.resolve()})
    state = SimpleNamespace(last_successful_read_file=None, last_tool_file=None, project_root=str(tmp_path), policy_denial=None)
    command = prefix.format(path=target) if '{path}' in prefix else prefix + ' ' + str(target)
    allowed, reason = file_policy.validate_existing_file_policy(state, 'Bash', {'command': command}, tool_target_from_input=lambda *_: '')
    assert not allowed and reason.startswith('protected_policy_file:')
    assert target.read_text() == 'version: 1\n'


@pytest.mark.parametrize('prefix', ['cat', '/usr/bin/cat --', 'env LANG=C cat', 'head -n 1', 'stat'])
def test_ordinary_control_file_reads_remain_usable(tmp_path, monkeypatch, prefix):
    target = tmp_path / 'policy.yaml';target.write_text('version: 1\n')
    monkeypatch.setattr(file_policy, 'PROTECTED_POLICY_PATHS', {target.resolve()})
    state = SimpleNamespace(last_successful_read_file=None, last_tool_file=None, project_root=str(tmp_path), policy_denial=None)
    allowed, reason = file_policy.validate_existing_file_policy(state, 'Bash', {'command': prefix + ' ' + str(target)}, tool_target_from_input=lambda *_: '')
    assert allowed and not reason
