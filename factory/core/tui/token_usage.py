"""TUI token usage models and display formatting (port of token_usage.rs)."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


BASELINE_TOKENS: int = 12000


def format_with_separators(n: int) -> str:
    """Format ``n`` with comma thousands separators (port of ``format_with_separators``)."""
    return f"{n:,}"


@dataclass
class TokenUsage:
    """Port of ``codex_protocol::TokenUsage``."""
    input_tokens: int = 0
    cached_input_tokens: int = 0
    output_tokens: int = 0
    reasoning_output_tokens: int = 0
    total_tokens: int = 0

    def is_zero(self) -> bool:
        return self.total_tokens == 0

    def cached_input(self) -> int:
        return max(0, self.cached_input_tokens)

    def non_cached_input(self) -> int:
        return max(0, self.input_tokens - self.cached_input())

    def blended_total(self) -> int:
        return max(0, self.non_cached_input() + max(0, self.output_tokens))

    def tokens_in_context_window(self) -> int:
        """Return the raw ``total_tokens`` value (port one-to-one)."""
        return self.total_tokens

    def percent_of_context_window_remaining(self, context_window: int) -> int:
        """Return percentage of context window remaining (port one-to-one).

        Returns 0 if the context window is <= BASELINE_TOKENS, otherwise
        returns the percent of the (window - baseline) budget that is
        still available.
        """
        if context_window <= BASELINE_TOKENS:
            return 0
        effective_window = context_window - BASELINE_TOKENS
        used = max(0, self.tokens_in_context_window() - BASELINE_TOKENS)
        remaining = max(0, effective_window - used)
        pct = (remaining / effective_window) * 100.0
        return max(0, min(100, round(pct)))


@dataclass
class TokenUsageInfo:
    """Port of ``codex::token_usage::TokenUsageInfo``."""
    total_token_usage: TokenUsage = field(default_factory=TokenUsage)
    last_token_usage: TokenUsage = field(default_factory=TokenUsage)
    model_context_window: Optional[int] = None


def display_token_usage(usage: TokenUsage) -> str:
    """Display formatter for ``TokenUsage`` (port of ``impl Display for TokenUsage``)."""
    cached_part = ""
    if usage.cached_input() > 0:
        cached_part = f" (+ {format_with_separators(usage.cached_input())} cached)"
    reasoning_part = ""
    if usage.reasoning_output_tokens > 0:
        reasoning_part = f" (reasoning {format_with_separators(usage.reasoning_output_tokens)})"
    return (
        f"Token usage: total={format_with_separators(usage.blended_total())} "
        f"input={format_with_separators(usage.non_cached_input())}{cached_part} "
        f"output={format_with_separators(usage.output_tokens)}{reasoning_part}"
    )


__all__ = [
    "BASELINE_TOKENS",
    "TokenUsage",
    "TokenUsageInfo",
    "display_token_usage",
    "format_with_separators",
]
