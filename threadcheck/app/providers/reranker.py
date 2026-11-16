"""Second-stage reranking of retrieved chunks.

Uses a sentence-transformers cross-encoder when the optional dependency is installed,
otherwise a lexical overlap scorer so the pipeline stays runnable anywhere.
"""
from __future__ import annotations

import re
from typing import Protocol


class Chunk(Protocol):
    chunk_id: str
    text: str


def _tokens(s: str) -> set[str]:
    return set(re.findall(r"[a-z0-9%]{3,}", s.lower()))


class LexicalReranker:
    def score(self, query: str, docs: list[str]) -> list[float]:
        q = _tokens(query)
        out = []
        for d in docs:
            t = _tokens(d)
            inter = len(q & t)
            # Weighted Jaccard: reward query coverage more than doc length
            out.append(0.7 * inter / max(1, len(q)) + 0.3 * inter / max(1, len(q | t)))
        return out


class CrossEncoderReranker:
    def __init__(self, model_name: str = "cross-encoder/ms-marco-MiniLM-L-6-v2"):
        from sentence_transformers import CrossEncoder  # optional dependency

        self.model = CrossEncoder(model_name)

    def score(self, query: str, docs: list[str]) -> list[float]:
        raw = self.model.predict([(query, d) for d in docs])
        # squash logits to [0, 1] for a consistent confidence scale
        import math

        return [1 / (1 + math.exp(-float(x))) for x in raw]


def get_reranker():
    try:
        return CrossEncoderReranker()
    except Exception:
        return LexicalReranker()
