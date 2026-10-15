"""Owner-enrolled local MCP services; no agent-supplied command or URL."""
from __future__ import annotations

import http.client
import json
import os
from pathlib import Path
import re
import socket
import stat
import subprocess
import secrets
import threading

from .host_service_policy import validate
from .runtime import get_data_root
from .tool_registry import registry, tool_error, tool_result

SERVICE_PATTERN = re.compile(r'[a-z][a-z0-9_-]{0,39}')
MAX_RPC_BYTES = 2 * 1024 * 1024


def _owner_home():
    if os.name == 'nt':
        return Path.home()
    import pwd
    return Path(pwd.getpwuid(os.getuid()).pw_dir)


def control_root():
    return Path(get_data_root()) / 'host-services'


def protected_paths(env=None):
    env = os.environ if env is None else env
    configured = env.get('IGOREK_FACTORY_HOME') or env.get('FACTORY_DATA_DIR')
    root = Path(configured).expanduser().resolve() / 'host-services' if configured else control_root().resolve()
    home = _owner_home()
    units = home / '.config/systemd/user'
    return [root, *units.glob('igorek-host-service-*.service'), *units.glob('igorek-host-service-*.service.d')]


def deny_control_path(path):
    target = Path(path).resolve()
    units = _owner_home() / '.config/systemd/user'
    if target.is_relative_to(units) and target.relative_to(units).parts[0].startswith('igorek-host-service-'):
        raise ValueError('owner_service_configuration_protected')
    if any(target == item.resolve() or target.is_relative_to(item.resolve()) for item in protected_paths()):
        raise ValueError('owner_service_configuration_protected')


class UnixHTTPConnection(http.client.HTTPConnection):
    def __init__(self, path, timeout=30):
        super().__init__('127.0.0.1', timeout=timeout)
        self.path = path

    def connect(self):
        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.settimeout(self.timeout)
        self.sock.connect(self.path)


