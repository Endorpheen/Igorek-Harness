import importlib.util
import json
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location('libvirt_bundle', Path(__file__).resolve().parents[1] / 'desktop/packaging/bundle-libvirt.py')
module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
ABI = 'cpython-314-x86_64-linux-gnu'


@pytest.fixture
def source(tmp_path, monkeypatch):
    monkeypatch.setattr(module.platform, 'freedesktop_os_release', lambda: {'ID': 'ubuntu', 'VERSION_ID': '26.04'})
    monkeypatch.setattr(module.platform, 'machine', lambda: 'x86_64')
    source = tmp_path / 'source'; source.mkdir()
    (source / 'libvirt.py').write_text('fixture')
    (source / ('libvirtmod.' + ABI + '.so')).write_bytes(b'fixture binding')
    metadata = source / 'libvirt_python-12.0.0.dist-info'; metadata.mkdir()
    (metadata / 'METADATA').write_text('Name: libvirt-python\nVersion: 12.0.0\n')
    copyright_file = tmp_path / 'COPYRIGHT'; copyright_file.write_text('fixture license')
    monkeypatch.setattr(module, 'COPYRIGHT', copyright_file)
    bundle = tmp_path / 'bundle'; bundle.mkdir(); (bundle / 'python').write_text('fixture python')
    (bundle / 'manifest.json').write_text(json.dumps({'python': 'python', 'factory_version': '0.1.819'}))
    def probe(argv, **kwargs):
        return ABI + '\n' if argv[-1] == module.ABI_PROBE else json.dumps({'soabi': ABI, 'library_version': 12000000})
    monkeypatch.setattr(module.subprocess, 'check_output', probe)
    return bundle, source


def test_binding_install_is_idempotent_and_records_external_library_requirement(source):
    bundle, origin = source
    result = module.add_bindings(bundle, origin)
    assert result['requires_host_libvirt'] is True
    assert (bundle / 'packages/libvirt-python.COPYRIGHT').read_text() == 'fixture license'
    assert result['package_version'] == '12.0.0'
    assert result['python_soabi'] == ABI
    assert (bundle / 'packages/libvirt.py').read_text() == 'fixture'
    assert module.add_bindings(bundle, origin) == result
    assert json.loads((bundle / 'manifest.json').read_text())['factory_version'] == '0.1.819'


@pytest.mark.parametrize('kind', ['abi', 'load', 'platform', 'symlink', 'conflict'])
def test_failed_compatibility_does_not_publish_bindings_or_metadata(source, monkeypatch, kind):
    import subprocess
    bundle, origin = source
    if kind == 'abi':
        monkeypatch.setattr(module.subprocess, 'check_output', lambda *a, **kw: 'cpython-313-x86_64-linux-gnu\n')
    elif kind == 'load':
        def failed(argv, **kwargs):
            if argv[-1] == module.ABI_PROBE:
                return ABI + '\n'
            raise subprocess.CalledProcessError(1, argv)
        monkeypatch.setattr(module.subprocess, 'check_output', failed)
    elif kind == 'platform':
        monkeypatch.setattr(module.platform, 'freedesktop_os_release', lambda: {'ID': 'ubuntu', 'VERSION_ID': '24.04'})
    elif kind == 'symlink':
        (origin / 'libvirt.py').unlink(); (origin / 'libvirt.py').symlink_to(origin / ('libvirtmod.' + ABI + '.so'))
    else:
        (bundle / 'packages').mkdir(); (bundle / 'packages/libvirt.py').write_text('unrelated')
    before = (bundle / 'manifest.json').read_bytes()
    with pytest.raises((ValueError, subprocess.CalledProcessError)):
        module.add_bindings(bundle, origin)
    assert (bundle / 'manifest.json').read_bytes() == before
    assert not (bundle / 'packages' / ('libvirtmod.' + ABI + '.so')).exists()


def test_package_binding_verification_rejects_changed_payload_and_unknown_names(source):
    bundle, origin = source
    module.add_bindings(bundle, origin)
    module.verify_bindings(bundle)
    (bundle / 'packages/libvirt.py').write_text('tampered')
    with pytest.raises(ValueError, match='Changed bundled'):
        module.verify_bindings(bundle)
    manifest_file = bundle / 'manifest.json'
    manifest = json.loads(manifest_file.read_text())
    manifest['desktop_kvm_bindings']['files']['../private'] = 'untrusted'
    manifest_file.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match='Invalid libvirt'):
        module.verify_bindings(bundle)
