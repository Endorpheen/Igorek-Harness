#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include "detours.h"
#include <vector>

static auto real_pipe_a = CreateNamedPipeA;
static auto real_pipe_w = CreateNamedPipeW;
static auto real_process_a = CreateProcessA;
static auto real_process_w = CreateProcessW;
static thread_local bool creating_process = false;

class PipeSecurity {
public:
    SECURITY_ATTRIBUTES attributes{};
    SECURITY_DESCRIPTOR descriptor{};
    std::vector<BYTE> storage;

    bool prepare(LPSECURITY_ATTRIBUTES original) {
        HANDLE token = nullptr;
        if (!OpenThreadToken(GetCurrentThread(), TOKEN_QUERY, TRUE, &token)) {
            if (GetLastError() != ERROR_NO_TOKEN ||
                !OpenProcessToken(GetCurrentProcess(), TOKEN_QUERY, &token)) return false;
        }
        DWORD needed = 0;
        GetTokenInformation(token, TokenDefaultDacl, nullptr, 0, &needed);
        storage.resize(needed);
        BOOL okay = needed && GetTokenInformation(token, TokenDefaultDacl, storage.data(), needed, &needed);
        DWORD error = okay ? ERROR_SUCCESS : GetLastError();
        CloseHandle(token);
        if (!okay) { SetLastError(error); return false; }
        auto dacl = reinterpret_cast<TOKEN_DEFAULT_DACL*>(storage.data())->DefaultDacl;
        if (!dacl) { SetLastError(ERROR_ACCESS_DENIED); return false; }
        if (!InitializeSecurityDescriptor(&descriptor, SECURITY_DESCRIPTOR_REVISION) ||
            !SetSecurityDescriptorDacl(&descriptor, TRUE, dacl, FALSE)) return false;
        attributes.nLength = sizeof(attributes);
        attributes.lpSecurityDescriptor = &descriptor;
        attributes.bInheritHandle = original ? original->bInheritHandle : FALSE;
        return true;
    }
};

static HANDLE WINAPI pipe_a(LPCSTR name, DWORD open_mode, DWORD pipe_mode, DWORD instances,
                           DWORD output, DWORD input, DWORD timeout, LPSECURITY_ATTRIBUTES original) {
    if (original && original->lpSecurityDescriptor)
        return real_pipe_a(name, open_mode, pipe_mode, instances, output, input, timeout, original);
    PipeSecurity security;
    if (!security.prepare(original)) return INVALID_HANDLE_VALUE;
    return real_pipe_a(name, open_mode, pipe_mode, instances, output, input, timeout, &security.attributes);
}

static HANDLE WINAPI pipe_w(LPCWSTR name, DWORD open_mode, DWORD pipe_mode, DWORD instances,
                           DWORD output, DWORD input, DWORD timeout, LPSECURITY_ATTRIBUTES original) {
    if (original && original->lpSecurityDescriptor)
        return real_pipe_w(name, open_mode, pipe_mode, instances, output, input, timeout, original);
    PipeSecurity security;
    if (!security.prepare(original)) return INVALID_HANDLE_VALUE;
    return real_pipe_w(name, open_mode, pipe_mode, instances, output, input, timeout, &security.attributes);
}

extern "C" __declspec(dllexport) BOOL WINAPI IgorekEmbed(HANDLE process) {
    LPCSTR library = "igorek-pipes.dll";
    return DetourUpdateProcessWithDll(process, &library, 1);
}

static BOOL finish_child(BOOL created, DWORD flags, LPPROCESS_INFORMATION information) {
    if (!created) return FALSE;
    if (IgorekEmbed(information->hProcess) &&
        ((flags & CREATE_SUSPENDED) || ResumeThread(information->hThread) != DWORD(-1))) return TRUE;
    DWORD error = GetLastError();
    TerminateProcess(information->hProcess, 1);
    CloseHandle(information->hThread);
    CloseHandle(information->hProcess);
    ZeroMemory(information, sizeof(*information));
    SetLastError(error);
    return FALSE;
}

static BOOL WINAPI process_w(LPCWSTR application, LPWSTR command, LPSECURITY_ATTRIBUTES process,
                            LPSECURITY_ATTRIBUTES thread, BOOL inherit, DWORD flags, LPVOID environment,
                            LPCWSTR cwd, LPSTARTUPINFOW startup, LPPROCESS_INFORMATION information) {
    if (creating_process || !information)
        return real_process_w(application, command, process, thread, inherit, flags, environment, cwd, startup, information);
    creating_process = true;
    BOOL created = real_process_w(application, command, process, thread, inherit, flags | CREATE_SUSPENDED,
                                  environment, cwd, startup, information);
    BOOL result = finish_child(created, flags, information);
    creating_process = false;
    return result;
}

static BOOL WINAPI process_a(LPCSTR application, LPSTR command, LPSECURITY_ATTRIBUTES process,
                            LPSECURITY_ATTRIBUTES thread, BOOL inherit, DWORD flags, LPVOID environment,
                            LPCSTR cwd, LPSTARTUPINFOA startup, LPPROCESS_INFORMATION information) {
    if (creating_process || !information)
        return real_process_a(application, command, process, thread, inherit, flags, environment, cwd, startup, information);
    creating_process = true;
    BOOL created = real_process_a(application, command, process, thread, inherit, flags | CREATE_SUSPENDED,
                                  environment, cwd, startup, information);
    BOOL result = finish_child(created, flags, information);
    creating_process = false;
    return result;
}

BOOL WINAPI DllMain(HINSTANCE, DWORD reason, LPVOID) {
    if (reason != DLL_PROCESS_ATTACH) return TRUE;
    if (!DetourRestoreAfterWith()) return TRUE;
    HANDLE token = nullptr;
    if (!OpenProcessToken(GetCurrentProcess(), TOKEN_QUERY, &token)) return FALSE;
    BOOL restricted = IsTokenRestricted(token);
    CloseHandle(token);
    if (!restricted) return FALSE;
    if (DetourTransactionBegin() != NO_ERROR) return FALSE;
    LONG error = DetourUpdateThread(GetCurrentThread());
    if (!error) error = DetourAttach(reinterpret_cast<PVOID*>(&real_pipe_a), reinterpret_cast<PVOID>(pipe_a));
    if (!error) error = DetourAttach(reinterpret_cast<PVOID*>(&real_pipe_w), reinterpret_cast<PVOID>(pipe_w));
    if (!error) error = DetourAttach(reinterpret_cast<PVOID*>(&real_process_a), reinterpret_cast<PVOID>(process_a));
    if (!error) error = DetourAttach(reinterpret_cast<PVOID*>(&real_process_w), reinterpret_cast<PVOID>(process_w));
    if (error) { DetourTransactionAbort(); return FALSE; }
    return DetourTransactionCommit() == NO_ERROR;
}
