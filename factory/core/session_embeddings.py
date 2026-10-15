from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

DEFAULT_EMBEDDING_MODEL = "jina-embeddings-v5-text-nano-retrieval"
EMBEDDINGS_BASE_URL_ENV = "IGOREK_SESSION_EMBEDDINGS_BASE_URL"
EMBEDDINGS_MODEL_ENV = "IGOREK_SESSION_EMBEDDINGS_MODEL"
EMBEDDINGS_API_KEY_ENV = "IGOREK_SESSION_EMBEDDINGS_API_KEY"


@dataclass(frozen=True)
class EmbeddingBatch:
    model: str
    vectors: list[list[float]]
    dimensions: int


class LocalSessionEmbedder:
    """Local embeddings client for SessionSearch semantic retrieval.

    Uses an OpenAI-compatible local `/v1/embeddings` endpoint so the embedding
    runtime can be swapped independently of SessionSearch itself.
    """

    def __init__(
        self,
        *,
        base_url: str | None = None,
        model: str | None = None,
        api_key: str | None = None,
        timeout_s: float = 30.0,
    ) -> None:
        raw_base = (base_url or os.environ.get(EMBEDDINGS_BASE_URL_ENV) or "").strip()
        self.base_url = raw_base.rstrip("/")
        self.model = str(model or os.environ.get(EMBEDDINGS_MODEL_ENV) or DEFAULT_EMBEDDING_MODEL).strip()
        self.api_key = str(api_key or os.environ.get(EMBEDDINGS_API_KEY_ENV) or "dummy").strip() or "dummy"
        self.timeout_s = max(5.0, float(timeout_s or 30.0))

    @property
    def available(self) -> bool:
        return bool(self.base_url and self.model)

    def embed_texts(self, texts: list[str]) -> EmbeddingBatch | None:
        normalized = [str(item or "").strip() for item in texts if str(item or "").strip()]
        if not normalized or not self.available:
            return None
        payload = {
            "model": self.model,
            "input": normalized,
            "encoding_format": "float",
        }
        url = f"{self.base_url}/v1/embeddings"
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(
            url,
            data=data,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_s) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, json.JSONDecodeError):
            return None
        data_items = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(data_items, list):
            return None
        vectors: list[list[float]] = []
        for item in data_items:
            if not isinstance(item, dict):
                return None
            vector = item.get("embedding")
            if not isinstance(vector, list) or not vector:
                return None
            try:
                vectors.append([float(value) for value in vector])
            except Exception:
                return None
        dimensions = len(vectors[0]) if vectors else 0
        if dimensions <= 0:
            return None
        return EmbeddingBatch(
            model=str(payload.get("model") or self.model),
            vectors=vectors,
            dimensions=dimensions,
        )

    def embed_text(self, text: str) -> tuple[str, list[float]] | None:
        batch = self.embed_texts([text])
        if batch is None or not batch.vectors:
            return None
        return batch.model, batch.vectors[0]
