"""Operating-system implementations behind shared runtime contracts."""

import os
from pathlib import Path


class LockBusyError(BlockingIOError):
    """Another live process holds the lease; other filesystem errors stay distinct."""


def exclusive_lock(path: Path, *, blocking=False) -> int:
    """Acquire a nonblocking process-owned lease; closing its fd releases it."""
    if os.name == 'nt':
        from .windows_files import open_lock
        try:
            return open_lock(path, blocking=blocking)
        except OSError as error:
            if getattr(error, 'winerror', None) == 32:
                raise LockBusyError('Another process holds the file lease') from error
            raise
    import fcntl
    descriptor = os.open(path, os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
    try:
        fcntl.flock(descriptor, fcntl.LOCK_EX | (0 if blocking else fcntl.LOCK_NB))
    except BlockingIOError as error:
        os.close(descriptor)
        raise LockBusyError('Another process holds the file lease') from error
    except BaseException:
        os.close(descriptor)
        raise
    return descriptor


def require_linux(feature: str) -> None:
    """Reject a platform-specific operation before accessing host resources."""
    import sys
    if sys.platform != 'linux':
        raise OSError(f'{feature}_unsupported_platform')
