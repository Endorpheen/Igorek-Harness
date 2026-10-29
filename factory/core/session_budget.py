"""Runtime counters and stop decisions for cc-backend sessions."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .tool_result_store import PERSISTED_OUTPUT_TAG


DECISION_CONTINUE = "continue"
DECISION_FORCE_SYNTHESIS = "force_synthesis"

REASON_REPEATED_TOOL_LOOP = "repeated_tool_loop"
REASON_NON_PRODUCTIVE_AFTER_USEFUL = "non_productive_after_useful"


@dataclass(frozen=True)
class BudgetDecision:
    action: str
    reason: str = ""
    details: dict[str, Any] = field(default_factory=dict)

    @property
    def should_continue(self) -> bool:
        return self.action == DECISION_CONTINUE


@dataclass
class SessionBudget:
    """Counters for one executor run.

    The first version mirrors existing executor limits instead of inventing new
    behavior.  It gives the runtime one structured place to explain why a long
    tool loop should stop or synthesize.
    """

    repeated_batch_limit: int | None = 4
    non_productive_after_useful_limit: int | None = 2

    rounds_started: int = 0
    tool_rounds: int = 0
    tool_calls: int = 0
    salvage_successes: int = 0
    salvage_failures: int = 0
    textual_tool_retries: int = 0
    policy_denial_retries: int = 0
    fact_check_corrections: int = 0
    approval_required_count: int = 0
    tool_result_raw_chars: int = 0
    tool_result_model_chars: int = 0
    persisted_tool_results: int = 0
    productive_rounds: int = 0
    non_productive_rounds: int = 0
    unknown_outcome_rounds: int = 0
    consecutive_non_productive: int = 0
    has_useful_result: bool = False
    consecutive_same_tool_batch: int = 0
    last_tool_batch_signature: tuple[tuple[str, str], ...] | None = None
    last_decision: BudgetDecision = field(
        default_factory=lambda: BudgetDecision(action=DECISION_CONTINUE)
    )

    def begin_round(self) -> None:
        self.rounds_started += 1

    def record_tool_batch(self, signature: tuple[tuple[str, str], ...], count: int) -> None:
        self.tool_rounds += 1
        self.tool_calls += max(0, int(count or 0))
        if signature == self.last_tool_batch_signature:
            self.consecutive_same_tool_batch += 1
        else:
            self.last_tool_batch_signature = signature
            self.consecutive_same_tool_batch = 1

    def record_tool_result(self, *, raw_content: str, model_content: str) -> None:
        raw = str(raw_content or "")
        model = str(model_content or "")
        self.tool_result_raw_chars += len(raw)
        self.tool_result_model_chars += len(model)
        if PERSISTED_OUTPUT_TAG in model:
            self.persisted_tool_results += 1

    def record_round_productivity(self, productive: bool | None) -> None:
        if productive is None:
            self.unknown_outcome_rounds += 1
            self.consecutive_non_productive = 0
        elif productive:
            self.productive_rounds += 1
            self.has_useful_result = True
            self.consecutive_non_productive = 0
        else:
            self.non_productive_rounds += 1
            self.consecutive_non_productive += 1

    def record_salvage_success(self) -> None:
        self.salvage_successes += 1

    def record_salvage_failure(self) -> None:
        self.salvage_failures += 1
        self.textual_tool_retries += 1

    def record_textual_tool_retry(self) -> None:
        self.textual_tool_retries += 1

    def record_policy_denial_retry(self) -> None:
        self.policy_denial_retries += 1

    def record_fact_check_correction(self) -> None:
        self.fact_check_corrections += 1

    def record_approval_required(self) -> None:
        self.approval_required_count += 1

    def evaluate_after_tool_round(self) -> BudgetDecision:
        if (
            self.repeated_batch_limit is not None
            and self.consecutive_same_tool_batch >= self.repeated_batch_limit
        ):
            return self._remember_decision(REASON_REPEATED_TOOL_LOOP)
        if (
            self.non_productive_after_useful_limit is not None
            and self.has_useful_result
            and self.consecutive_non_productive >= self.non_productive_after_useful_limit
        ):
            return self._remember_decision(REASON_NON_PRODUCTIVE_AFTER_USEFUL)
        return self._remember_decision("")

    def _remember_decision(self, reason: str) -> BudgetDecision:
        if not reason:
            self.last_decision = BudgetDecision(
                action=DECISION_CONTINUE,
                details=self.snapshot(),
            )
            return self.last_decision
        self.last_decision = BudgetDecision(
            action=DECISION_FORCE_SYNTHESIS,
            reason=reason,
            details=self.snapshot(),
        )
        return self.last_decision

    def snapshot(self) -> dict[str, Any]:
        return {
            "rounds_started": self.rounds_started,
            "tool_rounds": self.tool_rounds,
            "tool_calls": self.tool_calls,
            "salvage_successes": self.salvage_successes,
            "salvage_failures": self.salvage_failures,
            "textual_tool_retries": self.textual_tool_retries,
            "policy_denial_retries": self.policy_denial_retries,
            "fact_check_corrections": self.fact_check_corrections,
            "approval_required_count": self.approval_required_count,
            "tool_result_raw_chars": self.tool_result_raw_chars,
            "tool_result_model_chars": self.tool_result_model_chars,
            "persisted_tool_results": self.persisted_tool_results,
            "productive_rounds": self.productive_rounds,
            "non_productive_rounds": self.non_productive_rounds,
            "unknown_outcome_rounds": self.unknown_outcome_rounds,
            "consecutive_non_productive": self.consecutive_non_productive,
            "has_useful_result": self.has_useful_result,
            "consecutive_same_tool_batch": self.consecutive_same_tool_batch,
        }
