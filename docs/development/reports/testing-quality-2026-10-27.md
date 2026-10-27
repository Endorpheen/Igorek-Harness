# Testing quality investigation — 2026-10-27

## Final local assessment

All 11 repository risk areas have selected-scenario reviews recorded in the
[risk map](repository-risk-map-2026-10-27.md). Nine confirmed product defects and
one test-runner defect were fixed in the working tree. A selected mutation set
detected 18 of 18 faults; a separate Desktop redirect mutant survived its old
test and was detected by a new assertion. These are deliberately chosen samples,
not a repository-wide mutation score.

The split Python run before the subsequent version and release-note changes passed 11,413 tests and 906 subtests with 21 platform
skips and zero failures/errors; the locked Desktop suite passed 521 tests. A
separate Android source snapshot matching all 199 tracked Android files passed
483 JVM tests, lint and debug assembly. An isolated current Python wheel matched
all 405 packaged module files to the reviewed source and passed ten recovery
tests after installation. The Python run used one and two workers instead of
CI's four; two native coverage files could not be combined, so no reliable
overall coverage delta is claimed.

The 15 KVM/vsock and six Windows skips are not passes. Physical Android, real
external services, power-loss durability, current-build Desktop installation
and rollback, exactly-once memory-provider effects, and orphan requeue-event
recovery remain unverified. The work and package are not installed into the
user's environment. Later sections preserve the chronological investigation;
historical "open" or "pending" labels describe their point in time rather than
the final disposition. See [before/after progress](testing-progress-2026-10-27.md)
and [TestLOG](../TestLOG.md) for the current status and individual checks.

## Risk and method

This is a targeted investigation, not a repository-wide mutation score or a security certification. The first priority is preservation of user data during file replacement and interruption; next are workspace boundaries, permissions and concurrent session writes. Existing large suites exercise real temporary files, processes, checkpoints and structural isolation. A full VM, physical device, power-loss experiment and external service outage were outside this run.

