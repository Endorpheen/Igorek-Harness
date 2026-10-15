import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'desktop'))
from execution_mode import DesktopExecution
from vault_owner import OwnerVault
from factory.core.isolation.errors import IsolationError


def prepared_desktop_image(root, disk_bytes=20 * 1024**3):
    from dataclasses import replace
    from factory.core.isolation.base_image import build_manifest, ImageBuildInfo, write_manifest
    image = root / 'isolation/images/base.qcow2'
    image.parent.mkdir(parents=True)
    import struct
    header = bytearray(104); header[:4] = b'QFI\xfb'
    struct.pack_into('>I', header, 4, 3); struct.pack_into('>Q', header, 24, disk_bytes)
    image.write_bytes(header)
    write_manifest(replace(build_manifest(image, ImageBuildInfo(tool_version='0.1.819')), vsock_port=40519), image)


def test_mode_persists_without_changing_global_environment(tmp_path, monkeypatch):
    monkeypatch.setenv('IGOREK_ISOLATION_MODE', 'off')
    mode = DesktopExecution(tmp_path)
    assert mode.status() == {'mode': 'off'}
    assert mode.create_manager(None, None, None) is None
    mode.select('kvm')
    assert DesktopExecution(tmp_path).mode == 'kvm'
    import os
    assert os.environ['IGOREK_ISOLATION_MODE'] == 'off'
    with pytest.raises(IsolationError): mode.create_manager(None, None, None)
    assert mode.mode == 'kvm'
    mode.select('off')
    assert DesktopExecution(tmp_path).mode == 'off'


@pytest.mark.parametrize('content', ['broken', '{"schema_version":true,"mode":"off"}',
    '{"schema_version":1,"mode":"other"}', '{"schema_version":1,"mode":"kvm","extra":1}'])
def test_invalid_saved_mode_never_defaults_to_host_execution(tmp_path, content):
    (tmp_path / 'desktop-execution.json').write_text(content)
    with pytest.raises(ValueError): DesktopExecution(tmp_path)


def test_execution_mode_is_owner_only_unlocked_idle_and_strict(tmp_path):
    owner = OwnerVault(tmp_path)
    def call(action, **fields): return owner.dispatch({'id':'mode', 'action':action, **fields})
    assert call('execution_mode')['result']['execution']['mode'] == 'off'
    assert call('select_execution_mode', mode='kvm')['error'] == 'vault_locked'
    call('create', password='fixture-password')
    assert call('select_execution_mode', mode='invalid')['error'] == 'invalid_request'
    owner.agent = SimpleNamespace(status=lambda: {'running':True})
    assert call('select_execution_mode', mode='kvm')['error'] == 'stop_agent_first'
    owner.agent = None
    owner.image._update({'phase':'copying', 'running':True})
    assert call('select_execution_mode', mode='kvm')['error'] == 'image_busy'
    owner.image._update({'phase':'idle', 'running':False})
    assert call('select_execution_mode', mode='kvm')['result']['execution']['mode'] == 'kvm'
    assert call('select_execution_mode', mode='off', path='/tmp')['error'] == 'invalid_request'
    assert owner.execution.mode == 'kvm'
    owner.close()


def test_terminal_owner_can_be_closed_and_restarted_without_stale_socket(tmp_path, monkeypatch):
    from factory.core.isolation import terminal_owner
    directory = tmp_path / 'owner'; directory.mkdir(mode=0o700)
    path = directory / 'fixture.sock'
    monkeypatch.setattr(terminal_owner, 'owner_path', lambda _: path)
    first = terminal_owner.TerminalOwnerServer('fixture', object())
    assert path.exists()
    first.close()
    assert not path.exists() and not first._thread.is_alive()
    second = terminal_owner.TerminalOwnerServer('fixture', object())
    first.close()
    assert path.exists()
    second.close()


