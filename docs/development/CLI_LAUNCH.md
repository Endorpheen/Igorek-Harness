# One-terminal CLI lifecycle

The host-tool installer installs a real `igorek` console entry point. No shell
alias or second terminal is required. The Rich/prompt_toolkit frontend and the
existing task queue/executor remain in use. This CLI runtime targets POSIX
(Linux/macOS); this change does not add native Windows CLI support.

```bash
factory/scripts/install_host_tool.sh
igorek
igorek --profile my-profile
igorek --profile my-profile --resume SESSION_ID
igorek --no-alt-screen
```

First launch offers the existing interactive setup when no user profiles exist.
A single configured profile is selected automatically; otherwise choose a profile.
The last successful selection is remembered. Noninteractive startup requires an
unambiguous configured profile. `/profile` lists profiles; `/profile NAME` changes
the actual route, clears the previous model override and session approval rules,
and updates history metadata. Each invocation has a distinct chat ID.

## Interactive ownership and concurrent clients

The worker service is keyed by data home and profile, not profile name alone.
Different profiles have independent services. Clients of the same profile in the
same data home share a queue worker. Tasks retain the existing serial execution
policy: multiple clients do not imply simultaneous requests within that queue.

OS-held client locks represent liveness, including abnormal client exits. A short
startup lock serializes launchers. Existing updated desktop/gateway workers
advertise their queue binding and can be reused when explicitly sharing a data
home. A different running binding is rejected. No process is killed by name/PID.

Ctrl+C exits this CLI client and cancels only its current task. Other client
locks keep the shared service alive. The last client releases a CLI-owned worker;
cooperative completion/cancellation can take longer than frontend exit. A worker
owned by desktop/gateway is not stopped. Diagnostics go to owner-private files
under the CLI data home, rather than the terminal frontend.

The ordinary invocation starts no scheduler. Closing all ordinary clients does
not provide unattended execution of queued or scheduled work. Queued tasks remain
in SQLite; this is not a guarantee that an interrupted model/tool operation can
resume automatically. Use the explicit daemon for unattended work.

## Explicit shared background runtime

The lifecycle names follow the reviewed Codex app-server daemon interface:

```bash
igorek app-server daemon start
igorek app-server daemon version
igorek --profile my-profile
igorek app-server daemon stop
igorek app-server daemon restart
```

`start` is explicit and idempotent. New clients attach to an already running
daemon in the same data home; an ordinary `igorek` invocation never starts it
implicitly. Each client retains the lifecycle selected at startup. All commands
accept `--data-dir PATH` for an explicit isolated home. `version` returns JSON
including whether the daemon is running and owns the scheduler.

The owner-local Unix socket is private, with bounded JSON frames and Linux peer
UID checks. There is no new TCP API. The daemon serializes task execution across its in-process profile workers,
matching desktop AgentRuntime, because task-local environment overlays are
process-global. Foreground workers remain separate processes.
The daemon owns queue workers, the existing
TaskScheduler/Gateway callback and the existing task watchdog. A per-database OS
lease prevents two updated schedulers from running the same schedule. The daemon
can take over a profile after a reused foreground consumer exits. It does not
start optional Telegram, embeddings or desktop helpers.

Closing a client keeps the daemon and its scheduler alive. Ctrl+C still cancels
that client's current interactive task; unrelated tasks are retained. `stop`
refuses while tasks/callbacks are active, without force-killing them. Worker
execution locks close the race between checking activity and stopping workers.
After explicit stop, schedules remain stored but require a running scheduler.
A failed readiness check leaves a due schedule pending instead of consuming it.

This is a Python adaptation of Codex's explicit shared-server/client ownership,
not its Rust implementation. Codex's updater and forced shutdown timeout were not
imported. The daemon is opt-in, not an OS boot service, and does not auto-update.

## Terminal modes and compatibility

Fullscreen retains the current mouse interface. `--no-alt-screen` uses a
persistent inline prompt with normal terminal scrollback and no mouse capture.
Both use the same session frontend. Keeping the inline application alive avoids
losing fast input between prompts, including immediately after profile switches.
Profile commands run off the event loop with captured output while the composer
retains raw input; Enter cannot be cooked into a newline during worker startup.
The compact header shows version, profile, model and directory within terminal
width. To restore the older header while retaining the new launcher:

```bash
IGOREK_TUI_CLASSIC_STYLE=legacy igorek --no-alt-screen
```

The original `igorek-factory chat` path and its historical inline rendering remain
available. No Textual/plain frontend is restored or current TUI module removed.
Mouse copy delivery runs off the UI loop with one pending request. Failed or
unconfirmed copies retain selection for right-click retry; only confirmed local
copy clears it. An old completion cannot clear a newer selection.

Desktop keeps its separate owned data home, processes and Vault lifecycle. No
credentials are copied between desktop and CLI. Older runtimes without presence
advertisement/leases should retain separate homes; update both sides before
explicitly sharing a queue. Gateway, desktop, model authorization and executor
policies retain their existing routes.

## Diagrams and validation

- [Before: separate startup](../architecture/cli-before.svg)
- [After: interactive ownership and optional daemon](../architecture/cli-after.svg)
- [Interactive mode comparison](../architecture/cli-before-after.png)

Real POSIX PTY acceptance supplies geometry, answers cursor queries, switches
profiles, receives a synthetic model response and sends raw Ctrl+C. It checks
fullscreen/inline with foreground/shared workers, cancellation during a request,
second-client survival and the older header. Separate acceptance executes a
schedule without any frontend. Clipboard tests check nonblocking busy handling
and confirmed/unconfirmed delivery. These checks do not establish physical
Termux/Ubuntu ARM64 acceptance, native Windows CLI or real provider-account use.

After installation, restart affected CLI chats. Existing desktop/gateway workers
need their normal restart only if updating their backend package; running user
services are not restarted by these tests or the CLI installer.
