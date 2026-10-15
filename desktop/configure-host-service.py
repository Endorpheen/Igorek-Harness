"""Enroll a local service from an owner-supplied definition, never from tool arguments."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import pwd
import re
import secrets
import shutil
import subprocess


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--definition', type=Path, required=True)
    args = parser.parse_args()
    cfg = json.loads(args.definition.read_text())
    name = cfg['name']
    if not re.fullmatch('[a-z][a-z0-9_-]{0,39}', name):
        raise ValueError('invalid_service_name')
    root = args.root.resolve() / 'host-services'
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(root, 0o700)
    source = Path(__file__).resolve().parent
    policy = source / 'packages/factory/core/host_service_policy.py'
    if not policy.is_file():
        policy = source.parent / 'factory/core/host_service_policy.py'
    for path, target in [(policy, 'policy.py'), (source / 'host-service-runner.py', 'runner.py'), (Path(cfg.pop('adapter')), name + '-adapter.py')]:
        shutil.copyfile(path, root / target)
        os.chmod(root / target, 0o600)
    private = root / name
    for directory in (private, private / 'socket', private / 'cache', private / 'cache/home', private / 'runtime', Path(cfg['outputs'])):
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    cfg.update(version=1, unit='igorek-host-service-' + name + '.service',
               socket=str(private / 'socket/api.sock'), bootstrap=str(root / (name + '-adapter.py')),
               bootstrap_sha256=hashlib.sha256((root / (name + '-adapter.py')).read_bytes()).hexdigest(), token=secrets.token_hex(32))
    config_name = cfg.pop('runtime_config_name', 'runtime.json')
    if not re.fullmatch('[a-zA-Z0-9_.-]{1,80}', config_name) or config_name.startswith('.'):
        raise ValueError('invalid_runtime_config_name')
    cfg['runtime_config'] = str(private / 'runtime' / config_name)
    runtime_source = cfg.pop('runtime_config_source', None)
    runtime = Path(runtime_source).read_text() if runtime_source else '{}'
    (private / 'runtime' / config_name).write_text(runtime)
    os.chmod(private / 'runtime' / config_name, 0o600)
    cfg['environment']['NUMBA_CACHE_DIR'] = str(private / 'cache/numba')
    cfg['environment']['HOME'] = str(private / 'cache/home')
    cfg['environment']['TRITON_CACHE_DIR'] = str(private / 'cache/triton')
    cfg['environment']['XDG_CACHE_HOME'] = str(private / 'cache')
    cfg['environment']['PYTHONDONTWRITEBYTECODE'] = '1'
    destination = root / (name + '.json')
    destination.write_text(json.dumps(cfg, indent=2, ensure_ascii=False))
    os.chmod(destination, 0o600)
    def quoted(path):
        value = str(path)
        if any(char in value for char in '\n\r\x00'):
            raise ValueError('invalid_service_path')
        return '"' + value.replace('\\', '\\\\').replace('"', '\\"').replace('%', '%%') + '"'
    readable = [root / 'runner.py', root / 'policy.py', Path(cfg['bootstrap']), destination, Path(cfg['python']).parent.parent, Path(cfg['app_root']), *map(Path, cfg['python_paths'])]
    writable = [private / 'socket', private / 'cache', private / 'runtime', Path(cfg['outputs']), *map(Path, cfg.get('writable_paths', []))]
    argv = ['/usr/bin/bwrap', '--new-session', '--unshare-net', '--unshare-pid', '--unshare-ipc', '--unshare-uts',
            '--cap-drop', 'ALL', '--ro-bind', '/usr', '/usr', '--symlink', 'usr/bin', '/bin',
            '--symlink', 'usr/sbin', '/sbin', '--symlink', 'usr/lib', '/lib', '--symlink', 'usr/lib64', '/lib64', '--dev', '/dev',
            '--proc', '/proc', '--tmpfs', '/tmp', '--ro-bind', '/sys', '/sys']
    for path in ('/etc/ld.so.cache', '/etc/passwd', '/etc/group', '/etc/fonts', '/etc/localtime'):
        if Path(path).exists():
            argv += ['--ro-bind', path, path]
    for device in cfg.get('devices', []):
        if not re.fullmatch('/dev/(nvidia[0-9]+|nvidiactl|nvidia-uvm|nvidia-uvm-tools|dri/renderD[0-9]+)', device):
            raise ValueError('invalid_gpu_device')
        argv += ['--dev-bind', device, device]
    for path in dict.fromkeys(map(str, readable)):
        argv += ['--ro-bind', path, path]
    for path in dict.fromkeys(map(str, writable)):
        Path(path).mkdir(parents=True, exist_ok=True, mode=0o700)
        argv += ['--bind', path, path]
    argv += ['--chdir', cfg['app_root'], '--', cfg['python'], '-I', '-S', str(root / 'runner.py'), str(destination)]
    unit = '\n'.join([
        '[Unit]', 'Description=Igorek local service ' + name, 'StartLimitIntervalSec=120', 'StartLimitBurst=2',
        '[Service]', 'Type=simple', 'ExecStart=' + ' '.join(map(quoted, argv)),
        'Environment=PYTHONDONTWRITEBYTECODE=1', 'UMask=0077', 'NoNewPrivileges=yes',
        'MemoryMax=24G', 'TasksMax=256', 'Restart=on-failure', 'RestartSec=10',
        'TimeoutStopSec=30', 'KillMode=control-group', 'StandardOutput=journal', 'StandardError=journal', '',
    ])
    home = Path(pwd.getpwuid(os.getuid()).pw_dir)
    units = home / '.config/systemd/user'
    units.mkdir(parents=True, exist_ok=True)
    (units / cfg['unit']).write_text(unit)
    uid = str(os.getuid())
    env = {'PATH': '/usr/bin:/bin', 'HOME': str(home), 'XDG_RUNTIME_DIR': '/run/user/' + uid,
           'DBUS_SESSION_BUS_ADDRESS': 'unix:path=/run/user/' + uid + '/bus'}
    subprocess.run(['/usr/bin/systemctl', '--user', 'daemon-reload'], env=env, check=True)
    print(json.dumps({'service': name, 'configured': True, 'root': str(root)}, ensure_ascii=False))


if __name__ == '__main__':
    main()
