"""Protect desktop data with an inheritable owner-and-SYSTEM Windows DACL."""

import ctypes
from ctypes import wintypes

from .windows_files import _open, _close, _kernel, pinned_parent

_security = ctypes.WinDLL('advapi32', use_last_error=True)
_token_open = _security.OpenProcessToken
_token_open.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE)]
_token_open.restype = wintypes.BOOL
_token_info = _security.GetTokenInformation
_token_info.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD, ctypes.POINTER(wintypes.DWORD)]
_token_info.restype = wintypes.BOOL
_sid_text = _security.ConvertSidToStringSidW
_sid_text.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.LPWSTR)]
_sid_text.restype = wintypes.BOOL
_convert = _security.ConvertStringSecurityDescriptorToSecurityDescriptorW
_convert.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p]
_convert.restype = wintypes.BOOL
_get_security = _security.GetSecurityInfo
_get_security.argtypes = [wintypes.HANDLE, ctypes.c_int, wintypes.DWORD,
    ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p,
    ctypes.POINTER(ctypes.c_void_p)]
_get_security.restype = wintypes.DWORD
_equal = _security.EqualSid
_equal.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
_equal.restype = wintypes.BOOL
_set_security = _security.SetSecurityInfo
_set_security.argtypes = [wintypes.HANDLE, ctypes.c_int, wintypes.DWORD,
                         ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]
_set_security.restype = wintypes.DWORD
_kernel.GetCurrentProcess.restype = wintypes.HANDLE
_kernel.LocalFree.argtypes = [ctypes.c_void_p]
_kernel.LocalFree.restype = ctypes.c_void_p


def copy_file_security(source, destination, *, owner=False):
    """Preserve an existing file's DACL before atomically replacing its entry."""
    descriptor = ctypes.c_void_p(); dacl = ctypes.c_void_p()
    user = ctypes.c_void_p(); group = ctypes.c_void_p()
    get = _security.GetSecurityInfo
    get.argtypes = [wintypes.HANDLE, ctypes.c_int, wintypes.DWORD,
        ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_void_p), ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
    set_info = _security.SetSecurityInfo
    set_info.argtypes = [wintypes.HANDLE, ctypes.c_int, wintypes.DWORD,
                         ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]
    set_info.restype = wintypes.DWORD
    control = _security.GetSecurityDescriptorControl
    control.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.WORD), ctypes.POINTER(wintypes.DWORD)]
    control.restype = wintypes.BOOL
    information = 7 if owner else 4
    error = get(source, 1, information, ctypes.byref(user), ctypes.byref(group), ctypes.byref(dacl), None, ctypes.byref(descriptor))
    if error:
        raise ctypes.WinError(error)
    try:
        flags = wintypes.WORD(); revision = wintypes.DWORD()
        if not control(descriptor, ctypes.byref(flags), ctypes.byref(revision)):
            raise ctypes.WinError(ctypes.get_last_error())
        information |= 0x80000000 if flags.value & 0x1000 else 0x20000000
        error = set_info(destination, 1, information, user if owner else None,
                         group if owner else None, dacl, None)
        if error:
            raise ctypes.WinError(error)
    finally:
        _kernel.LocalFree(descriptor)


def read_dacl(path, *, directory=False):
    """Read the actual DACL as SDDL for acceptance and support diagnostics."""
    convert = _security.ConvertSecurityDescriptorToStringSecurityDescriptorW
    convert.argtypes = [ctypes.c_void_p, wintypes.DWORD, wintypes.DWORD,
                        ctypes.POINTER(wintypes.LPWSTR), ctypes.c_void_p]
    convert.restype = wintypes.BOOL
    descriptor = ctypes.c_void_p(); text = wintypes.LPWSTR()
    with pinned_parent(path) as canonical:
        handle = _open(canonical, access=0x20080, directory=directory)
        try:
            error = _get_security(handle, 1, 4, None, None, None, None, ctypes.byref(descriptor))
            if error:
                raise ctypes.WinError(error)
            if not convert(descriptor, 1, 4, ctypes.byref(text), None):
                raise ctypes.WinError(ctypes.get_last_error())
            return text.value
        finally:
            for pointer in (descriptor, text):
                if pointer:
                    _kernel.LocalFree(pointer)
            _close(handle)


def protect_directory(path):
    """Reject foreign-owned/reparse roots before setting their inheritable DACL."""
    token = wintypes.HANDLE()
    if not _token_open(_kernel.GetCurrentProcess(), 8, ctypes.byref(token)):
        raise ctypes.WinError(ctypes.get_last_error())
    text = wintypes.LPWSTR()
    descriptor = ctypes.c_void_p()
    owner_descriptor = ctypes.c_void_p()
    handle = None
    try:
        needed = wintypes.DWORD()
        _token_info(token, 1, None, 0, ctypes.byref(needed))
        buffer = ctypes.create_string_buffer(needed.value)
        if not _token_info(token, 1, buffer, needed.value, ctypes.byref(needed)):
            raise ctypes.WinError(ctypes.get_last_error())
        sid = ctypes.c_void_p.from_buffer(buffer).value
        owner_size = wintypes.DWORD()
        _token_info(token, 4, None, 0, ctypes.byref(owner_size))
        owner_buffer = ctypes.create_string_buffer(owner_size.value)
        if not _token_info(token, 4, owner_buffer, owner_size.value, ctypes.byref(owner_size)):
            raise ctypes.WinError(ctypes.get_last_error())
        default_owner = ctypes.c_void_p.from_buffer(owner_buffer).value
        if not _sid_text(sid, ctypes.byref(text)):
            raise ctypes.WinError(ctypes.get_last_error())
        sddl = f'D:P(A;OICI;FA;;;{text.value})(A;OICI;FA;;;SY)'
        if not _convert(sddl, 1, ctypes.byref(descriptor), None):
            raise ctypes.WinError(ctypes.get_last_error())
        with pinned_parent(path) as directory:
            handle = _open(directory, access=0xE0080, directory=True)
            owner = ctypes.c_void_p()
            error = _get_security(handle, 1, 1, ctypes.byref(owner), None, None, None,
                                  ctypes.byref(owner_descriptor))
            if error:
                raise ctypes.WinError(error)
            if not _equal(owner, sid) and not _equal(owner, default_owner):
                raise PermissionError('Desktop data must belong to the current Windows user')
            get_dacl = _security.GetSecurityDescriptorDacl
            get_dacl.argtypes = [ctypes.c_void_p, ctypes.POINTER(wintypes.BOOL),
                                ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(wintypes.BOOL)]
            get_dacl.restype = wintypes.BOOL
            present = wintypes.BOOL(); inherited = wintypes.BOOL(); dacl = ctypes.c_void_p()
            if not get_dacl(descriptor, ctypes.byref(present), ctypes.byref(dacl), ctypes.byref(inherited)) or not present or not dacl:
                raise ValueError('A private DACL is required')
            error = _set_security(handle, 1, 0x80000005, sid, None, dacl, None)
            if error:
                raise ctypes.WinError(error)
    finally:
        if handle is not None:
            _close(handle)
        for pointer in (text, descriptor, owner_descriptor):
            if pointer:
                _kernel.LocalFree(pointer)
        _close(token)
