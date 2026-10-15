import importlib.util
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

spec = importlib.util.spec_from_file_location('desktop_libvirt_helper_fix',
    Path(__file__).resolve().parents[1] / 'desktop/packaging/build-libvirt-helper.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_unknown_vendor_source_is_refused():
    with pytest.raises(ValueError, match='Unsupported'):
        module.patch_source(module.ANCHOR)


@pytest.mark.parametrize('permission_change', ['missing', 'extra', 'removed'])
def test_smoke_rejects_missing_extra_and_removed_permissions(tmp_path, monkeypatch, permission_change):
    baseline = {'"/disk.qcow2" rwk,'}
    def rules(helper, xml):
        socket = module.ET.fromstring(xml).find('./devices/filesystem/source').get('socket')
        if helper == 'baseline': return baseline
        grant = {f'"{socket}" rw,'} if socket else set()
        if permission_change == 'missing': return baseline
        if permission_change == 'extra': return baseline | grant | {'"/unrelated.sock" rw,'}
        return grant
    monkeypatch.setattr(module, 'dry_rules', rules)
    with pytest.raises(ValueError, match='permission delta'):
        module.verify_helpers('baseline', 'patched', tmp_path)


@pytest.mark.parametrize('failure', ['version', 'source', 'existing-output', 'compile'])
def test_refused_build_never_installs_or_overwrites_existing_files(tmp_path, monkeypatch, failure):
    source = tmp_path / 'source'; helper = source / 'src/security/virt-aa-helper.c'
    helper.parent.mkdir(parents=True)
    original = module.ANCHOR
    helper.write_text(original)
    monkeypatch.setattr(module, 'SOURCE_SHA256', hashlib.sha256(original.encode()).hexdigest())
    if failure == 'source': helper.write_text('unexpected source')
    output = tmp_path / 'output'
    if failure == 'existing-output': output.mkdir(); (output / 'marker').write_text('keep')
    calls = []
    def run(args, **kwargs):
        calls.append(args)
        if args[0] == '/usr/bin/dpkg-query':
            return SimpleNamespace(stdout='other' if failure == 'version' else module.UBUNTU_VERSION)
        assert args[0] == '/usr/bin/gcc'
        raise module.subprocess.CalledProcessError(1, args)
    monkeypatch.setattr(module.subprocess, 'run', run)
    with pytest.raises((ValueError, FileExistsError, module.subprocess.CalledProcessError)):
        module.build(source, tmp_path / 'configured', tmp_path / 'headers', output)
    if failure == 'existing-output': assert (output / 'marker').read_text() == 'keep'
    else: assert not output.exists()
    assert not list(tmp_path.glob('.libvirt-helper-*'))
    assert helper.read_text() == ('unexpected source' if failure == 'source' else original)
    assert all(args[0] in ('/usr/bin/dpkg-query', '/usr/bin/gcc') for args in calls)


def test_successful_build_publishes_local_only_artifact_and_license(tmp_path, monkeypatch):
    source = tmp_path / 'source'; helper = source / 'src/security/virt-aa-helper.c'
    helper.parent.mkdir(parents=True); helper.write_text(module.ANCHOR)
    (source / 'COPYING.LESSER').write_text('license fixture')
    monkeypatch.setattr(module, 'SOURCE_SHA256', hashlib.sha256(module.ANCHOR.encode()).hexdigest())
    def run(args, **kwargs):
        if args[0] == '/usr/bin/dpkg-query': return SimpleNamespace(stdout=module.UBUNTU_VERSION)
        assert args[0] == '/usr/bin/gcc'
        Path(args[args.index('-o') + 1]).write_bytes(b'compiled fixture')
    monkeypatch.setattr(module.subprocess, 'run', run)
    verified = []
    monkeypatch.setattr(module, 'verify_helpers', lambda *args: verified.append(args))
    actual_digest = module.digest
    monkeypatch.setattr(module, 'digest', lambda path:
        'system-fixture' if str(path) == '/usr/lib/libvirt/virt-aa-helper' else actual_digest(path))
    output = tmp_path / 'output'
    record = module.build(source, tmp_path / 'configured', tmp_path / 'headers', output)
    assert len(verified) == 2
    assert record['installed'] is False and record['verification'] == 'dry-run-only'
    assert json.loads((output / 'manifest.json').read_text()) == record
    assert (output / 'COPYING.LESSER').read_text() == 'license fixture'
    assert set(record['files']) == {'virt-aa-helper', 'COPYING.LESSER', 'virtiofs-socket.patch'}
    assert helper.read_text() == module.ANCHOR
    assert not list(tmp_path.glob('.libvirt-helper-*'))
