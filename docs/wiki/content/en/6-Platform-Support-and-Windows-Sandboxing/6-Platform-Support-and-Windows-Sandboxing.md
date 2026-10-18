# Platform Support & Windows Sandboxing

<details>
<summary>Relevant source files</summary>

The following files were used as context for generating this wiki page:

- [desktop/native/windows-pipes.cpp](desktop/native/windows-pipes.cpp)
- [desktop/opencode-windows.py](desktop/opencode-windows.py)
- [factory/core/platform_support/windows_sandbox.py](factory/core/platform_support/windows_sandbox.py)

</details>



This page provides an overview of the OS-specific isolation architecture in Igorek-Harness. While Linux environments rely on Bubblewrap (`bwrap`), Windows environments use a robust multi-layered user-mode sandboxing implementation. This system incorporates restricted tokens, Windows job objects, isolated desktops, and a native DLL injection mechanism via Microsoft Detours to secure child processes and OpenCode host runners.

For complete technical details, refer to the child pages:
- [WindowsSandbox Internals](#6.1)
- [Native Pipe Adapter & Detours](#6.2)
- [Windows OpenCode Host Runner](#6.3)
- [Safe File & Process Primitives](#6.4)

---

## 6.1. WindowsSandbox Internals

The `WindowsSandbox` class acts as the core orchestrator for process isolation on Windows platforms [factory/core/platform_support/windows_sandbox.py:57-59](). It creates a highly restricted security context for spawned child processes by stripping dangerous privileges, applying unique capability Security Identifiers (SIDs), assigning Discretionary Access Control List (DACL) grants, enforcing integrity labels, routing processes to an isolated window station/desktop, and confining execution to a Windows job object.

For details, see [WindowsSandbox Internals](#6.1).

Sources: `factory/core/platform_support/windows_sandbox.py:57-132`

---

## 6.2. Native Pipe Adapter & Detours

Because restricted Windows tokens limit a child process's ability to create named pipes with default user DACLs, Igorek-Harness employs a native C++ module (`windows-pipes.cpp`) built with vendored Microsoft Detours. The module exports `IgorekEmbed` [desktop/native/windows-pipes.cpp:60-63]() and intercepts `CreateNamedPipeA`/`CreateNamedPipeW` and `CreateProcessA`/`CreateProcessW` calls [desktop/native/windows-pipes.cpp:6-9]() to ensure that child-created named pipes inherit the correct token default DACLs.

For details, see [Native Pipe Adapter & Detours](#6.2).

Sources: `desktop/native/windows-pipes.cpp:1-120`

---

## 6.3. Windows OpenCode Host Runner

The `desktop/opencode-windows.py` script manages the extraction, environment preparation, and lifecycle execution of the native Windows OpenCode service within the `WindowsSandbox` [desktop/opencode-windows.py:1-142](). It handles archive extraction [desktop/opencode-windows.py:20-29](), short path pathing [desktop/opencode-windows.py:49-58](), environment variable mapping (including `SystemRoot`, `HOME`, and Git configurations) [desktop/opencode-windows.py:90-102](), TCP port discovery via `iphlpapi` [desktop/opencode-windows.py:31-46](), and HTTP readiness polling [desktop/opencode-windows.py:130-141]().

```mermaid
title "Windows OpenCode Runner Architecture"
graph TD
    subcode ["Code Entity Space"]
    Main ["main() <br/> desktop/opencode-windows.py:60"] --> Extract ["extract() <br/> desktop/opencode-windows.py:20"]
    Main --> Sandbox ["WindowsSandbox <br/> factory/core/platform_support/windows_sandbox.py:57"]
    Main --> Port ["listening_port() <br/> desktop/opencode-windows.py:31"]
    Sandbox --> Adapter ["igorek-pipes.dll <br/> desktop/native/windows-pipes.cpp"]
    end
```
Sources: `desktop/opencode-windows.py:1-142`, `factory/core/platform_support/windows_sandbox.py:57-132`, `desktop/native/windows-pipes.cpp:1-120`

For details, see [Windows OpenCode Host Runner](#6.3).

---

## 6.4. Safe File & Process Primitives

To prevent directory traversal, race conditions, and privilege escalation on the host filesystem, Igorek-Harness implements strict file and process primitives. These include atomic writes, workspace-bounded reads, exclusive locks, pinned parents, DACL preservation, and checkpointed git execution routines.

```mermaid
title "Platform Isolation and Process Lifecycle"
graph TD
    subcode ["Code Entity Space"]
    Script ["desktop/opencode-windows.py"] --> SandboxInit ["WindowsSandbox.__init__() <br/> factory/core/platform_support/windows_sandbox.py:60"]
    SandboxInit --> Bind ["_bind() <br/> factory/core/platform_support/windows_sandbox.py:100"]
    SandboxInit --> Token ["_token() <br/> factory/core/platform_support/windows_sandbox.py:85"]
    SandboxInit --> Grant ["_grant() <br/> factory/core/platform_support/windows_sandbox.py:87"]
    SandboxInit --> Job ["_job() <br/> factory/core/platform_support/windows_sandbox.py:94"]
    SandboxInit --> Desktop ["_desktop() <br/> factory/core/platform_support/windows_sandbox.py:95"]
    end
```
Sources: `factory/core/platform_support/windows_sandbox.py:60-99`, `desktop/opencode-windows.py:60-126`

For details, see [Safe File & Process Primitives](#6.4).
