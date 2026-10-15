"""Capability-scoped MCP access to the existing owner-enrolled host services."""
import hmac
import json
import os
from pathlib import Path
import secrets
import socket
import socketserver
import stat
import threading
from http.server import BaseHTTPRequestHandler

from factory.core.host_services import SERVICE_PATTERN
from factory.core.tool_registry import registry


class HostServicesMcp(socketserver.ThreadingMixIn, socketserver.TCPServer if os.name == 'nt' else socketserver.UnixStreamServer):
    daemon_threads = True
    request_queue_size = 8

    def __init__(self, root, manager):
        from factory.core.platform_support import require_linux
        require_linux('opencode_host_services')
        self.manager = manager
        self.grant = None
        self.mutex = threading.RLock()
        self.slots = threading.BoundedSemaphore(8)
        root = Path(root)
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        info = root.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
            raise ValueError('opencode_mcp_private_directory_required')
        self.path = root / 'api.sock'
        if self.path.exists() or self.path.is_symlink():
            info = self.path.lstat()
            if not stat.S_ISSOCK(info.st_mode) or info.st_uid != os.getuid():
                raise ValueError('opencode_mcp_socket_exists')
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as probe:
                probe.settimeout(1)
                try:
                    probe.connect(str(self.path))
                except ConnectionRefusedError:
                    self.path.unlink()
                else:
                    raise ValueError('opencode_mcp_already_running')
        super().__init__(str(self.path), Handler)
        os.chmod(self.path, 0o600)
        info = self.path.stat()
        self.identity = (info.st_dev, info.st_ino)

    def process_request(self, request, address):
        if not self.slots.acquire(blocking=False):
            request.close()
            return
        try:
            super().process_request(request, address)
        except Exception:
            self.slots.release()
            raise

    def process_request_thread(self, request, address):
        try:
            super().process_request_thread(request, address)
        finally:
            self.slots.release()

    def configure(self, operation, value):
        if not isinstance(value, dict):
            raise ValueError('invalid_opencode_services')
        with self.mutex:
            if operation == 'revoke' and set(value) == {'token'} and isinstance(value['token'], str):
                if self.grant and hmac.compare_digest(self.grant['token'], value['token']):
                    self.grant = None
                return {'revoked': True}
            if operation != 'grant' or set(value) != {'services', 'projects'} or not self.manager.allowed(None):
                raise ValueError('host_service_access_denied')
            services, projects = value['services'], value['projects']
            if (not isinstance(services, list) or not 1 <= len(services) <= 8
                    or any(not isinstance(name, str) or not SERVICE_PATTERN.fullmatch(name) for name in services)
                    or not isinstance(projects, list) or not 1 <= len(projects) <= 16):
                raise ValueError('invalid_opencode_services')
            for name in services:
                self.manager.config(name)
            from factory.core.isolation.workspace import resolve_workspace_export
            approved = []
            for raw in projects:
                if not isinstance(raw, str) or not Path(raw).is_absolute() or not Path(raw).is_dir():
                    raise ValueError('invalid_opencode_workspace')
                approved.append(resolve_workspace_export(raw)['host_path'])
            self.grant = {'token': secrets.token_hex(32), 'services': list(dict.fromkeys(services)),
                          'projects': list(dict.fromkeys(approved))}
            return {'socket': str(self.path), 'token': self.grant['token']}

    def authorized(self, token):
        with self.mutex:
            return bool(self.grant and isinstance(token, str)
                        and hmac.compare_digest(token, self.grant['token']) and self.manager.allowed(None))

    def tool(self):
        original = registry.get_schema('host_service')
        if original is None:
            raise ValueError('host_service_unavailable')
        schema = json.loads(json.dumps(original['input_schema']))
        schema['properties']['service']['enum'] = self.grant['services']
        schema['properties']['workspace'] = {'type': 'string', 'enum': self.grant['projects']}
        return {'name': 'host_service', 'description': original['description'], 'inputSchema': schema}

    def dispatch(self, token, message):
        with self.mutex:
            if not self.authorized(token):
                raise ValueError('host_service_access_denied')
            if not isinstance(message, dict) or message.get('jsonrpc') != '2.0':
                raise ValueError('invalid_mcp_request')
            method = message.get('method')
            if method == 'initialize':
                return {'protocolVersion': '2024-11-05', 'capabilities': {'tools': {}},
                        'serverInfo': {'name': 'igorek-host-services', 'version': '1.0'}}
            if method == 'ping':
                return {}
            if method == 'notifications/initialized':
                return None
            if method == 'tools/list':
                return {'tools': [self.tool()]}
            if method != 'tools/call':
                raise ValueError('mcp_method_not_allowed')
            params = message.get('params')
            if (not isinstance(params, dict) or set(params) - {'name', 'arguments', '_meta'}
                    or params.get('name') != 'host_service' or not isinstance(params.get('arguments'), dict)):
                raise ValueError('mcp_tool_not_allowed')
            fields = dict(params['arguments'])
            workspace = fields.pop('workspace', self.grant['projects'][0])
            if workspace not in self.grant['projects']:
                raise ValueError('opencode_workspace_not_granted')
            names = self.grant['services'][:]
            listing = fields.get('action') == 'list' and set(fields) == {'action'}
            if not listing and fields.get('service') not in names:
                raise ValueError('opencode_service_not_granted')
        if not self.authorized(token):
            raise ValueError('host_service_access_denied')
        if listing:
            rows = []
            for name in names:
                if not self.authorized(token):
                    raise ValueError('host_service_access_denied')
                rows.append(self.manager.execute({'action': 'status', 'service': name}))
            result = {'services': rows}
        else:
            result = self.manager.execute(fields, workdir=workspace)
        return {'content': [{'type': 'text', 'text': json.dumps(result, ensure_ascii=False)}],
                'structuredContent': result, 'isError': bool(result.get('error'))}

    def server_close(self):
        with self.mutex:
            self.grant = None
        super().server_close()
        try:
            info = self.path.lstat()
            if (info.st_dev, info.st_ino) == self.identity:
                self.path.unlink()
        except FileNotFoundError:
            pass


class Handler(BaseHTTPRequestHandler):
    def setup(self):
        self.request.settimeout(15)
        super().setup()

    def log_message(self, *_):
        pass

    def do_POST(self):
        self.connection.settimeout(15)
        token = self.headers.get('Authorization', '').removeprefix('Bearer ')
        if self.path != '/mcp' or not self.server.authorized(token):
            self.send_error(403)
            return
        try:
            length = int(self.headers.get('Content-Length', '-1'))
            if not 0 <= length <= 65536 or self.headers.get('Transfer-Encoding'):
                raise ValueError('invalid_mcp_size')
            message = json.loads(self.rfile.read(length))
            identity = message.get('id') if isinstance(message, dict) else None
            result = self.server.dispatch(token, message)
            if identity is None:
                self.send_response(202)
                self.end_headers()
                return
            payload = {'jsonrpc': '2.0', 'id': identity, 'result': result}
        except Exception as error:
            payload = {'jsonrpc': '2.0', 'id': locals().get('identity'),
                       'error': {'code': -32602, 'message': str(error) if isinstance(error, ValueError) else 'host_service_unavailable'}}
        raw = json.dumps(payload, ensure_ascii=False).encode()
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)
