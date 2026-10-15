import io
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'desktop'))
from runtime_logs import RuntimeLogs


def test_desktop_states_follow_threads_and_worker_without_systemd():
    logs = RuntimeLogs(io.StringIO())
    stop = SimpleNamespace(is_set=lambda: False)
    threads = [SimpleNamespace(is_alive=lambda: True), SimpleNamespace(is_alive=lambda: True)]
    agent = SimpleNamespace(status=lambda: {'running': False})
    states = {row['key']: row['active'] for row in logs.status(stop, threads, agent)['units']}
    assert states == {'runtime': 'active', 'mobile': 'active', 'web': 'active', 'worker': 'inactive'}
    agent.status = lambda: {'running': True}
    assert logs.status(stop, threads, agent)['units'][-1]['active'] == 'active'
    stop.is_set = lambda: True
    assert logs.status(stop, [], agent)['units'][0]['active'] == 'deactivating'
    assert logs.status(stop, [], agent)['units'][1]['active'] == 'inactive'


def test_journal_coalesces_print_fragments_and_redacts_before_display():
    logs = RuntimeLogs(io.StringIO())
    logs.write('api_key=')
    logs.write('private-key')
    logs.write('\nregular entry\n')
    rows = logs.tail('runtime', 10)['lines']
    assert 'private-key' not in '\n'.join(rows)
    assert '[REDACTED]' in rows[0]
    assert rows[1].endswith('regular entry')


def test_journal_is_bounded_and_rejects_host_sources():
    logs = RuntimeLogs(io.StringIO())
    for n in range(2005):
        logs.write(f'line {n}\n')
    assert len(logs.rows) == 2000
    tail = logs.tail('worker', 2)
    assert tail['truncated'] and len(tail['lines']) == 2
    assert tail['lines'][-1].endswith('line 2004')
    for key in ['gateway', '../private', 'igorek-gateway.service']:
        with pytest.raises(ValueError):
            logs.tail(key, 10)
