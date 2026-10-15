"""Keep untrusted guest descendants from acquiring setuid or file capabilities."""
from __future__ import annotations

import os
import ctypes


def protect_controller() -> None:
    """Refuse same-UID ptrace/proc-memory access to the privileged controller."""
    from .errors import IsolationError
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(4, 0, 0, 0, 0) != 0:
        raise IsolationError('guest_controller_protection_failed', str(ctypes.get_errno()))


def unprivileged_argv(argv, *, shell=False, executable=None):
    if shell:
        arguments = [argv] if isinstance(argv, str) else list(argv)
        command = [executable or '/bin/sh', '-c', *arguments]
    else:
        command = [argv] if isinstance(argv, (str, bytes, os.PathLike)) else list(argv)
        if executable is not None:
            command[0] = executable
    return ['/usr/bin/setpriv', '--no-new-privs', '--inh-caps=-all',
            '--ambient-caps=-all', '--', *command]
