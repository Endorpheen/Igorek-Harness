import importlib.util
import subprocess
from pathlib import Path

import pytest
from test_desktop_linux_package import source, package

spec = importlib.util.spec_from_file_location('desktop_deb', Path(__file__).resolve().parents[1] / 'desktop/packaging/package-deb.py')
deb = importlib.util.module_from_spec(spec); spec.loader.exec_module(deb)


def test_deb_has_gui_dependencies_and_root_owned_payload(source, tmp_path):
    package.build(source, tmp_path / 'snapshot')
    output = tmp_path / 'installer.deb'
    deb.build(tmp_path / 'snapshot/igorek-desktop', output)
    control = subprocess.check_output(['dpkg-deb', '-f', str(output)], text=True)
    for dependency in ('zenity', 'pkexec', 'libvirt-daemon-system', 'qemu-utils', 'virtiofsd', 'acl'):
        assert dependency in control
    listing = subprocess.check_output(['dpkg-deb', '-c', str(output)], text=True)
    assert 'root/root' in listing and 'install-gui.sh' in listing
    extracted = tmp_path / 'extracted'
    subprocess.run(['dpkg-deb', '-x', str(output), str(extracted)], check=True)
    entry = (extracted / 'usr/share/applications/igorek-desktop-installer.desktop').read_text()
    assert 'Terminal=false' in entry and 'Exec=/opt/igorek-desktop-installer/install-gui.sh' in entry
    package_root = extracted / 'opt/igorek-desktop-installer'
    deb_spec = importlib.util.spec_from_file_location('verified_deb', package_root / 'verify-package.py')
    verifier = importlib.util.module_from_spec(deb_spec); deb_spec.loader.exec_module(verifier)
    verifier.verify(package_root)


def test_deb_refuses_corrupt_snapshot_and_existing_output(source, tmp_path):
    package.build(source, tmp_path / 'snapshot'); root = tmp_path / 'snapshot/igorek-desktop'
    output = tmp_path / 'existing.deb'; output.write_bytes(b'keep')
    with pytest.raises(FileExistsError): deb.build(root, output)
    assert output.read_bytes() == b'keep'
    (root / 'install-gui.sh').write_text('corrupted')
    with pytest.raises(ValueError): deb.build(root, tmp_path / 'invalid.deb')
    assert not (tmp_path / 'invalid.deb').exists()
