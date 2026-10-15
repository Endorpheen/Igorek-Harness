# Test results

## 2026-10-15 — Repository layout and hosted platform follow-up

- Moved the optional inference entrypoint to `factory/api_server_core/server.py`
  (`python -m factory.api_server_core.server`) and its live prompt assembler into
  the same package. Removed unused duplicate worker/context helpers from that
  API assembler; the live `factory/core/prompt_assembly.py` remains unchanged.
  Moved its policy to packaged `factory/config/inference-policy.yaml`, preserving
  the protected-policy boundary and updating callers, coverage, CI and links.
- Removed a mutable Telegram emoji registry from the source inventory. Registry
  persistence now always uses the runtime data directory, including before that
  directory exists. CI configuration, Python metadata, pytest discovery and agent
  discovery files remain in their standard locations.
- Replaced the synthetic Tavily smoke value with an explicit fixture string.
  Gitleaks exclusions now require both an exact reviewed expression and its
  corresponding source path; the historical fake value remains narrowly matched
  for history checks. Source-only check: **zero findings**, redacted output.
- Local desktop: **316 passed, zero failures/skips**. Layout/inference/permission/
  demo follow-up: **47 passed plus six subtests**. Initial full layout snapshot:
  **4777 collected, 4746 passed, one failed, 30 skipped, 614 subtests**. The failure
  was the Unix HTTP fixture's early-rejection upload race; its client now still
  reads and asserts HTTP 403. Three layout cases were added after that snapshot;
  the new full run is pending. No failed test was removed or skipped.
- Hosted prior snapshot: source/secret checks, Android, Linux desktop contracts,
  native Linux first task/restart, Linux packages and Python 3.14 passed. Python
  3.13 exposed a missing annotation binding in an AST test fixture, now corrected.
  Windows file/Vault checks and package creation passed; native startup exposed
  short-path/canonical-path mismatch, now corrected before certificate validation.
  Windows desktop ACL assertions now use the Windows DACL directly, independent
  of PowerShell module search paths. Hosted reruns remain required.
- Desktop VM check has reached real manager/domain startup with a rebuilt 0.2.0
  image. Task completion remains under investigation; this is not a passing VM
  acceptance result. The original host image and user services remain untouched.

## 2026-10-15 — Igorek Harness 0.2.0 publication preparation

- Hosted CI exposed missing qemu-utils and restricted bubblewrap namespaces in
  the Linux runner, an AppArmor ABI mismatch on Ubuntu 24.04, and Windows file
  and test portability failures. These initial hosted runs are failed evidence,
  not acceptance. Scoped AppArmor profiles keep namespaces restricted to the
  expected binaries; no sandbox bypass is enabled.
- Windows parent handles now request directory-list access so delete sharing
  actually prevents rename. Recovery reads reject links and compare the opened
  file identity before reading. Terminal output acknowledgements no longer lose
  already-buffered output/exit frames when the guest closes first; a deterministic
  regression was added. Desktop fixtures check Windows ACLs rather than POSIX
  mode bits and use a real Windows Python child instead of a Unix shell shim.
  Local follow-up: **316 desktop tests passed**; **57 Python cases passed,
  two Windows-only cases skipped on Linux**. Hosted reruns are pending.
- The first desktop/Python publication milestone uses **0.2.0**.
  Native Windows and clean-OS Linux acceptance
  are explicitly deferred by the owner for source publication, not counted as passes.
- Removed the build-only advisory chain through a scoped `@electron/get` 5.1.0
  override. Clean locked npm installation reports **zero vulnerabilities**.
  Environment-proxy behavior was retained and verified with a real synthetic
  CONNECT proxy. **316 desktop tests passed**, zero failures/skips.
- Added inference ingress authentication before generation/compression, including
  paid backends, explicit compression proxies and non-loopback startup. Anonymous
  local generation remains usable. Visible policy-file targets now require a
  positively recognized read rather than a fragile mutation-command prefix.
