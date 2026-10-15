from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from typing import Any

from paroquant.inference import build_prompt


logger = logging.getLogger(__name__)

SUMMARY_PREFIX = (
    "[CONTEXT COMPACTION] Earlier turns in this conversation were compacted "
    "to save context space. The summary below describes work that was already "
    "completed, and the current session state may still reflect that work. "
    "Use the summary and the current state to continue without repeating work:"
)
LEGACY_SUMMARY_PREFIX = "[CONTEXT SUMMARY]:"
PRUNED_TOOL_PLACEHOLDER = "[Old tool output cleared to save context space]"
CHARS_PER_TOKEN = 4
MIN_SUMMARY_TOKENS = 1200
SUMMARY_RATIO = 0.20
SUMMARY_TOKENS_CEILING = 12_000
SUMMARY_FAILURE_COOLDOWN_SECONDS = 600


@dataclass(frozen=True)
class ContextCompactorConfig:
    enabled: bool = True
    threshold: float = 0.50
    target_ratio: float = 0.20
    protect_first_n: int = 3
    protect_last_n: int = 20
    prune_tool_result_chars: int = 200
    per_message_summary_chars: int = 220
    max_summary_items: int = 12
    summary_model: str | None = None


def _stringify_content(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict):
                item_type = str(item.get("type") or "")
                if item_type == "text":
                    text = str(item.get("text") or "")
                    if text:
                        parts.append(text)
                    continue
            try:
                parts.append(json.dumps(item, ensure_ascii=False, sort_keys=True))
            except Exception:
                parts.append(str(item))
        return "\n".join(part for part in parts if part)
    if isinstance(content, dict):
        try:
            return json.dumps(content, ensure_ascii=False, sort_keys=True)
        except Exception:
            return str(content)
    return str(content or "")


def _clip_text(text: str, limit: int) -> str:
    normalized = " ".join(str(text or "").split())
    if len(normalized) <= limit:
        return normalized
    return normalized[: max(0, limit - 3)].rstrip() + "..."


def _looks_like_summary_message(message: dict[str, Any]) -> bool:
    content = _stringify_content(message.get("content")).strip()
    return content.startswith(SUMMARY_PREFIX) or content.startswith(LEGACY_SUMMARY_PREFIX)


def _is_tool_message(message: dict[str, Any]) -> bool:
    role = str(message.get("role") or "")
    return role == "tool" or "tool_call_id" in message


def _has_tool_calls(message: dict[str, Any]) -> bool:
    tool_calls = message.get("tool_calls")
    return isinstance(tool_calls, list) and bool(tool_calls)


def _get_tool_call_id(tool_call: Any) -> str:
    if isinstance(tool_call, dict):
        return str(tool_call.get("id") or "")
    return str(getattr(tool_call, "id", "") or "")


