import ast
import http.client
import json
import os
import re
import subprocess
import sys
import threading
import time
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

import pytest

from factory.core.artifact_files import open_workspace_artifact
from factory.core.http_server import BoundedHTTPServer, HeaderDeadlineMixin


@pytest.fixture
def api(tmp_path):
    root = Path(__file__).parents[1]
    path = 'factory/factory_api.py'
    source = (subprocess.check_output(['git', 'show', 'HEAD:' + path], cwd=root).decode()
              if os.environ.get('IGOREK_SECURITY_BASELINE') else (root / path).read_text())
    tree = ast.parse(source)
    tree.body = [node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == 'FactoryAPIHandler']
    scope = dict(BaseHTTPRequestHandler=BaseHTTPRequestHandler, json=json, urlparse=urlparse,
                 HeaderDeadlineMixin=HeaderDeadlineMixin,
                 parse_qs=parse_qs, os=os, re=re, Path=Path, time=time, uuid=uuid,
                 API_KEY='fixture-key', CORS_ORIGINS=[], WORKSPACE_ROOT=str(tmp_path),
                 open_workspace_artifact=open_workspace_artifact,
                 LOGGER=SimpleNamespace(error=lambda *a, **k: None, warn=lambda *a, **k: None, info=lambda *a, **k: None))
    exec(compile(tree, str(root / path), 'exec'), scope)
    handler = scope['FactoryAPIHandler']
    for method in ('get_all_tasks', 'get_task_status', 'get_security_events', 'get_skills',
                   'list_schedules', 'get_app_server_session', 'get_app_server_events', 'ready'):
        setattr(handler, method, lambda self, *args: self._send_json_response(200, {'private': 'fixture-data'}))
    server = BoundedHTTPServer(('127.0.0.1', 0), handler)
    scope['_server'] = server
    scope['_handler'] = handler
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()

    def get(path, key=None, origin=None):
        client = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=3)
        headers = {}
        if key is not None:
            headers['X-API-Key'] = key
        if origin is not None:
            headers['Origin'] = origin
        try:
            client.request('GET', path, headers=headers)
            response = client.getresponse()
            return response.status, response.read()
        finally:
            client.close()

    yield tmp_path, get, scope
    server.shutdown()
    server.server_close()
    worker.join(3)


@pytest.mark.parametrize('suffix', ['', '/'])
def test_private_user_input_post_requires_key_before_resolution(api, monkeypatch, suffix):
    _, _, scope = api
    resolved = []
    def resolve(self, task_id, request_id):
        resolved.append((task_id, request_id))
        self._send_json_response(200, {'resolved': True})
    monkeypatch.setattr(scope['_handler'], 'resolve_task_user_input', resolve)
    for key, expected in ((None, 401), ('wrong-key', 401), ('fixture-key', 200)):
        client = http.client.HTTPConnection('127.0.0.1', scope['_server'].server_port, timeout=3)
        headers = {'Content-Type': 'application/json', 'X-Request-ID': 'fixture-request'}
        if key is not None:
            headers['X-API-Key'] = key
        client.request('POST', '/task/task-1/user-input/request-1' + suffix,
                       json.dumps({'response': {'choice': 'fixture'}}), headers)
        response = client.getresponse()
        assert response.status == expected
        response.read()
        client.close()
    assert resolved == [('task-1', 'request-1')]


@pytest.mark.parametrize('path', ['/tasks', '/task/task-1', '/task/task-1/file/report.txt',
    '/skills', '/schedules', '/security/events', '/app-server/sessions/session-1',
    '/app-server/sessions/session-1/events?after=0'])
def test_private_get_requires_key_without_origin(api, path):
    _, get, _ = api
    for key in (None, 'wrong-key'):
        status, body = get(path, key)
        assert status == 401
        assert b'fixture-data' not in body


def test_authorized_reads_health_and_cors_remain_compatible(api):
    _, get, scope = api
    assert get('/tasks', 'fixture-key')[0] == 200
    assert get('/health')[0] == 200
    assert get('/ready')[0] == 200
    assert get('/tasks', 'fixture-key', 'https://foreign.example')[0] == 403
    scope['API_KEY'] = ''
    assert get('/tasks')[0] == 503
    assert get('/health')[0] == 200


def test_artifact_routes_keep_normal_and_temp_downloads(api):
    root, get, _ = api
    for base, route in ((root / 'task-123/results', '/task/task-123/file/'),
                        (root / 'temp_execution_task-123', '/task/task-123/file/temp/')):
        base.mkdir(parents=True)
        (base / 'report.txt').write_bytes(b'ordinary-artifact')
        assert get(route + 'report.txt', 'fixture-key') == (200, b'ordinary-artifact')
        assert get(route + 'missing.txt', 'fixture-key')[0] == 404


