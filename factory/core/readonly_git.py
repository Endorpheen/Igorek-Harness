"""Read Git data with frozen configuration, bounded output and no host authority."""
from __future__ import annotations

from contextlib import ExitStack
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess
import sys
import tempfile

from .artifact_files import open_regular_file, parent_handle, private_io_directory
from .isolation.guest_bwrap import hardened_argv_prefix

OUTPUT_LIMIT = 20_000_000
CONFIG_LIMIT = 1_048_576
SAFE_KEYS = (r'^(core\.(repositoryformatversion|filemode|bare|ignorecase|symlinks)|'
             r'extensions\.(objectformat|refstorage|worktreeconfig)|'
             r'branch\..*\.(remote|merge)|remote\..*\.fetch)$')
_LIMIT_EXEC = (
    'import os,resource,sys; '
    f'resource.setrlimit(resource.RLIMIT_FSIZE,({OUTPUT_LIMIT},{OUTPUT_LIMIT})); '
    'resource.setrlimit(resource.RLIMIT_AS,(536870912,536870912)); '
    'os.execv(sys.argv[1],sys.argv[1:])'
)


def git_environment() -> dict[str, str]:
    env = {'PATH': os.defpath, 'LC_ALL': 'C.UTF-8', 'HOME': '/tmp',
            'GIT_CONFIG_NOSYSTEM': '1', 'GIT_CONFIG_GLOBAL': os.devnull,
            'GIT_TERMINAL_PROMPT': '0', 'GIT_OPTIONAL_LOCKS': '0',
            'GIT_NO_LAZY_FETCH': '1'}
    if os.name == 'nt':
        import ntpath
        root = os.environ.get('SystemRoot') or os.environ.get('SYSTEMROOT')
        if not root or not ntpath.isabs(root):
            raise ValueError('Windows Git requires an absolute system directory')
        env.update(SystemRoot=root, WINDIR=root, COMSPEC=ntpath.join(root, 'System32', 'cmd.exe'),
                   PATH=os.environ.get('PATH', ntpath.join(root, 'System32')),
                   HOME=str(Path.home()), USERPROFILE=str(Path.home()),
                   NoDefaultCurrentDirectoryInExePath='1')
        for name in ('TEMP', 'TMP'):
            if os.environ.get(name):
                env[name] = os.environ[name]
    return env


def _read(path: Path) -> bytes:
    with open_regular_file(path) as stream:
        raw = stream.read(CONFIG_LIMIT + 1)
    if len(raw) > CONFIG_LIMIT:
        raise ValueError('Git metadata exceeds its read budget')
    return raw


def _metadata(root: Path, shadow: Path | None) -> tuple[Path, Path]:
    if shadow is not None:
        actual = shadow.resolve(strict=True)
        return actual, actual
    entry = root / '.git'
    if entry.is_dir() and not entry.is_symlink():
        return entry, entry
    pointer = _read(entry).decode('utf-8').strip()
    if not pointer.startswith('gitdir: '):
        raise ValueError('Invalid worktree registration')
    actual = (root / pointer[8:]).resolve(strict=True)
    common = (actual / _read(actual / 'commondir').decode().strip()).resolve(strict=True)
    back = Path(_read(actual / 'gitdir').decode().strip()).resolve(strict=True)
    if actual.parent != common / 'worktrees' or back != entry:
        raise ValueError('External Git directory is not a registered linked worktree')
    return actual, common


def _configuration(git: str, path: Path) -> list[tuple[str, str]]:
    try:
        raw = _read(path)
    except FileNotFoundError:
        return []
    reply = subprocess.run([git, 'config', '--no-includes', '--file', '-', '-z',
                            '--get-regexp', SAFE_KEYS], input=raw, capture_output=True,
                           cwd='/', env=git_environment(), timeout=2)
    if reply.returncode not in {0, 1}:
        raise ValueError('Invalid repository configuration')
    result = []
    for item in reply.stdout.split(b'\0'):
        if not item:
            continue
        key, separator, value = item.partition(b'\n')
        result.append((key.decode('utf-8'), value.decode('utf-8') if separator else 'true'))
    if len(result) > 512:
        raise ValueError('Repository configuration exceeds its entry budget')
    return result


def _quote(value: str) -> str:
    if any(ord(char) < 32 and char not in '\n\t\b' for char in value):
        raise ValueError('Invalid Git configuration character')
    return '"' + value.replace('\\', '\\\\').replace('"', '\\"').replace(
        '\n', '\\n').replace('\t', '\\t').replace('\b', '\\b') + '"'