def test_manager_binds_only_desktop_image_and_vault_proxy(tmp_path, monkeypatch):
    from factory.core.isolation import config, manager
    from factory.core import worker
    from vault_egress import DesktopVaultEgress
    prepared_desktop_image(tmp_path)
    monkeypatch.setattr('qemu_access.prepare_access', lambda root: None)
    monkeypatch.setattr('qemu_access.verify_socket_policy', lambda root: None)
    owner = OwnerVault(tmp_path)
    owner.service.create('fixture-password')
    profile = owner.providers.save('fixture', 'fixture', 'https://fixture.invalid/v1', 'openai', 'test-only-key')
    owner.service.check_provider_profile(profile['credential_id'], lambda *_: {'status':'verified','reason':'ok'})
    owner.execution.select('kvm')
    captured = []
    host_ops = object()
    monkeypatch.setattr(worker, '_get_host_op_runtime', lambda: host_ops)
    monkeypatch.setattr(config.IsolationConfig, 'from_env', lambda *args, **kwargs: SimpleNamespace(enabled=True))
    owned_manager = SimpleNamespace(channel_server=SimpleNamespace(reserve_listener=lambda: None))
    monkeypatch.setattr(manager.IsolationManager, 'from_env', lambda env, *, seams: captured.append((env, seams)) or owned_manager)
    monkeypatch.setenv('IGOREK_ISOLATION_BASE_IMAGE', '/production/base.qcow2')
    assert owner.execution.create_manager(owner.lifecycle, profile['credential_id'], profile['config']) is owned_manager
    env, seams = captured[0]
    assert env['IGOREK_ISOLATION_BASE_IMAGE'] == str(tmp_path / 'isolation/images/base.qcow2')
    assert env['IGOREK_ISOLATION_OVERLAY_DIR'] == str(tmp_path / 'isolation/overlays')
    assert env['IGOREK_ISOLATION_LIBVIRT_URI'] == 'qemu:///system'
    assert env['IGOREK_ISOLATION_VSOCK_PORT'] == '40519'
    assert env['IGOREK_ISOLATION_SESSION_DISK_GB'] == '20'
    assert seams.host_ops is host_ops and isinstance(seams.provider_secrets, DesktopVaultEgress)
    assert seams.provider_secrets._token() == 'test-only-key'
    assert 'test-only-key' not in str(env)
    seams.provider_secrets.close(); owner.close()


def test_live_bridge_status_does_not_cache_host_terminal_route(tmp_path):
    from factory.core.mobile_bridge.service import MobileService
    from agent_runtime import DesktopAgentRuntime
    bridge = MobileService.__new__(MobileService)
    bridge._profile_backend = lambda profile: 'cc-backend'
    execution = DesktopExecution(tmp_path)
    runtime = DesktopAgentRuntime(tmp_path, None, bridge, execution=execution)
    assert bridge._isolation_status_value('fixture') == 'none'
    execution.select('kvm')
    assert bridge._isolation_status_value('fixture') == 'kvm-unavailable'
    runtime.isolation_manager = object()
    runtime.available_profiles = lambda: ['fixture']
    assert bridge._isolation_status_value('fixture') == 'kvm'
    runtime.isolation_manager = None
    assert bridge._isolation_status_value('fixture') == 'kvm-unavailable'
    execution.select('off')
    assert bridge._isolation_status_value('fixture') == 'none'


def test_busy_guest_channel_closes_manager_without_host_fallback(tmp_path, monkeypatch):
    from factory.core.isolation import config, manager
    from factory.core import worker
    from unittest.mock import Mock
    from factory.core.isolation.channel import ChannelError
    prepared_desktop_image(tmp_path)
    monkeypatch.setattr('qemu_access.prepare_access', lambda root: None)
    monkeypatch.setattr('qemu_access.verify_socket_policy', lambda root: None)
    execution = DesktopExecution(tmp_path)
    execution.select('kvm')
    owned = Mock()
    owned.channel_server.reserve_listener.side_effect = ChannelError('vsock_bind_failed')
    proxy = Mock()
    monkeypatch.setattr(config.IsolationConfig, 'from_env', lambda *a, **k: SimpleNamespace(enabled=True))
    monkeypatch.setattr(manager.IsolationManager, 'from_env', lambda *a, **k: owned)
    monkeypatch.setattr(worker, '_get_host_op_runtime', lambda: object())
    monkeypatch.setattr('vault_egress.DesktopVaultEgress', lambda *a: proxy)
    with pytest.raises(IsolationError, match='desktop_channel_unavailable'):
        execution.create_manager(None, 'fixture', {})
    owned.close.assert_called_once()
    proxy.close.assert_called_once()
    assert execution.mode == 'kvm'


