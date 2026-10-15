"""Install and switch verified local snapshots without touching user data."""
from __future__ import annotations

import argparse
import contextlib
import fcntl
import importlib.util
import json
import os
import re
import shutil
import tempfile
import uuid
from pathlib import Path

_spec = importlib.util.spec_from_file_location('package_verifier', Path(__file__).with_name('verify-package.py'))
_verifier = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_verifier)
MARKER = {'schema_version': 1, 'kind': 'igorek-linux-user-installation'}


class InstallationBusyError(RuntimeError):
    pass


@contextlib.contextmanager
def installation_lock(prefix: Path):
    if prefix.is_symlink():
        raise ValueError('Installation root must not be a symlink')
    prefix.mkdir(parents=True, exist_ok=True)
    marker = prefix / 'installation.json'
    if marker.exists():
        if marker.is_symlink() or json.loads(marker.read_text()) != MARKER:
            raise ValueError('Unknown installation directory')
    elif any(prefix.iterdir()):
        raise ValueError('Choose an empty directory for the first installation')
    if (prefix / 'run.lock').is_symlink():
        raise ValueError('Installation lock must not be a symlink')
    with (prefix / 'run.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise InstallationBusyError('Application or another installation is running; close it before switching') from None
        if not marker.exists():
            marker.write_text(json.dumps(MARKER) + '\n')
        for name in ['releases', 'generations']:
            path = prefix / name
            if path.is_symlink():
                raise ValueError('Installation control directory must not be a symlink')
            path.mkdir(exist_ok=True)
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def state(prefix: Path):
    active = prefix / 'active'
    if not active.is_symlink():
        if active.exists():
            raise ValueError('Invalid active installation')
        return {'current': None, 'previous': None}
    target = active.resolve()
    if target.parent != (prefix / 'generations').resolve():
        raise ValueError('Active generation escapes installation')
    result = json.loads((target / 'state.json').read_text())
    for key in ['current', 'previous']:
        build_id = result.get(key)
        if build_id is None and key == 'previous':
            continue
        if not isinstance(build_id, str) or not re.fullmatch('[0-9a-f]{12}', build_id):
            raise ValueError('Invalid build identifier')
        if (prefix / 'releases' / build_id).is_symlink():
            raise ValueError('Release directory must not be a symlink')
        if (target / key).resolve() != (prefix / 'releases' / build_id).resolve():
            raise ValueError('Generation pointer does not match its metadata')
    return result


def publish_generation(prefix: Path, current: str, previous: str | None):
    generation = prefix / 'generations' / uuid.uuid4().hex
    generation.mkdir()
    (generation / 'state.json').write_text(json.dumps({'current': current, 'previous': previous}) + '\n')
    (generation / 'current').symlink_to('../../releases/' + current, target_is_directory=True)
    if previous:
        (generation / 'previous').symlink_to('../../releases/' + previous, target_is_directory=True)
    temporary = prefix / ('.active-' + uuid.uuid4().hex)
    temporary.symlink_to('generations/' + generation.name, target_is_directory=True)
    os.replace(temporary, prefix / 'active')


def copy_payload(source: Path, target: Path, manifest: dict):
    target.mkdir()
    for name, record in manifest['files'].items():
        if 'link' in record:
            continue
        path = target / name; path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source / name, path)
    for name, record in manifest['files'].items():
        if 'link' not in record:
            continue
        path = target / name; path.parent.mkdir(parents=True, exist_ok=True)
        path.symlink_to(record['link'])
    shutil.copy2(source / 'package-manifest.json', target / 'package-manifest.json')


