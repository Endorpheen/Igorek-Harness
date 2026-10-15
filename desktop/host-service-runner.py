"""Run an owner-enrolled MCP adapter on a private Unix socket (Python 3.11+)."""
import asyncio
import hashlib
import hmac
import importlib.util
import json
import os
from pathlib import Path
import socket
import sys
import threading
import time


def load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main():
    control = Path(sys.argv[1]).resolve()
    config = json.loads(control.read_text())
    os.environ.clear()
    os.environ.update(config['environment'])
    os.environ['PYTHONUNBUFFERED'] = '1'
    os.environ['PYTHONNOUSERSITE'] = '1'
    os.chdir(config['app_root'])
    for item in reversed(config['python_paths']):
        sys.path.insert(0, item)
    policy = load(control.parent / 'policy.py', 'igorek_service_policy')
    bootstrap = Path(config['bootstrap'])
    if hashlib.sha256(bootstrap.read_bytes()).hexdigest() != config['bootstrap_sha256']:
        raise RuntimeError('service_adapter_changed')
    adapter = load(bootstrap, 'igorek_host_service_adapter')
    built = adapter.build(config)
    app = built['server'].streamable_http_app()
    busy = built['busy']
    idle = policy.IdleShutdown(config.get('idle_seconds', 300))
    in_flight = 0

    async def guarded(scope, receive, send):
        if scope['type'] != 'http':
            return await app(scope, receive, send)
        headers = dict(scope.get('headers', []))
        if not hmac.compare_digest(headers.get(b'authorization', b''), ('Bearer ' + config['token']).encode()):
            await send({'type': 'http.response.start', 'status': 401, 'headers': []})
            return await send({'type': 'http.response.body', 'body': b'Unauthorized'})
        if scope.get('path') not in ('/mcp', '/mcp/') or scope.get('method') != 'POST':
            await send({'type': 'http.response.start', 'status': 403, 'headers': []})
            return await send({'type': 'http.response.body', 'body': b'Forbidden'})
        body = b''
        while True:
            frame = await receive()
            if frame['type'] == 'http.disconnect':
                return
            body += frame.get('body', b'')
            if len(body) > 1024 * 1024:
                await send({'type': 'http.response.start', 'status': 413, 'headers': []})
                return await send({'type': 'http.response.body', 'body': b'Request too large'})
            if not frame.get('more_body'):
                break
        try:
            message = json.loads(body)
            policy.validate_rpc(message, config['tools'])
        except (ValueError, TypeError, KeyError):
            await send({'type': 'http.response.start', 'status': 403, 'headers': [(b'content-type', b'application/json')]})
            return await send({'type': 'http.response.body', 'body': b'{"error":"service_policy_denied"}'})
        delivered = False
        async def replay():
            nonlocal delivered
            if not delivered:
                delivered = True
                return {'type': 'http.request', 'body': body, 'more_body': False}
            return await receive()
        nonlocal in_flight
        is_call = message['method'] == 'tools/call'
        if is_call:
            in_flight += 1
            idle.touch()
        try:
            return await app(scope, replay, send)
        finally:
            if is_call:
                in_flight -= 1
                idle.touch()

    sock_path = Path(config['socket'])
    sock_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    sock_path.unlink(missing_ok=True)
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.bind(str(sock_path))
    os.chmod(sock_path, 0o600)
    import uvicorn
    server = uvicorn.Server(uvicorn.Config(guarded, lifespan='on', log_level='warning', limit_concurrency=8))
    def auto_stop():
        while not server.should_exit:
            time.sleep(2)
            if not server.started:
                idle.touch()
                continue
            try:
                active = in_flight > 0 or busy()
            except Exception:
                active = True
            if idle.expired(active):
                server.should_exit = True
    threading.Thread(target=auto_stop, daemon=True).start()
    server.run(sockets=[sock])


if __name__ == '__main__':
    main()