@pytest.mark.parametrize('port', [5005, 43001, True, '40519'])
def test_desktop_refuses_wrong_or_invalid_guest_channel_before_manager(tmp_path, port):
    import json
    prepared_desktop_image(tmp_path)
    path = tmp_path / 'isolation/images/base.qcow2.manifest.json'
    payload = json.loads(path.read_text()); payload['vsock_port'] = port
    path.write_text(json.dumps(payload))
    mode = DesktopExecution(tmp_path); mode.select('kvm')
    with pytest.raises(IsolationError): mode.create_manager(None, None, None)
    assert mode.mode == 'kvm'


def test_socket_policy_refusal_precedes_acl_proxy_manager_and_never_falls_back(tmp_path, monkeypatch):
    from factory.core.isolation.config import IsolationConfig
    prepared_desktop_image(tmp_path)
    monkeypatch.setattr(IsolationConfig, 'from_env', lambda *args, **kwargs: SimpleNamespace(enabled=True))
    def refuse(root): raise IsolationError('desktop_socket_policy_missing')
    def forbidden(*args, **kwargs): raise AssertionError('Execution side effect after refusal')
    monkeypatch.setattr('qemu_access.verify_socket_policy', refuse)
    monkeypatch.setattr('qemu_access.prepare_access', forbidden)
    monkeypatch.setattr('vault_egress.DesktopVaultEgress', forbidden)
    mode = DesktopExecution(tmp_path); mode.select('kvm')
    with pytest.raises(IsolationError, match='desktop_socket_policy_missing'):
        mode.create_manager(None, None, None)
    assert mode.mode == 'kvm'


@pytest.mark.parametrize('size,expected', [(2 * 1024**3, '8'), (20 * 1024**3, '20'), (20 * 1024**3 + 1, '21')])
def test_desktop_disk_cap_covers_virtual_image_without_inheriting_host_limit(tmp_path, monkeypatch, size, expected):
    from factory.core.isolation.config import IsolationConfig
    prepared_desktop_image(tmp_path, size)
    monkeypatch.setenv('IGOREK_ISOLATION_SESSION_DISK_GB', '1')
    captured = []
    def config(env, **kwargs):
        captured.append(env.copy())
        return SimpleNamespace(enabled=False, reason='test-stop-before-side-effects')
    monkeypatch.setattr(IsolationConfig, 'from_env', config)
    mode = DesktopExecution(tmp_path); mode.select('kvm')
    with pytest.raises(IsolationError, match='test-stop-before-side-effects'):
        mode.create_manager(None, None, None)
    assert captured[0]['IGOREK_ISOLATION_SESSION_DISK_GB'] == expected


@pytest.mark.parametrize('content', [b'not qcow2', bytes(104), None])
def test_invalid_or_zero_disk_size_refuses_before_config_and_side_effects(tmp_path, monkeypatch, content):
    from factory.core.isolation.config import IsolationConfig
    prepared_desktop_image(tmp_path, 0)
    if content is not None:
        (tmp_path / 'isolation/images/base.qcow2').write_bytes(content)
    def forbidden(*args, **kwargs): raise AssertionError('unexpected side effect')
    monkeypatch.setattr(IsolationConfig, 'from_env', forbidden)
    monkeypatch.setattr('qemu_access.prepare_access', forbidden)
    mode = DesktopExecution(tmp_path); mode.select('kvm')
    with pytest.raises(IsolationError, match='desktop_image_disk_size'):
        mode.create_manager(None, None, None)
    assert mode.mode == 'kvm'
