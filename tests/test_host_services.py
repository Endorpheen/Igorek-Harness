import json
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from factory.core.host_service_policy import validate, validate_rpc
from factory.core.host_services import HostServices


@pytest.fixture
def manager(tmp_path):
    root = tmp_path / 'host-services'; root.mkdir(mode=0o700)
    output = tmp_path / 'outputs'; output.mkdir()
    cfg = {'version': 1, 'name': 'demo', 'label': 'Demo', 'unit': 'igorek-host-service-demo.service',
           'socket': str(root / 'api.sock'), 'token': 'fixture', 'outputs': str(output), 'output_extensions': ['.wav'],
           'tools': {'generate': {'input_schema': {'type': 'object', 'properties': {'seconds': {'type': 'integer', 'minimum': 1, 'maximum': 120}}, 'required': ['seconds']}}}}
    path = root / 'demo.json'; path.write_text(json.dumps(cfg)); path.chmod(0o600)
    runner = lambda argv, **kwargs: SimpleNamespace(returncode=0, stdout='ActiveState=inactive\nSubState=dead\nMainPID=0\n')
    return HostServices(root, allowed=lambda profile=None: profile in (None, 'demo'), runner=runner), cfg, tmp_path


@pytest.mark.parametrize('value', [{'seconds': True}, {'seconds': 121}, {'seconds': 0}, {'seconds': 1, 'command': 'id'}, {'seconds': 1, 'path': '/etc/shadow'}, {}])
def test_positive_argument_contract_denies_non_enrolled_arguments(manager, value):
    ctl, cfg, root = manager
    with pytest.raises(ValueError):
        ctl.execute({'action': 'invoke', 'service': 'demo', 'method': 'generate', 'arguments': value})


@pytest.mark.parametrize('message', [[], {'jsonrpc': '2.0', 'method': 'resources/read'}, {'jsonrpc': '2.0', 'method': 'tools/call', 'params': []}, {'jsonrpc': '2.0', 'method': 'tools/call', 'params': {'name': 'shell'}}, {'jsonrpc': '2.0', 'method': 'tools/call', 'params': {'name': ['generate']}}])
def test_server_enforces_the_same_positive_policy(manager, message):
    with pytest.raises(ValueError): validate_rpc(message, manager[1]['tools'])


def test_generic_invocation_profile_lock_and_schema(manager):
    ctl, cfg, root = manager
    with patch.object(ctl, 'rpc', return_value={'structuredContent': {'job_id': 'abc'}}) as rpc:
        assert ctl.execute({'action': 'invoke', 'service': 'demo', 'method': 'generate', 'arguments': {'seconds': 120}}, profile='demo') == {'job_id': 'abc'}
        rpc.assert_called_once()
    with pytest.raises(ValueError): ctl.execute({'action': 'list'}, profile='other')
    ctl.allowed = lambda profile=None: False
    with pytest.raises(ValueError): ctl.execute({'action': 'start', 'service': 'demo'})


def test_fixed_systemctl_argv_and_no_shell_or_inherited_secrets(manager):
    ctl, cfg, root = manager
    with patch.object(ctl, 'runner', return_value=SimpleNamespace(returncode=0, stdout='')) as runner:
        ctl.execute({'action': 'start', 'service': 'demo'})
        argv = runner.call_args_list[0].args[0]
        assert argv == ['/usr/bin/systemctl', '--user', 'start', 'igorek-host-service-demo.service']
        assert runner.call_args.kwargs['shell'] is False
        assert set(runner.call_args.kwargs['env']) == {'PATH', 'LANG', 'HOME', 'XDG_RUNTIME_DIR', 'DBUS_SESSION_BUS_ADDRESS'}
    with pytest.raises(ValueError): ctl.execute({'action': 'start', 'service': 'demo;id'})
    with pytest.raises(ValueError): ctl.execute({'action': 'start', 'service': 'demo', 'command': 'id'})


def test_configuration_permissions_and_symlinks(manager):
    ctl, cfg, root = manager
    path = ctl.root / 'demo.json'; path.chmod(0o644)
    with pytest.raises(ValueError): ctl.config('demo')
    path.unlink(); path.symlink_to(root / 'elsewhere')
    with pytest.raises((OSError, ValueError)): ctl.config('demo')


