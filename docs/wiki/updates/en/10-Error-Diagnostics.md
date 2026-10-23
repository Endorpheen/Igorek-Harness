## Agent-correctable browser rejections

Known browser argument, URL and stale-reference rejections are recorded as rejected calls, with their concrete cause preserved. They remain visible while unresolved, but do not claim that owner intervention is required. A successful task can close these rejected calls with the explicit explanation that the call was not executed. Matching successful retries retain their separate proof. Replayed legacy browser incidents are enriched without creating new cards or losing acknowledgment. Actual execution failures such as `post_not_found`, actions marked `performed`, and uncertain outcomes are not cleared merely because a task succeeded.

Android support reports now include `server.availability_checked: false` and `server.status: not_collected`. The existing `server.available: false` means that server diagnostics were not included; no server availability probe was performed. Local history counts, recovered summaries and truncation limits retain their meaning.

Worker failures retain their supplied machine reason and sanitized error text. An empty or unrecognized diagnostic object is not treated as a typed internal error; Android uses the actual task/tool event instead. A failure without a recorded cause remains explicitly unknown.
