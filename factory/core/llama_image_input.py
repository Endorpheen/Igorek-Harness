"""Gate images using the capabilities of a loopback llama.cpp server."""
from __future__ import annotations

import http.client
import json
import ssl
from urllib.parse import urlencode, urlsplit

from .local_model_context import is_loopback_model_url
from .prompt_assembly import image_input_unavailable_notice


def _image_parts(content):
    if not isinstance(content, list):
        return False
    return any(
        isinstance(part, dict) and (
            part.get("type") in ("image", "image_url", "input_image")
            or (part.get("type") == "tool_result" and _image_parts(part.get("content")))
        ) for part in content
    )


def _has_images(payload):
    return any(isinstance(message, dict) and _image_parts(message.get("content"))
               for message in payload.get("messages", []))


def _text_content(content):
    if not isinstance(content, list):
        return content
    result = []
    for part in content:
        if isinstance(part, dict) and part.get("type") in ("image", "image_url", "input_image"):
            result.append({"type": "text", "text": image_input_unavailable_notice()})
        elif isinstance(part, dict) and part.get("type") == "tool_result":
            result.append({**part, "content": _text_content(part.get("content"))})
        else:
            result.append(part)
    return result


def _without_images(payload):
    return {**payload, "messages": [
        {**message, "content": _text_content(message.get("content"))}
        if isinstance(message, dict) and "content" in message else message
        for message in payload["messages"]
    ]}


class LlamaImageInput:
    """Keep capability observations within one task and one local endpoint."""

    def __init__(self, base_url, model, headers, *, enabled=True):
        self.enabled = enabled and is_loopback_model_url(base_url)
        self.target = urlsplit(base_url) if self.enabled else None
        self.model = model
        self.headers = {key: value for key, value in headers.items()
                        if key.lower() in {"authorization", "x-api-key"}}
        self.checked = False
        self.supported = None

    def _get(self, suffix):
        target = self.target
        path = target.path.rstrip("/")
        if path.endswith("/v1"):
            path = path[:-3].rstrip("/")
        connection = None
        try:
            connection = (http.client.HTTPSConnection(target.hostname, target.port, timeout=.5,
                          context=ssl.create_default_context()) if target.scheme == "https"
                          else http.client.HTTPConnection(target.hostname, target.port, timeout=.5))
            connection.request("GET", path + suffix, headers={**self.headers, "Accept": "application/json"})
            response = connection.getresponse()
            if response.status != 200:
                return None
            raw = response.read(1048577)
            if len(raw) > 1048576:
                return None
            payload = json.loads(raw)
            return payload if isinstance(payload, dict) else None
        except (OSError, http.client.HTTPException, ValueError, UnicodeError):
            return None
        finally:
            if connection is not None:
                connection.close()

    def _probe(self):
        props = self._get("/props?" + urlencode({"model": self.model}))
        if isinstance(props, dict) and isinstance(props.get("build_info"), str) and props["build_info"]:
            modalities = props.get("modalities")
            alias = props.get("model_alias")
            same_model = alias is None or alias == "" or alias == self.model
            if same_model and isinstance(modalities, dict) and type(modalities.get("vision")) is bool:
                return modalities["vision"]
        models = self._get("/v1/models")
        if not isinstance(models, dict) or not isinstance(models.get("data"), list):
            return None
        for item in models["data"]:
            if not isinstance(item, dict) or item.get("owned_by") != "llamacpp":
                continue
            aliases = item.get("aliases")
            aliases = aliases if isinstance(aliases, list) else []
            if item.get("id") != self.model and self.model not in aliases:
                continue
            architecture = item.get("architecture")
            modalities = architecture.get("input_modalities") if isinstance(architecture, dict) else None
            if isinstance(modalities, list) and "text" in modalities and all(isinstance(x, str) for x in modalities):
                return "image" in modalities
        return None

    def prepare(self, payload):
        if not self.enabled or not _has_images(payload):
            return payload
        if not self.checked:
            self.supported = self._probe()
            self.checked = True
        return _without_images(payload) if self.supported is False else payload

    def recover(self, payload, body):
        if not self.enabled or not _has_images(payload) or not isinstance(body, dict):
            return None
        error = body.get("error")
        if not isinstance(error, str) or not error.startswith(("HTTP 400:", "HTTP 500:", "HTTP 501:")):
            return None
        if "image input is not supported" not in error or "mmproj" not in error:
            return None
        self.checked = True
        self.supported = False
        return _without_images(payload)
