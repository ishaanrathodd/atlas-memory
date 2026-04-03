from __future__ import annotations

import os
from typing import Protocol

import httpx

from memory.config import MemoryConfig


class EmbeddingProvider(Protocol):
    async def embed_text(self, text: str) -> list[float]: ...

    async def embed_texts(self, texts: list[str]) -> list[list[float]]: ...


def truncate_embedding(vector: list[float], dimensions: int = 512) -> list[float]:
    if not isinstance(vector, list):
        raise ValueError("Embedding vector must be one-dimensional.")
    if len(vector) < dimensions:
        raise ValueError(f"Embedding vector has {len(vector)} dimensions, expected at least {dimensions}.")
    return [float(item) for item in vector[:dimensions]]


class OpenAIEmbeddingProvider:
    """Uses OpenAI text-embedding-3-small and truncates vectors to 512 dimensions."""

    def __init__(
        self,
        api_key: str | None = None,
        model: str | None = None,
        dimensions: int | None = None,
        base_url: str | None = None,
        http_client: httpx.AsyncClient | None = None,
    ) -> None:
        config = MemoryConfig.from_env()
        self.api_key = api_key or config.openai_api_key or os.getenv("OPENAI_API_KEY")
        self.model = model or config.embedding_model
        self.dimensions = dimensions if dimensions is not None else config.embedding_dimensions
        self.base_url = (base_url or config.openai_base_url).rstrip("/")
        self._http_client = http_client or httpx.AsyncClient(timeout=30.0)
        self._owns_client = http_client is None

    async def embed_text(self, text: str) -> list[float]:
        embeddings = await self.embed_texts([text])
        return embeddings[0]

    async def embed_texts(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        if not self.api_key:
            raise ValueError("OPENAI_API_KEY or MEMORY_OPENAI_API_KEY must be set for OpenAI embeddings.")
        response = await self._http_client.post(
            f"{self.base_url}/embeddings",
            headers={
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json",
            },
            json={"input": texts, "model": self.model},
        )
        response.raise_for_status()
        payload = response.json()
        data = sorted(payload["data"], key=lambda item: item["index"])
        return [truncate_embedding(item["embedding"], self.dimensions) for item in data]

    async def aclose(self) -> None:
        if self._owns_client:
            await self._http_client.aclose()


class MockEmbeddingProvider:
    """Returns deterministic zero vectors for tests."""

    def __init__(self, dimensions: int = 512) -> None:
        self.dimensions = dimensions

    async def embed_text(self, text: str) -> list[float]:
        return [0.0] * self.dimensions

    async def embed_texts(self, texts: list[str]) -> list[list[float]]:
        return [[0.0] * self.dimensions for _ in texts]