def _estimate_message_tokens(message: dict[str, Any]) -> int:
    total_chars = len(_stringify_content(message.get("content")))
    for tool_call in message.get("tool_calls") or []:
        if isinstance(tool_call, dict):
            total_chars += len(_stringify_content(tool_call.get("function", {}).get("arguments", "")))
        else:
            total_chars += len(str(tool_call))
    return max(1, total_chars // CHARS_PER_TOKEN) + 10


class ContextCompressor:
    def __init__(
        self,
        *,
        model: str,
        tokenizer: Any,
        context_length: int,
        summary_client: Any,
        config: ContextCompactorConfig,
        quiet_mode: bool = False,
    ) -> None:
        self.model = model
        self.tokenizer = tokenizer
        self.context_length = max(1, int(context_length))
        self.summary_client = summary_client
        self.config = config
        self.quiet_mode = quiet_mode
        self.threshold_tokens = int(self.context_length * max(0.05, min(config.threshold, 0.95)))
        self.tail_token_budget = int(self.threshold_tokens * max(0.10, min(config.target_ratio, 0.80)))
        self.max_summary_tokens = min(int(self.context_length * 0.05), SUMMARY_TOKENS_CEILING)
        self.compression_count = 0
        self.last_prompt_tokens = 0
        self.last_completion_tokens = 0
        self.last_total_tokens = 0
        self._summary_failure_cooldown_until = 0.0

    def update_from_response(self, usage: dict[str, Any]) -> None:
        self.last_prompt_tokens = int(usage.get("prompt_tokens", 0) or 0)
        self.last_completion_tokens = int(usage.get("completion_tokens", 0) or 0)
        self.last_total_tokens = int(usage.get("total_tokens", 0) or 0)

    def should_compress(self, prompt_tokens: int | None = None) -> bool:
        if not self.config.enabled:
            return False
        tokens = prompt_tokens if prompt_tokens is not None else self.last_prompt_tokens
        return int(tokens or 0) >= self.threshold_tokens

    def should_compress_preflight(self, messages: list[dict[str, Any]]) -> bool:
        prompt = build_prompt(self.tokenizer, messages, False)
        return self.should_compress(max(1, len(self.tokenizer.encode(prompt))))

    def get_status(self) -> dict[str, Any]:
        return {
            "threshold_tokens": self.threshold_tokens,
            "tail_token_budget": self.tail_token_budget,
            "context_length": self.context_length,
            "compression_count": self.compression_count,
            "last_prompt_tokens": self.last_prompt_tokens,
        }

    def _prune_old_tool_results(self, messages: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
        protect_tail_count = max(self.config.protect_last_n * 3, self.config.protect_last_n)
        prune_boundary = max(0, len(messages) - protect_tail_count)
        pruned: list[dict[str, Any]] = []
        pruned_count = 0

        for idx, message in enumerate(messages):
            cloned = dict(message)
            if idx < prune_boundary and _is_tool_message(message):
                content = _stringify_content(message.get("content"))
                if len(content) > self.config.prune_tool_result_chars and content != PRUNED_TOOL_PLACEHOLDER:
                    cloned["content"] = PRUNED_TOOL_PLACEHOLDER
                    pruned_count += 1
            pruned.append(cloned)
        return pruned, pruned_count

    def _extract_previous_summary(self, messages: list[dict[str, Any]]) -> tuple[str | None, int | None]:
        for idx in range(len(messages) - 1, -1, -1):
            message = messages[idx]
            if not _looks_like_summary_message(message):
                continue
            content = _stringify_content(message.get("content")).strip()
            for prefix in (LEGACY_SUMMARY_PREFIX, SUMMARY_PREFIX):
                if content.startswith(prefix):
                    return content[len(prefix) :].strip(), idx
            return content, idx
        return None, None

    def _align_boundary_forward(self, messages: list[dict[str, Any]], idx: int) -> int:
        while idx < len(messages) and _is_tool_message(messages[idx]):
            idx += 1
        return idx

    def _align_boundary_backward(self, messages: list[dict[str, Any]], idx: int) -> int:
        if idx <= 0 or idx >= len(messages):
            return idx
        check = idx - 1
        while check >= 0 and _is_tool_message(messages[check]):
            check -= 1
        if check >= 0 and _has_tool_calls(messages[check]):
            idx = check
        return idx

    def _find_tail_cut_by_tokens(self, messages: list[dict[str, Any]], head_end: int) -> int:
        accumulated = 0
        cut_idx = len(messages)
        for idx in range(len(messages) - 1, head_end - 1, -1):
            msg_tokens = _estimate_message_tokens(messages[idx])
            if accumulated + msg_tokens > self.tail_token_budget and (len(messages) - idx) >= self.config.protect_last_n:
                break
            accumulated += msg_tokens
            cut_idx = idx

        fallback_cut = max(head_end + 1, len(messages) - self.config.protect_last_n)
        if cut_idx > fallback_cut:
            cut_idx = fallback_cut
        if cut_idx <= head_end:
            cut_idx = fallback_cut
        cut_idx = self._align_boundary_backward(messages, cut_idx)
        return max(cut_idx, head_end + 1)

    def _sanitize_tool_pairs(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        surviving_call_ids: set[str] = set()
        result_call_ids: set[str] = set()

        for message in messages:
            if _has_tool_calls(message):
                for tool_call in message.get("tool_calls") or []:
                    call_id = _get_tool_call_id(tool_call)
                    if call_id:
                        surviving_call_ids.add(call_id)
            if _is_tool_message(message):
                call_id = str(message.get("tool_call_id") or "")
                if call_id:
                    result_call_ids.add(call_id)

        orphaned_results = result_call_ids - surviving_call_ids
        sanitized = [
            message
            for message in messages
            if not (_is_tool_message(message) and str(message.get("tool_call_id") or "") in orphaned_results)
        ]

        missing_results = surviving_call_ids - result_call_ids
        if not missing_results:
            return sanitized

        patched: list[dict[str, Any]] = []
        for message in sanitized:
            patched.append(message)
            if not _has_tool_calls(message):
                continue
            for tool_call in message.get("tool_calls") or []:
                call_id = _get_tool_call_id(tool_call)
                if call_id in missing_results:
                    patched.append(
                        {
                            "role": "tool",
                            "content": "[Result from earlier conversation — see context summary above]",
                            "tool_call_id": call_id,
                        }
                    )
        return patched

    def _serialize_for_summary(self, turns: list[dict[str, Any]]) -> str:
        parts: list[str] = []
        for msg in turns:
            role = str(msg.get("role") or "unknown")
            content = _stringify_content(msg.get("content"))
            if len(content) > 3000:
                content = content[:2000] + "\n...[truncated]...\n" + content[-800:]
            if role == "tool":
                tool_id = str(msg.get("tool_call_id") or "")
                parts.append(f"[TOOL RESULT {tool_id}]: {content}")
                continue
            if role == "assistant" and _has_tool_calls(msg):
                tool_lines: list[str] = []
                for tool_call in msg.get("tool_calls") or []:
                    if isinstance(tool_call, dict):
                        fn = tool_call.get("function", {})
                        name = str(fn.get("name") or "?")
                        args = _stringify_content(fn.get("arguments", ""))
                    else:
                        fn = getattr(tool_call, "function", None)
                        name = str(getattr(fn, "name", "?") if fn else "?")
                        args = ""
                    if len(args) > 500:
                        args = args[:400] + "..."
                    tool_lines.append(f"  {name}({args})")
                content += "\n[Tool calls:\n" + "\n".join(tool_lines) + "\n]"
            parts.append(f"[{role.upper()}]: {content}")
        return "\n\n".join(parts)

    def _compute_summary_budget(self, turns_to_summarize: list[dict[str, Any]]) -> int:
        rough_tokens = sum(_estimate_message_tokens(msg) for msg in turns_to_summarize)
        budget = int(rough_tokens * SUMMARY_RATIO)
        return max(MIN_SUMMARY_TOKENS, min(budget, self.max_summary_tokens))

    async def _generate_summary(self, turns_to_summarize: list[dict[str, Any]], *,
                                previous_summary: str | None = None) -> str | None:
        now = time.monotonic()
        if now < self._summary_failure_cooldown_until:
            return None

        summary_budget = self._compute_summary_budget(turns_to_summarize)
        content_to_summarize = self._serialize_for_summary(turns_to_summarize)
        if previous_summary:
            prompt = f"""You are updating a context compaction summary. A previous compaction produced the summary below. New conversation turns have occurred since then and need to be incorporated.

PREVIOUS SUMMARY:
{previous_summary}

NEW TURNS TO INCORPORATE:
{content_to_summarize}

Update the summary using this exact structure. Preserve still-relevant information. Add new progress. Move items from In Progress to Done when completed. Remove information only if clearly obsolete.

## Goal
[What the user is trying to accomplish]

## Constraints & Preferences
[User preferences, coding style, constraints, important decisions]

## Progress
### Done
[Completed work — include specific file paths, commands run, results obtained]
### In Progress
[Work currently underway]
### Blocked
[Any blockers or issues encountered]

## Key Decisions
[Important technical decisions and why they were made]

## Relevant Files
[Files read, modified, or created — with brief note on each]

## Next Steps
[What needs to happen next]

## Critical Context
[Specific values, error messages, configuration details, or data that must not be lost]

Write only the summary body. No preamble."""
        else:
            prompt = f"""Create a structured handoff summary for a later assistant that will continue this conversation after earlier turns are compacted.

TURNS TO SUMMARIZE:
{content_to_summarize}

Use this exact structure:

## Goal
[What the user is trying to accomplish]

## Constraints & Preferences
[User preferences, coding style, constraints, important decisions]

## Progress
### Done
[Completed work — include specific file paths, commands run, results obtained]
### In Progress
[Work currently underway]
### Blocked
[Any blockers or issues encountered]

## Key Decisions
[Important technical decisions and why they were made]

## Relevant Files
[Files read, modified, or created — with brief note on each]

## Next Steps
[What needs to happen next]

## Critical Context
[Specific values, error messages, configuration details, or data that must not be lost]

Write only the summary body. No preamble."""
        try:
            summary = await self.summary_client.generate_summary(prompt, max_tokens=summary_budget * 2)
            summary = str(summary or "").strip()
            if not summary:
                raise RuntimeError("empty summary response")
            self._summary_failure_cooldown_until = 0.0
            return self._with_summary_prefix(summary)
        except Exception as exc:
            self._summary_failure_cooldown_until = time.monotonic() + SUMMARY_FAILURE_COOLDOWN_SECONDS
            logger.warning(
                "Context compression summary generation failed: %s. Falling back to deterministic summary for %d seconds.",
                exc,
                SUMMARY_FAILURE_COOLDOWN_SECONDS,
            )
            return None

    def _with_summary_prefix(self, summary: str) -> str:
        text = (summary or "").strip()
        for prefix in (LEGACY_SUMMARY_PREFIX, SUMMARY_PREFIX):
            if text.startswith(prefix):
                text = text[len(prefix) :].lstrip()
                break
        return f"{SUMMARY_PREFIX}\n{text}" if text else SUMMARY_PREFIX

    def _build_deterministic_summary(
        self,
        middle_messages: list[dict[str, Any]],
        *,
        previous_summary: str | None,
    ) -> str:
        user_count = sum(1 for msg in middle_messages if str(msg.get("role") or "") == "user")
        assistant_count = sum(1 for msg in middle_messages if str(msg.get("role") or "") == "assistant")
        tool_count = sum(1 for msg in middle_messages if _is_tool_message(msg))
        other_count = max(0, len(middle_messages) - user_count - assistant_count - tool_count)
        lines = [
            "## Progress Snapshot",
            f"- Compacted messages: {len(middle_messages)}",
            f"- Role counts: user={user_count}, assistant={assistant_count}, tool={tool_count}, other={other_count}",
        ]
        if previous_summary:
            lines.extend(["", "## Earlier Compacted Context", previous_summary])
        lines.extend(["", "## Newly Compacted Turns"])
        for message in middle_messages[: self.config.max_summary_items]:
            role = str(message.get("role") or "unknown")
            content = _clip_text(_stringify_content(message.get("content")), self.config.per_message_summary_chars)
            lines.append(f"- {role}: {content or '(empty)'}")
        omitted = len(middle_messages) - min(len(middle_messages), self.config.max_summary_items)
        if omitted > 0:
            lines.append(f"- ... and {omitted} more compacted messages")
        return self._with_summary_prefix("\n".join(lines))

    async def compress(
        self,
        messages: list[dict[str, Any]],
        *,
        current_tokens: int | None = None,
    ) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
        if not self.config.enabled:
            return messages, None
        if len(messages) <= self.config.protect_first_n + self.config.protect_last_n + 1:
            return messages, None

        display_tokens = int(current_tokens or 0)
        if display_tokens and not self.should_compress(display_tokens):
            return messages, None

        working_messages, pruned_count = self._prune_old_tool_results(messages)
        previous_summary, previous_summary_idx = self._extract_previous_summary(working_messages)
        if previous_summary_idx is not None:
            working_messages = [
                message
                for idx, message in enumerate(working_messages)
                if idx != previous_summary_idx
            ]

        compress_start = self._align_boundary_forward(
            working_messages,
            min(self.config.protect_first_n, len(working_messages) - 1),
        )
        compress_end = self._find_tail_cut_by_tokens(working_messages, compress_start)
        if compress_start >= compress_end:
            return messages, None

        turns_to_summarize = working_messages[compress_start:compress_end]
        if not turns_to_summarize:
            return messages, None

        summary_text = await self._generate_summary(turns_to_summarize, previous_summary=previous_summary)
        used_fallback = False
        if not summary_text:
            summary_text = self._build_deterministic_summary(
                turns_to_summarize,
                previous_summary=previous_summary,
            )
            used_fallback = True

        compacted_messages = [
            *working_messages[:compress_start],
            {"role": "assistant", "content": summary_text},
            *working_messages[compress_end:],
        ]
        compacted_messages = self._sanitize_tool_pairs(compacted_messages)
        self.compression_count += 1

        metadata = {
            "applied": True,
            "strategy": "hermes-style-runtime",
            "original_message_count": len(messages),
            "compacted_message_count": len(compacted_messages),
            "compacted_middle_count": len(turns_to_summarize),
            "protect_first_n": self.config.protect_first_n,
            "protect_last_n": self.config.protect_last_n,
            "threshold_tokens": self.threshold_tokens,
            "tail_token_budget": self.tail_token_budget,
            "previous_summary_reused": previous_summary is not None,
            "pruned_tool_results": pruned_count,
            "summary_transport": "fallback" if used_fallback else "client",
            "compression_count": self.compression_count,
        }
        return compacted_messages, metadata
