# Release readiness

Use this checklist for a concrete source revision and its build artifacts.
Source publication and a stable product release are separate milestones.
Current measured results and unresolved acceptance limits are in
[TestLOG.md](TestLOG.md) and [testing](../TESTING.md).

## Source publication

- Review the tracked file inventory and every Git ref being published for
  secrets, personal data, local configuration and accidental task artifacts.
- Run the reviewed secret detector against both the source tree and public
  history. Redact values; investigate findings rather than widening exclusions.
- Keep Vault, keys, databases, logs, toolchains, build output and VM images out
  of Git. Removing a tracked file does not remove earlier copies from history.
- Verify current English/Russian READMEs, installation commands, local links,
  screenshots, licenses and third-party notices.
- Confirm the repository identity, supported platforms and documented limits.
- Obtain the owner's publication approval. Enable main-branch protection and
  GitHub Private Vulnerability Reporting when the public repository supports them.

## Candidate builds

- Build locked dependencies from a clean, identified source inventory; verify
  wheel, snapshot, APK and embedded installer contents for stale or private files.
- Run both Python test roots, desktop contracts and Android unit/lint checks.
  Report collected cases, subtests, lines and branches separately, with skip reasons.
- Require successful hosted Linux and Windows builds and maintained native
  first-task/restart scenarios. A configured workflow alone is not evidence.
- Record versions, artifact SHA-256 hashes, signing status and matching release
  notes. Keep signing keys and recovery material outside source and CI artifacts.
- Label preliminary builds explicitly. An unsigned Windows executable or passing
  unit suite must not be presented as a stable three-platform release.
- Publish only the intended installers and checksums. VM images are not release
  attachments for the current distribution.

## Stable release acceptance

- Linux: clean supported OS, installation, first task, restart, data-preserving
  update, rollback, uninstall, sandbox prerequisites, browser and voice.
- Windows: clean installation, workspace/file/Vault/ConPTY behavior, first task,
  browser and voice, update/recovery/uninstall, and publisher-signing decision.
- Android: physical HTTPS pairing, task/files/voice/background and update checks;
  independently recoverable release-key custody.
- Test longer sessions, multiple tasks, interruption/recovery and saved history
  on all three platforms. Synthetic accounts do not establish real OAuth consent.
- Verify actual VM/vsock/virtiofs/network boundaries where isolation is claimed.
  Functional completion does not close the privileged-guest egress limitation.
- Record accepted artifacts and obtain explicit stable-release approval.

Maintained first-run scenarios are described in
[FIRST_RUN_ACCEPTANCE.md](FIRST_RUN_ACCEPTANCE.md). Preserve durable user data
through updates; never reset accounts or delete failed checks to obtain a pass.
