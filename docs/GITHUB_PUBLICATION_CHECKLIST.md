# Publication and release checklist

Use evidence for a concrete commit and artifact. An unchecked gate remains open
until it is completed or the owner explicitly accepts a documented limitation.
Public source publication and a stable product release are distinct approvals.
Stable release acceptance requires Linux, Android and Windows. The owner has
explicitly deferred native Windows and clean-OS Linux checks for the source
publication milestone; these are deferred, never recorded as passed.

## 1. Source privacy and history

- [ ] Review the complete public file inventory for credentials, private keys,
  tokens, cookies, personal data and accidental task artifacts.
- [x] Enumerate the current tracked inventory and review non-text assets,
  generated media provenance and screenshot privacy separately from text checks.
- [ ] Inspect every branch, tag and commit intended for public publication;
  removing a file from the current tree does not remove it from history.
- [x] Run the existing reviewed secret-detector configuration against the exact
  1098-file publication inventory: gitleaks 8.30.1, zero findings, redacted output.
- [x] Triage secret-detector results without logging secret values. Revoke any
  real exposed credential before publication and agree on history remediation.
- [x] Inspect current screenshots and source binary assets. Remove media with
  unconfirmed redistribution rights from future packages.
- [ ] Approve the exact public source archive and final release attachments;
  current-tree review does not clear private Git history or old packages.
- [x] Exclude local environments, Vault, databases, logs, toolchains and build data.
- [x] Mark synthetic fixtures clearly and keep test-only data out of user packages.

Evidence: source commit, detector version/configuration, scanned refs, reviewed
results and approved public inventory. A detector result alone is not a personal
data audit. Do not force-push shared private history as an incidental cleanup.

## 2. Security findings

- [x] Reconcile every unique high/critical in existing reports with current code,
  its original trigger, valid behavior and regression evidence.
- [ ] Triage medium/low separately: remediated, inapplicable or explicitly accepted
  limitation with prerequisites and rationale. Do not silently accept a risk.
- [x] Verify API/Web UI/mobile authentication, pairing/revocation, HTTPS and owner
  confirmation for privileged operations.
- [ ] Verify the active execution boundary and failure when required protection
  is unavailable. Distinguish structural tests from hardware results.
- [x] Trace the npm installation warnings to their dependency and build paths,
  distinguishing shipped code from build tooling.
- [x] Remove the vulnerable build-tool dependency chain and verify clean locked
  installation plus real proxy compatibility: zero npm vulnerabilities.

Preserve historical reports as evidence of the version they assessed. Do not
rewrite a sealed report to make it describe a later source tree.

## 3. Legacy retirement

- [x] Enumerate imports, commands, dynamic loaders, services, configuration,
  assets, tests and docs for each retired component.
- [x] Confirm that current interfaces do not use the retired implementation.
- [x] Remove its entry points/dependencies together and provide safe migration
  for old installations without deleting saved data.
- [ ] Verify affected API, desktop, Web UI, mobile, CLI and operations paths.
- [x] Check the final wheel/snapshot/APK for stale copies of deleted modules.
- [x] Keep obsolete plans and local artifacts out of the public inventory.

A file's age or matching basename is insufficient evidence of dead code.

## 4. Language and source quality

- [x] Use English for original code comments, docstrings, skills and technical docs.
- [x] Use syntax-aware checks that distinguish comments from translated UI/data
  and third-party source. Preserve existing interface languages and encoding.
- [x] Remove leftover development debug prints and check production entrypoints
  for breakpoints/debugger statements. Preserve operational and protocol output.
- [x] Run the applicable syntax, import, type and formatting checks. Explain
  remaining warnings and exclusions.
- [x] Preserve third-party license, copyright and redistribution notices.

## 5. Project presentation

- [x] The English README explains the product, primary interface and concrete
  benefits; the complete Russian version is linked and kept consistent.
- [ ] Installation, requirements, first task, update and documentation links work
  from a clean checkout or actual release archive.
- [x] Verify the source-only archive, locked bootstrap and CLI initialization
  in a fresh temporary environment; exercise the Linux snapshot installer.
- [x] Screenshots show the current application with synthetic data and no private
  account or machine details.
- [x] Supported platforms/features are separated from unverified work and plans.
- [x] Check links, anchors and narrow/wide rendering. Badges point to real resources.
- [x] Keep private working records, machine paths and conversational history out
  of public product documentation.

## 6. Stable release artifacts

- [ ] Linux: clean installation, first task, restart, data-preserving update,
  rollback and uninstall from the supported package.
- [x] Verify a temporary Linux snapshot installation, 0.1.888 → 0.1.889 update,
  busy-install refusal, nine native stages, rollback, reapplication, uninstall
  retaining data and reinstallation. Clean OS/dependency acceptance is separate.
