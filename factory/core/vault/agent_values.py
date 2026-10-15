"""Append-only task-bound Vault writes from an explicitly named workspace file."""
import hashlib
import json
import os
import stat
from pathlib import Path, PurePosixPath

from .lifecycle import VaultLockedError
from .vault import DuplicateCredentialError


def validate_request(args):
    if not isinstance(args, dict) or set(args) != {'source_path', 'name', 'kind'}:
        raise ValueError()
    source, name, kind = args['source_path'], args['name'], args['kind']
    if not isinstance(source, str) or not 1 <= len(source) <= 512 or '\0' in source or '\\' in source:
        raise ValueError()
    path = PurePosixPath(source)
    if path.is_absolute() or any(part in {'', '.', '..'} for part in source.split('/')):
        raise ValueError()
    if not isinstance(name, str) or not 1 <= len(name) <= 128 or name != name.strip() \
            or any(ord(char) < 32 or ord(char) == 127 for char in name):
        raise ValueError()
    if kind not in {'secret', 'variable'}:
        raise ValueError()
    if kind == 'variable':
        import re
        if re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]{0,127}', name) is None:
            raise ValueError()


def read_workspace_value(root, relative):
    parts = PurePosixPath(relative).parts
    fd = os.open(Path(root).resolve(strict=True), os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        for part in parts[:-1]:
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=fd)
            os.close(fd)
            fd = child
        source = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
        try:
            before = os.fstat(source)
            if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or before.st_size > 4096:
                raise ValueError()
            data = os.read(source, 4097)
            after = os.fstat(source)
            if len(data) != before.st_size or len(data) > 4096 or (before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (after.st_size, after.st_mtime_ns, after.st_ctime_ns):
                raise ValueError()
            value = data.decode('utf-8')
            if len(value) > 1024:
                raise ValueError()
            return value, hashlib.sha256(data).hexdigest()
        finally:
            os.close(source)
    finally:
        os.close(fd)


class AgentValueWriter:
    def __init__(self, lifecycle, credential_id, config):
        from copy import deepcopy
        self.lifecycle = lifecycle
        self.credential_id = credential_id
        self.config = deepcopy(config)

    def save(self, args, *, workdir, task_id, session_id, profile):
        try:
            validate_request(args)
            if any(not isinstance(value, str) or not 1 <= len(value) <= 128 for value in (task_id, session_id, profile)):
                raise ValueError()
            with self.lifecycle._mutex:
                current = next((p for p in self.lifecycle.list_provider_profiles() if p['credential_id'] == self.credential_id), None)
                if current is None or current['config'] != self.config or current['status'] != 'verified' or profile != self.config['name']:
                    raise PermissionError()
                value, digest = read_workspace_value(workdir, args['source_path'])
                if args['kind'] == 'secret' and not value:
                    raise ValueError()
                origin = {'kind': 'agent', 'task_id': task_id, 'session_id': session_id, 'profile': profile}
                entry = self.lifecycle._append_agent_value(args['name'], args['kind'], value, origin, digest)
            return json.dumps({'ok': True, 'entry': entry, 'source_file_retained': True}, ensure_ascii=False)
        except VaultLockedError:
            return json.dumps({'ok': False, 'error': 'vault_locked'})
        except DuplicateCredentialError:
            return json.dumps({'ok': False, 'error': 'value_exists'})
        except Exception:
            return json.dumps({'ok': False, 'error': 'vault_store_refused'})
