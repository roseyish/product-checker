from typing import Any, Optional, TypedDict


class ClaimState(TypedDict, total=False):
    claim: str
    brand: Optional[str]
    normalized_claim: str
    claim_type: str
    quantities: list[str]
    candidates: list[dict[str, Any]]      # retrieved hits before rerank
    evidence: list[dict[str, Any]]        # reranked, top-n
    rule_flags: list[dict[str, str]]
    verdict: str
    confidence: float
    rationale: str
    citation_ids: list[str]