- Ordinary guest Bash/MCP children inherit no_new_privs; the owner-approved sudo
  path remains separate. The image uses a root-owned isolated Python runtime;
  the controller refuses same-UID proc-memory/ptrace access. Focused owning
  checks passed **128 cases**, four subtests and two prerequisite skips.
- Final full Python snapshot: **4776 collected / 4746 passed / 30 skipped /
  zero failed or errors**, **614 additional subtests**, both roots, 297 files.
  An old guest-shell test initially expected a raw shell string; its expectation
  now verifies no_new_privs without a nested bwrap. No case was removed/skipped.
  Coverage is **45571/77048 statements (59.15%)** and **12678/27534 branches
  (46.04%)**. The actual optional inference middleware is now measured, increasing
  the denominator; this is not directly comparable to the earlier smaller scope.
- Android **0.1.105/code106** retains its original debug identity and adds explicit
  release-signing configuration. A permanent release identity was generated separately from debug signing. The key
  and local backup are outside Git with private permissions. Debug v2 and release
  v2/v3 signatures passed verification; certificate identities differ as intended.
  Existing phone data/installations were not changed. External backup custody and
  physical-device acceptance remain separate owner/device gates.
- A fresh **0.2.0** guest image was built from a separate copy of the old base with
  current runtime changes and verified by its content manifest. The build first
  exposed unreadable host /boot kernel permissions and an outdated uv Python
  catalog; a separately unpacked official kernel and checksum-pinned uv 0.13.1
  fixed the build without changing the host kernel or original base.
- Desktop/live VM acceptance and hosted CI are still in progress. Approved guest
  root can change guest-local networking; no host-independent root-adversarial
  network claim is made. No real Vault/auth/session refresh or service restart
  occurred. Current privacy, dependency, firmware and signing limits stay visible.

## 2026-10-15 — Final artifact verification

- Final desktop/Python **0.1.889** binaries use clean source commit
  `e70abba9977b50e6a6f2527a8fc18c12dc932dd0` (`source_modified=false`).
  Linux snapshot build **60d102ff79c3**, NSIS, AppImage and DEB were rebuilt
  after the worker logging cleanup. Their recorded source files/audio and
  embedded manifests match; all **7493 snapshot/DEB entries** passed checks.
  Windows remains unsigned and unaccepted on native Windows.
- That final installed Linux snapshot repeated all nine native stages and the
  complete temporary installation/update/rollback/uninstall/reinstall cycle.
  Synthetic persisted data survived; no user installation or service changed.
- Final source export: **1098 files**, no Git history. Exact archive extraction,
  every manifest hash, locked bootstrap, CLI help/init and scoped secret checks
  passed. **gitleaks 8.30.1 reported zero findings** with redacted output and
  the existing reviewed rules. This does not clear historical personal data.
- Android **0.1.104/code105** remains the separately identified, verified debug
  APK from `1e59581d`; later desktop logging/docs changes do not affect it.
  Lint has **zero errors, ten warnings and two informational findings**:
  RedundantLabel, LockedOrientationActivity, DiscouragedApi,
  CustomX509TrustManager, DataExtractionRules, DrawAllocation, ObsoleteSdkInt,
  StaticFieldLeak, AutoboxingStateCreation, ViewConstructor and UseTomlInstead.
  These were not hidden or suppressed; their platform/UI review remains distinct
  from the passing 318 JVM cases and physical-device acceptance.
- Checksums and per-artifact source/signing metadata accompany the local build
  outputs. Stable release, public repository setup, hosted CI, real provider/
  device/platform acceptance and unresolved isolation/report gates remain open.

## 2026-10-15 — Final publication candidate acceptance

- Real temporary Linux snapshot lifecycle passed: clean previous installation,
  update 0.1.888 → 0.1.889, busy-install refusal, rollback, reapplication,
  uninstall retaining data, reinstallation and final removal. Menu entries were
  installed/removed only under temporary XDG directories. Synthetic canaries
  survived every switch. This does not prove clean OS dependency installation.