@pytest.mark.parametrize('kind', ['file', 'directory', 'results', 'task', 'temp'])
def test_artifact_routes_reject_links_at_every_untrusted_component(api, kind):
    root, get, _ = api
    outside = root.parent / (root.name + '-outside')
    outside.mkdir()
    (outside / 'private.txt').write_bytes(b'outside-canary')
    base = root / 'task-123/results'
    base.mkdir(parents=True)
    route = '/task/task-123/file/'
    if kind == 'file':
        (base / 'private.txt').symlink_to(outside / 'private.txt')
    elif kind == 'directory':
        (base / 'nested').symlink_to(outside, target_is_directory=True)
        route += 'nested/'
    elif kind == 'results':
        base.rmdir()
        base.symlink_to(outside, target_is_directory=True)
    elif kind == 'task':
        base.rmdir()
        base.parent.rmdir()
        (outside / 'results').symlink_to(outside, target_is_directory=True)
        base.parent.symlink_to(outside, target_is_directory=True)
    else:
        (root / 'temp_execution_task-123').symlink_to(outside, target_is_directory=True)
        route += 'temp/'
    status, body = get(route + 'private.txt', 'fixture-key')
    assert status == 400
    assert b'outside-canary' not in body


def test_artifact_rejects_parent_prefix_and_non_regular_files(api):
    root, get, _ = api
    base = root / 'task-123/results'
    base.mkdir(parents=True)
    other = base.with_name('results-other')
    other.mkdir()
    (other / 'private.txt').write_bytes(b'outside-canary')
    os.mkfifo(base / 'fifo')
    for path in ('../results-other/private.txt', 'fifo', 'nested/../../results-other/private.txt'):
        status, body = get('/task/task-123/file/' + path, 'fixture-key')
        assert status == 400
        assert b'outside-canary' not in body
    assert get('/task/../file/private.txt', 'fixture-key')[0] == 400


def test_directory_swap_cannot_redirect_open_artifact(tmp_path, monkeypatch):
    base = tmp_path / 'task/results'
    base.mkdir(parents=True)
    (base / 'report.txt').write_bytes(b'ordinary-artifact')
    external = tmp_path / 'external'
    external.mkdir()
    (external / 'report.txt').write_bytes(b'outside-canary')
    original = os.open

    def swap(path, *args, **kwargs):
        fd = original(path, *args, **kwargs)
        if path == 'results':
            base.rename(base.with_name('old-results'))
            base.symlink_to(external, target_is_directory=True)
        return fd

    monkeypatch.setattr(os, 'open', swap)
    with open_workspace_artifact(tmp_path, 'task/results/report.txt') as handle:
        assert handle.read() == b'ordinary-artifact'


def test_actual_api_import_database_and_http_download(tmp_path):
    script = '''
import http.client, json, threading
from pathlib import Path
from http.server import ThreadingHTTPServer
from factory import factory_api as api
with api.open_db(api.DB_PATH) as conn:
    conn.execute("INSERT INTO tasks (task_id, status, profile, input_text) VALUES (?, ?, ?, ?)",
                 ('fixture-task', 'ok', 'fixture', 'private-canary'))
base = Path(api.WORKSPACE_ROOT) / 'fixture-task/results'
base.mkdir(parents=True)
(base / 'report.txt').write_bytes(b'ordinary-artifact')
outside = Path(api.DB_PATH).parent / 'outside.txt'
outside.write_bytes(b'outside-canary')
(base / 'escape.txt').symlink_to(outside)
server = ThreadingHTTPServer(('127.0.0.1', 0), api.FactoryAPIHandler)
thread = threading.Thread(target=server.serve_forever, daemon=True)
thread.start()
def get(path, key=''):
    conn = http.client.HTTPConnection('127.0.0.1', server.server_port, timeout=3)
    try:
        conn.request('GET', path, headers={'X-API-Key': key})
        response = conn.getresponse()
        return response.status, response.read()
    finally: conn.close()
try:
    assert get('/tasks')[0] == 401
    status, body = get('/tasks', 'fixture-key')
    assert status == 200 and b'private-canary' in body
    assert get('/task/fixture-task/file/report.txt', 'fixture-key') == (200, b'ordinary-artifact')
    status, body = get('/task/fixture-task/file/escape.txt', 'fixture-key')
    assert status == 400 and b'outside-canary' not in body
    assert get('/health')[0] == 200
    print('FACTORY_API_SECURITY_E2E_PASS')
finally:
    server.shutdown(); server.server_close(); thread.join(3)
'''
    environment = dict(os.environ, FACTORY_DATA_DIR=str(tmp_path), IGOREK_FACTORY_HOME=str(tmp_path),
                       FACTORY_API_KEY='fixture-key', FACTORY_VAULT_ENABLED='1', MCP_SERVERS='',
                       FACTORY_BOLTBOOK_HEARTBEAT_ENABLED='false')
    result = subprocess.run([sys.executable, '-u', '-c', script], cwd=Path(__file__).parents[1],
                            env=environment, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr
    assert 'FACTORY_API_SECURITY_E2E_PASS' in result.stdout
