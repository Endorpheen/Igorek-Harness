# OpenCode delegation

Linux desktop can delegate one explicitly requested task to the installed
OpenCode 2.0.27 service while keeping the main conversation available. The
`opencode_task` tool supports start, status and cancellation. It never exposes
an operation for answering owner questions or granting permissions to the model.

The Python runtime captures the current Git workspace, including tracked dirty
files and nonignored untracked files, in an independent repository without
remotes or shared Git metadata. Private control paths, local context, environment
secrets and ignored artifacts are excluded. Symlinks and submodules are refused
instead of silently exporting a partial or externally writable tree. Snapshot
size and file-count limits apply.

The Electron parent validates the source against existing owner project grants.
It starts a separate OpenCode sandbox with only the task copy mounted writable,
using a separate profile capability. It opens that exact session in the existing
right panel. The session title is `Поручение: <title>`; no extra panel or status
bar is added. Opening OpenCode manually restores its original service and saved
navigation. Manual service configuration and running sessions are preserved.

Delegations are durable records, not active tasks in the main conversation queue.
A runtime monitor reads the session outcome, message timeline, pending forms and
permission requests. Every pending request is displayed through the existing
chat input-card mechanism. Answers use the exact run, session and request IDs;
plain conversation messages cannot accidentally answer OpenCode. Replying in
OpenCode resolves the corresponding main-chat card. Form values retain their
types, and permission replies are limited to one-time approval or rejection.

Completed assistant text is relayed without classifying its importance or trying
to recognize questions in prose. External text is historical evidence, not a
source of instructions or permissions. After successful execution, changed
files are compared against the captured source baseline before application.
Conflicting source files stop the entire transfer. The copy remains available
when transfer fails. A single result turn is queued after the current main-chat
turn completes; it reports the outcome without starting more delegated work.

Transient transport failures re-read the existing session rather than submitting
another prompt. Stable request keys protect repeated tool delivery. Closing the
panel does not stop the service. Owner lock and application exit stop delegated
services. Restarted runtimes mark unfinished records interrupted and do not
relaunch them: OpenCode pending forms are process-local and cannot be recovered
after a server restart. Cancellation interrupts execution without resuming queued
input, and does not roll back files already changed in the task copy.

This path requires Linux, host desktop execution, an installed OpenCode binary,
an already granted Git project and an available verified provider profile. KVM
guest execution, Windows delegation and arbitrary non-Git directories are not
enabled by this integration. Ordinary manual OpenCode behavior is unchanged.
