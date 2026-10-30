# Local completion control

Confirmed loopback llama.cpp backends using OpenAI-compatible or Anthropic
transport finish through an internal `finish_task` signal in the normal model
turn. Endpoint and model identity must match the capability probe. Cloud models,
unconfirmed proxies, other transports and tool-free requests keep their existing
request and completion path.

The local request offers ordinary work tools plus the internal finish signal,
without forcing the provider tool-choice mode. A question or requested draft may finish without work
tools. The signal carries the final answer, complete/blocked status, remaining
steps and current execution receipts. Receipt aliases are printed in tool results
because server chat templates may hide native tool-call IDs from the model.
Invented/failed evidence, unfinished plans and mixed finish/work batches are
rejected; successful tools are never replayed automatically. Existing approval,
cancellation, steering and output-budget rules remain in force.

A plain preamble without a finish signal continues the same task. Two consecutive
continuations without tool progress are allowed before an explicit incomplete
failure. Completion is still model-dependent: typed evidence cannot prove that
all natural-language requirements were understood. The signal is internal control
data; it does not count as a work tool or create a separate reviewer request.
A provider response that omits it can stop naturally and receive a bounded
continuation; EOS is not forced behind a tool call. Only the validated signal's
answer becomes a visible final message. Preliminary text stays in diagnostic
traces, while explicitly tagged reasoning stays in the reasoning channel.
The 0.2.41 forced tool-choice and premature-text regression is fixed in 0.2.42.

This replaces the 0.2.40 read-only reviewer, which could add a long delay after
visible final text. Four local-model acceptance cases in 0.2.41 covered a question,
premature preambles before/after work and a 101,972-input-token history. The
then-measured runtime tail after the final provider response was about 1 ms; total
prefill/generation time still depends on the model and context.

After updating, fully quit and relaunch Desktop and start a new chat. Separately
managed runtime/gateway installations must also be updated and restarted. No
profile reimport or model-server command change is required.
