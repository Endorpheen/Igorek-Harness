import importlib.util
import json
from pathlib import Path

import pytest

spec = importlib.util.spec_from_file_location('socket_bundle', Path(__file__).resolve().parents[1] / 'desktop/packaging/bundle-socket.py')
module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)


@pytest.fixture
def bundle(tmp_path):
    root = tmp_path / 'bundle'; root.mkdir()
    (root / 'python').write_bytes(b'fixture')
    (root / 'manifest.json').write_text(json.dumps({'python':'python','factory_version':'0.1.819'}))
    return root


def test_socket_recipe_pins_cpython_sources_and_enables_linux_header(bundle, tmp_path, monkeypatch):
    import hashlib
    from types import SimpleNamespace
    source = tmp_path / 'source'; source.mkdir()
    for name in module.SOURCES:
        target = source / name; target.parent.mkdir(parents=True, exist_ok=True); target.write_bytes(b'fixture')
    monkeypatch.setattr(module, 'SOURCES', {name: hashlib.sha256(b'fixture').hexdigest() for name in module.SOURCES})
    def probe(args, **kwargs):
        return '(3, 14, 4)\ncpython-314-x86_64-linux-gnu\n' if 'sysconfig' in args[-1] else 'PASS\n'
    def compile(args, **kwargs):
        assert '-DHAVE_LINUX_VM_SOCKETS_H=1' in args
        Path(args[-1]).write_bytes(b'fixture socket')
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(module.subprocess, 'check_output', probe)
    monkeypatch.setattr(module.subprocess, 'run', compile)
    record = module.add_socket(bundle, source)
    assert record['python_version'] == '3.14.4'
    assert set(record['files']) == {module.NAME, 'CPython-socket.LICENSE'}
    module.verify_socket(bundle)
    assert module.add_socket(bundle, source) == record
    assert json.loads((bundle / 'manifest.json').read_text())['factory_version'] == '0.1.819'
    (bundle / 'native' / module.NAME).write_bytes(b'changed')
    with pytest.raises(ValueError, match='Changed socket'):
        module.verify_socket(bundle)


@pytest.mark.parametrize('kind', ['abi', 'checksum', 'path'])
def test_bad_socket_build_does_not_publish(bundle, tmp_path, monkeypatch, kind):
    monkeypatch.setattr(module.subprocess, 'check_output', lambda *a, **k:
        '(3, 14, 5)\ncpython-314-x86_64-linux-gnu\n' if kind == 'abi' else '(3, 14, 4)\ncpython-314-x86_64-linux-gnu\n')
    source = tmp_path / 'source'; source.mkdir(); (source / 'socketmodule.c').write_bytes(b'wrong')
    if kind == 'path':
        (bundle / 'manifest.json').write_text(json.dumps({'python':'../external'}))
    before = (bundle / 'manifest.json').read_bytes()
    with pytest.raises(ValueError):
        module.add_socket(bundle, source)
    assert (bundle / 'manifest.json').read_bytes() == before
    assert not (bundle / 'native').exists()


def test_source_recipe_uses_exact_official_release():
    assert len(module.SOURCES) == 5
    assert all(len(digest) == 64 for digest in module.SOURCES.values())
    assert "python/cpython/v3.14.4/" in Path(module.__file__).read_text()


@pytest.mark.parametrize('kind', ['changed', 'symlink', 'late_import', 'unknown_record'])
def test_socket_bootstrap_rejects_unverified_or_late_loading(bundle, tmp_path, kind):
    import hashlib
    import sys
    spec = importlib.util.spec_from_file_location('socket_bootstrap_fixture', Path(module.__file__).resolve().parents[1] / 'socket_bootstrap.py')
    bootstrap = importlib.util.module_from_spec(spec); spec.loader.exec_module(bootstrap)
    native = bundle / 'native'; native.mkdir()
    files = {}
    for name in (module.NAME, 'CPython-socket.LICENSE'):
        path = native / name; path.write_bytes(b'fixture'); files[name] = hashlib.sha256(b'fixture').hexdigest()
    record = dict(schema_version=1, python_version='.'.join(map(str, __import__('sys').version_info[:3])), python_soabi=__import__('sysconfig').get_config_var('SOABI'), files=files)
    if kind == 'changed':
        (native / module.NAME).write_bytes(b'tampered')
    elif kind == 'symlink':
        (native / module.NAME).unlink(); (native / module.NAME).symlink_to(native / 'CPython-socket.LICENSE')
    elif kind == 'unknown_record':
        record['files']['../other'] = files[module.NAME]
    (bundle / 'manifest.json').write_text(json.dumps({'desktop_socket':record}))
    if kind == 'late_import':
        assert 'socket' in sys.modules
        with pytest.raises(RuntimeError, match='precede networking'):
            bootstrap.activate(bundle)
    else:
        with pytest.raises(ValueError):
            bootstrap.activate(bundle)
