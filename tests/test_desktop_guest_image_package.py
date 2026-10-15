import importlib.util
import json
from pathlib import Path
import subprocess
import sys

import pytest

DESKTOP = Path(__file__).resolve().parents[1] / 'desktop'
sys.path.insert(0, str(DESKTOP))
from factory.core.isolation.base_image import ImageBuildInfo, build_manifest, write_manifest
spec = importlib.util.spec_from_file_location('guest_package', DESKTOP / 'packaging' / 'package-guest-image.py')
package = importlib.util.module_from_spec(spec); spec.loader.exec_module(package)


def source_image(tmp_path):
    image = tmp_path / 'base.qcow2'
    subprocess.run(['/usr/bin/qemu-img', 'create', '-f', 'qcow2', str(image), '1M'], check=True, capture_output=True)
    manifest = write_manifest(build_manifest(image, ImageBuildInfo(tool_version='0.1.819')), image)
    data = json.loads(manifest.read_text())
    data['vsock_port'] = 40519
    data['desktop_channel'] = {'vsock_port': 40519}
    manifest.write_text(json.dumps(data))
    return image


def test_guest_deb_preserves_verified_image_and_has_no_execution_hooks(tmp_path):
    image = source_image(tmp_path)
    original = image.read_bytes()
    output = tmp_path / 'guest.deb'
    package.build(image, output, '0.1.819')
    extracted = tmp_path / 'extract'
    subprocess.run(['dpkg-deb', '-x', str(output), str(extracted)], check=True)
    target = extracted / 'usr/share/igorek-desktop-images/base.qcow2'
    assert target.read_bytes() == image.read_bytes() == original
    assert package.images.check_image(target, '0.1.819')['tool_version'] == '0.1.819'
    control = tmp_path / 'control'
    subprocess.run(['dpkg-deb', '-e', str(output), str(control)], check=True)
    assert {p.name for p in control.iterdir()} == {'control'}
    fields = (control / 'control').read_text()
    assert 'Package: igorek-desktop-guest-image' in fields
    assert 'Version: 0.1.819' in fields
    with pytest.raises(FileExistsError):
        package.build(image, output, '0.1.819')


@pytest.mark.parametrize('failure', ['checksum', 'channel', 'version', 'symlink'])
def test_guest_deb_refuses_invalid_source_before_publication(tmp_path, failure):
    image = source_image(tmp_path)
    version = '0.1.819'
    if failure == 'checksum':
        image.write_bytes(image.read_bytes() + b'changed')
    elif failure == 'channel':
        manifest = image.with_name('base.qcow2.manifest.json')
        data = json.loads(manifest.read_text()); data['vsock_port'] = 5005
        manifest.write_text(json.dumps(data))
    elif failure == 'version':
        version = '0.1.818'
    else:
        other = tmp_path / 'other'; other.mkdir()
        link = other / 'base.qcow2'; link.symlink_to(image); image = link
    output = tmp_path / 'guest.deb'
    with pytest.raises(ValueError):
        package.build(image, output, version)
    assert not output.exists()
