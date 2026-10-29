# Local completion control

Confirmed loopback llama.cpp backends using the OpenAI-compatible or Anthropic
protocol review text-only completion candidates when tools are available.
Backend identity is bound to the selected endpoint and model; a model name or
localhost address alone is insufficient. Cloud providers, unconfirmed proxies,
other protocols and tool-free requests keep their previous completion path.

Before accepting a final answer, the same local provider receives one additional
read-only request containing visible conversation, execution policy and current
tool receipts. It returns typed control data: complete, continue or blocked.
The reviewer cannot execute tools. Its control JSON and reasoning are not shown
as the user's answer; its token usage and verdict are recorded in task traces.
OpenAI-compatible reviews request a JSON schema. The configured output budget
and normal tool permissions are unchanged.

An unfinished authorized action continues within the same task using existing
results. The runtime does not replay prior tools automatically. Completed
questions, explanations, requested drafts, necessary clarification and honest
blocker reports can finish without unnecessary actions. A completed action must
cite actual current-task receipt IDs; failed or invented receipts do not prove
success. Approval waits, cancellation and later user scope retain priority.

Two consecutive continuations without tool progress are allowed; another
premature completion returns an explicit incomplete failure. Invalid, interrupted,
output-limited or oversized review requests are not silently accepted as success.
Semantic decisions remain model-dependent: typed evidence checks cannot prove
that every natural-language requirement was understood. The extra local request
adds latency; live-model accuracy and latency were not measured for this release.

After updating, fully quit and relaunch Desktop and start a new chat. Update and
restart any separately managed runtime/gateway before chat. Profiles and model
server commands need no changes or reimport. Linux/Windows installers include the
updated runtime; Android continues to use the desktop/runtime executor.
