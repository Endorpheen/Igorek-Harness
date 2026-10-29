"""Compatibility entry points for the retired lexical fact checker.

Free-form prose has no claim/evidence binding. Missing a tool-output word is
not a contradiction, and finding it does not establish factual correctness.
These legacy entry points abstain; the executor enforces protocol and tool
execution outcomes separately without asserting that prose was fact-checked.
"""
from typing import Any


def check_tool_result_fact(
    tool_results: list[dict[str, Any]], model_text: str, log_fn=None,
) -> tuple[bool, str]:
    """Abstain from unsupported natural-language factual judgments."""
    return False, ""


def check_pwd_output(
    tool_results: list[dict[str, Any]], model_text: str, log_fn=None,
) -> tuple[bool, str]:
    """Retain import compatibility without requiring a path in every answer."""
    return False, ""


def check_git_status(
    tool_results: list[dict[str, Any]], model_text: str, log_fn=None,
) -> tuple[bool, str]:
    """Retain import compatibility without keyword-based contradictions."""
    return False, ""
