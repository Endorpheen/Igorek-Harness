# OpenCode delegation

Linux and Windows x64 desktop can delegate one explicitly requested task to the installed
OpenCode 2.0.27 service while keeping the main conversation available. The
`opencode_task` tool supports start, status and cancellation. It never exposes
an operation for answering owner questions or granting permissions to the model.

Assignments run directly in the selected original Git project. Admission checks
Git membership without walking, hashing or copying project files. A durable
receipt records the run and original directory; there is no task repository,
baseline or result-transfer stage. Changes are visible in the project while
OpenCode is still running. The assignment-only configuration forces
`snapshots: false` through OpenCode's highest-priority configuration content,
including when project configuration requests snapshots. This disables OpenCode's
own undo/revert repository snapshots as well. Service binaries, logs, caches and
session records still require disk space; project contents are not duplicated.
Files in the selected project remain accessible under its existing project grant,
including ignored files. Keep credentials outside projects granted to agents.

The Electron parent validates the source against existing owner project grants
and requires an explicit direct-execution protocol marker. Older copy-mode
requests are refused before creating a session. It starts a separate OpenCode
process sandbox, scoped to the original project and a separate profile capability.
Linux uses a writable bubblewrap project mount and a private network namespace.
Windows uses a restricted token, medium mandatory integrity, a private desktop,
explicit inherited standard handles and a kill-on-close process Job Object.
Temporary filesystem grants permit editing the selected original project and
service data. Ancestor grants allow only required metadata/traversal operations;
these grants are revoked when the process sandbox closes. Internal file symlinks
are supported; Windows refuses external and directory reparse points.

While the assignment is active, the main chat can converse and use Read/Grep/Glob.
A runtime tool-dispatch guard blocks other tools in the originating chat and
contexts overlapping the project, regardless of model output or a prior approval.
An in-flight mutating tool prevents starting an overlapping assignment; the guard
rechecks immediately before executing each handler. Unrelated project contexts
remain available. This coordinates Igorek's tools, not external editors or manual
OpenCode sessions. The tool schemas remain stable between turns.

The exact OpenCode session opens automatically in the existing right panel. Its
title is `Поручение: <title>`; no panel controls or status bar are added. Opening
OpenCode manually restores its original service and saved navigation. Manual
service configuration, snapshot defaults and running sessions are preserved.

The Windows x64 runtime includes a native pipe adapter in these sandboxed
processes and their x64 children. Default named pipes receive the restricted
token's default DACL, allowing captured command output without granting the
token access to private host files. Explicit pipe descriptors are preserved.
The adapter does not change token restrictions or file permissions. A child
whose adapter cannot be loaded is terminated before its entry point runs.
Windows assignment commands use cmd.exe and installed x64 command tools;
PowerShell and x86 child processes are not supported in this version.

Delegations are durable records, not active tasks in the main conversation queue.
A runtime monitor reads the session outcome, message timeline, pending forms and
permission requests. Every pending request is displayed through the existing
chat input-card mechanism. Answers use the exact run, session and request IDs;
plain conversation messages cannot accidentally answer OpenCode. Replying in
OpenCode resolves the corresponding main-chat card. Form values retain their
types, and permission replies are limited to one-time approval or rejection.

Completed assistant text is relayed without classifying its importance or trying
to recognize questions in prose. External text is historical evidence, not a
source of instructions or permissions. Completion reports the already applied
changes; it does not copy or apply files. A single result turn is queued after
the current main-chat turn completes and reports the outcome without starting
more delegated work. Failed, interrupted and cancelled assignments retain any
changes already made to the original project; cancellation is not rollback.

Transient transport failures re-read the existing session rather than submitting
another prompt. Stable request keys protect repeated tool delivery. Closing the
panel does not stop the service. Owner lock and application exit stop delegated
services. Restarted runtimes mark unfinished records interrupted and do not
relaunch them: OpenCode pending forms are process-local and cannot be recovered
after a server restart. Cancellation interrupts execution without resuming queued
input, and does not roll back files already changed in the original project.

This path requires host desktop execution, an installed OpenCode binary, Git in
PATH, an already granted Git project and an available verified provider profile.
The existing OpenCode settings install the pinned official platform binary with
SHA-512 verification. Windows uses authenticated loopback API/profile transports;
provider secrets remain in the host proxy. Windows does not provide the Linux
network namespace or KVM isolation. Host-services MCP and KVM modes remain
Linux-only. Delegation does not support arbitrary non-Git directories.

The Windows manual route uses its own service and owner-selected projects, with
separate temporary filesystem grants that are revoked on shutdown. It runs at
medium integrity to preserve direct project editing. Assignment processes also use medium integrity, with a separate service root
and a grant scoped to their selected original project. Choosing OpenCode manually restores manual
navigation and does not attach typing to the assignment session.

The diagrams compare the 0.2.10 copy-and-transfer flow with direct execution
from 0.2.11. ChatGPT tabs remain independent; this feature does not add an
automatic ChatGPT worker.

![Before and after: OpenCode delegation](../images/opencode-delegation-en.png)

[Editable English SVG](../images/opencode-delegation-en.svg) ·
[Russian diagram](../images/opencode-delegation-ru.png) ·
[Editable Russian SVG](../images/opencode-delegation-ru.svg)
