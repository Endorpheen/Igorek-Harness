"""Run a Windows child with a restricted token, private desktop and process job."""
from __future__ import annotations

import ctypes
from ctypes import wintypes as W
import os
from pathlib import Path
import secrets
import subprocess


class Sid(ctypes.Structure):
    _fields_ = [('pointer', ctypes.c_void_p), ('attributes', W.DWORD)]


class SecurityAttributes(ctypes.Structure):
    _fields_ = [('length', W.DWORD), ('descriptor', ctypes.c_void_p), ('inherit', W.BOOL)]


class Startup(ctypes.Structure):
    _fields_ = [('cb', W.DWORD), ('reserved', W.LPWSTR), ('desktop', W.LPWSTR), ('title', W.LPWSTR),
                ('x', W.DWORD), ('y', W.DWORD), ('width', W.DWORD), ('height', W.DWORD),
                ('xchars', W.DWORD), ('ychars', W.DWORD), ('fill', W.DWORD), ('flags', W.DWORD),
                ('show', W.WORD), ('reserved_size', W.WORD), ('reserved_bytes', ctypes.c_void_p),
                ('stdin', W.HANDLE), ('stdout', W.HANDLE), ('stderr', W.HANDLE)]


class StartupEx(ctypes.Structure):
    _fields_ = [('startup', Startup), ('attributes', ctypes.c_void_p)]


class ProcessInfo(ctypes.Structure):
    _fields_ = [('process', W.HANDLE), ('thread', W.HANDLE), ('pid', W.DWORD), ('tid', W.DWORD)]


class JobBasic(ctypes.Structure):
    _fields_ = [('process_time', ctypes.c_int64), ('job_time', ctypes.c_int64), ('flags', W.DWORD),
                ('minimum_working_set', ctypes.c_size_t), ('maximum_working_set', ctypes.c_size_t),
                ('active_processes', W.DWORD), ('affinity', ctypes.c_size_t), ('priority', W.DWORD), ('scheduling', W.DWORD)]


class JobExtended(ctypes.Structure):
    _fields_ = [('basic', JobBasic), ('io', ctypes.c_uint64 * 6),
                ('process_memory', ctypes.c_size_t), ('job_memory', ctypes.c_size_t),
                ('peak_process_memory', ctypes.c_size_t), ('peak_job_memory', ctypes.c_size_t)]


class Trustee(ctypes.Structure):
    _fields_ = [('multiple', ctypes.c_void_p), ('operation', ctypes.c_int),
                ('form', ctypes.c_int), ('kind', ctypes.c_int), ('name', ctypes.c_void_p)]


class ExplicitAccess(ctypes.Structure):
    _fields_ = [('permissions', W.DWORD), ('mode', ctypes.c_int), ('inheritance', W.DWORD), ('trustee', Trustee)]


