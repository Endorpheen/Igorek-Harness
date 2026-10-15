from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Sequence

from .prompts import (
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
