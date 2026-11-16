"""LangGraph pipeline: extract -> retrieve -> rerank -> rule_check (tool) -> verify -> finalize.

Each node is a plain function over ClaimState so it can be unit-tested without the graph.
"""
from __future__ import annotations

from dataclasses import asdict

from langgraph.graph import END, StateGraph

from app.config import Settings
from app.graph.prompts import EXTRACT_SYSTEM, VERIFY_SYSTEM
from app.graph.state import ClaimState
from app.providers.embeddings import Embedder
from app.providers.llm import LLM
from app.retrieval.store import VectorStore
from app.rules.green_claims import check_claim


class ClaimVerifier:
    def __init__(self, llm: LLM, embedder: Embedder, store: VectorStore, reranker, settings: Settings):
        self.llm, self.embedder, self.store, self.reranker, self.s = llm, embedder, store, reranker, settings
        self.graph = self._build()

    # ---------- nodes ----------
    def extract(self, state: ClaimState) -> ClaimState:
        out = self.llm.complete_json(EXTRACT_SYSTEM, f"CLAIM: {state['claim']}")
        return {"normalized_claim": out.get("normalized_claim", state["claim"]),
                "claim_type": out.get("claim_type", "general_environmental"),
                "quantities": out.get("quantities", [])}

    def retrieve(self, state: ClaimState) -> ClaimState:
        qvec = self.embedder.embed([state["normalized_claim"]])[0]
        hits = self.store.search(qvec, self.s.top_k, brand=state.get("brand"))
        return {"candidates": [{**asdict(h.chunk), "retrieval_score": h.score} for h in hits]}

    def rerank(self, state: ClaimState) -> ClaimState:
        cands = state.get("candidates", [])
        if not cands:
            return {"evidence": []}
        scores = self.reranker.score(state["normalized_claim"], [c["text"] for c in cands])
        ranked = sorted(zip(cands, scores), key=lambda p: -p[1])[: self.s.rerank_top_n]
        return {"evidence": [{**c, "rerank_score": float(sc)} for c, sc in ranked]}

    def rule_check(self, state: ClaimState) -> ClaimState:
        """Deterministic tool call: regulatory-style checks over claim + evidence."""
        ev_text = " ".join(e["text"] for e in state.get("evidence", []))
        flags = check_claim(state["normalized_claim"], ev_text)
        return {"rule_flags": [asdict(f) for f in flags]}

    def verify(self, state: ClaimState) -> ClaimState:
        ev = state.get("evidence", [])
        ev_block = "\n\n".join(f"[{e['chunk_id']}] ({e['source']}) {e['text']}" for e in ev) or "[none]"
        flags = "; ".join(f"{f['rule_id']}: {f['title']}" for f in state.get("rule_flags", [])) or "none"
        user = (f"CLAIM: {state['normalized_claim']}\nCLAIM_TYPE: {state['claim_type']}\n"
                f"RULE_FLAGS: {flags}\n\nEVIDENCE:\n{ev_block}")
        out = self.llm.complete_json(VERIFY_SYSTEM, user)
        valid_ids = {e["chunk_id"] for e in ev}
        return {"verdict": out.get("verdict", "insufficient_evidence"),
                "confidence": float(out.get("confidence", 0.0)),
                "rationale": out.get("rationale", ""),
                "citation_ids": [c for c in out.get("citation_ids", []) if c in valid_ids]}

    def finalize(self, state: ClaimState) -> ClaimState:
        """Guardrails: downgrade unsupported certainty; a 'supported' verdict must cite evidence."""
        verdict, conf = state["verdict"], state["confidence"]
        if verdict == "supported" and not state.get("citation_ids"):
            verdict, conf = "insufficient_evidence", min(conf, 0.4)
        hard_flags = {"GC-02", "GC-03", "GC-05"}
        if verdict == "supported" and any(f["rule_id"] in hard_flags for f in state.get("rule_flags", [])):
            verdict, conf = "misleading", min(conf, 0.6)
        return {"verdict": verdict, "confidence": round(max(0.0, min(1.0, conf)), 2)}

    # ---------- graph ----------
    def _build(self):
        g = StateGraph(ClaimState)
        for name in ("extract", "retrieve", "rerank", "rule_check", "verify", "finalize"):
            g.add_node(name, getattr(self, name))
        g.set_entry_point("extract")
        g.add_edge("extract", "retrieve")
        g.add_edge("retrieve", "rerank")
        g.add_edge("rerank", "rule_check")
        g.add_edge("rule_check", "verify")
        g.add_edge("verify", "finalize")
        g.add_edge("finalize", END)
        return g.compile()

    def run(self, claim: str, brand: str | None = None) -> ClaimState:
        return self.graph.invoke({"claim": claim, "brand": brand})