def _frozen_config(rows: list[tuple[str, str]]) -> str:
    lines = []
    for key, value in rows:
        if not re.fullmatch(SAFE_KEYS, key, flags=re.I):
            raise ValueError('Unexpected repository configuration key')
        section, _, rest = key.partition('.')
        if section.lower() in {'branch', 'remote'}:
            subsection, _, name = rest.rpartition('.')
            if any(ord(char) < 32 for char in subsection):
                raise ValueError('Invalid Git subsection')
            header = '[' + section + ' ' + _quote(subsection) + ']'
        else:
            name = rest
            if key.lower() == 'extensions.worktreeconfig':
                continue
            header = '[' + section + ']'
        lines += [header, '\t' + name + ' = ' + _quote(value)]
    return '\n'.join(lines) + '\n'


def read_git(root: Path, args: list[str], *, shadow: Path | None = None,
             input_data: bytes | None = None, timeout: float = 15) -> bytes:
    """Never consult executable repository settings or fall back to host Git."""
    root = root.resolve(strict=True)
    git = shutil.which('git', path=os.defpath)
    bwrap = shutil.which('bwrap', path=os.defpath)
    if not git or not bwrap or sys.platform != 'linux':
        raise ValueError('Read-only Git isolation is unavailable')
    actual, common = _metadata(root, shadow)
    rows = _configuration(git, common / 'config')
    if any(key.lower() == 'extensions.worktreeconfig' and value.lower() in
           {'true', 'yes', 'on', '1'} for key, value in rows):
        rows += _configuration(git, actual / 'config.worktree')
    with private_io_directory(root) as private, ExitStack() as stack:
        metadata = private / 'git'
        metadata.mkdir()
        (metadata / 'config').write_text(_frozen_config(rows), encoding='utf-8')
        command = [*hardened_argv_prefix(bwrap), '--unshare-net']
        for path in ('/usr', '/lib', '/lib64', '/bin', '/sbin'):
            if Path(path).exists():
                command += ['--ro-bind', path, path]
        command += ['--proc', '/proc', '--dev', '/dev', '--tmpfs', '/tmp']
        descriptors = []

        def bind(path: Path) -> None:
            parent, name = stack.enter_context(parent_handle(path))
            fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
            stack.callback(os.close, fd)
            if not (stat.S_ISDIR(os.fstat(fd).st_mode) or stat.S_ISREG(os.fstat(fd).st_mode)):
                raise ValueError('Git metadata is not a regular entry')
            descriptors.append(fd)
            command.extend(['--ro-bind', '/proc/self/fd/' + str(fd), str(path)])

        bind(root)
        bind(private)
        (metadata / 'HEAD').write_bytes(_read(actual / 'HEAD'))
        entries = [(actual, 'index')]
        entries += [(common, name) for name in
                    ('objects', 'refs', 'packed-refs', 'shallow', 'reftable', 'info')]
        for path in actual.glob('sharedindex.*'):
            if len(entries) > 512:
                raise ValueError('Split-index metadata exceeds its entry budget')
            entries.append((actual, path.name))
        for source, name in entries:
            target = source / name
            try:
                bind(target)
            except FileNotFoundError:
                continue
            (metadata / name).symlink_to(target)
        command += ['--chdir', str(root), '--', git,
                    '--git-dir=' + str(metadata), '--work-tree=' + str(root),
                    '-c', 'core.fsmonitor=false', '-c', 'core.hooksPath=/dev/null',
                    '-c', 'submodule.recurse=false', '-c', 'core.packedGitWindowSize=16m',
                    '-c', 'core.packedGitLimit=64m']
        if args and args[0] in {'diff', 'status'}:
            args = [args[0], '--ignore-submodules=dirty', *args[1:]]
        if args and args[0] == 'diff':
            args = [args[0], '--no-ext-diff', '--no-textconv', *args[1:]]
        command += args
        with tempfile.TemporaryFile() as output, tempfile.TemporaryFile() as errors:
            reply = subprocess.run([os.path.realpath(sys._base_executable), '-I', '-S',
                                    '-c', _LIMIT_EXEC, *command], cwd='/', env=git_environment(),
                                   input=input_data, stdout=output, stderr=errors,
                                   timeout=timeout, pass_fds=tuple(descriptors), start_new_session=True)
            output.seek(0)
            raw = output.read(OUTPUT_LIMIT + 1)
            if reply.returncode or len(raw) > OUTPUT_LIMIT:
                raise ValueError('Read-only Git failed or exceeded its output budget')
            return raw
