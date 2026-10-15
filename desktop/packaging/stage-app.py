"""Stage one reviewed Electron application for the shared platform packager."""
import argparse
import hashlib
import json
from pathlib import Path, PureWindowsPath
import shutil
import tomllib
from bundle_sources import APP_FILES, BACKEND_FILES, LEGAL_FILES


def stage(runtime: Path, output: Path, target: str):
    repo = Path(__file__).resolve().parents[2]
    manifest = json.loads((runtime / 'manifest.json').read_text())
    version = tomllib.loads((repo / 'pyproject.toml').read_text())['project']['version']
    if output.exists():
        raise FileExistsError('Choose a fresh application directory')
    if output.resolve().is_relative_to(runtime.resolve()):
        raise ValueError('Application output must not be inside its runtime')
    if manifest['factory_version'] != version or manifest.get('schema_version') != 1:
        raise ValueError('Runtime version does not match source')
    if target == 'windows-x64' and manifest.get('platform') != target:
        raise ValueError('Expected a Windows x64 runtime')
    if target == 'linux-x64' and not manifest['python'].endswith('/bin/python3.14'):
        raise ValueError('Expected a Linux runtime')
    for field in ('python', 'entry'):
        name = manifest[field]
        relative = Path(name)
        if relative.is_absolute() or PureWindowsPath(name).drive or '..' in relative.parts or '\\' in name:
            raise ValueError('Invalid runtime entry path')
        if not (runtime / relative).is_file():
            raise ValueError('Runtime executable or entry point is missing')
    provenance = json.loads((runtime / 'source-provenance.json').read_text())
    if set(manifest.get('backend_sources', {})) != set(BACKEND_FILES):
        raise ValueError('Backend source inventory is incomplete; rebuild the runtime')
    for name, digest in manifest['backend_sources'].items():
        source = repo / 'desktop' / name
        if (hashlib.sha256(source.read_bytes()).hexdigest() != digest
                or hashlib.sha256((runtime / name).read_bytes()).hexdigest() != digest):
            raise ValueError(f'Stale backend source: {name}; rebuild before packaging')
    for name, digest in provenance['sources'].items():
        source = repo / name
        if not source.is_file() or source.is_symlink() or not source.resolve().is_relative_to(repo):
            raise ValueError('Runtime source is absent from the reviewed inventory')
        if hashlib.sha256(source.read_bytes()).hexdigest() != digest:
            raise ValueError(f'Stale runtime source: {name}; rebuild before packaging')
    if 'codex_quota' in manifest or (runtime / 'codex-quota').exists():
        raise ValueError('Legacy bundled quota executable is not allowed')
    for path in runtime.rglob('*'):
        if path.is_symlink() and (not path.resolve().is_relative_to(runtime.resolve()) or not path.exists()):
            raise ValueError('Runtime contains an external or broken link')
        if path.suffix in {'.db', '.sqlite', '.sqlite3', '.jsonl'} or path.name in {'.env', 'auth.json', 'passphrase.txt'}:
            raise ValueError('Runtime contains private data')
    output.mkdir(parents=True)
    for name in APP_FILES:
        source = repo / 'desktop' / name
        if source.is_symlink():
            raise ValueError('Application source must be a regular file')
        shutil.copy2(source, output / name)
    shutil.copytree(runtime, output / 'runtime', symlinks=True, ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
    (output / 'assets').mkdir()
    for name in ('release-notes.json', 'evil-octopus.png', 'evil-octopus-wink.png', 'igorek.png',
                 'github.svg', 'OPENAI-ICON-LICENSE.txt', 'GITHUB-ICON-LICENSE.txt'):
        source = repo / 'desktop/assets' / name
        if source.is_symlink():
            raise ValueError('Asset source must be a regular file')
        shutil.copy2(source, output / 'assets' / name)
    (output / 'licenses').mkdir()
    for name in LEGAL_FILES:
        shutil.copy2(repo / name, output / 'licenses' / Path(name).name)
    package = {'name': 'igorek-desktop', 'version': version, 'private': True, 'main': 'main.cjs',
               'desktopName': 'igorek-desktop.desktop',
               'description': 'Igorek personal AI agent workspace', 'author': 'Igorek contributors', 'license': 'Apache-2.0',
               'dependencies': {}}
    (output / 'package.json').write_text(json.dumps(package, indent=2) + '\n')
    inventory = {p.relative_to(output).as_posix(): hashlib.sha256(p.read_bytes()).hexdigest()
                 for p in output.rglob('*') if p.is_file() and not p.is_symlink()}
    (output / 'source-manifest.json').write_text(json.dumps({'platform': target, 'version': version,
        'source_commit': manifest['source_commit'], 'source_modified': manifest['source_modified'],
        'files': inventory}, indent=2) + '\n')
    print(f'Staged {target} application: {output}')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runtime', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--target', choices=('windows-x64','linux-x64'), required=True)
    args = parser.parse_args()
    stage(args.runtime, args.output, args.target)
