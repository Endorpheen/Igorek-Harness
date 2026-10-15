"""Check local snapshot integrity; this is not a publisher signature."""
from __future__ import annotations

import hashlib
import json
import os
import stat
import sys
from pathlib import Path


def verify(root: Path):
    root = root.resolve()
    manifest = json.loads((root / 'package-manifest.json').read_text())
    if manifest.get('schema_version') != 1 or manifest.get('kind') != 'linux-prototype-snapshot':
        raise ValueError('Unknown package manifest')
    entries = manifest['files']
    if not isinstance(entries, dict) or not entries:
        raise ValueError('Empty package manifest')
    for name, expected in entries.items():
        relative = Path(name)
        if relative.is_absolute() or '..' in relative.parts:
            raise ValueError('Invalid package path')
        path = root / relative
        if not path.resolve().is_relative_to(root):
            raise ValueError(f'Path escapes package: {name}')
        if 'link' in expected:
            if not path.is_symlink() or os.readlink(path) != expected['link']:
                raise ValueError(f'Changed symlink: {name}')
        else:
            if path.is_symlink() or not path.is_file():
                raise ValueError(f'Missing file: {name}')
            with path.open('rb') as stream:
                digest = hashlib.file_digest(stream, 'sha256').hexdigest()
            if digest != expected['sha256'] or stat.S_IMODE(path.stat().st_mode) != expected['mode']:
                raise ValueError(f'Changed file: {name}')
    return manifest


if __name__ == '__main__':
    try:
        result = verify(Path(sys.argv[1]))
        print(f"Integrity OK: {len(result['files'])} entries, build {result['build_id']}")
    except (OSError, ValueError, KeyError, IndexError) as error:
        print(f'Integrity check failed: {error}', file=sys.stderr)
        raise SystemExit(1)