- [x] Prepare and verify an APK signed by the explicitly authorized permanent
  release identity; retain debug compatibility and keep keys outside Git.
- [ ] Android: physical pairing/task/files/voice/background/update acceptance and
  independently recoverable signing-key backup custody.
- [ ] Windows: installable desktop package, clean machine dependencies, workspace,
  first task, browser/voice, update and uninstall acceptance.
- [x] Add shared Linux/Windows packaging, Windows OS implementations and a native
  Windows CI candidate workflow; keep its actual run/acceptance as an open gate.
- [x] Record supported OS versions, architecture, requirements and limitations.
- [ ] Exercise long sessions, multiple tasks, disconnect/recovery, background and
  restart with persisted history on all three platforms.
- [x] Build candidates from locked dependencies and an identified source commit.
  Record hashes, contents, versions, release notes and actual signing status.
- [ ] All required tests pass with exact passed/failed/skipped counts and reasons.
- [x] Record the passing full automated Python/desktop snapshot separately from
  native, hardware and physical-device gates; explain every skip category.

A missing accepted build for any of these platforms blocks a stable release.
Unsigned Android and untested Windows artifacts must not be labeled stable.

## 7. Repository layout

- [x] Keep responsibilities visible in [PROJECT_LAYOUT.md](PROJECT_LAYOUT.md).
- [x] Check imports, dynamic loading, static paths and packaging inventories
  before moving modules. Preserve public commands and persisted formats.
- [x] Group cohesive code/tests/assets/build tools without adding unused layers.
- [ ] Verify source, build, installation and actual interaction after each move.
- [x] Verify current source/build inventories, clean CLI bootstrap and temporary
  installed Linux interaction. Other-platform native gates remain above.
- [x] Inspect the public inventory independently of ignored local directories.

## 8. First-run acceptance

- [ ] A new user can install, create Vault, connect a model, choose a folder,
  create a session and inspect the first successful result.
- [x] A running executor alone does not complete the guide.
- [x] Existing setup resumes without unnecessary credential entry.
- [x] Failures, cancellation, stale replies and lock have clear recovery paths.
- [ ] Recovery and restart preserve the intended profiles and history.
- [x] Verify profile, pairing and history retention across a full Linux test
  backend/window restart, then continue the saved session.
- [ ] Optional voice/plugins/isolation follow a usable basic conversation.
- [ ] Perform clean Linux/Windows and physical Android acceptance; record where
  a newcomer needs unexplained knowledge or manual intervention.

See [the acceptance scenario](ONBOARDING_REVIEW.md).

## 9. Continuous verification

- [x] CI discovers both Python roots and all desktop test files.
- [x] Use separate fast contract and expanded native/platform scenarios.
- [x] Include Linux, Windows and Android; keep platform gaps visible.
- [x] Pin dependencies, action revisions and build inputs. Build clean sources.
- [x] Fork checks have no production credentials or privileged self-hosted access.
- [x] Publication/signing uses the owner's approval and protected environment.
- [x] Report coverage by language, lines and branches with honest denominators;
  define thresholds from measured baselines and improve meaningful gaps.
- [ ] Verify hosted CI results before claiming a passing badge or release gate.

## 10. Governance and final approval

- [x] The owner selected Apache-2.0 for original project contributions.
- [ ] Complete the dependency/asset ownership and notice inventory.
- [x] Record current first-party media provenance and verify bundled Python,
  Electron, vendored browser and Windows ripgrep notice locations.
- [x] Resolve Android debug/release runtime dependencies, review upstream license
  declarations and include their inventory, Apache-2.0 text and retained notices.
- [x] Contribution guidance and issue/PR reporting paths are usable.
- [ ] Confirm the public repository identity, description and supported topics.
- [ ] Enable private vulnerability reporting before source publication.
- [ ] Review final source/history/assets and obtain explicit publication approval.

## Evidence record

Record the source commit, platform, artifact hash/version, date, exact command,
result and limitations for each accepted gate. Keep credentials and personal
acceptance data out of the record. Current test evidence is maintained in
[TESTING.md](TESTING.md) and [TestLOG.md](development/TestLOG.md).

## Evidence reviewed — 15 October 2026

Latest milestone: Python/desktop **0.2.0**, Android **0.1.105/code106**;
4776 Python cases (4746 passed, 30 skipped, 614 additional subtests),
316 Node and 318 Android JVM tests. The 0.1.889 matrix below is retained
as the previous artifact baseline; newer native/CI evidence is recorded in TestLOG.
The builder advisory is resolved; the previous dependency-chain paragraph
below remains historical evidence, not an unresolved warning.

