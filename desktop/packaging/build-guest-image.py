"""Prepare a separate desktop guest image with its own fixed host channel."""
from __future__ import annotations

import argparse
from dataclasses import replace
from datetime import datetime, timezone
import json
from pathlib import Path
import re
import subprocess
import tempfile

import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from image_setup import check_image
from factory.core.isolation.base_image import load_manifest, manifest_path_for, compute_sha256, write_manifest

PORT = 40519
UNIT = '/etc/systemd/system/igorek-guest-runner.service'
DIRECTORY = UNIT + '.d'
DROPIN = DIRECTORY + '/desktop-channel.conf'


def desktop_override(unit, guest_user):
    if not re.fullmatch(r'[a-z_][a-z0-9_-]*', guest_user):
        raise ValueError('Invalid guest user')
    fields = {}
    section = None
    for line in unit.splitlines():
        line = line.strip()
        if not line or line.startswith(('#', ';')):
            continue
        if line.startswith('[') and line.endswith(']'):
            section = line
            continue
        if section == '[Service]' and '=' in line:
            key, value = line.split('=', 1)
            fields.setdefault(key, []).append(value)
    legacy = f'/home/{guest_user}/.local/bin/igorek-factory guest-runner --vsock-cid 2 --vsock-port 5005'
    current = '/usr/local/bin/igorek-factory guest-runner --vsock-cid 2 --vsock-port 5005'
    commands = fields.get('ExecStart', [])
    if (len(commands) != 1 or commands[0] not in {legacy, current} or fields.get('User') != [guest_user]
            or fields.get('Group') != [guest_user]
            or 'IGOREK_ISOLATION_VSOCK_PORT=5005' not in fields.get('Environment', [])
            or fields.get('ExecStartPre') != ['/usr/local/bin/igorek-guest-bwrap-probe']):
        raise ValueError('Unsupported guest runner unit')
    command = commands[0]
    return ('[Service]\nEnvironment=IGOREK_ISOLATION_VSOCK_PORT=' + str(PORT)
            + '\nExecStart=\nExecStart=' + command.rsplit(' ', 1)[0] + ' ' + str(PORT) + '\n')


def build_guest(source, output, *, tool_version, progress=print):
    source, output = Path(source).absolute(), Path(output).absolute()
    if source.resolve() != source or output.parent.resolve() != output.parent:
        raise ValueError('Symlink paths refused')
    if output.exists() or output.is_symlink():
        raise ValueError('Destination already exists')
    progress('Проверяю исходный образ')
    check_image(source, tool_version)
    original = load_manifest(manifest_path_for(source))
    read = subprocess.run(['/usr/bin/guestfish', '--ro', '-a', str(source), '-i',
        'cat', UNIT, ':', 'is-dir', DIRECTORY], check=True, capture_output=True, text=True, timeout=90)
    lines = read.stdout.rstrip().splitlines()
    if not lines or lines[-1] != 'false':
        raise ValueError('Existing guest overrides refused')
    override = desktop_override('\n'.join(lines[:-1]), original.guest_user)
    with tempfile.TemporaryDirectory(prefix='.desktop-guest-', dir=output.parent) as temporary:
        stage = Path(temporary) / 'images'; stage.mkdir(mode=0o700)
        image = stage / 'base.qcow2'
        progress('Копирую образ, рабочий источник не меняется')
        subprocess.run(['/usr/bin/cp', '--reflink=auto', '--sparse=always', '--', str(source), str(image)], check=True)
        image.chmod(0o600)
        progress('Настраиваю отдельный канал desktop')
        result = subprocess.run(['/usr/bin/guestfish', '-a', str(image), '-i', 'mkdir-p', DIRECTORY,
            ':', 'write', DROPIN, override, ':', 'cat', DROPIN],
            check=True, capture_output=True, text=True, timeout=90)
        if result.stdout.rstrip('\n') != override.rstrip('\n'):
            raise ValueError('Guest override verification failed')
        progress('Обновляю паспорт и проверяю готовый образ')
        manifest = replace(original, image_name=image.name, vsock_port=PORT, sha256=compute_sha256(image),
            size_bytes=image.stat().st_size, built_at=datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ'),
            builder=original.builder + '+desktop-channel')
        path = write_manifest(manifest, image)
        payload = json.loads(path.read_text())
        payload['desktop_channel'] = {'schema_version':1, 'vsock_port':PORT,
            'source_sha256':original.sha256, 'override_path':DROPIN}
        path.write_text(json.dumps(payload, indent=2) + '\n')
        check_image(image, tool_version)
        image.chmod(0o400); path.chmod(0o400)
        stage.rename(output)
    progress('Отдельный desktop-образ готов; выполнение не включено')
    return {'path':str(output / 'base.qcow2'), 'vsock_port':PORT, 'tool_version':tool_version}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--tool-version', required=True)
    args = parser.parse_args()
    print(json.dumps(build_guest(args.source, args.output, tool_version=args.tool_version)), flush=True)
