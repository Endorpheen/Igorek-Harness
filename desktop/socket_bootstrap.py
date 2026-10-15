"""Load the verified CPython socket extension before networking imports."""
import hashlib
import importlib.util
import json
from pathlib import Path
import sys
import sysconfig


NAME = '_socket.cpython-314-x86_64-linux-gnu.so'


def activate(bundle):
    bundle = Path(bundle).resolve()
    manifest = bundle / 'manifest.json'
    if not manifest.exists():
        return False
    record = json.loads(manifest.read_text()).get('desktop_socket')
    if record is None:
        return False
    if (not isinstance(record, dict) or record.get('schema_version') != 1 or record.get('python_version') != '.'.join(map(str, sys.version_info[:3]))
            or record.get('python_soabi') != sysconfig.get_config_var('SOABI')
            or set(record.get('files', {})) != {NAME, 'CPython-socket.LICENSE'}):
        raise ValueError('Invalid socket bundle manifest')
    if (bundle / 'native').is_symlink():
        raise ValueError('Native directory must not be a symlink')
    for name, digest in record['files'].items():
        path = bundle / 'native' / name
        if (path.is_symlink() or not path.is_file() or not path.resolve().is_relative_to(bundle)
                or hashlib.sha256(path.read_bytes()).hexdigest() != digest):
            raise ValueError('Changed socket bundle payload')
    if 'socket' in sys.modules or 'ssl' in sys.modules:
        raise RuntimeError('Socket bootstrap must precede networking imports')
    spec = importlib.util.spec_from_file_location('_socket', bundle / 'native' / NAME)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    if not hasattr(module, 'AF_VSOCK'):
        raise ValueError('Socket extension lacks AF_VSOCK')
    sys.modules['_socket'] = module
    return True