def test_files_export_only_configured_results_and_safe_destination(manager):
    ctl, cfg, root = manager
    source = Path(cfg['outputs']) / 'song.wav'; source.write_bytes(b'audio')
    (Path(cfg['outputs']) / 'secret.txt').write_text('hidden')
    (Path(cfg['outputs']) / 'alias.wav').symlink_to(source)
    rows = ctl.execute({'action': 'files', 'service': 'demo'})['files']; assert len(rows) == 1
    workspace = root / 'workspace'; workspace.mkdir()
    result = ctl.execute({'action': 'export', 'service': 'demo', 'file_id': rows[0]['id']}, workdir=str(workspace))
    assert Path(result['path']).read_bytes() == b'audio'
    assert Path(result['path']).is_relative_to(workspace)
    Path(result['path']).unlink(); (workspace / 'service_results').rmdir(); (workspace / 'service_results').symlink_to(Path(cfg['outputs']), target_is_directory=True)
    with pytest.raises(ValueError): ctl.export(cfg, rows[0]['id'], workspace)


@pytest.mark.parametrize('mode', ['workspace', 'user-read', 'full-dangerous'])
def test_private_service_control_protected_without_restricting_workspace(tmp_path, monkeypatch, mode):
    monkeypatch.setenv('IGOREK_FACTORY_HOME', str(tmp_path))
    from factory.core.cc_backend_tools import _validate_read_path, _validate_write_path
    for check in (_validate_read_path, _validate_write_path):
        with pytest.raises(ValueError, match='owner_service_configuration_protected'):
            check(str(tmp_path / 'host-services/demo.json'), str(tmp_path), mode)
        assert check(str(tmp_path / 'normal.txt'), str(tmp_path), mode) == str(tmp_path / 'normal.txt')


def test_bash_masks_control_after_workspace_binds(tmp_path, monkeypatch):
    private = tmp_path / 'host-services'; private.mkdir()
    monkeypatch.setenv('IGOREK_FACTORY_HOME', str(tmp_path))
    from factory.core.bash_scope_guard import wrap_with_sandbox_argv
    with patch('shutil.which', return_value='/usr/bin/bwrap'):
        for guest in (False, True):
            argv = wrap_with_sandbox_argv('true', workdir=str(tmp_path), guest_mode=guest)
            index = argv.index(str(private))
            assert argv[index-1] == '--tmpfs'
            assert index > argv.index('--bind')


def test_source_adapters_and_ui_have_no_machine_paths():
    for name in ('factory/core/host_services.py', 'desktop/host-service-runner.py', 'desktop/host-service-wangp.py', 'desktop/configure-host-service.py', 'factory/web_ui/static/plugins.js'):
        text = Path(name).read_text()
        assert '/run/media/' not in text and 'python3.11.11' not in text
    assert '--ro-bind\', \'/\'' not in Path('desktop/configure-host-service.py').read_text()
    assert "'--dev-bind', '/dev', '/dev'" not in Path('desktop/configure-host-service.py').read_text()


def test_auto_stop_never_interrupts_generation_and_ignores_status_polling():
    from factory.core.host_service_policy import IdleShutdown
    now = [0]
    idle = IdleShutdown(300, clock=lambda: now[0])
    now[0] = 299; assert not idle.expired(False)
    now[0] = 1000; assert not idle.expired(True)
    now[0] = 1299; assert not idle.expired(False)
    now[0] = 1300; assert idle.expired(False)
    idle.touch(); assert not idle.expired(False)


def test_idle_timeout_must_be_finite_owner_configured_range():
    from factory.core.host_service_policy import IdleShutdown
    for seconds in (0, -1, 3601, True, 30.0):
        with pytest.raises(ValueError): IdleShutdown(seconds)


def test_owner_ui_only_controls_enrolled_services_and_respects_lock(manager, tmp_path):
    import sys
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'desktop'))
    from vault_owner import OwnerVault
    owner = OwnerVault(tmp_path / 'owner')
    owner.service.create('test-password')
    owner.host_services = manager[0]
    try:
        assert owner.dispatch({'id': 'x', 'action': 'host_service', 'operation': 'list', 'service': ''})['result']['services'][0]['name'] == 'demo'
        assert 'error' in owner.dispatch({'id': 'x', 'action': 'host_service', 'operation': 'invoke', 'service': 'demo'})
        owner.interface_locked = True
        assert owner.dispatch({'id': 'x', 'action': 'host_service', 'operation': 'start', 'service': 'demo'})['error'] == 'interface_locked'
    finally:
        owner.close()


