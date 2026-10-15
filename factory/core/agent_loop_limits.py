from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class CcBackendLoopLimits:
    max_tool_rounds: int | None = None
    repeated_tool_batch_limit: int | None = None
    non_productive_after_useful_limit: int | None = None
    same_tool_batch_block_limit: int | None = None


def _optional_positive_int(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed > 0 else None


def _default_config_path() -> Path:
    return Path(__file__).resolve().parents[1] / "config" / "agent_loop_limits.yaml"


def load_cc_backend_loop_limits(path: str | os.PathLike[str] | None = None) -> CcBackendLoopLimits:
    config_path = Path(
        path
        or os.environ.get("IGOREK_AGENT_LOOP_LIMITS_CONFIG", "")
        or _default_config_path()
    )
    try:
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    except FileNotFoundError:
        raw = {}
    if not isinstance(raw, dict):
        raw = {}

    cc_backend = raw.get("cc_backend", {})
    if not isinstance(cc_backend, dict):
        cc_backend = {}

    return CcBackendLoopLimits(
        max_tool_rounds=_optional_positive_int(cc_backend.get("max_tool_rounds")),
        repeated_tool_batch_limit=_optional_positive_int(
            cc_backend.get("repeated_tool_batch_limit")
        ),
        non_productive_after_useful_limit=_optional_positive_int(
            cc_backend.get("non_productive_after_useful_limit")
        ),
        same_tool_batch_block_limit=_optional_positive_int(
            cc_backend.get("same_tool_batch_block_limit")
        ),
    )
