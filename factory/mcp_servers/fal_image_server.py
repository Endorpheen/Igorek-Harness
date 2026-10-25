#!/usr/bin/env python3
from __future__ import annotations

import base64
import json
import mimetypes
import os
import re
import sys
import time
from pathlib import Path
from typing import Any
from urllib import error as urllib_error
from urllib import request as urllib_request


SERVER_NAME = "FalImage"
SERVER_VERSION = "0.1.1"
DEFAULT_MODEL = "openai/gpt-image-2"
DEFAULT_IMAGE_SIZE = "landscape_4_3"
DEFAULT_QUALITY = "low"
DEFAULT_OUTPUT_FORMAT = "png"
DEFAULT_NUM_IMAGES = 1
DEFAULT_SYNC_MODE = False
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
MAX_MEDIA_BYTES = 32 * 1024 * 1024

ALLOWED_IMAGE_SIZE_PRESETS = {
    "square_hd",
    "square",
    "portrait_4_3",
    "portrait_16_9",
    "landscape_4_3",
    "landscape_16_9",
    "auto",
}
ALLOWED_QUALITY = {"low", "medium", "high"}
ALLOWED_OUTPUT_FORMATS = {"png", "jpeg", "webp"}


def _load_dotenv() -> None:
    base_dir = Path(__file__).resolve().parents[1]
    for env_path in (
        base_dir / ".env.gateway",
        base_dir / ".env",
        base_dir / ".env.local",
    ):
        if not env_path.exists():
            continue
        for raw_line in env_path.read_text(encoding="utf-8").splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            os.environ.setdefault(key.strip(), value.strip().strip("'").strip('"'))


def _default_model() -> str:
    return (os.getenv("FAL_IMAGE_MODEL") or DEFAULT_MODEL).strip() or DEFAULT_MODEL


def _default_quality() -> str:
    value = (os.getenv("FAL_IMAGE_QUALITY") or DEFAULT_QUALITY).strip().lower()
    return value if value in ALLOWED_QUALITY else DEFAULT_QUALITY


def _default_image_size() -> str:
    value = (
        os.getenv("FAL_IMAGE_SIZE")
        or os.getenv("FAL_IMAGE_IMAGE_SIZE")
        or DEFAULT_IMAGE_SIZE
    )
    return value.strip() or DEFAULT_IMAGE_SIZE


def _default_output_format() -> str:
    value = (
        os.getenv("FAL_IMAGE_OUTPUT_FORMAT") or DEFAULT_OUTPUT_FORMAT
    ).strip().lower()
    return value if value in ALLOWED_OUTPUT_FORMATS else DEFAULT_OUTPUT_FORMAT


def _default_num_images() -> int:
    try:
        return int(os.getenv("FAL_IMAGE_NUM_IMAGES") or DEFAULT_NUM_IMAGES)
    except (TypeError, ValueError):
        return DEFAULT_NUM_IMAGES


