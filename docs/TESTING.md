# Testing

Tests are reported by layer. A collected case, a subtest, measured source
coverage, a native interaction and a physical-device scenario are different
kinds of evidence; their numbers are not interchangeable.

## Reproducible local checks

```bash
uv sync --locked --dev
uv run --no-sync python -u tools/run_tests.py --coverage
npm --prefix desktop ci
npm --prefix desktop test
JAVA_HOME=/path/to/jdk17 ./android/gradlew -p android testDebugUnitTest lintDebug
```

The Python runner discovers both `factory/tests/` and `tests/`, gives each file a
fresh process and private temporary data, and shows progress as files finish.
It enforces per-test and per-file deadlines, saves JUnit output and combines
branch coverage. Every test file is attempted; a failed file keeps a nonzero
exit status. Do not disable failing files to obtain a passing run.

The root pytest configuration also isolates ordinary pytest collection/runs from
real home directories, Vault data and inherited credentials. Test fixtures must
supply their own accounts, keys and services. Native/optional system prerequisites
are checked separately and their absence must have a visible skip reason.

Desktop tests live in `desktop/tests/` and the npm command discovers every test
file. Native desktop acceptance is a separate maintained
scenario in `desktop/tests/native/first-run.cjs`, with Linux and Windows CI paths. Node test doubles exercise controller, transport, permission and state
contracts. They do not establish native visibility or pixel/layout behavior.

Android JVM tests use the pinned Gradle wrapper and version catalog. The wrapper
JAR and distribution have verified upstream checksums. Loopback TLS keys are generated only in the ignored build directory.
Device instrumentation,
voice/background behavior and update acceptance require a real device or the
corresponding emulator environment.

## Coverage scope

Python coverage includes `factory`, the desktop Python backend/build helpers and
the root API/prompt modules. Report executed lines and branches separately. An
aggregate percentage is affected by optional inference paths, terminal ports,
operations and VM code; it does not mean each interface has that coverage.

The latest accepted full-run snapshot on 15 October 2026 measured 59.15% of
77048 Python statements (45571 covered) and 46.04% of 27534 branches (12678
covered), with no failures. It collected 4776 cases: 4746 passed and 30 skipped,
plus 614 additional passing subtests. Desktop Node tests passed 316 cases.
The inference entrypoint is now measured through its actual middleware, adding
previously unmeasured code to the denominator. Earlier percentages are not a
like-for-like quality comparison. See TestLOG for changes and platform limits.
JavaScript and Kotlin coverage need their own reports and denominators. Never
add percentages across languages or infer a percentage from test count.

See [current results](development/TestLOG.md) for exact counts, date, scope,
failures, skips and changes. Do not add overlapping targeted runs together.

## Acceptance matrix

| Layer | Automated evidence | Separate acceptance |
| --- | --- | --- |
| Agent/worker | Tool, guard, routing, persistence, approval and failure regressions | Real provider behavior and long sessions |
| CLI | Input/render/state tests and actual PTY cases | Terminal geometry, mouse, resize and recovery |
| Desktop | Node contracts, Python backend and package/install tests | Actual Electron clicks, native views and first task |
| Web UI | Auth, ranges, files, history, renderer/state regressions | Browser interaction and accessibility |
| Android | JVM unit and lint/build checks | HTTPS pairing, physical update, voice and background |
| Isolation | Guard/protocol/lifecycle tests with controlled seams | KVM/vsock/virtiofs/nft on actual hardware |
| Packaging | Clean-source inventory, hashes and install/update/rollback contracts | Clean machine and platform dependencies |

## Areas requiring more evidence

- Windows preview packaging is implemented. File/Vault contracts and installed
  payload acceptance run on the Windows job in `Desktop packages`; that workflow
  still needs an actual hosted result. Wine and Linux checks do not establish
  native Windows containment, cryptographic behavior or stable acceptance.
- Native Linux first-task, visible progress, saved history and backend/window
  restart have a maintained scenario. Longer sessions, browser/voice and
  platform-specific native interactions still need broader acceptance.
- First-run, account consent, model failures and persistence need end-to-end
  acceptance through the actual installed application.
- Live VM capabilities and boundary checks remain host-dependent. Preserve all
  missing-prerequisite reasons in reports.
- Coverage of larger terminal/coordinator modules and optional inference/API
  paths needs improvement. Fix behavior and add meaningful regressions before
  setting stronger thresholds.
- Android line/branch coverage and device scenarios must be reported separately
  from passing JVM serialization or HTTP fixtures.

## CI

The CI workflow uses locked dependencies, immutable action revisions, minimal
repository permissions and separate Python, desktop, Android, source and secret
checks. Pull-request jobs use synthetic data and no production credentials.
A workflow file is not proof of a successful hosted run. Add a status badge only
after its repository, workflow and result actually exist.

## Native Linux acceptance

Build a new runtime and snapshot; do not replace a running installation. The
scenario uses a fresh temporary Vault and synthetic loopback provider, pins its
HTTPS certificate, exercises the first result, closes its test windows/backend,
then checks the saved profile, browser pairing, visible history and a second
task after restart. It cleans up its own data and processes. Existing host
credentials and model services are excluded.

```bash
work_dir=$(mktemp -d)
uv run --no-sync python desktop/packaging/build-runtime.py --output "$work_dir/runtime"
uv run --no-sync python desktop/packaging/package-linux.py \
  --runtime "$work_dir/runtime" --output "$work_dir/snapshot"
IGOREK_TEST_RUNTIME="$work_dir/snapshot/igorek-desktop/app/runtime" \
IGOREK_TEST_OUTPUT="$work_dir/results" \
  xvfb-run -a "$work_dir/snapshot/igorek-desktop/electron/electron" \
  desktop/tests/native/first-run.cjs
```

Electron sandbox prerequisites still apply. The scenario never disables the
sandbox. Hosted CI must run before a passing badge or release claim is made.
It does not establish real OAuth renewal, physical Android, Windows or VM egress
acceptance. Subscription quota is tested separately with read-only contracts;
see [quota integration](integrations/CHATGPT_QUOTA.md).
