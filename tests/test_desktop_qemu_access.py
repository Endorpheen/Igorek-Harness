import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'desktop'))
import qemu_access as module
from factory.core.isolation.errors import IsolationError


def test_storage_acl_is_scoped_to_vm_paths_and_never_vault(tmp_path, monkeypatch):
    root = tmp_path / 'desktop'; images = root / 'isolation/images'; images.mkdir(parents=True)
    (images / 'base.qcow2').write_bytes(b'fixture')
    vault = root / 'vault'; vault.mkdir(mode=0o700); (vault / 'key').write_bytes(b'fixture')
    calls = []
    monkeypatch.setattr(module, 'qemu_uid', lambda: 64055)
    monkeypatch.setattr(module.subprocess, 'run', lambda args, **kwargs: calls.append(args))
    assert module.prepare_access(root) == 64055
    assert ['/usr/bin/setfacl','-m','u:64055:--x',str(root)] in calls
    assert ['/usr/bin/setfacl','-m','u:64055:r--',str(images/'base.qcow2')] in calls
    assert ['/usr/bin/setfacl','-m','d:u:64055:rwx',str(root/'isolation/overlays')] in calls
    assert all(not Path(args[-1]).is_relative_to(vault) for args in calls)
    assert all('-R' not in args for args in calls)
    assert vault.stat().st_mode & 0o777 == 0o700


def test_symlink_image_is_refused_before_acl_changes(tmp_path, monkeypatch):
    root=tmp_path/'desktop';images=root/'isolation/images';images.mkdir(parents=True)
    source=tmp_path/'external';source.write_bytes(b'fixture');(images/'base.qcow2').symlink_to(source)
    def forbidden(*a,**k): raise AssertionError('ACL called on rejected image')
    monkeypatch.setattr(module.subprocess,'run',forbidden)
    with pytest.raises(IsolationError,match='storage_path'):module.prepare_access(root)


@pytest.mark.parametrize('identity', ['+64055:+109', '+0:+0', '+4294967295:+4294967295', 'unknown', '+1:+2\n+3:+4'])
def test_qemu_identity_comes_from_system_capabilities_and_connection_closes(monkeypatch, identity):
    closed=[]
    connection=SimpleNamespace(getCapabilities=lambda:
        '<capabilities><host><secmodel><model>dac</model><baselabel type="kvm">'+identity+'</baselabel></secmodel></host></capabilities>',
        close=lambda:closed.append(True))
    def open_connection(uri):
        assert uri=='qemu:///system';return connection
    monkeypatch.setitem(sys.modules,'libvirt',SimpleNamespace(open=open_connection))
    if identity=='+64055:+109':assert module.qemu_uid()==64055
    else:
        with pytest.raises(IsolationError):module.qemu_uid()
    assert closed==[True]


@pytest.mark.parametrize('kind', ['allowed', 'missing', 'other-socket', 'read-only', 'helper-failure', 'timeout', 'no-apparmor'])
def test_socket_policy_requires_exact_read_write_grant_without_loading_profiles(tmp_path, monkeypatch, kind):
    import subprocess
    import xml.etree.ElementTree as ET
    root = tmp_path / 'desktop'; images = root / 'isolation/images'
    images.mkdir(parents=True); (images / 'base.qcow2').write_bytes(b'fixture')
    (root / 'workspace').mkdir()
    closed = []
    connection = SimpleNamespace(getCapabilities=lambda:
        '<capabilities><host><secmodel><model>' + ('dac' if kind == 'no-apparmor' else 'apparmor') +
        '</model></secmodel></host></capabilities>', close=lambda: closed.append(True))
    monkeypatch.setitem(sys.modules, 'libvirt', SimpleNamespace(open=lambda uri: connection))
    calls = []
    def dryrun(args, **kwargs):
        calls.append(args)
        assert args[1:4] == ['-d', '-c', '-u']
        assert kwargs['timeout'] == 5 and kwargs['env'] == {'PATH': '/usr/bin:/bin'}
        xml = ET.fromstring(kwargs['input'])
        socket = xml.find('./devices/filesystem/source').get('socket')
        assert not Path(socket).exists()
        if kind == 'timeout': raise subprocess.TimeoutExpired(args, 5)
        path = socket + '.other' if kind == 'other-socket' else socket
        permission = 'r' if kind == 'read-only' else 'rw'
        output = '' if kind == 'missing' else f'  "{path}" {permission},\n'
        return SimpleNamespace(returncode=1 if kind == 'helper-failure' else 0, stdout='', stderr=output)
    monkeypatch.setattr(module.subprocess, 'run', dryrun)
    monkeypatch.setattr(module.Path, 'is_file', lambda self: True)
    if kind in ('allowed', 'no-apparmor'):
        module.verify_socket_policy(root)
    else:
        with pytest.raises(IsolationError, match='desktop_socket_policy_'):
            module.verify_socket_policy(root)
    assert closed == [True]
    assert len(calls) == (0 if kind == 'no-apparmor' else 1)