def _parse_bool(value: Any, *, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    return default


def _default_sync_mode() -> bool:
    return _parse_bool(os.getenv("FAL_IMAGE_SYNC_MODE"), default=DEFAULT_SYNC_MODE)


def _build_proxy_handler() -> urllib_request.BaseHandler:
    # Direct connection - no proxy
    return urllib_request.ProxyHandler({})


def _send(message: dict[str, Any]) -> None:
    sys.stdout.write(json.dumps(message, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def _make_result(request_id: Any, result: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _make_error(request_id: Any, code: int, message: str) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "error": {"code": code, "message": message},
    }


def _tool_definitions() -> list[dict[str, Any]]:
    return [
        {
            "name": "fal_generate_image",
            "description": (
                "Генерирует изображение через fal.ai, сохраняет его локально и возвращает путь к файлу. "
                "Подходит для Telegram attachment flow и локальной работы в workspace."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "prompt": {
                        "type": "string",
                        "description": "Текстовый prompt для генерации изображения.",
                    },
                    "model": {
                        "type": "string",
                        "description": f"fal.ai model id. По умолчанию {_default_model()}.",
                    },
                    "output_dir": {
                        "type": "string",
                        "description": "Куда сохранить итоговый файл. По умолчанию текущая рабочая директория процесса.",
                    },
                    "filename": {
                        "type": "string",
                        "description": "Имя итогового файла. Если не задано, имя будет сгенерировано автоматически.",
                    },
                    "image_size": {
                        "oneOf": [
                            {"type": "string"},
                            {
                                "type": "object",
                                "properties": {
                                    "width": {"type": "integer"},
                                    "height": {"type": "integer"},
                                },
                                "required": ["width", "height"],
                                "additionalProperties": False,
                            },
                        ],
                        "description": (
                            "Размер изображения. По умолчанию landscape_4_3 "
                            "(1024x768). Можно передать preset или {width,height}."
                        ),
                    },
                    "quality": {
                        "type": "string",
                        "enum": sorted(ALLOWED_QUALITY),
                        "description": "Качество генерации. По умолчанию low.",
                    },
                    "num_images": {
                        "type": "integer",
                        "description": "Сколько изображений запросить. По умолчанию 1.",
                    },
                    "output_format": {
                        "type": "string",
                        "enum": sorted(ALLOWED_OUTPUT_FORMATS),
                        "description": "Формат результата. По умолчанию png.",
                    },
                    "sync_mode": {
                        "type": "boolean",
                        "description": "fal.ai sync_mode. По умолчанию false.",
                    },
                },
                "required": ["prompt"],
                "additionalProperties": False,
            },
        },
        {
            "name": "fal_edit_image",
            "description": (
                "Редактирует существующее изображение через fal.ai openai/gpt-image-2/edit endpoint. "
                "Принимает локальный файл изображения + prompt описания изменений. "
                "Поддерживает inpainting с маской или простое редактирование без маски."
            ),
            "inputSchema": {
                "type": "object",
                "properties": {
                    "image_path": {
                        "type": "string",
                        "description": "Путь к локальному изображению для редактирования.",
                    },
                    "prompt": {
                        "type": "string",
                        "description": "Описание изменений, которые нужно внести.",
                    },
                    "mask_path": {
                        "type": "string",
                        "description": "Опциональный путь к маске (чёрно-белое изображение). Белые области будут изменены.",
                    },
                    "output_dir": {
                        "type": "string",
                        "description": "Куда сохранить итоговый файл. По умолчанию текущая рабочая директория.",
                    },
                    "filename": {
                        "type": "string",
                        "description": "Имя итогового файла. Если не задано, сгенерируется автоматически.",
                    },
                    "quality": {
                        "type": "string",
                        "enum": sorted(ALLOWED_QUALITY),
                        "description": "Качество редактирования. По умолчанию low.",
                    },
                    "output_format": {
                        "type": "string",
                        "enum": sorted(ALLOWED_OUTPUT_FORMATS),
                        "description": "Формат результата. По умолчанию png.",
                    },
                },
                "required": ["image_path", "prompt"],
                "additionalProperties": False,
            },
        }
    ]


def _slugify(text: str, *, fallback: str = "fal-image") -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", text.strip().lower()).strip("-")
    return slug[:48] or fallback


def _request_json(url: str, payload: dict[str, Any]) -> dict[str, Any]:
    api_key = (os.getenv("FAL_KEY") or os.getenv("FAL_API_KEY") or "").strip()
    if not api_key:
        raise RuntimeError("FAL_KEY или FAL_API_KEY не задан в окружении или .env")
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib_request.Request(
        url,
        data=body,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Key {api_key}",
        },
        method="POST",
    )
    opener = urllib_request.build_opener(_build_proxy_handler())
    try:
        with opener.open(req, timeout=180) as resp:
            data = resp.read(MAX_RESPONSE_BYTES + 1)
            if len(data) > MAX_RESPONSE_BYTES:
                raise RuntimeError('fal response exceeds the byte limit')
            return json.loads(data.decode("utf-8") or "{}")
    except urllib_error.HTTPError as exc:
        with exc:
            details = exc.read(MAX_RESPONSE_BYTES).decode("utf-8", errors="replace")
        raise RuntimeError(f"fal.ai HTTP {exc.code}: {details}") from exc
    except urllib_error.URLError as exc:
        raise RuntimeError(f"fal.ai unavailable: {exc.reason}") from exc


def _download_file(url: str, target_path: Path) -> None:
    req = urllib_request.Request(url, method="GET")
    opener = urllib_request.build_opener(_build_proxy_handler())
    try:
        with opener.open(req, timeout=180) as resp:
            payload = resp.read(MAX_MEDIA_BYTES + 1)
            if len(payload) > MAX_MEDIA_BYTES:
                raise RuntimeError('fal media exceeds the byte limit')
    except urllib_error.HTTPError as exc:
        with exc:
            details = exc.read(MAX_RESPONSE_BYTES).decode("utf-8", errors="replace")
        raise RuntimeError(
            f"fal media download failed: HTTP {exc.code}: {details}"
        ) from exc
    except urllib_error.URLError as exc:
        raise RuntimeError(f"fal media download unavailable: {exc.reason}") from exc
    target_path.parent.mkdir(parents=True, exist_ok=True)
    target_path.write_bytes(payload)


def _pick_image_urls(response: dict[str, Any]) -> list[str]:
    urls: list[str] = []
    # fal.ai generate endpoint uses "images"
    raw_images = response.get("images")
    if isinstance(raw_images, list):
        for item in raw_images:
            if isinstance(item, dict):
                url = str(item.get("url") or "").strip()
                if url:
                    urls.append(url)
                    if len(urls) > 4:
                        raise RuntimeError('fal response exceeds the image-count limit')
    if urls:
        return urls
    # OpenAI-style edit endpoint may use "data"
    raw_data = response.get("data")
    if isinstance(raw_data, list):
        for item in raw_data:
            if isinstance(item, dict):
                url = str(item.get("url") or "").strip()
                if url:
                    urls.append(url)
                    if len(urls) > 4:
                        raise RuntimeError('fal response exceeds the image-count limit')
        if urls:
            return urls
    top_url = str(response.get("image") or response.get("url") or "").strip()
    return [top_url] if top_url else []


def _resolve_output_dir(value: str | None) -> Path:
    if value and value.strip():
        return Path(value).expanduser().resolve()
    return Path.cwd().resolve()


def _resolve_filename(
    prompt: str, requested: str | None, output_format: str, index: int
) -> str:
    suffix_map = {"png": ".png", "jpeg": ".jpg", "webp": ".webp"}
    suffix = suffix_map.get(output_format.lower(), ".png")
    if requested and requested.strip():
        name = requested.strip()
        if index > 1:
            stem = Path(name).stem or "fal-image"
            ext = Path(name).suffix or suffix
            return f"{stem}-{index}{ext}"
        return name
    base = _slugify(prompt)
    timestamp = time.strftime("%Y%m%d-%H%M%S")
    return f"{base}-{timestamp}-{index}{suffix}"


def _normalize_image_size(value: Any) -> str | dict[str, int]:
    if isinstance(value, dict):
        try:
            width = int(value.get("width"))
            height = int(value.get("height"))
        except (TypeError, ValueError) as exc:
            raise RuntimeError(
                "image_size.width и image_size.height должны быть целыми числами"
            ) from exc
        if width <= 0 or height <= 0:
            raise RuntimeError(
                "image_size.width и image_size.height должны быть положительными"
            )
        return {"width": width, "height": height}

    raw = str(value or _default_image_size()).strip()
    if not raw:
        raw = DEFAULT_IMAGE_SIZE
    dimensions = re.fullmatch(r"(\d+)\s*[xX]\s*(\d+)", raw)
    if dimensions:
        return _normalize_image_size({"width": int(dimensions.group(1)), "height": int(dimensions.group(2))})
    if raw not in ALLOWED_IMAGE_SIZE_PRESETS:
        raise RuntimeError(
            "image_size должен быть preset "
            f"{', '.join(sorted(ALLOWED_IMAGE_SIZE_PRESETS))}, WxH или {{width,height}}"
        )
    return raw


def _encode_image_to_data_uri(path: str) -> str:
    file_path = Path(path).expanduser().resolve()
    if not file_path.exists() or not file_path.is_file():
        raise RuntimeError(f"Файл не найден: {path}")
    data = file_path.read_bytes()
    mime = mimetypes.guess_type(str(file_path))[0] or "image/png"
    b64 = base64.b64encode(data).decode("utf-8")
    return f"data:{mime};base64,{b64}"


def _build_fal_payload(arguments: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    prompt = str(arguments.get("prompt") or "").strip()
    if not prompt:
        raise RuntimeError("Поле prompt обязательно")
    model = str(arguments.get("model") or _default_model()).strip() or _default_model()
    image_size = _normalize_image_size(
        arguments.get("image_size") or _default_image_size()
    )

    quality = str(arguments.get("quality") or _default_quality()).strip().lower()
    if quality not in ALLOWED_QUALITY:
        raise RuntimeError("quality должен быть low, medium или high")

    output_format = (
        str(arguments.get("output_format") or _default_output_format()).strip().lower()
        or _default_output_format()
    )
    if output_format not in ALLOWED_OUTPUT_FORMATS:
        raise RuntimeError("output_format должен быть png, jpeg или webp")

    try:
        num_images = int(arguments.get("num_images", _default_num_images()))
    except (TypeError, ValueError) as exc:
        raise RuntimeError("num_images должен быть целым числом") from exc
    num_images = max(1, min(num_images, 4))

    sync_mode = _parse_bool(arguments.get("sync_mode"), default=_default_sync_mode())

    return model, {
        "prompt": prompt,
        "image_size": image_size,
        "quality": quality,
        "num_images": num_images,
        "output_format": output_format,
        "sync_mode": sync_mode,
    }


def _build_edit_payload(arguments: dict[str, Any]) -> dict[str, Any]:
    prompt = str(arguments.get("prompt") or "").strip()
    if not prompt:
        raise RuntimeError("Поле prompt обязательно")
    image_path = str(arguments.get("image_path") or "").strip()
    if not image_path:
        raise RuntimeError("Поле image_path обязательно")

    image_data_uri = _encode_image_to_data_uri(image_path)

    quality = str(arguments.get("quality") or _default_quality()).strip().lower()
    if quality not in ALLOWED_QUALITY:
        raise RuntimeError("quality должен быть low, medium или high")

    output_format = (
        str(arguments.get("output_format") or _default_output_format()).strip().lower()
        or _default_output_format()
    )
    if output_format not in ALLOWED_OUTPUT_FORMATS:
        raise RuntimeError("output_format должен быть png, jpeg или webp")

    sync_mode = _parse_bool(arguments.get("sync_mode"), default=_default_sync_mode())

    payload: dict[str, Any] = {
        "prompt": prompt,
        "image_urls": [image_data_uri],
        "quality": quality,
        "output_format": output_format,
        "sync_mode": sync_mode,
    }

    mask_path = str(arguments.get("mask_path") or "").strip()
    if mask_path:
        payload["mask_image_url"] = _encode_image_to_data_uri(mask_path)

    return payload


def _generate_image(arguments: dict[str, Any]) -> dict[str, Any]:
    model, payload = _build_fal_payload(arguments)
    prompt = str(payload["prompt"])
    output_format = str(payload["output_format"])
    output_dir = _resolve_output_dir(arguments.get("output_dir"))
    requested_filename = str(arguments.get("filename") or "").strip() or None

    response = _request_json(f"https://fal.run/{model}", payload)
    urls = _pick_image_urls(response)
    if not urls:
        raise RuntimeError(
            f"fal.ai не вернул image URL: {json.dumps(response, ensure_ascii=False)[:800]}"
        )

    saved_files: list[str] = []
    for index, url in enumerate(urls, start=1):
        file_name = _resolve_filename(prompt, requested_filename, output_format, index)
        target_path = output_dir / file_name
        _download_file(url, target_path)
        saved_files.append(str(target_path))

    lines = [
        f"Fal model: {model}",
        f"Prompt: {prompt}",
        f"Saved files: {len(saved_files)}",
    ]
    for path in saved_files:
        lines.append(f"- {path}")
    lines.append("")
    lines.append("STATUS: COMPLETE. The task is finished. Do NOT call this tool again.")
    lines.append("Сообщи пользователю путь к файлу и остановись.")

    return {
        "content": [{"type": "text", "text": "\n".join(lines)}],
        "saved_files": saved_files,
        "source_urls": urls,
        "model": model,
        "prompt": prompt,
    }


def _edit_image(arguments: dict[str, Any]) -> dict[str, Any]:
    payload = _build_edit_payload(arguments)
    prompt = str(payload["prompt"])
    output_format = str(payload.get("output_format", _default_output_format()))
    output_dir = _resolve_output_dir(arguments.get("output_dir"))
    requested_filename = str(arguments.get("filename") or "").strip() or None

    response = _request_json("https://fal.run/openai/gpt-image-2/edit", payload)
    urls = _pick_image_urls(response)
    if not urls:
        raise RuntimeError(
            f"fal.ai edit endpoint не вернул image URL: {json.dumps(response, ensure_ascii=False)[:800]}"
        )

    saved_files: list[str] = []
    for index, url in enumerate(urls, start=1):
        file_name = _resolve_filename(prompt, requested_filename, output_format, index)
        target_path = output_dir / file_name
        _download_file(url, target_path)
        saved_files.append(str(target_path))

    lines = [
        "Fal model: openai/gpt-image-2/edit",
        f"Prompt: {prompt}",
        f"Saved files: {len(saved_files)}",
    ]
    for path in saved_files:
        lines.append(f"- {path}")
    lines.append("")
    lines.append("STATUS: COMPLETE. The task is finished. Do NOT call this tool again.")
    lines.append("Сообщи пользователю путь к файлу и остановись.")

    return {
        "content": [{"type": "text", "text": "\n".join(lines)}],
        "saved_files": saved_files,
        "source_urls": urls,
        "model": "openai/gpt-image-2/edit",
        "prompt": prompt,
    }


def main() -> int:
    _load_dotenv()
    for raw_line in sys.stdin:
        line = raw_line.strip()
        if not line:
            continue
        request = None
        try:
            request = json.loads(line)
            method = request.get("method")
            request_id = request.get("id")
            params = request.get("params") or {}

            if method == "initialize":
                _send(
                    _make_result(
                        request_id,
                        {
                            "protocolVersion": "2024-11-05",
                            "capabilities": {"tools": {}},
                            "serverInfo": {
                                "name": SERVER_NAME,
                                "version": SERVER_VERSION,
                            },
                        },
                    )
                )
                continue

            if method == "notifications/initialized":
                continue

            if method == "ping":
                _send(_make_result(request_id, {}))
                continue

            if method == "tools/list":
                _send(_make_result(request_id, {"tools": _tool_definitions()}))
                continue

            if method == "tools/call":
                tool_name = params.get("name")
                arguments = params.get("arguments") or {}
                if tool_name == "fal_generate_image":
                    _send(_make_result(request_id, _generate_image(arguments)))
                    continue
                if tool_name == "fal_edit_image":
                    _send(_make_result(request_id, _edit_image(arguments)))
                    continue
                _send(
                    _make_error(
                        request_id, -32602, f"Неизвестный инструмент: {tool_name}"
                    )
                )
                continue

            _send(_make_error(request_id, -32601, f"Метод не поддерживается: {method}"))
        except Exception as exc:
            _send(
                _make_error(
                    request.get("id") if isinstance(request, dict) else None,
                    -32000,
                    str(exc),
                )
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
