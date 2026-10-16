# Working on Igorek

English is the primary repository language. Write code comments, docstrings,
reusable skills, commit messages and technical documentation in English.
Preserve user-facing translations and answer in the user's language.

## Scope and privacy

Perform the requested work and the steps needed to verify it; stop when complete.
Do not launch subagents or other agent tasks without explicit user permission.
A request to draft a task for another agent permits writing the task only.
Preserve parallel work. Do not stop desktop, gateway, model services or user tasks
for a check. Use temporary data, synthetic accounts and separate test processes.
Do not send messages, publish releases, rewrite shared history or expose the
repository publicly without the required explicit user authorization.

Read `docs/local/AGENTS.local.md`, `docs/local/handoff.md` and
`docs/local/Ledger.md` if present. These ignored files contain machine-specific
rules, unfinished work and previous checks. Preserve deferred work in local
records. Never stage local context, `Archive/`, keys, Vault data, databases,
logs, personal task artifacts, private screenshots, toolchains or local settings.
Stage specific reviewed paths, never `git add .`. Do not print credentials.
A `.gitignore` entry does not remove earlier data from Git history.

Use the existing canonical remote and branch. Do not invent another repository,
change upstream or restore archived branches without a concrete task. Public docs
belong in `docs/`; root READMEs and discovery files stay in standard locations.
`SKILL.md` stays inside its skill package. Keep third-party notices with assets.
Machine-dependent configuration must be labeled by environment, not described
as a portable project default.

## Investigation and implementation

Use `rg` for exact symbols. For unknown cross-module behavior use the configured
`jg` skill, verify its actual endpoint, then read the returned source. Never send
private data to an unverified endpoint. Use existing reference checkouts, and
inspect the actual implementation when the user requests a reference pattern.

Read [the debugging playbook](docs/development/DEBUGGING_PLAYBOOK.md) for loops,
repeated approvals, lost answers and malformed tool calls. Base conclusions on
IDs, trace events, JSON shapes, signatures, states and observed effects. A log
analysis includes the relevant user turn, request chain, modes, intent, workflow
attachment/skip, events and an outcome per turn. Do not invent logs or infer
success and routing from vague text impressions.

Choose policy/prompts/workflows before code, except for deterministic invariants.
Avoid phrase-specific handlers. Preserve strong cloud-model behavior and the
salvage/guard protections needed by weaker local models; add both positive and
compatibility regressions where applicable. Centralize CC-backend prompt rules
in `factory/core/prompt_assembly.py`; API prompts belong in
`factory/api_server_core/prompts.py` and `prompt_builder.py`. Workers and executors
consume prompts, rather than defining rules; only a minimal transport suffix
such as `/no_think` is allowed there.

Salvage changes are fixture-first: capture the real output under
`tests/fixtures/tool_salvage/`, add a test, preserve old shapes and prefer additive
changes. Explain changes to old behavior before making them. Test before host
reinstallation and smoke checks. Trace kinds come from `CcTraceEvent` in
`factory/core/trace_taxonomy.py`, use `domain.action`, and require a taxonomy test.
Do not mix the CC-backend taxonomy with API/workflow events.

## Verification and release

Use locked dependencies and the same commands as CI:

```bash
uv sync --locked --dev
uv run --no-sync python -u tools/run_tests.py --coverage
npm --prefix desktop ci
npm --prefix desktop test
```

Run both Python test roots, show progress, and record test changes and results
in `docs/development/TestLOG.md`. Separate collected tests, subtests, line/branch
coverage, native UI checks and physical-device acceptance. Explain skips; do not
remove or skip failures to obtain a green result. PTY tests require real terminal
geometry, responses to cursor queries and raw keyboard/SGR mouse events.

The only CLI frontend is Rich/prompt_toolkit with current mouse/rendering modules.
Do not restore Textual/plain or delete live `factory/core/tui/` modules based on
age or naming. Workers claim only their own profile's queue; do not duplicate a
worker already managed by the gateway. KVM requires the actual host capabilities,
provider and guest image. Never count skipped KVM/vsock/nft checks as passes.
Preserve durable workspace data independently of disposable guest overlays.

Bump Python/desktop once at the end, after source/docs/local context/tests and
before the final host-tool installation. Desktop release notes must match and
contain two to four short Russian points about this release. Android versions
are independent: code changes require incrementing versionCode/versionName
before rebuilding, matching Russian/English notes and a versioned APK filename.
Follow authorized local delivery rules; confirm the delivery receipt without
exposing credentials. Build from a clean source inventory and verify package
contents so deleted modules cannot survive in stale wheel/snapshot files.

Stable releases require accepted Linux, Android and Windows builds. Unit tests
do not prove clean installation, updates, rollback, signing or stability. The
final report states changes, verification, limits, artifact versions and restart
requirements. Backend/tool changes require runtime/gateway then chat restart;
CLI-only changes require chat restart. Say when no restart is needed and explain
technical effects in plain language.

### Public release descriptions and publication

- Releases serve all users, including first-time installers. Never present the
  maintainer's profile names, models, paths or setup as universal defaults. Use
  placeholders; label concrete examples explicitly.
- Separate upgrade instructions from first-time setup and optional features.
  State who needs each action; do not require changing an already correct model
  or configuration. Verify visible labels and prerequisites against the code.
- Write concise Russian and English descriptions with matching meaning: actual
  user-visible changes, supported download/install/update paths and relevant
  limits. Identify tested OS versions, signing status and preview status honestly.
- When a release changes architecture, embed a clear before/after diagram in
  the release description alongside a short explanation. Show actual component,
  ownership or data-flow changes and unaffected routes without overstating them.
  Keep editable SVG sources and use a public PNG preview that renders on desktop
  and mobile; verify the published image, not a machine-local file link.
- Publish only when authorized, from the reviewed protected-branch commit after
  required checks. Match tags, versions, assets, checksums and build provenance;
  exclude private data and VM images. Do not replace published binaries silently.
- Read back the published description, links and asset inventory. Mirror the
  same approved assets and description to Gitea when authorized. Text-only
  corrections do not require a new application version or rebuilt installers.