- Installed Linux 0.1.889 passed all **nine native stages** with a synthetic
  provider: first result/progress, Vault lock/unlock, full backend/window restart,
  persisted profile/pairing/history, continued session, eight real `/demo` steps
  and actual settings-link clicks. The user's services and accounts were excluded.
- Current source-only archive inventory (**1098 files**) and every recorded hash
  passed verification. A fresh temporary environment completed locked development
  dependency installation, CLI help and noninteractive synthetic-profile init.
  No private Git history, local records or build caches were exported.
- Scoped publication secret check: **gitleaks 8.30.1, zero findings**, existing
  reviewed configuration, redacted output, exact publication file inventory.
  This is not a new Codex Security repository scan or a personal-data/history audit.
- Candidate package checks passed: staged source hashes, original audio bytes,
  NSIS/AppImage embedded manifests, **7493 snapshot/DEB entries** and no private
  state/bytecode in the sealed snapshot. Windows installer certificate directory
  is empty: unsigned. The checking harness was corrected to handle the standard
  32-bit NSIS stub as well as 64-bit PE payloads; product code was unchanged.
- Refreshed both README images from packaged 0.1.889 using only the existing
  documentation fixtures; visually checked empty credential fields and synthetic
  chat/workspace labels. Image captures do not constitute account or task acceptance.
- The final quality review removed **two development DEBUG prints** from worker
  prompt assembly. Operational/protocol diagnostics remain. Affected worker,
  stable-prefix and demo regressions passed **62 cases and 12 subtests**, zero
  failures/skips. These overlap the full run and are not added to its counts.
  The full coverage snapshot below precedes this two-line logging cleanup.
- Desktop binaries are rebuilt from the final clean source commit after this
  cleanup. Android application logic is unchanged: the checked 0.1.104/code105
  APK was delivered through the authorized helper, confirmed `ok=true`,
  `curl_exit=0`, receipt message **3000**. Permanent signing and physical-device
  acceptance remain open. No active user installation was replaced or restarted.

## 2026-10-15 — Publication evidence and original completion chime

- Replaced the previous completion recording with an original 0.8-second,
  48 kHz mono two-tone synthesis. No external samples are used. Its generator
  is retained in `tools/generate_completion_sound.py`; the serving path,
  background-only policy, persisted toggle and replay guards are unchanged.
- Fresh locked full Python run: **4741 collected, 4711 passed, 30 skipped,
  zero failed/errors**, 295 isolated files and **614 additional passing
  subtests**. Coverage: **45362/75081 statements (60.42%)** and
  **12579/26562 branches (47.36%)**. No tests were removed or newly skipped.
- Skip categories: two Windows-only contracts, one live-provider credential
  case, ten existing terminal integration cases and seventeen VM/vsock/image/
  environment cases. Actual reasons remain in the XML results; an isolated
  fixture's skip does not establish that the host lacks KVM/vsock.
- Fresh locked desktop run: **315 passed**, zero failed/skipped. Initial
  sandboxed npm/test commands failed; the normal locked installation and
  tests passed with dependency downloads and temporary loopback sockets
  permitted. No source workaround or test suppression was introduced.
- Source publication contracts, fatal Ruff checks and whitespace checks pass.
  Current media were inventoried and screenshots visually checked. Both
  project mascots are original generated assets; the Android image's origin
  was confirmed by the owner. The vendored asset inventory prose is now English.
- npm installation's eight moderate warnings trace to one cached advisory for
  `sprintf-js` in electron-builder's optional download/logging chain. Those
  packages are not shipped in the staged app. The build-time advisory remains
  open; no automatic dependency update or new repository scan was performed.
- Android dependency inventory resolved **68 debug / 65 release artifacts**
  offline, with upstream Apache-2.0 license declarations for all artifacts and
  no native `.so` entries in their JAR/AAR files. The APK lacked the full Apache
  text; it now includes that text and the variant-specific inventory, preserving
  Xterm and OkHttp notices. Android bumped once to **0.1.104/code105** with
  matching Russian/English notes; application logic did not change.
