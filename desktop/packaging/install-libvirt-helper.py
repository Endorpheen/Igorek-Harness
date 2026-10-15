#!/usr/bin/python3 -I
"""Install one pinned compatibility helper; keep package upgrades authoritative."""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import socket
import stat
import subprocess
import tempfile
import uuid

VERSION = '12.0.0-1ubuntu5.5'
ORIGINAL_SHA = '029ffeb56de273b5348e5fa3a5385b3629a697e5eeaffdc549e2ed7012822272'
PATCHED_SHA = '21d210168605f516257b62ddda4e416e49e38de0a87175c204a740dbb359130e'
SOURCE_SHA = 'd922d6fc2bda6e0afc06a8a8380e803b651519f3d230120c3579a50311c86b22'
TARGET = Path('/usr/lib/libvirt/virt-aa-helper')
STATE = Path('/var/lib/igorek-desktop-libvirt-fix')
FILES = {'virt-aa-helper', 'COPYING.LESSER', 'virtiofs-socket.patch'}


def sha(data):
    return hashlib.sha256(data).hexdigest()


def read_regular(path):
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    with os.fdopen(fd, 'rb') as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_size > 4 * 1024 * 1024:
            raise ValueError('Недопустимый файл компонента.')
        return stream.read(), info


def payload_bytes(payload):
    payload = Path(payload).resolve()
    manifest = json.loads(read_regular(payload / 'manifest.json')[0])
    if (type(manifest.get('schema_version')) is not int or manifest['schema_version'] != 1
            or manifest.get('ubuntu_libvirt_version') != VERSION or manifest.get('architecture') != 'x86_64'
            or manifest.get('source_sha256') != SOURCE_SHA or manifest.get('system_helper_sha256') != ORIGINAL_SHA
            or manifest.get('verification') != 'dry-run-only' or manifest.get('installed') is not False
            or set(manifest.get('files', {})) != FILES):
        raise ValueError('Компонент не соответствует проверенной сборке libvirt.')
    files = {name: read_regular(payload / name)[0] for name in FILES}
    if any(sha(data) != manifest['files'][name] for name, data in files.items()) or sha(files['virt-aa-helper']) != PATCHED_SHA:
        raise ValueError('Контрольная сумма компонента не совпадает.')
    return files['virt-aa-helper']


def installed_version():
    return subprocess.run(['/usr/bin/dpkg-query', '-W', '-f=${Version}', 'libvirt0'],
                          check=True, capture_output=True, text=True, timeout=5).stdout


def compatible(payload=None):
    try:
        if installed_version() != VERSION or os.uname().machine != 'x86_64': return 'unsupported'
        current = sha(read_regular(TARGET)[0])
        if current == PATCHED_SHA: return 'installed'
        if current != ORIGINAL_SHA: return 'unsupported'
        if payload is None or not Path(payload).is_dir(): return 'missing'
        payload_bytes(payload)
        return 'ready'
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError):
        return 'unsupported'


def probe(helper):
    with tempfile.TemporaryDirectory(prefix='igorek-aa-verify-') as directory:
        path = Path(directory) / 'transport.sock'
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as endpoint:
            endpoint.bind(str(path))
            identity = str(uuid.uuid4())
            xml = (f'<domain type="kvm"><name>igorek-fix-verify</name><uuid>{identity}</uuid>'
                   '<memory unit="MiB">256</memory><vcpu>1</vcpu>'
                   '<os><type arch="x86_64" machine="q35">hvm</type></os><devices>'
                   '<filesystem type="mount"><driver type="virtiofs"/>'
                   f'<source socket="{path}"/><target dir="workspace"/>'
                   '</filesystem></devices></domain>')
            result = subprocess.run([str(helper), '-d', '-c', '-u', 'libvirt-' + identity],
                                    input=xml, capture_output=True, text=True, timeout=10,
                                    env={'PATH': '/usr/bin:/bin'})
            output = result.stdout + '\n' + result.stderr
            if result.returncode or f'"{path}" rw,' not in {line.strip() for line in output.splitlines()}:
                raise ValueError('Установленный компонент не прошёл проверку разрешения сокета.')


