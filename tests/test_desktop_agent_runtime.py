import json
import sys
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'desktop'))
from agent_runtime import DesktopAgentRuntime, AgentBusyError, ProfileNotVerifiedError
from vault_owner import OwnerVault
from factory.core.vault.lifecycle import VaultLockedError
from factory.core.worker import Worker


class FakeWorker:
    instances = []

    def __init__(self, profile, *, poll_interval, auth_headers_provider):
        self.profile = profile
        self.auth_headers_provider = auth_headers_provider
        self.instances.append(self)

    def run(self, *, stop_event, execution_lock):
        stop_event.wait()


@pytest.fixture
def setup(tmp_path):
    owner = OwnerVault(tmp_path)
    owner.service.create('test-password')
    service = SimpleNamespace(profile='', available_profiles=None)
    runtime = DesktopAgentRuntime(tmp_path, owner.lifecycle, service, worker_factory=FakeWorker, execution=owner.execution)
    owner.agent = runtime
    profile = owner.providers.save('test-profile', 'fixture-model', 'https://fixture.invalid/v1', 'openai', 'private-test-key')
    yield owner, runtime, service, profile
    owner.close()


def verify(owner, profile):
    owner.service.check_provider_profile(profile['credential_id'], lambda *_: {'status': 'verified', 'reason': 'ok'})


def test_unverified_profile_never_publishes_or_starts(setup, tmp_path):
    owner, runtime, service, profile = setup
    with pytest.raises(ProfileNotVerifiedError):
        runtime.start(profile['credential_id'])
    assert not (tmp_path / 'profiles').exists()
    assert service.available_profiles() == []


def test_worker_uses_same_vault_and_public_config_without_key(setup, tmp_path, monkeypatch):
    owner, runtime, service, profile = setup
    verify(owner, profile)
    assert runtime.start(profile['credential_id']) == {'running': True, 'profile': 'test-profile'}
    worker = FakeWorker.instances[-1]
    config = json.loads((tmp_path / 'profiles/test-profile.json').read_text())
    assert config == profile['config']
    assert 'private-test-key' not in (tmp_path / 'profiles/test-profile.json').read_text()
    real = Worker.__new__(Worker)
    real.config = config
    real.auth_headers_provider = worker.auth_headers_provider
    monkeypatch.setenv('OPENCODE_API_KEY', 'wrong-env-key')
    assert real._cc_backend_request_headers('openai') == {'Authorization': 'Bearer private-test-key'}
    with pytest.raises(ValueError):
        worker.auth_headers_provider({**config, 'base_url': 'https://other.invalid/v1'}, 'openai')
    with pytest.raises(ValueError):
        worker.auth_headers_provider(config, 'anthropic')
    assert runtime.start(profile['credential_id'])['running']
    assert FakeWorker.instances[-1] is worker
    assert owner.dispatch({'id': 'lock', 'action': 'lock'})['result']['state'] == 'locked'
    assert service.available_profiles() == []
    with pytest.raises(VaultLockedError):
        real._cc_backend_request_headers('openai')
    owner.service.unlock('test-password')
    assert service.available_profiles() == []
    runtime.start(profile['credential_id'])
    assert FakeWorker.instances[-1] is not worker


def test_busy_lock_stop_switch_and_check_are_rejected_without_changes(setup):
    owner, runtime, service, profile = setup
    verify(owner, profile)
    runtime.start(profile['credential_id'])
    runtime.execution_lock.acquire()
    try:
        for action, fields in [('lock', {}), ('stop_agent', {}), ('check_profile', {'credential_id': profile['credential_id']})]:
            assert owner.dispatch({'id': 'busy', 'action': action, **fields})['error'] == 'agent_busy'
        assert owner.service.status()['state'] == 'unlocked'
        assert service.available_profiles() == ['test-profile']
        second = owner.providers.save('second', 'fixture', 'https://second.invalid/v1', 'anthropic', 'other-key')
        verify(owner, second)
        with pytest.raises(AgentBusyError):
            runtime.start(second['credential_id'])
    finally:
        runtime.execution_lock.release()
    assert runtime.stop()['running'] is False


