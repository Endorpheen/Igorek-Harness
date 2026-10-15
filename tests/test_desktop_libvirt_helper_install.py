import importlib.util
import json
import os
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location('helper_install', Path(__file__).resolve().parents[1] / 'desktop/packaging/install-libvirt-helper.py')
helper = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helper)


@pytest.fixture
def setup(tmp_path, monkeypatch):
    original, patched = b'original helper', b'patched helper'
    monkeypatch.setattr(helper, 'ORIGINAL_SHA', helper.sha(original))
    monkeypatch.setattr(helper, 'PATCHED_SHA', helper.sha(patched))
    target = tmp_path / 'helper'; target.write_bytes(original); target.chmod(0o755)
    payload = tmp_path / 'payload'; payload.mkdir()
    files = {'virt-aa-helper': patched, 'COPYING.LESSER': b'license', 'virtiofs-socket.patch': b'patch'}
    for name, data in files.items(): (payload / name).write_bytes(data)
    manifest = dict(schema_version=1, ubuntu_libvirt_version=helper.VERSION, architecture='x86_64',
                    source_sha256=helper.SOURCE_SHA, system_helper_sha256=helper.ORIGINAL_SHA,
                    verification='dry-run-only', installed=False,
                    files={name: helper.sha(data) for name, data in files.items()})
    (payload / 'manifest.json').write_text(json.dumps(manifest))
    installer = helper.Installer(target, tmp_path / 'state', owner_uid=os.geteuid(),
                                version=lambda: helper.VERSION, check=lambda path: None, package_locks=())
    return installer, payload, original, patched


def test_install_and_rollback_are_idempotent(setup):
    installer, payload, original, patched = setup
    assert installer.install(payload) == 'installed'
    assert installer.target.read_bytes() == patched
    assert installer.target.stat().st_mode & 0o777 == 0o755
    assert (installer.state / 'original').read_bytes() == original
    assert installer.install(payload) == 'already-installed'
    assert installer.rollback() == 'restored'
    assert installer.target.read_bytes() == original
    assert installer.rollback() == 'already-restored'


def test_failed_probe_restores_original(setup):
    installer, payload, original, _ = setup
    def fail(path): raise ValueError('probe failed')
    installer.check = fail
    with pytest.raises(ValueError, match='probe failed'): installer.install(payload)
    assert installer.target.read_bytes() == original
    assert installer.record()['phase'] == 'restored'


@pytest.mark.parametrize('change', ['version', 'target', 'payload', 'symlink', 'permissions'])
def test_refuse_untrusted_input_before_replacement(setup, change):
    installer, payload, original, _ = setup
    if change == 'version': installer.version = lambda: 'new-version'
    elif change == 'target': installer.target.write_bytes(b'vendor update')
    elif change == 'payload':
        (payload / 'virt-aa-helper').write_bytes(b'untrusted')
        manifest = json.loads((payload / 'manifest.json').read_text())
        manifest['files']['virt-aa-helper'] = helper.sha(b'untrusted')
        (payload / 'manifest.json').write_text(json.dumps(manifest))
    elif change == 'symlink':
        other = installer.target.with_name('original'); installer.target.rename(other); installer.target.symlink_to(other)
    else: installer.target.chmod(0o777)
    before = installer.target.read_bytes()
    with pytest.raises(ValueError): installer.install(payload)
    assert installer.target.read_bytes() == before


@pytest.mark.parametrize('change', ['version', 'vendor', 'backup'])
def test_rollback_does_not_overwrite_updates_or_use_bad_backup(setup, change):
    installer, payload, _, _ = setup
    installer.install(payload)
    if change == 'version': installer.version = lambda: 'new-version'
    elif change == 'vendor': installer.target.write_bytes(b'vendor update')
    else: (installer.state / 'original').write_bytes(b'corrupt')
    before = installer.target.read_bytes()
    with pytest.raises(ValueError): installer.rollback()
    assert installer.target.read_bytes() == before


def test_prepared_transaction_can_be_recovered(setup):
    installer, payload, original, _ = setup
    installer.install(payload); installer.journal('prepared', 0o755)
    assert installer.rollback() == 'restored'
    assert installer.target.read_bytes() == original


def test_failed_replace_keeps_original(setup, monkeypatch):
    installer, payload, original, _ = setup
    atomic = installer.atomic
    def fail_target(path, data, mode):
        if path == installer.target: raise OSError('replace failed')
        atomic(path, data, mode)
    monkeypatch.setattr(installer, 'atomic', fail_target)
    with pytest.raises(OSError, match='replace failed'): installer.install(payload)
    assert installer.target.read_bytes() == original
    assert installer.record()['phase'] == 'restored'