class WindowsSandbox:
    """Keep assignment writes disposable and revoke explicit manual-project grants."""

    def __init__(self, root: Path, *, readable=(), writable=(), manual_projects=()):
        if os.name != 'nt':
            raise OSError('windows_sandbox_requires_windows')
        self.root = Path(root).resolve(strict=True)
        self.readable = [Path(item).resolve(strict=True) for item in readable]
        self.writable = [Path(item).resolve(strict=True) for item in writable]
        self.manual_projects = [Path(item).resolve(strict=True) for item in manual_projects]
        if any(not item.is_relative_to(self.root) for item in self.writable + self.readable):
            raise ValueError('windows_sandbox_writes_must_be_disposable')
        self.kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        self.security = ctypes.WinDLL('advapi32', use_last_error=True)
        self.user = ctypes.WinDLL('user32', use_last_error=True)
        self.handles = []
        self.allocations = []
        self.transit = []
        self.process = None
        self.thread = None
        self.desktop = None
        self.station = None
        self.job = None
        self._bind()
        self.capability = 'S-1-5-21-' + '-'.join(str(secrets.randbits(30) + 1) for _ in range(4))
        try:
            self._token()
            self._transit_grants()
            self._grant(self.root, writable=False)
            for item in self.readable:
                self._grant(item, writable=False)
            for item in self.writable:
                self._grant(item, writable=True)
            for item in self.manual_projects:
                self._manual_grant(item)
            self._job()
            self._desktop()
        except BaseException:
            self.close()
            raise

    def _bind(self):
        def bind(library, name, result, args):
            function = getattr(library, name)
            function.restype, function.argtypes = result, args
            return function
        H, D, P, B = W.HANDLE, W.DWORD, ctypes.c_void_p, W.BOOL
        self.open_token = bind(self.security, 'OpenProcessToken', B, [H, D, ctypes.POINTER(H)])
        self.get_token = bind(self.security, 'GetTokenInformation', B, [H, ctypes.c_int, P, D, ctypes.POINTER(D)])
        self.set_token = bind(self.security, 'SetTokenInformation', B, [H, ctypes.c_int, P, D])
        self.restrict = bind(self.security, 'CreateRestrictedToken', B,
            [H, D, D, P, D, P, D, P, ctypes.POINTER(H)])
        self.sid_from_text = bind(self.security, 'ConvertStringSidToSidW', B, [W.LPCWSTR, ctypes.POINTER(P)])
        self.sid_to_text = bind(self.security, 'ConvertSidToStringSidW', B, [P, ctypes.POINTER(W.LPWSTR)])
        self.sid_length = bind(self.security, 'GetLengthSid', D, [P])
        self.descriptor_from_text = bind(self.security, 'ConvertStringSecurityDescriptorToSecurityDescriptorW', B,
            [W.LPCWSTR, D, ctypes.POINTER(P), P])
        self.get_dacl = bind(self.security, 'GetSecurityDescriptorDacl', B,
            [P, ctypes.POINTER(B), ctypes.POINTER(P), ctypes.POINTER(B)])
        self.get_sacl = bind(self.security, 'GetSecurityDescriptorSacl', B,
            [P, ctypes.POINTER(B), ctypes.POINTER(P), ctypes.POINTER(B)])
        self.set_named_security = bind(self.security, 'SetNamedSecurityInfoW', D, [W.LPWSTR, ctypes.c_int, D, P, P, P, P])
        self.set_security = bind(self.security, 'SetSecurityInfo', D, [H, ctypes.c_int, D, P, P, P, P])
        self.get_named_security = bind(self.security, 'GetNamedSecurityInfoW', D,
            [W.LPCWSTR, ctypes.c_int, D, P, P, ctypes.POINTER(P), P, ctypes.POINTER(P)])
        self.merge_acl = bind(self.security, 'SetEntriesInAclW', D, [D, ctypes.POINTER(ExplicitAccess), P, ctypes.POINTER(P)])
        self.create_process = bind(self.security, 'CreateProcessAsUserW', B,
            [H, W.LPCWSTR, W.LPWSTR, P, P, B, D, P, W.LPCWSTR, ctypes.POINTER(StartupEx), ctypes.POINTER(ProcessInfo)])
        self.impersonate = bind(self.security, 'ImpersonateLoggedOnUser', B, [H])
        self.revert = bind(self.security, 'RevertToSelf', B, [])
        self.open_file = bind(self.kernel, 'CreateFileW', H, [W.LPCWSTR, D, D, P, D, D, H])
        self.current_process = bind(self.kernel, 'GetCurrentProcess', H, [])
        self.close_handle = bind(self.kernel, 'CloseHandle', B, [H])
        self.local_free = bind(self.kernel, 'LocalFree', P, [P])
        self.create_job = bind(self.kernel, 'CreateJobObjectW', H, [P, W.LPCWSTR])
        self.set_job = bind(self.kernel, 'SetInformationJobObject', B, [H, ctypes.c_int, P, D])
        self.assign_job = bind(self.kernel, 'AssignProcessToJobObject', B, [H, H])
        self.terminate_job = bind(self.kernel, 'TerminateJobObject', B, [H, W.UINT])
        self.wait = bind(self.kernel, 'WaitForSingleObject', D, [H, D])
        self.exit_code = bind(self.kernel, 'GetExitCodeProcess', B, [H, ctypes.POINTER(D)])
        self.resume = bind(self.kernel, 'ResumeThread', D, [H])
        self.terminate_process = bind(self.kernel, 'TerminateProcess', B, [H, W.UINT])
        self.attribute_init = bind(self.kernel, 'InitializeProcThreadAttributeList', B,
            [P, D, D, ctypes.POINTER(ctypes.c_size_t)])
        self.attribute_update = bind(self.kernel, 'UpdateProcThreadAttribute', B,
            [P, D, ctypes.c_size_t, P, ctypes.c_size_t, P, P])
        self.attribute_delete = bind(self.kernel, 'DeleteProcThreadAttributeList', None, [P])
        self.create_desktop = bind(self.user, 'CreateDesktopW', H, [W.LPCWSTR, P, P, D, D, ctypes.POINTER(SecurityAttributes)])
        self.close_desktop = bind(self.user, 'CloseDesktop', B, [H])
        self.create_station = bind(self.user, 'CreateWindowStationW', H, [W.LPCWSTR, D, D, ctypes.POINTER(SecurityAttributes)])
        self.get_station = bind(self.user, 'GetProcessWindowStation', H, [])
        self.set_station = bind(self.user, 'SetProcessWindowStation', B, [H])
        self.close_station = bind(self.user, 'CloseWindowStation', B, [H])

    @staticmethod
    def _check(value):
        if not value:
            raise ctypes.WinError(ctypes.get_last_error())
        return value

    def _sid(self, text):
        pointer = ctypes.c_void_p()
        self._check(self.sid_from_text(text, ctypes.byref(pointer)))
        self.allocations.append(pointer)
        return pointer

    def _sid_text(self, pointer):
        text = W.LPWSTR()
        self._check(self.sid_to_text(pointer, ctypes.byref(text)))
        try:
            return text.value
        finally:
            self.local_free(text)

    def _descriptor(self, text):
        pointer = ctypes.c_void_p()
        self._check(self.descriptor_from_text(text, 1, ctypes.byref(pointer), None))
        self.allocations.append(pointer)
        return pointer

    def _acl(self, descriptor, *, label=False):
        present, inherited, acl = W.BOOL(), W.BOOL(), ctypes.c_void_p()
        self._check((self.get_sacl if label else self.get_dacl)(descriptor, ctypes.byref(present), ctypes.byref(acl), ctypes.byref(inherited)))
        if not present or not acl:
            raise ValueError('windows_sandbox_acl_required')
        return acl

    def _token(self):
        original = W.HANDLE()
        self._check(self.open_token(self.current_process(), 0x8B, ctypes.byref(original)))
        self.handles.append(original)
        needed = W.DWORD()
        self.get_token(original, 1, None, 0, ctypes.byref(needed))
        user_buffer = ctypes.create_string_buffer(needed.value)
        self._check(self.get_token(original, 1, user_buffer, needed.value, ctypes.byref(needed)))
        self.owner = self._sid_text(ctypes.c_void_p.from_buffer(user_buffer))
        entries = (Sid * 4)(*[Sid(self._sid(text), 0) for text in
                             [self.capability, 'S-1-1-0', 'S-1-5-12', 'S-1-5-32-545']])
        disabled = (Sid * 1)(Sid(self._sid('S-1-5-32-544'), 0))
        token = W.HANDLE()
        self._check(self.restrict(original, 1, len(disabled), disabled, 0, None, len(entries), entries, ctypes.byref(token)))
        self.handles.append(token)
        self.token = token
        label = Sid(self._sid('S-1-16-8192' if self.manual_projects else 'S-1-16-4096'), 0x20)
        length = self.sid_length(label.pointer)
        self._check(self.set_token(token, 25, ctypes.byref(label), ctypes.sizeof(label) + length))
        owner = self._sid(self.owner)
        self._check(self.set_token(token, 4, ctypes.byref(owner), ctypes.sizeof(owner)))
        descriptor = self._descriptor('D:(A;;GA;;;SY)(A;;GA;;;' + self.owner + ')(A;;GA;;;' + self.capability + ')')
        dacl = ctypes.c_void_p(self._acl(descriptor).value)
        self._check(self.set_token(token, 6, ctypes.byref(dacl), ctypes.sizeof(dacl)))

    def _grant(self, root, *, writable):
        root = Path(root).resolve(strict=True)
        if writable and not root.is_relative_to(self.root):
            raise ValueError('windows_sandbox_write_root_escape')
        values = self._paths(root)
        descriptor = self._descriptor('D:P(A;OICI;FA;;;SY)(A;OICI;FA;;;' + self.owner + ')(A;OICI;' +
                                      ('0x1301bf' if writable else '0x1200a9') + ';;;' + self.capability + ')')
        acl = self._acl(descriptor)
        label = self._acl(self._descriptor('S:(ML;OICI;NW;;;LW)'), label=True) if writable else None
        owner = self._sid(self.owner)
        for path in values:
            error = self.set_named_security(str(path), 1, 0x80000005, owner, None, acl, None)
            if error:
                raise ctypes.WinError(error)
            if writable:
                error = self.set_named_security(str(path), 1, 0x10, None, None, None, label)
                if error:
                    raise ctypes.WinError(error)

    def _path_grant(self, path, permissions, inheritance, *, revoke=False):
        descriptor, existing, updated = ctypes.c_void_p(), ctypes.c_void_p(), ctypes.c_void_p()
        error = self.get_named_security(str(path), 1, 4, None, None, ctypes.byref(existing), None, ctypes.byref(descriptor))
        if error:
            if revoke:
                return
            raise ctypes.WinError(error)
        try:
            entry = ExplicitAccess(0 if revoke else permissions, 4 if revoke else 1, inheritance,
                                   Trustee(None, 0, 0, 0, self._sid(self.capability)))
            error = self.merge_acl(1, ctypes.byref(entry), existing, ctypes.byref(updated))
            if not error:
                error = self.set_named_security(str(path), 1, 4, None, None, updated, None)
            if error and not revoke:
                raise ctypes.WinError(error)
        finally:
            for pointer in (descriptor, updated):
                if pointer:
                    self.local_free(pointer)

    def _metadata_access(self, path):
        self._check(self.impersonate(self.token))
        try:
            handle = self.open_file(str(path), 0x80, 7, None, 3, 0x02200000, None)
            if handle == ctypes.c_void_p(-1).value:
                error = ctypes.get_last_error()
                if error == 5:
                    return False
                raise ctypes.WinError(error)
            self.close_handle(handle)
            return True
        finally:
            self._check(self.revert())

    def _transit_grants(self):
        paths = dict.fromkeys(path for root in [self.root, *self.manual_projects] for path in reversed(root.parents))
        for path in paths:
            if self._metadata_access(path):
                continue
            self._path_grant(path, 0x1000A0, 0)
            self.transit.append(path)
            if not self._metadata_access(path):
                raise PermissionError('windows_sandbox_ancestor_metadata_denied:' + path.name)

    def _manual_grant(self, root, *, revoke=False):
        for path in self._paths(Path(root), ignore_reparse=revoke):
            self._path_grant(path, 0x1301BF, 3, revoke=revoke)

    @staticmethod
    def _paths(root, *, ignore_reparse=False):
        pending = [Path(root)]
        while pending:
            path = pending.pop()
            if path.is_symlink() or path.is_junction():
                if ignore_reparse:
                    continue
                raise ValueError('windows_sandbox_reparse_point')
            yield path
            if path.is_dir():
                pending.extend(path.iterdir())

    def _job(self):
        self.job = self._check(self.create_job(None, None))
        self.handles.append(self.job)
        limits = JobExtended()
        limits.basic.flags = 0x2000
        self._check(self.set_job(self.job, 9, ctypes.byref(limits), ctypes.sizeof(limits)))

    def _desktop(self):
        self.station_name = 'igorek-station-' + secrets.token_hex(12)
        self.desktop_name = 'igorek-desktop-' + secrets.token_hex(12)
        descriptor = self._descriptor('D:(A;;GA;;;SY)(A;;GA;;;' + self.owner + ')(A;;GA;;;' + self.capability + ')')
        attributes = SecurityAttributes(ctypes.sizeof(SecurityAttributes), descriptor, False)
        self.station = self._check(self.create_station(self.station_name, 0, 0x10000000, ctypes.byref(attributes)))
        label = self._acl(self._descriptor('S:(ML;;NW;;;LW)'), label=True)
        error = self.set_security(self.station, 7, 0x10, None, None, None, label)
        if error:
            raise ctypes.WinError(error)
        original = self._check(self.get_station())
        self._check(self.set_station(self.station))
        try:
            self.desktop = self._check(self.create_desktop(self.desktop_name, None, None, 0, 0x10000000, ctypes.byref(attributes)))
            error = self.set_security(self.desktop, 7, 0x10, None, None, None, label)
            if error:
                raise ctypes.WinError(error)
        finally:
            self._check(self.set_station(original))

    def start(self, argv, *, cwd, env, stdin, stdout, stderr):
        import msvcrt
        stdio = [msvcrt.get_osfhandle(stream.fileno()) for stream in (stdin, stdout, stderr)]
        handles = (W.HANDLE * len(set(stdio)))(*dict.fromkeys(stdio))
        for handle in handles:
            os.set_handle_inheritable(handle, True)
        size = ctypes.c_size_t()
        self.attribute_init(None, 1, 0, ctypes.byref(size))
        storage = ctypes.create_string_buffer(size.value)
        self._check(self.attribute_init(storage, 1, 0, ctypes.byref(size)))
        try:
            self._check(self.attribute_update(storage, 0, 0x20002, handles, ctypes.sizeof(handles), None, None))
            startup = StartupEx()
            startup.startup.cb = ctypes.sizeof(startup)
            startup.startup.desktop = self.station_name + '\\' + self.desktop_name
            startup.startup.flags = 0x101
            startup.startup.show = 0
            startup.startup.stdin, startup.startup.stdout, startup.startup.stderr = stdio
            startup.attributes = ctypes.cast(storage, ctypes.c_void_p)
            command = ctypes.create_unicode_buffer(subprocess.list2cmdline([str(value) for value in argv]))
            environment = ctypes.create_unicode_buffer('\0'.join(key + '=' + value for key, value in sorted(env.items(), key=lambda item: item[0].upper())) + '\0\0')
            process = ProcessInfo()
            self._check(self.create_process(self.token, str(argv[0]), command, None, None, True,
                        0x400 | 0x4 | 0x80000, environment, str(cwd), ctypes.byref(startup), ctypes.byref(process)))
            self.process, self.thread, self.pid = process.process, process.thread, process.pid
            self.handles.extend([self.process, self.thread])
            try:
                self._check(self.assign_job(self.job, self.process))
                if self.resume(self.thread) == 0xFFFFFFFF:
                    raise ctypes.WinError(ctypes.get_last_error())
            except BaseException:
                self.terminate_process(self.process, 1)
                raise
        finally:
            self.attribute_delete(storage)
            for handle in handles:
                os.set_handle_inheritable(handle, False)
        return self.pid

    def poll(self):
        if not self.process:
            return None
        value = W.DWORD()
        self._check(self.exit_code(self.process, ctypes.byref(value)))
        return None if value.value == 259 else value.value

    def close(self):
        if self.job:
            self.terminate_job(self.job, 1)
        if self.process:
            self.wait(self.process, 5000)
        for handle in reversed(self.handles):
            self.close_handle(handle)
        self.handles.clear()
        self.job = self.process = self.thread = None
        for project in self.manual_projects:
            self._manual_grant(project, revoke=True)
        for path in reversed(self.transit):
            self._path_grant(path, 0x1000A0, 0, revoke=True)
        self.transit.clear()
        if self.desktop:
            self.close_desktop(self.desktop)
            self.desktop = None
        if self.station:
            self.close_station(self.station)
            self.station = None
        for allocation in reversed(self.allocations):
            self.local_free(allocation)
        self.allocations.clear()
