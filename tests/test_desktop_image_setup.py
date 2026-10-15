import sys
import subprocess
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'desktop'))
from image_setup import ImageSetupError, prepare_image
from factory.core.isolation.base_image import ImageBuildInfo, build_manifest, write_manifest


def source_image(tmp_path, version='0.1.819'):
    image = tmp_path / 'source.qcow2'
    subprocess.run(['/usr/bin/qemu-img', 'create', '-f', 'qcow2', str(image), '1M'], check=True, capture_output=True)
    write_manifest(build_manifest(image, ImageBuildInfo(tool_version=version)), image)
    return image


def test_prepare_separate_verified_readonly_image(tmp_path):
    source = source_image(tmp_path)
    original = source.read_bytes()
    root = tmp_path / 'desktop'
    result = prepare_image(root, source, tool_version='0.1.819')
    target = Path(result['path'])
    assert result['prepared'] and not result['execution_enabled']
    assert source.read_bytes() == target.read_bytes() == original
    assert target.stat().st_ino != source.stat().st_ino
    assert target.stat().st_mode & 0o777 == 0o400
    with pytest.raises(ImageSetupError, match='destination_exists'):
        prepare_image(root, source, tool_version='0.1.819')
    assert target.read_bytes() == original


@pytest.mark.parametrize('failure', ['checksum', 'version', 'symlink', 'backing', 'escape'])
def test_refuse_invalid_sources_without_publishing(tmp_path, failure):
    source = source_image(tmp_path, '0.1.818' if failure == 'version' else '0.1.819')
    root = tmp_path / 'desktop'
    if failure == 'checksum':
        with source.open('ab') as stream:
            stream.write(b'changed')
    elif failure == 'symlink':
        link = tmp_path / 'link.qcow2'; link.symlink_to(source); source = link
    elif failure == 'backing':
        overlay = tmp_path / 'overlay.qcow2'
        subprocess.run(['/usr/bin/qemu-img', 'create', '-f', 'qcow2', '-F', 'qcow2', '-b', str(source), str(overlay)], check=True, capture_output=True)
        write_manifest(build_manifest(overlay, ImageBuildInfo(tool_version='0.1.819')), overlay)
        source = overlay
    elif failure == 'escape':
        root.mkdir(); (root / 'isolation').symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(ImageSetupError):
        prepare_image(root, source, tool_version='0.1.819')
    assert not (root / 'isolation/images').exists()
    assert not [path for path in root.glob('isolation/.image-*') if path.is_dir()]


def test_copy_failure_does_not_publish_partial_image(tmp_path, monkeypatch):
    import image_setup
    source = source_image(tmp_path)
    root = tmp_path / 'desktop'
    original_run = subprocess.run

    def fail_copy(args, **kwargs):
        if args[0] == '/usr/bin/cp':
            Path(args[-1]).write_bytes(b'partial')
            raise subprocess.CalledProcessError(1, args)
        return original_run(args, **kwargs)

    monkeypatch.setattr(image_setup.subprocess, 'run', fail_copy)
    with pytest.raises(subprocess.CalledProcessError):
        prepare_image(root, source, tool_version='0.1.819')
    assert not (root / 'isolation/images').exists()
    assert not [path for path in root.glob('isolation/.image-*') if path.is_dir()]


def test_background_preparation_progress_and_exclusive_start(tmp_path, monkeypatch):
    import threading
    import image_setup
    entered, release = threading.Event(), threading.Event()
    def prepare(root, source, *, tool_version, progress, recover=False):
        progress('copying'); entered.set(); release.wait(5)
        progress('checking_copy')
        return {'prepared': True, 'execution_enabled': False, 'tool_version': tool_version}
    monkeypatch.setattr(image_setup, 'prepare_image', prepare)
    operation = image_setup.ImagePreparation(tmp_path)
    operation.start('/tmp/source.qcow2', '0.1.819')
    assert entered.wait(2)
    assert operation.status() == {'phase': 'copying', 'running': True}
    with pytest.raises(ImageSetupError, match='image_busy'):
        operation.start('/tmp/other.qcow2', '0.1.819')
    release.set()
    import time
    for _ in range(100):
        if not operation.status()['running']: break
        time.sleep(.01)
    assert operation.status()['phase'] == 'completed'


def test_background_error_is_sanitized(tmp_path, monkeypatch):
    import image_setup, time
    def fail(*args, **kwargs):
        raise RuntimeError('private-source-path-secret')
    monkeypatch.setattr(image_setup, 'prepare_image', fail)
    operation = image_setup.ImagePreparation(tmp_path)
    operation.start('/tmp/source.qcow2', '0.1.819')
    for _ in range(100):
        if not operation.status()['running']: break
        time.sleep(.01)
    assert operation.status() == {'phase': 'failed', 'running': False, 'error': 'image_failed'}


