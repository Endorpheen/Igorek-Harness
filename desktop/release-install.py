"""Apply a verified, owner-confirmed update after the desktop process exits."""
from __future__ import annotations

import ctypes
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time


def verified_file(path: Path, size: int, digest: str):
    info = path.lstat()
    if path.is_symlink() or not stat.S_ISREG(info.st_mode) or info.st_size != size:
        raise ValueError('update_file_changed')
    with path.open('rb') as stream:
        opened = os.fstat(stream.fileno())
        if (opened.st_dev, opened.st_ino) != (info.st_dev, info.st_ino):
            raise ValueError('update_file_changed')
        if hashlib.file_digest(stream, 'sha256').hexdigest() != digest:
            raise ValueError('update_checksum_mismatch')


def read_request(path: Path) -> dict:
    if path.is_symlink() or path.resolve() != path or path.stat().st_size > 16384:
        raise ValueError('invalid_update_request')
    value = json.loads(path.read_text(encoding='utf-8'))
    expected = {'schema', 'kind', 'file', 'size', 'sha256', 'version', 'target', 'parent_pid', 'restart', 'data_dir'}
    if set(value) != expected or value['schema'] != 1 or value['kind'] not in {'windows', 'snapshot', 'appimage'}:
        raise ValueError('invalid_update_request')
    if (not isinstance(value['size'], int) or not 0 < value['size'] <= 2 * 1024**3
            or not re.fullmatch(r'[a-f0-9]{64}', value['sha256'])
            or not re.fullmatch(r'\d{1,6}\.\d{1,6}\.\d{1,6}', value['version'])
            or type(value['restart']) is not bool or type(value['parent_pid']) is not int
            or value['parent_pid'] <= 1 or value['parent_pid'] == os.getpid()):
        raise ValueError('invalid_update_request')
    suffix = {'windows': 'win-x64.exe', 'snapshot': 'linux-amd64.deb', 'appimage': 'linux-x86_64.AppImage'}[value['kind']]
    file = Path(value['file'])
    target = Path(value['target'])
    data_dir = Path(value['data_dir'])
    if (not file.is_absolute() or file.parent != path.parent
            or file.name != f"igorek-{value['version']}-{suffix}"
            or not target.is_absolute() or target.resolve() != target
            or not data_dir.is_absolute() or data_dir.resolve() != data_dir or not data_dir.is_dir()):
        raise ValueError('invalid_update_target')
    if (value['kind'] == 'windows') != (sys.platform == 'win32'):
        raise ValueError('unsupported_update_platform')
    verified_file(file, value['size'], value['sha256'])
    return value


def wait_for_exit(pid: int, timeout: float = 60):
    if sys.platform == 'win32':
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.OpenProcess.argtypes = [ctypes.c_uint32, ctypes.c_bool, ctypes.c_uint32]
        kernel.OpenProcess.restype = ctypes.c_void_p
        kernel.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
        kernel.CloseHandle.argtypes = [ctypes.c_void_p]
        handle = kernel.OpenProcess(0x100000, False, pid)
        if not handle:
            if ctypes.get_last_error() == 87:
                return
            raise OSError('update_parent_wait_failed')
        try:
            if kernel.WaitForSingleObject(handle, int(timeout * 1000)) != 0:
                raise TimeoutError('update_parent_still_running')
        finally:
            kernel.CloseHandle(handle)
        return
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return
        time.sleep(0.1)
    raise TimeoutError('update_parent_still_running')


