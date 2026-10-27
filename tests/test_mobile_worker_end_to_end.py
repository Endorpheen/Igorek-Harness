import json
import multiprocessing
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest


def _run_fixture_worker(root, stop):
    allowed = {'PATH', 'LANG', 'LC_ALL', 'SYSTEMROOT', 'WINDIR'}
    inherited = {key: value for key, value in os.environ.items() if key in allowed}
    os.environ.clear()
    os.environ.update(inherited)
    os.environ.update({
        'HOME': str(root / 'home'),
        'IGOREK_FACTORY_HOME': str(root / 'data'),
        'FACTORY_DATA_DIR': str(root / 'data'),
        'FACTORY_PROFILES_DIR': str(root / 'data' / 'profiles'),
        'FACTORY_VAULT_ENABLED': 'false',
        'MCP_SERVERS': '',
        'IGOREK_ISOLATION_MODE': 'off',
    })
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from factory.core import cc_backend_executor
    from factory.core.worker import Worker
    expected = Path(__file__).resolve().parents[1] / 'factory' / 'core' / 'cc_backend_executor.py'
    assert Path(cc_backend_executor.__file__).resolve() == expected
    Worker('fixture', poll_interval=0.1).run(stop_event=stop)


@pytest.mark.parametrize("disconnect", [False, True])
def test_mobile_submission_reaches_worker_and_local_provider(tmp_path, monkeypatch, disconnect):
    root = tmp_path / 'owned'
    data = root / 'data'
    profiles = data / 'profiles'
    profiles.mkdir(parents=True)
    (root / 'home').mkdir()
    work = root / 'work'
    work.mkdir()
    requests = []

    class Provider(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            requests.append((self.path, body))
            events = [
                {'model': 'fixture-model', 'choices': [{'delta': {'content': 'fixture answer'}}]},
                {'model': 'fixture-model', 'choices': [{'delta': {}, 'finish_reason': 'stop'}]},
            ]
            wire = b''.join(('data: ' + json.dumps(event) + '\n\n').encode() for event in (events[:1] if disconnect else events))
            if not disconnect:
                wire += b'data: [DONE]\n\n'
            self.send_response(200)
            self.send_header('Content-Type', 'text/event-stream')
            self.send_header('Content-Length', str(len(wire)))
            self.end_headers()
            self.wfile.write(wire)

    server = ThreadingHTTPServer(('127.0.0.1', 0), Provider)
    thread = threading.Thread(target=server.serve_forever, kwargs={'poll_interval': 0.01}, daemon=True)
    thread.start()
    (profiles / 'fixture.json').write_text(json.dumps({
        'backend': 'cc-backend', 'provider': 'openrouter', 'model': 'fixture-model',
        'api_format': 'openai', 'base_url': f'http://127.0.0.1:{server.server_port}/v1',
        'filesystem_mode': 'workspace', 'mcp_servers': [], 'checkpoints': {'enabled': False},
    }))
    for key, value in {
        'HOME': str(root / 'home'), 'IGOREK_FACTORY_HOME': str(data),
        'FACTORY_DATA_DIR': str(data), 'FACTORY_PROFILES_DIR': str(profiles),
        'FACTORY_VAULT_ENABLED': 'false', 'MCP_SERVERS': '', 'IGOREK_ISOLATION_MODE': 'off',
    }.items():
        monkeypatch.setenv(key, value)
    from factory.core.mobile_bridge.service import MobileService
    from factory.core.runtime import get_db_path
    from factory.core.schema import initialize_database
    initialize_database(get_db_path())
    service = MobileService(profile='fixture', workdir=str(work), available_profiles=lambda: ['fixture'])
    session = service.create_session(profile='fixture', cwd=str(work))['session_id']
    receipt = service.send_message(session, 'fixture question', idempotency_key='fixture-request')
    task_id = receipt['task_id']
    context = multiprocessing.get_context('spawn')
    stop = context.Event()
    process = context.Process(target=_run_fixture_worker, args=(root, stop))
    try:
        process.start()
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            task = service.get_task(task_id)
            if task['status'] in {'ok', 'fail', 'dead_letter', 'cancelled', 'timeout'}:
                break
            assert process.is_alive(), f'worker exited: {process.exitcode}'
            time.sleep(0.05)
        else:
            raise AssertionError('fixture task did not finish')
        if disconnect:
            assert task['status'] != 'ok', task
            assert task['output_text'] != 'fixture answer'
        else:
            assert task['status'] == 'ok', task
            assert task['output_text'] == 'fixture answer'
        assert len(requests) == 1
        path, payload = requests[0]
        assert path == '/v1/chat/completions'
        assert payload['model'] == 'fixture-model'
        assert any('fixture question' in str(message.get('content', '')) for message in payload['messages'])
        repeated = service.send_message(session, 'fixture question', idempotency_key='fixture-request')
        assert repeated['task_id'] == task_id and repeated['duplicate'] is True
        assert [message['text'] for message in service.get_session(session)['messages'] if message['role'] == 'user'] == ['fixture question']
    finally:
        stop.set()
        if process.pid is not None:
            process.join(10)
            if process.is_alive():
                process.terminate()
                process.join(10)
        server.shutdown()
        server.server_close()
        thread.join(5)
        assert not thread.is_alive()
