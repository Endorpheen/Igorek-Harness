from __future__ import annotations

import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence

try:
    from factory.api_server_core.prompts import (
        agent_tool_rules,
        ask_user_question_rules,
        system_prompt,
        tools_intro,
        tools_prefer_tool_over_refusal,
        tools_usage_rules,
    )
except ModuleNotFoundError:
    project_root = Path(__file__).resolve().parent
    root_str = str(project_root)
    if root_str in sys.path:
        sys.path.remove(root_str)
    sys.path.insert(0, root_str)
    sys.modules.pop("core", None)
    from factory.api_server_core.prompts import (
        agent_tool_rules,
        ask_user_question_rules,
        system_prompt,
        tools_intro,
        tools_prefer_tool_over_refusal,
        tools_usage_rules,
    )


@dataclass(frozen=True)
class PromptBlock:
    key: str
    content: str
    title: str | None = None


OPENAI_MODEL_EXECUTION_GUIDANCE = (
    "# Execution discipline\n"
    "- Используй инструменты, когда это повышает точность, полноту или проверяемость ответа.\n"
    "- Не останавливайся раньше времени, если ещё один tool call materially improves the result.\n"
    "- Если результат инструмента пустой или частичный, попробуй другой запрос или стратегию до отказа.\n"
    "- Для текущих фактов, состояния системы, файлов, git и вычислений не полагайся на память, а используй инструменты.\n"
    "- Перед финальным ответом проверь, что требования пользователя действительно выполнены.\n"
)

GOOGLE_MODEL_OPERATIONAL_GUIDANCE = (
    "# Google model operational directives\n"
    "- Всегда используй абсолютные пути для файловых операций.\n"
    "- Перед изменениями сначала прочитай или найди релевантные файлы.\n"
    "- Не предполагаи наличие зависимостей: сначала проверь проектные файлы.\n"
    "- Держи пояснения короткими и прикладными.\n"
    "- Если несколько независимых чтений можно сделать сразу, предпочитай параллельный сбор контекста.\n"
    "- Работай до завершения задачи, не останавливайся на плане без исполнения.\n"
)

TOOL_USE_ENFORCEMENT_GUIDANCE = (
    "# Tool-use enforcement\n"
    "Если ты пишешь, что сейчас что-то сделаешь, в этом же ответе должен быть реальный tool call.\n"
    "Нельзя заканчивать ход обещанием будущего действия без фактического вызова инструмента.\n"
    "Каждый ответ должен либо реально продвигать задачу инструментами, либо давать финальный результат.\n"
)

PLATFORM_HINTS = {
    "telegram": (
        "Ты работаешь в Telegram. Не полагайся на markdown-рендеринг как на обязательный формат. "
        "Если нужно отправить файл пользователю, используй предусмотренный runtime-механизм вложений."
    ),
    "cli": (
        "Ты работаешь в CLI. Предпочитай простой текст, хорошо читаемый в терминале."
    ),
}

_CONTEXT_THREAT_PATTERNS = [
    (r"ignore\s+(previous|all|above|prior)\s+instructions", "prompt_injection"),
    (r"do\s+not\s+tell\s+the\s+user", "deception_hide"),
    (r"system\s+prompt\s+override", "sys_prompt_override"),
    (r"disregard\s+(your|all|any)\s+(instructions|rules|guidelines)", "disregard_rules"),
    (r"act\s+as\s+(if|though)\s+you\s+(have\s+no|don't\s+have)\s+(restrictions|limits|rules)", "bypass_restrictions"),
]
_CONTEXT_FILE_MAX_CHARS = 20_000


def _clean_text(value: str | None) -> str:
    return str(value or "").strip()


def _append_block(blocks: list[PromptBlock], key: str, content: str | None, *, title: str | None = None) -> None:
    text = _clean_text(content)
    if text:
        blocks.append(PromptBlock(key=key, content=text, title=title))


def _render_block(block: PromptBlock) -> str:
    if block.title:
        return f"## {block.title}\n{block.content}"
    return block.content


def render_prompt_blocks(blocks: Sequence[PromptBlock]) -> str:
    return "\n\n".join(_render_block(block) for block in blocks if _clean_text(block.content))


def _model_guidance_blocks(model_name: str | None) -> list[PromptBlock]:
    model = _clean_text(model_name).lower()
    blocks: list[PromptBlock] = []
    if not model:
        return blocks
    if any(token in model for token in ("gpt", "codex")):
        _append_block(blocks, "openai_execution", OPENAI_MODEL_EXECUTION_GUIDANCE, title="Model Guidance")
    if any(token in model for token in ("gemini", "gemma")):
        _append_block(blocks, "tool_enforcement", TOOL_USE_ENFORCEMENT_GUIDANCE, title="Tool Discipline")
        _append_block(blocks, "google_ops", GOOGLE_MODEL_OPERATIONAL_GUIDANCE, title="Operational Guidance")
    return blocks