def replace_appimage(file: Path, target: Path, size: int, digest: str) -> Path:
    info = target.lstat()
    if target.is_symlink() or not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid():
        raise ValueError('invalid_appimage_target')
    descriptor, staged = tempfile.mkstemp(prefix='.igorek-update-', dir=target.parent)
    stage = Path(staged)
    backup = stage.with_suffix('.previous')
    try:
        with os.fdopen(descriptor, 'wb') as output, file.open('rb') as source:
            shutil.copyfileobj(source, output)
            output.flush(); os.fsync(output.fileno())
        verified_file(stage, size, digest)
        stage.chmod(stat.S_IMODE(info.st_mode) | stat.S_IXUSR)
        current = target.lstat()
        if target.is_symlink() or (current.st_dev, current.st_ino) != (info.st_dev, info.st_ino):
            raise ValueError('appimage_target_changed')
        os.link(target, backup, follow_symlinks=False)
        os.replace(stage, target)
        descriptor = os.open(target.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
        return backup
    finally:
        stage.unlink(missing_ok=True)


def install_snapshot(file: Path, target: Path, version: str) -> Path:
    marker = target / 'installation.json'
    if marker.is_symlink() or json.loads(marker.read_text()) != {'schema_version': 1, 'kind': 'igorek-linux-user-installation'}:
        raise ValueError('unknown_snapshot_installation')
    import fcntl
    with (target / 'run.lock').open('r+b') as lock:
        deadline = time.monotonic() + 15
        while True:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise TimeoutError('snapshot_still_running') from None
                time.sleep(0.1)
        fcntl.flock(lock, fcntl.LOCK_UN)
    with tempfile.TemporaryDirectory(prefix='extracted-', dir=file.parent) as temporary:
        subprocess.run(['/usr/bin/dpkg-deb', '--extract', str(file), temporary],
                       stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       timeout=120, check=True)
        snapshot = Path(temporary) / 'opt/igorek-desktop-installer'
        spec = importlib.util.spec_from_file_location('update_package_verifier', snapshot / 'verify-package.py')
        verifier = importlib.util.module_from_spec(spec); spec.loader.exec_module(verifier)
        manifest = verifier.verify(snapshot)
        if manifest['factory_version'] != version:
            raise ValueError('update_version_mismatch')
        subprocess.run([sys.executable, '-I', '-B', str(snapshot / 'install-linux.py'),
                        '--snapshot', str(snapshot), '--prefix', str(target)],
                       stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                       timeout=180, check=True)
    return target / 'igorek'


def apply(value: dict) -> dict:
    file, target = Path(value['file']), Path(value['target'])
    verified_file(file, value['size'], value['sha256'])
    result = {'state': 'installed', 'version': value['version']}
    if value['kind'] == 'windows':
        executable = target / 'igorek.exe'
        if not executable.is_file() or executable.is_symlink():
            raise ValueError('unknown_windows_installation')
        # NSIS consumes the final /D tail verbatim, including spaces, without quotes.
        command = subprocess.list2cmdline([str(file)]) + ' /S /D=' + str(target)
        subprocess.run(command, shell=False, stdin=subprocess.DEVNULL,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=180, check=True)
        manifest = json.loads((target / 'resources/app/source-manifest.json').read_text())
        if manifest['version'] != value['version']:
            raise ValueError('update_version_mismatch')
    elif value['kind'] == 'snapshot':
        install_snapshot(file, target, value['version'])
    else:
        result['backup'] = str(replace_appimage(file, target, value['size'], value['sha256']))
    return result


def main(request: Path) -> int:
    result_path = request.with_suffix('.result.json')
    value = None
    exited = False
    try:
        value = read_request(request)
        request.with_suffix('.ready').write_text('ready', encoding='ascii')
        wait_for_exit(value['parent_pid'])
        exited = True
        result = apply(value)
    except Exception:
        result = {'state': 'failed', 'reason': 'installation_failed'}
    result_path.write_text(json.dumps(result), encoding='utf-8')
    if exited:
        (request.parent.parent / 'last-install-result.json').write_text(json.dumps(result), encoding='utf-8')
        if value['restart']:
            target = Path(value['target'])
            executable = target / 'igorek.exe' if value['kind'] == 'windows' else target / 'igorek' if value['kind'] == 'snapshot' else target
            if executable.is_file():
                subprocess.Popen([str(executable), '--data-dir', value['data_dir']], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                 stderr=subprocess.DEVNULL, close_fds=True)
    return 0 if result['state'] == 'installed' else 1


if __name__ == '__main__':
    raise SystemExit(main(Path(sys.argv[1])))
