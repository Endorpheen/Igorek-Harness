"""Validated declarative charts carried durably by ordinary tool history."""
from __future__ import annotations

import hashlib
import json
import math
import re
import uuid

from .tool_registry import registry, tool_error, tool_result

MAX_CHART_BYTES = 24000
MAX_LABELS = 40
MAX_SERIES = 6


def validate_chart(value):
    if not isinstance(value, dict):
        raise ValueError("Chart arguments must be an object")
    allowed = {"type", "title", "labels", "series", "unit", "source", "chart_id", "y_min", "y_max"}
    unknown = set(value) - allowed
    if unknown:
        raise ValueError("Unknown chart fields: " + ", ".join(sorted(unknown)))
    if len(json.dumps(value, ensure_ascii=True, allow_nan=True).encode()) > MAX_CHART_BYTES:
        raise ValueError("Chart data exceeds 24000 bytes; reduce the number of points")
    kind = value.get("type")
    if kind not in {"bar", "line", "pie"}:
        raise ValueError("type must be bar, line or pie")
    def text(raw, field, limit, required=False):
        if not isinstance(raw, str) or len(raw) > limit or (required and not raw.strip()):
            raise ValueError(f"{field} must be {'a non-empty' if required else 'a'} string up to {limit} characters")
        if any(ord(c) < 32 for c in raw):
            raise ValueError(f"{field} cannot contain control characters")
        return raw.strip()
    title = text(value.get("title"), "title", 160, True)
    labels = value.get("labels")
    if not isinstance(labels, list) or not 1 <= len(labels) <= MAX_LABELS:
        raise ValueError("labels must contain 1 to 40 category names")
    labels = [text(v, "label", 100, True) for v in labels]
    if len(set(labels)) != len(labels):
        raise ValueError("Category labels must be unique")
    series = value.get("series")
    if not isinstance(series, list) or not 1 <= len(series) <= MAX_SERIES:
        raise ValueError("series must contain 1 to 6 named series")
    normalized = []
    for item in series:
        if not isinstance(item, dict) or set(item) != {"name", "values"}:
            raise ValueError("Each series needs exactly name and values")
        name = text(item["name"], "series.name", 100, True)
        values = item["values"]
        if not isinstance(values, list) or len(values) != len(labels):
            raise ValueError("Each series needs one value for every label")
        if any(isinstance(v, bool) or not isinstance(v, (int, float)) or abs(v) > 1e12 or not math.isfinite(v) for v in values):
            raise ValueError("Values must be finite numbers between -1e12 and 1e12, without nulls or booleans")
        normalized.append({"name": name, "values": values[:]})
    if len({s["name"] for s in normalized}) != len(normalized):
        raise ValueError("Series names must be unique")
    if kind == "pie" and (len(normalized) != 1 or any(v < 0 for v in normalized[0]["values"]) or not any(normalized[0]["values"])):
        raise ValueError("Pie charts need one series with non-negative values and a positive total")
    result = {"type": kind, "title": title, "labels": labels, "series": normalized,
              "unit": text(value.get("unit", ""), "unit", 40), "source": text(value.get("source", ""), "source", 500)}
    for bound in ("y_min", "y_max"):
        if bound in value:
            v = value[bound]
            if isinstance(v, bool) or not isinstance(v, (int, float)) or abs(v) > 1e12 or not math.isfinite(v):
                raise ValueError(bound + " must be a finite number between -1e12 and 1e12")
            result[bound] = v
    values = [v for s in normalized for v in s["values"]]
    low = result.get("y_min", min(0, min(values)))
    high = result.get("y_max", max(0, max(values)))
    if low > min(values) or high < max(values) or ("y_min" in result and "y_max" in result and low >= high):
        raise ValueError("Axis bounds must include every value and y_min must be smaller than y_max")
    if kind == "bar" and (low > 0 or high < 0):
        raise ValueError("Bar chart bounds must include zero")
    chart_id = value.get("chart_id")
    if chart_id is not None:
        if not isinstance(chart_id, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", chart_id):
            raise ValueError("chart_id must contain 1 to 64 letters, digits, underscores or hyphens")
        result["chart_id"] = chart_id
    return result


def render_chart(arguments, **kwargs):
    try:
        chart = validate_chart(arguments)
    except (ValueError, TypeError, OverflowError) as exc:
        return tool_error(str(exc), reason_code="invalid_chart")
    revision = hashlib.sha256(json.dumps(chart, sort_keys=True, ensure_ascii=True).encode()).hexdigest()[:16]
    return tool_result(ok=True, kind="igorek.chart", schema_version=1,
                       chart_id=chart.get("chart_id") or uuid.uuid4().hex, revision=revision)


SCHEMA = {"name": "render_chart", "description": "Display an inline chart when it helps explain numeric comparisons, proportions or trends. Supply actual known numeric data, labels and named series. Types: bar (comparisons), line (ordered trends), pie (one non-negative series). Provide units and a source when known. Return metadata contains chart_id; reuse it to update the same chart. The application stores the input with tool history and renders it on desktop/mobile. Do not write HTML, scripts or repeat the raw JSON in the final answer.",
    "input_schema": {"type": "object", "properties": {
        "type": {"type": "string", "enum": ["bar", "line", "pie"]},
        "title": {"type": "string", "minLength": 1, "maxLength": 160},
        "labels": {"type": "array", "minItems": 1, "maxItems": 40, "items": {"type": "string", "minLength": 1, "maxLength": 100}},
        "series": {"type": "array", "minItems": 1, "maxItems": 6, "items": {"type": "object", "properties": {"name": {"type": "string", "minLength": 1, "maxLength": 100}, "values": {"type": "array", "items": {"type": "number", "minimum": -1e12, "maximum": 1e12}, "minItems": 1, "maxItems": 40}}, "required": ["name", "values"], "additionalProperties": False}},
        "unit": {"type": "string", "maxLength": 40}, "source": {"type": "string", "maxLength": 500},
        "chart_id": {"type": "string", "pattern": "^[A-Za-z0-9_-]{1,64}$"},
        "y_min": {"type": "number"}, "y_max": {"type": "number"}},
        "required": ["type", "title", "labels", "series"], "additionalProperties": False}}

registry.register(name="render_chart", toolset="session", schema=SCHEMA, handler=render_chart)
