## 2026-10-27 — OpenCode Go routing verification

Focused tests covered catalog route validation, selected-model preservation, desktop session selection, Google streaming and tool calls, generic Responses tool calls, and host/KVM dispatch and credential proxy seams. The locked Python run covered 589 files in two groups: seven required a short `/tmp` path, while 582 ran with an external temporary directory. Together they collected 11,557 tests: 11,536 passed, 21 were skipped, and 914 subtests passed, with no failures. Fifteen skips require live KVM or vsock prerequisites, and six require Windows; they are not passes. Two native coverage data paths could not be combined, so the coverage total is not claimed as a reliable aggregate. This run preceded the final small Vault and streaming error/cancellation changes; separate targeted runs passed 96 Vault/catalog tests and 76 Responses/Google tests, followed by the final generic-response and Google cancellation regressions. Exact-commit CI is still required.

The locked Desktop suite passed 531 tests. A native Electron run exercised profile selection and scrolling to the end of a 37-model list. Android unit tests passed 506 cases, lint passed, and APK 0.1.138 (139) built with the previous debug signer. A separate Android emulator did not finish booting; no local Android native or physical-device acceptance is claimed. Earlier full Python attempts failed because of temporary-disk quota, Git checkout context, and long Unix socket paths; a provider compatibility regression was fixed and retested. An earlier Desktop run used stale local dependencies and failed security checks. None of these attempts is counted as a passing run. No live OpenCode subscription inference or real KVM acceptance has been performed.

## 2026-10-27 — OpenCode Go model menu, partial implementation

The shared session-options path exposes live OpenCode Go model IDs matching the configured profile transport. Focused catalog and session tests passed 23 cases, including unavailable-catalog and unsupported-transport rejection. The catalog is public and does not establish account-specific entitlement. Cross-transport routing and Google/Responses API models remain unimplemented and no real provider inference or installed-app acceptance was performed.

## 2026-10-27 — Mercury profile icon

Android `ProfileChoicesTest` passed with Mercury fallback and explicit server identity precedence. Debug APK 0.1.137 (138) built successfully with the Inception vector mark; package version was verified. No emulator or physical-device visual check was performed.

## 2026-10-27 — OpenCode profile mark

Android debug APK 0.1.136 (137) built successfully with the OpenCode mark geometry checked against the upstream logo component. The package version was verified; no emulator or physical-device visual check was performed.

## 2026-10-27 — Android provider labels and icons

The Android `ProfileChoicesTest` suite passed, including legacy OpenRouter and OpenCode icon fallback and server-provided identity precedence. Kotlin compilation and release-note validation passed. No emulator or physical-device check was performed for this change.

## 2026-10-27 — Compact profile menus

Desktop native Electron acceptance exercised collapsed and expanded profile lists with thirteen alternatives, bounded scrolling, model and profile selection, keyboard navigation, busy rejection and preserved drafts. The provider icon and session model checks passed 53 Python tests across three files. Android unit tests passed 505 cases and lint passed; a separate Android 14 emulator acceptance verified compact and expanded menus, profile/model selection and non-empty pixels in all 13 provider icons. The first visual inspection revealed transparent Android icon images; these were corrected before the successful emulator check. No physical-device acceptance is claimed.

## 2026-10-27 — Release 0.2.27 preparation

Python and Desktop were advanced to 0.2.27 with matching Russian Desktop notes; the separately changed Android application is 0.1.127 (128) with matching Russian and English notes. After the version change, 58 selected recovery, installation, staging and runner cases passed. Offline lock consistency and publication contracts passed, and the reviewed source snapshot passed the secret scanner. The earlier full local Python run preceded the version and release-note changes; release acceptance requires checks on the exact release commit.

The first Windows package check found that a history regression asserted a literal LF separator while Windows writes CRLF. The assertion now requires the platform's exact line separator and still verifies the original bytes and both recovered records. The first Windows run is a failure; the corrected commit requires a new full CI run.

## 2026-10-27 — Isolated current Python package acceptance

A wheel was built from an independently copied tracked-source inventory; all 511 copied files still matched the working tree. Its 405 Python modules match the source bytes, no Python source outside the excluded test package was missing, and all six changed runtime modules were present. `igorek_agent_factory-0.2.26-py3-none-any.whl` SHA-256: `443f5aecd7ce91f5a5aa3ddad1c54d6fb768d7aec99dc9c3a2c260d9b2987005`. The wheel was installed into a separate environment synchronized from the lockfile; imports were verified to resolve inside that environment, CLI help and dependency compatibility passed, and ten mobile recovery tests passed against the installed package. The first import attempt omitted the disposable data directory and failed to open a database; it was not counted as a pass. No owner installation or service was changed. This is not a Desktop installer update/rollback or Windows acceptance.

## 2026-10-27 — Final split Python run

An isolated environment was synchronized with `uv sync --locked --dev`. Both Python roots ran as 577 files in two groups: six guest-path-sensitive files with one worker under `/tmp`, and 571 files with two workers under a separate short temporary directory. Results: **11,434 collected, 11,413 passed, 21 skipped, 906 subtests passed, zero failures or errors**. Hashes of 1,550 source files remained unchanged. The temporary directory was removed after completion. This is not CI's four-worker execution. Fifteen skips concern live KVM/vsock requirements and six concern native Windows. The coverage report exists but two native data files could not be combined, so its percentage is not a reliable aggregate comparison. Detailed summaries and source hashes are in `build/verification-testing-quality-20261027/split-final-current/`.

## 2026-10-27 — Final locked Desktop suite

In a separate source copy, `npm ci` installed the lockfile dependencies and `npm test` passed **521 tests**, with zero failures or skips, under Node 24.19.0. The first run used a long workspace temporary path and had 511 passes and ten `EINVAL` Unix-socket path failures; the unchanged suite passed with a separate short temporary directory, which was removed after completion. npm audit reported zero known vulnerabilities at installation time. This is a Node suite, not native Electron or Windows installation acceptance. Logs: `build/verification-testing-quality-20261027/desktop-final.log` and `desktop-final-short.log`.

## 2026-10-27 — Configuration and documentation risk review

Configuration, publication and Git safety contracts passed 53 tests; eleven Wiki import/citation tests passed in the existing documentation environment. The first Wiki unittest attempt using the Python test environment failed one module import due to missing BeautifulSoup; it was not counted as a pass. Offline `uv lock --check` passed. A strict Wiki build in a separate directory and local-link check passed with 77 HTML pages and 6,312 checked links. Wiki content was not modified or published; browser and external link availability were not verified. All eleven map areas now have a selected-risk review and explicit limitations, not an exhaustive all-path acceptance.

## 2026-10-27 — Packaging and update risk review

Five focused install, staging-inventory, runner and package files passed 107 tests in an isolated project temporary directory. An initial `/tmp` run had 10 failures and 38 errors when fixtures encountered `EDQUOT`; its 59 passes do not make it a successful run. Afterward only the 112 MiB temporary directory from that failed run and the 778 MiB test-owned successful fixture directory were removed. Synthetic installation and rollback checks are not clean installation of the current build or native Windows acceptance. Ten of eleven areas now have a documented review.

## 2026-10-27 — Tool-server and integration risk review

MCP stdio/provider-error contracts, Telegram allowlist denial and ACP execution/session cases passed 74 tests with controlled providers and local data. No real external messages were sent. The risk map records that live outages and all external side effects are not established by this focused set. Nine of eleven areas now have a documented review.

## 2026-10-27 — Android risk review

All 199 tracked Android files in the current working tree match the isolated copy used for the earlier 483-test JVM, lint and debug-assembly baseline. Existing endpoint checks include a real local TLS redirect to a separate plaintext listener and verify that the bearer request does not reach it. No new Android mutation or device acceptance is claimed. Eight of eleven map areas now have a documented review.

## 2026-10-27 — Desktop updater mutation and risk review

The existing foreign-redirect test passed even when the allowlist check was removed: the artificial server kept redirecting until the separate hop limit failed. A new assertion verifies that the updater makes no request to the foreign destination. In a copied source, both selected tests passed unmodified; after removing the allowlist condition, the old test still passed and the new test failed (five requests instead of one). All 16 updater tests passed on the original working tree under Node 24.19.0. The mutation uses a synthetic fetch and the locally installed dependencies, not the earlier clean locked baseline of 520 Desktop tests. An initial Node invocation with a name filter matched no individual tests and was not counted as evidence. Seven of eleven map areas now have a documented review.

## 2026-10-27 — Web UI and CLI risk review

Focused hostile-origin, missing-pairing, path traversal, approval state, terminal route and native CLI working-directory checks passed 31 tests and 32 subtests. CLI acceptance used an isolated real POSIX PTY with geometry and cursor responses; the chat body was substituted. Browser behavior and all WebSocket lifecycle paths are not established by these checks. Six of eleven areas now have a documented review.

## 2026-10-27 — Mobile bridge and API risk review

On the current source, selected submission recovery, finalization, local worker/provider, foreign approval and bridge server cases passed 139 tests with temporary data. The risk map identifies the current source hashes and distinguishes these local checks from physical-device and deployed-gateway acceptance. Five of eleven mapped areas now have a documented review.

## 2026-10-27 — Orphan mobile message recovery

When the source task was deleted, failure after creating its replacement but before reassigning the history caused a retry to create two tasks for the same queued message. A regression reproduced two tasks before the fix. The replacement now records the source task and queue row identifiers so retry can reuse it, including after a child process exits at that boundary. History reassignment uses the session write lock, and only the selected queue rows are consumed: a message arriving after the snapshot remains pending. The related mobile, queue and worker suite passed 135 tests and five subtests, followed by ten focused recovery tests. The full current tree and interruption after queue consumption but before publishing the requeue event remain unverified.

## 2026-10-27 — Isolation risk review

Three structural isolation suites passed 95 tests and 103 subtests with ten live probes skipped using short `/tmp` paths. A first run with the long project temporary path produced three Unix-socket path-length failures (92 passed, ten skipped, 103 subtests); the unchanged assertions passed on rerun. The previously attempted KVM guest still did not boot, so real guest isolation remains unverified. Four of eleven risk areas now have a documented review.

## 2026-10-27 — Security, vault and approval risk review

Selected API authorization, execution-policy, private-file and artifact failure checks passed 39 tests; separate vault lifecycle, secret-redaction and foreign-approval checks passed 85. The risk map records the selected scenarios, earlier targeted mutations and limits. This is a focused review rather than a security certification. Three of eleven areas now have a documented review.

## 2026-10-27 — Worker and workflow risk review

The selected heartbeat race, worker death, queue lifecycle, stream failures, workflow guards and local-provider mobile-to-worker checks passed 27 tests using Python 3.14.4 and requests 2.34.2. The first attempt using `.venv` failed collection because requests was absent; it is not counted as a pass. Prior controlled mutations and bounded repetitions are documented with their limits in the risk map. Two of eleven map areas now have a documented review.

## 2026-10-27 — History and scheduling risk review

Rechecked the existing and new history, archive, delivery and corrupt-result cases together: 33 passed on Linux using isolated temporary data. This records the selected failure boundaries and remaining power-loss limitation in `docs/development/reports/repository-risk-map-2026-10-27.md`. The previously measured bounded race repetitions and targeted mutations remain separate evidence. This is one reviewed area of eleven, not an assertion that every history or scheduler path is verified.

## 2026-10-27 — Pending mobile message resumption

A failure while building a requeued task prompt reproduced a lost pending message: the queue item was already marked consumed although the task remained finished. Existing resumption tests covered successful requeueing, not this interruption. The pending snapshot is now checked and consumption commits in the same SQLite transaction as the task and its round metadata; a process-shared task lock also serializes this transition with mobile finalization. A prompt failure preserves the item, and a SQLite trigger rejecting consumption rolls back the task transition. Both cases allow a successful retry. The related mobile, queue, worker, media and recovery suites passed 149 tests and five subtests. This does not cover power failure, the separate orphan-task path, or prove all concurrent interleavings.

## 2026-10-27 — Mobile response finalization recovery

A simulated metadata failure reproduced a duplicate assistant response on retry. The prior repeat-finalization test covered only successful calls. Finalization now records a turn identifier with the response and checks it on recovery; follow-up rounds get distinct identifiers committed with requeueing. The regression passes after an actual child process exits at the same boundary, and a failed attachment event is replayed. Identical responses in two legitimate rounds remain separate. The related recovery, mobile service, media, submission and worker suites passed 139 tests and five subtests. The latest complete Python run predates these source changes. The checks do not establish power-loss durability or exactly-once memory-provider effects before the response is recorded.

## 2026-10-27 — Full Python run and test uniqueness audit

Before the test-only deduplication below, all 576 files ran in two groups: 11,425 collected, 11,404 passed, 21 skipped, 906 subtests passed, zero failures or errors; 1,549 source hashes stayed unchanged. Six guest-path files used `/tmp` with one worker and 570 used a separate short directory with two workers. This differs from CI's four-worker run. Two native coverage data files could not be combined, so coverage is not a reliable comparison. The temporary directory was removed after all processes exited.

Compared selected earlier and new tests on four identical fault injections. All four faults survived the selected earlier tests and were detected by the corresponding new checks. One redundant new thread-based delivery test was removed; the retained process test's readiness synchronization and child source-path checks were strengthened. Both retained delivery tests passed; the process test passed on original code and failed on the no-lock mutation. These test-only changes postdate the full-run snapshot and were validated separately, not by another full run. Details: `docs/development/reports/test-uniqueness-audit-2026-10-27.md`.

## 2026-10-27 — Partial UTF-8 history recovery

A truncated multibyte UTF-8 character caused history and metadata reads to fail before a retry could recover a message. History readers now process records by line and skip undecodable records without rewriting the original bytes; metadata reading decodes only its own line. Tests cover the first and follow-up message, both ASCII and truncated UTF-8 fragments, and refusal to rewrite damaged bytes during metadata updates. Related checks passed 53 tests; the five history write tests passed separately. Two targeted mutations disabling recovery in either reader were each detected. This extends the existing partial-history defect, rather than adding another independent defect to the count.

## 2026-10-27 — Recovery after partial history write

A partial JSONL record without a terminating newline caused the next recovered message to be concatenated to the malformed fragment and disappear from history. Both initial and follow-up mobile submissions reproduced the loss. Appending now separates an unterminated final line while retaining its existing bytes. A valid final record without a newline is also retained. The focused history/recovery suite passed 51 tests. In a controlled copy, removing the separator caused all three relevant cases to fail; the baseline passed. The full passing Python run predates this change.

## 2026-10-27 — Full split Python run after fixture isolation

All 576 test files ran in two groups: 11,419 collected, 11,398 passed, 21 skipped, 906 subtests passed, no failures or errors. Six guest-path tests used `/tmp` and one worker; the other 570 used an isolated short temporary directory and two workers. Source hashes for 1,549 files were unchanged. This differs from CI's four-worker run. Six skips require Windows and 15 require live KVM/vsock conditions. Two native coverage data files could not be combined; the reported coverage percentage is not a reliable comparison.

A concise before/after inventory is in `docs/development/reports/testing-progress-2026-10-27.md`; group summaries, hash inventory and combined output are in `build/verification-testing-quality-20261027/split-full-run2/`. The test-owned temporary directory was removed after all processes exited.

## 2026-10-27 — Split full run and schedule fixture isolation

A 576-file Python run used six guest-path-sensitive files with `/tmp` and 570 files with a separate short temporary directory. Source hashes for 1,549 files did not change. The six files passed (167 passed, one live-vsock skip, 35 subtests); the other group had 11,230 passed, one error, 20 skips and 871 subtests. The error was a 60-second timeout while the schedule fixture fetched a real external model catalogue; this was not a clean full run. Two native coverage data files could not be combined.

The schedule fixture now substitutes unavailable local catalogue responses for both external catalogue fetches; it retains the schedule, storage and delivery assertions. Schedule/recovery-related checks subsequently passed 52 tests. This fixture change was made after the full-run source snapshot, so the earlier run is not a full verification of the changed tree.

## 2026-10-27 — Complete two-worker Python run with nonstandard temporary root

The full 576-file run completed with two workers: 11,419 collected, 11,359 passed, 38 failed, 22 skipped, 906 subtests passed. It is not a passing full run. All failures were in six files: one browser path-length assertion and 37 isolation scenarios whose test fixtures passed the nonstandard `/home` temporary path across a boundary that denies host paths. The same six files were rerun without changing assertions using the usual `/tmp`: 167 passed, one real-vsock check skipped, 35 subtests passed. A real VM or vsock smoke was not performed. Two native coverage data files could not be combined, so coverage is not reliable for comparison. The test runner's job count was reduced from CI's four to two to limit peak disk use. Results: `build/verification-testing-quality-20261027/full-worker-run/results5/summary.json`; the owned external temporary directory was removed after completion.

## 2026-10-27 — Concurrent messages in one session

Two independent services submitting different messages to the same idle session could both observe no active task and create separate tasks. A deterministic test reproduced the race. Submission now uses a process-shared session lock as well as the request-key lock. Checks with two spawned processes confirm one task plus one steer; a separate-session test confirms independent sessions do not wait on that lock. Removing the session lock in an isolated copy causes the process test to fail.

Mobile service, store, events, steer, failure recovery and local end-to-end checks: 188 passed and five subtests passed. Full-suite acceptance remains unconfirmed due to the documented quota limitation. Mutation logs: `build/verification-testing-quality-20261027/session-mutation-*.log`.

## 2026-10-27 — Separate mobile store compatibility

A regression test showed the initial steer recovery fix put a keyed message into the wrong database when `MobileService` used a separate `MobileStore`. The queue and its recovery record now share the worker task database; the event and its deduplication marker commit together in the mobile store database. This preserves the injectable separate-store configuration without claiming atomicity across the two databases. Retries bridge the commit boundary.

Combined mobile/queue/event/end-to-end checks: 183 passed, five subtests passed. Separate tests for atomic event-marker rollback passed in both shared and separate database configurations (2 passed). A mutation disabling the new event-marker check caused a uniqueness error and was detected; the attempted event was rolled back, so this result demonstrates failed recovery, not duplicate delivery. Full-suite acceptance remains blocked by quota.

## 2026-10-27 — Keyed follow-up recovery

Keyed follow-up messages now store the steer queue row and its recovery data in one SQLite transaction. Recovery writes missing history, records its event once and restores the receipt, including when the steer has already been consumed. Tests cover database rollback, history/event/receipt failures, concurrent keyed submissions, already-consumed messages and actual process termination. The combined mobile/service/store/events/queue/end-to-end checks passed 179 tests and five subtests.

A mutation that removes the event-deduplication guard was detected by the event-after-failure case. An initial apparent survivor was invalid because pytest imported the original checkout; the repeated experiment explicitly asserts the imported source path. The full suite after these changes is not confirmed because of the recorded disk quota. Logs: `build/verification-testing-quality-20261027/steer-mutation-*.log`.

## 2026-10-27 — Partial mobile submission recovery

For a new keyed task, persist the original submission data with the task, serialize same-key senders across processes, recover missing history, event and receipt without deleting or cancelling the task, and make the submission event idempotent. Eight focused scenarios passed, including history/event/receipt failures, two service instances, process death and already-processing or completed task status. A broader compatibility run before the last three cases passed 144 tests and five subtests.

The analogous failure for a follow-up steer message remains open: a failure after enqueue but before history write leads to two queued steer entries on retry. The explicit regression currently fails and is retained. This patch does not cover that case and has not passed a full suite; disk quota limitations remain.

## 2026-10-27 — Mobile submission interrupted after enqueue (open defect)

A controlled failure of the session history append after a mobile task is created reproduces an issue: retrying the same idempotency key finds the still-active task and enqueues the same text again as a steer message. The database contains one task and the history shows one message, but the steer queue contains an unintended duplicate. Regression `tests/test_mobile_submission_failure_recovery.py` currently fails and is intentionally retained. No fix or full-suite pass is claimed. The recovery design must account for task creation, journal append, event and receipt boundaries, including concurrent senders and already-running tasks; deleting or cancelling an already claimed task would risk data loss.

## 2026-10-27 — Mobile submission to worker with local provider

Added a full local path using the actual `MobileService`, SQLite queue and separate `Worker` process, with an isolated loopback OpenAI-compatible synthetic provider. Normal streamed output is persisted and an idempotent repeat does not create another task or user message. A stream ending without a terminal event is not reported as success. Both cases and related worker/stream tests passed: 20 tests. No real model or external service was contacted.

A deliberate removal of the stream-terminal check caused the interrupted-stream scenario to fail, while the normal scenario passed. The first mutation attempt was invalid because the spawned worker imported the original source, rather than the temporary mutant; its result is discarded. The test now checks the worker's actual source path before execution. Experiment logs: `build/verification-testing-quality-20261027/e2e-*.log`. The latest additions have not yet had a successful full-suite run due to the recorded quota limitation.

## 2026-10-27 — Short-path full-run attempt and focused recheck

A private temporary directory under `/home/end0` avoided long AF_UNIX paths: 43 selected socket tests passed. The subsequent 574-file run was interrupted after `dpkg-deb` reported disk quota exhaustion on that filesystem; it is not a successful full run. Its owned temporary processes were stopped and its temporary directory removed. A focused recheck of worker recovery, heartbeat races, session and scheduled delivery, and the runner passed 49 tests. Full post-change validation remains outstanding.

## 2026-10-27 — Worker heartbeat race and test runner resilience

A deterministic SQLite/process interleaving reproduced incorrect finalization of a live task when a heartbeat arrived after stale selection. Both `processing` and `cancelling` failed before the fix. Cleanup now rechecks the timestamp, status and profile in the update and reports the number actually finalized. Focused worker tests: 36 passed; the two race cases passed ten repetitions (20 executions); independently removing either freshness condition produced its intended failure. A separate actual child process claimed a task and was terminated; artificially aging its timestamp led to dead-letter without losing its input or touching another profile (1 passed).

An actual disk quota failure exposed a second problem: missing or invalid JUnit XML could crash the runner, or a missing report could be counted as success. Three new cases reproduced the failure; the runner now records a report error and nonzero status. Targeted runner/coverage tests: 6 passed.

Full validation after the changes is not complete: attempts hit a user `/tmp` quota, and a long project-based temporary path caused AF_UNIX path-length failures. The socket case passed when rerun with the normal short path. These runs must not be recorded as successful; the previous 11,366-pass run predates the worker and runner changes. Logs and mutations: `build/verification-testing-quality-20261027/`.

## 2026-10-27 — Isolated Android baseline

An isolated source copy matching all 199 tracked Android files in the current working tree, including pre-existing user changes, passed offline `testDebugUnitTest`, `lintDebug` and `assembleDebug` with Java 17/Gradle 9.8.1: 483 unit tests, 65 suites, no failures, errors or skips. The first incomplete-copy run was interrupted after its missing shared emoji assets were noticed; the successful run included the shared emoji, renderer and diagnostic fixtures. Gradle reported deprecation warnings. No emulator, physical device, installation, signing or delivery was performed. The generated build and lint reports are in `build/verification-testing-quality-20261027/android-source/`.

## 2026-10-27 — Locked desktop dependency baseline

An initial Node run against the existing desktop `node_modules` failed dependency security checks; installed Electron 44.7.0, Undici 7.30.0 and fast-uri 3.1.8 differ from the lockfile. An isolated copy of tracked source then ran `npm ci` with Node 24.11.0/npm 11.6.1 and the committed lockfile: 234 packages installed, npm audit reported zero known vulnerabilities, and `npm test` passed all 520 tests with no skips or failures. The original installation was not modified. The earlier failure is not evidence that the locked source or a published package is vulnerable; the local installation remains stale. This is a Node test run, not a native Electron UI or Windows acceptance. Log: `build/verification-testing-quality-20261027/desktop-locked.log`.

## 2026-10-27 — Repository risk inventory and HTTP authorization mutations

Inventoried 1,741 tracked files across factory, desktop, Android, tests, tools, documentation, workflows and root configuration; component boundaries and pending evidence are recorded in `docs/development/reports/repository-risk-map-2026-10-27.md`. This is an inventory and risk plan, not a claim that every component has been reviewed.

An isolated copy of the actual HTTP handler passed 11 selected tests with a local HTTP server. Five targeted mutations in key verification, GET/POST authentication and origin policy were each detected by their intended assertions. The route handlers were synthetic. Detailed original and mutation logs are in `build/verification-testing-quality-20261027/security-*.log`. No production behavior changed in this experiment.

## 2026-10-27 — Scheduled delivery race and recovery

Two independent service instances could append the same scheduled message concurrently. The shared cross-process session lock now covers the history check and append. Three targeted tests pass, including two spawned processes and forced process death after the history write; five consecutive runs passed (15 executions). Removing the lock in a temporary copy makes both concurrency checks fail.

Full isolated Python run after the fix: 572 files, 11,387 collected, 11,366 passed, 21 skipped, 906 subtests passed, no failures or errors. Two native coverage data files could not be combined; do not compare its percentage with earlier runs.

A separate synthetic KVM attempt failed before guest startup: libguestfs/supermin cannot read the host's root-only kernel. Its own cleanup receipt reported no leftovers. This is not a live isolation pass. Details and progress: `docs/development/reports/testing-quality-2026-10-27.md`.

## 2026-10-27 — Risk-focused file resilience and mutation sampling

Both Python roots: 571 files, 11,384 collected, 11,363 passed, 21 skipped, 906 subtests passed, no failures or errors. The run used an isolated environment from `uv sync --locked --dev`; two native coverage data files could not be combined, so its coverage percentage is not a reliable comparison. The final POSIX-only mode assertion adjustment was rechecked with all three focused tests and the mutation experiment after the full runner had already executed that file.

Four manually selected mutations: the selected old cases caught one; after the new cases all four were caught. Ten repetitions of the three new and three existing cross-process tests passed (60 executions). Focused isolation and process checks: 55 passed, 20 subtests passed, 10 live KVM checks skipped. Initial sandbox run could not use local sockets; the successful repeat was outside the sandbox. The web UI file completed after a long tail; no hang was established. Desktop npm tests were not run because npm is unavailable in this environment. No Windows, live VM or power-loss acceptance was performed.

Details, fault models and limitations: `docs/development/reports/testing-quality-2026-10-27.md`. No runtime code was changed.

## 2026-10-26 — Per-chat model selection and final coverage pass

Original 448-file Python goal scope: **88.5871% → 88.5875%**. The isolated Python run collected 11,381 tests: 11,360 passed, 21 skipped, 906 subtests, no failures or errors. The skips require their respective platforms.

Verified per-chat model routing, idle-only switching, profile isolation and retained history. A native Electron fixture exercised both model choices; 520 locked-dependency desktop cases passed. Two file-mode fixtures were rewritten without changing assertions to avoid false security alerts; all six affected cases passed afterward. Android unit tests and the debug APK build passed; a physical device was not tested.

Report: `build/verification-coverage-goal90-20261026-checkpoint23`.

## 2026-10-26 — Verified unused delegation helper (unreleased)

Goal basis clarification: retain the original 448-file product scope for the 90% target, with test files excluded. On that same scope the result was **88.5856% → 88.5871%**. The expanded all-Python figure below remains an additional metric and does not change the goal.

Expanded Python product-line coverage: **87.3642% → 87.4572%** (+0.0930 percentage points). Removing an unused private helper accounts for an estimated **+0.0063 points** by changing the denominator; it is not coverage gained from tests. Both primary Python test roots: 11,365 collected, 11,344 passed, 21 existing platform skips, 906 subtests, zero failures or errors. Eleven Wiki unit tests passed separately with coverage merged. The existing Wiki link checker also passed (6312 local links) and contributed 75 newly measured lines; it was not run in the preceding checkpoint. The 365 pre-existing exclusions are unchanged. 1738 source and fixture hashes verified. Full run: 439 seconds.

Removed a private delegation-prompt helper with no in-repository references. Repository history shows it was not used even at its introduction; active delegation uses the shared prompt assembler. Ten delegate cases and seven subtests passed before and after removal in an isolated copy and in the project. Strengthened the existing test for goal and working-directory propagation. Unknown third-party code could still call the private name; no real external model was used in these focused checks.

Reports: `build/verification-coverage-goal90-20261026-checkpoint22`. No version bump, release, installation, or push.

## 2026-10-26 — Full Python scope and stream failure recovery (unreleased)

Expanded Python product-line coverage: **87.3642%** (first baseline across 466 files, including tools, packaging and Wiki hooks). For a comparable view on the exact previous file set: **88.5669% → 88.5856%** (+0.0187 percentage points). The expanded overall percentage is not directly comparable to earlier reports. Both primary Python test roots: 11,365 collected, 11,344 passed, 21 existing platform skips, 906 subtests, zero failures or errors; An additional 11 Wiki tests passed in their separate environment; their coverage is merged. The 365 pre-existing exclusions remain unchanged. 1738 source and fixture hashes verified. Full run: 473 seconds.

Added tests for provider timeout, disconnect and interrupted tool-call data, and a helper-authored test for failed tool-result persistence. Two mutations of stream handling and two of persistence failure behavior were detected. Added source roots to measure all tracked non-test Python files; test files remain omitted only from coverage reports. No runtime behavior change in this checkpoint.

Reports: `build/verification-coverage-goal90-20261026-checkpoint21`. No version bump, release, installation, or push.

## 2026-10-26 — Verified legacy removal and schedule recovery (unreleased)

Python product-line coverage: **88.5168% → 88.5669%** (+0.0501 percentage points). Removing five private unused helpers accounts for an estimated **+0.0489 points** by changing the denominator; it is not test-driven coverage growth. Both test roots: 11,361 collected, 11,340 passed, 21 existing platform skips, 906 subtests, zero failures or errors. The 365 pre-existing exclusions are unchanged. 1736 source and fixture hashes verified. Full run: 477 seconds.

The five internal helpers have no direct references in the repository. History shows the corresponding active workflows were moved to dedicated modules; four initial-state values are identical. The same 161 API cases passed before and after removal in a temporary copy; 162 focused cases passed in the project, including the helper-authored schedule recovery case. The latter detected two deliberate failure/retry mutations. Unknown third-party code could still call these internal underscore-prefixed names.

Reports: `build/verification-coverage-goal90-20261026-checkpoint20`. No version bump, release, installation, or push.

## 2026-10-26 — Coverage scope, archive integrity and workflow guards (unreleased)

Corrected Python product-line coverage: **88.4930% → 88.5168%** (+0.0238 percentage points); branch coverage **79.9664%**. The earlier checkpoint included nine `factory/tests` files; its previous 88.6000% is not directly comparable and was recomputed from the original report. Both test roots still execute: 11,360 collected, 11,339 passed, 21 existing platform skips, 906 subtests, zero failures or errors. The 365 pre-existing exclusions are unchanged. 1735 source and fixture hashes verified. Full run: 492 seconds.

Fixed a reproduced race where archiving a chat could erase a concurrent message. Added cross-process regression coverage, five additional successful repetitions and Windows CI selection. Added an actual coverage-report check and four tests that reject model-planned writes during read-only API workflows; one isolated fault bypassing those workflow checks was detected by all four tests. The earlier helper-authored schedule-result failure test is included in this run. Native Windows verification remains pending.

Reports: `build/verification-coverage-goal90-20261026-checkpoint19`. No version bump, release, installation, or push.

## 2026-10-26 — Concurrent session history preservation (unreleased)

Overall Python line coverage: **88.5963% → 88.6000%** (+0.0037 percentage points); branch coverage **79.9063%**. Both Python test roots: 11,353 collected, 11,332 passed, 21 existing platform skips, 906 subtests, zero failures or errors. The 365 existing exclusions are unchanged. 1730 source and fixture hashes verified. Full run: 439 seconds.

Fixed a reproduced cross-process race in session history. Three regression cases cover overlapping metadata updates and messages and failed replacement. The regression failed against the old code; five additional repetitions passed after the fix. Focused session and mobile service checks: 129 passed, five subtests. These are Linux results, not native Windows verification.

The helper-authored schedule-result fault test was reviewed, mutation-checked and added after this full run; it passed separately and is not included in the above totals or coverage measurement.

Reports: `build/verification-coverage-goal90-20261026-checkpoint18`; testing guidance assessment: `docs/development/TEST_QUALITY_ASSESSMENT.md`. No version bump, release, installation, or push.

## 2026-10-26 — Web routing, autonomous tools and keyboard input (unreleased)

Overall Python line coverage: **88.5803% → 88.5963%** (+0.0160 percentage points); branch coverage **79.9063%**. Both Python test roots: 11,350 collected, 11,329 passed, 21 existing platform skips, 906 subtests, zero failures or errors. The 365 existing exclusions are unchanged. 1729 source and fixture hashes verified. Full run: 456 seconds.

Added 40 tests for web-route validation, autonomy and tool restrictions in both provider formats, and keyboard input boundaries. Eight helper-authored methods were reviewed and retained unchanged. Thirteen independent mutations were detected. These tests do not establish native Android, Windows or VM acceptance.

Reports: `build/verification-coverage-goal90-20261026-checkpoint17`. No runtime change, version bump, release, installation, or push.

## 2026-10-26 — Guest validation, update status and cancellation boundaries (unreleased)

Overall Python line coverage: **88.4252% → 88.5803%** (+0.1551 percentage points); branch coverage **79.8858%**. Both Python test roots: 11,310 collected, 11,289 passed, 21 existing platform skips, 906 subtests, zero failures or errors. The 365 existing exclusions are unchanged. 1725 source and fixture hashes verified. Full run: 473 seconds.

Added 64 tests for guest input validation, update-check transport, idle mouse frames, keyboard handling, cancellation acknowledgements and reconnect delivery. Twelve helper-authored methods were reviewed; the guest fixture was made portable, while the keyboard tests were retained unchanged. Twenty independent mutations were detected. A flaky test teardown now waits for the shared worker lock and still propagates unrelated errors; targeted checks passed. These tests do not establish native Android, Windows or VM acceptance.

Reports: `build/verification-coverage-goal90-20261026-checkpoint16`. No runtime change, version bump, release, installation, or push.

## 2026-10-26 — Keyboard bindings and asynchronous mouse copying (unreleased)

Overall Python line coverage: **88.3538% → 88.4252%** (+0.0714 percentage points); branch coverage **79.6807%**. Both Python test roots: 11,246 collected, 11,225 passed, 21 existing platform skips, 906 subtests, zero failures or errors. The 365 existing exclusions are unchanged. 1719 source and fixture hashes verified. Full run: 458 seconds.

Added 19 tests for keyboard event handling and asynchronous mouse copying, including six helper-authored tests reviewed and strengthened by the maintainer. Nine independent mutations were detected. These tests do not establish native Android, Windows or VM acceptance.

Reports: `build/verification-coverage-goal90-20261026-checkpoint15`. No runtime change, version bump, release, installation, or push.

## 2026-10-26 — Multimodal history and Markdown event boundaries (unreleased)

Overall Python line coverage: **88.2455% → 88.3538%** (+0.1083 percentage points); branch coverage **79.5781%**. Both Python test roots: 11,227 collected, 11,206 passed, 21 existing platform skips, 906 subtests, zero failures or errors. The 365 existing exclusions are unchanged. 1717 source and fixture hashes verified. Full run: 455 seconds.

Added 18 tests for image and caption preservation after history compaction, latest-image selection, local-link label suppression, and Markdown event handling. Five independent mutations were detected. The image conversion tests use the real server module with an isolated deterministic model boundary; Markdown event tests exercise the exported Writer API. These tests do not establish native Android, Windows or VM acceptance.

Reports: `build/verification-coverage-goal90-20261026-checkpoint14`. No runtime change, version bump, release, installation, or push.

## 2026-10-26 — Adjustable Mermaid diagram scale in the Wiki

Wide diagrams initially fit the article width. Localized Fit, zoom out/in and actual-size controls scale each diagram independently of article text. Fit follows viewport resizing; larger views retain their own bounded scroll area. The viewer preserves Material's closed SVG shadow root and its theme styling. No new library or application version change is required.

Strict MkDocs build, 11 Wiki importer/citation unit tests, 6,312 local links, 1,692 source links and imported-source hashes pass. The actual Chromium acceptance run covers all 76 page routes and 178 rendered diagrams with zero errors. New browser controls verify both languages, 100%/125% zoom, Fit, resize, unchanged paragraph font size, light/dark styles and touch interaction at 390px without page overflow. The initial combined browser run exposed a test theme-state assumption; the zoom check now restores the light theme before the pre-existing search/theme tests. The final complete run passes. JavaScript syntax, fatal Ruff and diff checks pass.

Screenshots and receipts: build/verification-wiki-diagram-zoom-20261026. Changes are local source only at this checkpoint; no GitHub push, Pages publication, application installation or owner-service restart has occurred.

## 2026-10-26 — Portable native-test report paths for release acceptance

The hosted Python jobs reproduced 12 failures in three native test files when the shared runner received a relative output directory. Child processes changed their working directory, so relative COVERAGE_FILE and isolated HOME/data paths no longer identified the original evidence directory. The runner now resolves the output path before launching children, including its direct run_file entry point. No application code, assertion or skip condition changed.

A new real-child regression changes directory and verifies that report, HOME and data paths remain absolute and writable. Re-running all three previously failing files and the runner contracts with a relative --output path and branch coverage passes 25/25 cases with zero skips/failures. Fatal Ruff and diff checks pass. The existing full local coverage result remains historical; final hosted acceptance will measure the final tree.

## 2026-10-26 — Desktop security dependency patches for 0.2.25

- Updated Electron 44.9.0 to 44.9.3, Undici 7.30.0/6.29.0 to 7.30.1/6.29.1, and fast-uri 3.1.8 to 3.1.9 without changing dependency major versions. npm audit, including build dependencies, reports zero known vulnerabilities.
- A real early-EOF regression found Electron still embeds an affected HTTP client. The update downloader now defaults to the locked, patched Undici client; custom transport injection, manual redirect validation, digest checks and ordinary compressed responses remain supported. Both offline packaging routes include its reviewed runtime files and MIT license in their existing inventories. Native update acceptance uses the same client.
- Added 21 desktop dependency/runtime cases and six packaging rejection cases. Focused security/updater checks: 36 passed. Complete desktop test script with Node 24.14.0 and locked dependencies: 520 passed, zero failures/skips. Staging, Linux snapshot and cross-platform Python contracts: 95 passed. Fatal Ruff and diff checks passed.
- URI cases cover encoded schemes, ambiguous authorities, relative normalization and colon-containing suffix/skipEscape forms. HTTP regressions exercise gzip and deflate early termination in owned subprocesses, plus valid compressed responses. An invalid draft assertion about an already absolute URI was corrected against the vendor contract, rather than changing production URI behavior.
- The compromised-CA private key is unavailable, so its interception attack is not claimed reproduced. Vendor-patched Electron and native TLS/certificate-pin acceptance are the substitute verification. Hosted native builds and package inventory validation are required before publication. Owner services and installed applications were not restarted.

## 2026-10-26 — Explicit local reasoning transport and provider compatibility (unreleased)

Overall Python lines: **88.2412% → 88.2403%** (-0.0009 percentage points); branches **79.3335%**. Target90%, unchanged whole-Python scope. Both roots, 545 files: 11,202 collected, 11,181 passes, 21 existing platform skips, 906 subtests, zero failures/errors. Added 29 cases; 1063 frozen source/lock/fixture hashes verified, exclusions365 unchanged; full duration431s. Release0.2.25 is authorized and prepared; at this checkpoint no release is yet published or installed, and no owner service was restarted.

Added29 self-authored cases under explicit user direction to implement the fix independently. Corrected pre-change acceptance has23 failures/6 passes; the final29 cases pass. Two earlier guest cases initially used an incorrect allowed-root fixture and were corrected to the existing DEFAULT_GUEST_ROOTS, without weakening path validation. Compatibility first passes180 cases/21 subtests, then fresh isolated acceptance passes189/189 cases/21 subtests with zero skips/failures/errors.

The optional profile object llama_cpp.reasoning_format validates auto/none/deepseek/deepseek-legacy; absent options do not inject new executor or HTTP fields. The worker supplies the option only for OpenAI Chat Completions, never Anthropic or Responses. Optional host/guest fields preserve the setting through the real manager/shared-wire builder/guest parser; default wire and executor kwargs stay unchanged. The executor validates explicit direct options before network and includes the format in ordinary requests and compaction requests. No English-language, phrase or model-name heuristic was added. Existing incremental tagged-reasoning guards remain covered, including unclosed/fragmented tags, fenced/inline/escaped literals, native reasoning, tool calls, passback, cancellation, usage and final-answer completion.

Controlled real loopback SSE acceptance verifies that structured reasoning never enters model.text_delta, while an English final answer containing a literal closing tag stays intact. A single48-token-budget smoke against an idle existing local server, using per-request deepseek formatting and an8-token reasoning budget, returns a separate reasoning_content field and a two-character final answer without think tags. It is a protocol smoke, not a benchmark or proof of every model behavior. The existing local profile alone is opted in, with a private protected backup; other profile fields are byte-value-preserved. No model/server/global parameter was changed or restarted. The installed runtime remains old until the authorized UI update. Historical-tool-evidence echo is a separate model/history concern and is not claimed fixed.

Release0.2.25 version metadata and concise desktop notes are prepared once. The user has explicitly authorized publication after acceptance; this checkpoint itself does not claim a published release or installed update.

This feature fix changes total executable statements from81190 to81218. The measured overall line ratio is 88.2411627047666→88.24029156098402%; the tiny decrease is reported unchanged rather than hidden with exclusions or padding tests. Zero test failures/errors. The coverage goal remains unfinished.

Reports and refreshed maps: build/verification-coverage-goal90-20261026-checkpoint13. Source changes become active after the upcoming authorized runtime update; the owner model server remains running.

## 2026-10-26 — Streamed Markdown integrity and single-pass approval batches (unreleased)

Overall Python lines: **88.1890% → 88.2412%** (+0.0522 percentage points); branches **79.3248%**. Target90%, unchanged whole-Python scope. Both roots, 544 files: 11,173 collected, 11,152 passes, 21 existing platform skips, 906 subtests, zero failures/errors. Added 55 cases; 1059 frozen source/lock/fixture hashes verified, exclusions365 unchanged; full duration421s. No upload, release, version bump, installation or owner-service restart.

Added55 cases:36 reviewed helper-authored streamed-text cases (26 functions),10 independent parent stream acceptance cases and9 parent approval-batch cases. All497 unrelated original helper source hashes,2888 dependency hashes, original24 test function names and their assertions were preserved. Only four permitted widget method bodies changed. Initial helper text-loss fixes still split the unfinished tail into a separate Markdown card; independent acceptance reproduced6 failures/4 passes. Parent corrected only the two finish bodies and verified46/46 cases with the helper draft. Raw Unicode/CRLF across every two-chunk split, renderer independence, live completed-line visibility, a coherent fenced code block, separate-message identity, repeated finish and actual active tool-cell preservation are covered. No text filters or model-specific behavior were added.

A separate real-session regression reproduced duplicate approval prompts for multiple blocked actions:4 failures/1 pass before the fix. Protocol contexts are now merged before one decision loop, rather than running that loop once per blocked record. Malformed non-record entries are ignored. Positive/negative and session-grant combinations, merged context, valid/invalid typed host operations, exact approved signatures, single user-history persistence and malformed-batch failure preservation pass. Existing host/tool gates and signature verification are unchanged. Focused isolation passes254 streamed-widget/reducer/CLI cases plus106 approval/lifecycle cases, zero failures/errors/skips.

Two followup helper responses were not accepted as implementation: one made zero tool calls and echoed historical evidence; the next performed one read-only Bash inspection and no edits. Their completed outputs did not change the audited workspace. The accepted code is the original36-case helper draft plus the independently verified parent tail correction. The installed-runtime incident is recorded separately without publishing model reasoning or attributing an unproven upstream trigger to a reasoning budget.

Reports and refreshed maps: build/verification-coverage-goal90-20261026-checkpoint12. These are source-only changes; installed application behavior is unchanged.

## 2026-10-26 — Read-evidence aggregation and cooperative final-trace cancellation (unreleased)

Overall Python lines: **87.9377% → 88.1890%** (+0.2513 percentage points); branches **79.2206%**. Target90%, unchanged whole-Python scope. Both roots, 541 files: 11,118 collected, 11,097 passes, 21 existing platform skips, 906 subtests, zero failures/errors. Added 55 cases; 1056 frozen source/lock/fixture hashes verified, exclusions365 unchanged; full duration468s. No upload, release, version bump, installation or owner-service restart.

Added55 cases relative to checkpoint10:42 helper-authored read-evidence cases (40 functions),12 independent parent acceptance cases and1 continuous-final-trace cancellation regression. All508 original helper source hashes and2888 provided dependency hashes are verified unchanged. New executable/config-file inventory contains only the allowed helper test; no conftest or alternate implementation is injected. Original40 test names are retained. Three draft failures were invalid fixtures/expectations (nine sampled values despite limit8, a listing header concatenated with a directory row and inconsistent module/root relationships); they were corrected without production edits. The listing test was simplified to rejected rows, so parent mixed-valid/invalid acceptance explicitly preserves the stronger intended contract. The initial collection failure omitted provided PYTHONPATH dependencies; no packages were installed to fix it.

The helper42 cases independently pass, then pass54 with12 parent cases using the actual inference fixture. Parent checks all evidence tags including main/decorated entrypoints, category bounds and preserved section endpoints, distinct evidence at an identical numbered range, real mixed listing rows, deduplicated trackable scans, case-sensitive candidate paths, argument gates, import aliases/package/self-exclusion/order, the exact first200-line scan boundary and a controlled resolved escape alongside a valid local module. The two reviewed test files are adopted; dependency snapshots and environment files are excluded from the canonical repository.

A separate real owned-thread regression reproduces the remaining cancellation gap: after a view startup failure and final task status, continuous late trace chunks keep resetting the grace deadline, so the poller survives the bounded join. Fixture barriers make entry into that state deterministic and teardown stops only its own generator/thread. The final-trace loop now checks done_event before continuing the grace drain. Normal final-chunk delivery remains covered by the previous Rich/application integration, while the original view error and backend task remain preserved. The regression genuinely fails before the one-condition fix; no test is weakened. Fresh isolated compatibility passes286/286 across the54 evidence cases,18 live-turn cases and214 existing CLI/application/reducer cases, with all process exits0.

A distinct new helper is assigned a bounded streaming text-integrity task on a separate public source/dependency snapshot. The original read-evidence task is terminal and handled; the shared own paired client remains active only because that next task is actually running. No streaming helper artifact is included in this frozen checkpoint.

Reports and refreshed maps: build/verification-coverage-goal90-20261026-checkpoint11. These are source-only changes; installed application behavior is unchanged.

## 2026-10-26 — Active-turn lifecycle integration and capability client failure contracts (unreleased)

Overall Python lines: **87.5888% → 87.9377%** (+0.3489 percentage points); branches **78.7789%**. Target90%, unchanged whole-Python scope. Both roots, 539 files: 11,063 collected, 11,042 passes, 21 existing platform skips, 906 subtests, zero failures/errors. Added 29 cases; 1054 frozen source/lock/fixture hashes verified, exclusions365 unchanged; full duration465s. No upload, release, version bump, installation or owner-service restart.

Added29 cases:12 completed helper transport cases and17 parent active-turn integration cases. Helper source inventory507 hashes is unchanged: no client production modification was needed. Immutable final snapshot preserves all12 test names, replaces its initial hardcoded temporary path with a source-relative path, sends proper JSON-RPC requests in positive fixtures and strengthens close/raw-error/token assertions. The initial2FAIL/10PASS were caused by its fake constructor discarding exceptions; the fake was corrected after review instead of changing working production behavior. Path/token and Connection are replaced before main, and socket is fake before connect. Independent acceptance passes17=12helper+5parent exact-boundary cases already canonical in the previous checkpoint.

The parent integration executes the real active-turn function, trace reducer, ChatWidget/BottomPane, MessageQueue and request-input forms/serializer. Task storage and async application execution are controlled boundaries; the poller is an actual owned Python thread, and fixture teardown releases/joins only those threads. Cases cover Rich fallback, application-unavailable fallback and controlled application execution; ok/fail/dead-letter results; missing tasks, polling exceptions and timeout; both interruption paths cancelling exactly the created fixture task; queued-worker notice, exactly-once followup enqueue, final grace-window trace chunks without loss/duplication, and cancelled/answered/inline request forms followed by view resumption.

The first draft16-case run had15PASS/1FAIL because its fake view supplied a submitted-text marker but left the buffer uncleared, contrary to the actual accept-handler contract. The fixture was corrected; the three existing real accept-handler checks (reset, empty input, control stripping) pass alongside it,19PASS. This was not a production bug. A separate new startup-failure case then reproduces an actual live poller left behind when view construction raises. The active-turn boundary now always signals done_event and performs its existing bounded poll_thread join in finally. It preserves the original view error and does not cancel the backend task on a view failure; explicit interruption still cancels only its own task exactly once. The17new scenarios plus3actual buffer contracts pass20.

Fresh isolated compatibility passes243/243 across the17 integration cases,12helper cases and214previous CLI/application/reducer cases, every subprocess exit0. No owner model, service or task is used; the captured-unit/async-boundary limits are explicit. The remaining helper is preparing read-evidence tests and is not silently included in this frozen run.

Reports and refreshed maps: build/verification-coverage-goal90-20261026-checkpoint10. These are source-only changes; installed application behavior is unchanged.

## 2026-10-26 — Selection replacement, text-input edges and capability transport boundaries (unreleased)

Overall Python lines: **87.4406% → 87.5888%** (+0.1483 percentage points); branches **78.4364%**. Target90%, unchanged whole-Python scope. Both roots, 537 files: 11,034 collected, 11,013 passes, 21 existing platform skips, 906 subtests, zero failures/errors. Added 24 cases; 1052 frozen source/lock/fixture hashes verified, exclusions365 unchanged; full duration473s. No upload, release, version bump, installation or owner-service restart.

Added24 cases in two files:12 helper-authored widget cases and12 independent parent acceptance cases (including3 previously run but not yet canonical terminal sequence checks). The helper final file contained11 cases and had inadvertently dropped its own previously valid selected-key case. Parent restored the exact original draft function into a new immutable snapshot. Both files of the first snapshot remain byte-for-byte equal to their pinned hashes. All507 source hashes were audited: only set_items in the two allowed selection modules changed, adding cursor/offset resets; the textarea and prompt sources remain unchanged.

Intermediate review corrected a nonexistent picker.move_down call to the existing scroll API and a wrong assertion that expected a checked flag to remain after toggling it off. The production API and correct toggle behavior were not changed to fit those tests. A render case was added after review, and unused imports removed. The final helper still used only five old multi-select items, so parent acceptance independently proves reset from selected19/offset12 for both list types. Multiple checked keys preserve input order, rendering preserves flags, and both empty/unselected boundaries are covered. The helper before log reproduces2FAIL/9PASS; after log gives11PASS. Final reviewed snapshot plus4 independent selection/input cases gives16PASS.

The12 parent boundary cases exercise three consecutive terminal frame sequences with a nonzero viewport origin, both replacement-list types, multi-key ordering and one-codepoint edge no-ops. Five capability-client cases keep Path/token, Connection and stdin controlled before main: exact65536/65537-character request limits, exact2MiB/2MiB+1 response bounds, bounded reads, request-ID zero, notification followed by a request, order/close counts and fake authorization. The existing client source is unchanged; there is no actual /run token/socket access or IPC. The larger capability helper is not counted as accepted yet. Fresh isolated two-file acceptance passes24/24 with every process exit0; lint and diff checks pass.

Reports and refreshed maps: build/verification-coverage-goal90-20261026-checkpoint9. These are source-only changes; installed application behavior is unchanged.

## 2026-10-26 — Terminal frame cleanup, stream catch-up and UI lifecycle contracts (unreleased)

Overall Python lines: **87.2170% → 87.4406%** (+0.2236 percentage points); branches **78.3542%**. Target90%, unchanged whole-Python scope. Both roots, 535 files: 11,010 collected, 10,989 passes, 21 existing platform skips, 906 subtests, zero failures/errors. Added 34 cases; 1050 frozen source/lock/fixture hashes verified, exclusions365 unchanged; full duration458s. No upload, release, version bump, installation or owner-service restart.

Added34 cases across three files:10 supplied by the scoped helper,11 parent stream/feedback integration cases and13 parent UI-lifecycle cases. The helper original source reproduced2FAIL/8PASS after correcting its stdout fixture. Its minimal draw fix clears rows left over from a longer previous frame, bounded to the existing viewport and preserving diff redraw behavior. The parent independently checked3 consecutive-frame sequences, audited507 original source hashes, verified the only allowed source edit and preserved the original10 test assertion ASTs. Only the two reviewed files from an immutable snapshot were adopted.

The first isolated focused run exposed a second fixture issue: the helper monkeypatched the shared shutil module to return a non-iterable size object, causing pytest progress reporting to terminate with exit3 after43 of52 cases. It is retained as failed evidence. A parent integration adapter replaces only the tested module shutil reference, keeping every test assertion intact. A fresh focused run passes all52 cases and every file exit code is zero. The parent removed a separate unsupported private-cache-length assumption from its own acceptance draft; the public bounded-output and screen-content assertions remain, and the original failed review log is retained.

Parent stream integration first reproduced7FAIL/4PASS: catch-up called a single-line commit despite an available batch API and always supplied zero queue age; the feedback header was one column too wide. The runner now uses public queue depth/oldest age and drains stable lines via the existing batch API. Smooth one-line order and held code/table blocks are preserved. The corrected header fits the declared normal widths. Existing stream compatibility gives24PASS with the11 new cases.

Thirteen lifecycle cases exercise callback success/errors with terminal ownership and event resumption, best-effort restoration errors, ordered single-use history flush and copied batches, clamped viewport overflow paths and notification gating/failure disablement. No lifecycle production changes were needed. Combined with the previous five draw tests these pass18. Three more helper tasks remain active or queued; none of their unreviewed changes is included in this frozen full run.

The coverage combine emits the same two pre-existing warnings as checkpoint7 because owned native-artifact directories have .coverage-prefixed names. Both native tests explicitly load child.coverage and update Coverage.current().get_data() before combine; these are directories mistaken for input files, not lost child coverage. All535 per-file exit codes, both-root discovery equality and1050 frozen hashes were independently verified.

Reports and refreshed maps: build/verification-coverage-goal90-20261026-checkpoint8. These are source-only changes; installed application behavior is unchanged.

## 2026-10-26 — Previously untested command, stream, editor and lifecycle boundaries (unreleased)

Overall Python lines: **86.2254% → 87.2170%** (+0.9916 percentage points); branches **78.1492%**. Target90%, unchanged whole-Python scope. Both roots, 532 files: 10,976 collected, 10,955 passes, 21 existing platform skips, 906 subtests, zero failures/errors. Added 289 cases; 1047 frozen source/lock/fixture hashes verified, exclusions365 unchanged; full duration434s. No upload, release, version bump, installation or owner-service restart.

Added122 cases across five files. The composer/popup draft reproduced3 failures/55 passes: selection after the first eight results becomes invisible and the glob fallback returns directory names as files. The popup now renders the offset slice and updates its offset on movement; fallback entries must be actual files within the existing bounded candidate listing. Fifty-two composer cases exercise disabled input, Unicode/word/line editing, command/file/skill selections without implicit submission, navigation/backspace/escape/no-match behavior, image selection/removal, multiline cursor coordinates, layout and footer state. Twenty-three public command metadata cases verify canonical aliases/round-trips, descriptors, declarative platform/debug visibility and supported invocation contexts. Focused compatibility146PASS.

A second zero-module draft reproduced8 failures/33 passes: malformed editor quoting raises rawValueError and line widths use character count or a heuristic that mismeasures emoji/combining marks. Quoting errors now use the existing PARSE_FAILED reason. Truncation shares the already-tested live-wrap Unicode width functions, keeping ANSI styles outside its width budget. Eleven editor cases verify precedence, quoted/literal argv, a real owned Python editor preserving Russian/emoji text, nonzero/missing executable cleanup and rejection before any temporary file on emptyargv. Thirty combined stream/width/turn cases cover real terminal-column expectations, ellipsis, reserved-width guards, every two-chunk split of multilingual/fencedMarkdown, idempotent commits/finalization/clear and eight concurrent real-thread attempts with exactly one turn owner. The separate compatibility group132PASS. No owner editor/service/model/network call is performed.

The first full run (10809collected/10787PASS/1FAIL/21skips) is preserved unmodified. An existing real daemon test waited only for database task completion, then raced the still-held execution/scheduler slot during stop. Production correctly refused that busy stop; it is not relaxed. The test now retries only this exact busy response until the real stop succeeds, propagates other errors, guarantees its HTTP-server shutdown and owns its exact Popen daemon handle for failure cleanup. Its original failed daemon was identified by exact module argv, fixture data-root and post-run start time; its temporary directory had already been removed by pytest retention. Only that confirmed owned PID receivedSIGTERM and exited. Owner services were untouched. Independent full test-file recheck23PASS. A fresh full run is required; the failed result is never rewritten as passing.

Added65 more cases across five files, prioritizing seven more baseline0% modules (including animation frames imported by their public driver). Owned mobile-store CLI checks cover pairing/list/revoke independence, listener configuration at a stub boundary, data-root precedence and malformed timestamp display. The original group6FAIL19PASS exposes bool/NaN/infinity/oversized timestamp handling, legacyNULL task text and a realSQLite heartbeat race in manual cleanup. The cleanup update now rechecks heartbeat freshness atomically as well as processing status, counts only actual updated rows and tolerates legacyNULL input. A second race variant preserves tasks that completed after the scan. Only ownedSQLite databases are used.

ACP entry tests verify stderr-only logging, the controlled protocol-runner boundary, interruption/error exit codes and idempotent entry-path setup; no native ACP stdio handshake is claimed. Command display tests verify literal argv quoting, supported shell-wrapper display and malformed quotation fallback without executing shell payloads. Animation/motion tests use a controlled clock and frame-request boundary to check bounded variant indexes, timed scheduling, zero-tick/scheduler-error fallback, distinct random choice, reduced-motion no-animation and visible shimmer text preservation. Two index failures reproduced before clamping both ends; one command-parser failure reproduced before preserving unparseable display input. Additional isolated group65PASS. This expands the final checkpoint to187 added cases before any separately accepted helper code.

The helper supplied actual client code and42 parameterized cases (20 test functions). Parent review rejected unrelated extraction/operator/scoring edits and preserved original assertions while adding60 independent payload-boundary cases. Immutable intermediate snapshots retain real failures (21/21, then1/164 including explicitnull reasons). Accepted snapshot4 independently passes167 cases including65 previous contracts. Tests adapt only the neighboring fixture import; unchanged assertions and unchanged moduleAST outside seven reviewed helper/decision/boolean functions are verified. The source rejects bool/nonfinite/malformed numeric scores, preserves integer precision and valid numeric compatibility, requires proper reason-list shapes and true boolean interaction flags, avoids corrupt brief rows/unsafe reaction ranking and rejects invalid post comment counts. Parent cases preserve positive comments with absent reasons while rejecting explicitnull. No transport/approval/send functions change. A canonical compatibility run supplies the recorded totals. The accepted snapshot contains42 helper plus60 parent cases; supplemental duplicate tests being written later are not blindly merged. Final added-case count before rerun is289, compared with the previous accepted checkpoint.

Final helper audit: both tasks are terminalOK and all queued review messages are consumed. Final helper source and original test definitions match accepted snapshot4; the helper did not add the two requested supplemental duplicate cases. The parent independently supplied and passed both absent-field and explicitnull cases, so this is not treated as missing accepted coverage. Canonical compatibility188PASS/2subtests is authoritative; model self-reported test text is not acceptance proof. The temporary paired client was revoked/deleted and its tokenfile removed without touching other devices; visible chat retained.

Reports and refreshed maps: build/verification-coverage-goal90-20261026-checkpoint7. These are source-only changes; installed application behavior is unchanged.

## 2026-10-26 — Unicode row integrity, fresh file searches and title/style contracts (unreleased)

Overall Python lines: **85.7030% → 86.2254%** (+0.5224 percentage points); branches **77.2702%**. Target90%, unchanged whole-Python scope. Both roots, 520 files: 10,687 collected, 10,666 passes, 21 existing platform skips, 906 subtests, zero failures/errors. Added 79 cases; 1035 frozen source/lock/fixture hashes verified, exclusions365 unchanged; full duration442s. No upload, release, version bump, installation or owner-service restart.

Added79 cases across four files. The original wrap/search group independently reproduced16 failures/19 passes. Prefix slicing used UTF-8 byte length to index Python strings, making Cyrillic prefixes too wide and letting an oversized CJK glyph swallow following ASCII. Both indexes now use code-point positions; the loop includes subsequent zero-width combining marks rather than splitting an accent from its base. Tests compare every two-chunk partition across four text fixtures and widths1/2/3/8, reconstruct all explicit/blank line boundaries, verify resize reflow, ordered draining and the documented no-wcwidth fallback.

Owned file trees verify replacing previous query matches, deduplicating roots, preserving independent snapshots, session reuse/root reset, old reporter suppression and backend-start failure recovery. Search results are reset before a new scan and a reporter now checks its query against the latest query as well as its session token. This does not add a new frontend search route.

Thirty-two title-output cases cover control/OSC/bidi/invisible characters, Unicode character limits, whitespace normalization/idempotence, quiet non-TTY/detached streams and exact trusted writer framing. Eleven style cases cover independent value updates/hash/equality, light/dark contrast, default colors and truecolor/256/16/unknown fallbacks. One draft style fixture mismatched explicit256 mode with an ambient truecolor terminal; the corrected fixture selects a matching actual capability, with no production style change. Focused compatibility111PASS. No native UI rendering or owner terminal mutation is claimed.

Reports and refreshed maps: build/verification-coverage-goal90-20261026-checkpoint6. These are source-only changes; installed application behavior is unchanged.

## 2026-10-26 — Typed bounded-read recovery and reaped terminal child ownership (unreleased)

Overall Python lines: **85.6451% → 85.7030%** (+0.0579 percentage points); branches **76.8161%**. Target90%, unchanged whole-Python scope. Both roots, 516 files: 10,608 collected, 10,587 passes, 21 existing platform skips, 906 subtests, zero failures/errors. Added 38 cases; 1031 frozen source/lock/fixture hashes verified, exclusions365 unchanged; full duration440s. No upload, release, version bump, installation or owner-service restart.

Added38 cases in two files. Thirty-two planner checks use actual application normalization and the deterministic generator fixture to verify known agent alias compatibility, malformed plans, absent tools and strictly positive numeric Read bounds. After fixing a test fixture startup error, the unchanged source independently reproduces4 failures/28 passes: booleans were accepted by isinstance(int) and prevented the required bounded retry. Exact int checks now reject bool and preserve valid integer offsets and limits. A first sandbox-only ASGI probe could not use the local test transport and was stopped by exact owned test path; the escalated run with explicit per-test timeout supplies the retained reproduction.

Five original PTY tests all reproduce failures: reaped child IDs remain attached and close attempts to signal them; ECHILD is not handled as completion. Two tests launch real owned Bash PTYs with isolated HOME/environment, verify24x80 then33x91 geometry, command bytes and exit0/7, reap independently and intercept every attempted signal after reaping. Three synthetic wait-boundary tests cover ChildProcessError, ECHILD, transient EINTR and signal exit143. poll_exit now holds the same lock as close and clears IDs after successful reaping or a confirmed no-child result. A sixth deterministic concurrent wait/close test proves close waits for reap completion and sends no signal. Focused compatibility111PASS/10subtests, plus updated lock group6PASS. User processes and services are untouched.

Reports and refreshed maps: build/verification-coverage-goal90-20261026-checkpoint5. These are source-only changes; installed application behavior is unchanged.

## 2026-10-26 — Snapshot failure boundaries and deterministic schedule clocks (unreleased)

Overall Python lines: **85.5452% → 85.6451%** (+0.0998 percentage points); branches **76.7185%**. Target90%, unchanged whole-Python scope. Both roots, 514 files: 10,570 collected, 10,549 passes, 21 existing platform skips, 906 subtests, zero failures/errors. Added 61 cases; 1029 frozen source/lock/fixture hashes verified, exclusions365 unchanged; full duration445s. No upload, release, version bump, installation or owner-service restart.

Added61 cases across two files. Snapshot checks cover missing Git caching, subprocess timeout/not-found/OS errors, rejecting overly broad roots before scanning, bounded directory counts, nonfatal per-turn snapshot failure, refusing invalid/unbounded blobs, byte limits and UTF-8 replacement in captured Git output, external root resolution, and failed init/diff/commit paths. The initial draft had21PASS/1 fixture failure because/tmp has a Git ancestor; the existing-parent fallback is now independently isolated while a real temporary repository tests nearest-root resolution. Production source is unchanged.

Deterministic clock cases verify disabled schedules, daily rollover versus one-shot expiry, UTC and Europe/Moscow conversion, supported daily-like cron forms, malformed times/cron, bounded strictly typed hourly intervals, future/expired anchors and missing/unknown types. All61 tests passed independently before the full run. No real user task, model call, scheduler loop or network execution occurs. Flint is working in a separate source snapshot on a larger implementation task and has not yet supplied accepted code for this checkpoint.

Reports and refreshed maps: build/verification-coverage-goal90-20261026-checkpoint4. These are source-only changes; installed application behavior is unchanged.

## 2026-10-26 — Viewport-safe terminal layout and complete emoji package data (unreleased)

Overall Python lines: **85.4023% → 85.5452%** (+0.1430 percentage points); branches **76.5712%**. Target90%, unchanged whole-Python scope. Both roots, 512 files: 10,509 collected, 10,487 passes, 22 skips (21 platform, 1 pre-existing live-upstream HTTP504), 906 subtests, zero failures/errors. Added 19 cases; 1027 frozen source/lock/fixture hashes verified, exclusions365 unchanged; full duration400s. No upload, release, version bump, installation or owner-service restart.

Added19 cases across two files. Seventeen public layout cases verify zero-size viewports, column/row clipping, cursor ownership/style, fixed footer allocation, weighted-flex remainder, inset measurement/render agreement, and text-wrapper row clipping. The original group passed16 and reproduced one genuine failure: a1-column viewport with2-column side insets measured child width-3 even though its rendered area width was0. InsetRenderable.desired_height now clamps that width to0, preserving ordinary layouts and aligning measurement with Rect.inset. Two helper Rect/Insets cases use exact dataclass AST/SHA fixtures and unchanged assertions for intersections, empty/disjoint geometry, bounded padding and immutable inputs. Combined focused compatibility51PASS.

Explicit package-data patterns include emoji metadata and nested GIFs. A fresh508-file tracked-source export builds a wheel using the pinned build backend offline. Archive checks compare every GIF/catalog/notice/JS/CSS byte against source and reject source tests or traversal paths. A first origin assertion correctly rejected importing from the source checkout when cwd shadowed PYTHONPATH; rerunning from the independent temporary directory proves the extracted-wheel module origin and verifies all16 media/receipt results without installing into the owner runtime. The wheel is retained only as packaging evidence under0.2.24; it is not published or offered as an update. All emoji files, including the previously ignored catalog/notice now explicitly tracked, are included in the new source inventory.

The pre-existing live grep.app smoke received upstreamHTTP504 in the full run:22 actual skips comprise21 platform checks and1 unavailable external service. This is not a new skip condition or a weakened test. The original full summary/coverage are retained unchanged; any independent recheck is recorded separately. Independent same-source recheck: {"collected": 4, "errors": 0, "failed": 0, "passed": 4, "skipped": 0, "subtests_passed": 0}. The original full-run skip is not rewritten.

Reports and refreshed maps: build/verification-coverage-goal90-20261026-checkpoint3. Host runtime remains unchanged; the wheel is only a temporary packaging verification. The already delivered Android0.1.125(126) is unchanged.

## 2026-10-26 — Animated Pepe reactions across desktop and Android chat (unreleased)

Overall Python lines: **85.3770% → 85.4023%** (+0.0252 percentage points); branches **76.3998%**. Target90%, unchanged whole-Python scope. Both roots, 510 files: 10,490 collected, 10,469 passes, 21 existing platform skips, 906 subtests, zero failures/errors. Added 56 cases; 1023 frozen source/lock/fixture hashes verified, exclusions365 unchanged; full duration411s. Android0.1.125(126) was explicitly requested, signed with the matching prior debug certificate, verified and delivered privately. No GitHub/Gitea push, desktop release, host installation or owner-service/model restart.

Added56 Python cases across two files. Fifty-two catalog/media/tool/prompt cases check every real bundled GIF digest, stable receipt/token identity, defensive copies, invalid argument shapes, malformed/duplicate catalogs, invalid asset paths, missing/symlink/tampered/oversized files and full/compact prompt guidance limited to desktop/android/mobile. Four actual owned HTTP cases validate authenticated catalog/all16GIF bytes, unauthorized access, unknown/path-like IDs and revoked disposable devices. Focused compatibility336PASS plus new HTTP4PASS; no failures/skips in these groups.

The 16 original GIFs total636797bytes. Each download matches its pinned Git blob hash; every GIF has multiple frames (2–30) according to local media inspection. No third-party executable is downloaded or run. Core registry exposes render_emoji across tool providers and validates stable known IDs instead of model-controlled URLs. Desktop Markdown decorates ordinary tokens while preserving inline/fenced code; user input stores those same tokens in existing message history. Native popover selection inserts at the textarea cursor without losing the prefix and closes the picker. Full desktop suite499PASS. Browser component proof asserts two inline images, literal code content, zero images within code, correct tool image, no broken images and preserved typed text.

Android uses the same catalog/GIF assets via a Gradle Sync task, native Movie playback compatible with minimumAPI26, inline text placeholders, tool-receipt rendering and a composer picker. It adds no media dependency or external media request. An initial compiler failure called composable localization from a native View constructor; using the existing pure AppLocale accessor fixes it. A corrected build passes477 unit tests and lint; an independent final Gradle verification exits0. Versioned APK0.1.125(126) contains all16 matching source assets and the same signing certificate as the prior shipped APK. SHA-256, size and successful private delivery receipt are retained in ignored verification evidence. Host runtime and running model are not installed/restarted; automatic model guidance is ready in source, while APK picker and animation are self-contained.

Reports and refreshed maps: build/verification-coverage-goal90-20261026-checkpoint2. Installed host runtime0.2.24 unchanged. Requested Android APK0.1.125(126) was verified and delivered; physical-device installation/acceptance is not claimed.

## 2026-10-26 — UTF-16 emoji persistence and task-completion boundaries (unreleased)

Overall Python lines: **85.0262% → 85.3770%** (+0.3509 percentage points); branches **76.3654%**. Target90%, unchanged whole-Python scope. Both roots, 508 files: 10,434 collected, 10,413 passes, 21 existing platform skips, 906 subtests, zero failures/errors. Added 53 cases; 991 frozen source/lock/fixture hashes verified, exclusions365 unchanged; full duration407s. No upload, release, version bump, installation or owner-service restart.

Added53 cases across five files. The initial41-case emoji/manifest group had15 failures: three valid Unicode spans returned wrong fallback text and twelve malformed/split-surrogate ranges crashed or produced invalid registrations. Telegram MessageEntity offsets/lengths are UTF-16 code units (https://core.telegram.org/bots/api#messageentity). Registration now validates integer/nonnegative offsets and positive lengths, uses bounded UTF-16 slicing and rejects broken surrogate spans. ASCII/manual aliases, duplicate replacement, actual owned JSON persistence/reload and alias-counter continuity remain covered. Two helper-generated unittest methods preserve their assertions; only the import is adapted to canonical code. The helper fixture source hash is unchanged; seven source functions match its baseline AST exactly and registration has the independently tested UTF-16 correction. Both original-fixture and canonical two-method runs pass.

Twelve manifest CLI cases hash actual owned nonbootable bytes, require explicit overwrite, reject tampering/version mismatch/missing files and preserve typed build errors; no VM boots. Eight ChatSession polling cases use real owned threads and temporary history with synthetic worker/UI boundaries: cancelled task finalization, early Escape bound to the submitted task, preparation failure before submission, worker exception propagation, persistent-app trailing trace drain including repaint failure and explicit fullscreen exit. An initial two-case fixture omitted task.finish, so expected committed content prematurely; adding the real terminal event and matching successful task status fixed the fixture without product changes. Two vault smoke tests run the actual developer entrypoint over owned cryptography, SQLite, scanner and worker-redaction fixtures and verify synthetic secrets are absent from printed output. Focused compatibility115PASS plus helper2PASS. No source exclusion, version change, install or publication.

Reports and refreshed maps: build/verification-coverage-goal90-20261026-checkpoint1. These are source-only changes; installed application behavior is unchanged.

## 2026-10-26 — Safe guest-terminal process ownership and input boundaries (unreleased)

Overall Python lines: **84.8529% → 85.0262%** (+0.1732 percentage points); branches **76.1701%**. Target85%, unchanged whole-Python scope. Both roots, 503 files: 10,381 collected, 10,360 passes, 21 existing platform skips, 906 subtests, zero failures/errors. Added 68 cases; 986 frozen source/lock/fixture hashes verified, exclusions365 unchanged; full duration394s. No upload, release, version bump, installation or owner-service restart.

Added 68 cases across five files. Original native exit-0 and exit-7 checks reproduced cleanup attempting SIGHUP after waitpid had already reaped the child; the signal was intercepted and never sent. GuestPty.poll_exit now clears ownership on confirmed reaping or ECHILD/ChildProcessError, retaining the PID for other wait errors. Three final native Linux checks exercise actual owned /bin/bash PTYs, resize from 101x31 to 63x17, normal 0/7 exits and self-SIGTERM status143. They verify child identity, cwd, UID, kernel NoNewPrivs=1 and zero effective/permitted/inheritable capabilities. Child coverage arcs are captured before exec and merged only after native effects and source SHA checks. No existing user process or runtime is touched.

Twenty transport/ownership boundary cases cover framed handshakes, invalid/EOF acknowledgements, fake VSOCK connect cleanup, peer rejection before service, transport exit paths, typed input-write errors and wait error ownership. Fifteen attachment cases exercise quoted paths, punctuation, image ordering, missing/unsupported/directory/oversized files, owned-home shorthand, malformed quoting and deleted-file summaries. Sixteen synthetic token/catalog cases cover malformed provider data, earliest-refresh boundaries, expired-token refusal, local empty-token revocation, hidden, duplicate or unusable catalog entries and typed empty-catalog errors. Fourteen terminal-title cases cover corrupt config fallback without rewriting, safe explicit saves, bounded numeric titles, home shortening, control-character stripping, fake OSC/BEL output and closed output streams. An initial title fixture expected two spaces where existing normalization correctly returns one; only the expectation was corrected, with no product change. The 135-case compatibility/feature group and separate 14-case title group passed under isolated branch coverage.

Reports and refreshed maps: build/verification-coverage-goal85-checkpoint44. These are source-only changes; installed application behavior is unchanged.

## 2026-10-26 — Preserved confirmation controls and native session-picker recovery (unreleased)

Overall Python lines: **84.5367% → 84.8529%** (+0.3163 percentage points); branches **75.9934%**. Target85%, unchanged whole-Python scope. Both roots, 498 files: 10,313 collected, 10,292 passes, 21 existing platform skips, 906 subtests, zero failures/errors. Added 104 cases; 981 frozen source/lock/fixture hashes verified, exclusions365 unchanged; full duration388s. No upload, release, version bump, installation or owner-service restart.

Added104 cases across five files. Original Telegram21-case and web25-case groups reproduced one lost reply_markup and ten dropped connections caused by non-object session/meta or unhashable isolation values. HTML fallback now preserves the existing keyboard; terminal context uses object/string/meta checks and existing refusal reasons while retaining host-terminal opt-in, KVM-unavailable refusal and no-shell-before-ticket rules. Tickets are validated as single-use, session/device-bound records without opening a backend.

Installer29-case adapter tests fake every dialog/privileged helper process, checking cancellation, explicit helper outcome, progress cleanup and refusal of incomplete installations. Terminal-owner16-case tests use actual owned Unix sockets with kernel SO_PEERCRED and private modes, exact session/profile/lease binding, duplicate/live and stale listeners, malformed request refusal and replacement-path preservation; the VM manager is a fake. Initial fixture errors used a reserved pytest request parameter and the wrong SessionSpec workdir attribute; corrected to payload/workspace_cwd without product changes.

Nine original native picker cases passed eight and reproduced a lone Escape blocking while reading a nonexistent continuation byte. The parser now reads bounded escape continuations from the file descriptor and decodes UTF-8 incrementally, avoiding TextIO prefetch/select mismatch. Per-key draining is removed so queued input is not discarded. An intermediate raw implementation exposed two real compatibility failures because tty.setraw default TCSAFLUSH discarded typeahead; using TCSADRAIN preserves it. Thirteen final owned PTY cases cover resume/fork selection, arrows/pages/sort, ASCII/Russian queries, backspace, empty-result handling, Ctrl+C and lone/partial Escape. Actual100x20 geometry, alt-buffer exit, canonical/ECHO restoration, child PID and unchanged source hashes are asserted; child arc data is merged only after those semantic checks. No legacy frontend is restored: the picker is already called by the current ChatSession resume/fork commands.

Reports and refreshed maps: build/verification-coverage-goal85-checkpoint43. These are source-only changes; installed application behavior is unchanged.

## 2026-10-26 — Typed protocol rejection, authenticated helper and Markdown preservation (unreleased)

Overall Python lines: **84.0712% → 84.5367%** (+0.4655 percentage points); branches **75.6555%**. Target85%, unchanged whole-Python scope. Both roots, 493 files: 10,209 collected, 10,188 passes, 21 existing platform skips, 906 subtests, zero failures/errors. Added 117 cases; 976 frozen source/lock/fixture hashes verified, exclusions365 unchanged; full duration389s. No upload, release, version bump, installation or owner-service restart.

Added117 cases across four files. Before fixes, the25-case MCP group reproduced nine failures: five non-object envelopes and three scalar/list/null error objects leaked type/attribute errors, while inherited lowercase proxy variables survived declared direct-connection cleanup. The client now validates the response object, formats arbitrary error shapes safely and filters inherited proxy names case-insensitively before applying explicit extra_env. Cleanup, revocation and replacement-handshake tests preserve old live clients and release only owned fake processes.

The29-case argument group reproduced seven failures: list/object plan status raised TypeError rather than ValueError, and five non-object synchronous approval replies raised AttributeError. String/object guards preserve valid plans and structured approval decisions without changing prompts or access policy. Existing scope fail-open semantics are asserted with owned diagnostic files only, including import/enforcement/log-write failure; no command executes.

The13-case helper group uses an actual owned authenticated HTTP server and reproduced missing submission X-API-Key plus a documented --status command rejected by an unrelated required text argument. Submission now uses the same owner header as lookup; text is optional only for status and remains required for submission. No real task is created.

Fifty public Markdown normalization tests verify pipe escapes/alignment, conservative closed-table unwrapping, all unfinished stream prefixes, indentation, blockquotes, CRLF and mismatched fences, and text rendering without remote fetch. An initial test assumed terminal hyperlink destination metadata that this renderer currently does not expose; corrected to its actual visible-text/no-network contract rather than adding a link feature or changing the renderer. This batch tests the normalization API and does not claim new frontend integration. The final isolated compatibility run passes366 cases and49 existing subtests, zero failures/errors.

Reports and refreshed maps: build/verification-coverage-goal85-checkpoint42. These are source-only changes; installed application behavior is unchanged.

## 2026-10-26 — Clipboard recovery and worker result persistence contracts (unreleased)

Overall Python lines: **83.9341% → 84.0712%** (+0.1370 percentage points); branches **75.2111%**. Target85%, unchanged whole-Python scope. Both roots, 489 files: 10,092 collected, 10,071 passes, 21 existing platform skips, 906 subtests, zero failures/errors. Added 57 cases; 972 frozen source/lock/fixture hashes verified, exclusions365 unchanged; full duration387s. No upload, release, version bump, installation or owner-service restart.

Added57 cases across three files. Clipboard readers use fake command results only: owned image paths, unsupported/oversized inputs, PNG/JPEG byte capture, timeout/error/empty capture cleanup, URI/plain fallback, display-family precedence and bounded Unicode text probes. All Popen/run entry points are blocked unless explicitly replaced by a fixture; no actual clipboard is read or changed.

Worker tests persist actual owned task rows and telemetry, retain the provider result without mutation, cap100 input telemetry items, redact synthetic api_key values, replace corrupt metadata extensions and prevent malformed/foreign metadata from redirecting execution or authorizing host ops. Actual parsed Codex callback events are deduplicated into an owned trace; MCP sync failure does not drop explicit launch overrides or JSON mode. Synthetic escalation checks preserve either the original failure or a genuine returned fallback answer. Durable parent workspace cases use the actual manager over owned files; initial test roots mixed explicit worker overrides with import-captured manager defaults, corrected by supplying one coherent real manager at the constructor boundary without product changes. Initial raw-reader expectations were replaced with a consumer-level scope/authorization invariant instead of coercing the historical reader API.

The helper created two rollout freshness/missing-file tests and completed its visible task. Independent execution failed both because patch.object(new=list) replaced a function with a non-callable list. The parent changed only those declarations to return_value=list, verified unchanged production function/fixture hashes, and import-adapted the repaired tests to the canonical worker. Both then passed. Original failures, repaired tests and review are preserved; own paired client revoked/deleted and chat retained. Final isolated compatibility run passes256 cases with no failures/errors or skips. No production source changes.

Reports and refreshed maps: build/verification-coverage-goal85-checkpoint41. These are source-only changes; installed application behavior is unchanged.

## 2026-10-26 — Ready-form startup synchronization and mobile delivery boundaries (unreleased)

Overall Python lines: **83.7123% → 83.9341%** (+0.2218 percentage points); branches **75.0189%**. Target85%, unchanged whole-Python scope. Both roots, 486 files: 10,035 collected, 10,014 passes, 21 existing platform skips, 906 subtests, zero failures/errors. Added 86 cases; 969 frozen source/lock/fixture hashes verified, exclusions365 unchanged; full duration387s. No upload, release, version bump, installation or owner-service restart.

Added86 cases across four files. A real prompt_toolkit application with an already pending user-input request reproduced Application.exit() failed: Application is not running. The turn loop scheduled run_async but inspected a pending request before the application initialized its run future. It now uses the documented pre_run callback to set an asyncio.Event and waits cooperatively for readiness before controlling the app. Existing completion, exception and cancellation paths still clean the run task. No timing sleep is used as the readiness proof.

Twelve real-application tests check one/two pre-existing forms, one queued follow-up without task resubmission, owned-task Ctrl+C/Esc semantics, history-viewer restoration, local TUI commands, an interrupt arriving before task creation, local persistent-session exits and one submitted turn with busy-state cleanup. All polling threads are joined before fixture teardown; task results and form handlers are synthetic. The compatibility run includes the existing five native owned PTY cases, without claiming native Windows or physical-device acceptance.

Mobile tests use real owned SQLite and session files for exactly-once read-only OpenCode result follow-up, busy/unavailable deferral, child attachment boundaries, source/change relative paths, journal reassignment and workdir/profile choices. The initial row reader assumed sqlite3.Row without setting row_factory; corrected in the fixture. Owned HTTP/TLS tests check invalid references/JSON, unavailable and failing schedulers, exact session/task/message route IDs, shared journal cursors, quota failures and real TLS requirement before a fake sudo decision; passwords never appear in responses. Menus persist ordering/deduplication, resets, invalid/cancelled inputs and skill toggles only in owned settings and skill stores. Final focused compatibility passes351 cases plus five existing subtests with no failures/errors.

Reports and refreshed maps: build/verification-coverage-goal85-checkpoint40. These are source-only changes; installed application behavior is unchanged.

## 2026-10-26 — Canonical tool schemas across API formats and resumed-call validation (unreleased)

Overall Python lines: **83.5685% → 83.7123%** (+0.1438 percentage points); branches **74.7975%**. Target85%, unchanged whole-Python scope. Both roots, 482 files: 9,949 collected, 9,928 passes, 21 existing platform skips, 906 subtests, zero failures/errors. Added 65 cases; 965 frozen source/lock/fixture hashes verified, exclusions365 unchanged; full duration384s. No upload, release, version bump, installation or owner-service restart.

Added65 cases across three files. After correcting fixture discovery, a missing browser descriptor and actual schedule required arguments, the21-case executor group reproduced six genuine failures: OpenAI textual Read salvage was not applied, two malformed textual Read inputs were accepted as text, both API formats executed a resumed pending Read missing file_path, and OpenAI normal required-field validation reached execution. Internal guards had received transport-converted function schemas rather than canonical name/input_schema records. Three consumers now receive anthropic_tools (the existing canonical variable); provider payload formatting and token estimation keep wire schemas. Pending calls now use the existing required-field helper before approval/execution and return the same structured argument error as normal calls. No salvage parser, prompt or model-specific branch changed.

The expanded37-case executor group passes valid recorded bracket-call salvage, bounded malformed salvage, dedicated web redirection, one-round schedule/skill completion, matching browser image receipts, existing unsupported-tool behavior, nonexecuting quoted reports and ordinary code, duplicate media prevention, task policy, suspended-context deduplication and same-task steer handling across APIs. Initial steer expectations were corrected to the actual follow-up-after-completion delivery boundary without product changes. The final focused compatibility run passes302 cases plus three existing subtests.

Host-service tests check bounded object JSON-RPC responses, error/close behavior, request budgets, ping readiness, tool result formats and field refusal. One actual owned Unix HTTP server verifies the authenticated request and result; atomic owned-file exports preserve an existing good result when the injected incoming stream grows or truncates. No actual service is started. The helper created two archive tests that passed independent execution unchanged: exact dummy executable bytes are extracted and an empty member is rejected. Function/fixture hashes and test class ASTs were verified; only a canonical import adapter was added. Both passed in the isolated canonical run; the own temporary client was revoked/deleted and the visible chat retained.

Reports and refreshed maps: build/verification-coverage-goal85-checkpoint39. These are source-only changes; installed application behavior is unchanged.

## 2026-10-26 — Staging provenance, bridge cleanup and typed guest transport failures (unreleased)

Overall Python lines: **83.2031% → 83.5685%** (+0.3654 percentage points); branches **74.6207%**. Target85%, unchanged whole-Python scope. Both roots, 479 files: 9,884 collected, 9,863 passes, 21 existing platform skips, 906 subtests, zero failures/errors. Added 152 cases; 962 frozen source/lock/fixture hashes verified, exclusions365 unchanged; full duration407s. No upload, release, version bump, installation or owner-service restart.

Added152 cases across six files. The40-case channel draft reproduced two TypeErrors for list/object search modes. A string guard preserves all valid modes and routes malformed types to session_search_request_invalid with fatal=False before any frame write; other access/size policies are unchanged. Accept-loop tests use an injected listener and synthetic kernel peer identity to check timeout, rejected peers, capacity and close-on-error paths, never native VSOCK.

Staging tests create a complete owned application/backend/legal allowlist and dummy runtime. They verify every output digest, ignored bytecode, both platform layout labels, version/schema/path rejection, stale source/provenance, private-state exclusion, legacy quota exclusion and symlink refusal. This validates portable staging policy, not native Windows install/launch. OpenCode tests use real owned binary socket pairs/loopback acceptance for bidirectional bytes and half-close; main uses fake sockets/processes/signals/libvirt and records only synthetic child environment and profile grants. Initial fixture errors omitted main initialization of the socket global and expected json instead of the real jsonc file; corrected without production changes. TCP assertions now collect exact expected bytes rather than assuming recv packet boundaries.

Guest-runner tests replace connection/handshake/executor boundaries and check cleanup, reply routing, cancellation and invalid initial frames. Archives preserve exact owned JSONL contents and collisions without overwriting another thread; malformed history summaries degrade safely. OAuth tests enforce bounded object replies, typed errors and no response-body leakage; two owned HTTP servers confirm bearer redirects are not followed. Real transient ECDSA signatures verify nonce/audience/time/curve/key-selection bindings. No actual key, authorization, VM, OpenCode service or model is used. Final focused compatibility passes312 cases with two existing platform skips; the adjusted socket file independently passes19 cases.

Reports and refreshed maps: build/verification-coverage-goal85-checkpoint38. These are source-only changes; installed application behavior is unchanged.

## 2026-10-26 — Empty tool-history recovery, interactive controls and cleanup failures (unreleased)

Overall Python lines: **82.8969% → 83.2031%** (+0.3062 percentage points); branches **74.1916%**. Target85%, unchanged whole-Python scope. Both roots, 473 files: 9,732 collected, 9,711 passes, 21 existing platform skips, 906 subtests, zero failures/errors. Added 104 cases; 956 frozen source/lock/fixture hashes verified, exclusions365 unchanged; full duration380s. No upload, release, version bump, installation or owner-service restart.

Added104 cases across five files. A29-case workflow draft reproduced one real failure: _extract_bash_file_target indexed splitlines()[0] for an empty command, causing IndexError while reconstructing tool exchanges. Splitting once on newline preserves the first-line rule and safely handles empty input. Remaining cases verify actual search/fetch exchanges, allowed URL evidence, deduplication, errors, completed answers and owned Python package/module resolution without network.

Actual prompt_toolkit applications consume pipe input for submit/multiline/transcript controls, overlay toggles, scrolling, reasoning visibility, persistent own-task cancellation, serialized form submit/cancel and fake clipboard paste. Rendered frame regions are checked against transcript spans and window offsets. CLI tests verify real parser flags with synthetic Wally, Vault, skill, task, doctor and model-selection boundaries. Initial fixture assumptions about the default alias were corrected; CLI imports require an owned root before collection, not only an autouse fixture.

Domain tests cover libvirt failure translation and dependency probes with fakes, attempts to finish teardown despite earlier errors, fake process reap/timeout and real owned Unix-socket ACL targets. No VM or native platform capability is claimed. The helper actually created two unittest methods and completed its visible task; independent execution initially passed one and failed one due to indexing the first argv string instead of the argv list, plus an incorrect slice. The parent repaired that assertion and removed an unused import; both then passed against the unchanged canonical checkpoint function. Original and repaired tests, AST/hash review and failed/passed reports are preserved. The temporary paired helper client was revoked/deleted, the chat retained and the active profile unchanged. Final focused compatibility:383 passes, one existing KVM/platform skip, five existing subtests, zero failures/errors.

Reports and refreshed maps: build/verification-coverage-goal85-checkpoint37. These are source-only changes; installed application behavior is unchanged.

## 2026-10-26 — Chat input, transfer failure and credential lifecycle contracts (unreleased)

Overall Python lines: **82.5389% → 82.8969%** (+0.3580 percentage points); branches **73.7144%**. Target85%, unchanged whole-Python scope. Both roots, 468 files: 9,628 collected, 9,607 passes, 21 existing platform skips, 906 subtests, zero failures/errors. Added 139 cases; 951 frozen source/lock/fixture hashes verified, exclusions365 unchanged; full duration401s. No upload, release, version bump, installation or owner-service restart.

Added139 cases across five files. Chat input tests verify idle/fullscreen return values, reasoning/viewer control signals, cleanup, exclusive routing, own-task-only cancellation and shell-safe resume hints; actual trace replay reads an owned Path with the canonical model.text_delta event. Initial fixture errors used a string path and a noncanonical event name; corrected to the actual interfaces without production changes.

Owned HTTP requests verify exact binary/range bytes, connection-close framing, streamed SSE frames, offline/error/revoked states, cursor validation, revoke-before-cookie-clear logout and invalid JSON rejection. The initial response double used dict headers, whereas urllib supplies Message/get_content_type; correcting the double resolves its errors. Direct draft tests replace process, MCP and Vault boundaries, preserving output selection, Unicode structured input, valid output after nonzero exit, malformed results, timeout and owned scratch cleanup. No actual agent or public post is invoked.

Voice tests verify environment/cache/Vault precedence, exact600-second cache expiry, fallback/deduplication, invalid JSON/SDP, typed errors that do not disclose upstream content and best-effort hangup. All keys and replies are synthetic. Worker tests verify MCP error formatting, interruption source, fail-closed resumed-operation decisions, metadata replacement and cached unavailable runtime. The final independent compatibility run passes398 cases and three existing subtests. No production source changes in this batch.

Reports and refreshed maps: build/verification-coverage-goal85-checkpoint36. These are source-only changes; installed application behavior is unchanged.

## 2026-10-26 — Portable OAuth configuration, update metadata and history navigation (unreleased)

Overall Python lines: **82.2915% → 82.5389%** (+0.2474 percentage points); branches **73.2784%**. Target85%, unchanged whole-Python scope. Both roots, 463 files: 9,489 collected, 9,468 passes, 21 existing platform skips, 906 subtests, zero failures/errors. Added 62 cases; 946 frozen source/lock/fixture hashes verified, exclusions365 unchanged; full duration381s. No upload, release, version bump, installation or owner-service restart.

The OAuth workflow draft reproduced a missing O_NOFOLLOW platform API failure. Configuration now uses the existing portable open_regular_file primitive, resolving authorized parent directories but not the final path component. Regression cases preserve relative paths and parent-directory symlinks while rejecting final symlinks. Real owned callback servers with fake token exchanges check PKCE, wrong-state retry, required tokens/scopes, cancellation and duplicate begin. No external authorization URL is opened.

The original 28-case update-control draft reproduced two failures: Python boolean schema/size values passed integer validation. Exact integer checks reject those values. Dummy payloads and fake extraction/install/restart boundaries check request validation, parent-live and failure results, snapshot version checks and state cleanup. Two helper-authored unittest methods were independently accepted against the unchanged verified_file function and import-adapted into the canonical suite: valid/checksum-tampered files and descriptor/inode replacement. The visible helper task completed successfully and its temporary paired client was revoked and removed.

History navigation uses actual prompt_toolkit applications with pipe input plus one owned native PTY. Navigation keys, EOF, raw SGR selection, expansion, scrolling, focus and terminal restoration are asserted; all clipboard operations use a fake buffer. Native child coverage is combined only after semantic and source-hash checks. The final focused compatibility group passes 199 cases and three existing subtests. No user service, model, installer or clipboard is touched.

Reports and refreshed maps: build/verification-coverage-goal85-checkpoint35. These are source-only changes; installed application behavior is unchanged.

## 2026-10-26 — MCP request recovery and UTF-8 child-process contracts (unreleased)

Overall Python lines: **82.0826% → 82.2915%** (+0.2089 percentage points); branches **73.0827%**. Target85%, unchanged whole-Python scope. Both roots, 458 files: 9,427 collected, 9,406 passes, 21 existing platform skips, 906 subtests, zero failures/errors. Added 81 cases; 941 frozen source/lock/fixture hashes verified, exclusions365 unchanged; full duration382s. No upload, release, version bump, installation or owner-service restart.

The original39-case X-search group reproduced19 framing failures: non-object input could raise again inside the exception handler and kill the loop, malformed JSON reused the previous request ID, and falsey/wrong-type params/arguments were misclassified or reached tool code. The loop now resets request_id per frame, rejects non-object requests with -32600 and invalid call objects with -32602, then serves the next frame. Existing query/result-limit coercion, missing-field behavior, unknown method/tool handling and parse-error compatibility remain. Dotenv paths are redirected to owned fixtures and no real bearer is read.

The corrected native14-case security/execution group reproduced three genuine failures in UTF-8 input/output and timeout cleanup, with11 policy cases already passing. The first native fixture passed Unicode through an ASCII -c argument; it was corrected to an owned UTF-8 script with an ASCII path and explicit spawn proof before product conclusions. Actual ASCII-parent pipes then showed lost/undecodable text and unreaped timeout returncode. Popen now declares UTF-8 and replacement decoding; timeout cleanup performs a bounded wait after kill. Native reports retain source hashes, actual parent codec, callbacks, child cleanup before fixture cleanup, scripts and child branch data. Only owned echo/sleep Python processes are used.

New81 cases also cover actual owned approval-store records for disabled/observe/enforce modes, exact interactive retry/session proof IDs and resolvers, explicit autonomous target/flag rules and route approvals; Gmail JSON-size/shape and redirect limits, refresh-token boundaries, MIME/plaintext/header limits, unsupported tool calls and mid-read revocation. A first Gmail fixture assumed the urllib redirect handler was an instance, although the supported builder also accepts a handler class; that expectation was corrected without product changes, and oversized parameter IDs were shortened. An actual two-server loopback redirect test confirms no bearer reaches the redirected target. The final focused compatibility group passes188 cases. No actual mail, search, model, agent process or user service is contacted.

Reports and refreshed maps: build/verification-coverage-goal85-checkpoint34. These are source-only changes; installed application behavior is unchanged.

## 2026-10-26 — Stream shape validation and terminal response transport contracts (unreleased)

Overall Python lines: **81.7459% → 82.0826%** (+0.3366 percentage points); branches **72.8365%**. Target85%, unchanged whole-Python scope. Both roots, 455 files: 9,346 collected, 9,325 passes, 21 existing platform skips, 906 subtests, zero failures/errors. Added 79 cases; 938 frozen source/lock/fixture hashes verified, exclusions365 unchanged; full duration374s. No upload, release, version bump, installation or owner-service restart.

The original20-case SSE group reproduced19 failures: malformed JSON shapes were inconsistently classified at normal event boundaries, and final leftover events were processed outside the surrounding read exception handler, letting AttributeError/TypeError escape. A legitimate nullable terminal delta also failed. Explicit shape checks now reject malformed object/choice/delta/tool-call index/ID/function/name/argument fields with PROTOCOL.INVALID_RESPONSE at sse_event, naming only the invalid field and exposing no raw content. Nullable optional deltas/functions, usage-only frames and valid fragmented calls remain supported. The expanded37-case group passes alongside19 previous wire and63 reasoning-stream cases; partial malformed calls never become success.

Two initial OpenAI media-acknowledgement failures, plus a forced-Write comparison, showed that short terminal helpers sent Anthropic-style payloads to Chat Completions and then read only Anthropic content/usage, losing the valid response and counters. Both helpers now accept the explicit task transport flag through one small adapter using existing converters; Responses requests retain their existing adapter route and Anthropic defaults are preserved. No authentication heuristic or model-specific rule is added. Tool generation is never repeated to repair an acknowledgement. An initial forced-Read failure fixture incorrectly expected immediate termination rather than a follow-up containing the tool error; only that expectation was corrected, preserving existing recovery policy.

New79 cases include three real owned POSIX daemon-loop/lock/cleanup checks with synthetic scheduler/watchdog, 37 stream-shape cases,19 executor workflow transitions and20 offline heartbeat/action-log cases. Executor tests cover bounded policy retries, explicit round and iteration limits, midstream cancellation, pending approval IDs/reasoning/context and remaining batch calls, best-effort progress consumers and terminal response usage. Social clients and actions are all replaced; real owned SQLite action logs are queried and summarized. No posts or comments are published. The final focused compatibility group passes258 cases. No user service or model is restarted.

Reports and refreshed maps: build/verification-coverage-goal85-checkpoint33. These are source-only changes; installed application behavior is unchanged.

## 2026-10-26 — Shared CLI runtime, SmartHelp and bootstrap contracts (unreleased)

Overall Python lines: **81.4141% → 81.7459%** (+0.3319 percentage points); branches **72.5913%**. Target85%, unchanged whole-Python scope. Both roots, 451 files: 9,267 collected, 9,246 passes, 21 existing platform skips, 906 subtests, zero failures/errors. Added 106 cases; 934 frozen source/lock/fixture hashes verified, exclusions365 unchanged; full duration371s. No upload, release, version bump, installation or owner-service restart.

The original24-case SmartHelp group reproduced two failures: URLError escaped the user-facing SmarthelpError contract, and the tool_name callback alias was forwarded twice. Network/IO/timeout errors now use the existing domain error; the normalized alias is removed from extra callback fields. Plain/agent answer handling, no-key rejection, UTF-8 payloads, synthetic auth, usage summaries and bounded tool previews are tested with fake provider/executor boundaries.

New106 cases cover shared CLI runtime frames, instance IDs, profile validation, duplicate-worker prevention, shared execution locks, busy-stop refusal, scheduler-count cleanup, process-ready/exit/timeout outcomes, restart ordering and actual owned socketpair/Unix client-server roundtrips; the installed igorek entrypoint parser, profile selection persistence, resume validation, local daemon commands and conventional error exits; two independently reviewed helper-authored preview methods; and real temporary five-step memory bootstrap, interrupted setup, explicit force behavior and idempotent memory writes. No configured worker, actual model, editor or user service is started or stopped. A first launcher fixture precreated its private state directory with permissive mode and its fake initializer did not populate the profile catalog; those fixtures were corrected separately without production changes.

The corrected bootstrap group reproduced five non-object JSON state failures; the first draft also had two fixture mistakes (keyword-only provider argument and unsupported daily-colon syntax), corrected separately. State reading now accepts dictionary JSON or returns a fresh default copy; invalid source bytes are left unchanged. Existing plain requests and the actual supported daily-memory syntax remain compatible. The focused compatibility group passes230 cases and12 existing subtests. The helper created exactly two methods, completed successfully, and passed independent exact-source/canonical runs with class/function AST and fixture-hash checks; only an import adapter was used.

Reports and refreshed maps: build/verification-coverage-goal85-checkpoint32. These are source-only changes; installed application behavior is unchanged.

## 2026-10-26 — ACP execution and resilient session recall contracts (unreleased)

Overall Python lines: **80.9508% → 81.4141%** (+0.4633 percentage points); branches **72.2578%**. Target85%, unchanged whole-Python scope. Both roots, 446 files: 9,161 collected, 9,140 passes, 21 existing platform skips, 906 subtests, zero failures/errors. Added 94 cases; 929 frozen source/lock/fixture hashes verified, exclusions365 unchanged; full duration376s. No upload, release, version bump, installation or owner-service restart.

The original20-case ACP group had five failures: ordinary and unknown-slash requests silently ended before reaching the executor because two relative imports targeted nonexistent ACP submodules; /tools used a nonexistent local module and registry.list_tools API; the standalone async runner nested asyncio.run inside its active loop. Imports now resolve the existing core executor/prompt builder, /tools consumes get_tool_schemas and the runner directly awaits acp.run_agent. Tests use real ACP objects, sessions, thread callbacks and FIFO tool-call identities with a synthetic executor and AsyncMock transport. No new history/cancellation feature is introduced or claimed.

Three original provider-session cases showed that NaN/infinite expiry values raised during integer conversion; they now return the existing missing-expiry sentinel. Temporary credential files, fake time, cache bypass, rotated refresh fields, unrelated metadata, account totals/prices, model catalog shapes and network errors are tested without contacting the provider.

The corrected20-case recall fixture reproduced11 failures for malformed/wrong-type tags and invalid timestamps. Optional tags now decode through one shared list-normalizer in lexical, local semantic and archive semantic paths. Invalid timestamps retain useful text with the existing unknown-time fallback. Four additional vector cases reproduced conversion failures or nonfinite false matches; invalid vectors are ignored per record and valid results remain. Actual owned SQLite/FTS/session JSONL operations and fixed two-dimensional embeddings are used. An initial embedder fixture omitted its dimensions field and an initial FTS test allowed ensure_schema to recreate the table; both fixtures were corrected separately, not counted as product fixes. The final fallback test uses an ordinary table to make MATCH unavailable.

New94 cases also protect context-compaction system/recent-tail retention, source immutability, summary reuse, deterministic fallback/cooldown, old-tool pruning, call/result pairing and bounded summaries, using a fake summary client. The full focused compatibility group passes175 cases; no user data, running services or model processes are changed.

Reports and refreshed maps: build/verification-coverage-goal85-checkpoint31. These are source-only changes; installed application behavior is unchanged.

## 2026-10-26 — File, memory, skill and Vault lifecycle contracts (unreleased)

Overall Python lines: **80.3241% → 80.9508%** (+0.6267 percentage points); branches **71.7454%**. Target85%, unchanged whole-Python scope. Both roots, 442 files: 9,067 collected, 9,046 passes, 21 existing platform skips, 906 subtests, zero failures/errors. Added 137 cases; 925 frozen source/lock/fixture hashes verified, exclusions365 unchanged; full duration368s. No upload, release, version bump, installation or owner-service restart.

The original12-case Vault CLI group reproduced one genuine failure: importing a temporary .env with source redaction enabled returned redacted_source=true but wrote the entire original assignment, including its secret value, into a comment. The redacted line now retains only the environment variable name and a redacted marker. Tests use a separate real encrypted SQLite Vault and verify secret-byte removal, unchanged unrelated lines, owner-only POSIX permissions, ordinary non-redacting imports, duplicate rejection without source modification, explicit replacement and metadata-only output. No user credential or original configuration is accessed.

New137 cases cover real temporary Write/Edit/Read operations, checkpoint-before-mutation ordering, unchanged bytes when checkpoints fail, best-effort change tracking, JSON-comment normalization and actual ripgrep patterns; post-turn memory proposal parsing, provider headers, explicit target citations and real temporary Saved facts updates; actual skill package creation, forced reinitialization, resource retention, enable/disable state and usage ordering in owned SQLite; Vault command lifecycle, scanning ignored/comment/large/nontext files, secret fingerprints without disclosure, duplicate files, path-policy classification, and provider verification with synthetic HTTP/network failures. Provider callbacks never contact the network. A first skills test used metadata key id rather than the actual skill_id; only that fixture expectation was corrected. The focused compatibility group passes194 cases, including previous Vault import, memory and skill tests.

Reports and refreshed maps: build/verification-coverage-goal85-checkpoint30. These are source-only changes; installed application behavior is unchanged.

## 2026-10-26 — Search quoting, chat keyboard and host web contracts (unreleased)

Overall Python lines: **79.9815% → 80.3241%** (+0.3426 percentage points); branches **71.0061%**. Target85%, unchanged whole-Python scope. Both roots, 437 files: 8,930 collected, 8,909 passes, 21 existing platform skips, 906 subtests, zero failures/errors. Added 177 cases; 920 frozen source/lock/fixture hashes verified, exclusions365 unchanged; full duration405s. No upload, release, version bump, installation or owner-service restart.

The original search tests reproduced seven failures out of43: unpaired trailing presentation-quote removal changed ordinary query text, and regex body extraction failed on shell-escaped apostrophes and quoted JSON. Query cleanup now removes only paired presentation quotes; body decoding uses shlex.split without execution; reconstructed retry commands use shlex.quote. Ordinary JSON and repeated form-data bodies remain supported. Shell-like query text is verified as a single JSON argument; no command is executed.

New177 passing cases cover shared chat input, clearing after submission, multiple queued follow-ups, cancellation before a task ID exists, local commands, completion and clipboard adapters; actual API CLI configuration with server startup replaced, missing-key listener rejection, malformed tool inputs and streamed/nonstreamed protocol serialization; host search/extraction errors, JSON-shape errors, provider override, secret redaction, URL limits and isolated session-search validation. A first keyboard preview check used an incorrect render_lines signature; it was corrected without product changes. Four first API expectations incorrectly assumed automatic retry after a Bash error; the existing short-circuit behavior is explicitly retained and tested: report the error without reexecution.

Six helper-authored JSON tests were independently run against the exact extracted source and the canonical module with only an import adapter. Class/function ASTs and fixture hash were checked. One requested prefixed-JSON recovery case was omitted by the helper and added separately by the parent. The helper created the file but did not promptly finish its response; only that owned assignment was cancelled after artifact acceptance. No completed-helper or restarted-model claim is made.

Reports and refreshed maps: build/verification-coverage-goal85-checkpoint29. These are source-only changes; installed application behavior is unchanged.

## 2026-10-25 — Resume directory selection and Telegram code preservation (unreleased)

Overall Python lines: **79.6414% → 79.9815%** (+0.3401 percentage points); branches **70.5233%**. Target85%, unchanged whole-Python scope. Both roots, 432 files: 8,753 collected, 8,732 passes, 21 existing platform skips, 906 subtests, zero failures/errors. Added 100 cases; 915 frozen source/lock/fixture hashes verified, exclusions365 unchanged; full duration374s. No upload, release, version bump, installation or owner-service restart.

Interactive resume failed before chat startup when a selected session had another working directory: the CLI imported a missing prompt and used incompatible enum/outcome names. The current prompt_toolkit frontend now provides the directory prompt, reusing the existing screen state with current key-event APIs. The CLI consumes its typed outcome. Saved/current directory choice, cancellation, missing saved folders and explicit-ID/new/fork paths have regression coverage. Six actual owned POSIX PTY cases check keyboard behavior and terminal-mode restoration; session selection and provider/chat startup boundaries are recorded rather than launching a model. No native Windows acceptance is claimed. Original reproduction:25 failing cases out of35; final CLI compatibility and native runs are retained.

Telegram HTML formatting double-escaped inline code, applied Markdown formatting inside code, and interpreted literal thinking tags as reasoning. A shared protected-block matcher now separates fenced code, inline code and actual thinking blocks before escaping/formatting. Code is escaped once, literal marker-like text is retained with a collision-free placeholder namespace, and the long-thinking fallback uses the same protection so long code containing thinking tags stays code. Real long thinking keeps its document fallback. Tests inspect rendered HTML structure, actual request payload construction and temporary fallback documents, using fake network boundaries with no real Telegram delivery. Initial converter reproduction had13 failures out of17; a further two failing cases exposed the earlier long-thinking fallback. Both stages are preserved as evidence. Additional dispatch tests cover profile/model/provider commands, help, attachments, approvals and chunked text without real providers.

The final focused group passes278 cases. Two independently reviewed helper methods covering ANSI atoms and styled slices are integrated with only a canonical import adapter; source AST/fixture checks and independent standalone/canonical runs are retained. The full run is the acceptance result, not a targeted estimate. Reports and refreshed maps: build/verification-coverage-goal85-checkpoint28. Installed application behavior is unchanged; source-only fixes await a future authorized update.

## 2026-10-25 — Adaptive Markdown table text and ANSI wrapping (unreleased)

Overall Python lines: **79.4601% → 79.6414%** (+0.1813 percentage points); branches **70.2139%**. Current user target85%, unchanged whole-Python scope. Both roots, 426 files: 8,653 collected, 8,632 passes, 21 existing platform skips, 906 subtests, zero failures/errors. Added 119 cases; 909 frozen source/lock/fixture hashes verified, exclusions365 unchanged; full duration367s. No upload, release, version bump, installation or owner-service restart.

The actual public Markdown renderer lost first-column HTML-like spillover when a grid switched to compact records. Both record fallback paths discarded spillover lines already collected from the table. They now carry those lines into the returned layout. The input is still literal Markdown text with HTML parsing disabled; no HTML execution or parser-mode change is introduced. The first21-case reproduction fails10 cases on the original canonical source, whose hash is preserved. Wider grids and comments provide positive compatibility cases.

Compact record labels and styled values were wrapped as raw strings, splitting SGR color sequences into visible fragments on narrow widths. They now use the existing ANSI-aware word wrapper when styled, retaining the previous ordinary-text wrapper otherwise. Tests verify complete control sequences, retained bold/italic/code values, empty values, header-only tables, multiple records, aligned/stacked layouts, mode-switch thresholds and unchanged input data. Character-count checks cover long/narrative/numeric/Russian cell values across several widths, including unlimited width; they prove retention, not comprehensive Unicode terminal-column/grapheme accuracy.

The expanded pre-fix comparison is retained, including an initially overstrict positive test which concatenated interleaved grid columns as though each cell stayed contiguous. Its input/expectation was corrected without changing production logic. The final compatibility group passes215 cases. These are actual parser/renderer and public record-layout calls, not function copies, private-branch forcing or a native GUI claim. A separate authorized local helper created two ANSI atom/slice methods. Parent review and independent standalone/canonical execution pass; none are merged into this frozen run. Evidence and refreshed maps: build/verification-coverage-goal85-checkpoint27. Target85% remains unfinished; installed application behavior is unchanged until a future authorized update.

## 2026-10-25 — Queue lifecycle, provider handoff and web proxy contracts (unreleased)

Overall Python lines: **79.1311% → 79.4601%** (+0.3290 percentage points); branches **69.9092%**. The current user target is **85%**, superseding the former90%target without changing measurement scope. Both roots, 424 files: 8,534 collected, 8,513 passes, 21 existing platform skips, 906 subtests, zero failures/errors. Added 149 cases; 907 frozen source/lock/fixture hashes verified, exclusions365 unchanged; full duration374s. No production behavior change, upload, release, version bump, installation or owner-service restart.

Actual Worker queue runs use temporary SQLite, owned heartbeat threads and synthetic execution. They verify profile ownership, persistent statuses/events/usage, paused-task completion timestamps, stop before claiming, idle and error shutdown, ready-presence cleanup, periodic stale-task cleanup and deduplicated next-step payloads. Heartbeats run during an execution wait and stop on success or exception. No model process is started and no owner queue is touched.

Worker post-tool synthesis tests use actual JSONL parsers and a fake security-execution boundary. They reject pre-tool commentary as a final answer, request a bounded synthesis only when evidence exists, preserve explicit failure on missing final answers, combine usage while keeping context-window precedence, and stop rollout monitoring on error. CC-worker tests verify actual prompt assembly, executor policy restrictions for Plan/explicit skills, stable tool-wire behavior, structured history, project instructions, optional checkpoint shapes, response-header refresh and diagnostic/usage preservation. A prior inside-file-symlink test now checks valid target resolution without requiring duplicate enumeration; behavior, coverage scope and case count are unchanged.

Owned loopback HTTP servers and a generic echo bridge exercise24 valid proxy routes and their no-cookie counterparts, invalid identifiers/mutations, bounded session limits, query allowlisting and binary uploads across multiple chunks. Tests verify exact byte preservation, header cleanup, filename/mime bounds, incomplete-body rejection and cookie clearing after revocation. Actual HTTP-to-WebSocket upgrades and masked frames reject malformed or unknown terminal tickets and non-open first messages without spawning a shell or contacting an actual bridge. A first ticket fixture confused malformed syntax with an unknown well-formed ticket; both distinctions are now asserted. A subsequent run without socket permissions produced environment setup errors; the corrected permitted-loopback run passes149 tests and10 subtests. Those failures are retained separately and are not product bugs or skips.

An authorized local helper created six tool-filter/input-surface methods with ten subtests. Parent review verifies unchanged source microfixture and canonical function ASTs, preserves the test-class AST and adapts only its import. Independent standalone and canonical execution pass. Its temporary client was removed; its chat, active user profile and model server remain intact. Full evidence, helper receipts and maps: build/verification-coverage-goal85-checkpoint26. The85% goal remains unfinished; installed application behavior is unchanged.

## 2026-10-25 — Structured keyboard input and Windows process cleanup (unreleased)

Overall Python lines: **78.6754% → 79.1311%** (+0.4557 percentage points); branches **69.4861%**. Both roots, 419 files: 8,385 collected, 8,364 passes, 21 existing platform skips, 896 subtests, zero failures/errors. Added 119 cases; 902 frozen source/lock/fixture hashes verified, exclusions365 unchanged; full duration367s. No upload, release, version bump, installation or owner-service restart.

Real prompt-toolkit key parsing revealed lost j/k, spaces and Backspace in text and secret fields. Navigation shortcuts now apply only outside text editing, with an explicit Backspace binding. Selection still supports arrows, j/k, Space and Enter; Tab/Shift+Tab preserve separate answers, and cancellation/EOF do not return partial answers. The existing no-prompt-toolkit form fallback also ignored text entirely; two original regressions failed, and text/editing now works there without restoring any alternate CLI frontend. Secret fallback rendering remains masked.

The stdin drain loop repeatedly read EOF forever. Both a bounded synthetic EOF regression and an actual owned DEVNULL child reproduce it before correction. The loop now stops on EOF and tolerates unselectable/closed streams. Twelve owned POSIX PTY cases verify raw arrow/control/Unicode key decoding and terminal-mode restoration. Each child imports the canonical source, verifies its hash and contributes branch coverage only after parent validation. Prompt-toolkit form cases use actual event loops with owned input pipes and dummy output; this is not a native graphical UI claim.

Portable Windows process contracts found three early-startup failure paths leaving already-enabled inheritable handles unchanged. Attribute preparation and inheritance changes are now inside a cleanup region; only initialized attribute lists are deleted and every successfully modified handle is reset. Synthetic WinAPI tests cover creation, job-before-resume ordering, explicit deduplicated stdio, failures, environment copying, optional adapter unloading, exit status and cleanup. Temporary-tree tests cover constructor scope checks, grants, SID/descriptor allocation ownership, ACL presence, symlink handling and ancestor-grant revocation. No real Windows API, token, ACL or native child is used by these portable checks; existing native Windows acceptance remains separate. One initial metadata fixture used the wrong synthetic error code and was corrected before the genuine three-case comparison.

An authorized local helper authored six usage-counter methods with three subtests, independently accepted against both its unchanged source microfixture and the canonical worker function. Only the import changed; test-class AST is identical. Tests verify sums, independent copies, null/string counters and latest nonzero context-window precedence instead of summation. The helper created its file, and its temporary client was removed while retaining its chat and the current profile. The final compatibility group passes230 tests and3 subtests. Full evidence, helper receipts and refreshed maps: build/verification-coverage-goal90-checkpoint25. The90% goal remains unfinished; installed runtime behavior is unchanged until a future authorized update.

## 2026-10-25 — Bash command destinations and adjacent subshell separators (unreleased)

Overall Python lines: **78.5317% → 78.6754%** (+0.1438 percentage points); branches **69.0648%**. Both roots, 414 files: 8,266 collected, 8,245 passes, 21 existing platform skips, 893 subtests, zero failures/errors. Added 126 cases; 897 frozen source/lock/fixture hashes verified, exclusions365 unchanged; full duration401s. No upload, release, version bump, installation or owner-service restart.

The static Bash guard lost following commands when shlex grouped adjacent punctuation such as closing parentheses with semicolons, logical operators or pipes. Seven valid command-chain regressions reproduce the missing outside destination against the original source and detect it after correction. Unquoted parentheses are now separated before tokenization, retaining quoted/escaped strings and comments. Tests also prove subshell directory state does not leak into the following outer command. The original general regression group had eight failures; one separate initial audit fixture used an incorrect cp destination and was corrected rather than treated as a product bug.

Public scope-decision tests cover output options for archive, download and Git commands; copy target directories; in-place sed/perl; sort/dd/xargs; explicit script and nested-shell destinations; static versus ambiguous cd; reads and descriptor redirects; quoted Unicode paths, symlink escapes, sibling prefixes and granted extra roots. Audit tests use actual temporary JSONL files, restrictive permissions, rotation, redaction before truncation and injected storage failures. A failing audit cannot turn a denial into permission. Trial cutover dates and explicit modes retain existing decisions. No shell command strings from these cases are executed. This heuristic remains separate from the operating-system sandbox and does not claim complete shell-language confinement.

The targeted compatibility group passes194 tests and55 subtests. A separate authorized local helper created six usage-counter test methods for the next checkpoint. Parent review and independent standalone execution pass; none are integrated into this frozen run. Full evidence and refreshed maps: build/verification-coverage-goal90-checkpoint24. The90% goal remains unfinished; installed runtime behavior is unchanged until a future authorized update.

## 2026-10-25 — Imported timestamps, API request validation and lifecycle (unreleased)

Overall Python lines: **78.3581% → 78.5317%** (+0.1736 percentage points); branches **68.7896%**. Both roots, 413 files: 8,140 collected, 8,119 passes, 21 existing platform skips, 893 subtests, zero failures/errors. Added 86 cases; 896 frozen source/lock/fixture hashes verified, exclusions365 unchanged; full duration363s. No upload, release, version bump, installation or owner-service restart.

An isolated helper authored two timestamp tests with six subtests. Independent review preserved its test-class AST and adapted only the canonical import. The original numeric parser raises on NaN, infinities and unrepresentable integers. Numeric conversion now returns an unknown date instead of aborting the imported history catalog. Parent regressions exercise real temporary JSONL/SQLite histories and existing seconds, milliseconds, fractional, negative and ISO/timezone values. The helper created its file; production correction and acceptance were performed by the maintainer. The helper did not modify production sources or owner data.

Factory API startup now closes the server socket and stops its background services on normal serve termination, KeyboardInterrupt, serve failure and bind failure. Tests include a real loopback health request and shutdown with temporary SQLite, plus host-selection cases without public binds. POST task validation rejects malformed body/text/profile/capabilities/metadata before any queued row is created; optional null fields retain default behavior. Authentication/CORS precedence and valid custom metadata remain compatible. This covers deterministic request validation, not model-specific prompting.

Schedule execution tests verify persisted output, error recovery, truncation and delivery boundaries with a fake gateway and fake message transport. A null delivery target previously became the literal string None; absent targets now produce no delivery attempt. Initial schedule fixture import errors were corrected before comparing behavior and are retained separately from the one genuine original regression. No actual messages or model calls occur. The final targeted compatibility group passes194 tests and6 subtests. Full evidence, helper receipts and maps: build/verification-coverage-goal90-checkpoint23. The90% goal remains unfinished; installed runtime behavior is unchanged until a future authorized update.

## 2026-10-25 — Task interruption, context-cache validation and tool-result contracts (unreleased)

Overall Python lines: **78.2678% → 78.3581%** (+0.0903 percentage points); branches **68.7018%**. Both roots, 408 files: 8,054 collected, 8,033 passes, 21 existing platform skips, 887 subtests, zero failures/errors. Added 88 cases; 891 frozen source/lock/fixture hashes verified, exclusions365 unchanged; full duration388s. No remote upload, release, version bump, host installation or owner-service restart.

Registered local commands observed per-command stop requests but ignored task-level cancellation during communicate. Three native regressions failed before correction: Event interruption and actual database task cancellation waited for the tool timeout. The existing cancellation predicate is now stored in thread-local tool context and consulted by the registered command wait loop. Native tests cover SIGTERM, a process ignoring SIGTERM and forced reaping, real task cancellation, unchanged per-session stop targeting, registry cleanup and thread/task isolation. Legacy untracked and guest execution paths are preserved; new native coverage is Linux procfs/process-group acceptance, not Windows acceptance. The last targeted compatibility group passes182 tests and7 subtests. Tests operate only on temporary databases and owned children.

Malformed optional context-cache roots, model maps and limit objects raised eight original AttributeErrors. Shape checks now skip invalid data and retain valid later provider metadata and existing registry precedence. Thirty-two cache cases use a temporary HOME and substitute registry lookup boundaries, without real model/network access. A terminal skill-delete confirmation previously claimed creation, validation and opening as well; three regressions failed. It now confirms only deletion. Web/MCP approval and completed artifact routing contracts are verified with synthetic tool results and no external service calls.

Fourteen end-to-end reasoning-turn cases prove the preceding splitter fix is consumed correctly: both OpenAI and Anthropic shapes request another turn after unfinished reasoning, repeated reasoning-only responses fail explicitly, and actual temporary-file success appears only after a real authorized Write. Ordinary answers and code samples do not cause spurious retries. This does not guarantee that an arbitrary model will choose the correct tool. A separate authorized local helper is assigned isolated timestamp-import tests for the next checkpoint; none of its pending changes are merged into this frozen run. Full evidence and updated maps: build/verification-coverage-goal90-checkpoint22. The90% goal remains unfinished.

## 2026-10-25 — Stream reasoning, MCP deadlines and external history recovery (unreleased)

Overall Python lines: **77.9429% → 78.2678%** (+0.3249 percentage points); branches **68.5404%**. Both configured roots, 403 files: 7,966 collected, 7,945 passed, 21 existing platform skips, 887 passing subtests, zero failures/errors. Added 156 cases; 886 frozen source/lock/fixture hashes match. Exclusions remain365. Full duration355s. No version bump, remote upload, release, installation or owner-service restart.

A provider emitted another literal think block in OpenAI content after native reasoning. Raw content was immediately traced as visible text, while final normalization only recognized complete tags. The fixture-first reproduction failed35 of43 initial cases. A shared incremental splitter now classifies before visible tracing, buffers partial tags and keeps unfinished thinking in the reasoning channel. Ordinary text, escaped tags, fenced/inline code examples, native reasoning, structured tool calls, usage, passback, terminal guards and provider text-block boundaries remain covered. Long text is assembled with linear joins. The final 11-file compatibility group passes256 tests and14 subtests; a held-open real local SSE connection proves classification before provider completion. Replaying the observed content routes its thinking away from visible output. This repairs display/routing and does not promise to change a model's decision to keep thinking instead of using a tool. Literal nonprotocol text is not classified by language or phrases.

Initial MCP catalog listing ignored the configured synchronization deadline while additions were bounded. Three regression cases failed before correction. Listing now receives the same timeout policy (minimum5seconds, default30); a real isolated sleeping Python child confirms expiration before any addition. Existing servers, partial errors, environment copying and generated Claude configuration are checked with temporary profiles. Malformed external Claude message counts could abort the whole catalog; two original-trigger cases failed. Invalid counts now become0 while the other sources remain available. Real isolated JSONL/SQLite scenarios cover Codex rollout/history fallback, call/result identities, reasoning/attachments, malformed indexes, file boundaries, and byte-identical provider databases after reads/imports. The read-only access policy is unchanged.

The authorized local helper produced eight useful unittest methods with two subtests, but returned code in the answer without creating the requested file. Parent review materialized the exact class, changed only its import and checked function/class AST equality; isolated and canonical runs pass. Missing whitespace-normalization assertions were added separately by the parent. The visible helper session is retained and only its temporary paired client was revoked/deleted. No helper completion/file-creation claim is inferred from task status alone. Evidence and current maps: build/verification-coverage-goal90-checkpoint21. Unreferenced compatibility helper bodies were not tested solely to increase the percentage. The90% goal remains unfinished.

## 2026-10-25 — Theme the chart revision menu (unreleased)

Replace the chart's native select popup, which used a white/blue system palette on Linux, with the existing web application's Popover approach. The menu uses shared panel/text/lime tokens, marks the current revision and supports pointer selection, arrow/Home/End navigation, Escape with trigger-focus restoration, Tab and outside dismissal. Positioning stays inside narrow viewport edges and disposal closes the popup. An initial browser acceptance run exposed asynchronous focus restoration overriding a rapid End selection; focus now moves synchronously when opening, and the unchanged interaction check passes.

Verification: existing 495 desktop contracts pass, JavaScript syntax and whitespace checks pass. Real Web UI scripts with synthetic data pass opening/theme colors, revision selection, retained data-panel state, arrows/End, Escape focus, outside click, disposal, narrow-screen bounds, and Escape closing only the revision menu inside an expanded chart. Existing data/SVG/export/history checks also pass. Screenshots and logs: build/verification-chart-menu-20261025. These are isolated browser checks, not newly claimed native Electron or Android checks. Python/Android sources and reported Python coverage are unchanged. Source-only fix for a future batch; no new version, public upload/release or owner application/model restart.

## 2026-10-25 — Release 0.2.24 preparation

The user explicitly requested a downloadable runtime release for the new chart tool. Freeze accepted inline charts and checkpoint19 tests/fixes; bump Python/Desktop once to0.2.24. Android stays0.1.124(125), the verified preview2 source. Add matching release notes and editable English/Russian data-flow diagrams. Publish only the accepted protected-main source and matching packages; keep the newly reported long-running Bash cancellation investigation outside this frozen release unless it blocks installation. Existing installed owner runtime and model server are not restarted by release checks.

## 2026-10-25 — Persistent inline charts for desktop and Android (unreleased)

Implemented provider-neutral `render_chart` in the existing built-in tool registry/session toolset. It accepts bounded bar/line/pie specifications (40 labels, 6 series, finite numbers, unique names, safe axis bounds); successful metadata is under 500 bytes. The complete specification stays in ordinary tool-input history, avoiding a new file store or broader permissions. Same chart_id updates one projected card while retaining raw history and selectable versions. Disabled toolsets remain disabled; native external agents need their ordinary tool exposure, not a provider-specific runtime fork.

The desktop Web UI renders validated/escaped SVG inside the chat, adapts narrow containers to labelled horizontal comparisons, exposes source data, SVG/CSV downloads and expanded viewing, and keeps data-table state during updates. It supports line/pie rendering too. Dynamic text is escaped, numeric geometry is finite, and CSV text labels are formula-escaped while numeric values stay numeric. The server explicitly serves charts.js. Android keeps raw chart JSON in history, validates the same core contracts and renders native Compose bars/line/pie with exact values, source text, data, CSV, fullscreen viewing and prior versions. Android0.1.124(125), previous debug signer retained; desktop/Python version remains0.2.23 until a separately authorized release.

Verification: full Python both roots 7,810 collected/7,789 passes,21 existing platform skips,885 subtests, zero failures/errors in399files. Overall lines 77.9252%→77.9429%; new tool expands the denominator, exclusions remain365. 32 new tool contracts plus one actual HTTP asset test passed. Desktop 495 tests pass; browser checks actual app scripts with synthetic API data, bar/line/pie output, history replay, no duplicate card on update, correct revision selection, retained data-panel state, table, SVG download and expansion. Android 477 unit tests and lint0errors pass, including actual SessionTimeline history restoration; APK version/assets/signature verified. The first new HTTP assertion used lowercase headers with a case-sensitive fixture; corrected to Content-Type, without changing server behavior. The full run includes the corrected157-case web UI suite. Production Python did not change during that run; the additional HTTP test was added before its file executed. Source inventory was recorded after the run, not described as a pre-run freeze.

Local native Android acceptance did NOT complete: the disposable Android14 x86_64 emulator had no KVM, repeated system and SystemUI ANRs, unavailable initial external storage and a null screenshot. A readiness/awake check and internal-cache capture were added to the fixture, but the run remained blocked by system ANRs and was stopped. No native/physical-device PASS is claimed. The mobile screenshot is explicitly a web preview of the responsive production component; the desktop screenshot uses the real Web UI with synthetic data. The future Android CI now exercises ChartInstrumentation on its existing KVM emulator and retains its screenshot; CI was not dispatched. The first APK preview was sent before the final Android error-routing and small-value formatting corrections. Corrected preview2 (same unreleased 0.1.124/125 version and signer) was verified and sent separately, message3022 superseding message3021; both receipts and binaries are retained. Portable SVG export includes title, legend and source. Owner applications, model8081, Chrome downloads and published releases were not changed.

Evidence: build/verification-charts-20261025 and build/verification-coverage-goal90-checkpoint20. A new release/installation is required before the currently running desktop advertises the tool. No public GitHub delivery or release was requested or performed. The 90% coverage goal remains unfinished.

## 2026-10-25 — Tool continuation and live syntax highlighting (unreleased)

Overall Python line coverage: **77.6389% → 77.9252%** (+0.2863 percentage points). Branches: **68.0039%**. Both roots: 7,777 collected, 7,756 passed, 21 existing platform skips, 885 passing subtests, zero failures/errors across 398 files. Added 89 tests; 873 frozen Python/lock source hashes verified; excluded lines remain 365. Full duration 349s. Scope and exclusions are unchanged; the 90% objective remains unfinished. No new version, release, GitHub delivery, host installation or model-server restart.

Real Pygments token objects did not receive their intended keyword/function/class/comment/builtin styles because token containment was treated like string containment. Normalizing the token name restores those styles. The byte guard previously counted Python characters and allowed a Unicode block above 512 KiB into the lexer; it now checks UTF-8 bytes. The valid initial 46-case group passed38 with eight actual failures; after correction the extended 50-case renderer group passes, including real Markdown/Python Unicode output and independent style modifiers. One pre-existing Markdown assertion now strips ANSI before checking visible print(1), and additionally checks the builtin underline; its visible-text contract is preserved. These are actual lexer/renderer tests, not native terminal screenshot acceptance.

Thirty-three tool-turn contracts use the actual normalized request/history and workflow helpers with a deterministic inference boundary that forbids real network/process execution. They verify bounded reads after file errors, refusal to repeat arbitrary failed shell commands, terminal workflow stops, project-root and deduplicated subdirectory scans, primary-file read gates, search/fetch planning and no refetch after completed sources. Three initial expectations were fixture errors (actual project root, resolved path prerequisite, authorization carried in the explicit search text), corrected without production changes. Real continuation cases exposed ValueError/TypeError for string/dict/list offsets in history; only the duplicate-request comparison now catches malformed numeric values, preserving a bounded next read and all normal offsets. No filesystem permission or tool policy is broadened. Before the continuation fix, two normal-offset cases passed and three malformed-offset cases failed.

Flint finished visible Igorek session chat-20261025-184750-00d04c in 502s. Its six methods / 31 subtests were independently run against the exact isolated functions and canonical module; helper/source and test-class AST equality is recorded. Parent review identified missing assertions for individual modifier independence and added three separate cases. The accepted five-file compatibility group passes 150 tests and 31 subtests. The original selected codexpro profile was restored after the helper completed; an empty restoration session is archived (DELETE is unsupported), the useful Flint chat is retained, and only the temporary paired client was revoked/deleted. No model server was restarted. Initial standalone invocation lacked the fixture import path; corrected PYTHONPATH execution passes.

Full/targeted reports, before-failure XML, helper review, source hashes and refreshed maps are retained in build/verification-coverage-goal90-checkpoint19. The accepted source changes require a future update and runtime/chat restart to take effect; owner applications remain unchanged.

## 2026-10-25 — Move settings to the left rail and simplify the topbar (unreleased)

## 2026-10-25 — Release 0.2.23 / Android 0.1.123 preparation

The release freezes the accepted coverage batches through checkpoint 18 and the verified settings-rail change. Python/Desktop are 0.2.23; Android is 0.1.123 (124). The accepted full Python baseline is 7,667 passed, 21 explicitly reported platform skips, 854 subtests; line coverage is 77.63890438815112%, branch coverage 67.49982738382931%. Release CI and package acceptance are recorded separately from this baseline. No additional coverage tranche or owner runtime restart is included.

The shared desktop/web UI settings button now sits immediately above updates in a bottom rail group. Both buttons are 30×30 CSS pixels with 20×20 SVGs; settings keeps its existing ID, labels, visibility and click handler. Removed the HTTPS/local and runtime-ready badges from the topbar and their unused CSS/DOM updates. Runtime state still controls the existing Stop button and chat indicators. No connection, task or approval policy changed.

Verified the actual page in isolated browser contexts at 1440×1000 and 520×900 with synthetic API responses: settings and updates open, settings appears once, buttons share dimensions and have a 10px gap, both header badges and the old header gear are absent, no page errors. Stop is visible for active state and hidden for idle state. Screenshots were visually inspected. Existing Desktop suite on bundled Node 24.19.0: 480 passed, zero failed/skipped; JavaScript syntax and whitespace checks pass. The shell npm command was unavailable and the default Node was 22; the equivalent package test command was run directly with the existing supported Node 24, without installing dependencies. Evidence: build/verification-settings-rail-20261025. No new tests were added for this small reversible UI relocation. This is source preview acceptance, not an installed Electron/Android acceptance. Versions, release and owner services remain unchanged; GitHub delivery is deferred. The Python coverage goal was explicitly paused by the user after accepted checkpoint18 at 77.6389%.

## 2026-10-25 — Managed app-server transport and session lifecycle (unreleased)

Overall Python lines: **77.2581% → 77.6389%** (+0.3808 percentage points); branches: 67.4998%. Both roots and 395 files: 7,688 collected, 7,667 passed, 21 existing Linux-run skips, 854 passing subtests, zero failures/errors. Added 86 tests: 42 adapter, 28 managed runtime, seven native process/locale, three real HTTP integrations and six reviewed Flint methods with 14 subtests. Source inventory: 870 frozen Python/lock files; exclusions remain 365. The 27 added executable statements implement transport/state cleanup and guards. Changed-line coverage is 64/66; exact remaining lines are recorded separately. The 90% goal remains unfinished. No GitHub delivery, version bump, release, installation or owner service restart.

Adapter tests exposed retained pending entries after timeout/send failure, non-object frames and malformed response identities disrupting the reader, failure to release callers on protocol EOF/close, unreaped killed children and a process left alive after a failed handshake. Requests now clean their registration in finally; protocol EOF and close fail remaining waiters. Readers retain the owned process reference and check generation before reporting closure. Invalid top-level messages are diagnosed without killing the loop; responses must match an emitted integer ID (booleans/floats/containers cannot consume another pending request). Failed startup closes its process; close waits after kill. Stderr is diagnostic: its closure does not fail requests whose valid response may still be buffered on stdout.

The initial adapter group passed 18/42. One new expectation was over-broad: failing waiters on stderr EOF could preempt valid buffered stdout. The corrected contract explicitly preserves that response; the valid baseline passed 19/42, with 23 remaining transport/state failures. The six actual owned-process scenarios passed three before correction and failed malformed-frame/abrupt-exit/close-during-wait cases. After correction all 42 adapter and six process cases pass. They use an owned Python JSON-line peer in place of only the Codex executable boundary; no actual Codex, model, command or user task is launched.

Managed-session tests use the actual ApprovalRuntimeBridge/SecurityStore on temporary SQLite. They cover profile/model/environment binding, notifications/diagnostics, user-decision persistence and native reply formatting, pending-request removal, event cursor/ring limits, unknown-method metadata, message failures, missing sessions and per-session close isolation. The initial 28-case run passed 26: numeric request ID zero was ignored by a truthiness check, and a database cleanup failure prevented adapter close. A None check preserves ID zero; try/finally closes the adapter even if pending-store cleanup raises. No approval decision policy or remember scope was broadened. Existing resolution ordering and unknown-method best-effort journal behavior were not redesigned.

Three native HTTP integrations exercise the real FactoryAPIHandler, actual managed runtime, actual temporary approval database and actual adapter against the owned peer. They create/read a session, send Unicode, observe a pending native request, explicitly reject it, verify persisted rejection and the wire decline, read event history, delete the session and verify its process exits. Refused RPC and abrupt process exit produce HTTP500 without inventing a successful answer; the temporary session can still be closed. These are actual loopback HTTP and process-pipe checks, not GUI, account, model or actual Codex API compatibility acceptance.

A seventh native scenario launches an ASCII-locale parent client against the exact canonical adapter. Before correction it raised UnicodeEncodeError; the transport now explicitly uses UTF-8 for both directions, and the same client succeeds with Cyrillic/emoji and the expected notification. The peer independently enforces UTF-8 for its standard streams and wire log, matching the JSON transport rather than the host test locale. The C-locale probe is POSIX-only; all seven run on Linux. This does not establish a Windows installation or full cross-platform runtime acceptance. Native/HTTP peer wire logs, process IDs/exit codes, pending-state and session-close receipts are preserved with the full run. The ASCII helper is stdlib-only; its protocol logic executes canonical transport source, not a copied implementation.

Flint authored six format-contract methods and finished the visible Igorek task in 369 seconds. Exact helper/formatter and test-class ASTs remain unchanged; parent integration uses a subclass calling the real adapter constructor and overriding only _send to collect replies. It covers file/command/permissions accept/decline scopes, requested-schema defaults, malformed/missing schemas and unsupported methods. Standalone and canonical execution pass with 14 subtests. No permission is actually granted and no action is executed. Only its temporary paired client was revoked/deleted; chat, current profile and owner model remain intact. The final six-file compatibility group passed 168 tests and 14 subtests, including 82 pre-existing Factory API checks.

Evidence, valid before/after results, corrected diagnostic-EOF expectation, native ASCII failure, real peer/HTTP receipts, source hashes, helper review and refreshed maps are in build/verification-coverage-goal90-checkpoint18. Fatal-error/unused-import, whitespace and publication checks pass. Existing Windows/KVM/vsock skips remain separate from passes. Owner Desktop/gateway/Flint, installed apps, VM images and published releases were not changed. Source changes need a future update/runtime and chat restart to take effect; no restart was performed during this work.

## 2026-10-25 — Clipboard transports and idle composer contracts (unreleased)

Overall Python lines: **76.7494% → 77.2581%** (+0.5086 percentage points); branches: 67.1294%. Both roots, 390 files: 7,602 collected, 7,581 passed, 21 existing Linux-run skips, 840 passing subtests, zero failures/errors. Added 155 tests: 88 clipboard backend cases, six reviewed Flint payload methods with two subtests, 36 idle prompt cases, 21 composer application cases and four omitted/null MCP-param compatibility cases. Exclusions remain 365. Four executable statements disappeared when redundant manual write/close operations were replaced by communicate(input=...) in the two faulty transports; no coverage scope/exclusion was changed. Changed-line coverage is 6/6. The 90% goal remains unfinished. GitHub delivery, versions, releases and installation remain deferred/unchanged.

The tmux/WSL transports manually closed stdin before communicate. Real owned Python subprocesses reproduced ValueError for both transports on Python 3.13.7; the same pipe succeeds on 3.14.4 because its stdlib tolerates a closed stdin. Both transports now let communicate own UTF-8 input and its lifecycle, return I/O failures as errors, and wait after killing a timed-out owned child. The same stdlib-only 3.13 probe against the exact canonical module now copies Unicode bytes exactly without exception. This is native pipe acceptance with only the clipboard command boundary replaced by a Python stdin-to-file process; it does not establish a real Windows/WSL, tmux, Wayland or X11 clipboard. The owner clipboard is never touched.

OSC52 fallback previously sent bytes to a text sys.stdout, raising TypeError when /dev/tty was unavailable (and on the Windows branch). It now selects stdout.buffer when available. Tests cover actual TextIOWrapper/BytesIO, tty-open/write failure and fallback, platform selection, backend ownership leases, native-module import/create/set failures, bounded pipe cleanup/errors, Linux backend priority, SSH/WSL routing and preserved diagnostics. Real external clipboard commands and terminal writes are forbidden by the test fixture. Native macOS stderr-descriptor operations are synthetic; their unit checks do not establish macOS acceptance.

The first clipboard group passed 79/88: two successful-copy tests over-constrained communicate's argument shape even though 3.14's existing behavior succeeded. Those fixtures now permit either correct delivery path. The corrected baseline passed 81/88, exposing seven I/O cleanup/binary-output failures; independent real 3.13 pipe probes established the compatibility defect separately. After correction all 88 pass, including owned real 3.14 pipes. A failed edit attempt did not apply the initial fixture adjustment; that unchanged intermediate run is retained but is not used as the corrected baseline.

Idle prompt tests exercise actual completion/validation/key-binding components with only the PromptSession constructor captured: slash exact/prefix ordering, bounded and cached file search, lazy/silent skill loading and recovery, Unicode input, newline/Tab/transcript shortcuts, help overlays, reasoning visibility and toolbar variants. The initial terminal-size mock replaced shared shutil behavior with an incompatible function, causing a pytest internal error and no coverage report. The fixture now uses ordinary COLUMNS/LINES. The next run passed 34/36; one failure was the Tab alias (the actual key is Control-I), corrected in the fixture. The valid baseline passed 35/36, exposing a real HTML fallback issue: replacing newlines with br tags loses them in prompt_toolkit's HTML parser. Keeping escaped literal newlines preserves the separate workspace line. All 36 pass without changing the normal ANSI route.

The 21 application-composer cases use real prompt_toolkit Buffer, Document, Layout and KeyBindings with a captured Application constructor. They verify trimmed submission, empty-input no-op, multiline entry, completion prefixes, transcript/cancel actions, help display, image/text paste and repeat filtering, reasoning toggles and mouse-notification/output fallback. The first run passed 13/21 because fixtures set buffer.text while leaving its cursor at zero, then incorrectly expected append-at-end behavior. Fixtures now assign Document(text), which places the cursor at the end as typed input would; all 21 pass without a production change for those failures. These are component/callback checks, not newly claimed native PTY or GUI acceptance. Existing native terminal/backend suites still run in the complete check.

Flint authored six pure payload methods and finished its visible Igorek task in 240 seconds. Exact source-function ASTs and all six test-class ASTs are unchanged. Parent integration adapts imports, corrects a byte-limit comment, and strengthens its FakeWriter to require bytes rather than silently accepting text. Standalone and canonical tests pass with two subtests. Its temporary paired client was revoked/deleted; the chat, current user profile and model process are retained. The final eight-file compatibility group passed 407 tests and five subtests. Four new MCP cases preserve prior omitted/null-param error codes and serve the next valid request, covering the normalization assignment left unexercised in checkpoint16.

Evidence, initial/valid before-after runs, real 3.13 source-hash probes, source inventory, helper review and maps are in build/verification-coverage-goal90-checkpoint17. Fatal-error/unused-import, whitespace and publication checks pass. Owner Desktop/gateway/Flint, clipboard contents, installed apps and VM images were not changed. No new native GUI/Windows/phone/model-inference or full Python-3.13-suite acceptance is claimed. CLI fixes are source-only for a future update; no owner restart was performed. Existing Windows/KVM/vsock skips remain separate from passes.

## 2026-10-25 — MCP recovery, skill resources and native desktop backend (unreleased)

Overall Python lines: **76.2309% → 76.7494%** (+0.5185 percentage points); branches: 66.5285%. Both roots, 386 files: 7,447 collected, 7,426 passed, 21 existing Linux-run skips, 838 passing subtests, zero failures/errors. Added 95 tests: 29 protocol-recovery cases, 54 skill storage/runtime cases, six reviewed Flint methods with eight subtests and six real backend-process scenarios. Exclusions remain 365; the denominator grew by 17 executable statements from narrow protocol guards. Changed-line coverage is 20/21 (95.24%); CoinGecko’s absent-params normalization assignment remains unexercised in this checkpoint and is a follow-up compatibility case. Artifact assembly initially assumed all changed lines were covered; this audit caught that reporting assumption and the record now states the actual missing line. The full test run itself was successful. The 90% goal is still unfinished. GitHub delivery, version bumps, releases and installation remain deferred/unchanged.

A non-object JSON request killed both bundled Tavily and CoinGecko stdio loops, preventing a subsequent valid request. Tavily's exception path itself accessed the invalid object; a later malformed JSON frame could also inherit the preceding request's ID. The original 21 regression cases all failed before correction. Both loops now return -32600 for a non-object request and -32602 for non-object tools/call params, preserve valid request IDs and continue reading. Tavily resets request_id before parsing each frame; existing malformed-JSON error codes remain compatible. Eight additional falsey-params cases bring the new suite to 29; it and the existing 49 stdio contracts pass together. Normal tools/catalog/provider behavior is retained. These tests use in-memory stdio and synthetic urllib, not live providers.

Skill storage tests cover root precedence, package/flat discovery and first-wins deduplication, persisted enable state, explicit installation overwrite, invalid names/write failures, frontmatter/legacy parsing, configuration/reload detection, link/copy resource replacement, task-local materialization, environment injection without values in prompt text, usage counts, summary filtering and existing database-outage behavior. Real temporary SQLite/files are used. The initial 50-case run passed 49: a legacy bold trigger became first trigger** because list-marker removal consumed its opening Markdown markers before bold unwrapping. The two parsing steps are reordered. All 54 new cases, including four bold-list variants, and the five existing selection/tool cases pass. Existing best-effort database-error semantics were exercised, not redesigned.

Flint authored six Skill resource methods through the visible Igorek chat and finished in 213 seconds. Its exact Skill-class AST remains unchanged in the fixture and canonical module. Parent review retained every method/assertion, adapted the import and four expected relative paths through str(Path(...)) for native separator portability, and verified the resulting class by an explicit AST transformation. Independent standalone and canonical execution pass with eight subtests. Its temporary paired client was revoked/deleted; the chat, current user profile and owner model process are retained. The final six-file MCP/skills compatibility group passed 143 tests and eight subtests.

The six native scenarios launch the actual canonical desktop/backend.py via the same Python interpreter with branch coverage, fresh HOME/data/config/cache, empty profiles/tasks and ephemeral loopback ports. Assertions prove ready/owner-reply framing, no active model worker or queued task, private runtime mode and certificate location. A real HTTPS client trusts only the test certificate, loads the web page, pairs using its own one-time code, receives a secure cookie, verifies paired state, logs out and verifies unpaired state. Pipe tests tolerate unmatched browser/OpenCode replies and invalid JSON, then serve the next valid status. Separate processes exit on stop, stdin EOF, SIGTERM and an oversized control frame. A genuinely unusable database directory produces only backend_start_failed and exit1. After every scenario the exact process exits, reader closes, HTTPS port is closed, canonical Python/lock hashes match and child branch coverage is verified before merging into the parent. No application UI is rendered and no installed owner process or model worker is used.

The first native run passed five of six; its startup-failure fixture incorrectly assumed that an unusable Vault directory is accessed at boot, but that storage is lazy. The fixture now blocks the database directory, which is actually required during startup; no production backend change or assertion weakening was made. The corrected native group passes all six, and all six pass again in the complete run with the frozen 859-file source inventory. Only verified same-source child data is merged; rejected/incomplete child runs are not counted. This is real POSIX backend/pipe/HTTPS acceptance, not native Electron GUI, Windows, physical phone, installation or model-inference acceptance.

Evidence, original failures, corrected fixtures, native child data/proofs, source hashes, helper review and maps are in build/verification-coverage-goal90-checkpoint16. Ruff fatal-error/unused-import, whitespace and publication checks pass. Installed apps, owner Desktop/gateway/Flint, releases and VM images were not changed. The fixes are source-only for a future update; no restart was performed during this work. Existing Windows/KVM/vsock skips remain separate from passes.

## 2026-10-25 — Summary clients and bundled service adapter contracts (unreleased)

Overall Python lines: **75.6901% → 76.2309%** (+0.5409 percentage points); branches: 65.9757%. Fresh complete run, both roots and 382 files: 7,352 collected, 7,331 passed, 21 existing Linux-run skips, 830 passing subtests, zero failures/errors. Added 120 tests (39 compression client, 26 host-service runner, 49 bundled MCP stdio and six reviewed Flint methods with 12 subtests). Production code, the 80,609-statement denominator and 365 excluded lines are unchanged. The 90% goal remains unfinished. No GitHub push, PR, CI dispatch, version bump, release or installation was performed.

Compression tests check explicit endpoint precedence over GGUF/OpenRouter/local routing, model/key selection, prompt trimming, empty-prompt no-op, HTTP payloads and timeouts for three transports, off-event-loop dispatch, HTTP/JSON/timeout failures, and local generator parameters/missing tokenizer/errors. They verify failure identity and that the client does not start another generation after a failed request. The existing inference fixture substitutes the model-engine dependency and network boundaries; no real inference, GPU or provider API was invoked. The helper's parsing cases test empty/missing choices, invalid message objects, strings, multipart text order, JSON-compatible content and the string fallback for a nonserializable value.

The host-service entrypoint is loaded from its canonical file. Tests use a temporary enrolled adapter/policy/control file, synthetic ASGI adapter/uvicorn/thread boundaries and a real temporary Unix socket. They verify the adapter hash before execution, explicit environment without inherited fixture secrets, cwd/import order, socket mode, bearer authorization before reading a body, POST-only routing, RPC allowlist/argument policy, chunk replay, disconnect/oversize denial and lifespan forwarding. A synthetic idle loop keeps starting/busy/error-state/in-flight services alive; successful and failed tool calls both release in-flight state so an actually idle service can exit. The test module is POSIX-only because this entrypoint uses Unix sockets; all 26 cases ran on Linux. This is entrypoint/ASGI contract acceptance, not an installed service, native systemd or real GPU adapter acceptance.

Bundled Tavily/CoinGecko tests run the actual stdio loops with in-memory input/output and synthetic urllib boundaries. They verify initialize/catalog/notification/error framing, flush-per-response, provider errors followed by another usable request, optional/provider key precedence, isolated dotenv precedence, output-directory argument filtering, bounded search/extract results, formatting/partial failures, URL encoding, market series/current defaults and timestamps, proxy policy and bounded HTTP details. Dotenv tests redirect only the module's fixture __file__ value to temporary data and restore environment changes; no real local dotenv or key is read. These tests do not establish live provider availability or installed registry enrollment.

Flint completed the visible Igorek task in 261 seconds. Its source-function AST is unchanged and its six test-method class AST is preserved; only import/fixture binding and formatting were adapted for the canonical client. Independent unittest and canonical pytest checks pass, including 12 subtests. Its temporary paired client was revoked/deleted; the chat, user's current profile and model process remain untouched. The final five-file compatibility group passed 146 tests and 12 subtests. Earlier focused groups passed 63 and 75 tests. A preliminary lint check found one unused test import, removed before running that group; no production change, failure suppression or new Linux skip was used.

Evidence, same-source hashes, full raw reports, targeted results, helper review/receipt and refreshed maps are in build/verification-coverage-goal90-checkpoint15. Fatal-error/unused-import, whitespace and publication checks pass. Existing Windows/KVM/vsock skips remain separate from passes. Installed apps, owner Desktop/gateway/Flint, published releases and VM images were not changed. Test-only changes need no application restart.

## 2026-10-25 — MCP approval recovery and Telegram queue checks (unreleased)

Overall Python lines: **75.5452% → 75.6901%** (+0.1448 percentage points); branches: 65.4920%. Both roots and 378 files: 7,232 collected, 7,211 passed, 21 existing Linux-run skips, 818 passing subtests, zero failures/errors. Added 73 tests: 31 Telegram queue/recovery cases, 36 real-executor MCP refresh cases and six reviewed Flint methods with ten subtests. Existing exclusions remain 365 and no skip was added. The 90% goal remains unfinished; all figures are from the fresh complete frozen-source run. GitHub delivery, versions, releases and installation remain deferred/unchanged.

A failed MCP enable could mark its user-input request submitted before the configuration step succeeded, leaving no pending request to retry. The Telegram handler now enables first and records an affirmative response only after that succeeds. Real SQLite tests preserve the original task, instruction, metadata and request ID through a failed attempt and successful retry; resuming clears stale timing/error state and queues the same task. Negative decisions preserve disabled server state and request cooperative cancellation; stale task callbacks do not enable or resolve anything. The existing Desktop-owner policy is now checked at the start of accept_mcp_enable_suggestion, before its global registry write, instead of raising only after that write in the profile-enable step. Denial leaves registry/profile bytes unchanged. This is early denial for an existing policy and request recovery, not a new cross-file/database transaction or a permission-scope redesign.

The first Telegram run passed 20/30. Six failures were fixture assumptions: get_task omits a null error field, task cancellation is cooperative (cancelling before worker acknowledgement), and the history writer does not accept workspace_task_id as a keyword. Fixtures now use the real shapes and persisted history rows. The valid baseline passed 26/30, exposing three lost-pending-request failures and one registry-write-before-denial failure. After the two narrow corrections all 30 passed; a further real retry regression brings the new file to 31 cases. The Telegram compatibility group passed 150 cases before adding that final retry case. No production behavior was changed to satisfy the mistaken fixture assumptions.

Queue tests verify stable workspace restoration from actual history, preserved provider/model overrides and attachments, waiting-approval state, missing gateway outcomes, transport failures, typing/progress cleanup, polling bootstrap/backoff recovery and update offsets. The owned typing thread recovers a synthetic send failure and terminates after stop. These tests use real temporary task/request SQLite and MCP configuration files. Telegram delivery, gateway execution and watchers are mocked; registered fixture server commands are never executed.

Executor tests exercise Anthropic, OpenAI Chat and Responses-compatible paths with synthetic model/tool responses. A successful tool refresh adds only known schemas, deduplicates names and updates the next request. Malformed/failed/empty refresh results do not invent tools. A genuine pending enable response stops before another model round and exposes only approved metadata fields. The initial run passed 30/36; three failures were a fixture expecting approval instead of the frozen approval_blocked_tools return shape. The corrected baseline passed 33/36: all three remaining failures showed mutation of the caller's enabled_tools list when a Vault provider was present. The executor now copies the list before refreshing it. All 36 cases pass while preserving default and Vault-enabled behavior. Caller data and the first serialized request remain unchanged. The final five-file executor/queue/parser compatibility group passed 143 cases and ten subtests.

Flint authored six parser methods through the visible Igorek chat for this task. The source-function AST matches its unchanged microfixture; the canonical test-class AST is preserved, adapting only the import/module docstring. Independent canonical execution passes all six methods and ten subtests. Wrong tool names, damaged/non-object JSON, wrong decisions, missing/blank identities and valid metadata are covered. Parent integration tests separately verify unexpected-field filtering. Only its temporary bridge client was revoked/deleted; the finished chat, user's current profile and model process are retained.

Evidence, corrected before/after results, source hashes, helper review, changed-line coverage and refreshed maps are in build/verification-coverage-goal90-checkpoint14. Fatal-error/unused-import, whitespace and publication checks pass. Model/Telegram transports are synthetic; no new native UI/device, GPU inference, live Telegram delivery or real MCP server acceptance is claimed. Installed apps, owner Desktop/gateway/Flint, VM images and published releases were not changed. Existing Windows/KVM/vsock skips remain separate from passes. A future installed update is needed to use these source changes.

## 2026-10-25 — Persistent native CLI cancellation and schedule confirmations (unreleased)

Overall Python lines: **75.3443% → 75.5452%** (+0.2009 percentage points); branches: 65.2903%. Both roots and 375 files: 7,159 collected, 7,138 passed, 21 existing Linux-run skips, 808 passing subtests, zero failures/errors. Added 28 cases: eight native POSIX PTY scenarios, six independently accepted Flint methods with eleven subtests, nine parent-owned message/schedule compatibility cases and five deterministic terminal-wait race regressions. Existing 365 excluded lines and all Linux-run skip conditions are unchanged. The 90% goal remains unfinished. Per the user's revised delivery instruction, no GitHub push, PR or CI dispatch is performed for this batch; releases/versions are separate and unchanged.

Real persistent-session acceptance exercises run_fullscreen, _fs_run_turn, _execute_user_turn and the actual active application. Two queued requests complete in order inside one Application; local /profile and /status are not sent to the task boundary and leave input raw. Escape cancels the active task and allows a recovery turn in the same app. Other scenarios cover inline mode with CPR responses, raw SGR wheel events, resize from 110x32 to 100x40, leaving fullscreen, Ctrl+C during execution, Ctrl+C before task ID creation and Ctrl+C in idle after a completed turn. Real temporary SQLite/history files retain both turns; source hashes and cleanup state are checked. Synthetic task snapshots/prompt/network/clipboard boundaries keep owner services and models out of the test. POSIX PTY guards honestly skip these eight new cases on non-POSIX hosts; all eight ran in this accepted Linux run.

The first six-case run passed four and failed two. One assertion incorrectly searched raw ANSI bytes for a contiguous answer string; plain terminal output and persisted history contained the correct answer, so the fixture now strips control sequences before checking content. The corrected six-case baseline passed five and failed Ctrl+C. Expanded eight-case acceptance passed six and failed both active and pre-ID Ctrl+C: the UI exited, but its task worker continued and persisted an ok answer without a cancellation request. The persistent session awaits that worker, so merely exiting its Application is insufficient. The Ctrl+C binding now cancels a known active task or records interrupt_pending until the task ID appears; idle and per-turn behavior remain intact.

The first after-run passed both cancellation scenarios and compatibility checks but reported an inline process-exit failure. An initial suspicion of a short teardown timeout was disproved when full12 failed two exit waits even with a twelve-second allowance. Both child reports recorded correct cancellation/history, only MainThread and completed UI cleanup. The actual cause was a race in the test wait: the predicate could observe a running process and the following poll observe exit, then break before rechecking the successful predicate. A deterministic reproduction using the original committed helper failed with observations [None, 0]. Both terminal harnesses now share a bounded waiter that rechecks the predicate on process exit. Five independent cases cover the exact race, a missing marker, immediate readiness, bounded waiting and a marker arriving at exit. The process-exit deadline was restored to the original three seconds; all eighteen focused cases (thirteen native plus five waiter regressions) pass. The failed full12 is preserved as rejected evidence and contributes no accepted coverage/map point. A new complete frozen-source run is the authority for these figures. Rich/session compatibility passed 160 cases. Child branch data are merged only after native assertions pass. This is native POSIX terminal acceptance, not Windows GUI, physical-phone or real model inference acceptance.

Flint wrote six schedule-confirmation methods in a second visible Igorek chat. They reject damaged JSON, non-object envelopes and non-created statuses, preserve explicit messages and verify scheduled/generic fallback confirmations. Its initial Unicode fixture expected an internal nonbreaking space to disappear; the helper corrected the input to test external whitespace, without modifying the production function. The final test-class AST is preserved on canonical import. The parent separately verifies internal spaces/tabs/newlines/Unicode preservation and blank-message fallback. All fifteen methods/cases and eleven subtests pass independently. Only the helper's temporary bridge client was revoked/deleted; its finished chat, the user's profile and model process were retained. No actual scheduled job is created by these tests.

Evidence, raw terminal transcripts, native parent/child receipts, source hashes, reviewed helper code and refreshed maps are stored in build/verification-coverage-goal90-checkpoint13. Fatal-error/unused-import, whitespace and publication checks pass. Changed executable-line coverage is 75.00%; missing changed lines are [847, 848] (best-effort cancellation exception handling is not claimed covered). Installed apps, published releases, owner Desktop/gateway/Flint and VM images remain unchanged. CLI source changes take effect when a newly updated CLI chat starts; no running session was restarted. Test harness changes address a proven exit-observation race, not a runtime timeout. The earlier Excel/source fix and user-service Laya recovery remain accepted separately.

## 2026-10-25 — Spreadsheet change accounting and approval lifecycle coverage (unreleased)

Overall Python lines: **75.2374% → 75.3443%** (+0.1069 percentage points); branches: 65.0712%. Full isolated verification across both roots and 372 files: 7,131 collected, 7,110 passed, 21 existing Linux-run skips, 797 passing subtests, zero failures/errors. Added 89 Python cases and 57 subtests (43 spreadsheet integration cases, 40 Telegram approval/lifecycle cases and six reviewed Flint methods), plus six Android unit cases. No coverage exclusion or skip was added; 365 excluded lines remain. The 90% objective is unfinished; the user explicitly resumed test work after requesting this fix.

The reported changed-files dialog showed workbook serialization churn as thousands of source-line additions/deletions. Known spreadsheet formats now have a non-text label and contribute no lines to session summaries, header chips or review totals. Historical persisted counters are normalized on read; files and their added/modified/deleted states remain visible. CSV/TSV, scripts, Markdown, SVG and compound names ending in a text extension retain real line diffs. A narrow shared backend classifier also keeps existing media formats out of source-line statistics. New tracker records avoid decoding the current non-text file for line counting; the existing checkpoint baseline interface is unchanged. Git review/direct mobile diff endpoints return unavailable rather than serialized-data hunks. Existing path authorization, recorded-file matching and checkpoint validation still precede direct mobile returns. Renames involving a workbook are also non-text comparisons.

The initial focused regression run passed six text-compatibility cases and failed 27 workbook cases out of 33. After correction the compatibility group passed 191 cases, and the final expanded workbook/history group passed 110. Tests use real temporary ZIP archives, checkpoint repositories, Git rename/add/delete operations, legacy records and the actual mobile service methods with synthetic session metadata. They verify that archived bytes/baselines are not read as source text. The six-file example now totals +4/−0; both workbooks remain listed. Android passed all 464 unit tests and lint with zero errors (39 warnings, two hints). Presentation tests cover the screenshot's mixed totals, workbook-only summaries, unchanged text formats and review counters. No new APK, native UI or physical-phone acceptance is claimed; installed clients still require a future update to receive this source change.

Forty additional Telegram cases cover malformed/unauthorized callbacks, exact task/action routing, acknowledgement failures without duplicate decisions, explicit runtime/queued approval selection, state chaining, rejection/session-scope flags, all five terminal task statuses and trace tool-event deduplication. No Telegram messages or real gateway task/provider requests are sent by these tests. The three-file compatibility set passed 120 cases. These tests expand a previously weakly covered live module rather than exercising unused exports for a larger numerator.

Flint authored six unittest methods with 57 subtests in a separate visible Igorek chat. The source microfixture matches the canonical classifier AST; the test-class AST is preserved, with only the canonical import/module docstring adapted. Independent execution passed all six methods and 57 subtests. The helper's finished chat is retained and only its temporary bridge client was revoked/deleted; the user's profile and running model were preserved. The classifier tests perform no filesystem/network access.

A publication-check false positive on a synthetic home directory was replaced with the documented generic example path; all publication contracts now pass. Source hashes were frozen for the complete measurement. Evidence, Android XML/lint reports, helper acceptance and updated map aliases are retained in build/verification-coverage-goal90-checkpoint11. Existing Windows/KVM/vsock skips are not passes. No Python/desktop/Android version, published release, installed app or owner Desktop/gateway/Flint process was changed. Source delivery to GitHub is independent of release publication.

## 2026-10-23 — Local-link rendering and CLI startup/completion contracts (unreleased)

Overall Python line coverage increased **74.9888% → 75.2374%** (+0.2485 percentage points); branches are 64.9471%. Both test roots and 369 files: 7,042 collected, 7,021 passed, 21 pre-existing Linux-run skips, 740 passing subtests, zero failures/errors. This final tranche adds 85 tests: 34 update-cache/startup contracts, 34 local-link/rendering contracts and 17 real prompt-toolkit completion contracts. The 90% objective is unfinished and work is paused at the user's request after this batch.

The corrected before-run passed 57/82 and failed 25 cases. The initial fixture had three calls to a nonexistent ANSI-strip helper and expected a trailing path separator to disappear without a product requirement; those fixture mistakes were corrected before accepting the baseline. Lexical path tests use PurePosixPath to keep fixtures portable, rather than relying on the runner's native path syntax. Seven remaining rendering failures showed directory-prefix collisions and percent-encoded file URLs. Shortening now requires a directory boundary, so /project-other/file is retained when cwd is /project. File URL paths are decoded once, preserving invalid UTF-8 escapes; extra cases verify invalid escapes and literal percent sequences. Relative links, line/column ranges, Unicode, localhost/UNC/drive paths, narrow wrapping and visible Markdown labels remain covered.

Twelve startup-check failures showed non-object cached JSON, malformed PyPI info values and cache-directory errors escaping into chat startup. Optional metadata/cache failures now fall back to the provider path without aborting startup; creating the cache path is inside the existing OSError boundary. Cached positive/negative results still avoid network calls, expired/broken cache refreshes, equal/older releases remain negative, unknown installed versions avoid network, GitHub fallback retains its install hint, and total provider failure does not write a misleading negative result. Actual temporary files are used, including a cache directory replaced by an unrelated file; provider results are synthetic and no network update/install runs.

Six completer failures showed that a trigger on the first line could generate a replacement for a subsequent composer line, including a trailing newline. Completion now stops once the cursor has crossed a newline. Real Document and Completion objects verify case-insensitive slash/file/skill matching, ordinary prose, cached file enumeration and preservation of text after a first-line cursor. File/skill inventory boundaries are synthetic; no native terminal acceptance is claimed for these cases. The initial compatibility group passed 281 tests; the final full run includes three additional cache/URL compatibility cases.

Source hashes were frozen for the complete run; coverage exclusions remain 365 and no skip was added. Raw before/after evidence, final source/test hashes, changed-line coverage, environment and refreshed map aliases are in build/verification-coverage-goal90-checkpoint10. Existing Windows/KVM/vsock skips are not passes. No release/version bump, installation, new helper-agent task or owner Desktop/gateway/Flint restart occurred. The next batch requires a new instruction from the user.

## 2026-10-23 — Native task lifecycle and provider stream/terminal responses (unreleased)

Overall Python line coverage increased **74.6682% → 74.9888%** (+0.3207 percentage points); branch coverage is 64.5940%. The active 90% line goal is unfinished. The final official isolated run includes both roots and 366 files: 6,957 collected, 6,936 passed, 21 existing Linux-run skips, 740 passing subtests, zero failures/errors. Added 58 tests (19 wire/SSE, five native PTY, 29 terminal-response adapters and five reviewed Flint methods) plus thirteen passing Flint subtests. Source measurement retains the existing 365 excluded lines. No unused production path was tested merely to increase the numerator.

The SSE before-run passed 12/19. Three failures showed that multiple data fields in one event were reduced to only the last line, losing valid JSON text/tool arguments at an event boundary or EOF. Data fields are now joined with newlines and their one optional leading space is handled consistently. Four failures showed that explicit provider errors, including an empty error object, could be ignored and a partial answer/tool call returned as success. Those events now return a redacted STREAM.INTERRUPTED failure at sse_event, retaining only approved provider/request identifiers. Parsing stops on that failure and no partial choices are returned. Real loopback HTTP tests preserve DONE spellings, comments, Unicode, indexed argument fragments, usage-only events, request/query immutability and HTTP error correlation/retry metadata. A real socket test waits until HTTPResponse.readline has begun before cancelling, verifying monitor shutdown wakes first-token waiting. Existing stream/cancel/usage compatibility passed 30 cases.

Five real POSIX PTY scenarios exercise the current ChatSession task path with synthetic task records: queued follow-up input, fullscreen SGR wheel/click events and resize, Ctrl+C, Esc and an external cancelled status. The initial terminal fixture incorrectly required a cursor query in fullscreen; fullscreen owns row zero and needs no query. Corrected acceptance answers CPR when requested and requires it for inline modes. The final before-run failed all five cases: four actual reports recorded exit on an already-finished Application, and externally cancelled tasks did not terminate. ChatSession now recognizes cancelled as terminal and calls exit only while the Application is running. Raw keys, initial 110x32 geometry, inline CPR responses, SGR dispatch/scroll offsets, resize to 100x40 and terminal process completion are verified. Clipboard/network/process boundaries inside the child are isolated; no worker/model or owner clipboard action is used. The controller compatibility group passed 165 cases.

Each new native child runs branch coverage against the canonical factory/desktop source, records source hashes and writes its own data artifact. Its data are merged into the active per-file coverage collector through the public CoverageData API only after successful native assertions; the full source inventory remains frozen for the entire run. This records actual child execution rather than treating the parent's import-only coverage as terminal acceptance. No coverage from another source revision or installed wheel is merged. Parent/child reports and raw terminal transcripts are retained. The five native cases are POSIX-specific and are conditionally skipped on non-POSIX hosts; they all ran in the accepted Linux run. This does not establish Windows or physical GUI acceptance.

Terminal-response tests passed 13/29 before correction. Invalid response/content/usage types could raise, numeric text could become a fabricated postamble, invalid token counters could contaminate totals, and error logging could echo credential assignments. A shared narrow reader accepts string text blocks and nonnegative integer counters, rejecting bool. Existing totals survive malformed/missing usage. Successful artifact helpers retain their factual fallback text when the postamble is unusable. A non-object/malformed synthesis envelope is an explicit PROTOCOL.INVALID_RESPONSE failure; a valid but empty synthesis remains synthesis_empty_after_stuck. Errors use the shared redactor. Native Anthropic, OpenAI Chat and Responses payload conventions, unchanged message history, thinking/non-thinking passback and no-tools final calls remain covered. A mistaken fixture initially expected reasoning_content on a non-thinking history; corrected cases preserve the existing conditional passback contract. The five-file executor compatibility set passed 65 tests.

Flint wrote five context-size validator methods against an unchanged source microfixture through the visible Igorek bridge chat. Independent canonical execution reproduced four failing subcases: bool accepted as a size, a fractional value returning zero, non-finite conversion raising, and a Unicode digit string that isdigit accepted but int rejected. The validator now returns only positive integers or None, while retaining valid numeric/string handling and positive-float truncation. The five canonical methods and thirteen subtests pass. Only the import and English docstrings were adapted; the assertion AST matches Flint's original. The helper's own temporary client was removed, the previous default profile conditionally restored, and its finished chat retained. No model server restart occurred.

Raw before/after receipts, native artifacts, hash proofs, the locked test environment and refreshed maps are retained under build/verification-coverage-goal90-checkpoint9. Fatal-error/unused-import and whitespace checks pass. No additional check was skipped in this Linux run, and the 21 old Windows/KVM/vsock skips are not passes. Installed applications and the published release remain unchanged. No version bump, publication, host installation, owner Desktop/gateway/model restart, real inference/GPU benchmark or new native Windows acceptance occurred. Work continues toward 90%.

## 2026-10-23 — Inference context, terminal decisions and CLI commands (unreleased)

Overall Python line coverage increased **74.0121% → 74.6682%** (+0.6561 percentage points); branches are 64.1558%. The 90% line goal remains active. The final official isolated run covers both roots and 362 files: 6,899 collected, 6,878 passed, 21 existing platform/environment skips, 727 passing subtests and zero failures/errors. Added 143 cases: 37 prompt/evidence contracts, 37 terminal/recovery decisions, 23 runtime-policy cases and 46 CLI command cases. The existing 365 excluded lines are unchanged. No source was removed from measurement, and unused helper functions were not exercised merely to increase the numerator.

The corrected prompt-context before-run passed 27/29 cases. Two regressions showed that Edit context matched only a basename, mixing the latest Read contents or errors from another directory's main.py. Focus now compares normalized complete paths while retaining tool_use_id association. A separate temporary diagnostic reproduced three missing-evidence cases for relative historical paths, including a later cd. The final four canonical history cases also cover failed cd. Relative tool targets now use the working directory inferred from the history at that tool-use boundary; absolute paths retain their normal handling. Actual temporary files and the real model-input builder verify correct evidence, aliases, bounded tails, unknown-ID rejection, Unicode/empty arguments, condensed directory/session evidence and bounded recovery. File operations are proposed, not executed by the inference server tests.

Terminal-decision tests initially passed 21/26 with a one-token request limit. Five failures exposed completed fixed responses reported as max_tokens in JSON while equivalent SSE responses ended with end_turn. Seven deterministic text-response branches now set end_turn explicitly: suggestions, completed command/write results, recovery stops, blocked implementation and policy denial. Generated responses keep their actual token-limit reason; separate JSON/SSE controls verify max_tokens remains available. ASGI HTTP checks exercise the completed Bash error directly, with no model call. Retry tests preserve schema/policy validation, exactly bounded generation attempts, valid empty replacements, and Read fallback after failed Edit recovery. Runtime policy matrices preserve permitted tools and explicit web authorization alongside local/explore/background restrictions.

The corrected CLI before-run passed 41/46. Fork Exit was handled as cancellation when its selected row was None. The Exit branch now runs first. Session model selection called a keyword-only helper positionally and could fail after partially changing state; named arguments now match its real signature, and the selected model is synchronized to the widget. Tests cover selection, reset, recommendation cancellation and incompatible models. Actual temporary settings/history exercise resets without losing unrelated fields, summaries, temporary history forks and profile/model state. Picker/setup dialogs, catalogs, skill activation and smarthelp are synthetic boundaries; no real helper task or external request is launched by these controller tests. The four-file CLI compatibility set passed 215 cases. This is controller/source acceptance, not new native PTY, GUI or physical-device acceptance.

Flint reviewed five frozen Edit-context scenarios through the existing Igorek bridge in a visible helper chat. Four case assessments were accepted. Its recommendation to associate an unknown tool_result ID with a file was explicitly rejected and not implemented; the existing negative contract retains the identity requirement. The relative-path concern was independently reproduced before its report and expanded to historical-cwd regressions. The helper chat is retained; its own temporary client was removed and the prior default profile conditionally restored. This was review assistance, not an attribution of the new test implementation to Flint.

Fixture/tooling errors are distinguished from product defects: API state construction needs the initialized lifespan; Rich output tests use controlled width and existing field labels; one mistyped targeted test path was rejected before execution. The temporary outside-repository diagnostic used direct pytest after the official runner correctly rejected its location; all final cases are canonical and included in the full official run. Source/test/lock hashes, raw before/after receipts, environment and refreshed maps are retained under build/verification-coverage-goal90-checkpoint8. Fatal-error/unused-import and whitespace checks pass. No coverage exclusions or skips were added. The 21 old skips remain six Windows and fifteen external KVM/vsock checks, not passes. No version bump, publication, host installation, owner Desktop/gateway/Flint restart, real inference/GPU benchmark or new native Windows acceptance occurred.

## 2026-10-23 — CLI stack lifecycle and worker escalation coverage (unreleased)

Overall Python line coverage increased **73.5830% → 74.0121%** (+0.4291 percentage points); branch coverage is 63.2697%. The 90% line target remains active. The full official isolated run includes both roots and 358 files: 6,756 collected, 6,735 passed, 21 existing platform/environment skips, 727 passing subtests, zero failures/errors. This tranche adds 67 cases (34 CLI stack, 28 worker escalation/telemetry/rollout, five reviewed Flint env-file cases), with no new coverage exclusions or skips. More tests do not automatically increase line coverage: several contracts exercise new failure conditions on already-covered lines. Whole-suite results are reported separately from targeted results.

The corrected before-run for the CLI stack passed 18/29 tests and reproduced eleven failures. Explicit environment values (including empty strings) remain authoritative; env-file values now override built-in defaults, with later files winning. An explicit gateway --data-dir sets both data-root aliases before env-file discovery. Invalid optional embeddings configuration no longer aborts startup, and quoted extra arguments remain one argv element. Windows literal backslashes and POSIX escaped spaces are preserved by platform-specific lexical escaping. Active profiles are validated before any child starts. Owned children are registered before reader startup and cleaned in a finally boundary when later startup fails. Timeout cleanup kills and waits again to reap the child; repeated shutdown signals do not interrupt cleanup. Existing model endpoints are reused without being registered for termination. The compatibility set passed 103 cases before four additional rollout cases were added; the final worker file passed 28/28. The Windows-path regression passed 31/32 before preserving literal backslashes; all 79 targeted compatibility cases passed afterward, including owned optional-child shutdown and continued API/worker operation after its failure.

Worker tests exercise actual escalation result parsing from the existing sanitized JSONL fixture, command construction, timeout/launch failures, task metadata, usage/tool/security propagation, stdout trace callbacks and periodic progress without launching a provider. Telemetry distinguishes unrelated events, configured server signals, tool calls and failures. Actual temporary rollout files exercise offsets, duplicate suppression, old/unreadable records and the final tail drain through a deterministic thread/event boundary; this is not native subprocess/concurrency acceptance. Project instruction tests preserve AGENTS.md precedence and CLAUDE.md fallback. Process/HTTP/security execution boundaries are synthetic; no owner credentials or services are used.

Flint completed its five-case assignment through the existing Igorek bridge in a visible helper chat. The microfixture's _load_env_file AST was verified against canonical source before accepting the tests, then all five were independently run against the real function. Only the import was adapted; no production function was replaced. Its temporary client was removed, its finished helper chat retained, and no task remained queued/processing in that session. Fixture mistakes are distinguished from product defects: the env snapshot prevents cross-test leakage, urllib's actual HTTPError behavior is used, and rollout path/kind expectations match the existing trace contract.

Immutable source/test/lock hashes, corrected before/after receipts, raw full coverage and refreshed maps are retained under build/verification-coverage-goal90-checkpoint6. Fatal-error/unused-import and whitespace checks pass. The existing 365 excluded lines and 21 skips (six Windows and fifteen external KVM/vsock checks) remain unchanged; skips are not passes. This is source-only work for a future release. No version bump, release publication, owner installation, Desktop/gateway/Flint restart, model benchmark or new native Windows/physical-device acceptance occurred.

## 2026-10-23 — ASGI inference API, tool-emission and failure-boundary coverage (unreleased)

Overall Python lines progressed **71.6254% → 73.5721% → 73.5830%**; branches progressed 60.8730% → 62.7743% → 62.7915%. The active 90% line target is not complete. The latest full official isolated run includes both roots and 355 files: 6,689 collected, 6,668 passed, 21 existing platform/environment skips and 727 passing subtests, with zero failures/errors. This tranche adds 116 cases without new skips or coverage exclusions. Coverage retains all configured source and the existing 365 excluded lines. Raw reports are separate snapshots, not averages of module percentages.

Development dependencies now lock FastAPI, Uvicorn, Requests and HTTPX2. Tests exercise the actual FastAPI/Starlette application through its in-process ASGI TestClient: middleware, Pydantic validation, exception responses, lifespan, OpenAI/Anthropic routes, token-count envelopes, stop boundaries, SSE events, tool-response serialization, multimodal payload forwarding and workflow transitions. Only the optional model SDK/data types, tokenizer/engine and CUDA availability boundary are simulated; framework/request handling is real. Network/process guards reject external side effects. This does not establish real inference/tokenizer accuracy, GPU performance, native TCP/HTTP2 framing, or concurrent transport cancellation. The locked environment was synchronized only in the isolated test venv; owner applications and model services were unchanged.

Corrected original-trigger authentication/lifespan tests passed 38/41 before three fixes: internal errors lacked the x-request-id response header; an upstream exception could echo a credential assignment in a 502 message; context-compressor/configuration failure during startup left an already-created generator unclosed. The 500 handler now includes correlation, generation failure text uses the shared redactor, and compressor setup is inside the generator's existing finally/close boundary. Opaque metadata.user_id strings containing JSON numbers, booleans, null, arrays or quoted strings exposed six failures among 17 state cases. Non-object JSON is now ignored rather than treated as a session-object mapping; valid session IDs, root reuse and explicit root replacement remain covered.

Three original-trigger failures among 34 corrected workflow cases exposed a successful Write result hidden behind the structural tool_result marker. The marker is stripped only after error detection, so error results and multiple calls are not shortcut to success. Planner completion is still consulted, preserving multi-step tasks. Streaming tests passed 13/15 before reproducing a credential assignment in a RuntimeError and hidden reminder content emitted before its closing tag arrived. The error uses shared safe_text, and the existing reminder filter withholds a recognized open block. Complete hidden blocks, ordinary Unicode/code, stop sequences across chunks, supported textual tool syntax and opaque image data all remain covered. No new salvage parser or model-specific text heuristic was introduced.

The initial tool-emission set passed only one of seven cases. A forced relative-parent Read bypassed the normal project-root policy; valid empty Write content was regenerated, valid empty Edit replacement triggered unnecessary generation, invalid content types/missing Edit fields could be emitted, and failed content generation could leave Write without its required content. File arguments are now completed only when the existing schema checks say they are incomplete/invalid. Supported required/type checks and filesystem policy run again at the common emission boundary, including forced tools, before any response or preamble is returned. Invalid generated input produces a correlated 502; denied policy produces a correlated 403. Explicit absolute overrides and complete Write/Edit/MultiEdit/Update arguments remain supported; all nine emission cases pass. This uses the existing validator's supported subset, not a claim of complete JSON Schema conformance. No proposed file operation is executed by these API tests.

Fixture corrections are kept separate from product defects: transitive fixtures are explicitly registered; the working-directory header uses the actual Primary working directory contract; active workflows may deliberately ask the model to continue after Bash; supported XML tool syntax is used instead of inventing another dialect. An unused duplicate parameterization was removed before final acceptance. Before/after/compatibility logs, immutable source/test/lock hashes, environment versions and the refreshed coverage map are retained. Executable-error/unused-import and whitespace checks pass. This remains source-only work for a future release: no source version bump, release publication, owner installation, Desktop/gateway/model restart or new native Windows/device acceptance. The 21 old skips remain six Windows and fifteen external KVM/vsock checks, not passes. Work continues toward 90%.

## 2026-10-23 — First checkpoints toward 90% overall Python line coverage (unreleased)

The active target is 90% overall Python line coverage, following the general [Google guidance](https://testing.googleblog.com/2020/08/code-coverage-best-practices.html?hl=pt_PT). Coverage is used to locate untested behavior; the percentage alone is not a guarantee of test quality. Lines progressed **70.0961% → 71.2180% → 71.6254%**; branches progressed 59.1532% → 60.4237% → 60.8730%. The target remains unfinished. The current complete report covers 57,642/80,477 statements and 17,585/28,888 branches. The existing 365 excluded lines remain unchanged.

The complete official isolated run includes both roots and 349 files: 6,573 collected, 6,552 passed, 21 existing platform/environment skips, 727 passing subtests, zero failures/errors. This series adds 270 cases and 38 subtests. Temporary SQLite, local files and actual request/payload encoders exercise gateway/session recovery, approval selection, message routing, deferred-job command packets, media persistence, source ranges and URL-preserving wrapping, worker orchestration and result persistence. Process/provider/Vault/Telegram boundaries are synthetic; no real model execution, credential use, message delivery or schedule execution occurs. Eight supplementary helper contracts were reviewed and independently run; one Unicode marker fixture was tightened to test the character rule independently of punctuation.

Corrected original-trigger runs distinguish real failures from fixture setup mistakes. Gateway preserved neither trailing nor leading delta whitespace (37 passes/one failure before correction). Telegram produced seven failures among 68 cases: invalid stored event cursors could crash session loading, queued approval index zero/negative could select another request, identical pending/workspace task IDs duplicated approvals, and same-name media saved within one second overwrote each other. Explicit bounds, cursor validation, task deduplication and unique temporary filenames now cover those cases; the five-file Telegram compatibility set passed 122 tests. Thirteen of 28 wrapping cases originally failed on unsupported string methods, calling an option string as a method, double-counted indentation, ranges extending past source text, lost spacing offsets and hyphenated URLs being split despite the no-hyphen option. The post-fix compatibility set passed 102 tests. These are deterministic format/state invariants, not model-specific handling.

Worker tests reproduced loss of numeric total_tokens metadata in five result states. The shared redactor classified every field containing token as a credential. Twenty-four of 95 direct redaction cases reproduced this independently. An exact allowlist now retains only known nonnegative integer token counters; bools, strings, arbitrary values and unknown credential-shaped fields remain redacted. Nested usage preserves counters without exposing api_key/access_token. The worker/redaction/Vault compatibility set passed 138 tests. Original text/secret patterns and approval-signature compatibility tests remain unchanged. Initial fixture corrections (SQLite parent lookup, creating test directories and guarding the actual urllib module) are recorded separately and are not production defects.

Complete source/test hashes, separate before/after receipts, raw coverage and refreshed maps are retained. No skips or coverage exclusions were added, and production files were not removed to reduce the denominator. This remains source-only work for a future release: no version bump, artifact publication, installation, Desktop/gateway/model restart or new native UI/Windows/physical-device acceptance. The 21 existing skips remain six Windows and fifteen external KVM/vsock checks; they are not passes. Work continues toward 90%.

## 2026-10-23 — Overall Python line-coverage goal (unreleased)

The target is overall Python **line** coverage across both test roots and the configured factory/desktop Python source inventory. Branch coverage is recorded separately; coverage.py's mixed line/branch display is not the target. The complete isolated runs show 63.3010% before this series, 66.2462% at checkpoint 1, 68.3377% at checkpoint 2 and **70.0961%** at final acceptance. The final report covers 56,407/80,471 statements and 17,087/28,886 branches (59.1532%). No test/production files were omitted to change the denominator, and the existing 365 excluded lines were not increased.

The official runner executed both roots and all 343 files in fresh processes with temporary homes/data: 6,303 collected, 6,282 passed, 21 existing platform/environment skips, 689 passing subtests, zero failures/errors. This adds 572 cases and 2 subtests, including four independently reviewed persona-rotation tests. All production and final new-test source hashes are verified against the frozen run. Raw complete runs, before/after reproductions, lint results and the coverage map are preserved as separate verification artifacts; percentages are not averages of module percentages.

Coverage now exercises social feed/draft persistence, persona selection, approval and rate limits; real loopback Factory API requests with isolated SQLite; CLI initialization, profile preservation, argument dispatch and MCP persistence; HTTP/process generation adapters; chat history, approval/resume and task finalization; clipboard/path and terminal byte protocols. External HTTP/provider/process/clipboard boundaries use synthetic responses and handles. The optional inference-engine types and requests interface are data-only test doubles, not installed or executed engine integrations. No real model generation, paid image/API request, message delivery, clipboard capture or owner service is invoked. Real HTTP/SQLite persistence is distinguished from mocked provider boundaries.

Original-trigger failures led to narrow correctness fixes: explicit zero feed counters are retained; catalog sorting does not mutate the cache; zero image dimensions are rejected consistently and malformed JSON-RPC input cannot inherit another request ID; pager defaults use the actual key primitives and follow-bottom logic records the viewport height; cleanup preview does not delete orphan directories and child task events are removed with child rows; scrollback emits bytes and flattened wrapped lines, with valid SGR color selectors; terminal links use valid character filtering and retain complete ranges after wrapping; widget initialization preserves model/branch/cwd; pasted paths use shell tokenization, decode file-URI escapes and reject unmatched quotes, and image capture calls Pillow's clipboard API. Tests preserve existing approval/root/credential policies. These are generic contracts, not model-specific runtime rules.

The before/after records also retain development fixture corrections: CLI polling/min-score arguments are integers, post drafting requires a submolt, draft editing uses --text, task lists use the documented tasks envelope, social generation limits returned candidates rather than model invocations, legacy history without metadata remains supported, and redundant DIM packets are not treated as a semantic failure. These fixture mistakes are not counted as production defects. The optional requests package was absent from the locked test environment; the adapter suite isolates that external interface instead of installing unlocked dependencies or skipping the cases.

Executable-error checks on changed production files, executable-error/unused-import checks on all new tests and diff whitespace pass. All changes remain source-only for a future release: no version bump, installer rebuild, owner installation, Desktop/gateway/model restart or public release. Earlier Android/Desktop/Windows native acceptance is historical; this run does not establish new physical-device, Windows, clipboard, GPU, or native PTY acceptance. The 21 existing skips remain six Windows and fifteen external KVM/vsock cases, not passes.

## 2026-10-23 — API text contracts and Read summaries (unreleased)

Two original-trigger regressions failed before the implementation: an empty Bash command raised IndexError while extracting its target, and a fenced code block using CRLF was not recognized. Empty input now returns no target and code fences accept both LF and CRLF. Existing filename/URL filters, instruction-file protection, supported schema aliases and bounded summary behavior are unchanged.

New behavioral cases cover file hints versus domains/JavaScript properties/numeric versions, token boundaries, code fences, explicit creation/replacement input, bounded Bash target extraction, Read line prefixes, imports/constants/route and main entrypoints, and bounded section sampling. Four additional section-summary contracts cover numbered Python definitions, deduplication/order, non-code text and the 12-item cap. All tests call real helpers; no referenced code, command, network request or model is executed by these contract tests.

The initial contract run collected 83 cases: 81 passed and the two original-trigger cases failed, with no errors/skips. The post-fix three-file compatibility set passed 198 cases. The official full run covered both roots and 329 files: 5,727 collected, 5,706 passed, 21 existing platform/environment skips, 687 passing subtests, zero failures/errors. The new section-summary file, added after discovery, passed separately against the same production source (four cases). Its final Unicode-escape representation was reviewed and independently rechecked with all four contracts retained. The accepted report counts each of 330 files once: 5,731 collected, 5,710 passed, 21 existing skips, 687 subtests, zero failures/errors. Raw coverage was combined after verifying unchanged production-source hashes, without averaging percentages. This adds 87 cases without new skips or coverage exclusions.

Lines rose from 50,697/80,459 (63.0097%) to 50,932/80,460 (63.3010%); branches from 15,037/28,884 (52.0600%) to 15,162/28,884 (52.4927%). Text helpers reached 235/243 lines (96.7078%) and 125/136 branches (91.9118%), from zero execution in the prior report. Executable-error/unused-import checks and diff whitespace passed. EN/RU Wiki and the coverage map are updated, with raw full/targeted/before receipts retained. This remains source-only work for a future release; no version bump, installation or owner service restart. Android/Desktop code is unchanged and earlier platform acceptance remains historical; fresh Windows CI/native-device acceptance is not claimed.

## 2026-10-23 — Skill mutation recovery and weak-area coverage (unreleased)

Eight original-trigger regressions failed before the implementation: creation adopted an occupied, unindexed directory and could remove its contents after rejection; scanner exceptions left five kinds of unaccepted mutation in place; a failed initial atomic write left its new package directory behind. Creation now exclusively claims a new package directory, refuses occupied paths and cleans up only its own failed creation. Scanner exceptions become rejected changes and use the existing rollback path. Static rejection, external-directory read-only rules and file boundaries remain enforced. Recovery still depends on writable storage; no durability guarantee for an unrecoverable rollback failure is introduced.

New tests exercise real temporary packages, supporting files, symlinks and SQLite, including metadata/categories/backend and disabled-state filtering, progressive reading and truncation, valid edits/patches/deletes, error reasons, read-only roots and rollback on injected scanner/storage failures. Two additional real static-scan rejection cases preserve existing files and reject creation. Scan decisions are injected only where needed to reproduce failure boundaries; the mutation functions are exercised directly. Supporting text and synthetic shell-like skill content are never executed. Five text-replacement cases cover uniqueness, global replacement, deletion, not-found and Unicode.

The accepted before-fix run collected 81 cases: 73 passed and eight failed, without errors/skips. The post-fix five-file compatibility set passed 90 cases. The final mutation contract file passed all 83 cases, including two real static-rejection additions; five independent text-replacement cases also passed. The complete official run covered both roots and all 328 files, including both new files: 5,644 collected, 5,623 passed, 21 existing platform/environment skips, 687 passing subtests, zero failures/errors. This adds 88 cases without new skips or coverage exclusions.

Lines rose from 50,249/80,451 (62.4591%) to 50,697/80,459 (63.0097%); branches from 14,809/28,884 (51.2706%) to 15,037/28,884 (52.0600%). Skills tools reached 388/433 lines (89.6074%) and 159/188 branches (84.5745%), up from 22.3529% / 5.3191%. Production executable-error checks, new-test unused-import checks and diff whitespace passed; the unchanged pre-existing unused `_default_skills_path` import remains outside this patch. Raw before/after/complete receipts and the coverage map are retained. EN/RU Wiki was updated. No version bump, release artifact, installation or owner service restart was performed. Android/Desktop code is unchanged; previous platform receipts are historical, and new Windows CI/native-device acceptance is not claimed.

## 2026-10-23 — API file-policy coverage and working-directory correctness (unreleased)

Original-trigger tests reproduced four failures in two behaviors: a primary working directory containing spaces was truncated, and an existing directory with a suffix was mistaken for a file when choosing the relative-path base. Session directory parsing now retains the full line and existing directories keep their identity. Root enforcement, temporary/additional roots, protected control files and existing Bash policy options are unchanged. Two additional Read/Bash regressions exercise the complete decision for a working directory containing both spaces and a dot.

The new policy contract cases use actual temporary files, symlinks and a synthetic policy file rather than owner paths/configuration. They cover instruction-header fallback/precedence, root traversal and sibling/symlink escapes, declared file aliases, creation/existence rules, protected-file reads/writes, known read wrappers, Bash allowlist/mutation decisions and actionable denial feedback. No Bash command under test is executed. A separate input-contract file covers quoted/Unicode paths, controls, shell metacharacters, length bounds, URL/drive syntax and invalid types.

The initial policy-contract run collected 78 cases: 74 passed and the four original-trigger cases failed. The focused post-fix set passed 111 cases, followed by two complete Read/Bash compatibility cases. All 80 new policy cases and eight input-contract cases pass. The official complete run covered both roots and 325 files: 5,548 collected, 5,527 passed, 21 existing platform/environment skips, 670 passing subtests, zero failures/errors. The new input-contract file, added after full-run discovery, passed independently against the same production source: eight tests and 17 subtests. The accepted report counts every file once across these receipts: 326 files, 5,556 collected, 5,535 passed, 21 existing skips, 687 subtests, zero failures/errors. Raw coverage was combined after verifying unchanged production-source hashes; percentages were not averaged. This adds 88 cases without new skips or coverage exclusions.

Lines rose from 49,998/80,449 (62.1487%) to 50,249/80,451 (62.4591%); branches from 14,646/28,882 (50.7098%) to 14,809/28,884 (51.2706%). File policy reached 386/403 lines (95.7816%) and 228/248 branches (91.9355%), up from 34.1646% / 26.8293%. Executable-error/unused-import checks and diff whitespace passed. EN/RU Wiki and the coverage map were updated. This is source-only work for a future release, with no version bump, installation or owner service restart. Desktop/Android code is unchanged; earlier unit/native results remain historical. New Windows CI and physical-device acceptance are not claimed.

## 2026-10-23 — Unreleased message-history and diagnostic-report regressions

New original-trigger cases exposed mismatched tool/result correlation: a parallel result could be paired with the last advertised call, a later pending call could capture an earlier result, and an orphan result could acquire unrelated arguments. Empty results could reuse old output, and an empty error result lost its error marker. Pairing now uses the result ID and only preceding calls, preserves id-less legacy input, retains empty-result identity/error flags and never invents an orphan call binding. One initial autocomplete fixture was corrected to match the actual before-marker draft format; the five remaining original-trigger failures were reproduced before changing the implementation.

Browser request validation distinguishes non-object input, missing/non-string action and unsupported actions in addition to specific missing/unexpected fields. Android tests cover notification serialization and both incident/overview exports, retain timeout uncertainty through task completion, and verify secret redaction. Six block-content cases exercise visible text, structured reasoning exclusion, Unicode/correlation and media omission. The focused Python contract set passes 84 cases. The official complete Python run covers both roots and all 324 files: 5,468 collected, 5,447 passed, 21 existing platform/environment skips, 670 passing subtests, zero failures/errors. This adds 52 Python cases without new skips or coverage exclusions. Lines rose from 49,739/80,425 (61.8452%) to 49,998/80,449 (62.1487%); branches from 14,470/28,876 (50.1108%) to 14,646/28,882 (50.7098%). The Anthropic message adapter reached 399/442 lines (90.2715%) and 239/292 branches (81.8493%), up from 36.6743% / 24.6622%. The Android unit suite passed 458 cases without failures or skips, five more than the published release. Executable-error/unused-import checks, diff whitespace and publication contracts passed. The coverage map and raw receipts are updated. This remains source-only work for a future release: no version bump, release artifact, installation or owner service restart. New native UI, Windows CI and physical-device acceptance are not claimed.

## 2026-10-23 — Unreleased follow-up: explicit browser rejection causes

This follow-up is intentionally isolated from the frozen 0.2.22 release. Browser argument validation names missing and unsupported schema fields and lists the allowed fields. Unrecognized key names are counted without exposing the supplied names or values. Existing per-parameter type/range guidance and access policy remain unchanged. Desktop reply timeouts record the action, waiting boundary, configured duration and unknown completion, so a timeout is not relabelled as an unexecuted rejection. Trace diagnostics retain the timeout code. Android preserves bounded result hints when typed details are absent and displays supplied details rather than replacing them with a generic URL or timeout label.

Seven original-trigger regressions failed before the follow-up. All 31 browser feedback cases pass after it, including the exact read/collect/result field mistakes and privacy checks. The five-file Python compatibility set passed 67 cases and 14 subtests. Android BrowserToolDiagnosticsTest passed 12 cases, including two new cause-propagation checks in both languages; the complete Android unit suite then passed 455 cases without failures or skips. Publication and executable-error checks also passed. The tests use synthetic inputs and do not operate owner browser/model services. No version is bumped and no release artifact includes this follow-up yet.

## 2026-10-23 — Release 0.2.22 and Android 0.1.122 (123) published

Published eleven matching preview assets on GitHub and Gitea: DEB, AppImage, EXE, APK, wheel, matching English/Russian PNG and editable SVG diagrams, manifest and checksums. GitHub tag points to protected main a251ec0b7a19e2aa777d85025b368d3d634e47f1; the private mirror tags canonical caa500115a4fedf4c0a3ee683bba513946fc0bcb. Native-accepted package build 32d2df63e8bfc1ad28d3cbcfa47c192fc4495550, reviewed PR head 9d40559a27208c5f056cc826423594a794f89d82 and integrated main have the identical complete source tree. Accepted packages were promoted without rebuilding unchanged content. Separate build/test/release SHAs are explicit in the manifest.

Both complete Python versions passed 5,393 cases and 670 subtests, with 23 environment skips and no failures/errors. The first Python 3.13 attempt was delayed installing operating-system prerequisites and was cancelled before tests; its one-job rerun passed without source/test changes or bypassing protection. Desktop passed 480 cases on Linux and 479 with one Linux-helper skip on Windows. Android passed 453 unit tests, lint and disposable-device context/notification acceptance. Native Linux and Windows installation/first-task acceptance passed. The locally shipped Android APK has byte-matched production inputs and the previous signing identity; it is not claimed byte-identical to the runner's debug-signed APK.

Package inventories, all 84 application sources, 26 backend wrappers and 448 wheel members matched the frozen source. Separate Linux install 0.2.21, update to 0.2.22, reinstall, rollback and return preserved synthetic data and launcher/icon registration. Every Gitea asset was downloaded and hashed; all eleven GitHub server digests, public links, visual/metadata bytes and sizes matched. The real 0.2.21 updater discovered 0.2.22 for DEB/AppImage/EXE and downloaded the exact DEB. Published EN/RU Wiki and release descriptions were read back. Owner applications and model servers were not stopped or updated.

Detailed field-level browser rejection causes and timeout explanations were completed in a separate unreleased worktree and are intentionally excluded from this frozen release. Restart Desktop fully after the user installs the update and reopen affected chats; an independently launched model server does not need restarting. Preview limits, signing and tested platforms are recorded in BUILD_MANIFEST.json.

## 2026-10-23 — Prepare release 0.2.22 and Android 0.1.122 (123)

The user explicitly approved merging the accepted changes and publishing this release. Protected source 309b20e5ffc6749dac6a3126626ea558464de97a contains the reviewed browser/diagnostic and Read recovery changes. Its exact PR head passed Python 3.13/3.14, Desktop Linux/Windows, native Linux and Windows installer/first-task checks, Android unit/lint/disposable-device acceptance, package targets, source/secret contracts and Wiki validation. All 45 new Read cases passed in both Python versions. English/Russian before-and-after architecture diagrams are included as editable SVG and rendered PNG for release descriptions.

This preparation changes version metadata and matching release notes only. Python/Desktop becomes 0.2.22. Android becomes 0.1.122 (123) because this batch includes production notification/diagnostic fixes. No tests or coverage exclusions are weakened. Final protected-main CI, clean package/source inventories, isolated install/update/rollback checks, APK source/signing verification, actual public downloads and mirrored release readback remain publication gates. Owner Desktop, gateway and model servers are not stopped or replaced.

## 2026-10-23 — Bounded Read and durable tool-result recovery

Added matching English/Russian before-and-after architecture diagrams as editable SVG and release-ready PNG. The diagrams were rendered and visually inspected; no release was published.

Read now returns bounded text pages with exact continuation, including within a long line, instead of recursively archiving its own output. Existing zero-based offsets and explicit line limits remain compatible. Small complete files still return plain text. Pages adapt to context size and the round's shared output budget, including prepared approval batches.

The output store records issued archives in the existing runtime SQLite database. Workspace-mode Read may recover an issued result for its task, or a later task in the same session and workspace, after checking the regular-file identity. This does not grant Write/Edit access or widen ordinary workspace permissions. Archives are immutable; changed, missing, foreign, unissued and symlinked files are rejected. Saving and registration must succeed before a readable path is advertised. Task/session history protects referenced archives from normal cleanup. Legacy recovery requires a matching structured spill event; aggregate-only legacy pointers without that evidence are not enrolled. EN/RU Wiki updates and the architecture document describe this behavior and its limits.

Three protocol-shaped executor regressions failed against the previous implementation and pass after the change. Separate before/after cases exposed stale-identity reissuance and a prepared approval batch exceeding its shared 40,000-character allowance; both are fixed. Tests exercise actual Read/store/SQLite/access logic, Unicode pagination, cross-process resumption, failure paths and compatibility. HTTP provider and Bash producer boundaries are synthetic; no live model request or owner service restart was used.

The official isolated Python run covered both test roots and 322 files: 5,415 collected, 5,394 passed, 21 existing environment skips, 670 passing subtests, zero failures/errors. After the final approval-batch allowance adjustment, all five affected test files passed again (120 cases). The accepted combined report replaces those files' earlier receipts and counts each file once: 5,416 collected, 5,395 passed, 21 existing skips, 670 subtests, zero failures/errors. Raw coverage shards were combined, not averaged. This adds 45 cases without new skips or coverage exclusions. Lines reached 49,739/80,425 (61.8452%); branches 14,470/28,876 (50.1108%). Read paging reached 95.08% lines / 91.67% branches; archive access 86.71% / 75.00%; result storage 92.91% / 73.91%.

Executable-error checks, new-module unused-import checks, diff whitespace and publication contracts passed. The coverage map and local receipts were updated. Desktop/Android source is unchanged; earlier unit/native receipts are historical, not fresh acceptance of Read. Windows CI and live-model acceptance of this batch remain outstanding. Versions, releases, installed runtime and owner services are unchanged; this is a source-only candidate. Activation will require a later runtime update and reopening affected chats, without restarting separately launched model servers.

## 2026-10-23 — Browser recovery feedback and accurate task diagnostics

Browser calls now explain action-specific arguments while retaining the flat schema used by the supported LLM transports. Rejections preserve their original error string and add a bounded hint and typed rejection diagnostic; supplied values are not echoed. Page read/scroll/collect/result calls still accept only action, and input/URL/profile/sandbox protections remain unchanged. Stale references request a fresh page snapshot. Calls marked performed, existing typed diagnostics and actual execution failures are not relabelled as unexecuted rejections. No automatic rewriting or retry loop was added.

Post extraction now associates a publication with its own permalink, body and media rather than a nested quote or linked card. Full reading can identify a rendered post omitted from the bounded feed index or lacking a time element. Body links cannot impersonate the requested post. A missing rendered post retains post_not_found and an explicit visible-DOM limitation. The new module is included in the source bundle inventory.

Android distinguishes agent-correctable browser rejections from owner intervention, reconciles legacy rejected calls after task success and preserves genuine execution failures and uncertain outcomes. Empty or unrecognized diagnostic objects no longer decode as invented internal errors. Worker task failures retain supplied machine reasons and sanitized error details; failures without evidence remain unknown. Android exports explicitly state that server availability was not checked. Related EN/RU Wiki additions were updated without modifying imported originals. Persisted-output access outside a workspace remains a separate issue; read permissions were not widened.

Before/after evidence: ten new feedback checks failed before the implementation; native Chromium misidentified a parent post as its quote; three worker subtests lost the supplied cause; two Android cases decoded an empty diagnostic incorrectly. These cases pass after the fixes. Focused Python acceptance passed 57 cases and three subtests. The final official isolated run covered both roots and all 320 files: 5,371 collected, 5,350 passed, 21 existing platform/environment skips, 670 subtests, zero failures/errors. This adds 26 Python cases and three subtests. Lines reached 49,490/80,220 (61.6928%); branches 14,371/28,782 (49.9305%). No skip or coverage exclusion was added. The browser Python module reached 94.34% lines / 91.86% branches.

Desktop passed 480/480, including two new reading cases. V8 reports 90.76% lines / 79.48% branches of instrumented files; injected DOM execution is verified separately and is not folded into this metric. Sandboxed native Electron 44.9.0 accepted nested/linked quotes, missing time elements, pagination and body-link rejection, plus existing main/iframe drawing. Android passed 453 unit cases and lint (zero errors, 42 existing warnings, two hints), adding ten cases. Source/publication contracts and executable-error checks passed; an unchanged pre-existing unused import in worker is outside this patch. The local coverage map and private acceptance records were updated. Application/Android versions are unchanged; no release, APK delivery, installation or owner service restart was performed.

## 2026-10-23 — Numeric tool inputs and weak-area Python coverage

Added 189 behavioral cases across tool-plan parsing, workflow transitions and workspace naming. The tool-input suite exercises captured bracket syntax, XML parameters, required/extra fields, defaults, file aliases, question normalization, policy ordering and numeric types. Its first 86-case run reproduced five real failures: booleans were accepted as numbers, and integer fields accepted booleans, fractional values and text. Numeric validation now rejects booleans and non-finite values, checks integer types and preserves integral values such as 2.0. Schema-declared numeric transport text is normalized before validation, preserving existing XML calls. Other schema shapes and nonnumeric content retain their existing behavior. Added an XML regression fixture before changing this compatibility path.

Workflow tests use real protocol-shaped histories to verify step limits, completion, one-attempt failure recovery, repairable tool-input errors, session recall, source-scan boundaries, forced reads and summaries without launching tools, network requests or models. Ten workspace tests use real cleaning/name generation and temporary SQLite databases, including parameter binding and missing-database behavior. The original naming implementation is unchanged.

The focused compatibility set passed 210 cases. The final official isolated run passed all 319 files: 5,345 collected, 5,324 passed, 21 existing environment skips, 667 subtests, zero failures/errors. Coverage rose from 48,416/80,160 lines (60.3992%) and 13,707/28,746 branches (47.6832%) to 49,454/80,183 lines (61.6764%) and 14,352/28,762 branches (49.8992%). No new skip or coverage exclusion was added. Tool parsing reached 98.38% lines / 96.04% branches; workflow engine 74.16% / 68.69%; workspace naming 76.04% / 77.78%. Native Windows/KVM evidence remains separate from this unit metric. Related EN/RU Wiki source and the local coverage map were updated; executable-error/unused-import checks and publication contracts passed. No owner application, model server or gateway restart was performed.

## 2026-10-23 — Release 0.2.21 and Android 0.1.121 (122) published

Published seven matching preview assets on GitHub and Gitea from protected source `ae17586a53819adc1a24f11aed9ff280a5902f55` after all final main checks passed. New features include settings-controlled resource monitoring, compact diagnostics, browser image downloads, video thumbnails, local streaming token usage and Android pinned chats. Reviewed dependency updates are included. Release notes and live EN/RU Wiki agree with the implementation.

Python 3.13: 5,156 collected, 5,133 passed, 23 skipped, 667 passing subtests, zero failures/errors. Lines 48,405/80,166; branches 13,761/28,746.

Python 3.14: 5,156 collected, 5,133 passed, 23 skipped, 667 passing subtests, zero failures/errors. Lines 48,405/80,160; branches 13,703/28,746.

The 23 CI Python skips cover six Windows contracts, fifteen host/guest KVM/vsock checks and two unavailable unprivileged-user-namespace checks. All six Windows and fifteen KVM/vsock cases passed separately on matching native platforms; both namespace checks passed on the local host. Thirteen obsolete/environment-dependent skips were repaired earlier. Native coverage is not added to unit coverage.

Desktop passed 478/478 on Linux and 477 with one Linux-only installer-helper skip on Windows. Android passed 443 unit tests, lint and actual Android 14 emulator context/notification scenarios. Three-or-more pin persistence, long-press actions, reconnect, unpin and Russian UI were accepted separately on a disposable Android 14/KVM device. The shipping APK is byte-identical to the previously accepted and delivered 0.1.121 (122), with the existing debug signing identity; its Android source and generated shared assets match this release. It is not claimed to have the CI runner's APK signature or identical bytes.

Final DEB, AppImage and EXE came from actual main CI. All source inventories, 83 application files, 26 backend wrapper files and 446 project-wheel members matched the clean protected source. Isolated Linux install 0.2.20, update to 0.2.21, reinstall, rollback and return passed with retained synthetic data and launcher/icon registration. Native Windows install/update/first-task acceptance passed. Image downloads, resource monitor lifecycle/metrics and video preview acceptance passed on Electron 44.9.0 with application source unchanged by the version-only release preparation. Native screenshots were inspected. Every asset's actual download hashes, tags, descriptions and inventory were verified on both hosts. The real 0.2.20 updater discovered 0.2.21 for DEB/AppImage/EXE and downloaded the exact DEB.

Restart Desktop completely after updating and reopen affected chats; this restarts its bundled runtime. Existing connections remain valid and independently launched model servers do not need restarting. Users of KVM isolation must prepare a guest image matching host version 0.2.21 before enabling isolation; the owner image was not replaced. Preview limits remain: unsigned Windows tested on Server 2025, native Linux Ubuntu 24.04, Android 14 emulator; physical Windows 11 and phone acceptance are not claimed. Owner Desktop, gateway, model services and installed tools were not stopped, replaced or restarted by this release work.

## 2026-10-23 — Prepare release 0.2.21

The reviewed feature and dependency tree is merged as protected source bb84b7a9572a9d9904e9b2fb36b2d2d47736cef2. Its complete main CI passed Python 3.13/3.14, Desktop on Linux/Windows, native Linux, Android unit/lint/native scenarios, both package targets, publication/secret checks and Wiki publication. One unchanged-source PR retry was needed for Android emulator rendering and Windows native fixture transport; the final main runs passed. Existing receipts retain both failures and successful acceptance.

This release preparation changes only Python/Desktop version metadata to 0.2.21, matching Desktop release notes and this record. Android remains the already accepted 0.1.121 (122), including persistent session pins. No test is removed, skipped or weakened. Final versioned installers, clean-source inventories, source/signature equivalence of the shipping APK, isolated update/rollback acceptance and publication readback remain release gates. Owner application and model services are not restarted.

## 2026-10-22 — Full skip audit and live KVM acceptance

- Resolved all 34 original skip dispositions: 13 deterministic checks now execute in the ordinary suite, six native Windows contracts passed on the Windows runner, and all 15 opt-in KVM/vsock checks passed in their matching environments. The regular Linux suite still correctly skips the six Windows cases and fifteen external host/guest cases; these are not failures or unverified claims. Evidence retains the earlier failed reproductions and the later successful result for each affected case.
- Live acceptance found a real host-local inference defect: the firewall allowed the QEMU host alias, but the task retained the host loopback URL. The host serializer now rewrites only a matching, allowed local route to the host alias, preserving scheme/port/path/query. It refuses credentials, mismatched routes, unsupported schemes and invalid ports. Disabled/remote routes and the secret proxy retain their existing behavior. Five subcases failed before the change; the four affected unit modules then passed 152 tests and 30 subtests, and additional malformed-route cases passed.
- Updated live-test setup to respect explicit hypervisor URI, guest channel port and scratch directory. Corrected prematurely collected host marker units, missing DBus socket identity, scoped teardown, and cleanup after constructor failure. Approval acceptance now verifies closure of the parked task runner and absence of an unapproved file; a warm VM remains valid. The full PoC cancels its task and explicitly stops its session through the existing lifecycle API. Production pause/cancel policy is unchanged.
- The real host/guest matrix accepted image preflight, VM lifecycle, all ten negative boundary probes, the complete approval/write/isolation/cancel/session-stop flow, and both original vsock smoke functions inside a guest. Host-observed CID mapping confirmed the additional guest handshake. Test VMs used enforced CPU/memory limits, a 20 GiB disk and a synthetic loopback provider; no real model request was made. The full flow and guest fixtures reported no cleanup leftovers.
- Matching acceptance used Ubuntu 26.04 and system libvirt. Session libvirt rejected the existing enforced memory tuning on this host; limits were retained and tested with a supported backend. The 20 GiB image was not truncated to the older 8 GiB test default. Existing owner images and execution configuration were not changed.
- Final official isolated Python run with the combined dependency set: 5,156 collected, 5,135 passed, 21 expected environment skips, 667 subtests, zero failures/errors across 316 files. Lines: 48,416/80,160 = 60.3992%; branches: 13,707/28,746 = 47.6832%; combined 57.0428%. Native acceptance is reported separately from this unit coverage. WorkspaceManager remains 91.30% lines / 80.00% branches. Related unit, native and coverage receipts are retained locally; no tests or coverage exclusions were weakened to improve the result.

## 2026-10-22 — Dependency and native-test follow-up (candidate)

- Reviewed the dependency-only patches for Electron 44.9.0, pywinpty 3.1.0, Ruff 0.17.1, setuptools 84.0.0 and pinned GitHub Actions updates. Combined them in the feature CI branch so all required platform checks validate the final tree together. Locked synchronization succeeded in a disposable Python environment; installed owner applications and model servers were not updated or restarted.
- Added network timeouts and bounded download retries to CI apt prerequisites after two Python jobs stopped before test execution. Test assertions and suite deadlines remain active.
- Scoped the live KVM fixture's systemd reset to its own marker unit; a new regression verifies that unrelated failed user services cannot be reset. The targeted PoC suite passed 48 tests with its one genuine opt-in VM skip.
- Fixed guest image construction when browser installation does not create a cache directory. Existing image/bwrap contracts passed 68 tests and 64 subtests. A separate current image was built and independently checksum-verified; this is not a claim that all live KVM/vsock checks passed. Live checks remain in progress with CPU/memory limits retained.

## 2026-10-22 — Desktop resources, image downloads, video previews and mobile pins (local candidate)

- Added a draggable resource monitor, off by default, with a persisted Desktop settings toggle. CPU uses timed deltas; Linux RAM uses MemAvailable. NVIDIA metrics use a bounded, read-only query with missing-value fallback. Hidden or disabled monitoring does not poll. The monitor has no Node access, external navigation or model-generation path. System Status and Task Manager now use a compact single-column layout and a 94-by-30-pixel refresh button in native acceptance.
- Added Save image as to the docked browser's existing native context menu. The original source URL/query and browser session are retained; downloads enter the existing history without navigating. Nine regressions and native Electron before/after acceptance passed using a synthetic X image page and exact saved-byte comparison. Text copy and ChatGPT page actions remain available.
- Video cards lazily generate small client-side poster frames through the existing authenticated media URL. Up to three positions avoid black intros; duplicate cards share work, concurrency is two, timeout is eight seconds, and detached/replaced cards cannot receive stale results. The original player is never autoplayed or repositioned; visible metadata loading does not reload a playing clip. Native Chromium accepted two equal posters from a black-intro fixture, zero playback events and unchanged player times.
- Android 0.1.121 (122) supports three or more pinned chats through existing server metadata, with long-press Pin/Unpin, a pin icon and activity ordering within pinned/ordinary groups. Rename, archive and active tasks are retained. All 443 Android unit tests passed; lint reported zero errors, 38 existing warnings and two hints. The signed debug APK and instrumentation build passed. A separate Android 14/KVM device accepted three native long-press pins, reconnect persistence, unpin, refresh ordering and Russian actions. This is synthetic-device acceptance, not a physical-phone claim.
- Final Desktop suite: 478 passed, zero failures/cancellations/skips; V8 92.19% lines and 79.36% branches. Native monitor, diagnostics, browser download and media-preview evidence is retained separately from unit coverage. Related EN/RU Wiki source overlays are updated. No owner service restart or application installation was performed; Desktop changes require a new build and application restart.

## 2026-10-22 — Coverage progress and previously skipped checks

- Added 22 WorkspaceManager cases using temporary real SQLite databases and file trees. Ten failed before the fix: cleanup used a human-readable folder name as a task ID and could delete an old active workspace. Cleanup now resolves the metadata task ID and preserves unreadable/unidentifiable metadata and symlink folders. Creation, results, reports, legacy paths, final-task cleanup and database-failure retention remain covered. Module coverage rose from zero to 91.30% lines and 80.00% branches.
- Re-enabled ten obsolete unconditional TUI skips. Seven already passed; two exposed alias wrappers returning a copied output list (and an empty list instead of None on unavailable UI). The wrappers now retain the unified output buffer. Updated the obsolete layout assertion to verify the separate activity area and actual composer, rather than an old child count. All 38 tests pass. Two environment-dependent negative cases now construct missing-tool and missing-workspace conditions explicitly, without simulating success of a real VM.
- Replaced the credential-dependent MiniMax URL smoke with a loopback HTTP contract using the production URL builder, auth resolver and HTTP transport. It validates exact route, headers, payload and decoded response without paid requests. This does not claim current vendor availability. Thirteen of the original 34 skipped cases now execute.
- Full official isolated Python run over both roots and 316 files: 5,144 collected, 5,123 passed, 21 skipped, 654 subtests, zero failures/errors. Lines: 48,410/80,150 = 60.3993%; branches: 13,705/28,742 = 47.6828%; combined 57.0428%. Before this batch: 59.9573% lines and 47.3831% branches. No new exclusion or skip was added. Remaining skips are six native Windows contracts and fifteen genuine KVM/vsock checks; Windows CI and a separate current KVM image are being prepared, not counted as passes.
- The local coverage map has been refreshed after this accepted batch, with module gaps and separate Python/V8 measurements. Ruff executable-error checks, new workspace tests, publication checks and the pinned redacted secret detector passed. Existing broader style debt is recorded separately; no unrelated style cleanup was performed.

## 2026-10-22 — GitHub PR UI coverage and stale pin-error regression

- Added 29 behavioral UI cases using the actual GitHub review module with a synthetic bridge and DOM, plus four reviewed PR-link parser cases. Covered inbox/search/pagination, account revalidation and cache clearing, delayed list/detail/files/timeline replies, review/fix drafts, pinned head/method/account merge requests, cancellation/recovery, timeline deduplication/filtering, resource links, clipboard/site callbacks, and chat binding. No real GitHub operation, clipboard write, account, or repository mutation was performed.
- Reproduced a delayed pin failure overwriting the notice for a different selected PR: the initial 20-case run had exactly this one failure. Scoped pin-error rendering to the initiating PR, matching the existing successful-response guard. Current-PR errors remain visible and retry works; unrelated action handling is unchanged.
- A separate hidden Electron instance with a disposable profile and Xvfb confirmed the real-DOM before/after result: the new PR displayed the old request error before the fix and a clean notice after. HTTP/HTTPS requests were blocked and the observed count was zero; GPU use was disabled. This is targeted native acceptance, not a physical-device or full packaged release check.
- Full Desktop V8 run: 451 passed, zero failures/skips (33 new cases). GitHub review UI lines 9.88% to 100%, branches 83.27%, functions 89.09%. Overall instrumented JavaScript lines 90.23% to 92.18%, branches 78.57% to 78.97%. The same instrumentation limits from the coverage map apply; this is not a claim of full application coverage.
- No Python source changed in this continuation, so the preceding 5,121-collected/5,087-passed/34-skipped/654-subtest Python receipt was retained without repeating its suites. TestLOG and the local coverage map were updated. No commit, publication, installation, version bump, or owner service restart was performed.

## 2026-10-22 — Coverage map and behavioral regression tests

- Measured both Python test roots before the changes: 5,054 collected, 5,020 passed, 34 existing skips, 654 subtests; no failures/errors. Added 67 Python cases across history search, tool-result correlation, streaming stop/delta behavior, and output sanitization, plus eight Desktop DOM cases for download history. Existing tests and runtime behavior were preserved except for the confirmed output-sanitization defect below.
- Three regressions reproduced loss of the entire useful body after leading `Latest tool used:` / `Tool result:` metadata. Narrowed the two prefix substitutions to strip only metadata while preserving multiline and inline output, case-insensitive labels, and normalized CRLF input. Unlabelled output and embedded literal labels remain intact. These three cases failed before and pass after.
- Final Python receipt covers all 315 discovered test files exactly once: full isolated 314-file run plus the final streaming file in a separate identical runner invocation. Combined: 5,121 collected, 5,087 passed, 34 skips, 654 subtests, zero failures/errors. Coverage combined from the original per-file data, not averaged percentages: lines 47,726/80,152 (59.54%) to 48,057/80,152 (59.96%); branches 13,444/28,736 (46.78%) to 13,616/28,736 (47.38%); aggregate 56.18% to 56.64%. Line/branch denominators are unchanged.
- Selected Python modules, lines/branches after: output utilities 100%/100% (previously 0%/0%); tool history 94.01%/85.42% (36.53%/21.88%); session search 93.48%/89.00% (34.78%/25.00%). Tests use synthetic transcripts and injected external recall boundaries; they do not call a model, execute observed shell commands, or replace the functions under test.
- Desktop V8 run: 418 passed, zero failures/skips. Measured lines 89.59% to 90.23%, branches 78.40% to 78.57%; download-history renderer lines 29.41% to 100%, branches 81.82% to 88.89%. This measures instrumented files, not the entire Electron/Web UI: some VM snippets, main/preload/native paths and Android are outside this percentage. DOM cases do not claim physical-device acceptance.
- Existing skips remain: six native Windows contracts, one live provider requiring a credential, seventeen virtualization/vsock/image prerequisites, and ten legacy idle-application cases requiring running state. No skip/exclusion was added to improve the result. Ruff for new Python tests and whitespace validation passed. No installation, publication, or owner service restart was performed. The local HTML map ranks remaining gaps and records measurement limits.

## 2026-10-22 — Real zero input context-bar regression

- Added one UI regression for measured zero input tokens: counters stay numeric, the context bar remains visible at 0%, its ARIA value is zero, and unavailable-data labels are absent. Existing helpers, five preceding cases, and application code are unchanged.
- Independent targeted run: six passed. A disposable truthiness mutation remained undetected by the preceding five tests and failed only the new test (five passed, one expected failure), confirming the regression is exercised.
- Full Desktop suite via `npm --prefix desktop test` with Node 24: 410 passed, zero failures/cancellations/skips. Python and runtime code were unchanged by this test-only addition; their suites were not repeated. No installation or service restart was performed.

## 2026-10-21 — Live local context metadata and mobile status card (local candidate)

- The running local server reports `default_generation_settings.n_ctx = 64000`. Added a bounded, non-generating loopback-only `/props` reader; no redirects, proxy inheritance, credentials or arbitrary remote metadata requests. Web model-info and Mobile session details now return this configured per-slot value. Local worker resolution checks live metadata before catalogue training maxima; cloud fallback remains intact. Unknown local metadata returns null to the UI rather than a guessed catalogue size.
- Android prefers freshly reported session context over an older task event. Occupancy remains the last model request's input count, not cumulative tokens or a live KV-cache measurement. Added regressions for 64k/128k changes, unknown/invalid values and cloud event fallback, plus native status-card acceptance to run on a disposable emulator.
- Full Python suite for the preceding layout/drag candidate passed: 5,018 collected, 4,984 passed, 34 existing skips, 644 subtests, zero failures/errors; aggregate branch-aware report 56% (not a claim of full coverage). The updated live-context targeted modules passed 22 tests and eight subtests; Web/Mobile compatibility checks passed (152 Web and 88 Mobile tests). The initial new-test failure was a missing required fixture argument; corrected and rerun: 34 targeted tests/eight subtests passed. Android unit tests: 410 passed, no failures/errors/skips; lint and APK assembly passed after increasing the isolated lint process Metaspace limit. Native Android acceptance remains pending. All 400 Desktop tests, 82 layout samples and four native canvas cases passed. Wiki tests: 11 passed; strict build passed.
- No public release, owner service restart or owner application update has been performed. Local artifacts will require an explicit application restart after installation.

## 2026-10-21 — Responsive composer and bounded browser dragging (local candidate)

- Reproduced the reported composer overflow using actual HTML/CSS in isolated native Electron: at a 700px conversation width, Send extended beyond the form. Intrinsic flex wrapping and bounded model-chip sizing now keep controls inside the main and workspace composers while the pane shrinks or grows. Native geometry/hit testing passed 82 samples across 240–900px with idle/busy controls and preserved draft text. Before/after evidence: `/tmp/igorek-composer-resize-before` and `/tmp/igorek-composer-resize-after`.
- Confirmed the browser exposed click/hover/fill/press/select/check/scroll but no mouse dragging. Added bounded `drag` to the model schema, Python private pipe, desktop dispatcher and trace action allowlist. Paths contain 2–128 normalized points inside a current visible target; duration is bounded to 100–3000ms. Native left-button input follows the path and always releases the button on dispatch failure. Cross-frame Chromium input retains the held-button mask. No arbitrary evaluation or local-file navigation was enabled.
- JavaScript interaction tests: 13 passed, including malformed/bounded paths, stale/protected/obscured targets, native input order, failure cleanup and iframe button masks. Python browser module: 12 passed, including private-pipe/schema validation and redacted diagnostics. Full Desktop suite: 400 passed, zero failures/skips.
- Native canvas acceptance passed four real trusted-input cases: freehand and ellipse on the top-level canvas and inside an iframe; each produced more than 3,000 nontransparent pixels. Screenshot inspected. Evidence: `/tmp/igorek-browser-drag-native`. This is controlled-canvas acceptance, not a claim that the local language model completed an Excalidraw drawing.
- Full Python suite and local packaging are pending. No owner application, gateway or model service was restarted; no release was published. The installed 0.2.17 does not receive these changes until updated and restarted.

## 2026-10-21 — Android notification replay and session-navigation regression

- The reported local_ui/display/presentation owner identified timeline rendering as the notification source. Reproduced the exact symptom on the previously delivered 0.1.117 (118) APK: opening a synthetic saved timeline containing ten failed tasks created ten false new notifications. Two before-fix unit regressions also failed when known observations reopened recovered incidents.
- Timeline rendering and saved-chat loading are now passive. Live failure admission runs after stream replay and persisted processed-sequence checks. Known event IDs cannot reopen recovered incidents; new event IDs still can. Typed errors retain source codes/references/time. Untyped live task failures use ERROR.UNKNOWN with real task/session identity, not an invented INTERNAL.UNEXPECTED UI error. Terminal events do not duplicate the same unresolved task incident, while a recovered transient failure cannot hide a later terminal failure in that task.
- Removed only legacy notification copies structurally identified as local_ui/display/presentation with ui:timeline: owners during journal restoration. Original chat/task history and genuine transport/model incidents remain untouched. Input/attachment errors now use the shared persisted report journal and consume their transient UI error state; new MCP operation failures are registered at the operation boundary. Read state survives navigation/restoration; acknowledgment does not assert recovery and cards label read state explicitly.
- Added fourteen replay/compatibility regressions. Final full Android result: 409 tests passed, zero failures/errors/skips; lint passed with zero errors, 38 warnings and two hints; debug and instrumentation assembly passed. Native Android 14/API 34 KVM acceptance passed on the final APK bytes: history A/B/A stays passive, read/ID/count survive repeated navigation and journal restoration, old events stay suppressed after recovery/clear, genuinely new errors remain unread, and input errors appear in the same diagnostic snapshot. The old ten-error reproduction and final screenshots were inspected. Physical-phone acceptance is separate.
- Android 0.1.118 (119) retains filename-only model labels and bounded incident/overview exports. Manifest, notes, DEX and signing continuity passed; APK SHA-256: `19dc7207e78545024b00c020faba35ce0a77d4d7a0965e0933d88c07281852b8`. EN/RU Wiki/contract, strict build, link/provenance check and eleven Wiki tool tests passed. Desktop/Python source and owner services are unchanged; their suites were not rerun. Install the new APK and reopen Android; no server restart is needed. Telegram delivery is recorded separately; no GitHub/Gitea release is claimed.

## 2026-10-21 — Bounded Android incident and overview diagnostic reports

- Replaced the notification card's whole-history clipboard action with an incident-only schema-2 report, bounded to 8 KiB UTF-8. It preserves the selected incident ID, typed error/status, explanation, state/recovery proof, UTC times and correlation references. The latest event appears once; at most four earlier events contain only differing fields, with explicit null for absent earlier metadata. Empty/private metadata is omitted, and absent references are never manufactured.
- The separate overall-report preview/copy/share action is bounded to 32 KiB, ten unresolved details and twelve groups. Unknown outcomes/owner attention take priority. Recovered incidents from the last 24 hours are grouped by code, component, endpoint role, HTTP status and native type, retaining counts and first/last times; older recoveries and omitted details/events/groups are counted. The existing 50-incident/eight-observation journal and internal snapshot remain unchanged. Copying/exporting does not acknowledge, recover or replay tasks.
- Added fourteen report regressions covering selection scope, references/times, recovery proof, delta encoding, missing earlier fields, bounded timelines, grouping/time windows, unresolved priority, code/status separation, group bounds, redaction, worst-case UTF-8 size and legacy notifications. Updated the earlier export test's detail-count expectation from 100 to the new explicit limit of ten. All 395 Android unit tests passed with zero failures/errors/skips; lint passed with zero errors, 38 existing warnings and two hints; debug and instrumentation assembly passed. Desktop/Python code is unchanged and those suites were not rerun.
- Native Android 14/API 34 KVM acceptance passed real long-press selection-only copying and scoped confirmation, the separate overview clipboard/preview, grouped recent recovery without full recovered incident details, repeated-text identity, acknowledge/reopen/cancel/clear. Actual clipboard reports measured 2,330 and 4,400 bytes. Both screenshots were inspected; physical-phone acceptance is separate. No support email was sent.
- Android 0.1.117 (118) has matching English/Russian release notes, verified manifest/DEX/embedded notes and the existing signing identity. APK SHA-256: `97e73acff5ef85b81569f259073c1c0c64323b4e7ee9229337a0b5267bc7a6ad`. Strict Wiki build, provenance/link check and all eleven Wiki tool tests passed; documentation describes the two export scopes. The previous filename-only model improvement remains included. Install the new APK and reopen Android; no Desktop/runtime/gateway restart is required. Telegram delivery is recorded separately; no GitHub/Gitea release is claimed.

## 2026-10-21 — Android filename-only model labels

- Local model labels now show the filename rather than filesystem directories in the same-chat profile menu/composer, new-chat model details and external-agent history metadata. Long menu labels use at most two lines. Cloud namespaces and aliases remain intact; original model/profile values, serialization and routing are unchanged.
- Added eight unit regressions for Unix/Windows/network/home/relative weight paths, file URIs, Unicode, empty/root inputs, cloud/remote compatibility and unchanged catalogue wire values. All 381 Android unit tests passed with zero failures, errors or skips; lint passed with zero errors (38 existing warnings and two hints). Debug and instrumentation APK assembly passed. Desktop/Python source is unchanged and its suites were not rerun for this Android-only fix.
- Native Android 14/API 34 acceptance on a disposable KVM emulator passed actual filename-only menu/composer labels, the retained raw model path returned by the real TLS fixture server, same-chat history/draft, cloud/local/back routing, next-task profile and busy-switch rejection. Both screenshots were inspected. Physical-phone acceptance is separate; the owner phone, Desktop, gateway and model services were untouched.
- Android 0.1.116 (117) has matching Russian/English notes; the versioned APK's manifest, embedded notes, new DEX helper and existing signing certificate were checked. APK SHA-256: `0d3057b0439080b59a5477bedc6d3b87c524a5e041c3861f1b5fced757074a1f`. Wiki EN/RU source overlays describe presentation-only labels; imported originals remain unchanged. Strict Wiki build, all 11 Wiki unit tests and the rebuilt-site link/provenance check passed with zero errors. Install the new Android APK and reopen the app; no desktop/runtime/gateway restart is needed. No new public release or Telegram delivery is claimed.

## 2026-10-21 — Final cross-platform release checks

- Complete final Python roots: 5,017 collected, 4,983 passed, 34 existing skips, zero failures/errors, and 644 subtests. The live grep.app success path returned actual hits in this run. This result includes all five new deterministic MCP error regressions.
- The Windows Desktop contract job exposed a transient `EPERM` while deleting its own temporary executable fixture after process exit. Added bounded `rmSync` retries for that fixture cleanup and reused the helper in the new owner-protocol test. All behavioral assertions and failure cases are unchanged; persistent cleanup failures still fail the test.
- The search settings help now uses the exact existing execution-mode label, “На этом компьютере — без KVM”.

## 2026-10-21 — Preserve upstream MCP tool failures

- The full Python 3.13 CI failure exposed an actual bridge defect: official grep.app responded over HTTP 200 with `result.isError=true`, but the local stdio wrapper discarded that flag and prepended a success/results header.
- Captured the public upstream response as a fixture. The unchanged bridge failed two new integration regressions before the fix; success/URL-contract and HTTP-error compatibility cases passed. The fix preserves the structural error flag, never classifies by response wording, and leaves the existing live test/assertions/availability handling unchanged.
- Targeted result after the fix: five deterministic regressions passed; the original live module passed its three protocol cases and retained its existing upstream-unavailable skip. The observed official MCP transport was HTTP 200 with `result.isError=true`; no inner service HTTP status is asserted.
- Added five deterministic HTTP-upstream → Node stdio regressions for the captured error, empty errors, normal results, successful text mentioning an error, and HTTP 429. Hosted live availability is reported separately from deterministic correctness.
- Hosted Linux/Windows artifacts now retain the new search settings native acceptance summaries.

## 2026-10-21 — Desktop search configuration

- Added owner settings for Tavily, Exa and Serper, with an explicit connection probe before selection is saved. Existing keys remain available when switching providers or disabling search.
- Desktop CC workers resolve search credentials from the same unlocked Desktop Vault as their model profile. Keys stay outside model prompts, published profiles and shell environments. CLI credential storage remains independent.
- Search credentials must be owner-created/imported secrets. Locked Vaults, modified/unverified model bindings, invalid keys and agent-created values cannot enable this route. Slow probes do not overwrite newer settings.
- Serper advertises search only. Tavily and Exa also provide extraction. The model prompt reflects the advertised capabilities. KVM has no new search credential bridge and this Desktop path is disabled there.
- Synthetic checks cover owner protocol authorization, actual registry schemas and dispatch, headers/endpoints, provider normalization, storage reopening, lock/delete/rotation, response limits, redirect refusal and sanitized failures. No production key or live paid account is used by tests.
- Initial focused run: 225 Python cases passed, zero failures/skips, plus nine subtests. Prompt follow-up: 71 cases passed plus twelve subtests. Desktop: 397 Node cases passed, zero failures/skips. Final verification artifacts and package acceptance are recorded separately under local build evidence.

- Complete Python roots: initial 4,996 collected, 4,962 passed, 34 pre-existing skips, zero failures/errors and 644 subtests. Coverage: 47,540/79,972 statements and 13,371/28,664 branches. The final expanded run collected 5,010; its live grep.app test encountered HTTP rate limiting and is retained for an unchanged whole-module recheck and hosted CI.
- All three new modules have 100% statement and branch coverage in the focused 49-case regression run. These figures describe the new modules, not the whole application.
- Native Linux first-task/restart acceptance and the separate native search settings path passed. Search acceptance uses synthetic fixed-endpoint responses and a disposable Vault, verifies all three providers through UI/private IPC/tool dispatch, and checks notification-only failure reporting. No live provider account acceptance is claimed.
- Android unit and lint checks passed for the already accepted 0.1.115 (116) APK. Its earlier native Android 14/API 34 fixture verifies notification-only errors and media presentation; physical-device acceptance remains separate.

## 2026-10-20 — Android same-chat executor profile selection

- Added an accessible profile/model menu in the Android composer using the existing authenticated profile-switch endpoint. It lists verified profiles and models, keeps the session, history, draft and attachments, updates the selected model without reopening the chat, and preserves the MCP selector. Unsupported connections keep their existing behavior and show an availability explanation.
- A shared route gate prevents concurrent profile switching and message admission, including sends from another screen. Server-side pending/running/input/approval guards remain authoritative. Session/client generation checks reject late results for a different chat or connection; old initial-detail requests cannot overwrite a successful switch. The request sends only the profile name and retains authenticated pinned TLS transport.
- All 364 Android unit tests passed, zero failures/errors/skips; lint, production debug assembly and profile-switch instrumentation assembly passed. Coverage includes legacy/catalogue choices, send/switch interlocks, authenticated real TLS requests, retained returned history, invalid paths and 400/401/409 responses without write retries. The existing backend profile-switch suite passed all 24 tests against disposable data, with no backend changes. An initial full Android run failed a prior notification snapshot test because default-value serialization could omit its dynamic timestamp; the fixture now uses `encodeDefaults=true`, matching production persistence, with its equality assertion intact. The complete repeated run passed.
- Android version 0.1.114 (115) has matching Russian/English notes. APK manifest, notes, new DEX types and signing continuity passed verification. APK SHA-256: `64f0134e9529ebd7e5ebf32c144231440b45635ba347696b12697cbbcef2d6a2`. Native Android 14/API 34 acceptance passed with KVM: the actual menu showed verified profiles/models, cloud/local/back switching retained the same session, history and draft, the next sent task was recorded under the selected local profile in the same session, and busy selection plus a direct HTTP switch were rejected without changing the route. Both menu/composer screenshots were inspected. The first software-emulator attempt had no host network route; its retry timed out amid system/System UI ANRs. The host subsequently confirmed usable KVM and the hardware-accelerated run passed. The initial sandbox-only inference about acceleration was incorrect. Physical-phone acceptance remains separate; no physical-device pass is claimed. No desktop/runtime upgrade, owner service restart, model request or subagent was performed.

## 2026-10-20 — Android recovered alerts leave the notification center

- Recovered incidents are excluded from both notification cards and the unread bell count. The filter applies to restored history and future recovery updates without clearing stored incidents; the bounded diagnostic report retains them. Unresolved errors, unrelated owners, unknown send outcomes and new outages after recovery remain visible. The empty list says that there are no active error notifications, and diagnostics remain accessible.
- All 358 Android unit tests passed with zero failures, errors or skips; `:app:lintDebug` passed. Regression coverage includes 16 restored recovered incidents, recurrence after recovery, unrelated owners and unconfirmed sends. The native notification fixture now includes two active incidents with identical text, a hidden recovered incident and disappearance after live recovery; native execution and physical-phone acceptance are not claimed for this change.
- Android is independently versioned 0.1.113 (114), with matching Russian/English notes. Debug and instrumentation APK assembly passed. The versioned APK manifest reports 0.1.113 (114), its bilingual notes match source, the updated UI string and notification class are present in DEX, and the APK signature matches the previous delivered version. The APK SHA-256 is `0e2430b123137ba11e5c5025adb43c890224ad72e3e8e3cbf1e124ed23674d6b`. Installing and reopening the Android app activates the change; no backend restart is required. Desktop/Python and running services are unchanged.

## 2026-10-19 — 0.2.16 protected-source release verification

- PR #40 merged normally at `d0401a31b28c1e4fb373b8fa847ed3228906bc0d` after all checks passed. Final main CI, packages and bilingual Wiki pipelines passed. Python 3.13/3.14 each collected 4,958 tests: 4,923 passed, 35 existing environment skips, 644 passing subtests and zero failures/errors. Desktop passed 394 contracts on Linux and 393 on Windows with one platform skip. Native Linux/Windows completed all 12 existing stages and separate same-chat composer acceptance, including retained draft/history, synchronized panes, concurrent-send protection, busy rejection and Escape. The Windows installer passed installation, reinstallation, update and relaunch.
- Final Linux DEB verified 7,548 inventory entries. Its isolated 0.2.15 → 0.2.16 installation, reinstallation, rollback and return retained synthetic data and active-generation octopus/management launchers. AppImage verified 6,418 entries with the existing six build-only omissions; Windows verified all 2,759 entries. Both match 76 application files, 24 backend files and all 440 project-wheel members. The clean wheel inventory contains 479 sources, includes the new composer script and records the protected commit with no modified sources.
- Android 0.1.112 (113) passed all 356 unit tests, lint, debug assembly and final Android 14/API 34 native acceptance: painted draft/sent/reopened thumbnails, legacy attachment label absent, draft removal retaining the sent image, visible authorization errors and retained quiet diagnostics. The first native attempt failed in instrumentation setup because its external report directory was unavailable, before launching the app. The software emulator also exhibited System UI ANR and repeated Bluetooth process aborts. After the environment was ready, the unchanged test passed; screenshots were inspected. Only the disposable AVD, its isolated ADB server and temporary AVD files were stopped/removed. The shipping APK exactly matches the tested file and retains signing-certificate continuity.
- GitHub release files, description and protected tag were read back; all seven server digests match local files. The actual 0.2.15 updater discovers 0.2.16 for DEB/AppImage/EXE and successfully downloads and verifies the DEB. The published English/Russian desktop and mobile Wiki pages contain the released versions. Gitea mirror verification and final publication are recorded in the local release receipt.
- This remains a preview: Windows is unsigned, Android uses debug signing, and physical Windows 11, phone and KVM acceptance are not claimed. The owner's installation and running services were not changed. Applying the desktop/runtime update requires application/runtime and chat restart, plus a separately managed gateway if its package is updated; Android changes activate after installing and reopening the APK. No subagents or external model API calls were used.

## 2026-10-19 — 0.2.16 release preparation and attachment label cleanup

- Android 0.1.112 (113) removes the redundant generated attachment line when structured attachments provide thumbnails/filenames. Exact matching Russian and English legacy suffixes are hidden in restored messages without rewriting stored text; messages without metadata and unrelated prose retain their fallback text. Added regressions for image-only messages, both languages, multiple filenames and preservation of unrelated text.
- Added the same native composer/profile acceptance to hosted Linux and Windows checks. Existing complete Python, desktop and native Android results are recorded below; final release checks, protected merge and package acceptance are pending.
- The first hosted Windows native run stopped at the existing onboarding-unlock fixture; its unchanged diagnostic rerun completed all 12 stages. The fixture previously waited only for rendered guide rows, while the initial status action could still clear the password field before re-enabling controls. It now also waits for the status action to re-enable the refresh control before entering the fixture password. Product logic and timeouts remain unchanged; assertions remain enabled and final CI is repeated.


## 2026-10-19 — Composer profile selection and Android attachment/reconnect fixes

- The composer profile/model label is now an accessible menu button in both chat surfaces. Selecting a verified profile activates its existing owner-managed worker and updates the same idle session. Session ID, transcript, workspace, draft and attachments are preserved. Pending work prevents switching, and actual profile changes rotate the approval generation so previous tool grants are not inherited, even when returning to the earlier profile. The new authenticated endpoint accepts only a profile name; revoked/unpaired clients, unverified connections and locked Vault states remain rejected.
- Locked-dependency Python verification passed both roots: 4,958 collected, 4,924 passed, 34 existing OS/environment skips, 644 passing subtests and zero failures/errors. Coverage: 47,321/79,777 statements and 13,302/28,594 branches. Desktop `npm ci` and all 394 contracts passed. The initial sandbox-only run could not bind existing loopback/socket fixtures; the unrestricted identical suite passed. Native Linux Electron acceptance against the real Web UI and synthetic profiles passed same-session switching, retained history/draft, busy-worker rejection and Escape. Its first fixture used the wrong history helper signature; the next run exposed a real Escape/focus issue, fixed before the final passing run. The menu screenshot was inspected. A later check found stale side-chat metadata during a main-chat switch; synchronized both surfaces, invalidated older side-chat loads and blocked simultaneous sends. The final native test held the profile response while submitting the other composer and confirmed no message was sent on an old route. Python/desktop were bumped once to 0.2.16 after the feature checks; 84 version/packaging/profile contracts and 11 focused desktop tests passed after the bump.
- Android now retains the server's existing user-upload metadata in DTOs, live/queued messages, optimistic updates and restored history. Selected images render inside the composer; sent images use a private cache and the existing authenticated, certificate-pinned upload route. Decoding runs off the UI thread with 256-pixel sampling and an 8 MiB automatic-preview cap. Non-image filenames and unreadable-image fallbacks remain visible.
- Only background/resume event-stream read timeouts may remain quiet for 15 seconds after returning to the activity. Their incidents remain in diagnostic exports, and an unrecovered outage is promoted after that foreground grace period. Ordinary foreground failures, authentication/TLS errors, model failures, send/write failures and unknown operation outcomes retain visible notifications. Existing visible incidents are not downgraded.
- Android 0.1.111 (112) passed all 353 unit tests with zero failures/errors/skips, lint and debug assembly. Tests cover live/queued/history image metadata, image-only messages, legacy-event compatibility, authenticated preview paths, size/MIME/path bounds, background/resume timing, recovery, persistent-outage promotion and preservation of genuine error notifications, including retention priority over quiet reconnect incidents. The initial build failed Compose lint on reading `StateFlow.value` inside composition; replaced it with `collectAsState`, without disabling lint. Final APK version, embedded notes, new DEX classes and signing-certificate continuity were verified. Native Android 14/API 34 acceptance passed actual painted draft/sent thumbnails, draft removal retaining the sent image, restored metadata/cache rendering, hidden quiet-timeout cards and visible authorization errors with both incidents retained in diagnostics. The first attempt was blocked by an emulator System UI ANR and first-install notification permission dialog; corrected the fixture permission setup and restarted only the disposable emulator System UI before the passing repeat. Screenshots were inspected. The verified APK was delivered successfully. The disposable emulator reported a shutdown SIGSEGV after its completed test and explicit stop; this was separate from application acceptance. Physical-phone acceptance remains separate.
- The preceding Mercury incident had an empty normalized response, zero tools/files and an incorrect successful task status. Raw upstream SSE was not retained. The owner explicitly deferred further Inception investigation and refused another key; no new provider calls or executor fix were made. Pending reproductions are kept only in ignored local context. No owner application/service was stopped or restarted and no release was published for these changes.

## 2026-10-19 — 0.2.15 final release verification

- PR #39 merged normally through protected main at `2fce60bdacd0c4db8bb8f372da38f8b0e570017e`, matching the reviewed tree. Final main CI `39301312279`, desktop packaging `39301312197` and Wiki deployment `39301312132` passed. Main Windows contracts initially failed with `EPERM` while deleting a temporary runtime-fixture directory; the exact failed job passed on rerun, with the original log retained and no assertions disabled.
- Hosted Python 3.13 and 3.14 each collected 4,945 tests: 4,910 passed, 35 existing environment/platform skips, zero failures/errors and 644 passing subtests. Python 3.13 covered 47,289/79,742 statements and 13,337/28,568 branches; Python 3.14 covered 47,283/79,736 statements and 13,280/28,568 branches. Skipped physical KVM/vsock/network capabilities are not accepted by these results. Desktop contracts passed 394 tests on Linux and 393 on Windows with one platform skip.
- Native Linux and final packaged Windows completed all twelve stages, including model deferral/direct OpenCode entry before any saved primary profile or running worker. Windows also passed installation, reinstallation, automatic update, retained synthetic data, packaged relaunch and uninstall on Windows Server 2025 x64. Linux 0.2.14 installation, 0.2.15 update/reinstall, rollback and return to 0.2.15 preserved the synthetic data canary and logical octopus PNG/management paths.
- Final Linux DEB matched all 7,552 snapshot entries. The extracted Windows installer matched all 2,758 source-manifest entries; the actual extracted AppImage matched its 6,422-entry inventory except the same six intentionally omitted build-only static/debug files. Both matched all 76 application sources, 24 backend sources and 439 project-wheel members. The clean 478-source wheel inventory matches protected main, and its SHA-256 exactly matches the Linux runtime wheel. Temporary untracked build linkage was removed before the final clean wheel build; the wheel bytes remained identical.
- Android remains independently versioned 0.1.110 (111), with unchanged source. Its fresh APK passed 335 unit tests, lint, assembly and signing-certificate continuity checks. Previous native Android 14/API 34 notification/copy-feedback acceptance of the same source passed. A fresh repeat on a software-only emulator was not accepted: boot/lock delays and a System UI ANR prevented completion; a 240-second timeout and the stopped repeat are recorded separately. No fresh native or physical-device pass is claimed. Only the task-owned emulator and AVD were disposed; the phone and owner services were untouched. APK delivery was confirmed.
- GitHub preview `v0.2.15` publishes seven verified files from protected main: Linux DEB/AppImage, Windows EXE, Python wheel, Android APK, build manifest and checksums. The published tag, bilingual body and every server SHA-256 match local shipping bytes. Actual published English/Russian Wiki pages contain the new setup actions. The unmodified 0.2.14 updater accepts actual published metadata for DEB, AppImage and Windows formats. Gitea mirror readback/publication is recorded in the final local release receipt.
- Windows remains unsigned and Android debug-signed; physical Windows 11, phone, VPN switching and KVM hardware acceptance are not claimed. The owner's working 0.2.14 installation was not updated or restarted. Applying this release requires restarting the updated application/runtime and then the chat, plus a separately managed gateway if its package is updated. No subagents were used.

## 2026-10-19 — First-run model deferral and Linux launcher, 0.2.15 preparation

- Added explicit model-later and OpenCode entries after Vault unlock, with no primary-model/worker/first-task prerequisite. The owner IPC retains exact main-frame, current-window, origin and Vault/interface-lock guards. The native tour now requires a first-run OpenCode entry with no saved profile or worker and a real settings/main-window transition.
- Linux menu metadata retains the active-generation path for the octopus PNG and management action, including update/rollback and the legacy SVG/management fallback. The existing installation launcher remains the application entry.
- Targeted checks passed: 98 desktop contracts and 93 Python provider/installer/package regressions. Initial fixture assertions were corrected to exercise mutable window identity and logical active-generation paths; the legacy management fallback still resolves to the expected retained snapshot. An earlier temporary-filesystem quota failure was infrastructure-only; the complete targeted Python selection passed with isolated workspace temporary data. Full locked suites, native acceptance and final packages are pending at this entry.
- The first full local run used an invalid temporary path inside the repository and was repeated with `/tmp`. The corrected run collected 4,944 tests: 4,909 passed, one legacy PTY test failed, 34 skipped and 644 subtests passed. Its repeated failure was traced to sending input before the terminal entered raw mode (canonical input and echo were still enabled, with no output received). The fixture now waits for the kernel raw-mode transition instead of assuming a fixed 0.5-second startup delay; CLI implementation and timeouts are unchanged. The corrected complete PTY module passed all 23 tests without changing CLI code or increasing timeouts. Full CI on the preceding source passed both Python versions; final CI is repeated for this fixture correction.
- The corrected full local Python run passed: 4,944 collected, 4,910 passed, 34 existing environment skips, zero failures/errors and 644 subtests. Coverage: 47,283/79,734 statements and 13,281/28,568 branches. Subsequent hosted Python 3.14 exposed two independent races: background Git maintenance removed a lock during private baseline copying, and `/proc` disappeared between a child-existence check and read. Private snapshot repositories now disable automatic maintenance/GC before committing; source repository configuration is unchanged. The process-cleanup fixture reads `/proc` once and accepts only an absent/gone process or a zombie as stopped. Tests and security assertions remain enabled. All 30 snapshot and process-runner regressions passed after these corrections.

## 2026-10-19 — Model connection-check timeout

- Increased the HTTP/HTTPS socket timeout and response-read deadline from 10 to 30 seconds. Added four delayed-response regressions covering both transports, accepting a 12-second response and retaining the timeout after 30 seconds.
- Targeted provider, catalogue, runtime and owner tests plus `factory/tests` passed: 223 passed, 10 existing idle-application fixtures skipped because they require `running_state`. The initial restricted-sandbox run could not bind seven existing local HTTP fixtures; the same complete selection passed with loopback access. A separate real local HTTP response delayed by 12 seconds passed the ordinary connection check.
- The owner explicitly requested immediate Windows preview publication and deferred further release verification. No additional test suites or native acceptance were run for this timeout change. Existing service processes and the installed owner application were not changed.

## 2026-10-19 — Diagnostic copy feedback and Russian connection wording

- Replaced the incorrect Russian transport-error phrase with “Не удалось связаться с сервисом. Причину сбоя определить не удалось.” in the canonical catalogue and regenerated Android, desktop and Web copies. This changes wording, not classification or retry behavior. Existing saved notification descriptions retain their original text. Clarified both Wiki languages: card long-press and the notification-center send action use the overall retained report; individual-incident selection is not implemented.
- Added a short localized Android confirmation after the diagnostic snapshot is copied to the clipboard. Android 0.1.110 (111) passed all 335 unit tests with zero failures, errors or skips, lint and debug assembly. The real Android 14/API 34 test passed an 850 ms card press, clipboard contents containing both fixture incident IDs and a visible confirmation event, followed by acknowledgment, reopening, report preview/cancellation and clearing. No email was sent.
- The shared Python diagnostic contracts passed 16 tests and 14 subtests; the desktop diagnostic contract file passed. Generated-client consistency and diff checks passed. Packaged DEX inspection rejected the first wording candidate, which had compiled before catalogue regeneration; rebuilt after regeneration and verified both the corrected error message and copy confirmation inside the final APK. Its signing certificate matches the previously delivered build. Physical-device confirmation remains separate from this emulator check.

## 2026-10-19 — Android notification center duplicate-identity regression

- Reproduced the reported bell-button crash on the previously delivered Android 0.1.108 (109) in an isolated Android 14/API 34 emulator. Two distinct incidents with the same displayed kind/text caused `IllegalArgumentException: Key ... was already used` in Compose `LazyColumn`. A recovered incident followed by a new outage is a valid source of this shape; text is not an incident identity.
- Changed notification row keys to the existing persistent incident ID. History, acknowledgment and recovery behavior remain intact; no data reset or desktop/server restart is needed. Added regressions for recurrence after recovery, identical descriptions from independent owners, serialization/restore, translation and acknowledgment retaining identity.
- Android 0.1.109 (110) passed all 335 unit tests with zero failures, errors or skips, lint and debug APK assembly. The same native test then passed real taps through opening both identical descriptions, acknowledgment without recovery, closing/reopening, report preview/cancellation, return to notifications and clearing. The original APK crashed under that test. Early fixture runs used the wrong language composition context; a later synthetic tap raced a dialog transition. Corrected the fixture context and real down/up gesture timing without weakening product assertions.
- The new APK's signing certificate matches build 109. This verifies an in-place emulator update, not acceptance on the reporter's physical phone; no phone log was available. The test does not send email. Application data and running desktop/gateway services were not changed.

## 2026-10-19 — Voluntary diagnostic support reports: implementation checks

- Real browser acceptance passed preview cancellation without download, byte-equivalent reviewed/downloaded JSON, fixed recipient and 390-pixel mobile layout; its screenshot was visually checked. The first test-page collector had an unparenthesized object-literal arrow return and failed to bind its button; corrected the fixture without changing product behavior. The bilingual Wiki strict build/link/import checks and all 76-page/178-diagram browser checks passed.
- Added a generated shared support contact, a bounded allowlisted JSON report format and review-before-handoff UI. Desktop uses a trusted main-frame IPC, owner-selected new JSON file and fixed-recipient mailto composer; Web uses a user-requested download and explicit email link. Android uses a dedicated private cache/FileProvider path and a system sharesheet with temporary read permission. No SMTP credentials, upload server or automatic email delivery are added; existing copying remains available.
- The first native test preload omitted the existing production notification snapshot API, so its report could exercise server data but not the seeded local error. Added the missing fixture API and explicit local-availability/owner/code assertions; the repeated real Linux tour passed all eleven stages with local data included. Product code and the already delivered Android APK were unchanged.
- Initial desktop run passed 381 of 382 tests; one existing completion-sound fixture extracted the newly inserted support handler as its callback. Kept the existing assertion and placed the new independent handler outside that fixture boundary. The repeated desktop suite passed all 384 tests with zero failures/skips, including eight new privacy/bounds/cancellation/stale-owner/exclusive-file/fixed-recipient checks. Final desktop contracts passed all 386 tests after closed-preview, offline-collection and missing-reference regressions were added.
- Shared catalogue/packaging tests passed 22 tests and 14 subtests. The full local Python run passed all 305 files: 4,939 collected, 4,904 passed, 35 existing environment skips, zero failures/errors and 644 passing subtests. Final Android unit/lint passed 333 tests with zero failures/errors/skips. The real Linux native tour passed all eleven stages, including exact reviewed/file equality and the fixed handoff destination; Windows acceptance is still pending at this entry. Added a required real Electron preview/cancel/reviewed-file/recipient stage with a synthetic save picker and intercepted email handoff. These checks intentionally do not send email or establish physical mail-app/Tuta delivery.

## 2026-10-19 — Error diagnostics 0.2.13 published and verified

- PR #38 merged normally through protected GitHub main at `78ebb62a7796d9e3d7c0995f951336b72fd3b3d9` after all 12 checks passed. The protected source tree matches reviewed head `f05eb1e1b30fb446a92224c25683cac86afffb73` and Windows build merge `6a373f646eab51d76d64d9d504cbed46622a39d6`. The final catalogue/generator UTF-8 regression fixed the observed Windows cp1252 import failure; no checks were skipped to obtain success.
- Hosted Python 3.13 and 3.14 each collected 4,938 tests: 4,903 passed, 35 existing OS/environment skips, zero failures/errors and 644 passing subtests. Desktop contracts passed 376 tests on both operating systems. Android passed 328 unit tests, lint and a clean debug build. Existing skips do not establish physical KVM/vsock/network-namespace or live-provider acceptance.
- Final Linux and Windows candidates passed all ten native UI stages, including real typed notification display, acknowledgment independent of recovery, rejection of unrelated-owner recovery and rendering of owned recovery. Windows passed installation/reinstallation, updater/relaunch and live OpenCode observation/right-panel acceptance. Linux 0.2.12 to 0.2.13 installation in a separate synthetic profile retained encrypted Vault data, preferences and the previous generation. Chromium sandboxing remained enabled; no owner installation or service was changed.
- The final AppImage matched 6,412 shipping-manifest entries with the same six build-only omissions; the extracted Windows installer matched all 2,754 entries. Both matched all application/backend sources and all 437 project-wheel members. All 476 clean wheel-source inventory entries match the reviewed public source; the Linux runtime wheel SHA exactly matches the shipping wheel. The Windows installer is unsigned. A transient TLS source-download timeout during Linux packaging was retried without source changes or weakened integrity checks.
- GitHub and Gitea published the same 11 verified files: Linux DEB/AppImage, Windows EXE, Python wheel, Android APK, English/Russian architecture PNG/SVG, build manifest and checksums. Titles are English and descriptions have matching English/Russian meaning. Published descriptions, tags, inventories and hashes were read back. Actual public GitHub PNGs were visually inspected; all four diagram objects on each host match shipping bytes. The real 0.2.12 updater discovers all three desktop formats and downloads the published DEB with the tested SHA-256. Release: [v0.2.13](https://github.com/Endorpheen/Igorek-Harness/releases/tag/v0.2.13).
- Main Wiki build/deploy `39227223983` passed. Actual published-site acceptance checked 76 pages, 178 Mermaid diagrams and all 76 language alternatives with zero errors; both searches, themes, same-page language navigation and mobile layout passed. Imported technical content remained unchanged. [English diagnostics](https://endorpheen.github.io/Igorek-Harness/10-Error-Diagnostics/) and [Russian diagnostics](https://endorpheen.github.io/Igorek-Harness/ru/10-Error-Diagnostics/) are available.
- Android 0.1.107 (108) APK signature, manifest and bilingual notes were checked and delivery was confirmed. This remains a preview: Windows is unsigned, Android uses debug signing, and physical Windows 11, physical VPN switching and KVM hardware acceptance are not claimed. Applying the backend/tool update requires restarting the updated application/runtime, a separately managed gateway when present, and then the chat. No subagents were used.

## 2026-10-19 — Unified error diagnostics: implementation and local checks

- Added the shared versioned catalogue, cross-language fixtures, deterministic transport classification, bounded retry/delivery policy, scoped recovery and bounded local incident snapshots. Kept legacy error fields, event families, approval signatures and task execution ownership. Unknown write outcomes use read-only admission receipts without automatically resubmitting messages. Added desktop/web/Android presentation and storage regressions, Android pinned local TLS fault tests, and native Electron notification/recovery acceptance.
- Desktop contracts passed 376 tests with zero failures or skips. Android unit/lint passed; 328 unit tests, zero failures/errors/skips. Shared classifier/transport regressions passed 21 tests and 14 subtests; bridge/security regressions passed 108 tests. The full Python run collected 4,937 tests: 4,902 passed, one failed, 34 existing environment skips, zero errors and 644 passing subtests. Its failure exposed an infrastructure-only metadata field receiving an ordinary diagnostic reason. Preserved the legacy field contract and added `diagnostic_failure_reason`; all 24 tests in that module then passed. The repeated full run passed all 305 files across both roots: 4,937 collected (99 under `factory/tests`, 4,838 under `tests`), 4,903 passed, 34 existing skips, zero failures/errors and 644 passing subtests. Line coverage was 47,281/79,722 (59.31%); branch coverage 13,276/28,560 (46.48%). An additional persisted-status/error observer regression passed with the targeted 62-test/14-subtest set after that run. Hosted final platform/package acceptance is pending at this entry.
- The first native Windows package job exposed a platform-default cp1252 decode of the UTF-8 catalogue (11 related contract failures, 31 passes and three Linux-only skips). Catalogue/history reads and generation now specify UTF-8 explicitly; a regression forces a non-UTF-8 default without changing the host locale. Windows packaging/native acceptance must be rerun on this correction.
- The first hosted secret check flagged the literal synthetic receipt idempotency key in two test fixtures. Added an exact-value, exact-path detector allowlist for that non-credential; scanning remains enabled for both the tree and all history. The local pinned detector then passed the public source tree.
- Earlier checks caught an intentionally changed unterminated-stream expectation, a desktop extraction fixture missing the new contract, additive HTTP-envelope expectations and an unexpected-error fixture patched at the wrong service boundary. Corrected fixtures without removing safeguards or skipping failures. Incomplete streams now reject partial tool calls; explicit finish-reason compatibility remains covered.
- Real native Electron/Linux with Chromium sandboxing passed all ten required UI stages, including typed notification, read acknowledgment, unrelated-owner rejection and stable owned recovery; the actual notification screenshot was visually inspected. Synthetic provider responses and separate temporary runtime data were used. Android debug APK 0.1.107 (108) passed signature, package version and bundled bilingual release-note checks; delivery was confirmed separately.
- Strict bilingual Wiki build and imported-source/link checks passed: 77 HTML pages, 6,097 local targets, 1,692 anchored source citations and zero errors. Added English/Russian diagnostics chapters and matching editable SVG/PNG before/after architecture diagrams. Original imported technical pages remain byte-for-byte unchanged. Native UI, hosted Windows, signed distribution, real VPN transitions and physical-device acceptance are separate from these local synthetic results.

## 2026-10-19 — Clickable Wiki source citations

- Reproduced exported source references rendered as inline code, including entire backtick-wrapped links, missing destinations, grouped citations, plain file/line references and unmatched label backticks. Normalize only presentation during the Wiki build; imported page bytes and source-commit provenance remain unchanged. Literal fenced examples, mixed inline examples and existing external links remain intact.
- Eleven documentation regressions passed, including the reported Electron overview in English and Russian. Strict local build and completeness/link checks passed: 75 HTML pages, 5,864 local targets and 1,692 source links. The build checker now rejects source citations rendered as unlinked inline code. All required hosted checks passed; PR #37 merged normally at `c436ec3d6c4d4b96d7e16ff09447fb884c101251`. Main Wiki build and Pages deployment `39179883513` passed. All 74 published pages match the expected 2,398 source-file links, including 1,692 line-anchored citations, with no errors. A real browser click from the reported Russian overview opened the correct GitHub file and line range. No application logic, version or installation changes; no restart is required.

## 2026-10-19 — Wiki publication accepted

- PR #36 merged through the protected GitHub main branch at `4f0549d39799b2fc54b32fd01ce1cdf19d2264e7` after all required checks passed. Wiki workflow `39171238994` passed both the full build/browser job and Pages deployment. The unchanged Python 3.14 process-cleanup test initially hit a `/proc` existence/read race; its isolated check and the full CI rerun passed without changing or skipping the test.
- Verified the actual public site at https://endorpheen.github.io/Igorek-Harness/ and its Russian `/ru/` route. Final public browser acceptance covered all 74 pages, 176 rendered diagrams and 74 language alternatives with zero errors, English/Russian search, theme switching, same-page language navigation and mobile navigation/layout. The owner also confirmed that the site opens.
- The account Pages site had an inherited, nonresolving custom domain. With separate explicit owner approval, removed that setting; the Wiki now uses its standard HTTPS GitHub Pages address. No source CNAME remained. Canonical Wiki source was fast-forwarded and pushed to main; application code, versions, installations and services remain unchanged. No restart is required.

## 2026-10-19 — Bilingual Wiki preparation

- Imported all 36 technical pages and 87 original Mermaid diagrams per language from the English/Russian exports, preserving original page bytes. Excluded personal export wrappers and raw service JSON. Added paired current-behavior notes for 0.2.12, clearly retaining obsolete copy/transfer descriptions as historical reference, and two current architecture diagrams.
- Added isolated, hash-locked MkDocs Material/static-i18n tooling, localized navigation and same-chapter language links, bilingual search, themes, mobile navigation, the existing angry octopus mark and shared diagram styling. Build-time repairs cover malformed export link/diagram syntax without rewriting imported technical prose.
- Six importer regressions passed, covering partial imports, retained languages/pages, manual-edit conflicts, original bytes, source-commit provenance and unsafe paths. Thirteen publication regressions passed; repository-root references are accepted only inside the Wiki and only for public inventory members. Ordinary broken links and private local targets remain rejected. Publication contracts and Ruff passed.
- Strict local build and all built local links/assets passed. Actual browser controls passed English/Russian search, theme switching, same-chapter navigation and mobile menu/layout. All 176 diagrams rendered during the full page traversal; final CI repeats that traversal with the same control checks. Earlier harness failures inspected closed Shadow DOM as ordinary DOM or targeted hidden labels; actual native DOM inspection and visible controls replaced those assumptions. No diagram or assertion was removed to obtain a pass.
- The owner approved first public deployment after reviewing the prepared design. Pages deployment remains gated to the approved main branch and its repository variable. No application/runtime logic, installed app, application version or service was changed; no application restart is needed.

## 2026-10-18 — OpenCode observation 0.2.12 publication

- Published preview `v0.2.12` on GitHub and Gitea after all 11 checks passed and PR #35 merged normally through the protected branch at `d0e210bbdb9db152c312dc2d089f0cf4f0b520d9`. The protected tree matches canonical product `c7065327459baac71103a21b6d3019c75a7a1756` and actual Windows CI source `88c63fb55083058048bf9e3131f7b424ab3944fa`. Documentation and instruction-only follow-ups do not change the frozen product.
- Fresh Linux runtime, snapshot, DEB, AppImage and wheel were built from the protected commit, retaining the previous release's optional Linux bindings. All nine native Linux UI stages and real OpenCode read-only observation/right-panel acceptance passed with Chromium sandboxing enabled. A synthetic 0.2.11 to 0.2.12 update retained encrypted Vault connections, release-note preferences and the previous generation. Physical KVM checks remain unaccepted.
- The extracted AppImage matched 6,406 shipping entries with the same six build-only omissions; the Windows installer matched all 2,748 entries. Both matched all application/backend sources and all 433 project-wheel members. The release wheel hash exactly matches the Linux runtime wheel. Initial packaging attempts hit temporary-storage quota; a separate retry used an incorrect builder working directory. Corrected final builds and verification passed without source changes or relaxed assertions.
- Both releases contain ten identical verified assets: DEB, AppImage, Windows EXE, wheel, Russian/English PNG and editable SVG architecture diagrams, build manifest and checksums. Published bodies, inventories, tags and image bytes were read back; the actual public English/Russian PNGs were visually checked. The old 0.2.11 updater discovers all three platform formats and downloads the published DEB with the exact tested SHA-256.
- Release titles are English on both hosts, including corrected 0.2.4 through 0.2.11 titles; existing descriptions, tags and binaries were preserved. The repository instructions now explicitly require English release titles. Descriptions remain bilingual. Windows remains an unsigned Server 2025 x64 preview with physical Windows 11 acceptance pending; Android remains unchanged. No owner installation or service was replaced or restarted. Users must restart the updated application/runtime and then the chat.

## 2026-10-18 — OpenCode observation 0.2.12 Windows and hosted acceptance

- All 11 checks passed on public PR #35 head `858fd50bc78ac6853e20785d38c6a5a9fe2b1535`. Its source tree `c74a20273f43eed7a16ee8f7efbd1c8a3f9c3da7` matches canonical implementation `c7065327459baac71103a21b6d3019c75a7a1756`. The PR remains a draft; no release was published or installed on the owner's machine.
- Both Python roots passed on Python 3.13 and 3.14: 4,905 collected, 4,870 passed, 35 existing OS/environment skips, zero failures/errors and 630 passing subtests per interpreter. This includes the compact-prompt regression added after the initial local full run. Desktop contracts passed on Linux and Windows; the final local desktop suite contains 369 passing tests. Native Linux first-task/restart and unchanged Android unit/lint/debug build checks also passed.
- Actual Windows Server 2025 x64 file/Vault/native OpenCode contracts passed 42 tests with three existing Linux-only skips. The real OpenCode fixture exposed a completed write, an active question and public progress text while the assignment was still running, and refused observer cancellation. Owner answers, direct source editing, no project copies/snapshots and outside-write denial remained intact.
- The Windows NSIS candidate passed installation, all nine native first-task/restart UI stages, reinstallation and the updater/relaunch acceptance. The installed runtime's OpenCode fixture additionally confirmed the live right panel, running progress and observer read-only behavior. Tests used synthetic loopback provider responses and temporary projects; physical Windows 11 and real-provider acceptance are not claimed. The installer remains an unsigned preview candidate.
- No owner application, runtime, gateway or chat was stopped, replaced or restarted. Applying this backend/tool change later requires restarting the updated application/runtime and then the chat. No subagents or other agent tasks were launched.

## 2026-10-18 — Read-only OpenCode observation (0.2.12 source)

- Implemented detailed observation in the existing start/status tool and monitor. Explicit status refreshes the current session, retains bounded current/recent actions, public partial/completed text, timestamps and pending owner input, and identifies stale/unavailable/historical data. It reuses the journal/card ingestion path to preserve once-only delivery. Missing run IDs select only the current chat's assignment. No extra UI or model polling task is added.
- Removed model cancellation from both the advertised schema and Python/Electron handlers; invalid cancellation is rejected without issuing an interrupt. Native OpenCode owner controls remain available. An observed owner interruption closes expired chat cards and releases the main-chat write guard without answering forms or sending controls. Full and compact prompts explain one-shot status reads, stale/truncated data and the distinction between a completed tool call and successful checks.
- Added regressions for live progress through actual main-chat tool dispatch, duplicate-free automatic delivery, stale fallback without relaunch, missing legacy progress, session scoping, bounded/redacted payloads, omitted reasoning/commands/raw outputs, owner interruption, Unicode clipping and provider tool IDs reused across messages. Targeted Python verification: 62 passed and 3 subtests. Full desktop verification: 369 passed. Locked dependencies, Ruff, publication contracts and diff checks passed. No tests or skips were weakened to obtain a green result.
- Both Python roots ran with coverage: 4,904 collected, 4,869 passed, 34 existing skips, 630 subtests, zero errors and one timeout in test_real_terminal_switch_reply_ctrl_c_and_other_client_survival[True-False-legacy] while waiting for profile selection to change. The entire unchanged CLI module passed on isolated rerun (23/23). Full-run coverage was 46,821/79,214 statements and 13,097/28,338 branches. The final compact-prompt contract was also covered in the targeted run after that full run; failure and rerun evidence are preserved locally.
- Native Linux OpenCode 2.0.27 plus the actual Electron right panel passed with a synthetic provider: public progress text and the completed write/current question were exposed before task completion; cancellation was refused while work continued; exact owner replies, source edits, no project copies/snapshots and outside-write denial remained intact. This verifies the native Linux/API contract, not a real-provider response or new Windows installer. Windows native fixtures now require the observation assertions, but new Windows acceptance has not been run here.
- Updated Russian/English PNG and editable SVG architecture diagrams to compare limited 0.2.11 status with 0.2.12 observation and owner-only stopping. Release notes cover observation and the already prepared unified octopus branding. The version remains the previously prepared 0.2.12; no second bump. No release or owner installation/restart was performed. Deployment requires a new desktop build and restarting the application/runtime, then the chat.

## 2026-10-18 — Unified desktop octopus branding (0.2.12 source)

- Replaced the desktop launcher SVG, application/window PNG and web UI mark with the same original octopus artwork already used by Android. Both SVGs embed the identical original image and apply its existing blue-channel alpha matrix with the desktop lime/mint gradient. The Android image and Android code are unchanged. Existing image references also update assistant avatars, favicon and welcome screens without layout or runtime logic changes.
- Rendered the SVG into the 512×512 RGBA application icon with native Electron/Chromium and visually checked the actual sidebar HTML/CSS at 34 px. The preview used isolated browser data and a synthetic local static server; it is a source-render check, not installation or physical Windows acceptance. The first hidden-window capture returned UnknownVizError; waiting for a visible native frame corrected the preview harness without weakening Chromium sandboxing.
- Locked dependency synchronization and npm installation passed. All 365 desktop contracts passed. Both Python roots ran with coverage: 4,898 collected, 4,863 passed, 34 existing OS/environment skips, 630 subtests and one scheduler lifecycle timing failure in test_cli_launch.py. The entire unchanged module passed on isolated rerun (23/23); no production scheduling code, assertions or skips were changed. Full-run statement coverage was 46,757/79,154 and branch coverage 13,067/28,306. Timing failure and rerun evidence are preserved locally.
- The running OpenCode status contract was also checked separately with its unmodified Python handler and a synthetic desktop API: explicit status returns run_id/status/result and does not request a fresh desktop snapshot; intermediate completed text and owner cards are automatically journaled/published, while the status result stays empty until completion. Rich observation remains a design proposal, not an implemented 0.2.12 feature.
- Python/desktop source versions and matching desktop release notes are prepared as 0.2.12. No new installer was published or applied to the owner installation, and no owner runtime/gateway/chat was stopped or restarted. A new desktop build/update is needed to display the changed installed launcher icon. No subagents were launched.

## 2026-10-18 — OpenCode 0.2.11 release acceptance and publication

- Published the preview on [GitHub](https://github.com/Endorpheen/Igorek-Harness/releases/tag/v0.2.11) and Gitea after all 11 required checks passed on `ec81cf0bc2af1f69effe557357e32a79d0971a44`. Protected PR #34 merged normally at `bdfd050f5ab178f672c7c9f1a0b4d5591199f229`; its tree `3c0edfe19ba74d8d0d6e3372d2d890b746cca944` matches canonical clean source `92f887030bd4d00f6b6b31445b3f6538eb581842` and the Windows CI merge `d87bf154e77d02dd69c30d3e05ba4e8b9ae5a20e`.
- Both Python roots passed on Python 3.13 and 3.14: 4,898 collected, 4,863 passed, 35 existing OS/environment skips, zero failures/errors and 630 subtests per interpreter. The roots contain 99 and 4,799 collected tests. Python 3.13 covered 46,743/79,160 statements and 13,114/28,306 branches; Python 3.14 covered 46,739/79,154 statements and 13,057/28,306 branches. Skipped native OS, KVM/vsock/namespace, TUI and live-provider cases are not counted as passes. The local Python 3.14 run passed 4,864 with 34 environment skips and 630 subtests.
- Desktop contracts passed on Linux and Windows (365 tests locally). Android unit/lint/debug build passed without Android source/version changes. Native Linux first-task/restart acceptance passed its nine stages with Chromium sandboxing enabled. Native Windows file/Vault contracts passed 42 tests with three existing Linux-only skips.
- The real Windows installer passed installation, reinstallation, native first-task/restart, automatic installer update and relaunch. Its nine baseline native UI stages passed. OpenCode on both operating systems proved nonblocking execution, live original-project edits before completion, exact typed owner form/question replies, internal relative/absolute file symlinks, denied outside writes, the existing live right panel and absence of project copies or internal OpenCode snapshots even when project configuration enables snapshots.
- A synthetic 0.2.10 → 0.2.11 DEB update retained encrypted Vault credentials, release-note preferences and the previous generation. The old public 0.2.10 updater discovered 0.2.11 for DEB, AppImage and Windows and downloaded the actual public DEB with the same tested SHA-256. Owner installations, runtime/gateway processes and chats were not stopped, installed over or restarted. No agent tasks were launched.
- The actual AppImage matched 6,406 shipping manifest entries; its six unchanged build-only omissions are recorded in the acceptance report. The actual Windows installer matched all 2,748 manifest entries. Both matched every application/backend source and all 433 project-wheel members; private data was absent. Clean optional Linux socket bindings used the existing cached CPython 3.14.4 sources after all five pinned SHA-256 values were verified, avoiding a source-download TLS timeout without changing HTTPS validation.
- Both published releases contain the same ten assets, matching sizes and hashes: DEB, AppImage, Windows EXE, Python wheel, Russian/English PNG and editable SVG architecture diagrams, build manifest and checksums. Descriptions, actual tag targets, asset inventories and published image bytes were read back. Published Russian/English GitHub PNGs were visually inspected; Gitea PNG/SVG downloads match the same verified image hashes.
- Tested desktop environments are Ubuntu 24.04 CI, Ubuntu 26.04 x86_64 and Windows Server 2025 x64. Windows remains an unsigned preview; physical Windows 11 acceptance is pending. Windows commands use cmd.exe and x64 children; PowerShell/x86, KVM and host-services MCP are outside Windows support. Android remains 0.1.106 (107), with no new APK delivery. Applying the backend/tool update requires restarting the application/runtime and then the chat; no owner restart was performed during verification.

## 2026-10-18 — Direct OpenCode assignments (0.2.11 in preparation)

- Reproduced the reported 0.2.10 admission failure at the project-snapshot boundary. The old whole-project copy/transfer design is replaced by explicitly selected direct project execution; legacy snapshot utilities and their compatibility tests remain unchanged and are not called for new assignments.
- Targeted Python delegation regressions: 22 passed, including no-copy admission with a 10 GiB sparse Linux file, internal relative/absolute links, direct cancellation, legacy restart recovery, active-writer exclusion, read-only normal/forced/pending tool dispatch and unchanged unrelated project execution.
- Initial fixture failures were corrected: a nonexistent registry test filename collected no tests; new executor fixtures initially lacked their artifact directory. Sandbox-denied local socket fixtures were rerun with process permissions rather than skipped. The previous focused set passed 64 tests with 6 existing Windows-only skips and 3 subtests; final broader verification is pending.
- Native Linux OpenCode 2.0.27 API fixture passed: original file visible while its question was pending, exact typed form reply, native question round trip, relative/absolute source links, external write denial, no project/baseline repository and no OpenCode snapshot store despite project `snapshots:true`. This is a real process with synthetic provider responses; it does not establish real-provider or native-panel acceptance. An initial npm registry TLS reset was bypassed using the previously SHA-512-verified pinned binary cache, without weakening HTTPS validation.
- RU/EN editable SVG and matching PNG architecture diagrams now compare copy/transfer with direct original-project execution and the runtime read-only guard. Both PNGs visually inspected.
- Initial Windows native admission failed before session creation because the synthetic project was inside AppData/Local/Temp, which the normal original-project selector rejects. The fixture now creates and cleans a separate temporary project under the user profile; production path restrictions are unchanged. The next fixture run exposed a missing parent directory after separating project/service roots; recursive creation fixes that setup error. Further Windows diagnostics confirmed the project-selector rejection: pytest overrides HOME/USERPROFILE with a temporary AppData path, so os.homedir() still selected protected application data. The fixture now uses the OS account profile from os.userInfo().homedir for its temporary project; path restrictions remain unchanged. Synthetic startup diagnostics retain the exact exception and selection inputs. The corrected Windows native API step passed and the installer built; native Electron acceptance then reached fixture cleanup after the OpenCode assertions and failed at synchronous removal with EPERM. Cleanup now awaits asynchronous removal with bounded retries so native handles can close before deleting only the synthetic project. Final Windows execution, hosted Python coverage, clean installers and publication for this source revision are pending. Complete local desktop tests passed (365 tests), native Linux baseline UI passed nine stages, and a synthetic 0.2.10 → 0.2.11 update retained Vault/settings/previous generation. No owner app/service was stopped, installed over or restarted; no agent tasks were launched.

# Test results

## 0.2.10 preview — independent OpenCode delegation

Desktop host execution can start one OpenCode assignment in a separate sandbox
and an independent source snapshot, while the main chat remains available.
All forms and permission requests go directly to the owner through existing
chat input cards; no model-facing answer or permission-grant operation exists.
The existing right panel opens the exact assignment session, named
`Поручение: <title>`. Manual sessions retain their service, grants and navigation.
Source snapshots retain dirty tracked and nonignored untracked files, exclude
private control data, and refuse symlinks/submodules. Result transfer compares
changed files to the captured baseline and refuses conflicts before writing.
A durable receipt schedules one result turn only after the current chat turn.
Restarted unfinished assignments are interrupted, never automatically relaunched.

Initial targeted checks: **45 Python passed**, followed by **192 passed and
5 subtests passed** across bridge/history/UI integration. New tests cover
independent dirty snapshots, outside symlinks, whole-transfer conflict refusal,
idempotent starts, session ownership, owner-only answers, question reconciliation,
automatic application and deferred one-time result delivery. Profile tests
confirm delegated grant creation/revocation does not invalidate the manual grant.
First full isolated Python roots: **4882 collected, 4850 passed, 32 skipped,
0 failures/errors, 630 passing subtests**. Existing hardware/platform skips are
retained and are not counted as physical KVM or Windows acceptance.
First complete desktop suite: **361 passed**; a further native-view regression
was then added for preserving manual saved navigation when switching services.
Final isolated Python roots: **4883 collected, 4852 passed, 31 skipped,
0 failures/errors, 630 passing subtests**. Statement coverage is
**46612/78576 (59.32%)**; branch coverage is **13016/28106 (46.31%)**;
combined coverage is **55.89%**. Skips comprise 17 unavailable/opt-in KVM,
vsock and image-builder checks, 10 existing idle-application lifecycle cases,
3 Windows-only filesystem/Git contracts and 1 live provider regression without
its optional API key. These are not reported as physical acceptance passes.

Final desktop suite: **363 passed, 0 failures/skips**. The intermediate run
with the added navigation test exposed a missing navigation parent directory
(**361 passed, 1 failed**); the service-specific navigation writer now creates
its own parent directory, and the regression passes without weakening it.

The final packaged Linux runtime passed all **9 maintained native stages**,
including first task, lock/unlock, full restart, continued chat, guided demo and
navigation, plus existing history/sources/Whats-New checks. A new real Electron
form check verifies trusted native submission, exact request routing, boolean,
integer and multiselect preservation, and omission of inactive conditional
fields. Its first screenshot ran before Chromium committed a frame
(`UnknownVizError`); waiting for animation frames and paint fixed the fixture.

A separate actual OpenCode/Electron check exposed an unsupported legacy
workspace URL (`Unrecognised route`). The bundled web router accepts only
server-scoped session links. The integration now builds that route centrally;
a regression covers it, and a real right-panel view displayed the assigned
session, native question, submitted answer, tool execution and final result.
The synthetic-provider checks also verified denial of shell writes to the
original source and successful application from the independent copy.

The first runtime build used a tool version without the pinned CPython download;
the existing pinned build tool completed the fresh build. Staging correctly
refused an earlier runtime after the input-cancellation source changed; it was
rebuilt rather than overlaid. The final Linux snapshot and actual Debian payload
match **all 7538 inventory entries**, with zero missing or mismatched files.
Version **0.2.10**, preview, local candidate only. No published assets were
replaced and no owner application/runtime was installed or restarted. Android
was unchanged. Windows and physical KVM acceptance were not performed.

Actual installed OpenCode **2.0.27** passed a synthetic-provider sandbox check:
nonblocking admission, the native question tool's form/reply roundtrip, writes
inside the copy, refused shell writes to the original directory, successful
execution outcome, and conflict-free result application. No real model account
or owner workspace was used. Two earlier fixture attempts failed because the
fixture assumed v1 names/arguments (`bash`/`filePath`); they were corrected against
actual advertised v2 schemas (`shell`/`path`). An initial restricted-sandbox
network run produced **19 passes, 1 failure and 25 errors** from unavailable
sockets, followed by the successful elevated temporary-socket target run.
One attempted test command referenced an absent history test module and collected
zero tests; the corrected integration command used the existing history module.
No test expectations or sandbox protections were weakened.

## 0.2.9 preview — bounded transient TLS recovery and safe diagnostics

The Responses adapter previously collapsed SSL exceptions into `responses_tls`,
discarding the native subtype and excluding transient TLS closures from retries.
It now records allowlisted SSL types/reasons and bounded errno/verification codes,
with separate certificate-verification text. Raw exceptions, certificate subjects,
certificate contents and headers are not added to diagnostic metadata.

The executor retains the existing two-retry limit for recognized EOF/clean-close
failures and the server internal-error alert. Certificate-verification failures,
protocol failures and unclassified/legacy TLS failures remain nonretryable.
Existing network/timeout and legacy provider behavior is retained. Completed tool
results remain in the same model round and are not executed again.

Real loopback HTTPS regressions perform Read, consume a ClientHello and close the
next connection before ServerHello, then recover or exhaust the existing retry
budget. They assert a single tool execution and preserved call/output identity.
A real untrusted certificate is rejected before any HTTP request and receives no
retry. The test-specific trust context keeps hostname verification and
`CERT_REQUIRED` enabled. Native platform packaging jobs now run these TLS and
diagnostic contracts on Linux and Windows before building installers.

The corrected HTTPS regressions failed twice with the original retry policy,
then passed with the new policy. An initial test-fixture attempt replaced the
HTTPSConnection class and broke its standard-library `super` lookup; that fixture
error was retained and corrected to use a verified fixture trust context. The
final targeted Responses/TLS/output/diagnostic/taxonomy run passed **161 cases**;
the TLS/diagnostic subset passed **13 cases**. Ruff executable-error and diff
checks passed. Full isolated roots, desktop contracts and package/update/native
gates remain required before publishing the candidate.

The TLS regressions and their diagnostic case now live in the existing portable
platform contract module, which the native Linux/Windows packaging jobs already
run. Their function bodies and parameter sets are unchanged; the CI workflow
remains unchanged. The preceding full isolated run completed both Python roots:
**4,872 collected, 4,841 passed, 31 existing skips, zero failures/errors**, plus
**630 passing subtests**. All **357 desktop contracts** passed. Local statement
coverage is **46,360/78,237**, and branch coverage **12,937/27,978**.
The relocated targeted suite passed **166 cases**, with three existing
Windows-only skips on Linux; all original regression bodies were retained.

The first Windows package check on public head `646301b` reached the real TLS
contracts but three cases failed while decoding UTF-8 traces with the platform's
default cp1252 codec. The fixture now reads its trace explicitly as UTF-8; TLS
assertions and retry/certificate behavior are unchanged. The failed job log was
retained rather than skipping these native-platform cases.
The portable contract module passed **16 cases** with three existing Windows-only
skips after the explicit-encoding fix; all 357 desktop contracts remain passing.

Final source freeze `35815383` and public PR head `4956682` match tree
`d39b42f48a0b1b75e18051ac5b32d8e473ffe640`. PR 32 passed all **eleven required
hosted checks** and merged normally into the protected branch as
`7c3dee0accbb53a32176ce7b0ec99f1fcd887527`. Python 3.13 and 3.14 each collected
**4,872 cases: 4,840 passed, 32 existing skips, zero failures/errors**, plus
**630 passing subtests**. The first Python 3.14 attempt retained a busy test-CLI
shutdown failure; an unchanged rerun of that job passed. No test or skip
condition was weakened. All **357 desktop contracts per OS** passed.

Real TLS platform contracts passed on Linux and Windows, including EOF recovery
after Read, bounded retry exhaustion, a single completed tool execution, rejection
of an untrusted certificate before HTTP, and safe diagnostic filtering. Packaged
Linux and Windows acceptance passed all nine maintained native stages and the
existing source/file-opening, application-history and installed-release-note
lifecycle checks. Windows installed, reinstalled, self-updated and relaunched with
synthetic data retained. Both actual-OS TLS cases and packaged UI acceptance are
distinct from live OpenAI-account acceptance; providers were synthetic loopback.

Final Linux snapshot build `79642e685d2b` and actual AppImage extraction verified
**6,399 files**, zero mismatches and six existing build-only omissions. Windows
verified **2,739 files**, zero missing files or mismatches, clean provenance and
no private data. Its CI merge source `9d0d90e` has the reviewed tree above. All
installed production Python and desktop sources matched the reviewed inventory.
One local wheel build encountered sandbox-restricted PyPI DNS resolution; a clean
offline build with the already cached exact pinned build dependency passed.

Preview **v0.2.9** was published on GitHub and Gitea with the same six assets and
matching Russian/English description: Linux DEB/AppImage, Windows EXE, CLI wheel,
build manifest and checksums. GitHub server digests and sizes matched all local
files; every Gitea file was downloaded and fully hashed before publication.
Published bodies, asset inventory and actual tag targets were read back. GitHub
targets the protected merge and Gitea the matching canonical source freeze.

The actual 0.2.8 updater found 0.2.9 for DEB, AppImage and Windows and verified the
downloaded public DEB. An isolated installation of that downloaded artifact over
0.2.8 retained the previous generation, encrypted Vault and What's New preference;
all three installed TLS production modules matched source. The public Windows
download returned HTTP 200. The owner's runtime, application, tasks and personal
workspace files were not replaced or restarted. Users must update and restart the
application/runtime and chat. Android remains 0.1.106 (107) and needs no update.
The Windows EXE is unsigned; physical Windows 11 acceptance remains pending and
the release is explicitly preview.

## 0.2.8 preview — sources, navigation and installed release notes

Session sources now use durable successful tool-result evidence for web and pull
request references, and merge validated output paths and persisted file changes.
User uploads retain their existing route. The Files API includes recorded edits
and shell outputs even when the final answer names no files. Shell commands also
record bounded before/after workspace metadata; incomplete scans are reported
without inventing deleted files or overwriting existing edit counts. Existing
workspace, key/private-file and download-ID checks remain enforced.

The desktop groups pull requests, files, web references and uploads, refreshes
sources and the open Files tab after tool results, preserves state across reload,
and rejects late responses from another session. Relative paths distinguish
identical basenames; file navigation refuses an ambiguous basename fallback.
Opening a source revalidates its current download identity and session after
an asynchronous refresh, so a final edit cannot leave a stale source link.
Russian and English group labels are supported. The source/data-flow change is
illustrated in the editable sources diagram included with the release.

Targeted bridge/source/change tests: **232 passed**, five passing subtests;
additional auth/scope regressions: **26 passed**. All **357 Node contracts** passed
without skips. Ruff executable-error checks passed. The full isolated Python run
collected **4,860 cases: 4,827 passed, 31 skipped, two failures, zero errors**, plus
**630 passing subtests**. One failure was the existing external grep.app live
check receiving Too Many Requests; the other was a test-only CLI shutdown being
refused as busy. That exact CLI case passed on a separate unchanged recheck.
Neither failing test nor its skip conditions were changed to hide these results.
Statement coverage: **46,349/78,221**; branch coverage: **12,930/27,970**.

A Linux packaged source-group/file-viewer run passed all nine stages with three
real one-shot approvals. The first new fixture correctly paused for permission;
the final fixture clicks actual approval controls instead of bypassing guards.

The same candidate now adds application back/forward history and opt-out
post-install release notes. Eight additional Node cases cover history branching,
bounded routes, failed/restored sessions, per-install identity, preference
persistence and the dedicated dialog IPC boundary. Native navigation, release
notes lifecycle, Linux/Windows installers,
update preservation and hosted CI remain required release gates. Android sources
are unchanged. The user's running application and workspace data are untouched.

After adding history and release notes, the full isolated Python run again
collected 4,860 cases: 4,827 passed, 31 skipped, two failures, zero errors and
630 passing subtests. Both failures exposed a new history integration dependency
in the isolated workspace/history fixtures. History hooks now use the optional
controller API; both affected test files passed unchanged (three cases). The
complete desktop suite passed all 357 contracts with local sockets available;
an earlier sandboxed invocation failed socket-dependent suites and is retained
as an environment failure. The eight history/preferences cases also passed after
including plugin navigation. Final hosted and packaged gates follow below.

Final local candidate at source commit `45c8d42d` passed the full isolated Python
runner: **4,860 collected, 4,828 passed, 32 skipped, zero failures/errors**, plus
**630 passing subtests**. The extra skip uses the unchanged live grep.app test's
existing upstream-unavailable condition (`fetch failed`); it is not a verified
external search pass. Statement coverage is **46,346/78,221** and covered branches
are **12,930/27,970**. Ruff and publication contracts passed.

Clean Linux runtime, snapshot, DEB, AppImage, wheel and source export were built.
Packaged native acceptance passed all nine maintained stages, plus real native
back/forward clicks, session sources/file opening and release-note lifecycle
checks. Three actual one-shot approvals were used for the source fixture.
The release-note window displayed four installed-version changes, default-on
checkbox and Close without overflow. Opt-out survived a reconstructed controller
and a new installation identity; manual reopening permitted re-enabling, and an
ordinary relaunch did not repeat the automatic window.

An isolated 0.2.7 to 0.2.8 update retained the previous generation, encrypted Vault
and release-note preference; installed application bytes matched the reviewed
source. AppImage staging verified **6,399 files**, zero mismatches; only six
build-only `.gitignore`/static-library/object files were omitted by the existing
packager defaults. The clean canonical source and public candidate had identical
trees, and both public-tree and history secret scans passed.

The first Windows package run on PR 31 head `43f1bb4` failed its new source
fixture: the UI remained in `waiting_approval` after an unacknowledged synthetic
mouse click. A diagnostic repeat accepted one Write, completed Read, then waited
on the next Write. Navigation had passed in both runs. The fixture now disables
automatic scroll during approval, waits for layout, checks the button at the
pointer position and records receipt of the real native click before marking
that request handled. It retains all permission guards and completion assertions.
Windows package/update acceptance and final hosted CI must pass before publication.
The running owner installation was not replaced or restarted.

On head `579962b`, Windows completed all four source tools and all three real
approvals, then failed opening the indexed file in both normal and diagnostic
runs. The bridge download opener still used POSIX-only `O_DIRECTORY`/`dir_fd`.
It now uses the existing shared regular-file opener, including Windows pinned
parent handles and reparse-point rejection, while retaining the download identity
check and normalized inaccessible-file error. A portable regression reads a
Unicode-named output and rejects its stale identity; the directory-redirect
contract now also covers bridge downloads. Native failure reports include the
viewer status/error for direct diagnosis.
The targeted platform, bridge HTTP and source suites passed **111 cases**, with
three existing Windows-only skips on Linux. Ruff and publication contracts passed.

Final source freeze `bb6fa105` matches public PR head `259a438` and protected merge
`ef21ba8fc39892d5b180754b3ce4d387bce40780` at tree
`7ee94468012875430691f05988a9bd15c997fe65`. All **eleven required hosted checks**
passed before a normal protected merge. Python 3.13 and 3.14 each collected
**4,861 cases: 4,829 passed, 32 existing skips, zero failures/errors**, plus
**630 passing subtests**; live grep.app searches passed in both final runs.
Desktop contracts passed **357 cases per OS**. The first hosted Python 3.14
attempt retained two failures: external grep.app rate limiting and a busy
test-runtime shutdown. An unchanged rerun of that job passed; no test or skip
condition was weakened. The final local full run retained one busy CLI shutdown
failure (**4,829 passes, 31 skips, 630 subtests**); its exact unchanged PTY case
passed separately in 5.72 seconds.
Final local coverage: **46,342/78,217 statements** and **12,928/27,968 branches**.

Clean final Linux and Windows packages passed all nine maintained native stages
and the additional sources/file-opening, back/forward and release-note lifecycle
checks, with three real one-shot source approvals per OS. Windows installed,
reinstalled, applied its verified updater and relaunched from a path containing
spaces and Unicode while preserving synthetic data. Its previously failing file
viewer passed with the portable safe opener. Final Linux snapshot build is
`a6e0b6c027d4`; actual AppImage extraction verified **6,399 files**, zero mismatches
and six build-only omissions. Windows verified **2,739 files**, zero missing files
or mismatches, clean provenance and no private data. Its source commit
`ac3f8961c6903da9c8823e6c98a321210cd7f5df` has the same reviewed tree.

The first final Linux runtime build encountered a TLS handshake timeout while
fetching checksum-pinned CPython source for its socket extension. A normal clean
retry passed with the same recipe and checksum checks. Dependent staging attempts
before that retry had no runtime and produced no release packages. Final native,
package and update acceptance used the successfully rebuilt payload.

Preview **v0.2.8** was published on GitHub and Gitea with the same eight assets
and matching Russian/English description: Linux DEB/AppImage, Windows EXE, CLI
wheel, build manifest, checksums and editable SVG/PNG source-flow diagram. GitHub
server SHA-256 digests and sizes matched all local files; every Gitea asset was
downloaded and fully hashed before publication. Published bodies, asset inventory,
tag targets and diagram bytes were read back. The GitHub tag targets the protected
merge; Gitea targets the matching canonical source freeze.

The actual 0.2.7 updater found 0.2.8 for DEB, AppImage and Windows and downloaded
the public DEB with checksum verification. Installing that downloaded artifact in
an isolated 0.2.7 prefix retained the previous generation, encrypted Vault and
release-note opt-out; installed desktop and bridge-opener bytes matched source.
The public Windows download returned HTTP 200. The published PNG was inspected.
The owner's running installation and services were untouched. Users must update
and restart the application/runtime and chat. Android remains 0.1.106 (107) and
needs no update. Windows Server 2025 and Linux acceptance do not constitute
physical Windows 11 acceptance; the Windows EXE remains unsigned and this release
is explicitly a preview.

## 0.2.7 preview — structured model network retries

The executor uses structured transport error codes before localized error text.
`responses_network` and `responses_timeout` retain the existing two-retry limit;
explicit permanent errors do not fall back to text matching. Legacy OpenAI chat
completion and Anthropic-compatible transport retries remain supported. Retry
trace events include the existing allowlisted model failure diagnostics.

Ten new regression cases cover a real local HTTP disconnect after a successful
Read, recovery with the same tool result and no duplicate execution, exhausted
retries, localized network/timeout messages, and permanent error codes whose text
contains a misleading timeout marker. The eight code/text cases failed before
the source fix. The initial socket fixture was denied by the execution sandbox;
it passed when run with loopback access.

Targeted Responses, output-contract and trace-taxonomy checks: **148 passed**.
Locked dependency synchronization and desktop dependency installation passed;
all **345 Node
contracts passed**, zero failures or skips. The initial desktop installation and
full Python run hit the system temporary-directory user quota. Intermediate
relocated runs also exposed the test harness's short-path and guest-path
assumptions. These failures were retained; no test was changed or skipped to
hide them. After owner-authorized removal of unused historical build caches,
the normal isolated runner completed both Python roots: **4,845 collected,
4,813 passed, 31 skipped, one failure, zero errors**, plus **630 passing
subtests**. The one failure is the existing live grep.app search receiving
`Too Many Requests` instead of JSON results; one separate recheck reproduced it.
It remains a failed external integration check, not a pass or a newly added skip.
The existing skips cover disabled terminal cases and isolation, platform and
live-provider prerequisites; no skip conditions changed.

Statement coverage is **46,166/78,021 (59.17%)** and branch coverage is
**12,842/27,868 (46.08%)**. Real loopback HTTP testing establishes the retry and
single tool-execution behavior. These initial source checks preceded the
packaged acceptance recorded below; physical devices were not exercised.

Protected PR 30 passed all **eleven hosted checks** on
`dd490a7062eea8d8f6ba30e4dade4763d6303f79` and merged normally as
`77bb69f30ef8f7ed332793be2a18571cda1a648c`. Python 3.13 and 3.14 each collected
**4,845 cases: 4,813 passed, 32 existing skips, zero failures/errors**, plus
**630 passing subtests**. All four grep.app checks passed in both CI runs,
including the live search that was rate-limited locally. No test or skip rule
was changed. Hosted Python 3.14 statement coverage is
**46,141/78,021** and branch coverage **12,833/27,868**. Android unit/lint/debug
passed without changing Android 0.1.106 (107). Desktop contracts passed all
**345 cases on both Linux and Windows**.

Both packaged Linux and Windows candidates passed all nine native first-task,
restart and continuation stages with a synthetic provider. The actual Linux
bundled interpreter/executor passed real HTTP disconnect recovery and retry
exhaustion after Read with exactly one tool execution. A temporary 0.2.6-to-0.2.7
update retained encrypted connections, the previous generation and the patched
executor bytes. The same update passed using the artifact downloaded from the
published release by the 0.2.6 updater, which also discovered the Linux AppImage
and Windows installer. Windows Server 2025 passed NSIS installation, updater,
relaunch and uninstall checks; the published Windows download returned HTTP 200
without authentication.

Clean source inventory, wheel/snapshot provenance, packaged source bytes and
server asset digests were verified. Windows remains unsigned, physical Windows
11 and real-provider acceptance remain separate limits. GitHub and Gitea preview
releases contain the same six approved assets; all
server digests/sizes and the published descriptions were read back. Every Gitea
asset was downloaded and hashed after upload. This is a preview release. The
user's running installation, services, Vault and tasks were not
modified; installing the update requires an application/runtime and chat restart.

## 0.2.6 candidate — settings notifications

Concurrent settings reads now wait in a bounded queue and repeated identical
queued reads share one request. Requests started by completion callbacks retain
FIFO order. An expected busy response is shown in settings without creating an
error notification. Authentication, backend failures and owner-frame checks
remain enforced and visible.

Five new regression cases failed before the fix; a separate ordering regression
exposed a callback overtaking queued work and passed after the FIFO correction.
Locked desktop installation and all **345 Node contracts** passed, with no skips.
The isolated Python run across both roots collected **4,835 cases: 4,804 passed,
31 skips, zero failures/errors**, plus **630 passing subtests**.
The skips include ten disabled terminal cases and existing isolation, platform
and live-provider checks. They are not successful checks; reasons are unchanged.

A separate real Electron/preload/owner-child check passed: 25 concurrent identical
reads produced one backend dispatch and zero busy notifications. Both a backend
error reply and a stopped backend produced genuine error notifications. Test
windows, data and child processes were isolated; the user's installed desktop,
Vault and saved notification history were not changed. This source fix requires
an updated desktop build to take effect; no new release has been published.

Protected PR 29 passed all **eleven hosted checks** on head
38d9144ee59bcb8a37db40a1a6fc6a6da970cf68 and merged normally as
cddedab295ce31a3e29733f8dfb977bb5f998f0a. Hosted Python 3.14 collected
**4,835 cases: 4,803 passed, 32 skipped, zero failures/errors**, plus **630 passing
subtests**. Statement coverage is **46,146/78,019**; branch coverage
**12,834/27,866**. Python 3.13 and Android unit/lint/debug gates also passed;
Android remains 0.1.106 (107). Desktop contracts passed **345 cases on each OS**.

The packaged Linux payload passed the real Electron busy-notification scenario
and all nine maintained native UI stages. Temporary **0.2.5-to-0.2.6** and
**0.2.3-to-0.2.6** updates retained encrypted data, the previous generation and the
exact corrected application files. The first legacy update check exhausted the
quota of its temporary filesystem; moving the isolated fixture to the build
filesystem passed without a product change. Windows Server 2025 x64 passed all
nine native stages, NSIS installation, verified update and packaged relaunch with
spaces/Unicode paths while retaining synthetic data. Package sources match the
reviewed files and declare an unmodified source tree. Windows remains unsigned.

GitHub v0.2.6 was published as a preview with three desktop installers, a CLI
wheel, BUILD_MANIFEST.json and SHA256SUMS. All six uploaded server sizes and
SHA256 digests match local files; the published user-facing description matches
the reviewed Russian/English text. Source provenance is recorded in the manifest.
The same approved files are mirrored to Gitea with downloaded checksum checks.
No user installation, model service, credentials or notification history was
modified. Existing notifications remain available; the fix takes effect after
the desktop update and relaunch. Physical Windows 11 acceptance remains separate.

## 0.2.5 candidate — one-command CLI and explicit shared runtime

The real igorek entry point uses existing profile queues, with OS-held client
leases and scoped Ctrl+C cancellation. Profile switching changes routing/history
and clears prior model overrides/approval rules. The optional POSIX daemon uses
explicit lifecycle commands, existing scheduler/Gateway execution and watchdog;
clients disconnect independently. One scheduler lease per database prevents duplicate
schedule execution. Busy daemon stop refuses rather than truncating tasks.

Persistent inline mode fixes fast input lost between prompt applications after a
profile switch. Compact startup and --no-alt-screen retain the old-header fallback
and original igorek-factory route. Mouse clipboard delivery is asynchronous with
one pending request; failed/unconfirmed delivery retains selection for retry.

Locked sync and the complete isolated run across both Python roots collected
**4,834 cases: 4,803 passed, 31 skipped, zero failures/errors**, plus **630 passing
subtests**. Statement coverage is **46,132/78,005**;
branch coverage **12,823/27,862**. The existing
system/terminal/live-provider/platform skips are not passes. Ruff executable-error
checks and publication contracts passed; locked npm install and all **338 desktop
contracts** passed. No dependencies were upgraded for this change.

The 22 launcher tests include nine real POSIX PTY configurations: fullscreen and
inline, foreground and explicit daemon, normal completion and active cancellation,
profile switching, second-client survival, and the old header. Geometry and cursor
queries are handled. A synthetic-provider schedule completed with no open frontend.
Other cases cover data-home isolation, consumer reuse/config mismatch, scheduler
ownership, idempotent daemon lifecycle and refusal to stop busy workers. Clipboard
regressions cover nonblocking/busy behavior, confirmation and late-completion safety.

The first full candidate run had two fixture failures: an old FakeClipboard did
not implement submit. Its adapter was updated; original selection assertions were
retained. The final complete rerun passed. An earlier classic PTY failure exposed
lost typeahead and led to the persistent inline fix. Original evidence is retained
locally. Sandbox socket denials required the normal host test permission; those
failed environment runs are not counted as successful product checks.

Physical Termux/ARM64 and real provider-account acceptance remain separate.
Native Windows CLI is not introduced. User services and credentials were untouched.

Hosted Python 3.13 then exposed a genuine raw-input race in the 0.2.5
candidate: a profile switch used prompt_toolkit run_in_terminal, which detaches
input and enables cooked mode. Enter typed after profile state was saved could
therefore arrive as LF instead of submission. The fixture now delays that exact
boundary; the previous installed wheel fails deterministically (one failed case).
Profile work now runs off the event loop with captured console output, while the
composer stays attached and raw. The complete focused set passes **260 cases**,
plus **3 subtests**, including all nine PTY configurations. This replaces the
first candidate's profile-command path; previous native builds are not reused for
publication. The full Python rerun and hosted checks gate the final candidate.

The raw-input fix passed a second complete local run: **4,834 collected,
4,803 passed, 31 skipped, 630 passing subtests, zero failures/errors**. The
updated installed wheel also passed all **22 launcher cases** outside source.

The daemon now shares one execution mutex across its profile workers, matching
desktop AgentRuntime. Worker task environment overlays are process-global; a new
regression holds one synthetic profile overlay while starting another worker,
proves the values remain isolated, and checks idle stop with a shared mutex.
This adds one launcher case (23 total); the final hosted full suites gate release.

Protected PR 28 then passed all **eleven hosted checks** on final head
f8db32ffb0062f0c80170cd97de856b8e0abe581 and merged normally as
002f43fed32e250e01de91240859ae5366ae2e8a. Python 3.13 and 3.14 each collected
**4,835 cases: 4,803 passed, 32 environment skips, zero failures/errors**, plus
**630 passing subtests**. Hosted 3.14 statement coverage is **46,158/78,019**;
branch coverage **12,835/27,866**. Desktop contracts passed **338 cases on each
OS**; Android unit/lint/debug checks passed unchanged at 0.1.106 (107).

The final shipping wheel passed all **23 launcher cases outside the source
checkout**, including the delayed profile switch and process-environment
isolation regression. The clean Linux snapshot contains **7,528 entries**, build
25fe32be7a31. Native Linux and Windows each passed all nine maintained stages and
redirect/progress checks. The temporary Linux **0.2.4-to-0.2.5** update retained an
encrypted Vault canary and previous generation. Windows Server 2025 x64 accepted
NSIS installation, verified helper update and packaged relaunch with spaces and
Unicode paths while preserving synthetic data. Both package manifests declare
unmodified sources; all reviewed product/backend/CLI bytes match. No private
state is bundled; the Windows executable is unsigned.

GitHub v0.2.5 was published as a preview from the protected merge commit, with
three desktop installers, the architecture-independent CLI wheel, five SVG/PNG
architecture assets, SHA256SUMS and BUILD_MANIFEST.json. All eleven server asset
sizes and SHA256 digests match local files. Anonymous Windows download and all
three PNG preview URLs return HTTP 200. The release description distinguishes
ordinary foreground ownership from the explicit daemon and serves all users.
Gitea mirrors these approved files with full downloaded checksum verification.
Physical Windows 11, Termux/Ubuntu ARM64 and live model-account acceptance remain
separate. User installation, model server, Vault and running services were not
changed. The cancelled update automation remains paused.

## 0.2.4 candidate — OpenCode 2 managed-profile authorization

OpenCode 2.0.27 gives a saved connection credential precedence over a provider's
configured API key. Its runtime auth can also replace the Authorization header.
An arbitrary saved key therefore replaced the managed proxy grant and caused
HTTP 403 before the upstream model request. The generated V2 profile now sends
the scoped grant in X-Igorek-Profile-Grant, independently of SDK credentials.
The proxy validates that header first; a present but invalid grant fails closed.
Legacy Bearer and x-api-key clients retain their previous behavior when the
dedicated header is absent. The grant is never forwarded upstream.

Four regressions cover a saved-key override with an exact model ID, invalid and
empty dedicated grants, and lock/revocation/binding enforcement. Before the fix,
three new regression cases failed as expected. After the fix, the profile suite
passed **27 cases**; the combined related suites passed **89 cases**, including
those 27. Desktop contracts passed **338 cases** and publication checks reported
zero problems. No tests were skipped. These targeted results do not replace the
published full-suite coverage baseline below. Real model generation and live
OpenCode sessions were not started; installed applications and credentials were
not changed. The fix is not included in the published 0.2.3 binaries.

The complete isolated Python run across both test roots collected **4,809 cases**:
**4,778 passed, 31 skipped, zero failures/errors**, plus **627 passing subtests**.
Statement coverage is **45,773/77,349 (59.18%)**; branch coverage is
**12,725/27,646 (46.03%)**. The 31 skips cover ten terminal cases, seventeen
isolation/system prerequisites, one live provider and three platform cases; none
were added for this change. Desktop locked installation and
all **338 contracts** passed. These checks use synthetic model endpoints; owner
acceptance of the actual llama-server connection remains separate. The installed
0.2.3 application is unchanged while the 0.2.4 packages are prepared.

Protected PR 26 then passed all eleven hosted checks and was merged without an
administrative bypass. Python 3.13 and 3.14 each collected **4,809 cases**:
**4,777 passed, 32 skipped, zero failures/errors**, plus **627 passing subtests**.
The hosted environment has one additional existing system-prerequisite skip.
Hosted 3.14 statement coverage is **45,763/77,349 (59.16%)**; branch coverage is
**12,721/27,646 (46.01%)**. Desktop contracts passed on both operating systems;
Android unit tests, lint and debug assembly passed without Android source changes.

Both native desktop scenarios passed all nine maintained stages and actual HTTP
redirect/progress checks. A temporary Linux 0.2.3-to-0.2.4 update retained an
encrypted Vault canary and the previous generation. Windows Server 2025 x64
accepted the NSIS install/update/relaunch scenario with spaces and Unicode in the
installation path and retained synthetic data. Packaged application/backend
sources match the reviewed files; manifests report unmodified sources. The
published GitHub v0.2.4 contains three desktop installers, SHA256SUMS and build
provenance with matching server digests. The installed 0.2.3 updater discovers
0.2.4 for DEB, AppImage and Windows without downloading or installing it.

These are synthetic-provider acceptance results. Real llama-server use and a
physical Windows 11 machine remain owner/friend checks; Windows is unsigned.
The owner's installation, running services, authorization and credentials were
not modified. Android 0.1.106 (107) remains available in the previous release.


## Published 0.2.3 acceptance — 16 October 2026

[Release 0.2.3](https://github.com/Endorpheen/Igorek-Harness/releases/tag/v0.2.3)
was published from protected PR 24 after all eleven checks passed. Both hosted
Python versions (3.13 and 3.14) collected **4,805 cases**: **4,773 passed,
32 skipped, zero failures/errors**, plus **627 passing subtests** each. The
hosted 3.14 statement coverage is **45,758/77,347 (59.16%)**; branch coverage is
**12,720/27,644 (46.01%)**. The 32 skips cover ten terminal-environment cases,
eighteen isolation/system prerequisites, one optional live-provider configuration
and three platform-specific cases. Local and hosted prerequisite availability
differ; the earlier local totals below are retained separately.

Desktop contracts passed **338 cases** on Linux and Windows. Both native tours
completed all nine maintained stages and the additional real-transport updater
checks. On Windows, the actual NSIS installer was installed, updated through the
verified updater helper, relaunched and closed gracefully; synthetic data survived.
The signed Android 0.1.106 (107) release APK and debug APK passed signature and
version checks, 318 unit tests and lint. The owner installed the debug APK and
confirmed its version screen; full physical-device acceptance remains open.

The release has six verified assets: three desktop installers, one signed Android
APK, SHA256SUMS and BUILD_MANIFEST.json. The Windows executable remains unsigned.
Clean-machine acceptance and full physical Android testing remain separate from
these synthetic native checks. The old GitHub binary releases were replaced;
source tags and history were preserved.

After this frozen release, Dependabot proposed Gradle wrapper 9.8.1 for future
Android builds. Its generated JAR must match the official checksum, and the
Windows script must retain the repository's normalized index line endings.
Local Gradle 9.8.1 unit tests, lint and debug assembly passed; eighteen source
and publication contract cases passed. This follow-up does not replace the
published 0.2.3 assets or their provenance.


## Dependabot compatibility and desktop update acceptance — 0.2.3 candidate

The pending Python updates are tested together: agent-client-protocol 0.12.1,
markdown-it-py 4.2.0, pydantic 2.14.1, tree-sitter 0.26.0 and
tree-sitter-language-pack 1.22.1. Their transitive versions remain locked.
The new tree-sitter Point tuple API exposed intermittent native crashes while
reading `.row` under Python 3.14. Comment locations now use the documented tuple
index, retaining exact line numbers for nested scripts. The original combination
failed three of five full publication runs; the adapted checker passed five of
five. Two regression cases exercise repeated large trees and embedded scripts.
The full isolated run across both roots collected **4,803 cases**: **4,772 passed,
31 skipped, zero failures/errors**, plus **627 passing subtests**. Statement
coverage is **45,772/77,347 (59.18%)**; branch coverage is
**12,723/27,644 (46.02%)**. Existing environment skips remain documented below.

Android updates require a coordinated migration to AGP 9.4.1, Gradle 9.6.0,
Kotlin/Compose compiler 2.4.21 and compile SDK 37.0. Compose BOM 2026.09.00,
Core KTX 1.19.1, OkHttp 5.5.0, serialization 1.11.0 and security-crypto 1.1.0
compile together. Minimum and target SDKs remain 26/34. The new Gradle wrapper
and distribution have verified official SHA-256 values. The generated resource
sets use the current typed DSL with explicit task dependencies. New Compose
lint detected a non-observable locale read; browser timestamps now observe the
configuration locale. Android **318 unit tests**, lint and debug assembly passed.
No lint checks were disabled. Earlier failed migration attempts remain recorded.
The first hosted Android run stopped because sdkmanager was absent from PATH;
the workflow now invokes the tool through the runner-provided ANDROID_HOME.

The maintained native scenario now exercises real HTTP redirects and visible
progress in the same Electron window on both desktop platforms. Its Linux path
also verifies a detached child under the actual launcher flock. A separate full
Electron-to-helper acceptance installed the public 0.2.2 package into a temporary
installation and retained a data canary. The owner's repaired local 0.2.1
successfully updated to 0.2.2 and restarted with preserved history/connections.
The earlier direct-file overlay was rejected by integrity verification and was
replaced with complete verified local snapshot generations. Final desktop contracts passed **338/338**, including shutdown-time owner IPC
refusal and closed-window secret delivery compatibility. The fixture was updated
to declare the existing shutdown state explicitly after the new guard exposed
its missing variable; the failed run is retained. Release 0.2.3
package acceptance and hosted Windows results are still pending at this point.


## Native update installation and visible progress

Electron can pass the launcher flock descriptor to a detached updater. The
helper now closes only its inherited descriptor; it does not unlock the live
launcher. A process regression verifies that the running parent remains
protected. An actual Electron process under flock successfully launched the
detached helper and updated a temporary 0.2.1 installation to the public 0.2.2
DEB, with full integrity verification and a retained user-data canary.

The update window now remains open through download percentage, checksum
verification and installation confirmation. Native Electron verified window
continuity and visible progress using synthetic bytes. Desktop contracts passed
**336/336**; Python installer and package checks passed **56/56**, without skips.
The repaired local launcher passed integrity verification for **7,510 entries**.
Owner acceptance is pending; no new release version was created.

## In-app release download transport

The actual Electron 44.7.0 `session.fetch` rejects GitHub asset redirects with
`Redirect was cancelled` when the updater requests manual redirect handling.
The update dialog now uses Node fetch, which exposes the redirect response to
the existing host allowlist. Size and SHA-256 verification remain mandatory.
A regression covers the dialog transport and a permitted asset redirect.
Desktop contracts passed **335/335**, with no skips. An isolated Electron
process downloaded the public 0.2.2 DEB (**162,654,004 bytes**) through the
corrected dialog and verified its published SHA-256. It did not install the
package or change the running application. The repaired local installation also passed the actual launcher integrity
check across all **7,505 entries**; the original generation remains available.
Owner in-app installation acceptance remains pending. No release version was changed.

## Project link and canonical release checkout — 0.2.2 candidate

The About button now says “GitHub проекта”; its existing destination remains
the project's repository. Version 0.2.2 includes the local-model and compaction
fixes below and provides a newer version for in-app acceptance from 0.2.1.

The remaining modified-tree flag in the Windows candidate came from the Gradle
batch wrapper: its committed blob still contained CRLF while the new text
attributes normalize the index to LF. Only its line endings were normalized;
Windows checkout still supplies CRLF. The real Git checkout regression now
includes the actual indexed wrapper, checks binary bytes and verifies a clean
tree with Windows-style autocrlf. No installer is accepted by ignoring the
provenance flag. Native package and update results are recorded after building.
The targeted checkout, source, model, compaction and update checks passed
**48 cases and 14 subtests**; desktop contracts passed **334/334**. Locked
dependency synchronization and publication contracts passed.

The OpenCode integration now uses the upstream UI Mark geometry in the rail and
workspace tab, with its MIT notice carried by desktop packages and Python
wheels. Desktop contracts passed **334/334** after the icon change; packaging and
publication checks passed **65 cases**. Android's first CI attempt stopped before
compilation because the pinned Gradle archive download exceeded the wrapper's
10-second default. The explicit network timeout is now 60 seconds; the Gradle
version and distribution SHA-256 remain unchanged. A preliminary local native
attempt stopped on HTTP 502; the synthetic fixture now reports the failing
route and structured code, and final package acceptance must still pass.

## Local-model discovery and overflow recovery — 0.2.1 candidate

Local request model IDs and display names now share endpoint normalization:
`/v1` is appended once, exact IDs remain intact in requests, and the composer
uses the running local server's readable model name. Model cache entries expire
and refresh at task boundaries; failed lookups can retry. Cloud model selection
and explicit overrides retain their previous behavior.

The llama-server `exceed_context_size_error` now enters existing reactive
compaction even when the approximate estimate is below the normal threshold.
Provider-rejected summary inputs split into smaller bounded chunks. Regression
tests cover the reported 35,512/32,768 and 101,461/98,048-token responses, cloud error compatibility,
successful continuation, full history archival, summary splitting and failures
that must not trigger compaction. Pressure thresholds and approval guards are
unchanged; token estimates remain approximate.

Locked Python dependency synchronization and desktop `npm ci` passed. The full
Python run covered **4,801 cases across both roots and 300 files**, with
**626 passing subtests** and **31 existing skips**. It initially reported one
failure in the isolated JavaScript file-refresh fixture because the new callback
was absent. The fixture now supplies and checks that callback while preserving
all file-refresh assertions; its targeted rerun passed. The other 299 files
passed in the full run: **4,770 passing cases in total after the correction**.
Coverage measured **45,769/77,331 statements (59.19%)** and
**12,723/27,636 branches (46.04%)**. Desktop passed **334/334** after locked
installation; the complete web UI file passed **152 cases**, and the final
model-API-only check passed **six cases**. Fatal Ruff checks and whitespace
validation passed. An initial restricted test invocation could not bind its
fixture sockets; the real localhost tests were rerun with that restriction lifted.

These are source-level fixes and synthetic acceptance results. The running
desktop, model server, user profiles, credentials and sessions were not changed.
No updated package was installed during this check. Version 0.2.1 is the release
candidate for these fixes; the final 98k regression and publication contracts
passed another 30 targeted cases with 12 subtests. Package acceptance is recorded
separately from these source-level results.

The local Linux 0.2.1 candidate passed all nine native stages and an actual
temporary 0.2.0→0.2.1 update retaining the previous generation and an encrypted
synthetic connection. Its AppImage manifest and critical product bytes match the
reviewed source. The first hosted Windows candidate passed native installation
and self-update checks, but package inspection rejected it: Windows checkout had
converted text to CRLF and its clean-source provenance correctly reported a
modified tree. Repository attributes now preserve LF source bytes on every OS
and retain CRLF for batch files. A real Git checkout with Windows-style
`core.autocrlf=true` verifies source/binary bytes and a clean tree; all 16 owning
publication cases passed. Changes to the attributes also trigger packaging.

## Automatic desktop installation — 0.2.0 candidate

The 0.2.0 candidate adds native confirmation, a separate verified installer helper,
closed-app replacement and relaunch with the same data directory. Active executor
work refuses installation; a postponed download can be reused. Windows copies
its bundled Python helper outside the application before replacement. Linux
snapshot installation uses the existing verified installer and AppImage preserves
previous bytes for rollback. Preview builds can discover newer preliminary releases.

Targeted installer/runtime/packaging checks passed **76 cases**; the complete
desktop suite passed **330 cases**. A real temporary Linux update from 0.1.889
to 0.2.0 retained the previous generation and an encrypted Vault credential.
Windows acceptance includes the actual NSIS helper, a spaces/Unicode install
path, retained synthetic data, packaged relaunch with the same data directory
and graceful shutdown. The [PR checks](https://github.com/Endorpheen/Igorek-Harness/pull/21/checks)
record its exact result; publishing requires this complete check to pass.

## Current snapshot — 15 October 2026

Python/desktop **0.2.0**; Android **0.1.105 / versionCode 106**.
Results below describe identified candidates, not a stable three-platform release.

| Layer | Result | Scope |
| --- | --- | --- |
| Python | **4793 collected; 4762 passed; 31 skipped; zero failures/errors** | Both roots, 300 isolated files; **614 additional passing subtests** |
| Python statements | **45773 / 77338, 59.19%** | Shared agent, inference API, desktop Python and build helpers |
| Python branches | **12720 / 27640, 46.02%** | Measured independently from line coverage |
| Desktop Node | **330 passed; zero failures/skips** | Controllers, IPC, permissions, transport, updates and state |
| Android JVM | **318 passed** | Unit contracts; lint and debug/release packaging checked separately |
| Hosted CI | **All eight CI jobs passed** | Python 3.13/3.14, desktop Linux/Windows, native Linux, Android, publication checks and secret detection |
| Hosted packaging | **Linux and Windows jobs passed** | Actual platform builds; native Windows installer acceptance |

Hosted evidence for this source is available in
[CI](https://github.com/Endorpheen/Igorek-Harness/actions/runs/38761267392) and
[Desktop packages](https://github.com/Endorpheen/Igorek-Harness/actions/runs/38761267454),
revision `2ef62ee`. Local candidate payloads identify source `ca86fdea`;
the exported application sources match that hosted revision.

### Skips

The 31 Python skips comprise three Windows-only cases, one live-provider
credential case, ten terminal integration prerequisites and seventeen
VM/vsock/image/environment cases. The native Windows job separately exercises
its platform prerequisites. A skip is not proof that a physical host lacks
KVM, and a synthetic integration does not establish real account consent.

## Native desktop acceptance

The maintained scenario completed all nine stages on hosted Linux and Windows:
packaged file tools and workspace limits; clean Vault and verified synthetic
profile; worker startup without falsely completing onboarding; HTTPS pairing
and first task; lock/unlock; full backend/window restart with retained profile,
pairing and history; session continuation; eight real demo steps; and contextual
links opening actual settings panels. Windows also completed silent installer
reinstallation and uninstallation on its disposable runner.

The Linux snapshot installer previously passed a temporary 0.1.888 to 0.1.889
update, busy-install refusal, rollback, reapplication, uninstall retaining data
and reinstallation. This remains lifecycle evidence for that installer path;
it is not a clean-OS acceptance result for the new candidate.

## Desktop with KVM

A fresh 0.2.0 guest image passed build verification and the socket capability
probe as the actual agent user. A separate synthetic desktop run completed image
preparation, explicit KVM selection, actual manager startup and all nine normal
stages, including restart, saved history and all eight demo steps. Two normal
one-shot approvals were clicked for the demo file; protections stayed enabled.

The fixture used a verified HTTPS provider with a process-local DNS mapping and
an added fixture CA. Its host-approved Electron 44.5.1 exercised VM transport;
the separate hosted native Linux scenario exercised current Electron 44.7.0.
These results establish functionality, not resistance to approved guest root.
The independent guest-egress limitation remains documented in
[isolation](../architecture/ISOLATION.md#important-limits).

## Regressions resolved in this candidate

- Windows canonical path checks expand short DOS aliases consistently with Python.
- Checkpoint Git receives required OS fields, cannot select a current-directory
  executable, has closed stdin and bounded Windows output collection after timeout.
- Guest Bash, MCP and checkpoint Git children retain no_new_privs; actual clean
  filters and environment-changing descendants exercise that boundary.
- Relative Read/Write/Edit paths use task workdir, preserving traversal, external
  absolute-path and symlink refusals. The real KVM demo exposed the original bug.
- Ripgrep is installed in the guest image; UTF-8 search and literal option-like
  patterns are checked through actual packaged file tools on Windows.
- File/Vault tests cover native Windows directory pinning, DACL preservation,
  encryption and exclusive ownership. Terminal EOF retains buffered output/exit.
- Updated Electron, builder, NumPy, prompt_toolkit, cryptography and pinned Actions
  passed their owning checks. A synthetic Vault created with the prior crypto
  dependency remained readable after the upgrade.

Intermediate failures were retained during investigation and resolved through
source changes; no failed test was removed to obtain these passing results.

## Packages and signing

Linux AppImage, verified-snapshot DEB and Windows NSIS candidates were built from
locked dependencies and clean source inventories. Embedded manifests and hashes
matched staged files; local databases, account files and stale retired modules
were absent. A digest verifies bytes and is not a publisher signature.

Android debug and release APKs have separate identities. The permanent release
key is outside Git; the debug APK retains compatibility with development
installations. Windows candidates remain unsigned. Physical Android and
independently recoverable Android-key backup custody are separate acceptance gates.

## Remaining acceptance

Clean Linux installation and manual Windows checks are deferred until after
source publication. Real provider consent, browser/voice, longer sessions,
physical Android pairing/background/update and publisher signing need their own
results. Existing security-report deployment proof gaps and privileged-guest
network containment are not cleared by functional tests.

Use [TESTING.md](../TESTING.md) for reproducible commands and coverage limits,
[FIRST_RUN_ACCEPTANCE.md](FIRST_RUN_ACCEPTANCE.md) for maintained scenarios and
[RELEASE_CHECKLIST.md](RELEASE_CHECKLIST.md) for release gates.

### 2026-10-18 — Windows OpenCode port, first native validation candidate

- Added a native restricted-token runner, private desktop, process Job Object,
  disposable writable workspace, and Windows loopback capability transports.
  Launch fails closed if the OS sandbox cannot be established.
- Linux compatibility check after the shared transport changes: 54 passed,
  5 Windows-only skipped; desktop Node tests: 363 passed, zero failures.
- Windows-specific process/file/network/job and profile-proxy contracts are
  pending execution on the actual Windows CI runner. This candidate is not
  release acceptance and does not establish Windows OpenCode compatibility.

- First actual Windows Server 2025 candidate failed two new contracts: token
  creation returned WinError 87 for the LUA flag combination, and the synthetic
  proxy test omitted the desktop module search path. Existing contracts: 39
  passed, 3 platform skips. The runner now explicitly disables Administrators
  and maximum privileges and retains full read/write restricting-SID checks;
  it does not fall back to an unrestricted process. Added a real installed
  OpenCode shell/write/owner-question/result/native-panel acceptance fixture.

- Second Windows run: profile proxy passed; token creation still returned
  WinError 87 (40 passed, 3 platform skips, one failed). Therefore the first
  flag combination is not established as the cause. Added synthetic SID/layout
  diagnostics to the failing test; diagnostic token probes are immediately
  closed and never launch a process. Production still fails closed.

- Native parameter probes isolated WinError 87: zero, one and two restricting
  SIDs succeeded; adding ALL_APPLICATION_PACKAGES (third SID) failed, despite
  valid SID encoding and correct 16-byte structure layout. Replaced that
  unsupported application-package group with the standard World SID. Full
  restricting access checks, private owner/SYSTEM DACL protection, low integrity,
  disabled Administrators and maximum-privilege removal remain enabled.
  Temporary diagnostic probes were removed after obtaining this evidence.

- Fourth Windows candidate: all 41 OS contracts passed (3 platform skips),
  including low-integrity writes, private-read/write denial, loopback HTTP and
  descendant termination. Installer build succeeded. Native tour then failed
  before OpenCode launch because its new test argument shadowed Electron's
  session import; the fixture now uses an explicit alias.
- Assignment capability identity is now passed explicitly by the controller,
  preserving the run ID when Windows places service data under the assignment
  directory. Added a regression for that platform layout (6 Node controller
  tests passed). Added manual-project edit and exact DACL-revocation acceptance,
  and case-insensitive Windows private-path exclusion.
- Added matching English/Russian before/after architecture diagrams as editable
  SVG and PNG. Platform documentation distinguishes native Windows filesystem/
  process restrictions from Linux namespaces and Linux-only KVM/host services.

- Fifth Windows candidate passed the manual direct-edit/DACL-restoration
  contract and built its installer. Native OpenCode admission failed (generic
  operation error in the installed Unicode path; HTTP 500 in the ASCII-path
  diagnostic clone). The helper protocol now explicitly enables Python UTF-8;
  the synthetic fixture captures bounded, token-redacted API errors for exact
  follow-up diagnosis. No real profile or account is used by these fixtures.
- Full local Python regression: 4,885 collected, 4,852 passed, 33 platform/
  optional skips, zero failures/errors; 630 subtests. Targeted shared contracts
  after the Windows layout change: 54 passed, 5 Windows skips. Locked npm ci
  completed; all 364 desktop tests passed.

- Sixth Windows candidate: both the Unicode installed runtime and ASCII
  diagnostic runtime reached the native server, but POST /api/session returned
  HTTP 500 with an empty body. Added a private per-service process log (truncated
  on each start), redacted synthetic-log capture on fixture failure, and a native
  headless API contract before packaging. This shortens diagnosis without
  removing the separate installed Electron/right-panel acceptance.

- Early native API contract reproduced the empty 500 before packaging (41
  passed, 3 platform skips, one failed). Normal process output only reported
  the listening address. Enabled temporary debug logging in the unreleased
  Windows candidate and bounded/redacted collection of its own log files.
  Debug verbosity must return to normal before a release is accepted.

- The debug trace isolated the first failure: FileSystem.realPath failed on
  lstat of the workspace's AppData ancestor (EPERM). Added a restricted-token
  access probe and temporary, non-inheriting READ_ATTRIBUTES/TRAVERSE grants
  only for inaccessible ancestor directories. Directory listing and file-data
  access are not included; the private-read/write denial tests remain required.
  Native acceptance also compares ancestor DACLs before and after both sandbox
  modes. Normal product logging is restored; extra tracing is explicit and used
  only by the synthetic fixture.

- The ancestor-DACL assertion initially attempted to use the file helper on a
  drive root, which has no filename; that test now omits the drive root. The
  directory metadata probe itself still runs for every ancestor. Added safe
  Windows error-class/numeric-code reporting for failed sandbox startup.
  Manual projects now include metadata probes for their own distinct ancestors.

- The metadata probe still failed after an individual grant. Ancestors are now
  processed from the volume root down, so each intermediate traversal grant is
  established before probing a deeper directory. Failures retain the ancestor's
  final component in the private exception for deterministic diagnosis.

- Top-down diagnostics located the remaining metadata denial at pytest's
  private ancestor. Python 3.14 creates mode-0700 Windows directories with
  SYSTEM/Administrators/OWNER_RIGHTS entries; an elevated runner's default owner
  can be Administrators, which the child token intentionally disables. Windows
  native fixtures now use a separate disposable root with the same explicit
  current-user/SYSTEM DACL as the desktop runtime. Production's durable OpenCode
  task directory now receives that existing private-directory protection too.
  Administrators remain disabled in the child; file-read/write denial and exact
  ancestor/project ACL restoration are still asserted.

- Native server path resolution now succeeds. The next observed failure is
  EPERM in Bun's uv_spawn of Git. Windows nested jobs cannot have Job UI limits;
  the runner now uses a private window station plus desktop instead, omits the
  shared logon SID from restricting SIDs, and retains kill-on-close containment
  without UI limits so Bun can create nested jobs. Added an explicit native
  denial check for opening the owner's WinSta0 clipboard station. Disposable
  files and the child token use the current user as owner; original project
  ownership is untouched. Ancestor ACL comparison now reports exact differences
  instead of a bare boolean, so the remaining restoration failure is observable.

- Removing Job UI limits did not resolve Bun's uv_spawn EPERM, so that causal
  hypothesis was not confirmed. Restored full Job UI limits and the private
  desktop. Opening a window-station handle alone is not a clipboard-read proof;
  native acceptance now checks denied GetClipboardData access without reading
  or modifying clipboard contents. Added a direct restricted-Python Git image/
  spawn probe to distinguish executable access from Bun's subprocess behavior.
- OpenCode now uses the runtime's native Windows path normalization, including
  expansion of short path aliases, for source/workspace/grant identity and
  service directories. The independent fixture also uses canonical paths.
  The remaining ancestor-DACL difference will be reported with exact values.

- Restricted Python successfully read and executed the same Git binary. Bun's
  EPERM therefore is not established as an executable-read denial. Initial
  process/thread objects now have explicit job-capability DACLs and matching
  integrity labels; only the newly restricted token receives self-query access.
  Native contracts assert own-process handle access and own-token query while
  private host-file denial remains required.
- Exact ancestor comparison showed unchanged ACEs but an added AUTO_INHERITED
  control flag. Add/revoke now uses an explicit security descriptor and the
  file-security setter to preserve the existing inheritance-control flags.
  No assertion is weakened. Windows unit fixtures normalize aliases and keep
  granted synthetic projects outside the intentionally blocked AppData tree.

- Initial own-process/token access now passes, but Bun's Git spawn still fails.
  Added an explicit diagnostics-only probe using the official executable's
  documented BUN_BE_BUN mode, comparing Node-compatible async/sync spawning,
  native Bun spawning and inherited/ignored/piped stdio under the same policy.
  Normal product startup does not run these probes.
- ACL evidence distinguishes the two setters: SetNamedSecurityInfo adds AI;
  SetFileSecurity clears AI. Add/revoke now selects the setter according to the
  existing AUTO_INHERITED bit so both original states can remain unchanged.

- Native Windows diagnostics isolated Bun spawning failure to captured stdio;
  inherited and ignored stdio succeed with the same Git executable and policy.
  Added diagnostics-only checks of an owned named pipe and nested restricted
  Python/Bun spawning to identify the failing Windows access check. Production
  startup remains unchanged; native delegation acceptance is still pending.
- Nested restricted Python/Bun still reproduces EPERM. The owned named-pipe
  probe rejected an unsupported open-mode bit (87); corrected its documented
  mode/object type and added a diagnostics-only medium-integrity comparison on
  the same disposable workspace. Production remains low-integrity for tasks.
- Low/medium diagnostic spawning has the same piped-stdio EPERM. The pipe
  probe now uses the exact duplex/WRITE_DAC open mode used by the runtime;
  READ_CONTROL is obtained through duplex access rather than an open-mode bit.
- The exact named-pipe server mode succeeds and its client open is denied
  with Win32 5. SecurityInfo type 1 returned 87; diagnostics now compare types
  and use the handle-based kernel security API to read/change only the probe
  pipe DACL. This is not a product policy change or an acceptance pass.

- Native Windows evidence now confirms the cause: default named-pipe DACL
  contains SYSTEM/Administrators/creator full access and Everyone/Anonymous
  read, omitting the restricted capability even though TokenDefaultDacl includes
  it. Client open returns 5; applying that token DACL to the owned probe pipe
  returns 0 and its client succeeds. Label is already Low.
- Added a Windows x64 process-local pipe adapter built from project source and
  pinned Microsoft Detours 4.0.1 (MIT notices included). It supplies only default
  pipe DACLs from the existing restricted token and loads in suspended x64 child
  processes before execution. Host loading is inactive; token restrictions and
  filesystem grants are unchanged. Injection/start failures terminate the child.
  Added nested Python/Node/Git captured-output and private Low-label read/DACL
  modification denial assertions to native acceptance. Native result pending.
- Local platform check first hit sandbox-denied loopback sockets (13 passed,
  6 skipped, 3 setup errors); rerun with loopback permitted preserves all tests.
- Permitted-loopback local platform rerun: 16 passed, 6 native-OS skips,
  0 failures/errors. Ruff E9/F63/F7/F82 and whitespace checks pass.
- Host adapter loading now explicitly releases its DLL handle after updating
  the suspended child, so disposable service directories remain removable.
  Native fixture compiler failures include bounded compiler output.
- First adapter run: native Bun async/sync/native captured spawning all pass,
  nested Node/Git and private Low-label/DACL denial pass. Actual OpenCode starts,
  completes tools and owner question/form round trips. The run remains failed:
  host DLL handle prevented fixture cleanup (fixed above), and the tool-result
  OS-denial text assertion needs the actual message shape before adjustment.
  Removed now-completed startup probes and enabled private per-process Git
  core.longpaths to prevent observed snapshot path-length failures.
- Second native run: 41 passed/3 skipped, DLL cleanup now passes. The only
  failure is the API contract assertion: actual Windows write tool returns
  structured tool.execution / "Unable to write <path>", stripping Win32 code.
  Acceptance now checks that exact error and independently attempts an outside
  write through the shell, requiring the OS denial and absence of both files.
  Per-service Git global config sets core.longpaths (environment-only setting
  did not prevent snapshot init warnings). The sanitized environment retains
  executable PATH; filesystem access still uses the restricted token. Windows
  shell acceptance additionally runs the installed Node executable from PATH.
- Linux native API fixture now passes through result application with an
  immutable /usr negative target. An earlier synthetic /tmp target addressed
  bubblewrap's disposable temporary tree, so its successful virtual write was
  not an original-project write; the host source remained unchanged. A long
  provider Unix socket fixture also hit its address-length limit and was moved
  to a short test root. No product isolation policy changed.
- Third Windows run: 41 passed/3 skipped. OpenCode completes assignment/forms,
  structured denied write and inside write. Retaining PATH selected PowerShell,
  whose sandboxed startup exited 53. The runner now explicitly selects built-in
  cmd.exe, while retaining installed command PATH. Assertions inspect actual
  tool output rather than assistant command strings. Core.longpaths environment
  and private global config did not remove snapshot init warnings; native data
  uses a Windows extended path prefix for the next acceptance run.
- Fourth native run still completes OpenCode assignment/forms and private
  isolation, but the shell fixture lacks its quoted node -e marker. The probe
  now uses node --version and reports actual tool output. Extended path prefixes
  also did not resolve Git snapshot init (Git normalizes them to //?/C:); the
  runner now uses the existing Windows short alias for its private data home
  where available, preserving the same directory and permissions.
- Russian/English editable and PNG before/after diagrams now identify the
  Windows pipe adapter; node labels fit their boxes. Windows cmd/x64/PowerShell
  limits are recorded in the architecture and matching release descriptions.
- Fifth Windows native run confirms real Git/Node output and successful Git
  snapshot operations with the data-home short alias (no prior init warning in
  captured logs). The remaining negative shell check had invalid cmd quoting;
  it now uses the equivalent relative path to the original synthetic source,
  requiring Access Denied and no created file. Existing private file read/write
  and Low-label DACL-modification denial assertions remain unchanged.

### 2026-10-18 — OpenCode delegation 0.2.10 release acceptance

- All 11 required checks passed on public head `48c0214d2ed6d68a49e222849532b3ab8ca18978`.
  PR 33 merged normally through the protected branch as
  `571d14a96d44f46ae29c7f165024a8ef1f9ad1cd`. Its tree matches canonical
  `f75222294e44c379b1758ed274c2696edb324730` and the Windows CI merge tree.
- Both Python roots ran on Python 3.13 and 3.14: 4,886 collected (99 under
  `factory/tests`, 4,787 under `tests`), 4,851 passed, 35 skipped, zero failures
  or errors, and 630 passing subtests per interpreter. Skips comprise six
  Windows-only contracts, ten existing TUI running-state fixtures, eighteen
  KVM/vsock/namespace-dependent checks and one live-provider regression.
  Skipped VM, vsock and namespace checks are not acceptance passes.
- Python 3.13 coverage: 46,615/79,076 statements and 13,073/28,258 branches;
  combined coverage 55.6096%. Python 3.14: 46,609/79,070 statements and
  13,017/28,258 branches; combined coverage 55.5549%. These figures do not
  measure native C++ adapter line coverage. Desktop contracts passed on Linux
  and Windows; the final local desktop run passed all 364 tests. Android unit,
  lint and debug build passed without Android source/version changes.
- Windows Server 2025 x64 acceptance passed restricted filesystem access,
  private Low-label read/DACL-change denial, captured nested Node/Git output,
  job termination, clipboard denial, manual project editing and exact ACL
  restoration. The actual NSIS installer, reinstallation, updater invocation,
  retained synthetic data and packaged relaunch passed with spaces/Unicode.
  The native UI passed all nine baseline stages plus live OpenCode right-panel
  navigation, nonblocking execution, exact form/question replies, denied outside
  writes, independent task work and checked result application.
- Linux native acceptance passed all nine baseline stages, history/restart,
  approvals, owner forms and release notes. Local acceptance with the fresh
  runtime used the existing machine-local AppArmor permission for a byte-identical
  Electron 44.7.0 executable, with Chromium sandbox enabled. An initial
  sandbox-disabled synthetic GUI run was not counted as sandbox acceptance.
  The separate real OpenCode 2.0.27 fixture passed shell execution, owner input,
  an immutable outside-write target, independent work and result application.
- Built fresh runtime, snapshot, DEB, AppImage and wheel from clean canonical
  sources. The release wheel hash matches the runtime wheel hash. Extracted
  AppImage verification matched 6,406 manifest entries; the same six build-only
  omissions (static libraries, Python object and build ignore file) remain.
  Extracted Windows installer verification matched all 2,748 manifest entries.
  All application/backend sources and all 433 actual project-wheel files match;
  no private data was included. Initial inventory scripts incorrectly assumed
  that non-packaged source examples/tests were installed; verification was
  corrected to compare the actual wheel inventory. No package exclusion or
  acceptance assertion was removed to obtain a pass.
- A synthetic 0.2.9 to 0.2.10 DEB update retained the encrypted Vault connection,
  release-note preferences and previous generation, and installed the exact new
  source bytes. The old 0.2.9 public updater subsequently discovered 0.2.10 for
  DEB, AppImage and Windows and downloaded the public DEB with the same verified
  SHA-256 as that tested update.
- Published preview `v0.2.10` on GitHub and the canonical Gitea mirror with the
  same ten assets: three desktop installers, wheel, four Russian/English PNG/SVG
  diagrams, build manifest and checksums. Published descriptions, tags, asset
  inventory and digests were read back. GitHub tag targets the protected merge;
  Gitea tag targets the canonical source commit. Published diagram bytes and
  their release links were verified. The legacy gh GraphQL PR-edit operation
  failed because of deprecated Projects; the final description was applied and
  read through REST instead.
- Windows remains an unsigned preview tested on Server 2025. Physical Windows
  11 acceptance is pending. Windows commands use cmd.exe and x64 children;
  PowerShell, x86 children, Linux network namespaces, KVM and host-services MCP
  are outside Windows support in this version. Android remains 0.1.106 (107),
  with no new APK. Users must restart the application/runtime and chat after
  updating; the running owner installation and services were not replaced by
  these synthetic checks.

## 2026-10-21 - Android 0.1.115 (116): notification-only failures and media change counters

- System/operation/task failures are presented only in the notification center. The shared bell is available outside chats, including session lists and connection controls. Timeline error records remain in data but are excluded from the chat feed. Local failure relaying is sanitized, bounded and idempotent; existing quiet transport incidents are not promoted. Normal progress, approval controls and security/capability state remain.
- Video/audio/image changes have media labels instead of textual line counters or copyable diffs. Mixed totals ignore legacy media counters; ordinary code counters and nullable unavailable text comparisons retain their existing behavior.
- Android production source only; no Python/desktop/backend installation changes, provider calls, owner-service restarts, commits or public release. Existing pending profile/notification work was preserved. Version advanced from the pending 0.1.114 (115) to 0.1.115 (116); matching Russian/English notes include the pending changes and these fixes.
- Verification: the complete Android JUnit suite passed **373/373**, zero failures/errors/skips; lintDebug, assembleDebug and assembleDebugAndroidTest passed. The final shipping build used the supported `igorekTestRunner=ru.igorek.remote.NotificationOnlyInstrumentation` property.
- Native acceptance: isolated Android 14/API 34 emulator, actual host KVM and software GPU, separate ADB server/device; no physical device or owner app was touched. `NOTIFICATION_ONLY_UI_PASS`: session list hides the injected global timeout while the bell retains it; failed task/HTTP details appear in the center only; MP4 +90000 is replaced by Video, mixed Python +12/-3 remains accurate. Four screenshots from the final shipping APK were captured and inspected. Shipping APK bytes match the installed base APK. Physical-phone acceptance is not claimed.
- Earlier attempts retained honestly: an initial launch used the wrong working directory; a separate offline Gradle cache lacked the Android plugin and was replaced by the existing complete cache. First product run had one new regression fail (local same-text notices from different owners were suppressed); corrected, then the full suite passed. The first native launch targeted an unregistered runner and failed before assertions; rebuilding the test APK with the supported runner property passed. Additional terminal handshake/read-error presentation paths were then fixed, and the full suite, lint, build and native checks were repeated successfully against the final artifact.
- Artifact: `build/android/igorek-remote-0.1.115-v116-debug.apk`, 23,232,467 bytes, SHA-256 `7e6d737ac5c3e3c3b394ad69dc03633008f7096994fc57686ea40175545060aa`. Evidence: `build/verification-notification-only-116/shipping/summary.json` and four native screenshots. Android app update/reopen is required; gateway/runtime restart is not needed.

## 2026-10-22 - Unreleased: OpenAI-compatible streaming token usage

- Chat Completions streaming requests now request `stream_options.include_usage` by default while preserving caller options and an explicit opt-out. The request object is not mutated. Native Anthropic and Responses routing remain unchanged.
- Missing, partial or invalid token statistics remain unavailable instead of becoming measured zeros. The plan panel retains round counts and latency, distinguishes unknown totals from real zero counts, and renders the last-request context bar only when its input count is known. Russian and English unavailable labels are included. Counts update after a completed model round; live generation estimates are not added, and historical fabricated zeros cannot be reconstructed.
- Original-trigger fixtures failed against the previous code, then passed after the fix. A real isolated localhost SSE fixture verifies the usage opt-in, terminal usage-only chunks, reasoning-inclusive completion counts, explicit opt-out and unavailable statistics. Plan-panel regressions cover percentages, real zeros, partial counts, unavailable totals, subsequent known requests and durable-event deduplication.
- Full Python 3.14 verification through the isolated test runner: 5,038 collected, 5,004 passed, 34 skipped, zero failures/errors, 651 passing subtests. Existing skips cover six Windows contracts, ten TUI running-state checks, seventeen KVM/vsock/environment contracts and one unconfigured live-provider regression. Coverage: 47,626/80,054 statements and 13,410/28,704 branches; combined coverage 56.1209%.
- Locked development dependencies were checked. The full desktop suite under Node.js 24.11.0 passed 405/405 with zero failures/skips. Five additional isolated headless-Chrome DOM checks passed, including a visible 25% context bar and an unavailable-state view without a false zero bar. The new Python regression file passes Ruff; `git diff --check` passes.
- Source-only change: the installed runtime and desktop files were verified unchanged. No owner service/model inference calls, restarts, installation, version bump, commit or publication were performed. Applying the fix later requires updating/restarting the desktop runtime; the model server does not require a restart for this telemetry fix.

## 2026-10-22 — Specific notification causes and confirmed recovery

Tool failures now retain tool/service/action, an explicit cause code and bounded redacted detail. Android converts bridge seconds once, repairs legacy fallback timestamps, and reconciles known incidents against later successful calls without creating historical notifications. Recovered incidents stay out of the list/badge. Unknown outcomes and failures of different resources remain active. Rejected validation calls can resolve after successful task completion with a distinct non-execution proof. Desktop/Web and model observations preserve explicit safe reasons. No execution routing, retries or model capability handling was changed.

Local verification: 427 Android unit tests, lint zero errors (39 existing warnings, two hints), androidTest compilation; 408 Desktop tests including parallel uncommitted usage tests. Full Python run: 5042 collected, 5007 passed, one privacy failure, 34 skips, 651 subtests; the arbitrary provider-code leak was fixed without relaxing the existing assertion. The complete OpenAI Responses module and diagnostic suites then passed: 92 tests and 14 subtests. Clean publication CI and native Android acceptance are pending and must not be reported as passed yet. Version targets: Desktop/Python 0.2.19; Android 0.1.120 (121).

## 2026-10-22 — Mobile reasoning code and whole-task elapsed time

Expanded Android reasoning now uses the existing Markdown renderer, including fenced code, language captions, syntax highlighting, horizontal scrolling and copying raw code. Collapsed previews remain bounded. Successful task cards show the interval between task execution start and finish; compatible bridge task metadata restores missing history boundaries. Queue creation and tool durations are not substituted for missing execution times.

Verification: all 435 Android unit tests passed with zero failures/errors/skips; lint reported zero errors (39 existing warnings and two hints); app and instrumentation APK assembly passed. Mobile bridge/service/event and diagnostic Python suites passed 136 tests and five subtests. Eight new timing regressions cover task isolation, replay, missing/reversed boundaries, history restoration and seconds/milliseconds. Native UI acceptance for rendered/copied reasoning code and the completion duration is included in the disposable-device scenario and is pending CI; no physical-device acceptance is claimed.

## 2026-10-22 — Offline Three.js artifact preview

The opaque artifact sandbox blocked the CDN script required by matching Three.js r128 animations. Matching pinned cdnjs/jsDelivr/unpkg imports are now embedded from a verified local MIT-licensed copy, without granting external network access. Processing is bounded to 4 MiB HTML and four substitutions; original downloads, oversized documents and unknown library versions remain unchanged. Native CI covers changing real Three.js frames and preserved interface/Node/network isolation.

Verification: the Web UI and preview suites passed 158 tests and 132 subtests; two additional real HTTP regressions then passed within the final 11-test targeted run (three subtests), covering rewritten preview Content-Length, byte-identical downloads and complete oversized streaming. Ruff executable-error checks and JavaScript syntax checks passed. Isolated Electron 44.7.0 with Chromium sandbox and explicit test-only SwiftShader rendered sixteen distinct frames for all three pinned library URLs, with zero external/interface requests and no Node/parent access. The actual reported local artifact also rendered and animated without source modification; its private screenshot is not part of public evidence. Xvfb without a software renderer initially rejected WebGL as blocklisted; that failed attempt is not hardware acceptance. CI uses explicit software WebGL for this synthetic scenario; physical GPU acceptance is not claimed.

## 2026-10-22 — Diagnostic report reopening and release acceptance follow-up

A native Linux run exposed a real rapid cancel/reopen race: a closed report dialog could still exist until its asynchronous close event, causing the next open action to return without opening a dialog. A regression failed before the fix and passed afterward. Open reports are still focused; closed remnants are removed before a new dialog is created. Fourteen focused diagnostics tests and the complete local Desktop suite passed (409 cases, including five parallel usage tests excluded from this release).

The imported Wiki originals were restored byte-for-byte and the new mobile/preview explanations moved into the established updates layer. All 72 imported originals match recorded hashes. The Three.js MIT notice is included. Previous clean-source CI passed Python 3.13/3.14, Desktop Linux/Windows and both package builds; the separate native 3D fixture produced sixteen changing frames for each of three pinned URL forms, with zero external/interface requests. The full Linux scenario failed on the dialog race above; its overall acceptance is not claimed yet. Android context scenarios passed; notification cause/recovery/history, reasoning formatting and task duration reached the expected native UI, then the copy-button locator failed. The locator now refreshes all accessibility windows; both row-position and exact clipboard assertions are retained, and failure evidence includes bounds and the UI tree. Final-source native/CI acceptance is pending.

## 2026-10-22 — Isolate native Android fixture lifecycle

The second clean CI run passed both complete Python suites, both Desktop suites, native Linux, both package builds and Wiki. Android again lost its injected fixture and displayed MainActivity's pairing screen during the unchanged cloud-context phase; recorded state still contained the expected window and input values. The two instrumentation scenarios now host the actual production composables in a dedicated non-exported debug Activity with no launcher entry, avoiding MainActivity's initial root and startup effects. No context, recovery, code-copy or clipboard assertion was removed. All 435 unit tests, lint, app assembly and instrumentation assembly passed locally after this change; native acceptance remains pending the next clean CI run.

## 2026-10-22 — Release 0.2.19 and Android 0.1.120 (121) verified and published

The final protected source is `1cabfc5bd8ead3799acad69a50e64d0c6fff6252` (PR 43); all thirteen main checks passed. Diagnostics retain actionable causes and confirmed recovery, repair legacy fallback dates, and avoid historical notification replay. Android formats expanded reasoning code and displays completed-task elapsed time. Matching Three.js r128 imports render offline in the opaque artifact sandbox. Rapid diagnostic-report cancel/reopen is fixed.

Python 3.13: 5,048 collected, 5,013 passed, 35 skipped, zero failures/errors and 647 passing subtests; line coverage 47,713/80,150, branch coverage 13,490/28,730.

Python 3.14: 5,048 collected, 5,013 passed, 35 skipped, zero failures/errors and 647 passing subtests; line coverage 47,711/80,144, branch coverage 13,435/28,730.

The 35 Python skips comprise six Windows contracts, eighteen real VM/vsock/namespace environment checks, ten TUI running-state requirements and one unconfigured live-provider regression; exact reasons are retained in the release manifest. Desktop Linux passed 404/404. Desktop Windows passed 403 with one Linux-only installer-helper skip; native Windows install/update acceptance passed separately. Android passed 435 unit tests without failures/errors/skips; lint has zero errors (39 existing warnings, two hints). Real disposable Android 14 UI checks passed context changes, notification causes/recovery/legacy replay, displayed/copyable reasoning code and the completed-task duration; screenshots were inspected. The two component scenarios use a non-exported debug host; physical-phone acceptance is not claimed.

Final DEB, AppImage and Windows EXE came from actual main CI, and the clean-source wheel and shipping APK were built from that protected commit. Every package inventory, application/backend source and all 445 project-wheel members matched. Linux 0.2.18 installation, update to 0.2.19, reinstall, rollback and return passed in an isolated prefix with retained synthetic data. The shipping APK keeps the previous debug signing identity; the CI runner has a different signer, so identical tested APK bytes are not claimed. SHA-256 of Android `igorek-remote-0.1.120-v121-debug.apk`: `c2f09c7240c786b036d0a789d8872bb8ea73ad236de7fd5a8850e24b14c182a8`.

Published matching seven-file preview releases on GitHub and Gitea: three desktop installers, APK, wheel, build manifest and checksums. All seven actual downloads on both hosts matched; published asset identities, descriptions, tags and server digests were read back. The real 0.2.18 public updater discovered 0.2.19 for DEB/AppImage/EXE and downloaded the exact DEB. Four newly updated English/Russian Wiki sections were checked on the live site; imported originals remain unchanged. The APK was delivered through the configured private channel with a successful receipt retained locally.

Limits: Windows is unsigned and tested on Server 2025; native Linux uses Ubuntu 24.04. 3D acceptance uses isolated software SwiftShader with Chromium sandbox; product GPU settings are unchanged and physical GPU/Windows 11 acceptance is not claimed. Only matching pinned Three.js r128 imports are substituted; other external resources remain blocked. Fully restart Desktop/runtime and reopen Android after updating; opening the affected chat reconciles retained old tool incidents. Existing connections do not need changes. Owner installations, gateway and model services were not restarted by these checks.

## 2026-10-22 — Preserve Desktop notification selection (0.2.20)

A native mouse-drag fixture reproduced selection disappearing when the notification center received an unchanged background snapshot. Replacing all card nodes also left the native context menu without selected text to copy. The center now ignores unchanged snapshots and defers background list rendering while a selection or left-button gesture is active. The unread badge and action availability still update; releasing selection applies the newest snapshot. Explicit read/clear actions and panel closing flush the pending state. Details explicitly allow text selection. Journal data, incident recovery and the native menu policy are unchanged.

The original trigger failed before the fix. An isolated sandboxed Electron 44.7.0 fixture then passed actual mouse selection, repeated and changed snapshots, a live unread badge, the native context-menu Copy command, Ctrl+C, deferred text/count updates, retained expansion, immediate Mark all read and immediate Clear. Clipboard contents matched the exact selected text. The complete local Desktop suite passed 409/409, including five parallel usage tests excluded from this release. JavaScript syntax and diff checks passed. Native Linux/Windows clean-package CI remains pending. Python/Desktop versions are 0.2.20; Android source and published 0.1.120 (121) are unchanged and require no new phone update.

## 2026-10-22 — Desktop 0.2.20 notification copying release verified

Published protected source `9d4e26241061cb1c45eee5db965ef15c718821e4` (PR 44), after all thirteen main checks passed. Repeated notification snapshots no longer destroy mouse-selected text. Genuine list updates wait until selection ends while the unread badge remains current; explicit read/clear actions apply immediately. The existing native context-menu Copy command and Ctrl+C retain the selected text. Journal and recovery behavior are unchanged.

The original native trigger failed before the fix. Both Linux and Windows packaged-source acceptance then passed actual mouse selection, repeated/changed snapshots, a live unread badge, native menu Copy, Ctrl+C, pending update delivery, retained expansion and immediate read/clear. Native screenshots were inspected. The clean Desktop suite passed 404/404 on Linux and 403 with one Linux-only installer-helper skip on Windows. The local original checkout also passed all 409 tests, including five parallel usage tests excluded from the shipped source. A missing local build dependency in the reusable clean checkout was restored through the locked npm install; no dependency versions or assertions were changed.

Python 3.13: 5,048 collected, 5,013 passed, 35 skipped, zero failures/errors and 647 passing subtests; line coverage 47,715/80,150, branch coverage 13,491/28,730.

Python 3.14: 5,048 collected, 5,013 passed, 35 skipped, zero failures/errors and 647 passing subtests; line coverage 47,702/80,144, branch coverage 13,434/28,730.

The 35 Python skips consist of six Windows contracts, eighteen VM/vsock/namespace requirements, ten TUI running-state requirements and one unconfigured live-provider regression; precise reasons remain in BUILD_MANIFEST.json. Android unit/lint and the existing native component scenarios passed, but Android source is unchanged at 0.1.120 (121). Its entire source tree and shared generated renderer inputs match 0.2.19. The companion APK is the same published file, SHA-256 `c2f09c7240c786b036d0a789d8872bb8ea73ad236de7fd5a8850e24b14c182a8`; no new Android shipping build or duplicate delivery was required.

DEB, AppImage and EXE were built by actual main CI; the wheel came from the same clean protected commit. Package inventories, application/backend sources and all 445 project-wheel members matched. Isolated Linux install 0.2.19, update to 0.2.20, reinstall, rollback and return all passed with retained synthetic data. GitHub and Gitea contain identical seven-file preview releases. Every actual download on both hosts matched, and published descriptions, tags, asset identities and digests were read back. The real 0.2.19 public updater discovered 0.2.20 for DEB/AppImage/EXE and downloaded the exact DEB. The live English/Russian Wiki copy sections were checked.

Restart Desktop after updating; Android requires no update for this Desktop fix. Existing connections remain valid. Preview limits remain: unsigned Windows installer tested on Server 2025, Linux on Ubuntu 24.04; physical Windows 11 and phone acceptance are not claimed. Owner application/model/gateway installations and running services were not changed or restarted by these checks.

## 2026-10-27 — Session goals: local verification

Goal state, automatic continuation, model tools, isolation relay, terminal commands and compact Desktop/Android controls were checked with synthetic data. A real worker and a local synthetic provider exercised continuation and completion. The goal-store and goal-tool modules each reached 100% line and branch coverage.

Python verification covered 585 test files: 11,492 passed, 21 skipped and 906 passing subtests after replacing 22 results affected by the forbidden `/var` temporary path with successful runs under `/tmp`, and rerunning the updated two-test web history file. The original run's web fixture lacked the newly required goal panel; the updated fixture also checks that switching sessions clears and reloads the goal. The combined coverage report measured 85.00% overall, 87.18% lines and 78.99% branches, versus 84.97%, 87.16% and 78.92% in the saved baseline. Both reports warn that two native-test coverage data files could not be combined; the comparison has that shared limitation.

Desktop: 525/525 automated tests passed in an isolated source copy with lockfile dependencies; lifecycle scripts were disabled during the offline dependency installation. Android: 487/487 unit tests passed and the debug APK for 0.1.128 (129) built; 202 Android source paths matched the isolated build copy. Physical devices and real KVM/vsock acceptance were not checked. External Codex and Claude backends cannot yet control the goal; automatic continuation is stopped for them rather than repeated indefinitely. No release or installation was performed.

## 2026-10-27 — Android slash-command suggestions (0.1.129 / 130)

Typing `/` in the ordinary mobile composer now lists implemented goal commands with localized descriptions and filters them by prefix and current goal status. Selecting a suggestion fills the draft and places the cursor after its argument separator. The placeholder advertises commands. MCP input keeps its existing behavior. Ordinary drafts, selection and multiline text retain their meaning.

The complete Android unit suite passed 492/492, with no failures or skips. Five new tests cover ordinary/unknown/skill/path/multiline inputs, command-state eligibility, prefix matching, parser compatibility, argument separators and both languages. JaCoCo measured the new filtering function at 16/16 lines and 18/18 branches; this is scoped algorithm coverage, not coverage of the whole application or Compose UI. Four targeted mutations were all killed by assertion failures: removing filtering, reversing pause eligibility, dropping the argument separator and making matching case sensitive. Original source was restored before the final full test and build. Lint completed with zero errors and 42 warnings.

Native instrumentation exercised the production SessionScreen on an owned Android 14 emulator with KVM, including a narrow 360dp screen and 1.3 font scale. It verified tap completion and subsequent typing, active/paused controls, no automatic pause/resume/edit/clear on selection, disabled rows, cursor retention during a goal update, insertion in the middle of ordinary text and multiline preservation. These checks use synthetic session state and do not establish server-side goal execution or physical-phone acceptance.

The final APK source inventory matched all 206 Android paths. Debug APK 0.1.129 (130), SHA-256 `edaba656a3fca8c397cbbb456ee63b045a6a05cefdf34829fc2021397af7831b`, retains the preceding APK's signing certificate. Evidence is retained locally under `build/slash-*`. Python/Desktop source was not changed for autocomplete; their previous verification was not rerun or counted as new work. Installing the Android update enables local suggestions without a runtime restart; actual goal execution still needs the corresponding server implementation.

## 2026-10-27 — Android command highlighting (0.1.130 / 131)

Recognized `/goal` commands and their complete control actions use the current theme's primary color and semibold weight in the composer. The objective, ordinary text, unknown commands and partial prefixes retain their normal appearance. The text and cursor offsets are unchanged. MCP input retains its existing appearance.

The complete Android unit suite passed 496/496 with no failures or skips, including four new visual-transformation tests for command boundaries, all four actions, original styles, Unicode and identity cursor offsets. JaCoCo measured the new transformation class at 9/9 lines and 2/2 branches; this scoped figure does not describe coverage of the whole Android application. Android lint reported zero errors and 40 warnings. Native instrumentation on an isolated KVM Android 14 emulator passed in light and dark themes; screenshots confirmed the command uses the theme accent while the objective keeps the normal text color. The same native scenario verified selection, subsequent typing, normal editing, paused and active controls, and no automatic command execution. No physical device or live goal execution was checked.

All 208 Android source paths matched the isolated build copy. Debug APK 0.1.130 (131), SHA-256 `35767b94ecec70d32c150926e602c90431e225f603be4509cb209585f5d72dcc`, uses the same signing certificate as 0.1.129. This change affects the Android UI only; Python/Desktop coverage was not recalculated. The new UI becomes available after installing the APK and needs no server restart; goal execution still requires the server update.

## 2026-10-27 — Android help, status, next-turn plan and launcher icon (0.1.131 / 132)

The mobile composer now offers `/help`, `/status` and `/plan` alongside `/goal`. Help and status are local dialogs using available session, context and quota data. Plan mode applies to the next ordinary request and is shown in the composer; unsupported backends and active tasks are rejected instead of claiming read-only execution. The server also rejects non-boolean plan flags and rejects a plan request that would otherwise steer a running task. External Codex and Claude backends remain unsupported for plan mode. `/fast` and `/ultrafast` were not added.

The Android launcher icon now uses the existing blue octopus image as an adaptive icon. The full Android unit suite passed 501/501 with no skips. It includes five new tests for exact local-command parsing, real status values and unavailable data, message serialization, and an authenticated TLS message request carrying the plan flag. Lint completed without errors. Native checks on an isolated Android 14 emulator exercised the actual chat composer, help and status dialogs, plan toggling, goal completions, cursor preservation, and a non-clickable suggestion. The launcher screenshot confirms the octopus icon in the app drawer. Physical-phone and installed-server acceptance were not performed.

The final Python run covered 587 files: 11,500 passed, 21 skipped and 906 passing subtests, with no failures or errors. Six skips require Windows; 15 require real KVM/vsock or live isolation configuration. The previous run exposed two old tests whose fixtures did not define a supported backend and whose malformed plan flag was historically accepted; those tests were updated to define an explicit `cc-backend` and require rejection of the invalid flag. A separate focused run passed 221 tests and five subtests, including actual HTTP authorization, invalid flags, an active-task conflict and unsupported-backend rejection.

Final combined Python line/branch coverage was 85.0066%, up from the prior 85.0000%; lines were 87.1887% versus 87.1838%, and branches 78.9973% versus 78.9850%. Both measurements have the existing limitation that two native-test coverage files could not be combined. All 214 Android source paths matched the isolated build copy. Debug APK 0.1.131 (132), SHA-256 `277e564ff4eef5b8112398f09e096ecadc040a1e3b9043bd7d49cb254a61e5a7`, uses the preceding APK's signing certificate. Server changes remain uninstalled and require a runtime/gateway restart after deployment; Android installation alone does not update the server.

## 2026-10-27 — Persistent mobile plan mode button (0.1.132 / 133)

The `/plan` command was removed. A labeled plan-mode switch now sits immediately beside the connection indicator; its color and accessibility switch state show whether it is enabled. A user's selection is stored per connection and chat and remains enabled across messages, profile changes and app restarts until explicitly disabled. When an active task exists, the UI explains that the change takes effect after that task. A request through a direct MCP conversation is blocked while plan mode is active. Unsupported profiles cannot send an ordinary message while the mode is enabled.

The server also refuses to steer an active plan-mode task with an ordinary request, complementing the existing rejection of a plan request while another task is active. This prevents a message from silently using a different mode from the one selected. The focused existing and new mobile/server suite passed 222 tests and five subtests. The full Python run passed 11,501 tests and 906 subtests, with 21 skips: six Windows-only and 15 live KVM/vsock or isolation checks. No failures or errors occurred. Combined coverage was 85.0122%, compared with 85.0066% before this change; line coverage was 87.1938% versus 87.1887%, branch coverage 79.0039% versus 78.9973%. Two native-test coverage data files could not be combined in either comparison.

Android unit tests passed 502/502, and lint reported no errors. Native Android 14 emulator tests verified the actual switch beside the status icon, accessibility state, light/dark UI behavior, per-chat persistence, user-controlled disabling and survival of a process restart. Two successive requests sent over HTTPS to an isolated synthetic server both carried `plan_mode: true`; a third after disabling carried `false`, and an unsupported-profile request did not reach the server. These checks do not establish physical-device or installed-server acceptance. All 217 Android source paths matched the isolated build copy. The debug APK 0.1.132 (133), SHA-256 `3d6e11e339249b981d826a862dbaf8b588052db94d9aa1ea9dfde6802c958cdf`, has the preceding APK's signing certificate. Server changes are not installed; the runtime and gateway must be updated and restarted to use the new server-side guards.
