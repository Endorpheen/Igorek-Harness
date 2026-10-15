"""Verified updates preserve the old installation until replacement succeeds."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

spec = importlib.util.spec_from_file_location('release_install', Path(__file__).parents[1] / 'desktop/release-install.py')
update = importlib.util.module_from_spec(spec); spec.loader.exec_module(update)


def test_changed_payload_is_refused_before_installer_execution(tmp_path, monkeypatch):
    file = tmp_path / 'update.exe'; file.write_bytes(b'original')
    digest = hashlib.sha256(file.read_bytes()).hexdigest()
    file.write_bytes(b'tampered')
    monkeypatch.setattr(update.subprocess, 'run', lambda *a, **k: pytest.fail('Unverified installer executed'))
    with pytest.raises(ValueError, match='checksum'):
        update.apply({'file': str(file), 'target': str(tmp_path), 'size': 8, 'sha256': digest, 'kind': 'windows'})


@pytest.mark.skipif(sys.platform != 'linux', reason='Linux AppImage replacement')
def test_appimage_replacement_is_atomic_and_keeps_rollback_and_user_data(tmp_path):
    target = tmp_path / 'Igorek.AppImage'; target.write_bytes(b'previous'); target.chmod(0o755)
    file = tmp_path / 'download'; file.write_bytes(b'new version')
    data = tmp_path / 'user-data'; data.mkdir(); (data / 'canary').write_bytes(b'keep')
    digest = hashlib.sha256(file.read_bytes()).hexdigest()
    backup = update.replace_appimage(file, target, file.stat().st_size, digest)
    assert target.read_bytes() == b'new version' and os.access(target, os.X_OK)
    assert backup.read_bytes() == b'previous' and (data / 'canary').read_bytes() == b'keep'


@pytest.mark.skipif(sys.platform != 'linux', reason='Linux AppImage replacement')
def test_failed_appimage_verification_keeps_previous_bytes(tmp_path):
    target = tmp_path / 'Igorek.AppImage'; target.write_bytes(b'previous')
    file = tmp_path / 'download'; file.write_bytes(b'tampered')
    with pytest.raises(ValueError):
        update.replace_appimage(file, target, file.stat().st_size, '0' * 64)
    assert target.read_bytes() == b'previous'
    assert not list(tmp_path.glob('.igorek-update-*'))


def test_request_refuses_payload_outside_its_private_job(tmp_path):
    request = tmp_path / 'install-request.json'
    file = tmp_path.parent / 'igorek-0.2.1-win-x64.exe'
    value = {'schema': 1, 'kind': 'windows', 'file': str(file), 'size': 1,
             'sha256': '0' * 64, 'version': '0.2.1', 'target': str(tmp_path),
             'parent_pid': os.getpid() + 1000, 'restart': False, 'data_dir': str(tmp_path)}
    request.write_text(json.dumps(value))
    with pytest.raises(ValueError):
        update.read_request(request)


def test_live_parent_is_never_replaced(tmp_path):
    child = subprocess.Popen([sys.executable, '-c', 'import time;time.sleep(10)'])
    try:
        with pytest.raises(TimeoutError):
            update.wait_for_exit(child.pid, timeout=0.1)
    finally:
        child.terminate(); child.wait(timeout=5)
    update.wait_for_exit(child.pid, timeout=1)


def test_windows_installer_directory_with_spaces_uses_nsis_tail(tmp_path, monkeypatch):
    target = tmp_path / 'Program folder'; target.mkdir(); (target / 'igorek.exe').write_bytes(b'old')
    manifest = target / 'resources/app/source-manifest.json'; manifest.parent.mkdir(parents=True)
    manifest.write_text(json.dumps({'version': '0.2.1'}))
    file = tmp_path / 'download.exe'; file.write_bytes(b'fixture')
    calls = []
    monkeypatch.setattr(update.subprocess, 'run', lambda command, **options: calls.append((command, options)))
    result = update.apply({'file': str(file), 'target': str(target), 'kind': 'windows',
                          'size': 7, 'sha256': hashlib.sha256(b'fixture').hexdigest(),
                          'version': '0.2.1', 'restart': False})
    assert result['state'] == 'installed'
    assert calls[0][0].endswith(' /S /D=' + str(target)) and calls[0][1]['shell'] is False


@pytest.mark.skipif(sys.platform != 'linux', reason='Linux launcher flock inheritance')
def test_helper_closes_inherited_lock_without_unlocking_running_parent(tmp_path):
    import fcntl
    lock = tmp_path / 'run.lock'
    descriptor = os.open(lock, os.O_RDWR | os.O_CREAT, 0o600)
    fcntl.flock(descriptor, fcntl.LOCK_SH)
    code = """
import fcntl, importlib.util, json, sys
from pathlib import Path
spec = importlib.util.spec_from_file_location('helper', sys.argv[1])
helper = importlib.util.module_from_spec(spec); spec.loader.exec_module(helper)
helper.close_inherited_installation_lock({'kind': 'snapshot', 'target': sys.argv[2]})
print('closed', flush=True)
sys.stdin.readline()
with (Path(sys.argv[2]) / 'run.lock').open('r+b') as stream:
    fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
    print('exclusive', flush=True)
"""
    child = subprocess.Popen([sys.executable, '-I', '-B', '-c', code, str(Path(update.__file__)), str(tmp_path)],
                             pass_fds=(descriptor,), stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    try:
        assert child.stdout.readline().strip() == 'closed'
        with lock.open('r+b') as separate:
            with pytest.raises(BlockingIOError):
                fcntl.flock(separate, fcntl.LOCK_EX | fcntl.LOCK_NB)
        os.close(descriptor); descriptor = None
        stdout, _ = child.communicate('continue\n', timeout=5)
        assert child.returncode == 0 and stdout.strip() == 'exclusive'
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if child.poll() is None:
            child.kill(); child.wait()
