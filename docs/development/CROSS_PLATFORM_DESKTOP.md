# Desktop targets

One repository contains the shared Electron interface, Python agent runtime and
operating-system implementations under `factory/core/platform_support/`.
Linux x64 remains the primary development target. Windows x64 is a preview:
hosted installation and first-task/restart checks pass; clean-machine manual
acceptance and publisher signing remain stable-release gates.

## Platform contracts

| Contract | Linux | Windows preview |
| --- | --- | --- |
| Vault ownership | Kernel `flock` lease | Exclusive file handle, released on process exit |
| Private data | POSIX permissions | Owner-and-SYSTEM inheritable DACL |
| File reads/writes | Directory descriptors and `O_NOFOLLOW` | Parent handles and reparse-point rejection; existing DACL preserved |
| Owner terminal | POSIX PTY | ConPTY through Windows-only `pywinpty` |
| Agent command execution | Existing sandbox and policy | Refused before spawning; no unsandboxed fallback |
| KVM and virtiofs | Existing implementation | Unavailable; selection and image preparation refused |
| OpenCode private Unix proxies | Existing implementation | Unavailable pending a native private IPC implementation |

The Windows terminal is an owner-operated capability and remains subject to the
existing paired-device and host-terminal controls. It does not grant shell
execution to the model. ConPTY and an installer are not isolation boundaries.

## Reproducible candidate packages

Install the locked build dependencies first:

```bash
uv sync --locked --dev
npm --prefix desktop ci
```

Build a fresh runtime and stage a reviewed application. Windows payloads can
be prepared on Linux; their Python archive is pinned by version and SHA256,
dependencies come from `uv.lock`, and project files come from a clean-source
wheel with provenance.

```bash
uv run --no-sync python desktop/packaging/build-windows-runtime.py --output build/windows/runtime
uv run --no-sync python desktop/packaging/stage-app.py \
  --runtime build/windows/runtime --output build/windows/app --target windows-x64
export IGOREK_DESKTOP_APP_DIR="$(pwd)/build/windows/app"
export IGOREK_DESKTOP_DIST_DIR="$(pwd)/build/windows/dist"
npm --prefix desktop run package -- --win --x64
```

Windows cross-packaging on Linux requires Wine. On a Windows build runner set
the same environment variables with PowerShell before running the package
command. For Linux use `build-runtime.py`, `--target linux-x64` and `--linux`.
The existing Linux snapshot/install path remains available.

Both targets share `desktop/electron-builder.cjs` and the reviewed source
allowlist. The common builder produces Linux AppImage and a per-user Windows
NSIS EXE. Linux DEB retains the existing verified-snapshot installer, preserving
its setup and Electron sandbox-policy path without inventing a public homepage.
Build that candidate separately:

```bash
uv run --no-sync python desktop/packaging/package-linux.py \
  --runtime build/linux/runtime --output build/linux/snapshot
uv run --no-sync python desktop/packaging/package-deb.py \
  --snapshot build/linux/snapshot/igorek-desktop --output build/linux/igorek-desktop-installer.deb
```

For an environment-specific Linux release that already includes native KVM
bindings, build its runtime with `--with-kvm-bindings` on the intended distro.
The base CI payload and host-mode tests do not establish KVM readiness. AppImage
also needs actual sandbox/install acceptance on the supported Linux system.

Staging rejects mismatched or stale runtimes, private database/auth files and
external links. Packaging commands do not publish a release.

## Acceptance and release

`Desktop packages` builds on Ubuntu and Windows runners and retains candidate
artifacts. Windows checks file containment, junction handling, exclusive Vault
ownership, DACLs and data encryption. Its installed payload then runs the
maintained Electron first-task/restart scenario with a synthetic provider.
Every required native stage must appear in the report. Installation and
reinstallation/uninstallation are separate checks; long-term update/rollback,
voice and physical-device acceptance still need their own evidence.

Wine diagnostics are useful for imports and early runtime checks. They do not
prove Windows junction, directory-sharing or cryptographic-provider behavior.
Never skip a failing native protection check to label a package accepted.

Candidates are unsigned until a publisher identity and signing pipeline exist.
Stable release publication must collect accepted Linux/Windows artifacts from
the same source revision, their checksums and signing/provenance records. Current hosted results are recorded in [TestLOG.md](TestLOG.md); a configured
workflow alone is not evidence of a successful run.
