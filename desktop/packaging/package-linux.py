"""Create an offline Linux snapshot from existing Electron and Python runtimes."""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import os
import platform
import shutil
import stat
import subprocess
import tarfile
import tempfile
import tomllib
from pathlib import Path

import sys
sys.path.insert(0, str(Path(__file__).resolve().parent))
from bundle_sources import APP_FILES, BACKEND_FILES, LEGAL_FILES



def check_links(root: Path):
    for path in root.rglob('*'):
        if path.is_symlink():
            target = os.readlink(path)
            if os.path.isabs(target) or not path.resolve().is_relative_to(root.resolve()):
                raise ValueError(f'External symlink: {path.relative_to(root)}')


def inventory(root: Path):
    entries = {}
    for path in sorted(root.rglob('*')):
        name = path.relative_to(root).as_posix()
        if path.is_symlink():
            entries[name] = {'link': os.readlink(path)}
        elif path.is_file():
            with path.open('rb') as stream:
                digest = hashlib.file_digest(stream, 'sha256').hexdigest()
            entries[name] = {'sha256': digest, 'mode': stat.S_IMODE(path.stat().st_mode)}
    return entries


def build(repo: Path, output: Path, *, libvirt_fix: Path | None = None, runtime_path: Path | None = None):
    if platform.system() != 'Linux' or platform.machine() != 'x86_64':
        raise ValueError('Only Linux x86_64 is supported by this snapshot')
    if output.exists():
        raise FileExistsError('Choose a new output directory')
    if libvirt_fix is not None:
        spec = importlib.util.spec_from_file_location('libvirt_fix', Path(__file__).with_name('install-libvirt-helper.py'))
        fixer = importlib.util.module_from_spec(spec); spec.loader.exec_module(fixer)
        fixer.payload_bytes(libvirt_fix)
    desktop = repo / 'desktop'
    packaging = desktop / 'packaging'
    runtime = runtime_path.resolve() if runtime_path is not None else desktop / 'runtime'
    electron = desktop / 'node_modules/electron/dist'
    if output.resolve().is_relative_to(runtime.resolve()) or output.resolve().is_relative_to(electron.resolve()):
        raise ValueError('Output must not be inside an input runtime')
    for path in runtime.rglob('*'):
        if path.name == 'passphrase.txt' or path.name == '.env' or path.name.startswith('.env.') or path.suffix in {'.db', '.sqlite', '.sqlite3', '.jsonl'}:
            raise ValueError(f'User data must not be bundled: {path.relative_to(runtime)}')
    manifest = json.loads((runtime / 'manifest.json').read_text())
    version = tomllib.loads((repo / 'pyproject.toml').read_text())['project']['version']
    if manifest.get('factory_version') != version or manifest.get('schema_version') != 1:
        raise ValueError('The existing Python bundle does not match the project version')
    if 'codex_quota' in manifest or (runtime / 'codex-quota').exists() or (runtime / 'codex-quota').is_symlink():
        raise ValueError('Legacy bundled Codex quota executable: rebuild the runtime')
    release_path = desktop / 'assets/release-notes.json'
    if release_path.is_symlink():
        raise ValueError('Release notes must be a regular file')
    release = json.loads(release_path.read_text())
    if release.get('version') != version or not isinstance(release.get('changes'), list) or not 1 <= len(release['changes']) <= 4 or any(not isinstance(item, str) or not item.strip() or len(item) > 160 for item in release['changes']):
        raise ValueError('Release notes must match the desktop version and contain brief changes')
    expected_electron = json.loads((desktop / 'package.json').read_text())['devDependencies']['electron']
    if (electron / 'version').read_text().strip().removeprefix('v') != expected_electron:
        raise ValueError('Electron does not match the pinned version')
    python_name = manifest['python']
    if not isinstance(python_name, str) or Path(python_name).is_absolute() or '..' in Path(python_name).parts:
        raise ValueError('Invalid bundled Python path')
    if not (runtime / python_name).is_file():
        raise ValueError('Bundled Python is missing')
    check_links(runtime); check_links(electron)
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.linux-package-', dir=output.parent) as temporary:
        stage = Path(temporary)
        root = stage / 'igorek-desktop'
        app = root / 'app'; app.mkdir(parents=True)
        (root / 'licenses').mkdir()
        for name in LEGAL_FILES:
            source = repo / name
            if source.is_symlink():
                raise ValueError(f'License source must be a regular file: {name}')
            shutil.copy2(source, root / 'licenses' / source.name)
        for name in APP_FILES:
            source = desktop / name
            if source.is_symlink():
                raise ValueError(f'Application source must be a regular file: {name}')
            shutil.copy2(source, app / name)
        (app / 'package.json').write_text(json.dumps({'name': 'igorek-desktop-prototype', 'private': True, 'main': 'main.cjs'}) + '\n')
        print('Copying existing Python and Electron; no dependency builds or downloads.', flush=True)
        ignore = shutil.ignore_patterns('__pycache__', '*.pyc')
        shutil.copytree(runtime, app / 'runtime', symlinks=True, ignore=ignore)
        shutil.copytree(electron, root / 'electron', symlinks=True)
        overlay = manifest.setdefault('prototype_source_overlay', {})
        for name in BACKEND_FILES:
            shutil.copy2(desktop / name, app / 'runtime' / name)
            overlay[name] = hashlib.sha256((desktop / name).read_bytes()).hexdigest()
        for name, digest in overlay.items():
            if name.startswith('packages/factory/'):
                source = repo / 'factory' / name.removeprefix('packages/factory/')
                if not source.is_file() or hashlib.sha256(source.read_bytes()).hexdigest() != digest:
                    raise ValueError(f'Stale source overlay: {name}; update it before packaging')
        (app / 'runtime/manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
        spec = importlib.util.spec_from_file_location('libvirt_bundle', Path(__file__).with_name('bundle-libvirt.py'))
        helper = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(helper)
        helper.verify_bindings(app / 'runtime')
        spec = importlib.util.spec_from_file_location('socket_bundle', Path(__file__).with_name('bundle-socket.py'))
        helper = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(helper)
        helper.verify_socket(app / 'runtime')
        shutil.copy2(packaging / 'verify-package.py', root / 'verify-package.py')
        shutil.copy2(packaging / 'install-linux.py', root / 'install-linux.py')
        shutil.copy2(packaging / 'install-gui.py', root / 'install-gui.py')
        shutil.copy2(packaging / 'installer-gtk.py', root / 'installer-gtk.py')
        (app / 'assets').mkdir()
        shutil.copy2(release_path, app / 'assets/release-notes.json')
        shutil.copy2(desktop / 'assets/evil-octopus.png', app / 'assets/evil-octopus.png')
        shutil.copy2(desktop / 'assets/evil-octopus-wink.png', app / 'assets/evil-octopus-wink.png')
        shutil.copy2(desktop / 'assets/igorek.png', app / 'assets/igorek.png')
        shutil.copy2(desktop / 'assets/OPENAI-ICON-LICENSE.txt', app / 'assets/OPENAI-ICON-LICENSE.txt')
        shutil.copy2(desktop / 'assets/github.svg', app / 'assets/github.svg')
        shutil.copy2(desktop / 'assets/GITHUB-ICON-LICENSE.txt', app / 'assets/GITHUB-ICON-LICENSE.txt')
        (root / 'assets').mkdir()
        shutil.copy2(desktop / 'assets/evil-octopus.png', root / 'assets/evil-octopus.png')
        shutil.copy2(packaging / 'install-libvirt-helper.py', root / 'install-libvirt-helper.py')
        if libvirt_fix is not None:
            (root / 'libvirt-fix').mkdir()
            for name in ('manifest.json', 'virt-aa-helper', 'COPYING.LESSER', 'virtiofs-socket.patch'):
                shutil.copy2(libvirt_fix / name, root / 'libvirt-fix' / name)
            fixer.payload_bytes(root / 'libvirt-fix')
        shutil.copy2(desktop / 'igorek.svg', root / 'igorek.svg')
        gui_launcher = (packaging / 'install-gui.sh').read_text().replace('@PYTHON@', python_name)
        (root / 'install-gui.sh').write_text(gui_launcher)
        (root / 'install-gui.sh').chmod(0o755)
        shutil.copy2(packaging / 'install-sandbox-policy.sh', root / 'install-sandbox-policy.sh')
        launcher = (packaging / 'portable-launcher.sh').read_text().replace('@PYTHON@', python_name)
        (root / 'igorek').write_text(launcher); (root / 'igorek').chmod(0o755)
        (root / 'install-sandbox-policy.sh').chmod(0o755)
        for path in root.rglob('*'):
            if path.is_file() and not path.is_symlink():
                path.chmod(0o755 if path.stat().st_mode & 0o111 else 0o644)
        check_links(root)
        print('Hashing package contents.', flush=True)
        for cache in root.rglob('__pycache__'):
            if cache.is_dir() and not cache.is_symlink():
                shutil.rmtree(cache)
        for cache in root.rglob('*.pyc'):
            cache.unlink()
        entries = inventory(root)
        payload_hash = hashlib.sha256(json.dumps(entries, sort_keys=True).encode()).hexdigest()
        package = {'schema_version': 1, 'kind': 'linux-prototype-snapshot', 'factory_version': version,
                   'source_commit': manifest.get('source_commit'),
                   'source_modified': manifest.get('source_modified'),
                   'electron_version': expected_electron, 'platform': 'linux-x86_64', 'build_id': payload_hash[:12],
                   'files': entries, 'verification': 'local-integrity-only'}
        (root / 'package-manifest.json').write_text(json.dumps(package, indent=2) + '\n')
        subprocess.run([str(app / 'runtime' / python_name), '-B', '-I', str(root / 'verify-package.py'), str(root)], check=True)
        archive = stage / f'igorek-desktop-{version}-linux-x86_64-{payload_hash[:12]}.tar.gz'
        print('Compressing the snapshot.', flush=True)
        with tarfile.open(archive, 'w:gz', compresslevel=1) as tar:
            tar.add(root, arcname=root.name)
        with archive.open('rb') as stream:
            archive_hash = hashlib.file_digest(stream, 'sha256').hexdigest()
        (stage / (archive.name + '.sha256')).write_text(f'{archive_hash}  {archive.name}\n')
        published = stage / 'output'; published.mkdir()
        root.rename(published / root.name)
        archive.rename(published / archive.name)
        (stage / (archive.name + '.sha256')).rename(published / (archive.name + '.sha256'))
        published.rename(output)
    print(f'Snapshot ready: {output}; build {payload_hash[:12]}', flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--libvirt-fix', type=Path, help='Готовый проверенный компонент KVM, без сборки при установке.')
    parser.add_argument('--runtime', type=Path, help='Use a separately built runtime without replacing the active one.')
    args = parser.parse_args()
    build(Path(__file__).resolve().parents[2], args.output.absolute(), libvirt_fix=args.libvirt_fix, runtime_path=args.runtime)
