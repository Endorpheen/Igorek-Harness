"""Read-only, bounded comparisons for the web changes workbench."""
from __future__ import annotations

import hashlib
import os
import re
import stat
import subprocess
from pathlib import Path
from typing import Any

from .change_file_types import non_text_change_kind
from .session_changes import count_line_changes, is_internal_path
from .session_diff import MAX_DIFF_FILE_BYTES, build_diff_hunks

MAX_FILES = 2000
MAX_UNTRACKED = 500


class ReviewError(ValueError):
    def __init__(self, message: str, code: str = "read_error"):
        super().__init__(message)
        self.code = code


def git_read(root: Path, args: list[str], *, shadow: Path | None = None, input_data: bytes | None = None) -> bytes:
    from .readonly_git import read_git
    try:
        return read_git(root, args, shadow=shadow, input_data=input_data)
    except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
        raise ReviewError("Сравнение недоступно для выбранной базы") from exc


def safe_path(root: Path, path: str) -> Path:
    relative = Path(path)
    if not path or relative.is_absolute() or '..' in relative.parts or is_internal_path(path):
        raise ReviewError("Путь файла вне проекта")
    absolute = root / relative
    try:
        absolute.resolve().relative_to(root.resolve())
    except ValueError as exc:
        raise ReviewError("Путь файла вне проекта") from exc
    return absolute


