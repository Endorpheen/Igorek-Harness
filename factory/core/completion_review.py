"""Typed completion evidence and bounded recovery for local model backends."""
from __future__ import annotations

from dataclasses import dataclass, field
import json
import secrets

from .local_history_output import local_history_guard_enabled


def completion_review_enabled(base_url, api_format, capabilities, model, tools):
    return bool(tools) and local_history_guard_enabled(
        base_url, api_format, capabilities=capabilities, model=model
    )


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
    receipt_token: str = field(default_factory=lambda: secrets.token_hex(3))
    plan_pending: bool = False
    _progress: int = 0
    _retry_progress: int = -1
    _retries: int = 0

    def record(self, tool_id, name, succeeded, tool_input=None):
        self.receipts.append({"id": tool_id, "alias": "rt_" + self.receipt_token + "_" + str(len(self.receipts) + 1), "name": name, "succeeded": succeeded})
        if succeeded is not False:
            self._progress += 1
            if name == "update_plan" and isinstance(tool_input, dict) and isinstance(tool_input.get("plan"), list):
                self.plan_pending = any(item.get("status") != "completed" for item in tool_input["plan"])

    def request_continuation(self):
        if self._retry_progress != self._progress:
            self._retry_progress, self._retries = self._progress, 0
        self._retries += 1
        return self._retries <= 2

    @property
    def attempts(self):
        return self._retries
