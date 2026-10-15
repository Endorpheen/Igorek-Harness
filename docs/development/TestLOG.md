# Test results

## Automatic desktop installation — verification in progress

The 0.2.0 candidate adds native confirmation, a separate verified installer helper,
closed-app replacement and relaunch with the same data directory. Active executor
work refuses installation; a postponed download can be reused. Windows copies
its bundled Python helper outside the application before replacement. Linux
snapshot installation uses the existing verified installer and AppImage preserves
previous bytes for rollback. Preview builds can discover newer preliminary releases.

Targeted installer/runtime/packaging checks passed 76 cases. The last complete
desktop run before the update-preparation guard passed 329 cases. The integrated run and actual
Windows updater acceptance remain pending; no release has been published yet.

## Current snapshot — 15 October 2026

Python/desktop **0.2.0**; Android **0.1.105 / versionCode 106**.
Results below describe identified candidates, not a stable three-platform release.

| Layer | Result | Scope |
| --- | --- | --- |
| Python | **4787 collected; 4756 passed; 31 skipped; zero failures/errors** | Both roots, 299 isolated files; **614 additional passing subtests** |
| Python statements | **45690 / 77180, 59.20%** | Shared agent, inference API, desktop Python and build helpers |
| Python branches | **12702 / 27588, 46.04%** | Measured independently from line coverage |
| Desktop Node | **322 passed; zero failures/skips** | Controllers, IPC, permissions, transport, updates and state |
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
