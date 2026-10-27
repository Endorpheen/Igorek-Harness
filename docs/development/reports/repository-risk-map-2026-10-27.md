# Repository-wide testing map — 2026-10-27

This map inventories the whole tracked repository and directs review by impact. A row is a review boundary, not an assertion that every line has been checked. Counts are from `git ls-files`; untracked work is not included. Results for one platform or commit do not establish results for another. The coverage run has two missing native data files and must not be treated as a complete execution map.

| Inventory | Tracked files | Main types |
| --- | ---: | --- |
| `factory/` | 504 | 414 Python, 30 JavaScript, 17 shell |
| `desktop/` | 225 | 108 CommonJS, 52 Python, 14 JavaScript, 12 C++ |
| `android/` | 199 | 164 Kotlin, resources and Gradle |
| `tests/` | 594 | 567 Python and fixtures |
| `tools/` | 12 | 12 Python |
| `docs/` | 189 | Markdown, diagrams, Wiki assets and hooks |
| `.github/` | 6 | CI and publication workflows |
| Root files | 12 | build, dependencies, project instructions and readmes |

The count describes files, not user features or test coverage. Generated assets and test fixtures are included in their directories; local ignored files and current untracked tests are not. Root count is calculated from tracked root entries including dotfiles.

| Component and boundary | Principal risks | Existing evidence to inspect or exercise | Next evidence; all five methods are considered where applicable |
| --- | --- | --- | --- |
| Conversation history, storage and scheduling (`factory/core/cli_chat.py`, `session_*`, `scheduler*`, `scheduled_delivery.py`, `schema.py`) | Lost data, duplicate delivery, crash/retry, concurrent writes | `test_session_write_race18.py`, `test_archive_race18.py`, `test_scheduled_delivery.py`; new process-death and duplicate-delivery tests | More crash boundaries, process contention and targeted recovery mutants. Power loss remains distinct from process death. |
| Worker, queue, execution, provider and workflow (`factory/core/worker*`, executor and workflow modules, `api_server_core/`) | Lost tasks, unsafe approval bypass, stale state, disconnect/hang | `test_worker_queue_lifecycle.py`, `test_live_turn_integration.py`, `test_openai_stream_failures21.py`, `test_inference_workflow_guards19.py` | A real worker claim and process death were checked with artificial timestamp aging; heartbeat/cleanup race was reproduced and fixed. A mobile submission through a real worker process and a synthetic local provider now covers success and incomplete response; additional retry mutants, real gateway and external provider remain. |
| Security, vault, files and approval (`factory/core/security*`, `vault/`, `artifact_files.py`, file tools) | Escaped paths, leaked secrets, lost permissions, unapproved actions | `test_factory_api_security.py`, `test_security_execution_contracts.py`, `test_vault_crypto.py`, `test_artifact_failure_security.py` | Negative permission and race tests; mutations of access and approval decisions; native platform boundaries. |
| Isolation and host/guest channels (`factory/core/isolation/`, related scripts) | Host escape, cross-session access, data loss in cleanup, stuck VM | `test_isolation_negative_security.py`, `test_isolation_parallel_sessions.py`, `test_isolation_no_host_escape.py` | Real KVM/vsock checks with independent host observations and cleanup receipt; current live attempt failed before boot. |
| Mobile bridge and HTTP API (`factory/core/mobile_bridge/`, `factory/factory_api.py`) | Unauthorized access, token/revocation errors, replay, disconnect | `test_mobile_bridge_server.py`, `test_mobile_bridge_foreign_approvals.py`, `test_factory_api_security.py` | Mutate authentication and authorization; exercise reconnect/replay and concurrent requests with isolated services. |
| Web UI and CLI (`factory/web_ui/`, `factory/core/tui/`, CLI modules) | Wrong displayed state, approval confusion, unsafe origins, stalled input | `test_web_ui.py`, `test_web_ui_approval_state.py`, `test_cli_cwd_native.py` | Native interaction, origin and session-boundary cases; repeated lifecycle tests; distinguish backend fixtures from real clients. |
| Desktop application and native integration (`desktop/` excluding tests and packaging) | Update failure, IPC boundary, credential leakage, native process lifecycle | `desktop/tests/`, `tests/test_desktop_backend_native_contracts.py`, `tests/test_desktop_vault_owner.py` | Locked Node suite passed 520/520 in a separate copy; targeted IPC/update mutants and isolated native lifecycle remain. Windows requires Windows. |
| Android (`android/app/src/main/`) | Incorrect pairing/permissions, offline replay, crashes, untrusted links | `android/app/src/test/`, `android/app/src/androidTest/`; previous test reports are historical | Current working-tree Android source: 483 unit tests passed, lint and debug build passed in an isolated copy; emulator/device acceptance and targeted mutations remain. |
| Tool servers, integrations and scheduled external actions (`factory/mcp_servers/`, `mcp_tasks/`, `acp_bridge/`, integrations) | Excess privileges, network failure, duplicate or malformed effects | `test_bundled_mcp_stdio_contracts.py`, `test_telegram_allowlist_security.py`, `test_acp_agent_execution_contracts.py` | Synthetic provider failure, denial and retry/idempotency checks; never send real messages as a test. |
| Packaging, updates, deployment and CI (`desktop/packaging/`, `factory/deploy/`, `factory/scripts/`, `tools/`, `.github/`) | Unsafe install/rollback, stale package content, untested platform | `test_release_install.py`, `test_desktop_stage_inventory.py`, workflow definitions | Isolated clean install/update/rollback and inventory; native acceptance per OS, no publication or user installation. |
| Configuration, documentation, Wiki and remaining root files (`factory/config/`, `docs/`, root files) | Misstated defaults, broken commands/links, dependency or CI drift | Wiki workflow and existing link/build checks, lockfile validation | Verify claims against implementation; treat assets and prose as documentation checks rather than manufacturing code mutations. |

