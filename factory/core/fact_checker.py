"""Fact-check module for detecting model hallucination after tool_result."""

from typing import Any


def check_tool_result_fact(
    tool_results: list[dict[str, Any]],
    model_text: str,
    log_fn=None,
) -> tuple[bool, str]:
    """Check if model hallucinated after tool_result.

    Args:
        tool_results: List of tool result blocks from the round.
        model_text: The text response from the model.
        log_fn: Optional logging function.

    Returns:
        Tuple of (needs_correction, error_message).
        If needs_correction is True, model must retry with error_message.
    """
    if not tool_results or not model_text:
        return False, ""

    model_text_lower = model_text.lower()

    for tr in tool_results:
        if tr.get("type") != "tool_result":
            continue

        tool_name = tr.get("tool_name", "")

        # Bash single-line output check (branch name, path, version, etc)
        if tool_name == "Bash":
            raw = tr.get("content", "").strip()
            if not raw:
                continue

            # Extract first line only
            bash_output = raw.split("\n")[0]

            # Check for short single-token output (branch, path, hash, etc)
            if bash_output and len(bash_output) < 50 and " " not in bash_output:
                if bash_output.lower() not in model_text_lower:
                    # Model hallucinated - check for wrong value in text
                    text_words = model_text.split()
                    for word in text_words:
                        word_clean = word.strip("`*,.!?")
                        if (
                            len(word_clean) > 2
                            and word_clean.lower() != bash_output.lower()
                        ):
                            if log_fn:
                                log_fn(f"[fact-check] DETECTED: tool_output='{bash_output}', model_wrote='{word_clean}'")
                            return True, (
                                f"ОШИБКА ФАКТА: Tool result содержит '{bash_output}', "
                                f"но в ответе написано '{word_clean}'. "
                                f"Используй ТОЛЬКО факты из Tool result, не из памяти. "
                                f"Перепиши ответ с правильным значением."
                            )

    if log_fn:
        log_fn(f"[fact-check] PASSED: model used tool_result correctly")

    return False, ""


def check_pwd_output(
    tool_results: list[dict[str, Any]],
    model_text: str,
    log_fn=None,
) -> tuple[bool, str]:
    """Check if model correctly reported pwd output."""
    if not tool_results or not model_text:
        return False, ""

    model_text_lower = model_text.lower()

    for tr in tool_results:
        if tr.get("type") == "tool_result" and tr.get("tool_name") == "Bash":
            raw = tr.get("content", "").strip()
            if not raw:
                continue

            # Extract first line (the actual path)
            pwd_output = raw.split("\n")[0]

            # Check if path is present in model response
            if pwd_output and pwd_output not in model_text_lower:
                if log_fn:
                    log_fn(f"[fact-check] pwd output not in model text: {pwd_output}")
                return True, (
                    f"ОШИБКА ФАКТА: pwd показал '{pwd_output}', но в ответе этого нет. "
                    f"Используй ТОЛЬКО вывод из Tool result."
                )

    return False, ""


def check_git_status(
    tool_results: list[dict[str, Any]],
    model_text: str,
    log_fn=None,
) -> tuple[bool, str]:
    """Check if model correctly reported git status."""
    if not tool_results or not model_text:
        return False, ""

    model_text_lower = model_text.lower()
    keywords = ["clean", "modified", "untracked", "nothing to commit"]

    for tr in tool_results:
        if tr.get("type") == "tool_result" and tr.get("tool_name") == "Bash":
            raw = tr.get("content", "").lower()
            if "git status" not in raw and "git status" not in tr.get("tool_input", {}).get("command", ""):
                continue

            # Check that at least some git status keywords appear in model output
            found_keywords = sum(1 for kw in keywords if kw in raw and kw in model_text_lower)
            if raw and found_keywords == 0:
                if log_fn:
                    log_fn(f"[fact-check] git status: no keywords in model output")
                return True, (
                    f"ОШИБКА ФАКТА: git status показал изменения, но в ответе ничего об этом нет. "
                    f"Используй ТОЛЬКО факты из Tool result."
                )

    return False, ""