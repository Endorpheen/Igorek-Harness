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


@pytest.mark.skipif(sys.platform != 'linux', reason='Linux guest Git descendant contract')
def test_checkpoint_git_filter_cannot_spawn_a_privileged_guest_descendant(tmp_path, monkeypatch):
    import shlex
    from factory.core.checkpoint_manager import _run_git
    work = tmp_path / 'project'; work.mkdir()
    marker = tmp_path / 'filter-nnp.txt'
    script = tmp_path / 'filter.py'
    script.write_text('import pathlib,sys\n'
        'value=next(x for x in pathlib.Path("/proc/self/status").read_text().splitlines() if x.startswith("NoNewPrivs:"))\n'
        'pathlib.Path(sys.argv[1]).write_text(value)\n'
        'sys.stdout.buffer.write(sys.stdin.buffer.read())\n')
    env = dict(os.environ, GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL=os.devnull)
    subprocess.run(['git', 'init', str(work)], env=env, check=True, capture_output=True)
    command = shlex.join([sys.executable, str(script), str(marker)])
    subprocess.run(['git', '-C', str(work), 'config', 'filter.fixture.clean', command],
                   env=env, check=True, capture_output=True)
    (work / '.gitattributes').write_text('*.txt filter=fixture\n')
    (work / 'note.txt').write_text('fixture\n')
    monkeypatch.setenv('IGOREK_ISOLATION_GUEST', '1')
    ok, _, error, _ = _run_git(['add', '-A'], shadow_repo=work / '.git', working_dir=str(work))
    assert ok, error
    assert marker.read_text().strip() == 'NoNewPrivs:\t1'
