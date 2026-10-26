"""Domain lifecycle failure contracts; all hypervisor/process seams are fakes."""
import os
from pathlib import Path
import socket
import subprocess
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from factory.core.isolation import domain as d


@pytest.mark.parametrize('result', ['missing-module', 'open-error', 'no-connection', 'ok'])
def test_libvirt_connection_failures_are_typed(monkeypatch, result):
    connection = object()
    importer = Mock()
    if result == 'missing-module':
        importer.side_effect = ImportError('fixture absent')
    else:
        opener = Mock(side_effect=RuntimeError('fixture denied') if result == 'open-error' else None, return_value=connection if result == 'ok' else None)
        importer.return_value = SimpleNamespace(open=opener)
    monkeypatch.setattr(d.importlib, 'import_module', importer)
    if result == 'ok':
        assert d.open_libvirt_connection('fixture:///session') is connection
    else:
        with pytest.raises(d.IsolationError, match='libvirt_unavailable'):
            d.open_libvirt_connection('fixture:///session')


@pytest.mark.parametrize('failure', ['device', 'virtiofs', 'qemu', 'connection', 'close', 'none'])
def test_capability_probe_reports_first_missing_dependency_without_starting_vm(tmp_path, monkeypatch, failure):
    device = tmp_path / 'kvm-fixture'
    virtiofs = tmp_path / 'virtiofs-fixture'
    if failure != 'device':
        device.touch()
    if failure not in {'device', 'virtiofs'}:
        virtiofs.touch()
    monkeypatch.setattr(d, '_KVM_DEVICE', device)
    monkeypatch.setattr(d.shutil, 'which', lambda _: None if failure == 'qemu' else '/fixture/qemu-img')
    connection = Mock()
    if failure == 'close':
        connection.close.side_effect = RuntimeError('fixture close')
    opener = Mock(side_effect=d.IsolationError('fixture_connection') if failure == 'connection' else None, return_value=connection)
    monkeypatch.setattr(d, 'open_libvirt_connection', opener)
    reason = d.kvm_unavailable_reason(virtiofsd_path=str(virtiofs))
    if failure in {'close', 'none'}:
        assert reason is None
        connection.close.assert_called_once()
    else:
        assert reason and {'device': str(device), 'virtiofs': str(virtiofs), 'qemu': 'qemu-img', 'connection': 'fixture_connection'}[failure] in reason
    if failure in {'device', 'virtiofs', 'qemu'}:
        opener.assert_not_called()


@pytest.fixture
def domain(tmp_path, monkeypatch):
    spec = d.DomainSpec('fixture', tmp_path/'base', tmp_path/'workspace', tmp_path/'overlays', host_uid=1000,host_gid=1000,virtiofs_socket=tmp_path/'owned.sock')
    instance = d.SessionDomain(Mock(), runner=Mock(return_value=d.CommandResult(0)))
    instance._spec = spec
    monkeypatch.setattr(d, 'time', SimpleNamespace(sleep=lambda _: None))
    return instance


@pytest.mark.parametrize('failure', ['destroy', 'state', 'undefine', 'stop', 'none'])
def test_teardown_attempts_remaining_cleanup_after_domain_error(domain, tmp_path, monkeypatch, failure):
    handle = Mock()
    handle.isActive.return_value = 1
    if failure == 'state':
        handle.isActive.side_effect = RuntimeError('fixture state')
    if failure == 'destroy':
        handle.destroy.side_effect = RuntimeError('fixture destroy')
    if failure == 'undefine':
        handle.undefine.side_effect = RuntimeError('fixture undefine')
    domain._domain = handle
    overlay = tmp_path / 'owned-overlay'
    overlay.write_text('fixture')
    domain._overlay = overlay
    stop = Mock(side_effect=OSError('fixture stop') if failure == 'stop' else None)
    monkeypatch.setattr(domain, '_stop_external_virtiofsd', stop)
    if failure != 'none':
        with pytest.raises(d.IsolationError):
            domain.teardown()
    else:
        domain.teardown()
    handle.undefine.assert_called_once()
    stop.assert_called_once()
    assert domain._domain is None and not overlay.exists()


@pytest.mark.parametrize('running,timeout', [(False, False), (True, False), (True, True)])
def test_virtiofs_stop_reaps_fake_process_and_removes_only_owned_files(domain, tmp_path, running, timeout):
    process = Mock()
    process.poll.return_value = None if running else 0
    process.wait.side_effect = [subprocess.TimeoutExpired('fixture',3),0] if timeout else None
    domain._virtiofsd = process
    domain._owned_socket = True
    path = domain._spec.virtiofs_socket
    path.write_text('fixture socket placeholder')
    pid = Path(str(path)+'.pid')
    pid.write_text('fixture pid')
    untouched = tmp_path / 'unrelated'
    untouched.write_text('keep')
    domain._stop_external_virtiofsd()
    assert process.terminate.called is running
    assert process.kill.called is timeout
    assert process.wait.call_count == (2 if timeout else 1)
    assert not path.exists() and not pid.exists() and untouched.read_text() == 'keep'
    assert domain._virtiofsd is None and not domain._owned_socket
    domain._stop_external_virtiofsd()


@pytest.mark.parametrize('failure', ['exec', 'exit', 'timeout', 'regular-file'])
def test_failed_external_virtiofs_start_cleans_owned_process_without_launching_binary(domain, monkeypatch, failure):
    process = Mock(returncode=4)
    process.poll.return_value = 4 if failure == 'exit' else None
    process.wait.return_value = 0
    popen = Mock(side_effect=OSError('fixture exec') if failure == 'exec' else None, return_value=process)
    monkeypatch.setattr(d.subprocess, 'Popen', popen)
    if failure == 'regular-file':
        domain._spec.virtiofs_socket.write_text('keep')
    with pytest.raises(d.IsolationError) as error:
        domain._start_external_virtiofsd(domain._spec)
    assert error.value.reason == ('virtiofs_socket_exists' if failure == 'regular-file' else 'virtiofsd_start_failed')
    assert domain._virtiofsd is None
    if failure == 'regular-file':
        popen.assert_not_called()
        assert domain._spec.virtiofs_socket.read_text() == 'keep'
    elif failure != 'exec':
        process.wait.assert_called_once()


@pytest.mark.skipif(os.name!='posix',reason='Owned Unix socket creation and permission contract')
@pytest.mark.parametrize('acl_failure', [False, True])
def test_external_socket_acl_is_applied_to_owned_socket_only(domain, monkeypatch, acl_failure):
    owned = socket.socket(socket.AF_UNIX)
    process = Mock()
    process.poll.return_value = None
    process.wait.return_value = 0
    path = domain._spec.virtiofs_socket
    def spawn(argv, **kwargs):
        assert argv[0] == domain._spec.virtiofsd_path
        assert '--sandbox=namespace' in argv
        owned.bind(str(path))
        return process
    monkeypatch.setattr(d.subprocess, 'Popen', spawn)
    domain._runner.return_value = d.CommandResult(1,'','fixture acl') if acl_failure else d.CommandResult(0)
    try:
        if acl_failure:
            with pytest.raises(d.IsolationError,match='virtiofs_socket_acl_failed'):
                domain._start_external_virtiofsd(domain._spec)
        else:
            domain._start_external_virtiofsd(domain._spec)
            assert path.stat().st_mode & 0o777 == 0o666
            assert domain._virtiofsd is process
        domain._runner.assert_called_once_with(['setfacl','-m','u:libvirt-qemu:rw',str(path)])
    finally:
        domain._stop_external_virtiofsd()
        owned.close()
    assert not path.exists()
