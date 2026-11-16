from enum import Enum
from typing import Optional

from pydantic import BaseModel, Field


class Verdict(str, Enum):
    SUPPORTED = "supported"
    UNSUPPORTED = "unsupported"
    MISLEADING = "misleading"
    INSUFFICIENT_EVIDENCE = "insufficient_evidence"


class Citation(BaseModel):
    chunk_id: str
    source: str
    excerpt: str = Field(..., description="Verbatim evidence passage used in the decision")
    score: float


class RuleFlag(BaseModel):
    rule_id: str
    title: str
    detail: str


class VerifyRequest(BaseModel):
    claim: str = Field(..., min_length=5, max_length=2000)
    brand: Optional[str] = Field(None, description="Restrict evidence to this brand's documents")


class VerifyResponse(BaseModel):
    claim: str
    normalized_claim: str
    claim_type: str
    verdict: Verdict
    confidence: float = Field(..., ge=0.0, le=1.0)
    rationale: str
    citations: list[Citation]
    rule_flags: list[RuleFlag]


class IngestRequest(BaseModel):
    source: str = Field(..., description="Document identifier, e.g. 'brand-x-sustainability-report-2025'")
    brand: Optional[str] = None
    doc_type: str = Field("report", description="report | standard | regulation | other")
    text: str = Field(..., min_length=50)


class IngestResponse(BaseModel):
    source: str
    chunks_indexed: int