- Fresh Android JVM run: **318 passed**, zero failures/errors/skips. Offline
  lint/debug build passed. Final APK version, license/source bytes and v2
  signature passed verification; release signing/device acceptance remain open.
- Final version/packaging/audio follow-up passed **67 cases**, zero failures or
  skips. These overlap the full run and are not added to its totals. Desktop/
  Python bumped once to **0.1.889**; dependencies stayed locked.
- Public status now separates accepted automated/native Linux evidence from
  unaccepted native Windows, physical Android, signing, hosted CI, historical
  privacy, deployment-dependent claims and guest-egress architecture gates.
  User applications, credentials and services were not modified or restarted.

## 2026-10-15 — README architecture diagrams

- Replaced the ASCII architecture blocks in both root READMEs with matching
  English/Russian SVGs. This is documentation only; application code, versions,
  packages, credentials and running services are unchanged.
- Checked the queued and direct execution routes against `mobile_bridge`,
  `worker`, the ACP adapter, Telegram transport selection and desktop integration
  code. ACP bypasses the task queue; CLI/Telegram routing depends on the profile.
  ChatGPT/OpenCode workspaces retain their separate lifecycle; KVM is optional
  and Linux-only.
- Both SVGs parsed as XML, use local vector shapes/fonts only and contain no
  scripts, external images or asset references. Their README references resolve.
- Rendered both languages in Chromium at 1280 px, measured text bounds against
  the containing cards, and checked a 768 px embedded-image preview visually.
  Final diagrams have zero clipped labels. Application tests were not rerun for
  this documentation-only change; the latest runtime results remain below.

## 2026-10-15 — Composer access icon and compact question navigation

- Replaced the small diamond with a 20 px SVG exclamation icon inside a subtle
  neon circle. The 34 x 28 px control stays aligned with composer buttons;
  automatic approval and full-access colors remain distinct. Tooltips,
  accessibility labels and permission behavior are unchanged.
- Moved question navigation to the left gutter. Marks follow chronological
  order at a uniform spacing of at most 10 px, independent of answer length;
  very long threads fit the available height. Previews open toward the chat,
  while active highlighting and clicks retain their existing message targets.
- Full Python run across both roots: **4741 collected, 4711 passed, 30 skipped,
  zero failed/errors**, 295 isolated files and **614 passed subtests**. Coverage:
  **45355/75081 statements (60.41%)**, **12576/26562 branches (47.35%)**.
  Existing platform/environment skips are not counted as passes.
- Desktop Node: **315 passed**, zero failed/skipped. Locked dependencies were
  synchronized. No implementation-mirroring unit tests were added for this
  reversible visual change.
- An isolated native Electron preview completed all nine existing acceptance
  stages. Additional real DOM checks at **1920 and 1280 px** verified a left
  rail, equal compact spacing, icon size, no diamond and an actual click to
  the selected question. Screenshots were visually inspected with both a
  floating and stacked project summary. User services/accounts were untouched.

## 2026-10-15 — Contextual desktop navigation

- A shared 38-route catalog covers all 17 settings panels, major desktop views
  and session workspace tabs. Explicit `[label](igorek://ui/route)` links are
  enabled only through the trusted desktop bridge and the fixed catalog.
  Ordinary words, inline/fenced code, unknown routes and URLs with extra
  parameters do not become navigation actions. The unimplemented updater is
  explicitly marked unavailable.
- Model UI guidance comes from the same catalog, including normal desktop
  turns and native backend prompts. Navigation opens a view on user click;
  it does not grant model tools, change settings or authorize credentials.
  The demo uses these same links for the next steps.
- Full Python snapshot: **4740 collected, 4710 passed, 30 skipped, zero
  failed/errors**, both roots, 295 isolated files and **612 passed subtests**.
  One native-backend guidance case was added afterward; current inventory is
  **4741**. The final overlapping affected run passed **263 cases** and
  **20 subtests** without failures/skips. These figures are not summed into
  an invented full run. The stable full-run coverage preceding this addition
  remains recorded in the guided-demo entry below.