def _platform_block(platform: str | None) -> PromptBlock | None:
    value = PLATFORM_HINTS.get(_clean_text(platform).lower())
    if not value:
        return None
    return PromptBlock(key="platform", title="Platform", content=value)


def build_system_prompt_blocks(
    *,
    extra_blocks: list[str] | None = None,
    model_name: str | None = None,
    platform: str | None = None,
) -> list[PromptBlock]:
    blocks: list[PromptBlock] = []
    _append_block(blocks, "identity", system_prompt())
    platform_block = _platform_block(platform)
    if platform_block is not None:
        blocks.append(platform_block)
    blocks.extend(_model_guidance_blocks(model_name))
    for idx, block in enumerate(extra_blocks or [], start=1):
        _append_block(blocks, f"extra_{idx}", block)
    return blocks


def build_system_prompt(
    *,
    extra_blocks: list[str] | None = None,
    model_name: str | None = None,
    platform: str | None = None,
) -> str:
    return render_prompt_blocks(
        build_system_prompt_blocks(
            extra_blocks=extra_blocks,
            model_name=model_name,
            platform=platform,
        )
    )


def build_tools_prompt(tools: list[dict]) -> str:
    if not tools:
        return ""

    tool_names = [str(tool.get("name", "tool")) for tool in tools[:24]]
    valid_names = ", ".join(tool_names)
    lines = tools_intro().splitlines()
    for tool in tools[:24]:
        name = str(tool.get("name", "tool"))
        description = str(tool.get("description", "")).strip()
        short_desc = re.sub(r"\s+", " ", description).strip()
        if len(short_desc) > 180:
            short_desc = short_desc[:177] + "..."
        lines.append(f"- {name}: {short_desc}" if short_desc else f"- {name}")

    lines.append(tools_usage_rules(valid_names))
    lines.append(agent_tool_rules())
    lines.append(ask_user_question_rules())
    lines.append(tools_prefer_tool_over_refusal())
    return "\n".join(lines)


def _filesystem_policy_text(*, workdir: str, filesystem_mode: str) -> str:
    if filesystem_mode == "user-read":
        return (
            "Файловый режим: user-read.\n"
            f"- Читать можно внутри workdir: {workdir}\n"
            f"- Читать также можно в пользовательских директориях: {Path.home()}, /mnt, /media, /tmp\n"
            f"- Писать и редактировать можно только внутри workdir: {workdir}\n"
            "- Системные директории /etc, /var, /usr, /bin, /sbin, /lib*, /proc, /sys, /dev, /run, /boot, /root закрыты\n"
            "- Bash выполняется с cwd=workdir"
        )
    if filesystem_mode == "full-dangerous":
        return (
            "Файловый режим: full-dangerous.\n"
            "- Читать и писать можно почти везде по абсолютным путям.\n"
            "- Системные директории /etc, /var, /usr, /bin, /sbin, /lib*, /proc, /sys, /dev, /run, /boot, /root закрыты.\n"
            "- Bash выполняется с cwd=workdir."
        )
    return (
        "Файловый режим: workspace.\n"
        f"- Читать можно только внутри workdir: {workdir}\n"
        f"- Писать и редактировать можно только внутри workdir: {workdir}\n"
        "- Bash выполняется с cwd=workdir"
    )


def _cc_backend_core_rules() -> str:
    return (
        "Primary working directory задаёт корень для всех файловых действий.\n"
        "Все пути в tool-вызовах должны быть абсолютными.\n"
        "Не выдумывай ограничения: опирайся только на фактический файловый режим и доступные инструменты.\n"
        "Не запускай браузеры, редакторы и внешние GUI-приложения.\n"
        "Если пользователь просто общается, отвечай текстом и не вызывай инструменты без необходимости."
    )


def _web_tools_guidance() -> str:
    return (
        "Доступны web_search и web_extract.\n"
        "- Если пользователь даёт URL и просит посмотреть страницу, используй web_extract.\n"
        "- Если пользователь просит что-то найти в интернете, используй web_search.\n"
        "- Если пользователь явно просит использовать web_search или web_extract, вызывай именно этот инструмент напрямую.\n"
        "- Bash с curl/wget для интернета допустим только как запасной путь.\n"
        "Примеры:\n"
        "web_search(query=\"фильмы ужасов 2026 в кинотеатрах СПб\", limit=5)\n"
        "web_extract(urls=[\"https://example.com\"])\n"
        "Нельзя печатать пользователю строки вида web_search(...) или web_extract(...); вместо этого делай настоящий tool call."
    )


