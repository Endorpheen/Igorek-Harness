"""Build the pinned standard CPython socket module with Linux VSOCK support."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import urllib.request

SOURCES = {
    "socketmodule.c": "9b2b0599d18bbf21ed312a224d7e4965fc9cc9d2c52cea39577348d9ac8ac8cc",
    "socketmodule.h": "54226a0bb807e9d19a39afec055bdffa96ebc3be37e367fb68b030da98a77a51",
    "clinic/socketmodule.c.h": "915c7428c9d1769c8eb1aec8ac74f49bab23a85f3956b78ffd2bc88b5edb7253",
    "addrinfo.h": "14f80b91404b86a33df2f6abe94df3e0b28330a8e03f956241c68d9d30350fb4",
    "LICENSE": "b0e25a78cffb43f4d92de8b61ccfa1f1f98ecbc22330b54b5251e7b6ba010231"
}

NAME = '_socket.cpython-314-x86_64-linux-gnu.so'
PROBE = """import sys
sys.path.insert(0,sys.argv[1])
from socket_bootstrap import activate
assert activate(sys.argv[1])
import socket,ssl
assert hasattr(socket,'AF_VSOCK')
a,b=socket.socketpair()
a.sendall(b'ok');assert b.recv(2)==b'ok'
a.close();b.close()
print('PASS')
"""


def verify_socket(bundle):
    bundle = Path(bundle)
    record = json.loads((bundle / 'manifest.json').read_text()).get('desktop_socket')
    if record is None:
        return
    if (not isinstance(record, dict) or record.get('schema_version') != 1
            or record.get('python_version') not in {'3.14.3', '3.14.4'}
            or record.get('python_soabi') != 'cpython-314-x86_64-linux-gnu'
            or set(record.get('files', {})) != {NAME, 'CPython-socket.LICENSE'}):
        raise ValueError('Invalid socket bundle manifest')
    if (bundle / 'native').is_symlink():
        raise ValueError('Native directory must not be a symlink')
    for name, digest in record['files'].items():
        path = bundle / 'native' / name
        if (path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(bundle.resolve())
                or hashlib.sha256(path.read_bytes()).hexdigest() != digest):
            raise ValueError('Changed socket bundle payload')


def add_socket(bundle, source=None):
    bundle = Path(bundle).resolve()
    manifest_path = bundle / 'manifest.json'
    manifest = json.loads(manifest_path.read_text())
    relative = Path(manifest['python'])
    if relative.is_absolute() or '..' in relative.parts:
        raise ValueError('Invalid Python path')
    python = (bundle / relative).resolve()
    if not python.is_relative_to(bundle):
        raise ValueError('Python escapes bundle')
    abi = subprocess.check_output([str(python), '-I', '-c',
        'import sys,sysconfig;print(sys.version_info[:3]);print(sysconfig.get_config_var("SOABI"))'], text=True)
    if abi.splitlines() != ['(3, 14, 4)', 'cpython-314-x86_64-linux-gnu']:
        raise ValueError('Unsupported bundled Python ABI/version')
    if manifest.get('desktop_socket') is not None:
        verify_socket(bundle)
        return manifest['desktop_socket']
    native = bundle / 'native'
    if native.exists() or native.is_symlink():
        raise ValueError('Native destination already exists')
    with tempfile.TemporaryDirectory(prefix='.socket-build-', dir=bundle) as temporary:
        stage = Path(temporary)
        for name, digest in SOURCES.items():
            target = stage / name
            target.parent.mkdir(parents=True, exist_ok=True)
            if source is None:
                url = 'https://raw.githubusercontent.com/python/cpython/v3.14.4/' + (name if name == 'LICENSE' else 'Modules/' + name)
                with urllib.request.urlopen(url, timeout=30) as response:
                    data = response.read()
            else:
                origin = Path(source) / name
                if origin.is_symlink():
                    raise ValueError('Source symlink refused')
                data = origin.read_bytes()
            if hashlib.sha256(data).hexdigest() != digest:
                raise ValueError('CPython source checksum mismatch: ' + name)
            target.write_bytes(data)
        include = python.parent.parent / 'include/python3.14'
        payload = stage / 'native'; payload.mkdir()
        subprocess.run(['gcc', '-shared', '-fPIC', '-O2', '-DHAVE_LINUX_VM_SOCKETS_H=1',
            '-I' + str(include), '-I' + str(include / 'internal'), str(stage / 'socketmodule.c'),
            '-o', str(payload / NAME)], check=True, timeout=120)
        shutil.copy2(stage / 'LICENSE', payload / 'CPython-socket.LICENSE')
        record = dict(schema_version=1, python_version='3.14.4', python_soabi='cpython-314-x86_64-linux-gnu',
            sources=SOURCES, files={p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in payload.iterdir()})
        check = stage / 'probe'; check.mkdir()
        shutil.copytree(payload, check / 'native')
        shutil.copy2(Path(__file__).resolve().parents[1] / 'socket_bootstrap.py', check / 'socket_bootstrap.py')
        (check / 'manifest.json').write_text(json.dumps({'desktop_socket':record}))
        result = subprocess.check_output([str(python), '-I', '-c', PROBE, str(check)], text=True, timeout=15)
        if result.strip() != 'PASS':
            raise ValueError('Socket compatibility probe failed')
        payload.rename(native)
    manifest['desktop_socket'] = record
    fd, name = tempfile.mkstemp(prefix='.socket-manifest-', dir=bundle)
    try:
        with os.fdopen(fd, 'w') as stream:
            json.dump(manifest, stream, indent=2); stream.write('\n')
        os.replace(name, manifest_path)
    finally:
        Path(name).unlink(missing_ok=True)
    return record


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--runtime', type=Path, required=True)
    parser.add_argument('--source', type=Path)
    args = parser.parse_args()
    add_socket(args.runtime, args.source)
    print('Socket bundle verified', flush=True)
