"""Package an existing verified desktop guest image without rebuilding it."""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile

_spec = importlib.util.spec_from_file_location('image_setup', Path(__file__).resolve().parents[1] / 'image_setup.py')
images = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(images)


def build(source: Path, output: Path, version: str):
    source, output = source.absolute(), output.absolute()
    if output.exists():
        raise FileExistsError(output)
    if source.name != 'base.qcow2':
        raise ValueError('Expected base.qcow2')
    print('Проверяю существующий образ и контрольную сумму.', flush=True)
    facts = images.check_image(source, version)
    manifest = source.with_name(source.name + '.manifest.json')
    passport = json.loads(manifest.read_text())
    if passport.get('vsock_port') != 40519 or passport.get('desktop_channel', {}).get('vsock_port') != 40519:
        raise ValueError('Expected the separate desktop guest channel')
    if not version or any(c not in '0123456789.' for c in version):
        raise ValueError('Invalid package version')
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.guest-deb-', dir=output.parent) as directory:
        root = Path(directory) / 'payload'
        target = root / 'usr/share/igorek-desktop-images'
        target.mkdir(parents=True)
        print('Копирую готовый образ; ВМ не пересобирается.', flush=True)
        subprocess.run(['/usr/bin/cp', '--reflink=auto', '--sparse=always', '--', str(source), str(target / source.name)], check=True)
        subprocess.run(['/usr/bin/cp', '--', str(manifest), str(target / manifest.name)], check=True)
        copied = images.check_image(target / source.name, version)
        if copied != facts:
            raise ValueError('Copied image differs from source')
        for path in target.iterdir():
            path.chmod(0o644)
        control = root / 'DEBIAN'; control.mkdir()
        (control / 'control').write_text(
            'Package: igorek-desktop-guest-image\nVersion: ' + version + '\nArchitecture: amd64\n'
            'Maintainer: Igorek Project <noreply@localhost>\nSection: misc\nPriority: optional\n'
            f'Installed-Size: {(facts["size_bytes"] + 1023) // 1024}\n'
            'Description: Verified base image for the Igorek desktop guest\n'
            ' Contains the existing qcow2 and its integrity manifest; does not start a VM.\n')
        archive = Path(directory) / 'image.deb'
        print('Упаковываю образ в отдельный Debian-пакет.', flush=True)
        subprocess.run(['/usr/bin/dpkg-deb', '--root-owner-group', '-Zxz', '-z1', '--build', str(root), str(archive)], check=True,
                       env={**os.environ, 'TMPDIR': directory})
        os.link(archive, output)
    return output


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--tool-version', required=True)
    args = parser.parse_args()
    print(build(args.source, args.output, args.tool_version), flush=True)
