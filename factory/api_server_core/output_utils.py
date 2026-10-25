import re


def sanitize_output(text: str) -> str:
    text = re.sub(r"(?is)<system-reminder>.*?(?:</system-reminder>|$)", "", text)
    text = re.sub(r"(?im)<\|channel\>[^\r\n]*", "", text)
    text = re.sub(r"(?im)<channel\|>", "", text)
    text = re.sub(r"(?im)<\|start\|>", "", text)
    text = re.sub(r"(?im)<\|message\|>", "", text)
    text = re.sub(r"(?im)<\|end\|>", "", text)
    text = re.sub(
        r"(?is)(?:\buser\b\s*\bassistant\b\s*)+(?:\s*[\r\n]+)?",
        "",
        text,
    )
    text = re.sub(
        r"(?is)(?:Я здесь\.?\s*[\r\n]+(?:user|assistant)\s*[\r\n]+)+",
        "",
        text,
    )

    lines = text.splitlines()
    filtered: list[str] = []
    skip_needles = (
        "The following asking for help with using Claude Code tools",
        "The system will automatically compress prior messages",
        "The following skills are available for use with the Skill tool",
        "Codebase and user instructions are shown below",
        "You are Claude Code, Anthropic's official CLI for Claude.",
    )
    for line in lines:
        stripped = line.strip()
        if stripped in {
            "user",
            "assistant",
            "<system-reminder>",
            "</system-reminder>",
            "<|channel>",
            "<channel|>",
            "<|start|>",
            "<|message|>",
            "<|end|>",
        }:
            continue
        if any(needle in line for needle in skip_needles):
            continue
        filtered.append(line)

    cleaned = "\n".join(filtered).strip()
    cleaned = re.sub(r"(?ms)^\s*(user|assistant)\s*$", "", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    paragraphs: list[str] = []
    seen: set[str] = set()
    for paragraph in cleaned.split("\n\n"):
        norm = paragraph.strip()
        if not norm:
            continue
        if norm in seen:
            continue
        seen.add(norm)
        paragraphs.append(norm)
    cleaned = "\n\n".join(paragraphs).strip()
    return cleaned or text.strip()


def sanitize_output_for_mode(text: str, mode: str, *, suggestion_mode: str, tool_result_mode: str) -> str:
    cleaned = sanitize_output(text)
    cleaned = re.sub(r"(?is)\[tool_use\b.*?\]", "", cleaned)
    cleaned = re.sub(r"(?is)\[tool_result\b.*?\]", "", cleaned)
    cleaned = re.sub(r"(?is)<tool_call>.*?</tool_call>", "", cleaned)
    cleaned = re.sub(r"(?is)<\|tool_call\|>.*?<tool_call\|>", "", cleaned)
    cleaned = re.sub(r"(?is)<tool_use_error>.*?</tool_use_error>", "", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
    if mode == suggestion_mode:
        cleaned = cleaned.splitlines()[0].strip() if cleaned.strip() else ""
        cleaned = re.sub(r"\s+", " ", cleaned).strip()
        words = cleaned.split()
        if len(words) > 8:
            cleaned = " ".join(words[:8])
    elif mode == tool_result_mode:
        cleaned = re.sub(r"(?i)^\s*Latest tool used:[^\r\n]*(?:\r?\n|$)", "", cleaned)
        cleaned = re.sub(r"(?i)^\s*Tool result:[ \t]*(?:\r?\n)?", "", cleaned)
        cleaned = cleaned.strip()
    return cleaned


def extract_visible_delta(
    raw_text: str,
    emitted_text: str,
    mode: str,
    *,
    suggestion_mode: str,
    tool_result_mode: str,
) -> tuple[str, str]:
    cleaned = sanitize_output_for_mode(
        raw_text,
        mode,
        suggestion_mode=suggestion_mode,
        tool_result_mode=tool_result_mode,
    )
    if not cleaned.startswith(emitted_text):
        return cleaned, cleaned
    return cleaned, cleaned[len(emitted_text):]


def infer_stop_reason(output_tokens: int, max_tokens: int) -> str:
    if output_tokens >= max_tokens:
        return "max_tokens"
    return "end_turn"


def split_stop_sequence(text: str, stop_sequences: list[str] | None) -> tuple[str, str | None, bool]:
    if not text or not stop_sequences:
        return text, None, False

    valid_sequences = [seq for seq in stop_sequences if isinstance(seq, str) and seq]
    if not valid_sequences:
        return text, None, False

    earliest_index: int | None = None
    matched_sequence: str | None = None
    for sequence in valid_sequences:
        index = text.find(sequence)
        if index < 0:
            continue
        if earliest_index is None or index < earliest_index or (index == earliest_index and len(sequence) > len(matched_sequence or "")):
            earliest_index = index
            matched_sequence = sequence

    if earliest_index is not None and matched_sequence is not None:
        return text[:earliest_index], matched_sequence, True

    holdback = 0
    for sequence in valid_sequences:
        max_prefix = min(len(sequence) - 1, len(text))
        for prefix_len in range(max_prefix, 0, -1):
            if text.endswith(sequence[:prefix_len]):
                holdback = max(holdback, prefix_len)
                break

    if holdback:
        return text[:-holdback], None, False
    return text, None, False


def sanitize_tool_preamble(text: str) -> str:
    cleaned = sanitize_output(text)
    cleaned = re.sub(r"\s+", " ", cleaned).strip()
    cleaned = cleaned.strip("\"'` ")
    if not cleaned:
        return ""
    if len(cleaned) > 120:
        cleaned = cleaned[:120].rstrip(" ,.;:-")
    return cleaned
