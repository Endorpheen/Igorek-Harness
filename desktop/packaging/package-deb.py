"""Wrap a verified Linux snapshot in a Debian installer package."""
from __future__ import annotations

import argparse
import importlib.util
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile

DEPENDENCIES = ('zenity', 'pkexec', 'python3', 'python3-gi', 'gir1.2-gtk-4.0', 'libvirt0', 'libvirt-daemon-system',
                'qemu-system-x86', 'qemu-utils', 'virtiofsd', 'acl', 'libnss3', 'libnspr4',
                'libgtk-3-0t64', 'libasound2t64', 'libgbm1', 'libxss1', 'libdrm2',
                'libatk-bridge2.0-0t64', 'libxkbcommon0', 'libx11-6', 'libxcomposite1',
                'libxdamage1', 'libxrandr2', 'libxfixes3', 'libcups2t64')


def build(snapshot: Path, output: Path):
    spec = importlib.util.spec_from_file_location('verify_deb_snapshot', Path(__file__).with_name('verify-package.py'))
    verifier = importlib.util.module_from_spec(spec); spec.loader.exec_module(verifier)
    snapshot = snapshot.resolve(); manifest = verifier.verify(snapshot)
    version, identity = manifest['factory_version'], manifest['build_id']
    if not re.fullmatch(r'\d+\.\d+\.\d+', version) or not re.fullmatch(r'[0-9a-f]{12}', identity):
        raise ValueError('Invalid package version or identity')
    if not (snapshot / 'install-gui.sh').is_file():
        raise ValueError('Graphical installer is missing')
    if output.exists(): raise FileExistsError('Output already exists')
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.deb-build-', dir=output.parent) as directory:
        root = Path(directory) / 'root'; root.mkdir()
        target = root / 'opt/igorek-desktop-installer'; target.parent.mkdir()
        print('Copying the verified snapshot; no builds or downloads.', flush=True)
        subprocess.run(['/usr/bin/cp', '-a', '--reflink=auto', '--', str(snapshot), str(target)], check=True)
        verifier.verify(target)
        for path in root.rglob('*'):
            if path.is_symlink(): continue
            if path.is_dir(): path.chmod(0o755)
            else: path.chmod(0o755 if path.stat().st_mode & 0o111 else 0o644)
        control = root / 'DEBIAN'; control.mkdir()
        size = sum(p.stat().st_size for p in target.rglob('*') if p.is_file() and not p.is_symlink())
        (control / 'control').write_text(
            'Package: igorek-desktop-installer\n'
            f'Version: {version}+snapshot.{identity}\nArchitecture: amd64\n'
            'Maintainer: Igorek Project <noreply@localhost>\nSection: utils\nPriority: optional\n'
            f'Installed-Size: {(size + 1023) // 1024}\nDepends: {", ".join(DEPENDENCIES)}\n'
            'Description: Igorek desktop setup for Ubuntu 26.04\n'
            ' Includes the Python and Electron runtimes and graphical user setup.\n')
        applications = root / 'usr/share/applications'; applications.mkdir(parents=True)
        (applications / 'igorek-desktop-installer.desktop').write_text(
            '[Desktop Entry]\nType=Application\nName=Установка Игорька\n'
            'Comment=Установка и обновление Игорька\n'
            'Exec=/opt/igorek-desktop-installer/install-gui.sh\n'
            'Icon=/opt/igorek-desktop-installer/igorek.svg\nTerminal=false\nCategories=Utility;\n')
        archive = Path(directory) / 'installer.deb'
        print('Packing the Debian installer.', flush=True)
        subprocess.run(['/usr/bin/dpkg-deb', '--root-owner-group', '-Zxz', '-z1', '--build', str(root), str(archive)], check=True)
        os.link(archive, output)
    return output


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--snapshot', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    print(build(args.snapshot, args.output), flush=True)