class HostServices:
    def __init__(self, root=None, allowed=None, runner=None):
        self.root = Path(root) if root else control_root()
        self.allowed = allowed or (lambda profile=None: False)
        self.runner = runner or subprocess.run
        self.lock = threading.RLock()

    def names(self):
        if not self.root.is_dir():
            return []
        return [path.stem for path in sorted(self.root.glob('*.json')) if SERVICE_PATTERN.fullmatch(path.stem)]

    def config(self, name):
        from .platform_support import require_linux
        require_linux('systemd_host_services')
        if not isinstance(name, str) or not SERVICE_PATTERN.fullmatch(name):
            raise ValueError('invalid_service_name')
        root_info = self.root.lstat()
        if not stat.S_ISDIR(root_info.st_mode) or root_info.st_uid != os.getuid() or root_info.st_mode & 0o077:
            raise ValueError('invalid_service_configuration')
        path = self.root / (name + '.json')
        info = path.lstat()
        if not stat.S_ISREG(info.st_mode) or stat.S_ISLNK(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077 or info.st_size > 256 * 1024:
            raise ValueError('invalid_service_configuration')
        value = json.loads(path.read_text())
        if not isinstance(value, dict) or value.get('version') != 1 or value.get('name') != name or value.get('unit') != 'igorek-host-service-' + name + '.service':
            raise ValueError('invalid_service_configuration')
        if not Path(value['socket']).resolve().is_relative_to(self.root.resolve()) or not isinstance(value.get('tools'), dict):
            raise ValueError('invalid_service_configuration')
        return value

    def systemctl(self, config, operation):
        from .platform_support import require_linux
        require_linux('systemd_host_services')
        if operation not in ('show', 'start', 'stop'):
            raise ValueError('invalid_service_operation')
        uid = os.getuid()
        env = {'PATH': '/usr/bin:/bin', 'LANG': 'C.UTF-8', 'HOME': str(_owner_home()),
               'XDG_RUNTIME_DIR': '/run/user/' + str(uid),
               'DBUS_SESSION_BUS_ADDRESS': 'unix:path=/run/user/' + str(uid) + '/bus'}
        argv = ['/usr/bin/systemctl', '--user', operation, config['unit']]
        if operation == 'show':
            argv += ['--property=ActiveState,SubState,MainPID']
        result = self.runner(argv, shell=False, env=env, capture_output=True, text=True, timeout=30)
        if result.returncode:
            raise ValueError('service_control_failed')
        return dict(line.split('=', 1) for line in result.stdout.splitlines() if '=' in line)

    def rpc(self, config, method, params=None):
        body = json.dumps({'jsonrpc': '2.0', 'id': 'igorek', 'method': method, 'params': params or {}}).encode()
        if len(body) > 1024 * 1024:
            raise ValueError('service_request_too_large')
        connection = UnixHTTPConnection(config['socket'])
        try:
            connection.request('POST', '/mcp', body, {'Authorization': 'Bearer ' + config['token'],
                'Host': '127.0.0.1:8000', 'Accept': 'application/json, text/event-stream', 'Content-Type': 'application/json',
                'MCP-Protocol-Version': '2024-11-05'})
            response = connection.getresponse()
            raw = response.read(MAX_RPC_BYTES + 1)
            if response.status != 200 or len(raw) > MAX_RPC_BYTES:
                raise ValueError('service_api_unavailable')
            value = json.loads(raw)
            if not isinstance(value, dict) or value.get('error'):
                raise ValueError('service_api_error')
            return value.get('result', {})
        finally:
            connection.close()

    def status(self, name):
        cfg = self.config(name)
        state = self.systemctl(cfg, 'show')
        ready = False
        if state.get('ActiveState') == 'active':
            try:
                self.rpc(cfg, 'ping')
                ready = True
            except (OSError, ValueError, http.client.HTTPException):
                pass
        return {'name': name, 'label': cfg.get('label', name), 'state': state.get('ActiveState', 'unknown'),
                'substate': state.get('SubState', ''), 'pid': int(state.get('MainPID') or 0), 'ready': ready, 'idle_seconds': cfg.get('idle_seconds', 300)}

    def files(self, config):
        from .mobile_bridge.files import MAX_DOWNLOAD_BYTES, describe_file
        base = Path(config['outputs']).resolve()
        rows = []
        if not base.is_dir():
            return rows
        for folder, directories, names in os.walk(base, followlinks=False):
            directories[:] = [name for name in directories if not name.startswith('.') and not (Path(folder) / name).is_symlink()]
            for name in names:
                path = Path(folder) / name
                if name.startswith('.') or path.is_symlink() or path.suffix.lower() not in config.get('output_extensions', []):
                    continue
                try:
                    info = describe_file(path)
                    if info['size'] <= MAX_DOWNLOAD_BYTES:
                        rows.append((info, path))
                except OSError:
                    pass
        return sorted(rows, key=lambda item: item[0]['modified_at'], reverse=True)[:100]

    def export(self, config, identity, workdir):
        from .mobile_bridge.files import open_file_safely
        if not isinstance(identity, str) or not re.fullmatch('[a-f0-9]{64}', identity):
            raise ValueError('invalid_result_id')
        found = next((item for item in self.files(config) if item[0]['id'] == identity), None)
        if found is None:
            raise ValueError('service_result_unavailable')
        info, source = found
        base = Path(workdir).resolve()
        destination = base / 'service_results'
        if destination.is_symlink():
            raise ValueError('invalid_export_directory')
        destination.mkdir(exist_ok=True)
        if destination.resolve().parent != base:
            raise ValueError('invalid_export_directory')
        target_name = identity[:16] + '-' + source.name
        target = destination / target_name
        directory = os.open(destination, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        temporary = '.export-' + secrets.token_hex(16)
        try:
            with open_file_safely(source, info) as incoming:
                descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600, dir_fd=directory)
                count = 0
                with os.fdopen(descriptor, 'wb') as outgoing:
                    while chunk := incoming.read(65536):
                        count += len(chunk)
                        if count > info['size']:
                            raise ValueError('service_result_changed')
                        outgoing.write(chunk)
                if count != info['size']:
                    raise ValueError('service_result_changed')
                os.replace(temporary, target_name, src_dir_fd=directory, dst_dir_fd=directory)
        finally:
            try:
                os.unlink(temporary, dir_fd=directory)
            except FileNotFoundError:
                pass
            os.close(directory)
        return {'name': info['name'], 'path': str(target), 'bytes': info['size']}

    def execute(self, fields, *, profile=None, workdir=None):
        if not self.allowed(profile):
            raise ValueError('host_service_access_denied')
        if not isinstance(fields, dict) or set(fields) - {'action', 'service', 'method', 'arguments', 'file_id'}:
            raise ValueError('invalid_service_fields')
        action = fields.get('action')
        if action == 'list':
            if set(fields) != {'action'}:
                raise ValueError('invalid_service_fields')
            rows = []
            for name in self.names():
                try:
                    rows.append(self.status(name))
                except (ValueError, OSError, subprocess.SubprocessError):
                    rows.append({'name': name, 'label': name, 'state': 'unavailable', 'ready': False})
            return {'services': rows}
        cfg = self.config(fields.get('service'))
        if action in ('status', 'start', 'stop', 'describe', 'files'):
            if set(fields) != {'action', 'service'}:
                raise ValueError('invalid_service_fields')
            if action in ('start', 'stop'):
                self.systemctl(cfg, action)
            if action == 'describe':
                return {'service': cfg['name'], 'tools': cfg['tools']}
            if action == 'files':
                return {'files': [info for info, path in self.files(cfg)]}
            return self.status(cfg['name'])
        if action == 'invoke':
            if set(fields) - {'action', 'service', 'method', 'arguments'}:
                raise ValueError('invalid_service_fields')
            method = fields.get('method')
            if not isinstance(method, str) or method not in cfg['tools']:
                raise ValueError('service_method_not_allowed')
            arguments = fields.get('arguments', {})
            validate(cfg['tools'][method]['input_schema'], arguments)
            with self.lock:
                reply = self.rpc(cfg, 'tools/call', {'name': method, 'arguments': arguments})
            if reply.get('isError'):
                return {'error': 'service_call_failed', 'details': reply.get('content', [])}
            if isinstance(reply.get('structuredContent'), dict):
                return reply['structuredContent']
            content = reply.get('content') or []
            if len(content) == 1 and content[0].get('type') == 'text':
                try:
                    return json.loads(content[0]['text'])
                except ValueError:
                    pass
            return {'content': content}
        if action == 'export':
            if set(fields) != {'action', 'service', 'file_id'} or not workdir:
                raise ValueError('invalid_service_fields')
            return self.export(cfg, fields['file_id'], workdir)
        raise ValueError('invalid_service_action')

    def install(self):
        def handle(fields, **context):
            try:
                return tool_result(self.execute(fields, profile=context.get('profile'), workdir=context.get('workdir')))
            except (ValueError, OSError, subprocess.SubprocessError, http.client.HTTPException) as error:
                return tool_error(str(error) if isinstance(error, ValueError) else 'host_service_unavailable')
        registry.register(name='host_service', toolset='host_services',
            schema={'name': 'host_service', 'description': 'Управление только одобренными локальными сервисами хоста. list показывает сервисы; start/stop/status управляют ими; describe возвращает разрешённые методы и точные схемы; invoke вызывает метод без shell/URL; files показывает результаты, export копирует file_id в текущую рабочую папку для отправки пользователю. GPU хоста остаётся только у сервиса, не у Bash.',
            'input_schema': {'type': 'object', 'properties': {
                'action': {'type': 'string', 'enum': ['list', 'start', 'stop', 'status', 'describe', 'invoke', 'files', 'export']},
                'service': {'type': 'string'}, 'method': {'type': 'string'},
                'arguments': {'type': 'object'}, 'file_id': {'type': 'string'},
            }, 'required': ['action'], 'additionalProperties': False}},
            handler=handle, check_fn=lambda: os.name != 'nt' and self.allowed(None) and bool(self.names()),
            description='Operate owner-enrolled local host services')
