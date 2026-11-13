"""Exercise Android unlock against a separately owned desktop process fixture."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import subprocess
import time
import ssl
import urllib.request


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--adb', required=True)
    parser.add_argument('--serial', required=True)
    parser.add_argument('--adb-port')
    parser.add_argument('--ready-file', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    repo = Path(__file__).resolve().parents[3]
    args.output.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + 40
    while not args.ready_file.is_file() and time.monotonic() < deadline:
        time.sleep(0.2)
    ready = json.loads(args.ready_file.read_text())
    request = urllib.request.Request(ready['control_url'] + '/restart', data=b'{}',
        headers={'Authorization': 'Bearer ' + ready['token']}, method='POST')
    with urllib.request.urlopen(request, context=ssl.create_default_context(cafile=ready['certificate']), timeout=30) as response:
        assert response.status == 200
    adb = [args.adb, *(['-P', args.adb_port] if args.adb_port else []), '-s', args.serial]
    def run(*options):
        return subprocess.run([*adb, *options], check=True, capture_output=True, timeout=60)
    for path in ('android/app/build/outputs/apk/debug/app-debug.apk',
                 'android/app/build/outputs/apk/androidTest/debug/app-debug-androidTest.apk'):
        run('install', '-r', str(repo / path))
    for port in (ready['port'], ready['control_port']):
        run('reverse', f'tcp:{port}', f'tcp:{port}')
    run('shell', 'settings', 'put', 'system', 'screen_off_timeout', '1800000')
    run('shell', 'input', 'keyevent', 'KEYCODE_WAKEUP')
    run('shell', 'wm', 'dismiss-keyguard')
    command = [*adb, 'shell', 'am', 'instrument', '-w']
    for key in ('url', 'control_url', 'token', 'fingerprint'):
        command += ['-e', key, ready[key]]
    command += ['ru.igorek.remote.test/ru.igorek.remote.RemoteUnlockInstrumentation']
    log = args.output / 'instrumentation.log'
    try:
        with log.open('w') as stream:
            result = subprocess.run(command, stdout=stream, stderr=subprocess.STDOUT, timeout=240)
        text = log.read_text()
        if result.returncode or 'REMOTE_UNLOCK_PASS:' not in text:
            raise RuntimeError('Remote unlock native acceptance failed; see instrumentation.log')
    finally:
        subprocess.run([*adb, 'pull', '/sdcard/Android/data/ru.igorek.remote/files/remote-unlock-qa',
                        str(args.output)], capture_output=True, timeout=30)
    print('REMOTE_UNLOCK_PASS: Android UI, real desktop restart, retained pairing and session', flush=True)


if __name__ == '__main__':
    main()