## Acceptance and accounting

For each row record the source snapshot, risk scenarios, test level, environments, fault models, outcomes, skips and unresolved evidence. Classify mutation results as killed, survived, equivalent, invalid or timed out. Repeat race-sensitive scenarios with bounded timeouts; a clean sample is not a proof of absence. A row can be marked reviewed only after its selected risks and evidence have been examined; it can be marked verified only for a named configuration and the checks actually performed. Track missing platform access as a limitation, not a passing test. The separate [testing quality report](testing-quality-2026-10-27.md) records measured experiments and current progress.

## Reviewed areas: 11 of 11

### Conversation history, storage and scheduling

- Snapshot: working tree based on `72c3c4fdbffd12267367995bde46b0483a686a9e`; modified `cli_chat.py` SHA-256 `5dfaaf0b4d6cae3116f8f8c77529dca845444ba49070bb85393fd51ba877040d`, `session_history.py` `345f8c934a5636da74bce9d70e5178427e2239fbaa8d3561512984cf3a0c4de7`, and `scheduled_delivery.py` `cdd290457a3c89aba93a53bea8af22f440cd44e97ce6c7762be9ac49f454a4d9`.
- Selected risks: interrupted or corrupt history writes, concurrent metadata and message updates, duplicate scheduled delivery, and recovery after the delivery process stops. Existing tests were compared with the new failure conditions; the overlap audit removed one redundant test.
- Linux temporary-file and separate-process checks: `tests/test_session_write_race18.py`, `tests/test_archive_race18.py`, `tests/test_scheduled_delivery.py`, `tests/test_scheduled_delivery_concurrency.py`, and `tests/test_schedule_corrupt_recovery18.py`: **33 passed**. Earlier bounded repetitions and lock/separator/reader mutation results are recorded in [the detailed report](testing-quality-2026-10-27.md); these do not imply a project-wide mutation score.
- Limits: process death is not a power-loss or filesystem durability test; no claim is made about every history format or scheduler interleaving. The complete Python run predates the latest mobile source changes.

### Worker, queue, execution, provider and workflow

- Snapshot: working tree based on `72c3c4fdbffd12267367995bde46b0483a686a9e`; `worker.py` SHA-256 `1e8037363600d15f2368396c480c4d1f94f2a969d9c510fcdb5004733a953015`. Test file SHA-256: heartbeat race `b468d51acc4d4fb1f9acb899f6c82a9e6806caf6bd320df82e751d0f8af19d1c`, worker process death `cd3cc181c119a5ad2b29a147b8160620ddd3fe139e0ecdcc43ef1fb6cb1a0233`, mobile-to-worker `02383ad02cd55b904f6c46f4bd52b0150562f9d47124349cb6c2afde602cc4d9`.
- Selected risks: live heartbeat racing stale cleanup, process death and retained task state, profile isolation, partial provider streams, and workflow write restrictions. On Linux with Python 3.14.4, six selected files passed **27 tests**. A first attempt using `.venv` did not collect because `requests` was missing; the successful run used the existing test environment with requests 2.34.2, and the failed attempt is not counted as a pass.
- Previous evidence includes two independently detected heartbeat-check mutations, ten repetitions of both race cases, and one detected stream-termination mutation in a worker process whose imported source was checked. The provider in that end-to-end check was synthetic and local; task aging after process death was simulated.
- Limits: this is not a real external-provider outage, live gateway deployment, five-minute timeout or exhaustive workflow/approval verification.

### Security, vault, files and approval

