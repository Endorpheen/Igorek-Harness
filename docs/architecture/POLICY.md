# Execution policy

Executable inference API policy is configured in [policy.yaml](../../factory/config/inference-policy.yaml).
This document describes the controls; changing prose does not change enforcement.
The shared agent runtime has additional guards and permission layers.

## File and command controls

The file policy selects managed tools, project-root enforcement, absolute-path
overrides, creation of new files and explicit additional roots. The shipped
configuration contains no private workstation directories.

Bash policy controls compound commands, mutation, command allowlists and nested
shells. Empty command lists and a permissive profile must not be presented as
operating system isolation. Operators should grant only the intended working
folders and execution mode.

The inference API protects its executable policy, this document and the policy
implementation against ordinary file-tool edits. This document's protected path
is `docs/architecture/POLICY.md`.

## Access levels

| Level | security_mode | guard_mode | filesystem_mode |
| --- | --- | --- | --- |
| strict | enforce | on | workspace |
| standard | observe | on | workspace |
| trusted | observe | warn | workspace |
| full | disabled | off | full-dangerous |

The server resolves a session override before the global setting and baseline.
A weaker level requires explicit confirmation and a bounded lifetime. Defaults
are one hour; `full` is bounded to one hour, `trusted` to one day. Expired or
invalid overrides fall back to the stricter available setting. Task metadata
alone does not choose an access level.

## Limits

Permission settings and approval prompts are user controls, not an independent
security boundary against a process that already has equivalent host access.
Paired devices participate in the owner's trust domain. Protect pairing tokens
and revoke unused devices. Bash scope enforcement and VM support have
backend-specific boundaries; do not assume all provider adapters run in a VM.

Use [isolation](ISOLATION.md) for the host/guest model and
[security policy](../SECURITY.md) for private vulnerability reporting.
