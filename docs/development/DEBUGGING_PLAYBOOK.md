# Debugging playbook

Investigate the first structural divergence. Use trace events, request/task IDs,
JSON shapes, signatures, states, response codes and actual files. Do not increase
retry limits or add phrase-based routing before finding the broken contract.

## Investigation order

1. Record the exact user turn and expected result.
2. Locate its task/request chain in trace and persisted state.
3. List model requests, tool calls/results, approvals, retries and completion.
4. Compare inputs and outputs at the first unexpected boundary.
5. Check `tool_use_id`, signature, status, failure reason, transport and payload.
6. Add an original-trigger regression and a compatibility case.
7. Check the installed library's real API; permissive mocks must not hide an
   unsupported parameter or return shape.
8. Exercise empty strings/lists, optional fields and missing values explicitly.

A log report includes the user turn, request chain, modes, intent, workflow
attachment or skip, key events and an outcome for each turn. Finish with what
worked, what failed and the remaining defect, grounded in that evidence.

## Repeated approval

Compare the original blocked call, persisted `approval_blocked_tools`, resumed
`forced_tool_call` and recomputed signature. Mutation after signature creation
causes another approval. Fix the mutating serializer, normalizer, redactor or
resume boundary; do not bypass a mismatching signature.

A redactor must not rewrite an identifier such as `is_secret: bool = False`
inside code. Redaction that changes tool input also changes its authorization
identity. Match secret values and typed fields without corrupting code.

## Missing or invented tool

Compare the advertised schemas with the actual registry and the model's
structured response. Follow salvage metadata and the exact original output.
Salvage is fixture-first and additive. A tool name appearing in prose is not
proof of a structured call or its execution.

## Answer lost between backend and UI

Follow the response through executor events, reducer state, persisted messages,
journal reconstruction and renderer. Reasoning collapse must preserve the final
answer. Check empty output, streaming fragments, committed rounds, replayed
frames and session changes. Use stable message identity to avoid duplication.

## Action without a model turn

Check the explicit workflow attach/skip decision and trace. Determine whether a
legitimate structural shortcut ran or a model step was incorrectly bypassed.
Do not infer this from how quickly the UI changed.

## Repeated project reads

Inspect successful read records, active-file state, next required tool and the
workflow's terminal condition. A retrieved file must reach the next model turn.
Do not classify an exploration loop using a list of words in the answer.

## Source changed but the interface looks old

Distinguish repository source, installed wheel, bundled runtime, running process
and resumed history. Verify the executable and source hashes before editing the
renderer again. Reinstall through the supported installer; do not overlay a
working process's files. Restart the applicable runtime and then chat.

## Lists, scrollback and overlays

Keep source markers separate from renderer-generated markers to avoid duplicate
numbering. Check nested lists, code fences, empty lines and terminal width.
An input overlay must release its reserved terminal rows before normal output
resumes. Submit, cancellation, repaint and application exit are separate states.

The full-screen owner exits an active application only once. An already finished
prompt_toolkit application must not receive a second `exit()` call.

## Real terminal checks

Use a child PTY, set window size, answer `ESC[6n`, then send raw key and SGR mouse
sequences. Verify clicks, wheel, hover, resize and final exit from actual process
output. DummyOutput does not establish usable terminal geometry.

## Real UI checks

Exercise the current application with synthetic accounts/data. Preserve native
sandboxing, origin checks, owner-frame checks and lifecycle. A mocked DOM tests a
contract; it does not prove element visibility, layout or click behavior. Test
stale replies, canceled dialogs, locked state and return from background.

## Reporting

State the trigger, first broken boundary, fix, valid behavior retained, checks
and remaining limits. Separate mocked network tests from real account consent,
unit tests from native UI, and structural VM checks from live hardware results.
Never include tokens, cookies, Vault contents or personal conversations.