def test_recursive_search_does_not_disclose_service_configuration(tmp_path, monkeypatch):
    from factory.core.cc_backend_tools import _tool_grep
    monkeypatch.setenv('IGOREK_FACTORY_HOME', str(tmp_path))
    control = tmp_path / 'host-services'; control.mkdir()
    (control / 'demo.json').write_text('private-fixture-token')
    (tmp_path / 'normal.txt').write_text('ordinary-fixture-token')
    result = _tool_grep({'pattern': 'fixture-token'}, str(tmp_path), 'full-dangerous')
    assert 'ordinary-fixture-token' in result
    assert 'private-fixture-token' not in result


def test_bash_hides_user_bus_without_any_enrolled_host_service(tmp_path, monkeypatch):
    import os
    from factory.core import bash_scope_guard as scope
    from factory.core import host_services
    original = Path.is_dir
    bus = Path('/run/user') / str(os.getuid())
    monkeypatch.setattr(scope.shutil, 'which', lambda name: '/usr/bin/bwrap')
    monkeypatch.setattr(host_services, 'protected_paths', lambda env: [tmp_path / 'absent-control-root'])
    monkeypatch.setattr(Path, 'is_dir', lambda path: path == bus or original(path))
    argv = scope.wrap_with_sandbox_argv('true', workdir=str(tmp_path), env={}, guest_mode=False)
    assert ['--tmpfs', str(bus)] == argv[argv.index(str(bus)) - 1:argv.index(str(bus)) + 1]
    assert 'DBUS_SESSION_BUS_ADDRESS' in argv
    assert argv[argv.index('DBUS_SESSION_BUS_ADDRESS') - 1] == '--unsetenv'


def test_owner_enrollment_is_generic_and_uses_narrow_gpu_bindings(tmp_path, monkeypatch):
    import importlib.util
    import sys
    module_path = Path(__file__).resolve().parents[1] / 'desktop/configure-host-service.py'
    spec = importlib.util.spec_from_file_location('enroll_fixture', module_path)
    module = importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    app = tmp_path / 'app'; app.mkdir()
    adapter = tmp_path / 'adapter.py'; adapter.write_text('def build(config): return None\n')
    definition = tmp_path / 'definition.json'
    definition.write_text(json.dumps({'name': 'studio', 'label': 'Other app', 'adapter': str(adapter),
        'python': '/usr/bin/python3', 'app_root': str(app), 'python_paths': [str(app)],
        'outputs': str(tmp_path / 'outputs'), 'output_extensions': ['.wav'], 'tools': {},
        'environment': {'PATH': '/usr/bin:/bin'}, 'runtime_config_name': 'studio.json', 'devices': ['/dev/nvidia0']}))
    home = tmp_path / 'owner'; home.mkdir()
    monkeypatch.setattr(module.pwd, 'getpwuid', lambda uid: SimpleNamespace(pw_dir=str(home)))
    calls = []
    monkeypatch.setattr(module.subprocess, 'run', lambda argv, **kwargs: calls.append(argv))
    monkeypatch.setattr(sys, 'argv', ['configure', '--root', str(tmp_path / 'data'), '--definition', str(definition)])
    module.main()
    cfg = json.loads((tmp_path / 'data/host-services/studio.json').read_text())
    assert Path(cfg['runtime_config']).name == 'studio.json'
    assert Path(cfg['runtime_config']).stat().st_mode & 0o077 == 0
    unit = (home / '.config/systemd/user/igorek-host-service-studio.service').read_text()
    assert '"--dev-bind" "/dev/nvidia0" "/dev/nvidia0"' in unit
    assert '"--ro-bind" "/" "/"' not in unit
    assert '"--dev-bind" "/dev" "/dev"' not in unit
    assert '"--unshare-net"' in unit and 'NoNewPrivileges=yes' in unit
    assert calls == [['/usr/bin/systemctl', '--user', 'daemon-reload']]
