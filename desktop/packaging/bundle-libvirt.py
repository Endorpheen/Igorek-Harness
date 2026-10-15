"""Add Ubuntu 26.04 libvirt bindings to a compatible standalone Python bundle."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import shutil
import subprocess
import tempfile
from pathlib import Path


PROBE = '''import json,sys,sysconfig
sys.path.insert(0,sys.argv[1])
import libvirt
print(json.dumps({'soabi':sysconfig.get_config_var('SOABI'),'library_version':libvirt.getVersion()}))
'''
COPYRIGHT = Path('/usr/share/doc/python3-libvirt/copyright')
ABI_PROBE = "import sysconfig;print(sysconfig.get_config_var('SOABI'))"


def verify_bindings(bundle: Path):
    manifest = json.loads((bundle / 'manifest.json').read_text())
    record = manifest.get('desktop_kvm_bindings')
    if record is None:
        return
    names = {'libvirt.py', 'libvirtmod.cpython-314-x86_64-linux-gnu.so', 'libvirt-python.COPYRIGHT'}
    if (not isinstance(record, dict) or record.get('schema_version') != 1
            or record.get('target') != 'ubuntu-26.04-x86_64'
            or record.get('python_soabi') != 'cpython-314-x86_64-linux-gnu'
            or record.get('requires_host_libvirt') is not True
            or not isinstance(record.get('files'), dict) or set(record['files']) != names):
        raise ValueError('Invalid libvirt binding manifest')
    packages = bundle / 'packages'
    for name, digest in record['files'].items():
        path = packages / name
        if (path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(packages.resolve())
                or hashlib.sha256(path.read_bytes()).hexdigest() != digest):
            raise ValueError('Changed bundled libvirt binding: ' + name)


def add_bindings(bundle: Path, source: Path = Path('/usr/lib/python3/dist-packages')):
    release = platform.freedesktop_os_release()
    if release.get('ID') != 'ubuntu' or release.get('VERSION_ID') != '26.04' or platform.machine() != 'x86_64':
        raise ValueError('This binding recipe supports Ubuntu 26.04 x86_64 only')
    bundle = bundle.resolve(); source = source.resolve()
    manifest_path = bundle / 'manifest.json'
    manifest = json.loads(manifest_path.read_text())
    relative = Path(manifest['python'])
    if relative.is_absolute() or '..' in relative.parts:
        raise ValueError('Invalid bundled Python path')
    python = bundle / relative
    if not python.resolve().is_relative_to(bundle):
        raise ValueError('Bundled Python escapes the bundle')
    abi = subprocess.check_output([str(python), '-I', '-c', ABI_PROBE], text=True, timeout=10).strip()
    if abi != 'cpython-314-x86_64-linux-gnu':
        raise ValueError('Bundled Python ABI does not match the Ubuntu bindings')
    files = ['libvirt.py', f'libvirtmod.{abi}.so']
    for name in files:
        path = source / name
        if path.is_symlink() or not path.is_file():
            raise ValueError('Missing regular Ubuntu libvirt binding: ' + name)
    if COPYRIGHT.is_symlink() or not COPYRIGHT.is_file():
        raise ValueError('Ubuntu libvirt copyright information is missing')
    sources = {name: source / name for name in files}
    sources['libvirt-python.COPYRIGHT'] = COPYRIGHT
    files = list(sources)
    metadata = list(source.glob('libvirt_python-*.dist-info/METADATA'))
    if len(metadata) != 1 or metadata[0].is_symlink():
        raise ValueError('Expected one libvirt-python package metadata file')
    version = next((line[9:] for line in metadata[0].read_text().splitlines() if line.startswith('Version: ')), None)
    if not version:
        raise ValueError('Libvirt package version is missing')
    packages = bundle / 'packages'
    if packages.is_symlink():
        raise ValueError('Package directory must not be a symlink')
    packages.mkdir(exist_ok=True)
    expected = {name: hashlib.sha256(sources[name].read_bytes()).hexdigest() for name in files}
    for name in files:
        target = packages / name
        if target.is_symlink() or (target.exists() and hashlib.sha256(target.read_bytes()).hexdigest() != expected[name]):
            raise ValueError('Existing binding differs; it will not be overwritten')
    with tempfile.TemporaryDirectory(prefix='.libvirt-stage-', dir=bundle) as temporary:
        stage = Path(temporary)
        for name in files:
            shutil.copy2(sources[name], stage / name)
            (stage / name).chmod(0o644)
            if hashlib.sha256((stage / name).read_bytes()).hexdigest() != expected[name]:
                raise ValueError('Binding changed while copying')
        checked = json.loads(subprocess.check_output([str(python), '-I', '-c', PROBE, str(stage)], text=True, timeout=10))
        if checked.get('soabi') != abi or not isinstance(checked.get('library_version'), int):
            raise ValueError('Native libvirt binding verification failed')
        for name in files:
            if not (packages / name).exists():
                os.replace(stage / name, packages / name)
    manifest['desktop_kvm_bindings'] = {
        'schema_version': 1, 'source': 'ubuntu-system-package', 'target': 'ubuntu-26.04-x86_64',
        'package_version': version, 'python_soabi': abi, 'library_version': checked['library_version'],
        'requires_host_libvirt': True, 'files': expected,
    }
    fd, name = tempfile.mkstemp(prefix='.libvirt-manifest-', dir=bundle)
    try:
        with os.fdopen(fd, 'w') as stream:
            json.dump(manifest, stream, indent=2); stream.write('\n')
        os.replace(name, manifest_path)
    finally:
        Path(name).unlink(missing_ok=True)
    return manifest['desktop_kvm_bindings']


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--runtime', type=Path, required=True)
    parser.add_argument('--source', type=Path, default=Path('/usr/lib/python3/dist-packages'))
    args = parser.parse_args()
    print(json.dumps(add_bindings(args.runtime, args.source)), flush=True)
