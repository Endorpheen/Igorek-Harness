## 2026-10-18 — Direct OpenCode assignments (0.2.11 in preparation)

- Reproduced the reported 0.2.10 admission failure at the project-snapshot boundary. The old whole-project copy/transfer design is replaced by explicitly selected direct project execution; legacy snapshot utilities and their compatibility tests remain unchanged and are not called for new assignments.
- Targeted Python delegation regressions: 22 passed, including no-copy admission with a 10 GiB sparse Linux file, internal relative/absolute links, direct cancellation, legacy restart recovery, active-writer exclusion, read-only normal/forced/pending tool dispatch and unchanged unrelated project execution.
- Initial fixture failures were corrected: a nonexistent registry test filename collected no tests; new executor fixtures initially lacked their artifact directory. Sandbox-denied local socket fixtures were rerun with process permissions rather than skipped. The previous focused set passed 64 tests with 6 existing Windows-only skips and 3 subtests; final broader verification is pending.
- Native Linux OpenCode 2.0.27 API fixture passed: original file visible while its question was pending, exact typed form reply, native question round trip, relative/absolute source links, external write denial, no project/baseline repository and no OpenCode snapshot store despite project `snapshots:true`. This is a real process with synthetic provider responses; it does not establish real-provider or native-panel acceptance. An initial npm registry TLS reset was bypassed using the previously SHA-512-verified pinned binary cache, without weakening HTTPS validation.
- RU/EN editable SVG and matching PNG architecture diagrams now compare copy/transfer with direct original-project execution and the runtime read-only guard. Both PNGs visually inspected.
- Initial Windows native admission failed before session creation because the synthetic project was inside AppData/Local/Temp, which the normal original-project selector rejects. The fixture now creates and cleans a separate temporary project under the user profile; production path restrictions are unchanged. The next fixture run exposed a missing parent directory after separating project/service roots; recursive creation fixes that setup error. A third Windows run still stopped before session creation with a masked startup error; the synthetic native fixture now preserves the thrown startup exception to identify its boundary. Final Windows execution, hosted Python coverage, clean installers and publication for this source revision are pending. Complete local desktop tests passed (365 tests), native Linux baseline UI passed nine stages, and a synthetic 0.2.10 → 0.2.11 update retained Vault/settings/previous generation. No owner app/service was stopped, installed over or restarted; no agent tasks were launched.

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
