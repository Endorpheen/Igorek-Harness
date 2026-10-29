"""Evidence-bound completion review for confirmed local model backends."""
from __future__ import annotations

from dataclasses import dataclass, field
import json

from .local_history_output import local_history_guard_enabled


COMPLETION_REVIEW_SCHEMA = {
    "type": "object",
    "properties": {
        "status": {"type": "string", "enum": ["complete", "continue", "blocked"]},
        "requires_tools": {"type": "boolean"},
        "evidence_ids": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["status", "requires_tools", "evidence_ids"],
    "additionalProperties": False,
}


def completion_review_enabled(base_url, api_format, capabilities, model, tools):
    return bool(tools) and local_history_guard_enabled(
        base_url, api_format, capabilities=capabilities, model=model
    )


def review_transcript(messages):
    """Project visible context without leaking reasoning or image payloads."""
    result = []
    for message in messages:
        content = message.get("content", "")
        if isinstance(content, list):
            content = [
                {key: part[key] for key in ("type", "text", "id", "name", "input", "tool_use_id", "content") if key in part}
                for part in content if isinstance(part, dict)
                and part.get("type") in {"text", "tool_use", "tool_result"}
            ]
            for part in content:
                if isinstance(part.get("content"), list):
                    part["content"] = [block for block in part["content"]
                                       if isinstance(block, dict) and block.get("type") == "text"]
        result.append({"role": message.get("role"), "content": content})
    return result


@dataclass(frozen=True)
class CompletionReview:
    status: str
    requires_tools: bool
    evidence_ids: tuple[str, ...] = ()


def parse_completion_review(text, receipts):
    """Accept only typed control data backed by this task's execution receipts."""
    text = text.strip()
    if text.startswith("```json\n") and text.endswith("\n```"):
        text = text[8:-4]
    try:
        value = json.loads(text)
    except (TypeError, ValueError) as exc:
        raise ValueError("invalid_completion_review") from exc
    if not isinstance(value, dict) or set(value) != {"status", "requires_tools", "evidence_ids"}:
        raise ValueError("invalid_completion_review")
    status, requires, ids = value["status"], value["requires_tools"], value["evidence_ids"]
    if status not in ("complete", "continue", "blocked") or type(requires) is not bool:
        raise ValueError("invalid_completion_review")
    if not isinstance(ids, list) or any(not isinstance(item, str) or not item for item in ids):
        raise ValueError("invalid_completion_review")
    known = {item["id"]: item for item in receipts}
    if len(ids) != len(set(ids)) or any(item not in known for item in ids):
        raise ValueError("unbound_completion_evidence")
    if status == "complete" and requires:
        if not ids or any(known[item]["succeeded"] is False for item in ids):
            return CompletionReview("continue", True)
    return CompletionReview(status, requires, tuple(ids))


@dataclass
class CompletionRecovery:
    """Bound retries without reducing output budgets or repeating side effects."""
    receipts: list[dict] = field(default_factory=list)
    _progress: int = 0
    _retry_progress: int = -1
    _retries: int = 0

    def record(self, tool_id, name, succeeded):
        self.receipts.append({"id": tool_id, "name": name, "succeeded": succeeded})
        if succeeded is not False:
            self._progress += 1

    def request_continuation(self):
        if self._retry_progress != self._progress:
            self._retry_progress, self._retries = self._progress, 0
        self._retries += 1
        return self._retries <= 2

    @property
    def attempts(self):
        return self._retries
