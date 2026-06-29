"""
PCG embedding client — routes through the homelab AI Gateway (port 8777).

Used to populate the `embedding` field on PCG-owned nodes (Preference,
Observation, CommunicationStyle, Insight) so Neo4j 5 native vector indexes
declared in `migrations.py` actually have content to retrieve against.

Design:
  - Primary path: POST {AI_GATEWAY_URL}/v1/embeddings (OpenAI-compatible).
  - Fallback: return None (caller decides whether to proceed without an
    embedding). We deliberately avoid importing sentence-transformers
    locally — heavy dep, and ChromaDB already handles local fallback for
    the document-chunks collection.
  - Dimension mismatch: if the gateway returns a vector whose length != the
    configured `embedding_dims`, we log a warning and return None. The
    vector indexes are created with a fixed dim; mismatched writes would
    silently fail to index.
"""

from __future__ import annotations

import logging
from typing import List, Optional

import aiohttp

from config import get_settings

log = logging.getLogger(__name__)

_TIMEOUT = aiohttp.ClientTimeout(total=10)


class Embedder:
    """Thin async client for AI-Gateway-backed embeddings."""

    def __init__(self, settings=None):
        self.settings = settings or get_settings()
        self._base_url = self.settings.ai_gateway_url.rstrip("/")
        # Accept both "http://host:port" and "http://host:port/api/v1". The
        # /v1/embeddings OpenAI-compat path is universal; normalize by
        # stripping a trailing /api/v1 if present, then appending /v1.
        if self._base_url.endswith("/api/v1"):
            self._base_url = self._base_url[: -len("/api/v1")]
        self._endpoint = f"{self._base_url}/v1/embeddings"
        self._model = self.settings.embedding_model
        self._dims = int(self.settings.embedding_dims)
        self._api_key = self.settings.ai_gateway_api_key
        self._healthy: Optional[bool] = None  # tri-state: None=unknown

    @property
    def endpoint(self) -> str:
        return self._endpoint

    @property
    def dims(self) -> int:
        return self._dims

    @property
    def is_known_healthy(self) -> bool:
        """True only after at least one successful call. Used by callers
        that want to skip the round-trip when the gateway is known-down."""
        return self._healthy is True

    def _headers(self) -> dict:
        h = {"Content-Type": "application/json"}
        if self._api_key:
            h["Authorization"] = f"Bearer {self._api_key}"
        return h

    async def embed(self, text: str) -> Optional[List[float]]:
        """Embed a single string. Returns None on any failure."""
        if not text or not text.strip():
            return None
        vecs = await self.embed_batch([text])
        return vecs[0] if vecs else None

    async def embed_batch(
        self, texts: List[str]
    ) -> List[Optional[List[float]]]:
        """Embed a batch. Returns a list aligned with inputs; entries that
        failed are None. Batches larger than 96 are chunked (common gateway
        limit)."""
        if not texts:
            return []
        out: List[Optional[List[float]]] = []
        batch_size = 96
        for i in range(0, len(texts), batch_size):
            chunk = texts[i : i + batch_size]
            out.extend(await self._embed_one_call(chunk))
        return out

    async def _embed_one_call(
        self, texts: List[str]
    ) -> List[Optional[List[float]]]:
        """Single HTTP call. Handles dim validation and fallback-to-None."""
        body = {"model": self._model, "input": texts}
        try:
            async with aiohttp.ClientSession(timeout=_TIMEOUT) as session:
                async with session.post(
                    self._endpoint,
                    headers=self._headers(),
                    json=body,
                ) as resp:
                    if resp.status != 200:
                        text = await resp.text()
                        log.warning(
                            "embed: gateway HTTP %d: %s",
                            resp.status,
                            text[:200],
                        )
                        self._healthy = False
                        return [None] * len(texts)
                    data = await resp.json()
        except Exception as e:  # noqa: BLE001
            log.warning("embed: gateway unreachable: %s", e)
            self._healthy = False
            return [None] * len(texts)

        items = data.get("data") or []
        if len(items) != len(texts):
            log.warning(
                "embed: gateway returned %d vectors for %d inputs",
                len(items),
                len(texts),
            )
            self._healthy = False
            return [None] * len(texts)

        result: List[Optional[List[float]]] = []
        for item in items:
            vec = item.get("embedding")
            if not isinstance(vec, list):
                result.append(None)
                continue
            if len(vec) != self._dims:
                log.warning(
                    "embed: dim mismatch (got %d, expected %d) — dropping. "
                    "Check EMBEDDING_MODEL / EMBEDDING_DIMS in .env.",
                    len(vec),
                    self._dims,
                )
                result.append(None)
                continue
            result.append([float(x) for x in vec])

        if any(v is not None for v in result):
            self._healthy = True
        return result


# Module-level singleton. Connects lazily on first call.
_embedder: Optional[Embedder] = None


def get_embedder() -> Embedder:
    global _embedder
    if _embedder is None:
        _embedder = Embedder()
    return _embedder