def build_cc_backend_system_prompt(
    *,
    workdir: str,
    filesystem_mode: str,
    model_name: str | None = None,
    foundation_context: str = "",
    skills_text: str = "",
    wrapped_task_context: str = "",
    web_tools_enabled: bool = False,
) -> str:
    blocks: list[PromptBlock] = build_system_prompt_blocks(model_name=model_name)
    _append_block(
        blocks,
        "workspace",
        (
            f"Primary working directory: {workdir}\n"
            f"Твоя рабочая директория (workdir): {workdir}\n"
            f"Пример абсолютного пути: {workdir}/file.txt"
        ),
        title="Workspace",
    )
    _append_block(blocks, "filesystem", _filesystem_policy_text(workdir=workdir, filesystem_mode=filesystem_mode), title="Filesystem")
    _append_block(blocks, "cc_rules", _cc_backend_core_rules(), title="Runtime Rules")
    if web_tools_enabled:
        _append_block(blocks, "web_tools", _web_tools_guidance(), title="Web Tools")
    _append_block(blocks, "foundation", foundation_context, title="Foundation Memory")
    _append_block(blocks, "skills", skills_text, title="Skills")
    _append_block(blocks, "wrapped_context", wrapped_task_context, title="Context")
    return render_prompt_blocks(blocks)


def build_codex_backend_input(
    *,
    workdir: str,
    profile: str,
    stage: str,
    task_text: str,
    capabilities: Iterable[str] | None = None,
    input_files: Iterable[str] | None = None,
    skills_text: str = "",
    foundation_context: str = "",
) -> str:
    blocks = [
        PromptBlock(
            key="identity",
            content=(
                "Всегда отвечай на русском языке. Никакого английского в ответах.\n"
                f"Текущая рабочая директория: {workdir}\n"
                "Разрешено создавать и изменять файлы только внутри этой рабочей директории.\n"
                "Не используй пути вне текущей рабочей директории."
            ),
        ),
        PromptBlock(key="profile", title="Runtime", content=f"Профиль выполнения: {profile}\nЭтап задачи: {stage}"),
    ]
    capability_items = [str(item).strip() for item in (capabilities or []) if str(item).strip()]
    input_file_items = [str(item).strip() for item in (input_files or []) if str(item).strip()]
    if capability_items:
        _append_block(blocks, "capabilities", ", ".join(capability_items), title="Capabilities")
    if input_file_items:
        _append_block(blocks, "input_files", ", ".join(input_file_items), title="Input Files")
    _append_block(blocks, "skills", skills_text, title="Skills")
    _append_block(blocks, "foundation", foundation_context, title="Foundation Memory")
    _append_block(blocks, "task", task_text, title="User Task")
    return render_prompt_blocks(blocks)


def _scan_context_content(content: str, filename: str) -> str:
    findings: list[str] = []
    for pattern, label in _CONTEXT_THREAT_PATTERNS:
        if re.search(pattern, content, re.IGNORECASE):
            findings.append(label)
    if findings:
        return f"[BLOCKED: {filename} contained potential prompt injection ({', '.join(findings)}).]"
    return content


def _truncate_content(content: str, filename: str) -> str:
    if len(content) <= _CONTEXT_FILE_MAX_CHARS:
        return content
    head_chars = int(_CONTEXT_FILE_MAX_CHARS * 0.7)
    tail_chars = int(_CONTEXT_FILE_MAX_CHARS * 0.2)
    marker = f"\n\n[...truncated {filename}: kept {head_chars}+{tail_chars} of {len(content)} chars.]\n\n"
    return content[:head_chars] + marker + content[-tail_chars:]


def build_context_files_prompt(cwd: str | None = None) -> str:
    base = Path(cwd or ".").resolve()
    sections: list[str] = []
    for name in ("AGENTS.md", "agents.md", "CLAUDE.md", "claude.md", ".cursorrules"):
        candidate = base / name
        if not candidate.is_file():
            continue
        try:
            content = _clean_text(candidate.read_text(encoding="utf-8"))
        except Exception:
            continue
        if not content:
            continue
        content = _scan_context_content(content, candidate.name)
        sections.append(_truncate_content(f"## {candidate.name}\n\n{content}", candidate.name))
    return "\n\n".join(section for section in sections if section)
