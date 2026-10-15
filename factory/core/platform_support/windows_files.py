"""Handle-pinned Windows file access rejecting junctions and other reparse points."""

import ctypes
from ctypes import wintypes
from contextlib import contextmanager
import msvcrt
import os
from pathlib import Path, PureWindowsPath
import secrets


_kernel = ctypes.WinDLL('kernel32', use_last_error=True)
_create = _kernel.CreateFileW
_create.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, ctypes.c_void_p,
                   wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
_create.restype = wintypes.HANDLE
_close = _kernel.CloseHandle
_close.argtypes = [wintypes.HANDLE]
_close.restype = wintypes.BOOL
_attributes = _kernel.GetFileInformationByHandle
_attributes.argtypes = [wintypes.HANDLE, ctypes.c_void_p]
_attributes.restype = wintypes.BOOL
_file_type = _kernel.GetFileType
_file_type.argtypes = [wintypes.HANDLE]
_file_type.restype = wintypes.DWORD
_INVALID = ctypes.c_void_p(-1).value
_REPARSE = 0x400
_DIRECTORY = 0x10
_OPEN_FLAGS = 0x00200000 | 0x02000000  # OPEN_REPARSE_POINT | BACKUP_SEMANTICS


class _Information(ctypes.Structure):
    _fields_ = [('attributes', wintypes.DWORD), ('creation', wintypes.FILETIME),
                ('access', wintypes.FILETIME), ('write', wintypes.FILETIME),
                ('volume', wintypes.DWORD), ('size_high', wintypes.DWORD),
                ('size_low', wintypes.DWORD), ('links', wintypes.DWORD),
                ('index_high', wintypes.DWORD), ('index_low', wintypes.DWORD)]


def _path(value):
    path = PureWindowsPath(value)
    if not path.is_absolute() or len(path.drive) != 2 or path.drive[1] != ':':
        raise ValueError('Only absolute local Windows paths are supported')
    for part in path.parts[1:]:
        if (part in {'.', '..'} or part.endswith((' ', '.')) or ':' in part
                or any(character in part for character in '\x00<>"|?*') or os.path.isreserved(part)):
            raise ValueError('Invalid Windows file path')
    return Path(path)


def _open(path, *, access=0x80, share=3, disposition=3, directory=False):
    if directory:
        # Attribute-only handles do not participate in Windows delete sharing.
        access |= 0x1  # FILE_LIST_DIRECTORY pins the directory against rename.
    handle = _create(str(path), access, share, None, disposition, _OPEN_FLAGS, None)
    if handle == _INVALID:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        info = _Information()
        if not _attributes(handle, ctypes.byref(info)):
            raise ctypes.WinError(ctypes.get_last_error())
        if (info.attributes & _REPARSE or bool(info.attributes & _DIRECTORY) != directory
                or _file_type(handle) != 1):
            raise ValueError('Reparse points and special files are not allowed')
        return handle
    except BaseException:
        _close(handle)
        raise


@contextmanager
def pinned_parent(value, *, create=False):
    """Deny deletion/rename of every parent until the file operation finishes."""
    path = _path(value)
    if path == Path(path.anchor):
        raise ValueError('A file name is required')
    handles = []
    try:
        current = Path(path.anchor)
        handles.append(_open(current, directory=True))
        for part in path.parts[1:-1]:
            current /= part
            if create:
                try:
                    current.mkdir()
                except FileExistsError:
                    pass
            handles.append(_open(current, directory=True))
        yield path
    finally:
        for handle in reversed(handles):
            _close(handle)


def _descriptor(handle, flags):
    try:
        return msvcrt.open_osfhandle(handle, flags | os.O_BINARY)
    except BaseException:
        _close(handle)
        raise


def open_regular_file(value):
    with pinned_parent(value) as path:
        handle = _open(path, access=0x80000000, share=1)
        descriptor = _descriptor(handle, os.O_RDONLY)
    return os.fdopen(descriptor, 'rb')


def open_lock(value, *, blocking=False):
    with pinned_parent(value, create=True) as path:
        while True:
            try:
                handle = _open(path, access=0xC0000000, share=0, disposition=4)
                break
            except OSError as error:
                if not blocking or error.winerror != 32:
                    raise
                import time
                time.sleep(0.05)
        return _descriptor(handle, os.O_RDWR)


def atomic_write_file(value, content, **options):
    """Replace a directory entry while its complete parent chain stays pinned."""
    data = None if hasattr(content, 'read') else content.encode('utf-8') if isinstance(content, str) else bytes(content)
    with pinned_parent(value, create=True) as path:
        existing = None
        try:
            existing = _open(path, access=0x20080)
        except FileNotFoundError:
            pass
        temporary = path.with_name('.igorek-write-' + secrets.token_hex(16))
        try:
            handle = _open(temporary, access=0x40040000 | (0x80000 if options.get('preserve_owner') else 0), share=0, disposition=1)
            with os.fdopen(_descriptor(handle, os.O_WRONLY), 'wb') as stream:
                if existing is not None and options.get('preserve_mode', True):
                    from .windows_security import copy_file_security
                    copy_file_security(existing, msvcrt.get_osfhandle(stream.fileno()), owner=options.get('preserve_owner', False))
                if data is None:
                    while chunk := content.read(65536):
                        stream.write(chunk)
                else:
                    stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            if existing is not None:
                _close(existing)
                existing = None
            os.replace(temporary, path)
        finally:
            if existing is not None:
                _close(existing)
            temporary.unlink(missing_ok=True)
