# Trace contracts

Igorek has two event families. Use the correct family for the component under
investigation; similar descriptions do not make event names interchangeable.

## Shared agent executor

`factory/core/trace_taxonomy.py` defines `CcTraceEvent` for
`cc_backend_trace.jsonl`. Kinds use `domain.action`, including `task.finish`,
`tool.result`, `post_turn.verdict` and `budget.decision`. Add a taxonomy regression
before introducing a new kind. Do not add an underscore spelling alongside it.

Follow task/session IDs, `tool_use_id`, canonical approval signatures, explicit
status and failure reason. A model's prose is not an authoritative completion
or workflow record.

## Inference API/workflow events

The separate API/workflow layer records request-oriented events, traditionally
in `anthropic_trace.jsonl`. Its event names include:

| Event | Evidence |
| --- | --- |
| normalized_request | Normalized request mode and shape |
| file_resolution | Active file source, roots and resolved path |
| turn_state | Required tools, successful reads and denial state |
| planner_result | Parsed plan and its count |
| planner_salvaged_tool_use | Structured recovery of a textual tool call |
| planner_stdout_only_bash_rejected | Rejection of a shell artifact without a valid execution contract |
| tool_turn_response | Tool-producing response path |
| text_turn_stream | Streamed text response path |
| text_turn_response | Completed text response path |
| count_tokens | Token-count request and result |

Project-analysis events such as `project_analysis_attached`,
`project_analysis_skipped` and `forced_project_analysis_scan` belong to that
workflow family. Preserve their existing contracts when investigating old traces.

## Reading a trace

Start with the exact user turn and request-ID chain. List modes, selected intent,
workflow attachment/skip, tool requests/results, approvals, retries and terminal
state. Identify the first structural divergence before choosing a fix. Compare
payloads as normalized JSON or bytes, not as a rough textual impression.

Keep full personal transcripts and credentials out of public reports. A compact
report should describe the contract, synthetic trigger and observed fields.
See the [debugging playbook](DEBUGGING_PLAYBOOK.md).
