"""Run the official OpenCode service and its host-only vsock ingress in a guest."""
import argparse
import os
from pathlib import Path
import signal
import socket
import stat
import subprocess
import threading

from importlib.util import spec_from_file_location, module_from_spec


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--binary', type=Path, default=Path('/usr/local/bin/opencode'))
    parser.add_argument('--password-file', type=Path, required=True)
    parser.add_argument('--project', type=Path, required=True)
    args = parser.parse_args()
    info = args.password_file.lstat()
    if not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid() or info.st_mode & 0o077:
        raise ValueError('Private guest password file required')
    password = args.password_file.read_text().strip()
    if len(password) < 24 or not args.binary.is_absolute() or not args.project.is_dir():
        raise ValueError('Invalid guest service configuration')
    spec = spec_from_file_location('opencode_bridge', Path(__file__).with_name('opencode-sandbox.py'))
    bridge = module_from_spec(spec)
    spec.loader.exec_module(bridge)
    env = dict(os.environ)
    env['OPENCODE_SERVER_PASSWORD'] = password
    child = subprocess.Popen([str(args.binary), 'serve', '--hostname', '127.0.0.1', '--port', '4096'],
                             cwd=args.project, env=env, stdin=subprocess.DEVNULL,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    listener = socket.socket(socket.AF_VSOCK, socket.SOCK_STREAM)
    listener.bind((socket.VMADDR_CID_ANY, 40521))
    listener.listen(64)
    def stop(*_):
        child.terminate()
        listener.close()
        raise SystemExit(0)
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    def accept():
        while child.poll() is None:
            incoming, peer = listener.accept()
            if peer[0] != socket.VMADDR_CID_HOST:
                incoming.close()
                continue
            def handle(incoming):
                try:
                    outgoing = socket.create_connection(('127.0.0.1', 4096), timeout=10)
                    outgoing.settimeout(None)
                except OSError:
                    incoming.close()
                    return
                bridge.pump(incoming, outgoing)
            threading.Thread(target=handle, args=(incoming,), daemon=True).start()
    threading.Thread(target=accept, daemon=True).start()
    raise SystemExit(child.wait())


if __name__ == '__main__':
    main()
