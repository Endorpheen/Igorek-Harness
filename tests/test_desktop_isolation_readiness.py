import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'desktop'))
import isolation_readiness as module
from factory.core.isolation.base_image import ImageBuildInfo, build_manifest, write_manifest


def capabilities(ready=True):
    return dict(kvm_present=ready, libvirt_reachable=ready, virtiofsd_present=ready,
                qemu_img_present=ready, bindings_present=ready)


def checks(report):
    return {item['id']: item for item in report['checks']}


def test_missing_desktop_image_does_not_use_production_configuration(tmp_path, monkeypatch):
    root = tmp_path / 'not-created'
    monkeypatch.setenv('IGOREK_ISOLATION_BASE_IMAGE', '/production/private.qcow2')
    report = module.desktop_readiness(root, capability_probe=capabilities, tool_version='0.1.819')
    result = checks(report)
    assert result['kvm_image']['status'] == 'needs_action'
    assert result['kvm_image']['facts']['path'] == str(root / 'isolation/images/base.qcow2')
    assert result['desktop_execution']['status'] == 'unknown'
    assert '/production/private.qcow2' not in str(report)
    assert not root.exists()


@pytest.mark.parametrize('kind', ['compatible', 'old-version', 'size', 'manifest', 'escape'])
def test_image_metadata_never_claims_full_integrity_or_running_vm(tmp_path, kind):
    root = tmp_path / 'desktop'; image = root / 'isolation/images/base.qcow2'
    image.parent.mkdir(parents=True); image.write_bytes(b'fake-image')
    manifest = build_manifest(image, ImageBuildInfo(tool_version='0.1.818' if kind == 'old-version' else '0.1.819'))
    manifest_path = write_manifest(manifest, image)
    if kind == 'size':
        image.write_bytes(b'larger-image')
    elif kind == 'manifest':
        manifest_path.write_text('invalid')
    elif kind == 'escape':
        image.unlink(); image.symlink_to(tmp_path / 'outside.qcow2')
    result = checks(module.desktop_readiness(root, capability_probe=capabilities, tool_version='0.1.819'))
    assert result['kvm_image']['status'] == ('unknown' if kind == 'compatible' else 'needs_action')
    assert result['desktop_execution']['status'] == 'unknown'


@pytest.mark.parametrize('probe', [lambda: None, lambda: capabilities(False)])
def test_failed_capability_probe_is_not_ready(tmp_path, probe):
    report = checks(module.desktop_readiness(tmp_path, capability_probe=probe, tool_version='0.1.819'))
    assert report['kvm_management']['status'] in {'unknown', 'needs_action'}
    assert report['desktop_execution']['status'] == 'unknown'


def test_capability_timeout_is_bounded_and_unknown(monkeypatch):
    import subprocess
    def timeout(*args, **kwargs):
        assert kwargs['timeout'] == 4
        raise subprocess.TimeoutExpired(args[0], 4)
    monkeypatch.setattr(module.subprocess, 'run', timeout)
    assert module.probe_capabilities() is None


def test_unloadable_binding_is_distinct_from_missing_binding(tmp_path):
    probe = capabilities(False)
    probe['bindings_present'] = True
    probe['bindings_loadable'] = False
    report = checks(module.desktop_readiness(tmp_path, capability_probe=lambda: probe, tool_version='0.1.819'))
    assert report['kvm_management']['status'] == 'needs_action'
    assert report['kvm_management']['facts']['bindings_loadable'] is False
    assert 'не может быть загружен' in report['kvm_management']['message']


def test_readiness_checks_system_libvirt_and_reports_missing_bundled_vsock(tmp_path, monkeypatch):
    import json
    from types import SimpleNamespace
    def probe(args, **kwargs):
        assert "check_host_capabilities('qemu:///system')" in args[3]
        return SimpleNamespace(stdout=json.dumps(dict(capabilities(), bindings_loadable=True, vsock_present=False)))
    monkeypatch.setattr(module.subprocess, 'run', probe)
    result = checks(module.desktop_readiness(tmp_path))
    assert result['kvm_management']['facts']['libvirt_uri'] == 'qemu:///system'
    assert result['kvm_channel']['status'] == 'needs_action'
    assert result['kvm_channel']['facts']['vsock_present'] is False
