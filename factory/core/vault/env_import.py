"""Bounded owner-selected dotenv import; previews never contain values."""
import hashlib
import json
import os
import re
import secrets
import stat
import time
from pathlib import Path


class EnvImportError(ValueError):
    def __init__(self, code='env_import_invalid', line=0):
        self.code, self.line = code, line
        super().__init__(code)


def parse_env(data):
    try:
        text = data.decode('utf-8-sig')
    except UnicodeError as exc:
        raise EnvImportError() from exc
    entries = {}
    for number, line in enumerate(text.splitlines(), 1):
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        if line.startswith('export ') or line.startswith('export\t'):
            line = line[6:].lstrip()
        match = re.fullmatch(r'([A-Za-z_][A-Za-z0-9_]{0,127})[ \t]*=[ \t]*(.*)', line)
        if match is None:
            raise EnvImportError(line=number)
        name, raw = match.groups()
        if name in entries or len(entries) >= 128:
            raise EnvImportError(line=number)
        value = raw
        if raw.startswith(('"', "'")):
            quote, chars, index, closed = raw[0], [], 1, False
            while index < len(raw):
                char = raw[index]
                if char == quote:
                    tail = raw[index + 1:].strip()
                    if tail and not tail.startswith('#'):
                        raise EnvImportError(line=number)
                    closed = True
                    break
                if char == '\\' and index + 1 < len(raw):
                    following = raw[index + 1]
                    escapes = {'\\': '\\', quote: quote}
                    if quote == '"':
                        escapes.update(n='\n', r='\r', t='\t')
                    if following in escapes:
                        chars.append(escapes[following]); index += 2
                        continue
                chars.append(char); index += 1
            if not closed:
                raise EnvImportError(line=number)
            value = ''.join(chars)
        else:
            value = re.split(r'[ \t]+#', raw, maxsplit=1)[0].rstrip()
        if len(value) > 1024 or '\0' in value:
            raise EnvImportError(line=number)
        entries[name] = value
    if not entries:
        raise EnvImportError('env_import_empty')
    return entries


def read_source(source):
    path = Path(source)
    if not path.is_absolute():
        raise EnvImportError()
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        before = os.fstat(fd)
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or before.st_size > 256 * 1024:
            raise EnvImportError()
        data = os.read(fd, 256 * 1024 + 1)
        after = os.fstat(fd)
        identity = lambda info: (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)
        if len(data) != before.st_size or identity(before) != identity(after):
            raise EnvImportError('env_import_changed')
        return data, identity(before), hashlib.sha256(data).hexdigest()
    finally:
        os.close(fd)


def _unique_pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise EnvImportError()
        result[key] = value
    return result


class EnvImport:
    def __init__(self, lifecycle, clock=time.monotonic):
        self.lifecycle, self.clock, self.draft = lifecycle, clock, None

    def clear(self, draft_id=None):
        if draft_id is not None and (not isinstance(draft_id, str) or re.fullmatch(r'[0-9a-f]{64}', draft_id) is None):
            raise EnvImportError()
        if draft_id is None or self.draft and secrets.compare_digest(self.draft['id'], draft_id):
            self.draft = None

    def prepare(self, source):
        with self.lifecycle._mutex:
            self.clear()
            vault = self.lifecycle._require()
            try:
                data, identity, digest = read_source(source)
                entries = parse_env(data)
            except (OSError, UnicodeError) as exc:
                raise EnvImportError() from exc
            existing = {record.service for record in vault.list_credentials()}
            draft_id = secrets.token_hex(32)
            self.draft = dict(id=draft_id, source=source, filename=Path(source).name, entries=entries,
                              identity=identity, digest=digest, expires=self.clock() + 300,
                              vault=vault, key_digest=hashlib.sha256(vault.key).digest())
            return {'draft_id': draft_id, 'filename': Path(source).name, 'expires_in_seconds': 300,
                    'entries': [{'name': name, 'empty': value == '', 'exists': 'desktop-value:' + name in existing}
                                for name, value in entries.items()]}

    def commit(self, draft_id, selection, password):
        with self.lifecycle._mutex:
            if not isinstance(draft_id, str) or re.fullmatch(r'[0-9a-f]{64}', draft_id) is None:
                raise EnvImportError()
            vault = self.lifecycle._require()
            draft = self.draft
            if draft is None or not secrets.compare_digest(draft_id, draft['id']):
                raise EnvImportError('env_import_expired')
            if self.clock() >= draft['expires'] or draft['vault'] is not vault or draft['key_digest'] != hashlib.sha256(vault.key).digest():
                self.clear()
                raise EnvImportError('env_import_expired')
            try:
                selected = json.loads(selection, object_pairs_hook=_unique_pairs)
            except (ValueError, TypeError) as exc:
                raise EnvImportError() from exc
            if not isinstance(selected, dict) or not 1 <= len(selected) <= 128:
                raise EnvImportError('env_import_empty')
            for name, kind in selected.items():
                if name not in draft['entries'] or not isinstance(kind, str) or kind not in {'secret', 'variable'} \
                        or kind == 'secret' and draft['entries'][name] == '':
                    raise EnvImportError()
            try:
                _, identity, digest = read_source(draft['source'])
            except (OSError, EnvImportError) as exc:
                self.clear()
                raise EnvImportError('env_import_changed') from exc
            if identity != draft['identity'] or digest != draft['digest']:
                self.clear()
                raise EnvImportError('env_import_changed')
            entries = {name: (kind, draft['entries'][name]) for name, kind in selected.items()}
            try:
                self.lifecycle.import_owner_values(entries, password, draft['filename'])
            except Exception as exc:
                from .vault import DuplicateCredentialError
                if isinstance(exc, DuplicateCredentialError):
                    raise EnvImportError('env_import_conflict') from exc
                raise
            self.clear()
            return {'imported': len(entries), 'values': self.lifecycle.list_values()}
