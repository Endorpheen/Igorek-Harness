"""Output-budget validation shared by profile and isolated task boundaries."""
from __future__ import annotations


def optional_output_token_limit(value: object) -> int | None:
    """Accept an exact positive token count, or None for automatic budgeting."""
    if value is None:
        return None
    if type(value) is not int or value <= 0:
        raise ValueError("max_output_tokens must be a positive integer or null")
    return value
