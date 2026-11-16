"""Composition root: builds providers once and exposes ingest/verify used by API, CLI, and eval."""
from __future__ import annotations

from app.config import Settings, get_settings
from app.graph.pipeline import ClaimVerifier
from app.ingest.chunker import chunk_text
from app.providers.embeddings import get_embedder
from app.providers.llm import get_llm
from app.providers.reranker import get_reranker
from app.retrieval.store import get_store
from app.schemas import Citation, RuleFlag, Verdict, VerifyResponse


class ThreadCheckService:
    def __init__(self, settings: Settings | None = None):
        self.s = settings or get_settings()
        self.embedder = get_embedder(self.s)
        self.store = get_store(self.s)
        self.verifier = ClaimVerifier(get_llm(self.s), self.embedder, self.store, get_reranker(), self.s)

    def ingest(self, source: str, text: str, brand: str | None = None, doc_type: str = "report") -> int:
        chunks = chunk_text(text, source, brand, doc_type, self.s.chunk_size, self.s.chunk_overlap)
        if not chunks:
            return 0
        vecs = self.embedder.embed([c.text for c in chunks])
        return self.store.upsert(chunks, vecs)

    def verify(self, claim: str, brand: str | None = None) -> VerifyResponse:
        st = self.verifier.run(claim, brand)
        by_id = {e["chunk_id"]: e for e in st.get("evidence", [])}
        cited = [by_id[c] for c in st.get("citation_ids", []) if c in by_id]
        return VerifyResponse(
            claim=claim,
            normalized_claim=st["normalized_claim"],
            claim_type=st["claim_type"],
            verdict=Verdict(st["verdict"]),
            confidence=st["confidence"],
            rationale=st["rationale"],
            citations=[Citation(chunk_id=e["chunk_id"], source=e["source"], excerpt=e["text"],
                                score=e.get("rerank_score", 0.0)) for e in cited],
            rule_flags=[RuleFlag(**f) for f in st.get("rule_flags", [])],
        )
