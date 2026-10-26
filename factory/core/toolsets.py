"""Toolset definitions for grouping tools.

Toolsets are named groups of tools. They allow enabling/disabling groups
of tools together and querying availability by category.

Adapted from Hermes toolsets.py — simplified without includes/composition
for now; can be extended later if needed.
"""

from __future__ import annotations

from typing import Any

# ---------------------------------------------------------------------------
# Static toolset definitions
# ---------------------------------------------------------------------------

TOOLSETS: dict[str, dict[str, Any]] = {
    "file": {
        "description": "File read/write/edit operations",
        "tools": ["Write", "Read", "Edit", "SendApk"],
    },
    "terminal": {
        "description": "Shell command execution",
        "tools": ["Bash"],
    },
    "search": {
        "description": "File search and content search",
        "tools": ["Glob", "Grep"],
    },
    "web": {
        "description": "Web search and content extraction (requires TAVILY_API_KEY)",
        "tools": ["web_search", "web_extract"],
    },
    "session": {
        "description": "Search past conversation sessions",
        "tools": ["SessionSearch", "memory_forget", "update_plan", "render_chart", "render_emoji"],
    },
    "skills": {
        "description": "Discover, inspect, scan, and manage local skills",
        "tools": ["skills_categories", "skills_list", "skill_view", "skill_scan", "skill_manage"],
    },
    "mcp": {
        "description": "MCP server tools (image generation, etc.)",
        "tools": [],  # populated dynamically at startup
    },
    "interactive": {
        "description": "Interactive user input tools (forms, multi-select, confirm)",
        "tools": ["request_user_input"],
    },
    "browser": {
        "description": "Real browser automation via agent-browser (open, snapshot, click, type, screenshot)",
        "tools": ["browser"],
    },
    "all": {
        "description": "All available tools",
        "tools": [],
        "includes": ["file", "terminal", "search", "web", "session", "skills", "mcp", "browser"],
    },
}


def resolve_toolset(name: str) -> list[str]:
    """Resolve a toolset name to a list of tool names.

    Handles ``"all"`` and ``"*"`` as aliases for every tool.
    Recursively resolves ``includes`` with cycle detection.
    """
    if name in ("all", "*"):
        result: set[str] = set()
        for ts_name, ts_def in TOOLSETS.items():
            if ts_name == "all":
                continue
            result.update(resolve_toolset(ts_name))
        return sorted(result)

    ts_def = TOOLSETS.get(name)
    if not ts_def:
        return []

    tools = set(ts_def.get("tools", []))
    visited: set[str] = {name}

    def _resolve_includes(includes: list[str]) -> None:
        for inc in includes:
            if inc in visited:
                continue
            visited.add(inc)
            inc_def = TOOLSETS.get(inc)
            if not inc_def:
                continue
            tools.update(inc_def.get("tools", []))
            _resolve_includes(inc_def.get("includes", []))

    _resolve_includes(ts_def.get("includes", []))
    return sorted(tools)


def get_toolset_names() -> list[str]:
    """Return sorted list of all toolset names."""
    return sorted(TOOLSETS.keys())


def validate_toolset(name: str) -> bool:
    """Check if a toolset name is valid."""
    return name in TOOLSETS or name in ("all", "*")


def user_facing_toolset_names() -> list[str]:
    """Return toolset names suitable for user selection (excludes 'all' and 'mcp')."""
    return [name for name in sorted(TOOLSETS) if name not in ("all", "mcp")]
