# Test results

## 0.2.8 candidate — session sources and file visibility

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
