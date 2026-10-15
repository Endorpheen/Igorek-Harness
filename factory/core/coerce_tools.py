"""Tool argument type coercion.

LLMs frequently return numbers as strings ("42" instead of 42) and booleans
as strings ("true" instead of true).  This module compares each argument
against the tool's registered JSON Schema and attempts safe coercion.

Adapted from Hermes model_tools.py coerce_tool_args().
"""
from __future__ import annotations

from typing import Any

from .tool_registry import registry


def coerce_tool_args(tool_name: str, args: dict[str, Any]) -> dict[str, Any]:
    """Coerce tool call arguments to match their JSON Schema types.

    Original values are preserved when coercion fails.
    """
    if not args or not isinstance(args, dict):
        return args

    schema = registry.get_schema(tool_name)
    if not schema:
        return args

    properties = (schema.get("input_schema") or {}).get("properties")
    if not properties:
        return args

    for key, value in args.items():
        if not isinstance(value, str):
            continue
        prop_schema = properties.get(key)
        if not prop_schema:
            continue
        expected = prop_schema.get("type")
        if not expected:
            continue
        coerced = _coerce_value(value, expected)
        if coerced is not value:
            args[key] = coerced

    return args


def _coerce_value(value: str, expected_type):
    """Attempt to coerce a string *value* to *expected_type*."""
    if isinstance(expected_type, list):
        for t in expected_type:
            result = _coerce_value(value, t)
            if result is not value:
                return result
        return value

    if expected_type in ("integer", "number"):
        return _coerce_number(value, integer_only=(expected_type == "integer"))
    if expected_type == "boolean":
        return _coerce_boolean(value)
    return value


def _coerce_number(value: str, integer_only: bool = False):
    try:
        f = float(value)
    except (ValueError, OverflowError):
        return value
    if f != f or f == float("inf") or f == float("-inf"):
        return value
    if f == int(f):
        return int(f)
    if integer_only:
        return value
    return f


def _coerce_boolean(value: str):
    low = value.strip().lower()
    if low == "true":
        return True
    if low == "false":
        return False
    return value
