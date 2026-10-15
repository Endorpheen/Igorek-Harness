# First-run behavior and acceptance

The first-run guide connects Vault, a model profile, a working directory and a
first task. Optional integrations follow the basic chat setup.

## Current flow

1. Create, restore or unlock Vault.
2. Save a model connection or use the account sign-in flow.
3. Verify the connection with the built-in text probe.
4. Start the executor for that exact verified profile.
5. Choose a working directory, create a session, send a small task and inspect
   the resulting answer.

A running process completes step four only. Step five requires a committed
`ok` task with a completion timestamp and nonempty saved output for that profile.
The settings page receives profile identifiers, not task text. Locked settings
cannot read that progress. Unavailable history is shown as unavailable, rather
than reported as successful setup.

The guide uses saved connections without asking for keys again. It refreshes
first-result progress separately, so checking history does not clear an open
credential form. A profile change or lock invalidates an earlier pending reply.

This check establishes that a task produced a persisted result. It does not
prove that every model answer is correct. The user should check the first
answer against the requested file or other known input.

## Clean-install acceptance

Run independently on each supported desktop platform:

- Install from the release artifact without developer configuration.
- Create Vault and understand recovery before storing important credentials.
- Connect one supported model; then verify a tool call in the first task.
- Choose a dedicated folder containing a synthetic file with known content.
- Ask the agent to read that file; compare the answer with its contents.
- Change a test file through the agent, inspect the confirmation and result.
- Cancel another task and confirm it leaves a usable session.
- Restart and check the saved connection, history and continuation.
- Exercise a bad key, unreachable server, unsupported model, canceled sign-in
  and network interruption. The recovery path should explain the next action.
- Pair Android through HTTPS QR and continue the same session.

Test voice, plugins, schedules and VM isolation after the basic conversation
works. Account consent and physical-device checks are separate from mocked
network tests. Linux checks do not establish Windows acceptance.

## Remaining acceptance gates

Clean Windows installation and its runtime packaging require a Windows test
environment. Physical Android update/background/voice acceptance requires a
connected device. Real OAuth consent requires the account owner's interaction.
Keep these gates open until their actual results are recorded.

## Linux candidate evidence — 15 October 2026

The actual installed Electron/Python 0.1.889 was exercised with clean temporary
data and a synthetic loopback provider: Vault creation, profile save/check,
worker start, HTTPS browser pairing, session creation, a completed ordinary
message, saved first-result progress, lock and unlock. The first-result step
remained incomplete while only the worker was running. The profile/history
survived lock/unlock and a full test backend/window restart. A second task
continued the saved session. The eight-step `/demo` completed actual file-tool
calls; its contextual links opened Plugins, Notifications and Primary model
settings. All nine native acceptance stages passed. This proves those paths
with a controlled provider; it does not establish real account consent, cloud
model quality, long-session recovery or Windows/physical Android behavior.

The snapshot installer also passed a real temporary 0.1.888 → 0.1.889 update,
rollback, reapplication, uninstall retaining data and reinstallation. Synthetic
canaries remained intact; application/menu files were removed on uninstall.
This is not acceptance of a clean OS's dependencies or system installer dialogs.
