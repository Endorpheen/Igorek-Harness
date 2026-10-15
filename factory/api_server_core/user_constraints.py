from dataclasses import dataclass
import re


@dataclass(frozen=True)
class ExplicitRuntimeConstraints:
    no_subagents: bool = False
    no_background: bool = False
    allow_subagents: bool = False
    explicit_local_scope: bool = False
    explicit_web_research: bool = False


def _normalized_lines(text: str) -> list[str]:
    normalized: list[str] = []
    for raw_line in (text or "").splitlines():
        stripped = raw_line.strip().lower()
        if not stripped:
            continue
        # Normalize Markdown/list prefixes so explicit user constraints survive
        # common bullet formatting, including numbered lists.
        stripped = re.sub(r"^(?:[-*+]\s+|\d+[.)]\s+)", "", stripped)
        stripped = " ".join(stripped.split())
        if stripped:
            normalized.append(stripped)
    return normalized


def extract_explicit_runtime_constraints(text: str) -> ExplicitRuntimeConstraints:
    lines = set(_normalized_lines(text))
    lowered_text = (text or "").lower()
    explicit_local_scope = any(
        marker in lowered_text
        for marker in (
            "текущий проект",
            "текущего проекта",
            "кодовая база",
            "workspace",
            "codebase",
            "current project",
            "repo",
            "repository",
        )
    )
    explicit_web_research = any(
        marker in lowered_text
        for marker in (
            "tavily",
            "web search",
            "search the web",
            "в интернете",
            "sources",
            "news",
        )
    )
    return ExplicitRuntimeConstraints(
        no_subagents=(
            "без subagents" in lines
            or "without subagents" in lines
            or "no subagents" in lines
        ),
        no_background=(
            "без фоновых задач" in lines
            or "without background tasks" in lines
            or "no background tasks" in lines
        ),
        allow_subagents=(
            "используй агента" in lowered_text
            or "use agent" in lowered_text
        ),
        explicit_local_scope=explicit_local_scope,
        explicit_web_research=explicit_web_research,
    )