The chosen approach follows the supplied references: use coverage to locate gaps rather than equate it with quality ([Google coverage](https://testing.googleblog.com/2020/08/code-coverage-best-practices.html)); choose useful fault models rather than optimize a mutation percentage ([Google mutation testing](https://testing.googleblog.com/2021/04/mutation-testing.html), [Meta](https://engineering.fb.com/2025/02/05/security/revolutionizing-software-testing-llm-powered-bug-catchers-meta-ach/)); use real component interactions where they matter ([Google larger tests](https://abseil.io/resources/swe-book/html/ch14.html), [Microsoft](https://learn.microsoft.com/en-us/azure/well-architected/operational-excellence/testing)); and inject controlled failures without disturbing user services ([AWS](https://docs.aws.amazon.com/wellarchitected/latest/reliability-pillar/rel_testing_resiliency_failure_injection_resiliency.html)).

## Mutation experiment

Four hand-selected, single changes were made one at a time in a temporary copy of `factory/core/artifact_files.py`. The same two existing platform cases and the three new cases were run against each change; baseline passed. A killed mutant means a test failed for the intended behavioral reason, not that a production defect existed.

| Fault injected | Existing selected tests | After new tests | Evidence |
| --- | --- | --- | --- |
| Do not remove staging file after an exception | Survived | Killed | Staging file remains after failed input |
| Do not preserve existing file mode | Survived | Killed | Restrictive mode changes after retry |
| Follow a parent directory symlink | Killed | Killed | Existing boundary test observes external access |
| Stop reading after the first input chunk | Survived | Killed | The input error is hidden and incomplete content would be accepted |

This is 4/4 detected in this deliberately small selection; it is not representative of all mutants or the whole codebase. The three surviving changes in the existing selected cases identified specific missing assertions, not proven defects in the unmodified implementation. The mutation script and logs are retained in `build/verification-testing-quality-20261027/` on the test host.

## Failure, concurrency and security evidence

`tests/test_artifact_failure_security.py` checks a real replacement against a failing input stream, preservation of old bytes and permissions, cleanup on a handled error, and a successful retry. A separate spawned process is terminated before replacement, after which the original remains readable and retry succeeds. Another test swaps the destination for a symlink immediately before replacement and checks the external file is unchanged. The process-death test does not assert cleanup of orphan staging files, power-loss durability, or `fsync` semantics.

The new tests and the existing cross-process session metadata/message race tests passed ten consecutive runs with a 20-second per-test timeout: 60 test executions, zero failures or timeouts. This is a bounded observation, not a proof that races or flakiness do not exist. The security checklist and concurrent tests passed outside the restricted sandbox: 55 passed, 20 subtests passed, 10 skipped because live KVM probes were not enabled. The first sandbox run had four socket permission failures; these were environment failures, not counted as passes. No real guest isolation claim follows from the structural checks.

Existing `tests/test_file_mutation_contracts.py` uses actual temporary files for write, edit and read and checks checkpoint ordering; its checkpoint manager and tracker are test doubles. Existing `tests/test_schedule_corrupt_recovery18.py` covers corrupt scheduled result, retry and delivery through the service fixture. These are integration boundaries, not an end-to-end test of a live deployment. Full-suite results are recorded in `docs/development/TestLOG.md`. The run passed 571 files, but two native coverage data files could not be combined; the resulting coverage percentage is not a reliable comparison. The final POSIX-only permission assertion was adjusted after its full-suite file had run and was subsequently verified in the focused tests and mutation experiment. Initially npm was not in PATH; a local Node 24/npm was found and a subsequent locked desktop test run is recorded below. The long-running web UI file eventually passed; a hang was not established.

## Remaining priorities

- Exercise KVM/vsock isolation with the actual host provider and guest image and record each live verdict independently.
- Test crash/restart and recovery at the scheduler, storage and message-delivery boundaries with real processes, including duplicate delivery prevention.
- Run controlled repeated concurrency and stress scenarios with reproducible seeds and resource limits; ten repeats cannot rule out rare races.
- Investigate crash-left staging files and define expected cleanup and confidentiality behavior; distinguish process termination from power-loss durability.
- Broaden mutation sampling to authorization, approvals, recovery and retry logic, classifying equivalent and unproductive mutants before drawing conclusions.

## Progress toward the testing objective

The following statuses track evidence, not a percentage of all possible bugs. An area is complete only after the chosen high-risk scenarios are exercised, detected defects are fixed and re-tested, and the report states real platform limitations.

| Area | Status | Evidence available | Remaining |
| --- | --- | --- | --- |
| Mutation testing | In progress | Four file-writing mutants and five HTTP authorization mutants detected; a separate removed-lock mutant is detected by both delivery concurrency checks. | Sample authorization, delivery recovery and retries; evaluate surviving and equivalent mutants. |
| Integration and end-to-end | In progress | Actual files, session journals and SQLite-backed service fixtures, including two independent delivery processes. | Exercise a representative full user-to-worker-to-result flow with real processes and isolated data. |
| Failure recovery and data | In progress | Interrupted file input; killed writer before replace; killed delivery process between history and event, followed by retry. | Cover storage failures and recovery at additional commit boundaries and document power-loss limits. |
| Concurrency and stability | In progress | Sixty repeated file/session test executions; deterministic duplicate-delivery race reproduced, then protected by shared session lock. | Five repetitions of all three new delivery tests passed (15 executions); broader stress and adjacent competing operations remain. |
| Security and isolation | In progress | Structural negative isolation checks and a raced destination symlink. | Real guest boundary and permission tests depend on KVM/libvirt readiness; retain honest skipped/blocked results. |

On the host, KVM/vsock devices are available and system Python can import libvirt; the installed isolated runtime cannot import it without the system packages. The diagnostic confirms connectivity but is not a guest acceptance test.

The delivery defect was reproduced with two independent service instances: both read history before either append and wrote duplicate messages. The fix holds the existing cross-process session lock across the check and append. Three targeted checks passed: concurrent service instances, two spawned processes, and process death after the history append before event insertion followed by retry. Removing the lock in an isolated copy made both concurrency tests fail; the crash recovery test still passed. A subsequent isolated full run passed 572 files: 11,366 passed, 21 skipped, 906 subtests passed; two native coverage files could not be combined. No overall coverage comparison is inferred.

A synthetic KVM attempt failed during image preparation before guest startup. The reason is that libguestfs/supermin cannot read the host kernel (`/boot/vmlinuz-*` is root-only); the test-owned cleanup receipt reported no leftovers. No live guest or isolation pass is claimed.

## HTTP authorization mutation sample

In a separate source copy, the 11 selected parameterized HTTP checks passed on the original handler. Five individual mutations were then introduced one at a time: accept a wrong key, allow an unconfigured key, exempt `/tasks` from authentication, bypass POST authentication, and allow a foreign origin with no allowlist. All five caused the intended security assertions to fail (respectively 10, 1, 2, 2 and 1 failing cases). The tests use a local real HTTP server but replace backend endpoints with synthetic handlers; this is not proof for every route, protocol or deployment. The script and individual logs are retained in `build/verification-testing-quality-20261027/security-*`.

The [repository-wide risk map](repository-risk-map-2026-10-27.md) inventories tracked components and identifies existing evidence and pending checks for each. An inventory row is not a completed review.

## Desktop locked baseline

A first Node 24 run against the existing `desktop/node_modules` failed dependency security tests. Its Electron 44.7.0, Undici 7.30.0 and fast-uri 3.1.8 did not match the committed lockfile. In a separate tracked-source copy, Node 24.11.0/npm 11.6.1 ran `npm ci` and then `npm test`: 520 passed, no skips or failures; npm audit reported zero known vulnerabilities. This verifies the locked dependency baseline in this environment, not native Electron UI or Windows acceptance. The original installation was not modified and remains stale.

## Android baseline

An isolated source copy matched all 199 tracked Android files in the current working tree, including independent user changes. Java 17/Gradle 9.8.1 offline `testDebugUnitTest`, `lintDebug` and `assembleDebug` passed: 483 tests across 65 suites, no failures, errors or skips. The first interrupted copy lacked shared assets; the successful run included the shared emoji, renderer and diagnostics fixtures. This is not emulator, physical-device, installation or signing acceptance.

## Worker heartbeat versus stale cleanup

A deterministic test used an actual SQLite database and a separate cleanup process. After it selected a stale task, the other process updated its heartbeat before cleanup resumed. Before the fix, both `processing` and `cancelling` tasks were incorrectly finalized despite the fresh heartbeat. Cleanup now rechecks profile, status and timestamp in the conditional SQLite update, and reports the number actually changed. Existing genuinely stale tasks still finalize and other profiles remain untouched.

The focused set passed 36 tests; the two race cases passed ten consecutive repetitions (20 executions). Separately removing either timestamp condition in an isolated source copy caused the corresponding regression test to fail. A real child process also claimed a queued task and was terminated; its retained task was finalized as dead-letter after its timestamp was artificially aged, while another profile remained unchanged. That check does not wait five real minutes or simulate power loss. A full Python run was launched before this last standalone crash test was added; its final result and the separate crash result must be reported separately. Evidence: `build/verification-testing-quality-20261027/worker-*`.

## Test runner failure reporting

An actual quota exhaustion produced an empty JUnit XML and the runner raised a parse error while its executor waited for remaining jobs. A separate missing report with a successful process exit was incorrectly treated as success. Three tests reproduced empty, truncated and missing reports. The runner now records a report error and a nonzero exit status without weakening timeout behavior; the runner and coverage regression set passed six tests. This addresses report parsing, not all possible disk-full behavior.

A new full run remains unaccepted: `/tmp` quota exhaustion caused invalid results, and moving temporary data to a long project path produced AF_UNIX path-length failures. One affected socket case passed again using the ordinary short path. The previous full-suite success predates these worker and runner changes.

A third full-run setup used a short private directory on the home filesystem: 43 selected Unix-socket tests passed, but the full run was interrupted when the Debian package fixture exhausted disk quota there. The test-owned directory was removed after its processes stopped. A separate focused recheck of all current worker, session-delivery and runner changes passed 49 tests. These focused results do not replace the required full run.

## Mobile-to-worker end-to-end check

A synthetic loopback OpenAI-compatible provider, actual `MobileService`, SQLite queue and a separately spawned real `Worker` were exercised together. A normal stream produced the expected saved task output; repeating the same submission key retained the original task and one user message. A stream that ended after partial content without a terminal event did not produce success. The two scenarios plus related stream and queue checks passed 20 tests. No real provider, physical Android device, gateway process or published deployment was involved.

A targeted mutation disabling the terminal-event check made the interrupted scenario fail while the normal case passed. An initial apparent survivor was invalid: the spawned process imported the original checkout instead of the mutant. The test now verifies the source path in the worker, and only the repeated, controlled-source result is counted. Logs and the mutation harness are in `build/verification-testing-quality-20261027/e2e-*`.

## Open defect: retry after history write failure

With an isolated SQLite store, a forced failure of the history append after task creation leaves the queued task active without its idempotency receipt. Retrying the same message and key reuses that task but adds the same text to the steer queue, potentially delivering it twice. The one-task/one-visible-message assertions alone passed; an explicit zero-steer assertion exposed the error. `tests/test_mobile_submission_failure_recovery.py` reproduces the defect and currently fails. A correct fix must preserve already-claimed work, recover the journal, event and receipt, and handle concurrent senders and process interruption; this is not yet implemented.

## Partial fix and remaining steer recovery defect

For a new keyed submission, a recovery record is now stored in the task metadata and same-key requests are serialized using a cross-process lock. A retry can restore missing history, a deduplicated submission event and the receipt without cancelling an already claimed or completed task. Eight focused scenarios passed, including injected failures, two service instances and actual process death. A related compatibility run passed 144 tests and five subtests before the last focused cases were added.

A separate follow-up message directed to an active task still has a distinct failure: if its steer queue insertion succeeds but history append fails, retrying the same key inserts another steer entry. `test_retry_after_steer_history_failure_does_not_enqueue_duplicate` reproduces it and remains failing. No completion claim is made for this class of recovery, and full post-change acceptance remains outstanding.

## Follow-up message recovery

For a keyed steer message, the queue insertion and recovery record are committed in the same SQLite transaction. On retry, missing history, the idempotent event and receipt are restored without re-enqueueing an already consumed message. Controlled cases covered rollback, event and receipt failures, process death and a consumed queue entry. The combined mobile bridge, event, queue and end-to-end checks passed 179 tests and five subtests; the full suite remains unconfirmed due to quota.

Disabling the event-deduplication guard produced two events and the corresponding test failed. An initial apparent survivor was invalid because the original checkout had been imported; the repeated run verified the imported source path. The earlier “open steer recovery defect” entries describe the before state; they are retained as historical evidence, not current status.

## Separate mobile database compatibility

The first keyed-steer recovery implementation wrote to the mobile store database and silently left the worker queue empty when a separate `MobileStore` was injected. A regression reproduced that failure. The queue and recovery record now commit together in the runtime task database; the mobile event and deduplication marker commit together in the mobile store database. There is no cross-database atomicity claim: retry bridges the two transactions.

The mobile, queue, store, events and local end-to-end set passed 183 tests and five subtests. Additional trigger-injected tests of event/marker rollback passed for both shared and separate stores. After updating the mutation to the new schema, removing the event-marker check caused a uniqueness error on retry; the attempted event was rolled back. This demonstrates detection of failed recovery, not an observed duplicate event. The previous mutation result describes the previous implementation. Full-suite acceptance remains unconfirmed due to quota.

## Concurrent different messages in one session

Two independent services could both observe an idle session and create separate tasks for concurrent, differently keyed messages. A per-session process-shared lock now serializes submission while retaining the separate per-key lock. Two spawned processes produced one task and one steer; another test confirmed different sessions can proceed independently. Removing the session lock in an isolated copy made the process test fail at the expected interleaving; imported source paths were checked. The related mobile/service/store/events/queue/end-to-end set passed 188 tests and five subtests. Full-suite acceptance remains outstanding because of the recorded quota.

## Complete run with nonstandard temporary root

The 576-file run completed with two workers: 11,419 collected, 11,359 passed, 38 failed, 22 skipped and 906 subtests passed. It was not a passing full run. The failures were in six files: one browser path-length assertion and 37 tests that treated a `/home` temporary path as a guest path; the channel correctly rejected that host path. With the same source and assertions, the six files were rerun using ordinary `/tmp`: 167 passed, one live-vsock check skipped and 35 subtests passed. The full result and selective rerun are separate evidence, not a green full-suite result. Two native coverage data files could not be combined. The full run used two workers rather than CI's four.

## Split full run and external catalogue timeout

A complete 576-file run used `/tmp` for six guest-path-sensitive files and a separate short temporary directory for the remaining 570; hashes for 1,549 source files remained unchanged. The six-file group passed 167 tests with one live-vsock skip and 35 subtests. The other group passed 11,230, skipped 20, passed 871 subtests and had one error: a schedule test fixture timed out while reading an external model catalogue. This was not a passing full run. Two native coverage files could not be combined.

The schedule fixture now substitutes no-result responses for the two external catalogue fetches; its schedule and delivery checks remain in place. A focused rerun passed 52 tests. This fixture change postdates the full-run snapshot, so it is not evidence of a full pass for the changed tree.

## Partial JSONL history recovery

An interrupted append can leave a final JSONL fragment without a newline. Before the fix, retry appended the recovered message to that fragment, and the history reader ignored the entire combined line. Both initial and follow-up submission regressions reproduced the loss. The append path now separates an unterminated final line without removing any existing bytes; an already-valid last line without a newline also remains readable. The focused history and recovery set passed 51 tests. Removing the separator in a verified isolated source copy caused all three relevant cases to fail. This is a process-interruption check, not a power-loss durability claim, and the earlier full run predates the fix.

A further edge case cut a multibyte UTF-8 character in the incomplete record. Before the fix, both initial and follow-up recovery raised `UnicodeDecodeError`. The history readers now skip undecodable lines while leaving file bytes intact; metadata reads decode only the header. Related checks passed 53 tests, and metadata update explicitly refuses to rewrite a damaged file. Two targeted mutations disabling the respective reader protections were detected. This extends the partial-history defect; it is not counted as a separate product defect.