- Snapshot: working tree based on `72c3c4fdbffd12267367995bde46b0483a686a9e`; `artifact_files.py` SHA-256 `011718cd9000afd2e9cfc040a8923fb40db377e5d40728d0efa99e7c9d72f0a6`, `security_levels.py` `ad3867ff621d86400cc9ce3b0c8c01b675f6be8010b124a390150f357e41b664`, and new `test_artifact_failure_security.py` `b07c8718a4f82889d7c76e06981178cf9feaf44de62a215ba50e6139ad19c7a4`.
- Selected Linux risks: unauthorized API reads and writes, symlink and directory replacement, damaged or interrupted file replacement, private-file permissions, foreign approvals, vault lifecycle and redaction, and security-policy distinctions. The API, execution, crypto and artifact suite passed **39** tests; the vault and foreign-approval suite passed **85** tests. These are separate focused runs.
- Four selected file mutations and five selected HTTP authorization mutations were detected in controlled source copies. Their individual limits, including synthetic backend routes and the POSIX-only filesystem boundary, are documented in [the detailed report](testing-quality-2026-10-27.md).
- Limits: these checks do not certify the security of every route, credential provider or deployment; actual guest isolation and native platform permissions are accounted for separately.

### Isolation and host/guest channels

- Snapshot: working tree based on `72c3c4fdbffd12267367995bde46b0483a686a9e`; selected test SHA-256 values are negative-security `0a9a384a2cb526271e141105afbc222ee7f73e875908bf2046d532130bda7781`, parallel-sessions `ab69e523abf9aad40c6b96f729e51c3c4e439f32368f25a9e7376ff2d74d88eb`, and host-escape `8f93da92d6a4b242e8770d595cb66c3025ba13efbc30d11475da875c7c2467bc`.
- Selected structural risks: sensitive host paths and sockets, unauthorized exports, another session's resources and host operation boundaries. With short `/tmp` paths the Linux checks passed **95 tests and 103 subtests**; **10 live probes were skipped**. An initial long temporary path caused three `AF_UNIX path too long` failures; the short-path rerun retained the same assertions and is reported separately.
- Limits: structural checks are not a live VM security boundary. A previous isolated KVM attempt could not boot a guest because image preparation could not read the host kernel; its cleanup receipt was clean. The actual KVM/vsock boundary remains unverified.

### Mobile bridge and HTTP API

- Snapshot: working tree based on `72c3c4fdbffd12267367995bde46b0483a686a9e`; `mobile_bridge/service.py` SHA-256 `7a7934b0e9390cc7372be73a3229f2c5dc99fdceb01478e8834b9fe41652c2c9`, `store.py` `47c7f8ae239730dcd1e2579a33587bfe3cfa8f622f8523fe81ad326dac9edcc4`, and new recovery test file `5c812865ac210ab9d720175b66cb169e72de28a0eb911bd867af0eda86a33479`.
- Selected risks: unauthorized requests and foreign approvals, repeated keyed submissions, concurrent messages, partial history, response and queue recovery, orphan-task retry, provider disconnect. Five selected files passed **139 tests** on Linux with local synthetic services and disposable SQLite data. The new recovery file includes actual process death and cancellation interleavings; it does not duplicate the old successful repeat-finalization test.
- Limits: the run does not include a physical Android client or a deployed gateway. It does not prove power-loss durability or exactly-once memory-provider effects. Interruption after orphan queue consumption but before the requeue event is published has not been verified.

### Web UI and CLI

- Snapshot: working tree based on `72c3c4fdbffd12267367995bde46b0483a686a9e`; web server SHA-256 `0d7d6010f461f00c9c169df52c4234f873adb455c96f57a5e6ac32cea68bd4b9`, browser app `31fa8e8cd6d04901e34c9d58c5b66c754a0c5674115df9efb43ffb6c3bc50d45`, native CLI test `42cd45e49bd138adaff247f0470d9ad55f0c17d599fee8ed648ba75136db595e`.
- Selected risks: untrusted web origin, missing pairing, traversal in workspace trees, stale or cross-task approval state, terminal route requirements, and CLI working-directory selection and terminal restoration. On Linux, focused checks passed **31 tests and 32 subtests**. CLI cases used an actual owned PTY with geometry and cursor responses, while the chat body was substituted; web approval state used a JavaScript fixture.
- Limits: this is not an installed browser or physical-device acceptance of every UI flow; static route checks do not establish full WebSocket lifecycle behavior.

### Desktop application and native integration

