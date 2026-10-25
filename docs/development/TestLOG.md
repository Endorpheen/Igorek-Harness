## 2026-10-25 — Spreadsheet change accounting and approval lifecycle coverage (unreleased)

Overall Python lines: **75.2374% → 75.3443%** (+0.1069 percentage points); branches: 65.0712%. Full isolated verification across both roots and 372 files: 7,131 collected, 7,110 passed, 21 existing Linux-run skips, 797 passing subtests, zero failures/errors. Added 89 Python cases and 57 subtests (43 spreadsheet integration cases, 40 Telegram approval/lifecycle cases and six reviewed Flint methods), plus six Android unit cases. No coverage exclusion or skip was added; 365 excluded lines remain. The 90% objective is unfinished; the user explicitly resumed test work after requesting this fix.

The reported changed-files dialog showed workbook serialization churn as thousands of source-line additions/deletions. Known spreadsheet formats now have a non-text label and contribute no lines to session summaries, header chips or review totals. Historical persisted counters are normalized on read; files and their added/modified/deleted states remain visible. CSV/TSV, scripts, Markdown, SVG and compound names ending in a text extension retain real line diffs. A narrow shared backend classifier also keeps existing media formats out of source-line statistics. New tracker records avoid decoding known non-text files, and Git review/direct mobile diff endpoints return unavailable rather than serialized-data hunks. Existing path authorization, recorded-file matching and checkpoint validation still precede direct mobile returns. Renames involving a workbook are also non-text comparisons.

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
