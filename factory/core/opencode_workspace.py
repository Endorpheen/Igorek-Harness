"""Independent source snapshots and conflict-checked OpenCode result delivery."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import subprocess
from pathlib import Path


MAX_FILES = 20000
MAX_BYTES = 512 * 1024 * 1024
PRIVATE_PARTS = {'.git', '.aws', '.ssh', '.gnupg', '.codex', '.claude', '.igorek-agent-factory', 'Archive'}


def git(root: Path, *args: str) -> bytes:
    env = {key: value for key, value in os.environ.items() if not key.startswith('GIT_')}
    env.update(GIT_CONFIG_NOSYSTEM='1', GIT_CONFIG_GLOBAL=os.devnull, GIT_TERMINAL_PROMPT='0')
    result = subprocess.run(['git', '-c', 'core.fsmonitor=false', '-c', 'core.hooksPath=/dev/null',
                             '-c', 'commit.gpgsign=false', *args], cwd=root, env=env,
                            capture_output=True, timeout=60)
    if result.returncode:
        raise ValueError('opencode_git_operation_failed')
    return result.stdout


def private(name: str) -> bool:
    path = Path(name)
    return (bool(PRIVATE_PARTS.intersection(path.parts)) or path.parts[:2] == ('docs', 'local')
            or path.name == '.env' or path.name.startswith('.env.') and not path.name.endswith('.example'))


def entry(root: Path, name: str) -> dict | None:
    target = root / name
    if Path(name).is_absolute() or '..' in Path(name).parts:
        raise ValueError('opencode_invalid_snapshot_path')
    if target.is_symlink() or not target.resolve().is_relative_to(root.resolve()):
        raise ValueError('opencode_snapshot_symlink_not_supported')
    try:
        info = target.stat()
    except FileNotFoundError:
        return None
    if not stat.S_ISREG(info.st_mode):
        raise ValueError('opencode_snapshot_requires_regular_files')
    if info.st_size > MAX_BYTES:
        raise ValueError('opencode_snapshot_too_large')
    raw = target.read_bytes()
    return {'sha256': hashlib.sha256(raw).hexdigest(), 'mode': stat.S_IMODE(info.st_mode), 'size': len(raw)}


def inventory(root: Path, control: Path | None = None) -> dict:
    args = [] if control is None else ['--git-dir=' + str(control), '--work-tree=' + str(root)]
    names = git(root, *args, 'ls-files', '--cached', '--others', '--exclude-standard', '-z').split(b'\0')
    result, total = {}, 0
    for encoded in names:
        if not encoded:
            continue
        name = os.fsdecode(encoded)
        if private(name):
            continue
        value = entry(root, name)
        if value is not None:
            result[name] = value
            total += value['size']
        if len(result) > MAX_FILES or total > MAX_BYTES:
            raise ValueError('opencode_snapshot_too_large')
    return result


def prepare(source: str, folder: Path) -> Path:
    root = Path(source).resolve(strict=True)
    if not root.is_dir() or root == Path.home() or root == Path('/'):
        raise ValueError('opencode_project_required')
    git(root, 'rev-parse', '--show-toplevel')
    if any(row.startswith(b'160000 ') for row in git(root, 'ls-files', '--stage').splitlines()):
        raise ValueError('opencode_submodule_snapshot_not_supported')
    before = inventory(root)
    folder.mkdir(parents=True, mode=0o700)
    workspace = folder / 'workspace'
    workspace.mkdir(mode=0o700)
    for name in before:
        target = workspace / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(root / name, target, follow_symlinks=False)
        if entry(workspace, name) != before[name]:
            raise ValueError('opencode_source_changed_during_snapshot')
    if inventory(root) != before:
        raise ValueError('opencode_source_changed_during_snapshot')
    git(workspace, 'init', '--initial-branch=main')
    git(workspace, 'add', '--force', '--all')
    git(workspace, '-c', 'user.name=Igorek', '-c', 'user.email=igorek@localhost',
        'commit', '--allow-empty', '-m', 'Capture delegated task baseline')
    shutil.copytree(workspace / '.git', folder / 'baseline.git', symlinks=False)
    (folder / 'baseline.json').write_text(json.dumps(before), encoding='utf-8')
    return workspace


def apply_result(source: str, folder: Path) -> dict:
    root, workspace = Path(source).resolve(strict=True), folder / 'workspace'
    baseline = json.loads((folder / 'baseline.json').read_text(encoding='utf-8'))
    current = inventory(workspace, folder / 'baseline.git')
    changed = sorted(name for name in baseline.keys() | current.keys() if baseline.get(name) != current.get(name))
    conflicts = [name for name in changed if entry(root, name) != baseline.get(name)]
    if conflicts:
        return {'applied': False, 'conflicts': conflicts, 'files': changed}
    backups = {}
    try:
        for name in changed:
            target = root / name
            if entry(root, name) != baseline.get(name):
                raise ValueError('opencode_source_changed_during_apply')
            backups[name] = target.read_bytes() if baseline.get(name) else None
            if name not in current:
                target.unlink()
                continue
            raw = (workspace / name).read_bytes()
            if hashlib.sha256(raw).hexdigest() != current[name]['sha256']:
                raise ValueError('opencode_result_changed_during_apply')
            target.parent.mkdir(parents=True, exist_ok=True)
            temporary = target.with_name('.' + target.name + '.igorek-' + folder.name)
            try:
                with temporary.open('xb') as stream:
                    stream.write(raw)
                temporary.chmod(current[name]['mode'])
                temporary.replace(target)
            finally:
                temporary.unlink(missing_ok=True)
    except Exception:
        for name, raw in backups.items():
            target = root / name
            if raw is None:
                target.unlink(missing_ok=True)
            else:
                target.write_bytes(raw)
                target.chmod(baseline[name]['mode'])
        raise
    return {'applied': True, 'conflicts': [], 'files': changed}
