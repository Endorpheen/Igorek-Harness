# Execution and isolation boundaries

The selected execution mode determines the boundary. A prompt or an approval
card is not a virtual machine. The default mode is `off`; unavailable isolation
must not be reported as active isolation.

## Host and guest

The host owns session records, task state, Vault, approvals and durable history.
The isolated guest receives a versioned task payload through a typed channel.
One validated workspace is exported through virtiofs; a disposable overlay
holds guest installations and temporary files. Persisted workspace artifacts
remain separate from that overlay.

The VM path supports the cc-backend executor. Other adapters must report their
actual mode. Desktop OpenCode has its own integration and isolation path;
sharing a window does not establish an identical security boundary.

| Surface | Contract |
| --- | --- |
| Workspace | A validated real path, explicit export, sensitive/control paths denied |
| Channel | Framed typed messages, session/CID binding and bounded payloads |
| Approvals | Host decision, canonical paths/signatures and one-shot consumption |
| Credentials | Host broker/provider proxy; no general secret-bearing guest environment |
| Egress | Generated default-deny rules and explicit pinned destinations/ports |
| Host operations | Named broker operations; no generic remote shell operation |
| Persistence | Host session/history and workspace data survive guest teardown |

The provider proxy fixes the upstream origin. Request paths cannot replace the
recipient of its token, and redirects do not carry credentials to another
origin. Read-only Git comparisons and status use a private configuration containing only
repository format and branch metadata, with filesystem/network isolation and
process output limits. Repository filters, hooks and external diff drivers are
not loaded. Build queries disable fsmonitor and hooks.

## Important limits

- Rebuild and verify an image when its recipe or boundary changes. A tool version
  string alone does not prove that an older image contains the latest rules.
- Documented ephemeral MCP environment placements can carry credentials inside
  the guest. They are a distinct trust decision from host-brokered access.
- Network containment pins addresses at session start and is coarser than
  end-to-end hostname assurance. Do not claim DNS/per-host isolation solely
  from an IP/port allowlist.
- Rule read-back checks structural properties, addresses and counts. It is not
  a proof of every possible equivalent or reordered rule representation.
- Probe nonces prevent stale/replayed reports, but a guest that can read a nonce
  can also construct a report. Host observations must veto unsupported claims.
- Guest-local egress rules are not an independent security boundary against
  privileged guest code. The current image recipe gives the runner access to
  privileged network tools; its other descendants can bypass those rules.
  Host-enforced egress or a separate privileged policy controller, followed by
  native adversarial acceptance, remains a release blocker. Bash capability
  dropping alone does not close that boundary.
- Ordinary guest Bash and MCP descendants now inherit `no_new_privs`, blocking
  setuid/file-capability elevation even when they change their environment.
  The controller uses a root-owned isolated Python runtime and refuses same-UID
  ptrace/proc-memory access. Only the existing owner-approved `igorek-sudo`
  path retains guest elevation. These controls protect the unapproved-code
  boundary; they do not make in-guest netfilter independent of approved guest root.
- Guest caches and installs are disposable. If a workspace cannot provide its
  durable root, degraded persistence must be visible; do not claim recovery
  across teardown for data that remained on the overlay.
- Mobile review/checkpoints and attachment path translation need acceptance
  through the actual host/guest route, not just serializer tests.

The current durable-root implementation distinguishes off-mode files from VM
internal storage. Do not globally hide a workspace directory merely because the
VM uses that basename internally.

## Verification

Structural tests exercise real guards with controlled libvirt/channel seams.
They establish contracts, not successful hardware isolation. Live acceptance
requires KVM, libvirt, vsock, virtiofs, the verified image and guest rule tools.
Record missing prerequisites as skips with reasons.

```bash
KVM_AVAILABLE=1 IGOREK_NEGATIVE_WORKSPACE=/path/to/synthetic-workspace \
  uv run --no-sync python -u -m pytest tests/test_isolation_negative_security.py -v -rs
bash factory/scripts/isolation_poc.sh --profile my-profile --workspace /path/to/synthetic-workspace
KVM_AVAILABLE=1 factory/scripts/isolation_bench.sh
```

Use a dedicated synthetic workspace. Check file bytes, exported devices,
credential absence, control sockets, denied destinations, cancellation,
approval resume, sibling-session independence and cleanup. Performance results
require an actual run and its structured artifact; skipped metrics are not zero.

## Operations

Configure the image and mode explicitly for the target host. CLI data normally
lives under the user's `.igorek-agent-factory/`; managed desktop owns its own
selected data directory. These are per-machine locations, not shared state
synchronized by Git. Do not copy another machine's absolute paths as defaults.

After backend updates, restart runtime/gateway and then chat. Preserve workspace
history and checkpoints when replacing an image. Review
[operations](../operations/OPERATIONS_RUNBOOK.md), [policy](POLICY.md) and the
[security policy](../SECURITY.md) before changing trust boundaries.
