"""Build a Windows x64 Python payload on Linux or Windows from locked sources."""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import tomllib
import urllib.request
import zipfile
from bundle_sources import BACKEND_FILES

PYTHON_VERSION = '3.14.4'
PYTHON_SHA256 = 'cda80a9b1e75c0f1b4f9872ca1b417f0d19bce32facc811aea9180e70fad5fb9'
PYTHON_URL = f'https://www.python.org/ftp/python/{PYTHON_VERSION}/python-{PYTHON_VERSION}-embed-amd64.zip'
RG_ARCHIVE = 'ripgrep-15.1.0-x86_64-pc-windows-msvc'
RG_SHA256 = '124510b94b6baa3380d051fdf4650eaa80a302c876d611e9dba0b2e18d87493a'
RG_URL = f'https://github.com/BurntSushi/ripgrep/releases/download/15.1.0/{RG_ARCHIVE}.zip'


def build(output: Path):
    repo = Path(__file__).resolve().parents[2]
    if (repo / '.python-version').read_text().strip() != PYTHON_VERSION:
        raise ValueError('Update the verified Windows Python pin before building')
    if output.exists():
        raise FileExistsError('Choose a fresh output directory')
    uv = shutil.which('uv')
    if not uv:
        raise ValueError('uv is required on the build machine')
    output = output.resolve(); output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.windows-runtime-', dir=output.parent) as temporary:
        stage = Path(temporary); bundle = stage / 'bundle'; bundle.mkdir()
        archive = stage / 'python.zip'
        with urllib.request.urlopen(PYTHON_URL, timeout=60) as response, archive.open('wb') as stream:
            shutil.copyfileobj(response, stream)
        if hashlib.sha256(archive.read_bytes()).hexdigest() != PYTHON_SHA256:
            raise ValueError('Windows Python checksum mismatch')
        python = bundle / 'python'; python.mkdir()
        with zipfile.ZipFile(archive) as package:
            for name in package.namelist():
                if Path(name).name != name or '\\' in name:
                    raise ValueError('Unexpected Python archive entry')
            package.extractall(python)
        requirements = stage / 'requirements.txt'
        subprocess.run([uv, 'export', '--frozen', '--no-dev', '--no-emit-project', '--format',
                        'requirements.txt', '-o', str(requirements)], cwd=repo, check=True, stdout=subprocess.DEVNULL)
        common = [uv, 'pip', 'install', '--python-platform', 'x86_64-pc-windows-msvc',
                  '--python-version', PYTHON_VERSION, '--only-binary', ':all:', '--link-mode', 'copy',
                  '--target', str(bundle / 'packages')]
        subprocess.run([*common, '--require-hashes', '-r', str(requirements)], cwd=repo, check=True)
        subprocess.run([sys.executable, str(repo / 'tools/build_wheel.py'), '--output', str(stage / 'wheels')],
                       cwd=repo, check=True)
        wheels = list((stage / 'wheels').glob('*.whl'))
        if len(wheels) != 1:
            raise ValueError('Expected exactly one project wheel')
        subprocess.run([*common, '--no-deps', str(wheels[0])], cwd=repo, check=True)
        for metadata in (bundle / 'packages').glob('igorek_agent_factory-*.dist-info'):
            for name in ('direct_url.json', 'uv_cache.json'):
                (metadata / name).unlink(missing_ok=True)
        provenance_path = wheels[0].with_suffix('.provenance.json')
        provenance = json.loads(provenance_path.read_text())
        shutil.copy2(provenance_path, bundle / 'source-provenance.json')
        for name in BACKEND_FILES:
            shutil.copy2(repo / 'desktop' / name, bundle / name)
        (python / 'python314._pth').write_text('python314.zip\n.\n../packages\n', encoding='utf-8')
        archive = stage / 'ripgrep.zip'
        with urllib.request.urlopen(RG_URL, timeout=60) as response, archive.open('wb') as stream:
            shutil.copyfileobj(response, stream)
        if hashlib.sha256(archive.read_bytes()).hexdigest() != RG_SHA256:
            raise ValueError('Windows ripgrep checksum mismatch')
        tools = bundle / 'tools'; tools.mkdir()
        with zipfile.ZipFile(archive) as package:
            for name in ('rg.exe', 'COPYING', 'LICENSE-MIT', 'UNLICENSE'):
                (tools / name).write_bytes(package.read(f'{RG_ARCHIVE}/{name}'))
        version = tomllib.loads((repo / 'pyproject.toml').read_text())['project']['version']
        manifest = {'schema_version': 1, 'platform': 'windows-x64', 'python': 'python/python.exe',
                    'entry': 'backend.py', 'python_version': PYTHON_VERSION, 'factory_version': version,
                    'source_commit': provenance['source_commit'], 'source_modified': provenance['working_tree_modified'],
                    'lock_sha256': hashlib.sha256((repo / 'uv.lock').read_bytes()).hexdigest(),
                    'python_archive_sha256': PYTHON_SHA256, 'wheel_sha256': provenance['wheel_sha256']}
        manifest['backend_sources'] = {name: hashlib.sha256((bundle / name).read_bytes()).hexdigest() for name in BACKEND_FILES}
        manifest['tools'] = {'ripgrep': {'version': '15.1.0', 'archive_sha256': RG_SHA256,
                            'sha256': hashlib.sha256((tools / 'rg.exe').read_bytes()).hexdigest()}}
        (bundle / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
        shutil.move(str(bundle), output)
    print(f'Windows Python payload: {output}')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    build(parser.parse_args().output)
