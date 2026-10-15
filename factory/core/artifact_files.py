"""Open workspace artifacts without following links or escaping the root."""
import os
import stat
import secrets
import tempfile
from contextlib import contextmanager
from pathlib import Path
from typing import BinaryIO


@contextmanager
def private_io_directory(workdir: Path):
    """Keep intermediate file-tool data outside the task's writable roots."""
    roots = [workdir.resolve()]
    if os.environ.get('OBSIDIAN_VAULT_PATH'):
        roots.append(Path(os.environ['OBSIDIAN_VAULT_PATH']).expanduser().resolve())
    staging = Path(tempfile.gettempdir())
    if any(staging.is_relative_to(root) for root in roots):
        staging = Path.home() / '.cache/igorek-tool-files'
    staging = staging.resolve()
    if any(staging.is_relative_to(root) for root in roots):
        raise ValueError('No private staging directory outside the task write grant')
    if os.name == 'nt':
        from .platform_support.windows_files import pinned_parent
        with pinned_parent(staging / '.entry', create=True):
            pass
    else:
        with parent_handle(staging / '.entry', create=True):
            pass
    with tempfile.TemporaryDirectory(prefix='igorek-io-', dir=staging) as temporary:
        yield Path(temporary)


@contextmanager
def parent_handle(path: str | Path, *, create: bool = False):
    """Anchor an already authorized absolute path without following new links."""
    path = Path(path)
    if not path.is_absolute() or path.name in {"", ".", ".."} or ".." in path.parts:
        raise ValueError("Expected an authorized absolute file path")
    directory = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in path.parts[1:-1]:
            if create:
                try:
                    os.mkdir(part, dir_fd=directory)
                except FileExistsError:
                    pass
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
            os.close(directory)
            directory = child
        yield directory, path.name
    finally:
        os.close(directory)


def open_regular_file(path: str | Path) -> BinaryIO:
    """Open a validated canonical file, rejecting raced links and special files."""
    if os.name == 'nt':
        from .platform_support.windows_files import open_regular_file as windows_open
        return windows_open(path)
    with parent_handle(path) as (directory, name):
        descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise ValueError("Only regular files can be read")
        return os.fdopen(descriptor, "rb")
    except BaseException:
        os.close(descriptor)
        raise


def atomic_write_file(path: str | Path, content: str | bytes | BinaryIO, *, mode: int = 0o666,
                      preserve_mode: bool = True, preserve_owner: bool = False) -> None:
    """Replace one authorized entry through its parent descriptor, never its link."""
    if os.name == 'nt':
        from .platform_support.windows_files import atomic_write_file as windows_write
        return windows_write(path, content, mode=mode, preserve_mode=preserve_mode, preserve_owner=preserve_owner)
    data = None if hasattr(content, 'read') else content.encode("utf-8") if isinstance(content, str) else bytes(content)
    with parent_handle(path, create=True) as (directory, name):
        try:
            current = os.stat(name, dir_fd=directory, follow_symlinks=False)
        except FileNotFoundError:
            current = None
        if current is not None and not stat.S_ISREG(current.st_mode):
            raise ValueError("Output entry is not a regular file")
        temporary = ".igorek-write-" + secrets.token_hex(16)
        descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                             mode, dir_fd=directory)
        try:
            with os.fdopen(descriptor, "wb") as stream:
                if preserve_mode and current is not None:
                    os.fchmod(stream.fileno(), stat.S_IMODE(current.st_mode))
                if preserve_owner and current is not None and (current.st_uid, current.st_gid) != (os.getuid(), os.getgid()):
                    os.fchown(stream.fileno(), current.st_uid, current.st_gid)
                if data is None:
                    while chunk := content.read(65536):
                        stream.write(chunk)
                else:
                    stream.write(data)
                stream.flush()
            os.replace(temporary, name, src_dir_fd=directory, dst_dir_fd=directory)
        finally:
            try:
                os.unlink(temporary, dir_fd=directory)
            except FileNotFoundError:
                pass


def read_regular_text(path: str | Path, *, max_bytes: int = 8 * 1024 * 1024,
                      errors: str = "strict") -> str:
    with open_regular_file(path) as stream:
        data = stream.read(max_bytes + 1)
    if len(data) > max_bytes:
        raise ValueError("File exceeds the bounded text-read limit")
    return data.decode("utf-8", errors=errors).replace("\r\n", "\n").replace("\r", "\n")


def open_workspace_artifact(root: Path, relative_path: str) -> BinaryIO:
    parts = relative_path.split("/")
    if any(part in {"", ".", ".."} or "\\" in part or "\x00" in part for part in parts):
        raise ValueError("Invalid file path")
    trusted_root = root.expanduser().resolve(strict=True)
    if os.name == 'nt':
        return open_regular_file(trusted_root.joinpath(*parts))
    directory = os.open("/", os.O_RDONLY | os.O_DIRECTORY)
    try:
        for part in (*trusted_root.parts[1:], *parts[:-1]):
            child = os.open(part, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=directory)
            os.close(directory)
            directory = child
        descriptor = os.open(parts[-1], os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory)
    finally:
        os.close(directory)
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            raise ValueError("Invalid file path")
        return os.fdopen(descriptor, "rb")
    except BaseException:
        os.close(descriptor)
        raise
