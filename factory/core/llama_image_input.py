"""Gate images using the capabilities of a loopback llama.cpp server."""
from __future__ import annotations

from .model_backend_capabilities import LlamaServerCapabilities
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


class LlamaImageInput(LlamaServerCapabilities):
    """Apply image policy using the same task-scoped backend capability source."""

    def prepare(self, payload):
        if not self.enabled or not _has_images(payload):
            return payload
        self.resolve()
        return _without_images(payload) if self.supported is False else payload

    def recover(self, payload, body):
        if not self.enabled or not _has_images(payload) or not isinstance(body, dict):
            return None
        error = body.get("error")
        if not isinstance(error, str) or not error.startswith(("HTTP 400:", "HTTP 500:", "HTTP 501:")):
            return None
        if "image input is not supported" not in error or "mmproj" not in error:
            return None
        if self.resolve().backend != "llama_cpp":
            return None
        self.supported = False
        return _without_images(payload)
