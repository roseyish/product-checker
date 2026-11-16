"""Embedding providers. Mock uses hashed bag-of-words so cosine similarity is still meaningful for tests."""
from __future__ import annotations

import hashlib
import re
from abc import ABC, abstractmethod

import httpx
import numpy as np

from app.config import Settings


class Embedder(ABC):
    dim: int

    @abstractmethod
    def embed(self, texts: list[str]) -> np.ndarray:
        """Return an (n, dim) float32 array of L2-normalised vectors."""


def _normalise(m: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(m, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return (m / norms).astype(np.float32)


class OpenAICompatibleEmbedder(Embedder):
    def __init__(self, settings: Settings, timeout: float = 60.0):
        self.base_url = settings.llm_base_url.rstrip("/")
        self.model = settings.embed_model
        self.dim = settings.embed_dim
        self.headers = {"Authorization": f"Bearer {settings.llm_api_key}", "Content-Type": "application/json"}
        self.client = httpx.Client(timeout=timeout)

    def embed(self, texts: list[str]) -> np.ndarray:
        out: list[list[float]] = []
        for i in range(0, len(texts), 64):  # batch to stay under provider payload limits
            payload = {"model": self.model, "input": texts[i : i + 64], "dimensions": self.dim}
            resp = self.client.post(f"{self.base_url}/embeddings", headers=self.headers, json=payload)
            if resp.status_code == 400 and "dimensions" in resp.text:
                payload.pop("dimensions")  # provider does not support Matryoshka truncation
                resp = self.client.post(f"{self.base_url}/embeddings", headers=self.headers, json=payload)
            resp.raise_for_status()
            out.extend(item["embedding"] for item in resp.json()["data"])
        vecs = np.array(out, dtype=np.float32)
        if vecs.shape[1] != self.dim:
            raise ValueError(f"EMBED_DIM={self.dim} but provider returned {vecs.shape[1]} dims; "
                             f"set EMBED_DIM={vecs.shape[1]} in .env")
        return _normalise(vecs)


class HashEmbedder(Embedder):
    """Deterministic 512-dim hashed unigram+bigram embedding. No model download, no network."""

    def __init__(self, dim: int = 512):
        self.dim = dim

    def embed(self, texts: list[str]) -> np.ndarray:
        m = np.zeros((len(texts), self.dim), dtype=np.float32)
        for row, text in enumerate(texts):
            tokens = re.findall(r"[a-z0-9%]+", text.lower())
            grams = tokens + [f"{a}_{b}" for a, b in zip(tokens, tokens[1:])]
            for g in grams:
                h = int(hashlib.md5(g.encode()).hexdigest(), 16)
                m[row, h % self.dim] += 1.0 if h & 1 else -1.0
        return _normalise(m)


def get_embedder(settings: Settings) -> Embedder:
    return OpenAICompatibleEmbedder(settings) if settings.llm_provider == "openai" else HashEmbedder()