@pytest.mark.parametrize('check_status', ['verified', 'failed'])
def test_other_profile_check_keeps_busy_worker_and_vault_access(setup, monkeypatch, check_status):
    owner, runtime, service, profile = setup
    verify(owner, profile)
    runtime.start(profile['credential_id'])
    thread, worker = runtime.thread, FakeWorker.instances[-1]
    second = owner.providers.save('second', 'fixture', 'https://second.invalid/v1', 'openai', 'other-key')
    entered, release = threading.Event(), threading.Event()
    replies = []

    def checker(config, key):
        assert config == second['config'] and key == 'other-key'
        entered.set()
        assert release.wait(5)
        return {'status': check_status, 'reason': 'ok' if check_status == 'verified' else 'authentication'}

    monkeypatch.setattr('provider_probe.check_connection', checker)
    runtime.execution_lock.acquire()
    checking = threading.Thread(target=lambda: replies.append(owner.dispatch(
        {'id': 'parallel', 'action': 'check_profile', 'credential_id': second['credential_id']})))
    checking.start()
    try:
        assert entered.wait(2)
        accessed = threading.Event()
        headers = []
        reader = threading.Thread(target=lambda: (headers.append(worker.auth_headers_provider(profile['config'], 'openai')), accessed.set()))
        reader.start()
        try:
            assert accessed.wait(2), 'Connection probe blocked active worker access to Vault'
            assert headers == [{'Authorization': 'Bearer private-test-key'}]
        finally:
            release.set()
            reader.join(3)
        checking.join(3)
        assert replies[0]['result']['check']['status'] == check_status
        assert runtime.thread is thread and thread.is_alive()
        assert FakeWorker.instances[-1] is worker
        assert service.available_profiles() == ['test-profile']
        assert owner.dispatch({'id': 'same', 'action': 'check_profile', 'credential_id': profile['credential_id']})['error'] == 'agent_busy'
    finally:
        release.set()
        checking.join(3)
        runtime.execution_lock.release()


@pytest.mark.parametrize('change', ['edit', 'lock'])
def test_check_does_not_commit_after_profile_or_vault_changes(setup, change):
    owner, runtime, service, profile = setup
    from factory.core.vault.lifecycle import VaultBusyError

    def checker(*_):
        if change == 'lock':
            owner.service.lock()
            owner.service.unlock('test-password')
        else:
            owner.providers.update(profile['credential_id'], 'test-profile', 'new-model',
                                   'https://fixture.invalid/v1', 'openai', '')
        return {'status': 'verified', 'reason': 'ok'}

    with pytest.raises(VaultBusyError):
        owner.service.check_provider_profile(profile['credential_id'], checker)
    saved = owner.providers.list_profiles()[0]
    assert saved['status'] == 'unverified'
    if change == 'edit':
        assert saved['config']['model'] == 'new-model'


def test_legacy_worker_auth_path_remains_compatible(monkeypatch):
    monkeypatch.setenv('OPENCODE_API_KEY', 'legacy-key')
    worker = Worker.__new__(Worker)
    worker.config = {'provider': 'custom', 'api_format': 'openai'}
    assert worker._cc_backend_request_headers('openai') == {'Authorization': 'Bearer legacy-key'}
    worker.config = {'provider': 'custom', 'api_format': 'anthropic'}
    assert worker._cc_backend_request_headers('anthropic') is None


def test_standard_worker_loop_finishes_claimed_task_before_stopping(tmp_path, monkeypatch):
    import factory.core.worker as module
    monkeypatch.setattr(module, 'DB_PATH', str(tmp_path / 'queue.db'))
    monkeypatch.setenv('IGOREK_ISOLATION_MODE', 'off')
    worker = Worker.__new__(Worker)
    worker.profile, worker.backend, worker.pid = 'test', 'cc-backend', 1
    worker.tasks_processed = 0
    worker.last_cleanup_time, worker.cleanup_interval = 10**12, 300
    worker.poll_interval = 1
    stop, gate = threading.Event(), threading.Lock()
    calls = []
    worker._ensure_db_schema = lambda conn: None
    worker.claim_task = lambda conn: {'task_id': 'test'}
    def execute(conn, task):
        assert not gate.acquire(blocking=False)
        stop.set()
        calls.append('execute')
        return {'status': 'ok'}
    worker._execute_task_with_heartbeat = execute
    worker.update_task = lambda *args: calls.append('update')
    worker.route_next = lambda *args: calls.append('route')
    worker.run(stop_event=stop, execution_lock=gate)
    assert calls == ['execute', 'update', 'route']
    assert worker.tasks_processed == 1
    assert gate.acquire(blocking=False)
    gate.release()