def install(snapshot: Path, prefix: Path):
    snapshot = snapshot.resolve(); prefix = prefix.absolute()
    manifest = _verifier.verify(snapshot)
    build_id = manifest.get('build_id')
    if not isinstance(build_id, str) or not re.fullmatch('[0-9a-f]{12}', build_id):
        raise ValueError('Invalid build identifier')
    with installation_lock(prefix):
        before = state(prefix)
        release = prefix / 'releases' / build_id
        if release.is_symlink():
            raise ValueError('Release directory must not be a symlink')
        if release.exists():
            existing = _verifier.verify(release)
            if existing != manifest:
                raise ValueError('Build identifier conflicts with an existing snapshot')
        else:
            with tempfile.TemporaryDirectory(prefix='.stage-', dir=prefix) as temporary:
                staged = Path(temporary) / 'release'
                copy_payload(snapshot, staged, manifest)
                _verifier.verify(staged)
                staged.rename(release)
        launcher = prefix / 'igorek'
        content = '''#!/usr/bin/env bash
set -euo pipefail
INSTALL_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
exec flock --shared "$INSTALL_ROOT/run.lock" "$INSTALL_ROOT/active/current/igorek" "$@"
'''
        if launcher.exists() and (launcher.is_symlink() or launcher.read_text() != content):
            raise ValueError('Unknown launcher; it will not be overwritten')
        if not launcher.exists():
            launcher.write_text(content); launcher.chmod(0o755)
        if before['current'] != build_id:
            publish_generation(prefix, build_id, before['current'])
        return state(prefix)


def rollback(prefix: Path):
    prefix = prefix.absolute()
    if not (prefix / 'installation.json').is_file():
        raise ValueError('No existing installation')
    with installation_lock(prefix):
        before = state(prefix)
        if not before['previous']:
            raise ValueError('No previous snapshot to restore')
        _verifier.verify(prefix / 'releases' / before['previous'])
        publish_generation(prefix, before['previous'], before['current'])
        return state(prefix)


def uninstall(prefix: Path, data_home: Path, *, delete_data=False, config_home=None):
    prefix, data_home = Path(prefix).absolute(), Path(data_home).absolute()
    config_home = Path(config_home or os.environ.get('XDG_CONFIG_HOME') or Path.home() / '.config').absolute()
    roots = [config_home / 'igorek-desktop-runtime', config_home / 'igorek-desktop-owned'] if delete_data else []
    menu = data_home / 'applications/igorek-desktop.desktop'
    if not (prefix / 'installation.json').is_file():
        raise ValueError('No existing installation')
    if prefix.resolve() != prefix or any(root.resolve() != root or root == prefix
            or prefix.is_relative_to(root) or root.is_relative_to(prefix) for root in roots):
        raise ValueError('Unsafe removal path')
    if menu.parent.resolve() != menu.parent or menu.is_symlink():
        raise ValueError('Menu entry must not be a symlink')
    if menu.exists():
        lines = menu.read_text().splitlines()
        escaped = str(prefix).replace('\\', '\\\\').replace('\n', '\\n').replace('\r', '\\r').replace('\t', '\\t')
        if 'X-Igorek-Installer=1' not in lines or 'X-Igorek-InstallRoot=' + escaped not in lines:
            raise ValueError('Unrelated menu entry; removal cancelled')
    with installation_lock(prefix):
        if not state(prefix)['current']:
            raise ValueError('No installed snapshot')
        allowed = {'installation.json', 'run.lock', 'releases', 'generations', 'active', 'igorek'}
        if any(path.name not in allowed for path in prefix.iterdir()):
            raise ValueError('Unknown files in installation; removal cancelled')
        if any(root.exists() and not root.is_dir() for root in roots):
            raise ValueError('Unsafe data directory')
        for root in roots:
            if root.exists():
                shutil.rmtree(root)
        menu.unlink(missing_ok=True)
        shutil.rmtree(prefix)
    return {'removed': True, 'data_deleted': delete_data}


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--prefix', type=Path, default=Path.home() / '.local/share/igorek-desktop')
    actions = parser.add_mutually_exclusive_group(required=True)
    actions.add_argument('--snapshot', type=Path)
    actions.add_argument('--rollback', action='store_true')
    actions.add_argument('--status', action='store_true')
    args = parser.parse_args()
    try:
        result = state(args.prefix.absolute()) if args.status else rollback(args.prefix) if args.rollback else install(args.snapshot, args.prefix)
        if not args.status:
            spec=importlib.util.spec_from_file_location('menu_registration',Path(__file__).with_name('install-gui.py'))
            gui=importlib.util.module_from_spec(spec);spec.loader.exec_module(gui)
            gui.register_menu(args.prefix,Path(os.environ.get('XDG_DATA_HOME') or Path.home()/'.local/share'))
        print(json.dumps(result, ensure_ascii=False))
    except (OSError, ValueError, RuntimeError, KeyError) as error:
        parser.exit(1, str(error) + '\n')
