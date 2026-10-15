"""Build-only Ubuntu 26.04 compatibility fix; never install or load profiles."""
from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import tempfile
import uuid
import xml.etree.ElementTree as ET

UBUNTU_VERSION = '12.0.0-1ubuntu5.5'
SOURCE_SHA256 = 'd922d6fc2bda6e0afc06a8a8380e803b651519f3d230120c3579a50311c86b22'
ANCHOR = '''        virDomainFSDef *fs = ctl->def->fss[i];

        if (fs->type == VIR_DOMAIN_FS_TYPE_MOUNT &&'''
FIX = '''        virDomainFSDef *fs = ctl->def->fss[i];

        if (fs->fsdriver == VIR_DOMAIN_FS_DRIVER_TYPE_VIRTIOFS && fs->sock) {
            if (vah_add_file(&buf, fs->sock, "rw") != 0)
                return -1;
        }

        if (fs->type == VIR_DOMAIN_FS_TYPE_MOUNT &&'''


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def patch_source(original):
    if hashlib.sha256(original.encode()).hexdigest() != SOURCE_SHA256 or original.count(ANCHOR) != 1:
        raise ValueError('Unsupported libvirt helper source')
    return original.replace(ANCHOR, FIX, 1)


def dry_rules(helper, xml):
    identity = ET.fromstring(xml).findtext('uuid')
    result = subprocess.run([str(helper), '-d', '-c', '-u', 'libvirt-' + identity],
                            input=xml, capture_output=True, text=True, timeout=10,
                            env={'PATH': '/usr/bin:/bin'})
    if result.returncode:
        raise ValueError('Libvirt helper dry-run failed')
    return {line.strip() for line in (result.stdout + '\n' + result.stderr).splitlines()
            if line.strip().startswith('"') and line.strip().endswith(',')}


def verify_helpers(original, patched, directory):
    directory = Path(directory)
    workspace = directory / 'workspace'; workspace.mkdir()
    transport = directory / 'transport.sock'
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as listener:
        listener.bind(str(transport))
        base = ET.fromstring(f'''<domain type="kvm">
          <name>igorek-helper-probe</name><uuid>{uuid.uuid4()}</uuid>
          <memory unit="MiB">256</memory><vcpu>1</vcpu>
          <os><type arch="x86_64" machine="q35">hvm</type></os>
          <devices><emulator>/usr/bin/qemu-system-x86_64</emulator>
          <filesystem type="mount"><driver type="virtiofs"/>
          <source/><target dir="workspace"/></filesystem></devices></domain>''')
        filesystem = base.find('./devices/filesystem')
        source = filesystem.find('source')
        for kind in ('external', 'managed', 'legacy-path'):
            source.attrib.clear()
            source.set('socket' if kind == 'external' else 'dir', str(transport if kind == 'external' else workspace))
            filesystem.find('driver').set('type', 'path' if kind == 'legacy-path' else 'virtiofs')
            xml = ET.tostring(base, encoding='unicode')
            before = dry_rules(original, xml); after = dry_rules(patched, xml)
            expected = {f'"{transport}" rw,'} if kind == 'external' else set()
            if not before <= after or after - before != expected:
                raise ValueError('Unexpected AppArmor permission delta: ' + kind)
            print('PASS: ' + kind + ' permissions', flush=True)
    transport.unlink()


def build(source, configured, headers, output):
    source = Path(source).resolve(); configured = Path(configured).resolve()
    headers = Path(headers).resolve(); output = Path(output).absolute()
    installed = subprocess.run(['/usr/bin/dpkg-query', '-W', '-f=${Version}', 'libvirt0'],
                               check=True, capture_output=True, text=True).stdout
    if installed != UBUNTU_VERSION or os.uname().machine != 'x86_64':
        raise ValueError('Unsupported installed libvirt version/platform')
    helper_source = source / 'src/security/virt-aa-helper.c'
    original = helper_source.read_text(); patched = patch_source(original)
    if output.exists() or output.is_symlink():
        raise FileExistsError(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    system_helper = Path('/usr/lib/libvirt/virt-aa-helper')
    with tempfile.TemporaryDirectory(prefix='.libvirt-helper-', dir=output.parent) as temporary:
        stage = Path(temporary)
        baseline = stage / 'baseline.c'; baseline.write_text(original)
        fixed = stage / 'fixed.c'; fixed.write_text(patched)
        include = [configured, source, configured / 'include', source / 'include',
                   *[source / 'src' / part for part in ('', 'util', 'conf', 'security', 'storage_file', 'hypervisor')],
                   headers / 'usr/include/glib-2.0', headers / 'usr/lib/x86_64-linux-gnu/glib-2.0/include',
                   Path('/usr/include/libxml2')]
        flags = ['-O2', '-D_GNU_SOURCE', '-DAPPARMOR_DIR="/etc/apparmor.d"', *['-I' + str(path) for path in include]]
        libraries = ['libvirt.so.0', 'libglib-2.0.so.0', 'libxml2.so.16', 'libgobject-2.0.so.0', 'libgio-2.0.so.0']
        for src, binary in ((baseline, stage / 'baseline'), (fixed, stage / 'virt-aa-helper')):
            subprocess.run(['/usr/bin/gcc', *flags, str(src), '-o', str(binary),
                            *['-l:' + library for library in libraries]], check=True, timeout=60)
        with tempfile.TemporaryDirectory(prefix='igorek-aa-check-') as checks:
            verify_helpers(stage / 'baseline', stage / 'virt-aa-helper', checks)
        with tempfile.TemporaryDirectory(prefix='igorek-aa-check-') as comparison:
            verify_helpers(system_helper, stage / 'virt-aa-helper', comparison)
        payload = stage / 'payload'; payload.mkdir()
        shutil.copy2(stage / 'virt-aa-helper', payload / 'virt-aa-helper')
        shutil.copy2(source / 'COPYING.LESSER', payload / 'COPYING.LESSER')
        (payload / 'virtiofs-socket.patch').write_text(''.join(difflib.unified_diff(
            original.splitlines(True), patched.splitlines(True),
            fromfile='a/src/security/virt-aa-helper.c', tofile='b/src/security/virt-aa-helper.c')))
        record = {'schema_version': 1, 'ubuntu_libvirt_version': installed, 'architecture': 'x86_64',
                  'source_sha256': SOURCE_SHA256, 'system_helper_sha256': digest(system_helper),
                  'verification': 'dry-run-only', 'installed': False,
                  'files': {name: digest(payload / name) for name in ('virt-aa-helper', 'COPYING.LESSER', 'virtiofs-socket.patch')}}
        (payload / 'manifest.json').write_text(json.dumps(record, indent=2) + '\n')
        payload.rename(output)
    return record


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description='Подготовка одного helper разработчиком; системная установка не выполняется.')
    parser.add_argument('--source', required=True, type=Path)
    parser.add_argument('--configured', required=True, type=Path)
    parser.add_argument('--headers', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    build(args.source, args.configured, args.headers, args.output)
    print('Готово. Система не изменена; гостевой запуск ещё не проверен.', flush=True)