def test_existing_different_profile_is_not_overwritten(setup, tmp_path):
    owner, runtime, service, profile = setup
    verify(owner, profile)
    folder = tmp_path / 'profiles'
    folder.mkdir()
    path = folder / 'test-profile.json'
    path.write_text('{"existing": true}')
    with pytest.raises(ValueError):
        runtime.start(profile['credential_id'])
    assert path.read_text() == '{"existing": true}'
    assert not runtime.status()['running']


def test_desktop_worker_never_materializes_legacy_vault_env():
    worker = Worker.__new__(Worker)
    worker.auth_headers_provider = lambda *_: {}
    worker._vault_request_config = lambda *_: {'enabled': True, 'services': ['opencode']}
    with pytest.raises(PermissionError):
        worker._materialize_vault_env(task_id='fixture')


def test_desktop_injects_and_closes_only_its_manager(setup, monkeypatch):
    owner, runtime, service, profile = setup
    verify(owner, profile)
    calls = []
    manager = SimpleNamespace(close=lambda: calls.append('close'))
    monkeypatch.setattr(runtime.execution, 'create_manager', lambda *args: manager)
    class IsolatedWorker(FakeWorker):
        def __init__(self, profile, *, isolation_manager, **kwargs):
            assert isolation_manager is manager
            super().__init__(profile, **kwargs)
    runtime.worker_factory = IsolatedWorker
    runtime.start(profile['credential_id'])
    runtime.stop()
    assert calls == ['close']
    assert runtime.isolation_manager is None


def test_desktop_manager_start_failure_never_starts_host_worker(setup, monkeypatch):
    from factory.core.isolation.errors import IsolationError
    owner, runtime, service, profile = setup
    verify(owner, profile)
    count = len(FakeWorker.instances)
    def refuse(*args): raise IsolationError('isolation_unavailable:fixture')
    monkeypatch.setattr(runtime.execution, 'create_manager', refuse)
    assert owner.dispatch({'id':'start','action':'start_agent','credential_id':profile['credential_id']})['error'] == 'isolation_unavailable'
    assert len(FakeWorker.instances) == count and not runtime.status()['running']


def test_opencode_headers_are_stable_per_persisted_session_and_preserve_auth():
    worker = object.__new__(Worker)
    worker.config = {'base_url': 'https://opencode.ai/zen/go/v1', 'model': 'deepseek-v4-flash'}
    worker.auth_headers_provider = lambda config, api_format: {'Authorization': 'Bearer fixture-key'}
    first = worker._cc_backend_request_headers('openai', 'local-session-one')
    second = worker._cc_backend_request_headers('openai', 'local-session-one')
    other = worker._cc_backend_request_headers('openai', 'local-session-two')
    assert first['x-opencode-session'] == second['x-opencode-session'] == 'local-session-one'
    assert other['x-opencode-session'] != first['x-opencode-session']
    assert first['Authorization'] == second['Authorization'] == other['Authorization'] == 'Bearer fixture-key'
    worker.config['base_url'] = 'https://other.invalid/v1'
    assert worker._cc_backend_request_headers('openai', 'local-session-one') == {'Authorization': 'Bearer fixture-key'}


def test_opencode_session_headers_reach_two_requests_and_a_new_session():
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from provider_probe import post_json
    seen = []
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            self.rfile.read(int(self.headers['Content-Length']))
            seen.append(self.headers.get('x-opencode-session'))
            body = b'{"ok":true}'
            self.send_response(200); self.send_header('Content-Length', str(len(body))); self.end_headers()
            self.wfile.write(body)
        def log_message(self, *args):
            pass
    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    worker = object.__new__(Worker)
    worker.config = {'base_url': 'https://opencode.ai/zen/go/v1', 'model': 'deepseek-v4-flash'}
    worker.auth_headers_provider = lambda config, fmt: {'Authorization': 'Bearer fixture-key'}
    try:
        for sid in ('local-session-one', 'local-session-one', 'local-session-two'):
            headers = worker._cc_backend_request_headers('openai', sid)
            assert post_json(f'http://127.0.0.1:{server.server_port}/chat/completions', {}, headers) == {'ok': True}
        assert seen == ['local-session-one', 'local-session-one', 'local-session-two']
    finally:
        server.shutdown(); server.server_close(); thread.join()