- Desktop Node: **315 passed**, zero failed/skipped. Catalog tests match every
  real owner panel and workspace tab; IPC and renderer tests cover trusted
  access, explicit clicks, inactive samples and preservation of ordinary text.
- Native Linux acceptance completed **nine stages**. Actual demo links opened
  Plugins and the Notifications settings panel; a normal model answer opened
  Primary model settings. Its request included the catalog guidance. A test
  initially waited for a background window's throttled frame; focusing the
  synthetic chat corrected the harness without changing production rendering.
- Native Windows installation/UI/signing remain pending. Synthetic tests do
  not read or mutate real Vault credentials and do not restart user services.

## 2026-10-15 — Guided desktop demo

- The common Linux/Windows chat now runs `/demo` as eight real model turns in a
  separate workspace: welcome, plan, Write, Read, Edit, Glob, Grep and plan
  completion. Every step has localized guidance. Tool events and file contents
  determine progression; unsupported or failed steps never count as passed.
- Full Python run across both roots: **4738 collected, 4708 passed, 30 skipped,
  zero failed/errors**, 295 isolated files and **612 passed subtests**. The 30
  platform/environment skips retain their existing reasons; they are not passes.
  Coverage: **45338/75066 statements (60.40%)**, **12569/26556 branches (47.33%)**.
- Desktop Node contracts: **311 passed**, zero failed/skipped. New tests cover
  the separate demo session, preserved drafts and refusing attached files before
  upload. Python regressions cover real file operations, cancellation, restart,
  duplicate submission, unsupported profiles and workspace/tool restrictions.
- Native Electron on Linux completed all eight acceptance stages, including the
  entire demo, actual tool results, final summary and reopened history, against
  a synthetic loopback provider and fresh Vault. No real credentials or running
  user services were used. The native CI report gate now requires this stage on
  both operating systems. Native Windows acceptance remains pending.
- Initial checks found an overly strict final-newline comparison and two isolated
  renderer compatibility failures. These were fixed; affected checks and the
  complete Python suite were rerun successfully. Initial sandbox socket refusals
  were rerun with loopback sockets permitted, without weakening tests.

## 2026-10-15 — Cross-platform desktop candidate

- Full Python snapshot: **4722 collected, 4692 passed, 30 skipped, zero failed/errors**
  across both roots and 293 isolated files; **606 passed subtests** are separate.
  Six packaging/report-gate cases were added afterward: the current inventory is
  **4728 collected**. A later 257-case affected run passed **255**, skipped the
  two Windows-only contracts and passed eight subtests. A final 91-case guard
  run passed **89**, with those same two platform skips. These overlapping runs
  are not summed or relabeled as a full 4728-case run.
- Full-snapshot coverage: **45084/74729 statements (60.33%)** and
  **12458/26404 branches (47.18%)**. Windows implementations are not exercised
  by Linux coverage; native scoped coverage is configured separately in CI.
  Subsequent Windows/build refinements are verified by affected checks; this
  denominator describes the full-run snapshot, not an invented new report.
- Locked `uv sync --locked --dev` and desktop `npm ci` completed. All desktop
  contract files passed **308 Node cases**, with zero failures/skips. Fatal
  Ruff checks, publication inventory/language/link checks and actionlint passed.
- The maintained native Linux scenario passed **seven stages** with synthetic
  data: packaged Read/Write/Glob/Grep and workspace refusal; fresh Vault/profile;
  running worker versus first-result progress; HTTPS pairing/first task;
  lock/unlock; persisted profile/cookie/visible history after restart; continued
  session. The new file subprocess initially omitted its explicit package path
  under `-I`; the harness was corrected and the complete scenario rerun.
- Windows Python 3.14.4 under isolated Wine passed runtime/worker imports, Vault
  create/lock/unlock and exclusive ownership, UTF-8 files, private DACL setup and
  DACL preservation, command refusal and a real worker's first task over paired
  HTTPS. The actual Windows payload also passed Read/Write/Glob/Grep, literal
  option-like regex and workspace escape refusal. The bundled ripgrep archive
  is version/SHA256 pinned and includes its licenses.
