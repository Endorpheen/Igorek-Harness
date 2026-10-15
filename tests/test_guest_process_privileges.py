"""Guest arbitrary-code children stay unprivileged across shell/environment changes."""
import os
import subprocess
import sys
from pathlib import Path

import pytest

from factory.core.isolation.guest_process import unprivileged_argv
from factory.core.running_commands import run_process


@pytest.mark.skipif(sys.platform != 'linux', reason='Linux guest process contract')
@pytest.mark.parametrize('shell', [False, True])
def test_guest_children_keep_nonewprivs_after_overriding_guest_environment(monkeypatch, shell):
    monkeypatch.setenv('IGOREK_ISOLATION_GUEST', '1')
    script = 'import os;os.environ["IGOREK_ISOLATION_GUEST"]="0";print(next(x for x in open("/proc/self/status") if x.startswith("NoNewPrivs:")).strip())'
    argv = [sys.executable, '-c', script]
    if shell:
        import shlex
        argv = shlex.join(argv)
    result = run_process(argv, label='synthetic guest child', shell=shell, capture_output=True, text=True, check=True)
    assert result.stdout.strip() == 'NoNewPrivs:\t1'


@pytest.mark.skipif(sys.platform != 'linux', reason='Linux guest process contract')
def test_plain_host_execution_is_unchanged(monkeypatch):
    monkeypatch.delenv('IGOREK_ISOLATION_GUEST', raising=False)
    original = subprocess.run([sys.executable, '-c', 'print("ordinary-host")'], capture_output=True, text=True, check=True)
    result = run_process([sys.executable, '-c', 'print("ordinary-host")'], label='host fixture', capture_output=True, text=True, check=True)
    assert result.stdout == original.stdout


@pytest.mark.skipif(sys.platform != 'linux', reason='Linux guest controller contract')
def test_controller_refuses_dumpability_in_a_separate_process():
    code = 'from factory.core.isolation.guest_process import protect_controller;import ctypes;protect_controller();print(ctypes.CDLL(None).prctl(3,0,0,0,0))'
    result = subprocess.run([sys.executable, '-c', code], capture_output=True, text=True, check=True)
    assert result.stdout.strip() == '0'


def test_image_controller_and_launcher_are_not_agent_writable():
    recipe = (Path(__file__).parents[1] / 'factory/scripts/build_isolation_image.sh').read_text()
    assert 'chown -R root:root /opt/igorek/runtime /opt/igorek/python' in recipe
    assert 'exec /opt/igorek/runtime/bin/python -I -m factory.cli' in recipe
    assert 'Environment=IGOREK_ISOLATION_GUEST=1' in recipe
    assert recipe.index('rm -rf -- "${IGOREK_GUEST_REPO_DIR}"') < recipe.index('tar -xf /opt/igorek/repo.tar')
    assert unprivileged_argv(['python3', 'fixture.py'])[-2:] == ['python3', 'fixture.py']
