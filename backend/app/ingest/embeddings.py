import hashlib
import re
from functools import lru_cache
from typing import Protocol

import numpy as np

from app.config import get_settings


class Embedder(Protocol):
    model_name: str
    dim: int

    def embed_documents(self, texts: list[str]) -> list[list[float]]: ...

    def embed_query(self, text: str) -> list[float]: ...


class AzureEmbedder:
    """Embeddings from an Azure OpenAI embedding deployment (text-embedding-3-small)."""

    def __init__(self, deployment: str, dim: int):
        from app.llm.azure_client import make_azure_client

        self.model_name = deployment
        self.dim = dim
        self._client = make_azure_client()

    def _embed(self, texts: list[str]) -> list[list[float]]:
        import openai

        from app.llm.base import LLMError

        try:
            resp = self._client.embeddings.create(model=self.model_name, input=texts, dimensions=self.dim)
        except openai.APIStatusError as exc:
            raise LLMError(f"Embedding request failed ({exc.status_code}): {exc.message}") from exc
        except openai.APIConnectionError as exc:
            raise LLMError("Could not reach Azure OpenAI for embeddings.") from exc
        return [d.embedding for d in sorted(resp.data, key=lambda d: d.index)]

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        out: list[list[float]] = []
        for i in range(0, len(texts), 64):
            out.extend(self._embed(texts[i : i + 64]))
        return out

    def embed_query(self, text: str) -> list[float]:
        return self._embed([text])[0]


class HashEmbedder:
    """Deterministic bag-of-words hashing embedder. Offline use and tests only."""

    model_name = "hash-bow"

    def __init__(self, dim: int):
        self.dim = dim

    def _vec(self, text: str) -> list[float]:
        v = np.zeros(self.dim, dtype=np.float64)
        for tok in re.findall(r"[a-z0-9]+", text.lower()):
            h = int(hashlib.md5(tok.encode()).hexdigest(), 16)
            v[h % self.dim] += 1.0 if (h >> 64) & 1 else -1.0
        n = np.linalg.norm(v)
        return (v / n).tolist() if n else v.tolist()

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        return [self._vec(t) for t in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._vec(text)


@lru_cache
def get_embedder() -> Embedder:
    s = get_settings()
    if s.embedding_provider == "hash":
        return HashEmbedder(s.embedding_dim)
    if s.embedding_provider == "azure":
        return AzureEmbedder(s.azure_openai_embedding_deployment, s.embedding_dim)
    raise ValueError(f"Unknown EMBEDDING_PROVIDER: {s.embedding_provider}")