def test_recovery_preserves_old_image_and_creates_separate_verified_copy(tmp_path):
    source = source_image(tmp_path)
    root = tmp_path / 'desktop'
    prepare_image(root, source, tool_version='0.1.819')
    old = root / 'isolation/images/base.qcow2'
    inode = old.stat().st_ino
    result = prepare_image(root, source, tool_version='0.1.819', recover=True)
    backup = Path(result['backup_path'])
    assert (backup / 'base.qcow2').stat().st_ino == inode
    assert old.stat().st_ino != inode
    assert old.stat().st_uid == __import__('os').getuid()
    assert old.stat().st_mode & 0o777 == 0o400
    assert old.read_bytes() == (backup / 'base.qcow2').read_bytes() == source.read_bytes()


@pytest.mark.parametrize('failure', ['overlay', 'extra_file', 'symlink', 'source_is_target', 'checksum'])
def test_recovery_refusals_preserve_existing_image(tmp_path, failure):
    source = source_image(tmp_path)
    root = tmp_path / 'desktop'
    prepare_image(root, source, tool_version='0.1.819')
    target = root / 'isolation/images/base.qcow2'
    inode = target.stat().st_ino
    if failure == 'overlay':
        overlays = root / 'isolation/overlays'; overlays.mkdir(); (overlays / 'running.qcow2').write_bytes(b'active')
    elif failure == 'extra_file': (target.parent / 'unrelated').write_bytes(b'keep')
    elif failure == 'symlink':
        target.unlink(); target.symlink_to(source)
    elif failure == 'source_is_target': source = target
    else:
        source.write_bytes(b'invalid')
    with pytest.raises(ImageSetupError): prepare_image(root, source, tool_version='0.1.819', recover=True)
    if failure != 'symlink': assert target.stat().st_ino == inode
    assert not list((root / 'isolation').glob('.images-backup-*'))


def test_failed_recovery_publication_restores_original_directory(tmp_path, monkeypatch):
    source = source_image(tmp_path); root = tmp_path / 'desktop'
    prepare_image(root, source, tool_version='0.1.819')
    target = root / 'isolation/images/base.qcow2'; inode = target.stat().st_ino
    rename = Path.rename
    def fail_stage(path, destination):
        if path.name.startswith('.image-'): raise OSError('publication failed')
        return rename(path, destination)
    monkeypatch.setattr(Path, 'rename', fail_stage)
    with pytest.raises(OSError): prepare_image(root, source, tool_version='0.1.819', recover=True)
    assert target.stat().st_ino == inode
    assert not list((root / 'isolation').glob('.images-backup-*'))


def test_failed_atomic_exchange_keeps_original_and_removes_only_new_copy(tmp_path, monkeypatch):
    import image_setup
    source = source_image(tmp_path); root = tmp_path / 'desktop'
    prepare_image(root, source, tool_version='0.1.819')
    target = root / 'isolation/images/base.qcow2'; inode = target.stat().st_ino
    def refuse(first, second): raise OSError('unsupported filesystem')
    monkeypatch.setattr(image_setup, 'exchange_directories', refuse)
    with pytest.raises(OSError): prepare_image(root, source, tool_version='0.1.819', recover=True)
    assert target.stat().st_ino == inode
    assert not list((root / 'isolation').glob('.images-backup-*'))


@pytest.mark.parametrize('after_exchange', [False, True])
def test_process_death_at_exchange_always_leaves_complete_target(tmp_path, after_exchange):
    import os
    source = source_image(tmp_path); root = tmp_path / 'desktop'
    prepare_image(root, source, tool_version='0.1.819')
    target = root / 'isolation/images/base.qcow2'; original_inode = target.stat().st_ino
    pid = os.fork()
    if pid == 0:
        import image_setup
        exchange = image_setup.exchange_directories
        def terminate(first, second):
            if after_exchange: exchange(first, second)
            os._exit(77)
        image_setup.exchange_directories = terminate
        prepare_image(root, source, tool_version='0.1.819', recover=True)
        os._exit(78)
    _, status = os.waitpid(pid, 0)
    assert os.waitstatus_to_exitcode(status) == 77
    assert target.read_bytes() == source.read_bytes()
    assert (target.stat().st_ino != original_inode) == after_exchange
    backups = list((root / 'isolation').glob('.images-backup-*'))
    assert len(backups) == 1
    assert (backups[0] / 'base.qcow2').read_bytes() == source.read_bytes()
    assert ((backups[0] / 'base.qcow2').stat().st_ino == original_inode) == after_exchange
    assert prepare_image(root, source, tool_version='0.1.819', recover=True)['prepared']
