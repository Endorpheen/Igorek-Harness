# Operations runbook

Use the installed tool and that machine's data directory. Configuration, Vault,
profiles and task history are local state; Git synchronizes source code only.
Managed desktop owns its backend lifecycle and its selected data directory.

## Diagnose before restarting

Record the affected interface, version, profile, session/task ID and exact
failure. Inspect the corresponding process/service and structured trace. Follow
[the debugging playbook](../development/DEBUGGING_PLAYBOOK.md). Do not stop a
working stack merely to discover which component failed.

Workers consume only their own profile's queue. A queued task needs a worker for
that profile. `FACTORY_WORKER_PROFILES` adds gateway-managed profiles;
`igorek-worker@` provides a separately managed user worker. Do not duplicate the
active gateway worker. Service templates use per-user paths, not a developer
checkout location.

## Logs and data

Use per-component logs and task traces. Redact values and personal content before
sharing them. A path or event name alone may identify a machine or private task;
public reports should use synthetic examples.

CLI data normally lives under `~/.igorek-agent-factory/` on each machine.
Installed desktop data is independent from the immutable application snapshot.
Back up the actual selected data directory; copying source is not a data backup.

## Backup, update and rollback

The helper scripts in `factory/scripts/` provide backup/restore and controlled
update operations for their documented installation mode. Inspect their help and
scope before using them. Preserve Vault recovery material independently and
verify restoration into a temporary location before relying on a backup.

For Linux desktop, use `desktop/packaging/install-linux.py` and its verified
snapshot. The installer owns release generations and update/rollback state.
Do not replace files under a running snapshot or delete data as part of an
ordinary application update. A busy installation should refuse a switch.

The retired web monitor is not part of the current stack. Installer migration
may remove its old unit; do not restart it as a recovery step. The task API,
mobile bridge and shared Web UI have separate endpoints and authentication.

## Credentials and access

Rotate a credential at its issuing service and update the intended Vault/profile.
Check the result without printing the key. Pairing uses HTTPS and a bounded
code; revoke devices that no longer need access. A model connection, browser
account session, voice account and plugin grant are distinct permissions.

Host APK delivery requires an unambiguous recipient from the owner's Telegram
allowlists. If several are configured, set `IGOREK_APK_DELIVERY_CHAT_ID` to one of
those allowed IDs. No recipient is compiled into the application. Invalid or
ambiguous selection refuses delivery before obtaining credentials.

Local Wally credentials and legacy skill directories use standard per-user
configuration paths or explicit environment overrides. Local inference binaries
must be installed in a standard location or configured explicitly. Do not add
private workstation paths to shared source defaults.

## Isolation and restarts

Use [isolation](../architecture/ISOLATION.md) for prerequisites, image replacement,
negative probes, persistence and known limits. Missing hardware is not a pass.
After backend/runtime or host-tool updates, restart runtime/gateway and then
chat. A CLI-only update requires a chat restart. Reopen desktop to load its new
application/runtime generation; preserve active tasks before switching.
