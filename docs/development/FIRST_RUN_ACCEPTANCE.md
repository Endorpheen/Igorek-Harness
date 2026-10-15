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

Hosted Windows candidate installation and the maintained first-task scenario
are checked by CI. Clean-machine and manual Windows acceptance remain separate. Physical Android update/background/voice acceptance requires a
connected device. Real OAuth consent requires the account owner's interaction.
Keep these gates open until their actual results are recorded.

Current platform results are recorded in [TestLOG.md](TestLOG.md).