| Scope | Accepted evidence | Limit |
| --- | --- | --- |
| Python | Fresh locked run: 4741 collected, 4711 passed, 30 skipped, zero failures/errors; 614 additional passing subtests, both roots, 295 isolated files | Skips remain open where the capability is required |
| Python coverage | 45362/75081 statements (60.42%); 12579/26562 branches (47.36%) | Counts do not establish per-feature or other-language coverage |
| Desktop Node | Fresh locked run: 315 passed, zero failures/skips | Contracts do not prove native Windows interaction |
| Native Linux 0.1.889 | Nine stages from a temporary installed snapshot: clean Vault/profile, first task, lock/unlock, full test backend/window restart, retained profile/pairing/history, continuation, eight real demo steps and settings links; install/update/rollback/uninstall retaining data | Synthetic provider; not clean OS prerequisites, real OAuth consent, cloud-model quality or long-session acceptance |
| Windows 0.1.889 | Clean-source NSIS candidate; staged payload hashes and embedded manifest verified. Earlier 0.1.888 Wine checks exercised the worker and demo | Unsigned; Wine is not native Windows acceptance |
| Android 0.1.104/code105 | Fresh offline run: 318 JVM tests, lint/debug build, APK version/assets/notices and v2 signature verified | Debug key; permanent signing and physical-device acceptance pending |
| Source presentation | Bilingual READMEs, local links and 420/1280 px rendering; matching EN/RU architecture SVGs checked for text bounds and 768/1280 px rendering | Public GitHub identity and hosted results are not configured |
| Source archive | 1098 manifest-verified files, no private Git history; locked development bootstrap, CLI help and synthetic init passed in a fresh temporary environment | Final personal-data/attachment review and owner publication approval remain open |
| Current assets | Source binary inventory and synthetic screenshots reviewed; project-generated mascots confirmed; original synthesized completion chime replaces the previous recording | Previous packages/history are not cleared by replacing today's asset |

The 30 Python skips comprise two Windows-only contracts, one live-provider
credential test, ten existing terminal integration cases and seventeen
VM/vsock/image/environment cases. Their actual prerequisites and fixture
conditions are retained in the test reports. A skip message from an isolated
test process is not proof that the physical host lacks KVM or vsock.

Historical security reports remain sealed. The six selected findings were
remediated previously. The broader historical report has 38 remediated mechanism
groups, five inapplicable groups, three deployment-dependent review groups and
one unresolved guest-egress boundary. No new repository security scan was run.
The three review groups remain open; missing deployment evidence is not risk
acceptance. [Isolation limits](architecture/ISOLATION.md#important-limits)
describe the independent VM networking blocker.

The quota counter uses HTTPS directly, without bundled Codex CLI. Existing
confirmed bindings remain readable; failed read-only checks do not refresh,
log out or rewrite credentials. All acceptance in this review uses temporary
data and synthetic accounts. The owner's running applications are unchanged.

## Remaining gates by required action

| Gate | Required next evidence or decision |
| --- | --- |
| Public source approval | Confirm the GitHub identity and exact reviewed source-only export; do not publish private Git history or old binary attachments |
| Historical privacy/old media | Review every ref actually proposed for publication; the source-only export is a prepared alternative, not permission to publish |
| Private reporting and CI | Enable GitHub Private Vulnerability Reporting on the selected repository and obtain actual hosted workflow results |
| Dependency review | Finish optional guest-image distribution notices; update or explicitly disposition the build-tool advisory |
| Existing report follow-up | Resolve the three deployment-dependent proof gaps and implement/accept the required guest-egress architecture independently of guest root |
| Linux stable acceptance | Clean supported OS/dependencies, installer integration, real provider consent, browser/voice, longer sessions and recovery |
| Windows stable acceptance | Actual Windows install/update/uninstall, file/Vault/ConPTY/UI/browser/voice checks, real provider consent and signing decision |
| Android stable acceptance | Permanent release signing identity and physical pairing/files/voice/background/update checks |
| Final stable approval | Identified accepted artifacts on all three platforms and explicit owner release approval |

See [current tests](TESTING.md), [test results](development/TestLOG.md),
[first-run acceptance](ONBOARDING_REVIEW.md) and
[asset notices](THIRD_PARTY_NOTICES.md). Unchecked gates remain open. Passing
candidate checks must not be described as a stable three-platform release.

The locked npm installation reported eight moderate package warnings from one
advisory, [GHSA-hp3w-g68c-fv3c](https://github.com/advisories/GHSA-hp3w-g68c-fv3c),
for `sprintf-js` 1.1.3. Its installed dependency chain is
`electron-builder → app-builder-lib → @electron/get → global-agent → roarr →
sprintf-js`. These are development/download tools, excluded from the staged
application's empty npm runtime dependency inventory. This narrows the affected
surface; it is not an acceptance of the remaining build-time risk. No additional
audit was invoked: the existing `npm ci` response and installed dependency tree
provided this evidence.
