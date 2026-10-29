"""Stable event taxonomy for cc-backend trace JSONL."""

from __future__ import annotations

import re
from typing import Any


EVENT_KIND_RE = re.compile(r"^[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*$")


class CcTraceEvent:
    TASK_START = "task.start"
    TASK_COMPLETION_SIGNAL = "task.completion_signal"
    TASK_FINISH = "task.finish"

    MODEL_ROUND_RESPONSE = "model.round_response"
    MODEL_TEXT_DELTA = "model.text_delta"
    MODEL_REASONING_DELTA = "model.reasoning_delta"
    MODEL_COMPLETION_REVIEW = "model.completion_review"
    MODEL_SYNTHESIS_CALL = "model.synthesis_call"
    MODEL_TERMINAL_FAL_RESPONSE_CALL = "model.terminal_fal_response_call"
    MODEL_FORCED_WRITE_POSTAMBLE = "model.forced_write_postamble"

    PLANNER_SALVAGE_SUCCESS = "planner.salvage_success"
    PLANNER_SALVAGE_REJECTED = "planner.salvage_rejected"
    PLANNER_TEXTUAL_ARTIFACT_RETRY = "planner.textual_artifact_retry"

    PLAN_UPDATED = "plan.updated"

    TOOL_REQUEST_BATCH = "tool.request_batch"
    TOOL_RESULT = "tool.result"
    TOOL_APPROVAL_REQUIRED = "tool.approval_required"

    POLICY_DENIAL_RETRY = "policy.denial_retry"

    POST_TURN_VERDICT = "post_turn.verdict"
    POST_TURN_PENDING_WRITE_DETECTED = "post_turn.pending_write_detected"
    POST_TURN_RETRY_REQUIRED = "post_turn.retry_required"

    FACT_CHECK_PASSED = "fact_check.passed"
    FACT_CHECK_CORRECTION_FORCED = "fact_check.correction_forced"

    NETWORK_RETRY = "network.retry"
    NETWORK_RECOVERED = "network.recovered"

    PAYLOAD_RETRY = "payload.retry"

    STEER_DELIVERED = "steer.delivered"

    BUDGET_DECISION = "budget.decision"

    COMPACTION_CHECK = "compaction.check"
    COMPACTION_STARTED = "compaction.started"
    COMPACTION_PROGRESS = "compaction.progress"
    COMPACTION_BOUNDARY = "compaction.boundary"
    COMPACTION_SKIPPED = "compaction.skipped"
    COMPACTION_FAILED = "compaction.failed"
    COMPACTION_COMPLETED = "compaction.completed"

    MULTIMODAL_INPUT_FILES_PROCESSED = "multimodal.input_files_processed"

    # Isolation capability placement (plan todo 31): a capability that could not
    # be placed inside the guest is always visible, never a silent skip.
    ISOLATION_MCP_UNAVAILABLE = "isolation.mcp_unavailable"
    ISOLATION_MCP_BROKERED_PENDING = "isolation.mcp_brokered_pending"
    ISOLATION_SKILL_ASSET_UNAVAILABLE = "isolation.skill_asset_unavailable"

    # Isolation egress policy (plan todo 16): a guest connection the per-session
    # default-deny ruleset dropped is a visible event, never a silent hang.
    ISOLATION_BLOCKED_HOST = "isolation.blocked_host"

    # Isolation provider credentials (plan todo 18): every dispatch records
    # whether the provider call is host-authenticated (the guest holds no key)
    # or falls back to the documented ephemeral-env residual.
    ISOLATION_PROVIDER_AUTH_PROXIED = "isolation.provider_auth_proxied"
    ISOLATION_PROVIDER_AUTH_RESIDUAL = "isolation.provider_auth_residual"

    # Isolation host-operation broker (plan todo 18 seam; todo 19 implements the
    # transport): every typed host-op decision is audited, never silent.
    ISOLATION_HOST_OP = "isolation.host_op"

    # Isolation resource limits (plan todo 24): a guest that hit its CPU,
    # memory, disk, process or FD budget is a visible event, never a silent
    # teardown or a retry loop.
    ISOLATION_LIMIT_EXCEEDED = "isolation.limit_exceeded"

    # Isolation admission control (plan todo 33): a session queued for VM
    # capacity is visible while it waits and when the wait times out or is
    # canceled by a stop; a queued session is never silently failed.
    ISOLATION_CAPACITY_WAIT = "isolation.capacity_wait"

    # Isolation teardown policy (plan todo 26): a guest that cannot write its
    # durable root inside the shared workspace keeps running on the disposable
    # in-guest root, and that degradation is visible, never silent.
    ISOLATION_DURABLE_ROOT_UNAVAILABLE = "isolation.durable_root_unavailable"
    ISOLATION_RECOVERY_STARTED = "isolation.recovery_started"
    ISOLATION_RECOVERY_FINISHED = "isolation.recovery_finished"


def trace_event(kind: str, **fields: Any) -> dict[str, Any]:
    """Build a cc-backend trace event with a taxonomy-validated kind."""
    if not EVENT_KIND_RE.match(kind):
        raise ValueError(f"Invalid cc-backend trace kind: {kind!r}")
    return {"kind": kind, **fields}


def all_event_kinds() -> list[str]:
    kinds: list[str] = []
    for name in dir(CcTraceEvent):
        if name.startswith("_"):
            continue
        value = getattr(CcTraceEvent, name)
        if isinstance(value, str):
            kinds.append(value)
    return sorted(kinds)