- Snapshot: working tree based on `72c3c4fdbffd12267367995bde46b0483a686a9e`; `release-updates.cjs` SHA-256 `095b68732ebec182339934512da169ac982869f01f43f077b4d0312356141da6`, changed updater test `8695b8d4b6ee89e27069a8ef78fe2943742dea3ae67bd0ec624013b2a2661a1a`. An earlier isolated locked-dependency Desktop baseline passed **520 tests** before the new test; the current updater file passed **16 tests** separately under Node 24.19.0.
- Selected update risk: bypassing the allowed-redirect check was a surviving mutation for the previously existing redirect test. It still passed because repeated redirects eventually hit the hop limit. The added assertion checks that no request reaches the foreign destination: both tests pass on the original, while only the new check fails on the mutant. The copied-source run used a synthetic fetch and the locally installed dependencies, which are not the locked baseline; logs are in `build/verification-testing-quality-20261027/desktop-mutation/`.
- Limits: this is a focused updater fault model, not a complete IPC audit, native Electron acceptance or Windows verification. The locked full Desktop suite predates the new test.

### Android

- Snapshot: the isolated Android copy used for the earlier baseline was re-compared byte-for-byte against all **199 tracked Android files** in the current working tree, including pre-existing user changes: zero missing or changed files.
- Existing selected risks include rejecting cleartext and malformed pairing endpoints, refusing a wrong certificate fingerprint, preventing bearer-token delivery to a redirected plaintext listener, and avoiding duplicate replay notifications. The isolated Android baseline passed **483 JVM tests**, lint and debug assembly; the HTTPS-to-plaintext check uses actual local TLS and a separate plaintext listener.
- Limits: no Android mutation was executed during this review; JVM checks are not emulator, installed-app, physical-device or signing acceptance.

### Tool servers and external integrations

- Snapshot: working tree based on `72c3c4fdbffd12267367995bde46b0483a686a9e`; selected test SHA-256 values: MCP stdio `6802314c02961e88a9eda3f36f014acf3a84d682d311548012d9a308a53c17b1`, Telegram allowlist `ffe9c8025bad04f36ec2691865e8bc4a39c5e55633acc48f0c32f74bb434ab34`, ACP execution `f899feaf47715ae1891739653e83a5072fde1bb7951861f5e44b8563718e67d7`.
- Selected risks: malformed MCP input and provider failures leaving the protocol unusable, missing credentials contacting the network, empty Telegram allowlists, foreign users/chats, ACP cancellation and session isolation. Three focused files passed **74 tests** using controlled providers and local data; no real message was sent.
- Limits: this is not a live third-party outage or an end-to-end verification of every external action's idempotency and permissions.

### Packaging, updates, deployment and CI

- Snapshot: working tree based on `72c3c4fdbffd12267367995bde46b0483a686a9e`; modified runner `tools/run_tests.py` SHA-256 `a194058e1b0b420c266e67e5eb3e3833b72fee8d97703225493b95c52006c6ab`; staging inventory test `0351c1951de295f152a7cdb874ecb5d1da92354050c3aed7e4261b2885d35e50` and installation test `ea240e101cf384e4c1466d3d8e8bc9877c8d76c5447bbe1d9e4660baf66292bc`.
- Selected risks: tampered update payloads, atomic replacement and rollback, stale or incomplete build inventories, symlinks and private data in packages, and invalid test-runner reports. Five focused Linux files passed **107 tests** with isolated synthetic installations and data. A first run using `/tmp` had **10 failures and 38 errors** when test fixtures encountered `EDQUOT`; the unchanged tests passed in a separate workspace temporary directory. Its 778 MiB of test-owned fixtures were removed after the process finished.
- Limits: these are synthetic packaging/install checks, not a clean build and installation of the current working tree, native Windows acceptance or release publication.

### Configuration, documentation, Wiki and root files

- Snapshot: working tree based on `72c3c4fdbffd12267367995bde46b0483a686a9e`; `pyproject.toml` SHA-256 `65ee38f72a4cd805a7826623d307b6338ad96e60ab4ba6b4ff8e30b43f84aa4a`, `uv.lock` `527f54f3df9194c06fc99f259baae26c930df9ce7ad6fbff2810fbd7c7b78642`, Wiki config `c40601fb4664eff4268fa9c1710919a197f245aad9933e8c0a24d4b1a5ab2920`.
- Selected risks: configuration failing open, unsafe Git configuration, stale lockfiles, broken Wiki links and incomplete imports. The configuration and publication contract files passed **53 tests**; eleven Wiki importer/citation tests passed in the existing documentation environment; `uv lock --check --offline` passed. A strict Wiki build in a separate output directory passed, and its check found **77 HTML pages, 6,312 local links checked and zero errors**.
- The first Wiki unit-test attempt in the Python test environment failed to import one module because BeautifulSoup was missing; the subsequent complete run used a separate existing environment. Wiki content was not edited or published.
- Limits: the existing Wiki environment was not freshly synchronized from its lockfile for this review, and browser/device checks and external link availability were not established. Review of all eleven map areas records selected evidence and limitations; it does not mean every possible scenario has been verified.
