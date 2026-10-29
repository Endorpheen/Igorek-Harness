"""Task-scoped backend identity and capabilities from model-bound metadata."""
from __future__ import annotations

from dataclasses import dataclass
import http.client
import json
import ssl
from urllib.parse import urlencode, urlsplit

from .local_model_context import is_loopback_model_url


def _endpoint_key(url):
    if not is_loopback_model_url(url):
        return None
    target = urlsplit(url)
    path = target.path.rstrip("/")
    for suffix in ("/chat/completions", "/messages", "/v1"):
        if path.endswith(suffix):
            path = path[:-len(suffix)]
    return target.scheme, target.hostname, target.port or (443 if target.scheme == "https" else 80), path


@dataclass(frozen=True)
class ModelBackendCapabilities:
    backend: str = "unknown"
    vision: bool | None = None
    base_url: str = ""
    model: str = ""

    def matches(self, base_url, model):
        key = _endpoint_key(self.base_url)
        return bool(key and key == _endpoint_key(base_url) and self.model and self.model == model)


class LlamaServerCapabilities:
    """Resolve once per task; unknown metadata never opts a proxy into repairs."""

    def __init__(self, base_url, model, headers, *, enabled=True):
        self.enabled = enabled and is_loopback_model_url(base_url)
        self.base_url = base_url
        self.target = urlsplit(base_url) if self.enabled else None
        self.model = model
        self.headers = {key: value for key, value in headers.items()
                        if key.lower() in {"authorization", "x-api-key"}}
        self.checked = False
        self.supported = None
        self.backend = "unknown"

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
        models = self._get("/v1/models")
        rows = models.get("data") if isinstance(models, dict) else None
        matching = []
        if isinstance(rows, list):
            matching = [row for row in rows if isinstance(row, dict) and (
                row.get("id") == self.model or (
                    isinstance(row.get("aliases"), list) and self.model in row["aliases"]
                )
            )]
        accepted_aliases = {self.model}
        if matching:
            # A selected cloud model cannot inherit server-wide llama props.
            if len(matching) != 1 or matching[0].get("owned_by") != "llamacpp":
                return None
            item = matching[0]
            self.backend = "llama_cpp"
            accepted_aliases.update(value for value in [item.get("id"), *(
                item.get("aliases") if isinstance(item.get("aliases"), list) else []
            )] if isinstance(value, str))
            architecture = item.get("architecture")
            modalities = architecture.get("input_modalities") if isinstance(architecture, dict) else None
            if isinstance(modalities, list) and "text" in modalities and all(isinstance(x, str) for x in modalities):
                return "image" in modalities
        elif isinstance(rows, list) and rows:
            return None
        props = self._get("/props?" + urlencode({"model": self.model}))
        if not isinstance(props, dict) or not isinstance(props.get("build_info"), str) or not props["build_info"]:
            return None
        alias = props.get("model_alias")
        if not isinstance(alias, str) or not alias or alias not in accepted_aliases:
            return None
        modalities = props.get("modalities")
        if isinstance(modalities, dict) and type(modalities.get("vision")) is bool:
            self.backend = "llama_cpp"
            return modalities["vision"]
        return None

    def resolve(self):
        if not self.checked:
            if self.enabled and isinstance(self.model, str) and self.model:
                self.supported = self._probe()
            self.checked = True
        return ModelBackendCapabilities(self.backend, self.supported, self.base_url, self.model)