class Installer:
    def __init__(self, target=TARGET, state=STATE, *, owner_uid=0, version=installed_version,
                 check=probe, package_locks=(Path('/var/lib/dpkg/lock-frontend'), Path('/var/lib/dpkg/lock'))):
        self.target = Path(target); self.state = Path(state)
        self.owner_uid = owner_uid; self.version = version; self.check = check
        self.package_locks = package_locks

    def secure_file(self, path):
        if path.absolute() != path.resolve(): raise ValueError('Символическая ссылка в системном пути.')
        data, info = read_regular(path)
        if info.st_uid != self.owner_uid or info.st_nlink != 1 or stat.S_IMODE(info.st_mode) & 0o022:
            raise ValueError('Небезопасные права системного файла.')
        return data, info

    @contextmanager
    def locked(self):
        if os.geteuid() != self.owner_uid: raise ValueError('Требуются права администратора.')
        if self.version() != VERSION: raise ValueError('Версия libvirt изменилась; системный файл не заменён.')
        for parent in (self.target.parent, self.state.parent):
            info = parent.stat()
            if parent.absolute() != parent.resolve() or info.st_uid != self.owner_uid or info.st_mode & 0o022:
                raise ValueError('Небезопасный каталог системного компонента.')
        self.state.mkdir(mode=0o700, exist_ok=True)
        info = self.state.lstat()
        if self.state.is_symlink() or info.st_uid != self.owner_uid or stat.S_IMODE(info.st_mode) != 0o700:
            raise ValueError('Небезопасный каталог резервной копии.')
        descriptors = []
        try:
            fd = os.open(self.state / 'lock', os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
            descriptors.append(fd)
            self.secure_file(self.state / 'lock')
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            for path in self.package_locks:
                fd = os.open(path, os.O_RDWR | os.O_NOFOLLOW); descriptors.append(fd)
                fcntl.lockf(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            if self.version() != VERSION:
                raise ValueError('Версия libvirt изменилась; системный файл не заменён.')
            yield
        finally:
            for fd in reversed(descriptors): os.close(fd)

    def atomic(self, path, data, mode):
        fd, name = tempfile.mkstemp(prefix='.igorek-', dir=path.parent)
        try:
            with os.fdopen(fd, 'wb') as stream:
                stream.write(data); stream.flush(); os.fchmod(stream.fileno(), mode); os.fsync(stream.fileno())
            os.replace(name, path)
            directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
            try: os.fsync(directory)
            finally: os.close(directory)
        finally:
            Path(name).unlink(missing_ok=True)

    def journal(self, phase, mode):
        self.atomic(self.state / 'state.json', json.dumps({'schema_version': 1, 'phase': phase,
            'version': VERSION, 'original_sha': ORIGINAL_SHA, 'patched_sha': PATCHED_SHA,
            'mode': mode}).encode(), 0o600)

    def record(self):
        value = json.loads(self.secure_file(self.state / 'state.json')[0])
        if (value.get('schema_version') != 1 or value.get('version') != VERSION
                or value.get('original_sha') != ORIGINAL_SHA or value.get('patched_sha') != PATCHED_SHA
                or value.get('phase') not in ('prepared', 'installed', 'restored') or value.get('mode') != 0o755):
            raise ValueError('Недопустимое состояние резервной копии.')
        if sha(self.secure_file(self.state / 'original')[0]) != ORIGINAL_SHA:
            raise ValueError('Резервная копия не прошла проверку.')
        return value

    def install(self, payload):
        fixed = payload_bytes(payload)
        with self.locked():
            original, info = self.secure_file(self.target)
            if stat.S_IMODE(info.st_mode) != 0o755: raise ValueError('Неожиданные права libvirt helper.')
            if sha(original) == PATCHED_SHA:
                self.record(); self.check(self.target); self.journal('installed', 0o755)
                return 'already-installed'
            if sha(original) != ORIGINAL_SHA: raise ValueError('Системный helper изменился; замена отменена.')
            backup = self.state / 'original'
            if backup.exists() or backup.is_symlink():
                if sha(self.secure_file(backup)[0]) != ORIGINAL_SHA: raise ValueError('Резервная копия изменена.')
            else:
                self.atomic(backup, original, 0o600)
            self.journal('prepared', 0o755)
            try:
                self.atomic(self.target, fixed, 0o755)
                self.check(self.target)
                self.journal('installed', 0o755)
            except Exception:
                current = sha(self.secure_file(self.target)[0])
                if current not in (ORIGINAL_SHA, PATCHED_SHA):
                    raise ValueError('Файл изменён извне; автоматический откат остановлен.')
                if current == PATCHED_SHA:
                    self.atomic(self.target, original, 0o755)
                self.journal('restored', 0o755)
                raise
            return 'installed'

    def rollback(self):
        with self.locked():
            record = self.record()
            current, _ = self.secure_file(self.target)
            if sha(current) == ORIGINAL_SHA:
                self.journal('restored', record['mode']); return 'already-restored'
            if sha(current) != PATCHED_SHA:
                raise ValueError('Системный helper обновлён извне; старый файл не восстановлен.')
            self.atomic(self.target, self.secure_file(self.state / 'original')[0], record['mode'])
            self.journal('restored', record['mode'])
            return 'restored'


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Установка одного проверенного компонента KVM; службы не перезапускаются.')
    parser.add_argument('action', choices=('install', 'rollback', 'status'))
    parser.add_argument('--payload', type=Path)
    args = parser.parse_args()
    try:
        if args.action == 'status': result = compatible(args.payload)
        elif args.action == 'install':
            if args.payload is None: raise ValueError('Не указан готовый компонент.')
            result = Installer().install(args.payload)
        else: result = Installer().rollback()
        print(json.dumps({'status': result}))
    except (OSError, ValueError, KeyError, TypeError, subprocess.SubprocessError):
        parser.exit(1, 'Системная настройка KVM не выполнена. Проверьте права администратора, версию libvirt и целостность поставки.\n')
