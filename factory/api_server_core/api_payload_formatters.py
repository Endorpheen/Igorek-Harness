import re
from typing import Any


def normalize_bash_payload_text(text: str) -> str:
    """Normalize untrusted output text without dereferencing embedded host paths."""
    cleaned = (text or "").strip()
    preview_marker = "Preview (first 2KB):"
    if preview_marker in cleaned:
        cleaned = cleaned.split(preview_marker, 1)[1].strip()
    cleaned = re.sub(r"(?im)^Output too large.*$", " ", cleaned)
    cleaned = re.sub(r"(?im)^Full output saved to:.*$", " ", cleaned)
    cleaned = re.sub(r"(?im)^Preview \(first .*?\):$", " ", cleaned)
    cleaned = re.sub(r"/tmp/[^\s|]+", " ", cleaned)
    return cleaned.strip()


def format_compact_api_payload(payload: dict[str, Any]) -> list[str]:
    compact_price_lines = _format_compact_price_payload(payload)
    if compact_price_lines:
        return compact_price_lines

    compact_weather_lines = _format_compact_weather_payload(payload)
    if compact_weather_lines:
        return compact_weather_lines

    return []


def _format_compact_price_payload(payload: dict[str, Any]) -> list[str]:
    lines: list[str] = []
    for asset, quote_map in payload.items():
        if not isinstance(asset, str) or not isinstance(quote_map, dict) or not quote_map:
            return []
        for currency, value in quote_map.items():
            if not isinstance(currency, str) or not isinstance(value, (int, float)):
                return []
            lines.append(f"`{asset.upper()}`: `{value}` `{currency.upper()}`")
    return lines


def _format_compact_weather_payload(payload: dict[str, Any]) -> list[str]:
    current = payload.get("current_condition")
    if not isinstance(current, list) or len(current) != 1 or not isinstance(current[0], dict):
        return []

    item = current[0]
    temp_c = item.get("temp_C")
    feels_like_c = item.get("FeelsLikeC")
    humidity = item.get("humidity")
    wind_kmph = item.get("windspeedKmph")
    desc_list = item.get("weatherDesc")
    if not all(isinstance(value, str) and value for value in (temp_c, feels_like_c, humidity, wind_kmph)):
        return []
    if not isinstance(desc_list, list) or len(desc_list) != 1 or not isinstance(desc_list[0], dict):
        return []
    description = desc_list[0].get("value")
    if not isinstance(description, str) or not description:
        return []

    return [
        f"Сейчас: `{temp_c}` `°C`",
        f"Ощущается как: `{feels_like_c}` `°C`",
        f"Погодные условия: `{description}`",
        f"Влажность: `{humidity}` `%`",
        f"Ветер: `{wind_kmph}` `км/ч`",
    ]
