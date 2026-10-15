"""The isolated runner contains credentials, local data and timed-out children."""
import importlib.util
import json
import os
from pathlib import Path
import time
from types import SimpleNamespace

import pytest


@pytest.mark.skipif(os.name != 'posix', reason='POSIX process-group acceptance')
def test_file_timeout_stops_children_and_keeps_host_configuration_private(tmp_path, monkeypatch):
    source = Path(__file__).resolve().parents[1] / 'tools/run_tests.py'
    spec = importlib.util.spec_from_file_location('publication_runner', source)
    runner = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(runner)
    wrapper = tmp_path / 'python-fixture'
    wrapper.write_text('''#!/usr/bin/env python3
import json, os, signal, subprocess, time
from pathlib import Path
root = Path(os.environ['FACTORY_DATA_DIR'])
child = subprocess.Popen(['python3', '-c', 'import signal,time; signal.signal(signal.SIGTERM,signal.SIG_IGN); print("ready",flush=True); time.sleep(30)'], stdout=subprocess.PIPE)
child.stdout.readline()
(root / 'proof.json').write_text(json.dumps({'child':child.pid,'environment':dict(os.environ)}))
time.sleep(30)
''')
    wrapper.chmod(0o755)
    test = tmp_path / 'test_fixture.py'
    test.write_text('')
    output = tmp_path / 'results'
    output.mkdir()
    monkeypatch.setattr(runner, 'REPO', tmp_path)
    monkeypatch.setattr(runner, 'sys', SimpleNamespace(executable=str(wrapper)))
    monkeypatch.setenv('FIXTURE_PRIVATE_TOKEN', 'synthetic-runner-secret')
    result = runner.run_file(test, 0, output, timeout=1, coverage=False)
    assert result['exit_code'] == 124
    proof = json.loads(next(output.glob('*/data/proof.json')).read_text())
    assert 'FIXTURE_PRIVATE_TOKEN' not in proof['environment']
    assert proof['environment']['HOME'].startswith(str(output))
    child = Path('/proc') / str(proof['child']) / 'stat'
    for _ in range(50):
        if not child.exists() or child.read_text().split()[2] == 'Z':
            break
        time.sleep(0.02)
    else:
        os.kill(proof['child'], 9)
        pytest.fail('Timed-out test left a running child process')
