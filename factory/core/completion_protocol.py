"""Local same-turn completion signals with deterministic evidence validation."""
from __future__ import annotations

from dataclasses import dataclass
import json

from .completion_review import parse_completion_review


FINISH_TASK = "finish_task"
FINISH_SCHEMA = {
    "name": FINISH_TASK,
    "description": (
        "Finish the current task and deliver the final answer in this call, without another model request. "
        "Use alone, after all authorized steps and verification are done, or to report a genuine blocker. "
        "For action requests set requires_tools=true and copy the Runtime execution receipt IDs printed in current tool results. "
        "For questions or requested text drafts requires_tools=false is allowed without work tools. "
        "Never finish with a preamble or a promise. List any remaining steps instead of claiming completion."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "status": {"type": "string", "enum": ["complete", "blocked"]},
            "answer": {"type": "string", "minLength": 1},
            "requires_tools": {"type": "boolean"},
            "evidence_ids": {"type": "array", "items": {"type": "string"}},
            "remaining_steps": {"type": "array", "items": {"type": "string"}},
        },
        "required": ["status", "answer", "requires_tools", "evidence_ids", "remaining_steps"],
        "additionalProperties": False,
    },
}


@dataclass(frozen=True)
class FinishRequest:
    status: str
    answer: str
    requires_tools: bool
    evidence_ids: tuple[str, ...]


def validate_finish(value, state):
    """Validate a control request without inferring completion from prose."""
    fields = {"status", "answer", "requires_tools", "evidence_ids", "remaining_steps"}
    if not isinstance(value, dict) or set(value) != fields:
        raise ValueError("invalid_finish_request")
    if value["status"] not in ("complete", "blocked"):
        raise ValueError("invalid_finish_status")
    answer = value["answer"]
    if not isinstance(answer, str) or not answer.strip():
        raise ValueError("missing_final_answer")
    remaining = value["remaining_steps"]
    if not isinstance(remaining, list) or any(not isinstance(step, str) or not step.strip() for step in remaining):
        raise ValueError("invalid_remaining_steps")
    evidence = value["evidence_ids"]
    aliases = {item.get("alias", item["id"]): item["id"] for item in state.receipts}
    if isinstance(evidence, list):
        evidence = [aliases.get(item, item) if isinstance(item, str) else item for item in evidence]
    review = parse_completion_review(json.dumps({"status": value["status"],
        "requires_tools": value["requires_tools"], "evidence_ids": evidence}), state.receipts)
    if value["status"] == "complete":
        if remaining or (value["requires_tools"] and state.plan_pending):
            raise ValueError("unfinished_steps")
        if review.status == "continue":
            raise ValueError("completion_evidence_missing")
        if state.receipts and not review.evidence_ids:
            raise ValueError("completion_evidence_missing")
        if any(item["succeeded"] is False for item in state.receipts if item["id"] in review.evidence_ids):
            raise ValueError("completion_evidence_failed")
    return FinishRequest(value["status"], answer.strip(), review.requires_tools, review.evidence_ids)
