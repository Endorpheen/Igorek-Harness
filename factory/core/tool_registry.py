"""Central registry for factory tools.

Each tool file calls ``registry.register()`` at module level to declare its
schema, handler, toolset membership, and availability check.  Callers query
the registry instead of maintaining parallel data structures.

Import chain (circular-import safe):
    factory/core/tool_registry.py  (no imports from tool modules)
           ^
    factory/core/cc_backend_tools.py  (imports registry at module level)
           ^
    factory/core/cc_backend_executor.py  (imports tools module)

Adapted from Hermes tools/registry.py — simplified for our sync-only,
Anthropic input_schema format tools.
"""

import json
import logging
from contextlib import ExitStack
from threading import RLock
from typing import Any, Callable

from .tool_result_outcome import ToolResultText

logger = logging.getLogger(__name__)


class ToolEntry:
    """Metadata for a single registered tool."""

    __slots__ = (
        "name", "toolset", "schema", "handler", "check_fn",
        "requires_env", "description",
    )

    def __init__(
        self,
        name: str,
        toolset: str,
        schema: dict[str, Any],
        handler: Callable,
        check_fn: Callable | None,
        requires_env: list[str] | None,
        description: str,
    ):
        self.name = name
        self.toolset = toolset
        self.schema = schema
        self.handler = handler
        self.check_fn = check_fn
        self.requires_env = requires_env or []
        self.description = description


class ToolRegistry:
    """Singleton registry that collects tool schemas + handlers."""

    def __init__(self) -> None:
        self._tools: dict[str, ToolEntry] = {}
        self._execution_guards = []
        self._guards_lock = RLock()
        self._toolset_checks: dict[str, Callable] = {}

    # ------------------------------------------------------------------
    # Registration
    # ------------------------------------------------------------------

    def register(
        self,
        name: str,
        toolset: str,
        schema: dict[str, Any],
        handler: Callable,
        check_fn: Callable | None = None,
        requires_env: list[str] | None = None,
        description: str = "",
    ) -> None:
        """Register a tool.  Called at module-import time by each tool block."""
        existing = self._tools.get(name)
        if existing and existing.toolset != toolset:
            logger.warning(
                "Tool name collision: '%s' (toolset '%s') overwritten by '%s'",
                name, existing.toolset, toolset,
            )
        self._tools[name] = ToolEntry(
            name=name,
            toolset=toolset,
            schema=schema,
            handler=handler,
            check_fn=check_fn,
            requires_env=requires_env or [],
            description=description or schema.get("description", ""),
        )
        if check_fn and toolset not in self._toolset_checks:
            self._toolset_checks[toolset] = check_fn

    def deregister(self, name: str, *, expected_handler: Callable | None = None) -> None:
        """Remove a tool.  Cleans up toolset check if last tool in toolset."""
        entry = self._tools.get(name)
        if expected_handler is not None and (entry is None or entry.handler is not expected_handler):
            return
        entry = self._tools.pop(name, None)
        if entry is None:
            return
        if entry.toolset in self._toolset_checks and not any(
            e.toolset == entry.toolset for e in self._tools.values()
        ):
            self._toolset_checks.pop(entry.toolset, None)
        logger.debug("Deregistered tool: %s", name)

    # ------------------------------------------------------------------
    # Schema retrieval (Anthropic format)
    # ------------------------------------------------------------------

    def get_definitions(self, toolset: str | None = None) -> list[dict[str, Any]]:
        """Return Anthropic-format tool schemas.

        Only tools whose ``check_fn()`` returns True (or have no check_fn)
        are included.  If *toolset* is given, only tools from that toolset.
        """
        check_results: dict[Callable, bool] = {}
        result: list[dict[str, Any]] = []

        for name in sorted(self._tools):
            entry = self._tools[name]
            if toolset and entry.toolset != toolset:
                continue
            if entry.check_fn:
                if entry.check_fn not in check_results:
                    try:
                        check_results[entry.check_fn] = bool(entry.check_fn())
                    except Exception:
                        check_results[entry.check_fn] = False
                        logger.debug("Tool %s check raised; skipping", name)
                if not check_results[entry.check_fn]:
                    continue
            result.append(entry.schema)

        return result

    # ------------------------------------------------------------------
    # Dispatch
    # ------------------------------------------------------------------

    def add_execution_guard(self, guard) -> None:
        with self._guards_lock:
            if guard not in self._execution_guards:
                self._execution_guards.append(guard)

    def remove_execution_guard(self, guard) -> None:
        with self._guards_lock:
            if guard in self._execution_guards:
                self._execution_guards.remove(guard)

    def execution_guards(self):
        with self._guards_lock:
            return tuple(self._execution_guards)

    def execution_error(self, name, args, **context):
        for guard in self.execution_guards():
            error = guard.policy_error(name, args, context)
            if error:
                return error
        return None

    def dispatch(self, name: str, args: dict[str, Any], **kwargs: Any) -> str:
        """Execute a tool handler by name.

        All exceptions are caught and returned as ``{"error": "..."}``
        JSON string for consistent error format.
        """
        entry = self._tools.get(name)
        if not entry:
            return ToolResultText(json.dumps({"error": f"Unknown tool: {name}"}), False)
        try:
            with ExitStack() as stack:
                for guard in self.execution_guards():
                    error = stack.enter_context(guard.lease(name, args, kwargs))
                    if error:
                        return ToolResultText(json.dumps(error, ensure_ascii=False), False)
                return entry.handler(args, **kwargs)
        except Exception as e:
            logger.error("Tool %s dispatch error: %s: %s", name, type(e).__name__, e)
            return ToolResultText(json.dumps(
                {"error": f"Tool execution failed: {type(e).__name__}: {e}"}
            ), False)

    # ------------------------------------------------------------------
    # Query helpers
    # ------------------------------------------------------------------

    def get_all_tool_names(self) -> list[str]:
        """Return sorted list of all registered tool names."""
        return sorted(self._tools.keys())

    def get_schema(self, name: str) -> dict[str, Any] | None:
        """Return a tool's raw schema, bypassing check_fn filtering."""
        entry = self._tools.get(name)
        return entry.schema if entry else None

    def get_toolset_for_tool(self, name: str) -> str | None:
        """Return the toolset a tool belongs to, or None."""
        entry = self._tools.get(name)
        return entry.toolset if entry else None

    def has_tool(self, name: str) -> bool:
        """Check if a tool is registered (regardless of availability)."""
        return name in self._tools

    def is_toolset_available(self, toolset: str) -> bool:
        """Check if a toolset's requirements are met."""
        check = self._toolset_checks.get(toolset)
        if not check:
            return True
        try:
            return bool(check())
        except Exception:
            return False

    def get_tool_to_toolset_map(self) -> dict[str, str]:
        """Return ``{tool_name: toolset_name}`` for every registered tool."""
        return {name: e.toolset for name, e in self._tools.items()}


# Module-level singleton
registry = ToolRegistry()


# ---------------------------------------------------------------------------
# Helpers for tool response serialization
# ---------------------------------------------------------------------------

def tool_error(message: str, **extra: Any) -> str:
    """Return a JSON error string for tool handlers."""
    result: dict[str, Any] = {"error": str(message)}
    if extra:
        result.update(extra)
    return json.dumps(result, ensure_ascii=False)


def tool_result(data: Any = None, **kwargs: Any) -> str:
    """Return a JSON result string for tool handlers."""
    if data is not None:
        return json.dumps(data, ensure_ascii=False)
    return json.dumps(kwargs, ensure_ascii=False)