def current_text(root: Path, path: str) -> str | None:
    safe_path(root, path)
    descriptors = []
    try:
        directory = os.open(root, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
        descriptors.append(directory)
        parts = Path(path).parts
        for part in parts[:-1]:
            directory = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
            descriptors.append(directory)
        fd = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
        descriptors.append(fd)
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_size > MAX_DIFF_FILE_BYTES:
            raise ReviewError("Файл слишком большой или не является обычным текстовым файлом", "too_large" if info.st_size > MAX_DIFF_FILE_BYTES else "not_regular")
        chunks = []; size = 0
        while chunk := os.read(fd, 65536):
            size += len(chunk)
            if size > MAX_DIFF_FILE_BYTES: raise ReviewError("Файл слишком большой", "too_large")
            chunks.append(chunk)
        raw = b''.join(chunks)
        if b'\0' in raw: raise ReviewError("Бинарный файл", "binary")
        return raw.decode('utf-8', errors='replace')
    except FileNotFoundError:
        return None
    finally:
        for fd in reversed(descriptors): os.close(fd)


def blob_text(root: Path, path: str, revision: str, *, shadow: Path | None = None) -> str | None:
    safe_path(root, path)
    spec = revision + ':' + path
    try:
        size = int(git_read(root, ['cat-file', '-s', spec], shadow=shadow))
    except ReviewError:
        return None
    if size > MAX_DIFF_FILE_BYTES:
        raise ReviewError("Файл слишком большой для построчного сравнения", "too_large")
    raw = git_read(root, ['cat-file', 'blob', spec], shadow=shadow)
    if b'\0' in raw:
        raise ReviewError("Бинарный файл", "binary")
    return raw.decode('utf-8', errors='replace')


def checkpoint_repositories(root: Path, data_root: Path, durable: Path | None) -> list[Path]:
    roots = [(data_root, str(root.resolve()))]
    if durable is not None:
        roots.insert(0, (durable, '/workspace'))
        roots.insert(1, (durable, str(root.resolve())))
    found = []
    for base, workdir in roots:
        digest = hashlib.sha256(workdir.encode()).hexdigest()[:16]
        path = base / 'checkpoints' / digest
        if (path / 'HEAD').is_file() and not path.is_symlink():
            found.append(path)
    return found


def checkpoint_text(root: Path, path: str, repositories: list[Path]) -> tuple[bool, str | None]:
    for shadow in repositories:
        try:
            revision = git_read(root, ['rev-parse', '--verify', 'HEAD^{commit}'], shadow=shadow).decode().strip()
            return True, blob_text(root, path, revision, shadow=shadow)
        except ReviewError:
            continue
    return False, None


class ChangesReview:
    def __init__(self, root: Path, *, session_files: list[dict[str, Any]], repositories: list[Path], baseline: str = '', excluded: list[str] | None = None, include_untracked: bool = False):
        self.root = root.resolve()
        self.session_files = session_files
        self.repositories = repositories
        self.baseline = baseline
        self.excluded = excluded or []
        self.include_untracked = include_untracked
        self.excluded_untracked = 0
        self.omitted_by_reason: dict[str, int] = {}
        self.shadow: Path | None = None
        self.revision = ''
        self.empty_baseline = False
        self.mode = 'session'
        self.base_label = ''
        self.refs: list[str] = []
        self.warning = ''
        self.old_texts: dict[str, str | None | ReviewError] = {}

    def prepare(self, mode: str, base: str = '') -> None:
        if mode not in {'session', 'working', 'branch'}:
            raise ReviewError("Неизвестный режим сравнения")
        self.mode = mode
        if mode == 'session':
            for shadow in self.repositories:
                try:
                    if self.baseline and re.fullmatch('[0-9a-f]{40,64}', self.baseline):
                        revision = git_read(self.root, ['rev-parse', '--verify', self.baseline + '^{commit}'], shadow=shadow).decode().strip()
                    else:
                        history = git_read(self.root, ['rev-list', '--parents', '--max-count=10000', 'HEAD'], shadow=shadow).decode().splitlines()
                        revisions = [line.split()[0] for line in history if len(line.split()) > 1]
                        revision = revisions[-1]
                        self.warning = 'Для старой истории база начала сессии не сохранена. Используется самый ранний доступный снимок проекта.'
                    self.shadow = shadow; self.revision = revision
                    self.base_label = 'Снимок ' + revision[:8] + ' → текущие файлы'
                    return
                except (ReviewError, IndexError):
                    continue
            self.base_label = 'Базовая версия отсутствует'
            self.warning = 'История правок сохранена, но точные счётчики и дифф требуют базовой версии.'
            return
        top = git_read(self.root, ['rev-parse', '--show-toplevel']).decode().strip()
        if Path(top).resolve() != self.root:
            raise ReviewError('Для сравнения Git открой сессию в корне репозитория')
        self.refs = git_read(self.root, ['for-each-ref', '--format=%(refname:short)', 'refs/heads/', 'refs/remotes/']).decode().splitlines()[:200]
        if mode == 'working':
            try:
                self.revision = git_read(self.root, ['rev-parse', '--verify', 'HEAD^{commit}']).decode().strip()
                self.base_label = 'Последний коммит HEAD (' + self.revision[:8] + ') → текущая рабочая папка'
            except ReviewError:
                self.empty_baseline = True
                self.base_label = 'Пустой репозиторий → рабочая папка'
        else:
            if not base:
                try: base = git_read(self.root, ['rev-parse', '--abbrev-ref', '@{upstream}']).decode().strip()
                except ReviewError: base = 'HEAD'
            if base != 'HEAD' and base not in self.refs:
                raise ReviewError('Неизвестная ветка сравнения')
            self.revision = git_read(self.root, ['merge-base', 'HEAD', base]).decode().strip()
            self.base_label = 'Общий предок HEAD и ' + base + ' (' + self.revision[:8] + ') → текущая рабочая папка'
            self.base = base

    def pin_revision(self, revision: str) -> None:
        if not revision: return
        if not re.fullmatch('[0-9a-f]{40,64}', revision):
            raise ReviewError('Неверная версия сравнения')
        if self.mode == 'session' and self.shadow is None:
            raise ReviewError('Базовая версия отсутствует')
        self.revision = git_read(self.root, ['rev-parse', '--verify', revision + '^{commit}'], shadow=self.shadow).decode().strip()
        self.empty_baseline = False

    def candidates(self) -> tuple[list[dict[str, Any]], int]:
        if self.mode == 'session':
            files = [f for f in self.session_files if not self.is_excluded(f['path'])]
            self.omitted_by_reason = {'file_limit': max(0, len(files) - MAX_FILES)}
            return files[:MAX_FILES], sum(self.omitted_by_reason.values())
        parts = git_read(self.root, ['diff', '--no-ext-diff', '--no-textconv', '--name-status', '-z', '-M'] + (['--cached'] if self.empty_baseline else [self.revision]) + ['--']).decode('utf-8', errors='replace').split('\0')
        files = []; i = 0
        while i < len(parts) and parts[i]:
            status = parts[i]; i += 1
            old = parts[i]; i += 1
            path = old
            if status.startswith(('R', 'C')):
                path = parts[i]; i += 1
            files.append({'path': path, 'old_path': old, 'status': {'A':'added','D':'deleted','R':'renamed','C':'added'}.get(status[0], 'modified')})
        untracked = git_read(self.root, ['ls-files', '--others', '--exclude-standard', '-z']).decode('utf-8', errors='replace').split('\0')
        untracked = [p for p in untracked if p and not is_internal_path(p) and not self.is_excluded(p)]
        self.excluded_untracked = 0 if self.include_untracked else len(untracked)
        if self.include_untracked:
            tracked_paths = {f['path'] for f in files}
            files += [{'path': p, 'status':'added'} for p in untracked[:MAX_UNTRACKED] if p not in tracked_paths]
        safe = []
        for item in files:
            if self.is_excluded(item['path']): continue
            try: safe_path(self.root, item['path'])
            except ReviewError: continue
            safe.append(item)
        self.omitted_by_reason = {'untracked_limit': max(0, len(untracked) - MAX_UNTRACKED) if self.include_untracked else 0, 'file_limit': max(0, len(safe) - MAX_FILES)}
        return safe[:MAX_FILES], sum(self.omitted_by_reason.values())

    def load_baselines(self, entries: list[dict[str, Any]]) -> None:
        """Read selected blobs in one bounded batch instead of spawning per file."""
        if not self.revision: return
        wanted = {entry.get('old_path') or entry['path'] for entry in entries}
        objects = {}; total = 0
        raw = git_read(self.root, ['ls-tree', '-r', '-l', '-z', self.revision], shadow=self.shadow)
        for record in raw.split(b'\0'):
            if not record: continue
            head, _, name = record.partition(b'\t')
            path = name.decode('utf-8', errors='replace')
            if path not in wanted: continue
            fields = head.split()
            if fields[1] != b'blob':
                self.old_texts[path] = ReviewError('Не текстовый файл', 'not_regular'); continue
            size = int(fields[3])
            if size > MAX_DIFF_FILE_BYTES or total + size > 16_000_000:
                self.old_texts[path] = ReviewError('Файл слишком большой' if size > MAX_DIFF_FILE_BYTES else 'Лимит общего размера базовых версий; открой файл отдельно', 'too_large' if size > MAX_DIFF_FILE_BYTES else 'batch_limit'); continue
            total += size; objects[path] = fields[2]
        if objects:
            content = git_read(self.root, ['cat-file', '--batch'], shadow=self.shadow, input_data=b'\n'.join(objects.values()) + b'\n')
            offset = 0
            for path in objects:
                end = content.index(b'\n', offset)
                size = int(content[offset:end].split()[2]); offset = end + 1
                blob = content[offset:offset+size]; offset += size + 1
                self.old_texts[path] = ReviewError('Бинарный файл', 'binary') if b'\0' in blob else blob.decode('utf-8', errors='replace')
        for path in wanted:
            if path not in self.old_texts: self.old_texts[path] = None

    def is_excluded(self, path: str) -> bool:
        return any(path == prefix or path.startswith(prefix + '/') for prefix in self.excluded)

    def file_data(self, entry: dict[str, Any], *, hunks: bool = False) -> dict[str, Any]:
        result = {**entry, 'additions': None, 'deletions': None, 'source':'unavailable', 'unavailable_reason':'no_baseline', 'hunks':[], 'truncated':False}
        if not self.revision and not self.empty_baseline:
            return result
        if non_text_change_kind(entry['path']) or non_text_change_kind(entry.get('old_path') or ''):
            result['unavailable_reason'] = 'binary'
            result['note'] = 'Нетекстовый файл: сравнение строк неприменимо'
            return result
        try:
            old_path = entry.get('old_path') or entry['path']
            old = None if self.empty_baseline else self.old_texts[old_path] if old_path in self.old_texts else blob_text(self.root, old_path, self.revision, shadow=self.shadow)
            if isinstance(old, ReviewError): raise old
            new = current_text(self.root, entry['path'])
            result['status'] = 'added' if old is None and new is not None else 'deleted' if new is None and old is not None else entry['status']
            result['additions'], result['deletions'] = count_line_changes(old or '', new or '')
            result['source'] = 'checkpoint' if self.shadow else 'git'
            result.pop('unavailable_reason', None)
            if hunks:
                result['hunks'], result['truncated'] = build_diff_hunks(old or '', new or '')
        except (ReviewError, OSError) as exc:
            result['note'] = str(exc)
            result['unavailable_reason'] = exc.code if isinstance(exc, ReviewError) else 'read_error'
        return result

    def result(self, path: str = '') -> dict[str, Any]:
        candidates, omitted = self.candidates()
        if path:
            safe_path(self.root, path)
            if self.is_excluded(path): raise ReviewError("Внутренний файл runtime")
            entry = next((f for f in candidates if f['path'] == path), None)
            if entry is None: raise ReviewError('Файл отсутствует в выбранном сравнении')
            return self.file_data(entry, hunks=True)
        self.load_baselines(candidates)
        files = [self.file_data(entry) for entry in candidates]
        files = [f for f in files if f['source'] == 'unavailable' or f['additions'] or f['deletions'] or f['status'] == 'renamed']
        for file in files: file.pop('hunks', None)
        reasons: dict[str, int] = {}
        for file in files:
            if file['source'] == 'unavailable':
                code = file['unavailable_reason']
                reasons[code] = reasons.get(code, 0) + 1
        unknown = sum(reasons.values())
        return {'files':files, 'totals':{'files':len(files), 'additions':sum(f['additions'] or 0 for f in files), 'deletions':sum(f['deletions'] or 0 for f in files), 'unknown':unknown, 'counted_files':len(files) - unknown, 'unavailable':reasons, 'partial':bool(unknown or omitted)}, 'mode':self.mode, 'revision':self.revision, 'base':getattr(self,'base',''), 'base_label':self.base_label, 'refs':self.refs, 'warning':self.warning, 'omitted':omitted, 'omitted_by_reason':self.omitted_by_reason, 'excluded_untracked':self.excluded_untracked, 'include_untracked':self.include_untracked, 'scope':'session_files' if self.mode == 'session' else 'tracked_and_untracked' if self.include_untracked else 'tracked'}
