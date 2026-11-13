"""Synthetic desktop process restart fixture for the Android unlock acceptance."""
from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
from pathlib import Path
import queue
import signal
import socket
import ssl
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

REPO = Path(__file__).resolve().parents[3]
sys.path[:0] = [str(REPO), str(REPO / 'desktop')]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--ready-file', type=Path, required=True)
    args = parser.parse_args()
    root = args.root.resolve()
    root.mkdir(parents=True, exist_ok=False)
    os.environ.update(HOME=str(root/'home'), XDG_CONFIG_HOME=str(root/'config'),
                      XDG_CACHE_HOME=str(root/'cache'), IGOREK_FACTORY_HOME=str(root), FACTORY_DATA_DIR=str(root),
                      FACTORY_SESSION_EMBEDDINGS_ENABLED='off')
    from vault_owner import OwnerVault
    from factory.core.mobile_bridge.store import MobileStore
    from factory.core.mobile_bridge.tls_server import TLSHandshakeMixin
    from factory.core.cli_chat import _append_entry, _session_path
    owner = OwnerVault(root)
    owner.service.create('fixture-unlock-code')
    owner.close()
    store = MobileStore()
    paired = store.complete_pairing(code=store.create_pairing_code()['code'], name='Acceptance phone', platform='android')
    session = 'chat-20261113-120000-unlock'
    path = _session_path(session)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({'type': 'meta', 'session_id': session, 'profile': 'fixture',
                               'title': 'Retained unlock fixture', 'cwd': str(root / 'workspace')}) + '\n')
    _append_entry(path, role='user', text='Earlier fixture question')
    _append_entry(path, role='assistant', text='Retained fixture response')
    with socket.socket() as reservation:
        reservation.bind(('127.0.0.1', 0))
        port = reservation.getsockname()[1]
    (root / 'mobile-access.json').write_text(json.dumps({'schema_version': 2, 'port': port,
                                                       'public_url': '', 'enabled': True}))
    lock = threading.RLock()
    stop = threading.Event()
    child = None
    states = []
    restarts = 0
    error_log = (root / 'fixture-backend.log').open('w')

    def start():
        nonlocal child
        rows = queue.Queue()
        child = subprocess.Popen([sys.executable, '-u', str(REPO / 'desktop/backend.py'), str(root)],
                                 stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=error_log, text=True,
                                 env={**os.environ, 'PYTHONPATH': str(REPO), 'PYTHONUNBUFFERED': '1'})
        process = child
        def read():
            for line in process.stdout:
                frame = json.loads(line)
                if frame.get('type') == 'owner_state':
                    states.append(frame.get('state', {}).get('state'))
                rows.put(frame)
            rows.put(None)
        threading.Thread(target=read, daemon=True).start()
        ready = rows.get(timeout=30)
        if not ready or ready.get('type') != 'ready':
            raise RuntimeError('Synthetic desktop did not become ready')
        return Path(ready['cert'])

    def close():
        if child and child.poll() is None:
            child.stdin.write('stop\n')
            child.stdin.flush()
            child.wait(timeout=15)

    cert = start()
    fingerprint = hashlib.sha256(ssl.PEM_cert_to_DER_cert(cert.read_text())).hexdigest()
    key = cert.with_name('localhost-key.pem')

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *unused):
            pass

        def do_POST(self):
            nonlocal restarts
            if not hmac.compare_digest(self.headers.get('Authorization', ''), 'Bearer ' + paired['token']):
                self.send_error(403)
                return
            if self.path != '/restart':
                self.send_error(404)
                return
            with lock:
                close()
                start()
                restarts += 1
            body = json.dumps({'restarts': restarts}).encode()
            self.send_response(200)
            self.send_header('Content-Length', str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    class Server(TLSHandshakeMixin, ThreadingHTTPServer):
        daemon_threads = True
    server = Server(('127.0.0.1', 0), Handler)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(str(cert), str(key))
    server.enable_tls(context)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    args.ready_file.parent.mkdir(parents=True, exist_ok=True)
    args.ready_file.write_text(json.dumps({'url': f'https://127.0.0.1:{port}',
        'control_url': f'https://127.0.0.1:{server.server_port}', 'port': port,
        'control_port': server.server_port, 'fingerprint': fingerprint,
        'certificate': str(cert), 'token': paired['token'], 'session': session}) + '\n')
    args.ready_file.chmod(0o600)
    for signum in (signal.SIGINT, signal.SIGTERM):
        signal.signal(signum, lambda *_: stop.set())
    print('Synthetic desktop is ready for remote-unlock acceptance', flush=True)
    try:
        stop.wait()
    finally:
        server.shutdown()
        server.server_close()
        with lock:
            close()
        (root / 'acceptance-backend.json').write_text(json.dumps({'restarts': restarts, 'unlock_states': states}) + '\n')
        error_log.close()


if __name__ == '__main__':
    main()