- These Wine results are **not native Windows acceptance**: junction creation
  is unsupported, directory-sharing behavior differs, and fresh cryptographic
  key generation failed with Wine's entropy provider. The diagnostic first-task
  test used a separate TLS fixture; production crypto checks were not weakened.
  Native Windows tests retain the strict checks and fresh certificate creation.
- A preliminary per-user Windows NSIS candidate was built on Linux. Final
  candidates use the identified clean source revision; Windows remains preview
  and unsigned. The shared build workflow does not publish releases. Its actual
  hosted Windows run, signing, voice and complete update/rollback acceptance are
  still open. Android and the working host installation were not changed.
- The common Linux AppImage build succeeded. The initial common DEB target
  required an unassigned public homepage, so DEB packaging retains the tested
  snapshot installer instead. The host Linux release also retains the previous
  package's libvirt/VSOCK components; this does not count as a live VM test.
- Final snapshot inspection found interpreter-generated bytecode caches from
  its own verification. Packaging now removes them before the inventory and
  uses `-B` for final verification; final archives must contain no bytecode cache.
  The initial affected run reported 36 fixture failures because its fake Python
  consumed exactly one interpreter flag. The fixture now accepts both valid
  flags; all 57 affected package cases passed on rerun, with no checks removed.
- The two additional Linux skips are Windows directory-handle and DACL tests.
  The previous 28 integration/hardware/optional skips retain their reasons below.

## 2026-10-15 — Direct quota and maintained native acceptance

- Current Python inventory: **4716 collected cases**. The last complete full-run
  result remains the 4702-case publication baseline below; it is not relabeled
  as a full run of this newer inventory.
- The affected quota/usage/owner/package/DEB suites passed **173 cases**, with no
  failures or skips. Read-only regressions forbid process launch, refresh/revoke,
  credential writes and retries after denial; legacy bindings and changed
  accounts are covered. Existing CLI-based fixtures were migrated to HTTP,
  retaining their confirmation, identity and credential-preservation checks.
- All desktop contract files passed **307 tests**, without failures/skips.
  The added UI case confirms an existing account without login polling.
- Actual read-only account acceptance passed: quota over HTTPS, confirmed
  identity, no CLI, no refresh/login or credential writes, and unchanged native
  authorization-file content. No real credentials or account identifiers are
  included in the evidence.
- The maintained native Linux scenario passed first-result progress,
  lock/unlock, saved profile/cookie/history after backend restart and a continued
  task. Window focus follows actual user interaction; hidden views intentionally
  defer polling/animation frames. The final packaged rerun is recorded locally.
- Native acceptance is a separate CI job and uses only synthetic data. Hosted
  execution, Windows, physical Android and VM networking remain open. Android
  source and its previously recorded JVM/lint results are unchanged.


## 2026-10-15 — Publication candidate

| Suite | Result | Scope |
| --- | --- | --- |
| Python | 4702 collected; 4674 passed; 28 skipped; 0 failed/errors | Both roots, 291 isolated files |
| Python subtests | 606 passed | Additional parameter/interaction checks, separate from collected cases |
| Desktop JavaScript | 306 passed; 0 failed/skipped | All 25 Node test files |
| Android JVM | 318 passed; 0 failed/errors/skipped | Debug unit tests |
| Android lint | 0 errors; 10 issue groups covering 31 warning locations; 2 informational issues | Dependency/AGP age, orientation, TLS and platform configuration remain visible |

Python coverage: **45058/74417 statements (60.55%)** and
**12457/26322 branches (47.33%)**. The combined coverage.py figure is 57.09%;
it is not the line percentage. JavaScript/Kotlin coverage was not measured.

Reproduce the commands in [TESTING.md](../TESTING.md). Results are not sums of
overlapping targeted runs. The accepted full run followed a 4701-case run with
six failures: the memory test assumed message matching instead of owner consent;
proxy tests and the desktop Vault adapter needed the new capability URL.
All six were corrected and the complete suite rerun. No failure was skipped.

