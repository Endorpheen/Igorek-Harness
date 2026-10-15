from __future__ import annotations

from typing import Any

from .prompt_assembly import (
    build_system_prompt as assemble_system_prompt,
    build_tools_prompt as assemble_tools_prompt,
)

__all__ = ["build_system_prompt", "build_tools_prompt"]


def build_system_prompt(*, extra_blocks: list[str] | None = None, model_name: str | None = None, platform: str | None = None) -> str:
    return assemble_system_prompt(extra_blocks=extra_blocks, model_name=model_name, platform=platform)


def build_tools_prompt(tools: list[dict[str, Any]]) -> str:
    return assemble_tools_prompt(tools)
