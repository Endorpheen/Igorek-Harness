"""Prepare a separate desktop base image without enabling execution."""
from __future__ import annotations

import ctypes
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
import uuid

from factory.core.isolation.base_image import manifest_path_for, verify_image


class ImageSetupError(ValueError):
    pass


def check_image(image: Path, version: str) -> dict:
    manifest = manifest_path_for(image)
    if image.is_symlink() or manifest.is_symlink():
        raise ImageSetupError('symlink_source')
    result = verify_image(image, expected_tool_version=version)
    if not result.ok:
        raise ImageSetupError(result.code)
    if image.stat().st_size != result.manifest.size_bytes:
        raise ImageSetupError('size_mismatch')
    try:
        process = subprocess.run(['/usr/bin/qemu-img', 'info', '--output=json', str(image)],
                                 capture_output=True, text=True, timeout=15, check=True)
        info = json.loads(process.stdout)
    except (OSError, subprocess.SubprocessError, ValueError) as error:
        raise ImageSetupError('invalid_qcow2') from error
    if (info.get('format') != 'qcow2' or info.get('backing-filename')
            or info.get('full-backing-filename') or info.get('encrypted')
            or info.get('format-specific', {}).get('data', {}).get('data-file')):
        raise ImageSetupError('dependent_image')
    return {'sha256': result.sha256, 'tool_version': result.tool_version,
            'size_bytes': image.stat().st_size}


def sync_directory(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def exchange_directories(first, second):
    library = ctypes.CDLL(None, use_errno=True)
    try:
        exchange = library.renameat2
    except AttributeError as error:
        raise ImageSetupError('atomic_exchange_unavailable') from error
    exchange.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
    exchange.restype = ctypes.c_int
    if exchange(-100, os.fsencode(first), -100, os.fsencode(second), 2) != 0:
        raise OSError(ctypes.get_errno(), 'Atomic image exchange failed')


def prepare_image(root: Path, source: Path, *, tool_version: str, progress=lambda phase: None, recover=False) -> dict:
    from factory.core.platform_support import require_linux
    require_linux('kvm_image')
    import fcntl
    root, source = Path(root).absolute(), Path(source).absolute()
    if root.resolve() != root or source.parent.resolve() != source.parent:
        raise ImageSetupError('symlink_path')
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    isolation = root / 'isolation'
    if isolation.is_symlink():
        raise ImageSetupError('symlink_path')
    isolation.mkdir(mode=0o700, exist_ok=True)
    lock = isolation / '.image-setup.lock'
    fd = os.open(lock, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'w') as stream:
        fcntl.flock(stream, fcntl.LOCK_EX)
        target = isolation / 'images'
        backup = None
        if target.exists() or target.is_symlink():
            if not recover:
                raise ImageSetupError('destination_exists')
            if (target.is_symlink() or not target.is_dir() or target.stat().st_uid != os.getuid()
                    or {p.name for p in target.iterdir()} != {'base.qcow2', 'base.qcow2.manifest.json'}
                    or any(p.is_symlink() or not p.is_file() for p in target.iterdir())):
                raise ImageSetupError('unsafe_destination')
            if list((isolation / 'overlays').glob('*.qcow2')):
                raise ImageSetupError('image_in_use')
            if source.parent == target:
                raise ImageSetupError('recovery_source_is_destination')
            backup = isolation / ('.images-backup-' + uuid.uuid4().hex)
        elif recover:
            raise ImageSetupError('recovery_destination_missing')
        progress('checking_source')
        check_image(source, tool_version)
        stage = Path(tempfile.mkdtemp(prefix='.image-', dir=isolation))
        exchanged = False
        backup_prepared = False
        try:
            image = stage / 'base.qcow2'
            progress('copying')
            subprocess.run(['/usr/bin/cp', '--reflink=auto', '--sparse=always', '--', str(source), str(image)],
                           capture_output=True, check=True)
            shutil.copyfile(manifest_path_for(source), manifest_path_for(image))
            progress('checking_copy')
            facts = check_image(image, tool_version)
            image.chmod(0o400)
            manifest_path_for(image).chmod(0o400)
            for path in (image, manifest_path_for(image)):
                with path.open('rb') as handle:
                    os.fsync(handle.fileno())
            sync_directory(stage)
            if backup is not None:
                stage.rename(backup)
                backup_prepared = True
                sync_directory(isolation)
                # RENAME_EXCHANGE leaves a complete image at target even after process death.
                exchange_directories(backup, target)
                exchanged = True
            else:
                stage.rename(target)
            sync_directory(isolation)
        finally:
            if stage.exists():
                shutil.rmtree(stage)
            if backup_prepared and not exchanged:
                shutil.rmtree(backup)
    return {'prepared': True, 'execution_enabled': False,
            'path': str(target / 'base.qcow2'), **({'backup_path': str(backup)} if backup else {}), **facts}


class ImagePreparation:
    def __init__(self, root):
        self.root = root
        self._lock = threading.Lock()
        self._state = {'phase': 'idle', 'running': False}

    def status(self):
        with self._lock:
            return dict(self._state)

    def _update(self, value):
        with self._lock:
            self._state = value

    def start(self, source, version, *, recover=False):
        import sys
        if sys.platform != 'linux':
            raise ImageSetupError('unsupported_platform')
        with self._lock:
            if self._state['running']:
                raise ImageSetupError('image_busy')
            self._state = {'phase': 'checking_source', 'running': True}
        def run():
            try:
                result = prepare_image(self.root, Path(source), tool_version=version,
                    progress=lambda phase: self._update({'phase': phase, 'running': True}), recover=recover)
                self._update({'phase': 'completed', 'running': False, 'result': result})
            except ImageSetupError as error:
                self._update({'phase': 'failed', 'running': False, 'error': str(error)})
            except Exception:
                self._update({'phase': 'failed', 'running': False, 'error': 'image_failed'})
        threading.Thread(target=run, name='desktop-image-preparation', daemon=True).start()
        return self.status()