### Why 28 cases are skipped

- 10 terminal cases already carry an explicit `running_state` integration skip.
  These are a maintained test gap; passing sibling cases do not close it.
- 10 negative VM scenarios and one PoC require explicit live-run opt-in and a
  dedicated synthetic workspace.
- Two vsock smokes require execution inside a VM and a live host listener.
- One domain smoke requires explicit opt-in and a matching built image.
- One preflight case cannot reach libvirt from the isolated test environment.
- One negative workspace-refusal case exercises an incompatible capability branch.
- One builder-absence case is skipped because an image builder is installed.
- One live MiniMax case requires a real API key, deliberately absent from tests.

These reasons do not mean the physical host lacks KVM or vsock. Native VM/image,
network and provider acceptance remain separate from isolated unit tests.

### Test-base changes

- Added regressions for authentication, pairing/revocation, filesystem races,
  bounded reads/output, private staging, backup restoration, MCP lifecycle and
  consent, proxy capabilities, memory confirmation, APK parsing, approval
  visibility, channel backpressure and Git configuration races.
- Added first-result onboarding, stale-reply, test-runner process/credential
  isolation, package inventory and portability checks.
- Retired tests only with proven-unused implementations: the Textual/plain
  interfaces, old monitor/helpers and two prototype TUI coordinator cases.
  Current terminal rendering, mouse and shared widgets retain their tests.
- Test modules now live under `desktop/tests/`; all files are discovered.
  Android loopback TLS keys are generated under ignored build output instead of
  being stored as a private-key fixture in source.
- Syntax-aware English/privacy/link checks, fatal Ruff checks, workflow syntax,
  clean-wheel installation and README wide/narrow rendering passed locally.
- After the full run, the socket build recipe/bootstrap were aligned with the
  pinned Python 3.14.4; the affected socket, libvirt and Linux package suites
  were rerun: 63 passed. The final packaging/source-contract follow-up passed 64 cases. The source checksums come from the exact official CPython tag.
- Actual packaged Electron/runtime first-task acceptance passed with a synthetic
  provider and clean Vault/history, followed by lock/unlock persistence.
  Snapshot install/update/rollback/reapply/uninstall preserved synthetic data.
  Refreshed interface screenshots were visually reviewed.

Hosted CI, native Windows, permanent Android release signing and physical-device
acceptance remain open. Passing Linux tests are not evidence for those gates.

## 2026-10-14 — Publication preparation

- The initial isolated full run collected 4573 Python tests: 4515 passed, 30 failed,
  28 skipped. An additional 601 subtests passed; these are counted separately.
- Initial measured Python coverage: 44365/75599 executable lines (58.68%),
  12116/26668 branches (45.43%). This is a baseline from the pre-fix run,
  not a claim about the final release. Desktop JavaScript and Android are separate.
- All 25 desktop Node test files passed: 302 tests, no failures or skips.
- The missing desktop test files are now included by discovery; the test files
  live in `desktop/tests/`. The Python terminal integration test resolves the
  checkout location from its own file instead of requiring `/workspace`.
- Thirty initial Python failures were addressed by repairing the test setup:
  the search-tools AST scope retains the package identity; browser tests use
  temporary home directories; VM-death tests simulate death after dispatch;
  the domain contract explicitly checks the backing image; UI fixtures include
  current state fields, workspace tabs and DOM event/move behavior.
- Targeted confirmation: 106 passed / 3 failed initially, followed by all three
  remaining cases passing after the fixture fixes; 35 subtests also passed.
  A complete final rerun is still required after the remaining source changes.
- Test execution now supports isolated processes per file, visible progress,
  per-test and per-file deadlines, JUnit reports and combined branch coverage.
- No failing tests were removed or marked skipped to obtain passing results.

## Earlier test history

Earlier counts and known gaps are being reconciled into the current testing
and release documents. Do not add results from overlapping runs together.
See [testing](../TESTING.md) and the [release checklist](../GITHUB_PUBLICATION_CHECKLIST.md).
