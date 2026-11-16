from app.schemas import Verdict


def test_supported_claim_is_supported_with_citation(service):
    r = service.verify("62% of the polyester in our outerwear range is GRS-certified recycled polyester.", "Northloom")
    assert r.verdict == Verdict.SUPPORTED
    assert r.citations and r.citations[0].source == "northloom-report-2025"
    assert 0.0 <= r.confidence <= 1.0


def test_overstated_absolute_claim_is_not_supported(service):
    r = service.verify("Our outerwear is made with 100% recycled polyester.", "Northloom")
    assert r.verdict in {Verdict.MISLEADING, Verdict.UNSUPPORTED}


def test_carbon_neutral_brand_claim_is_flagged(service):
    r = service.verify("Northloom is a carbon neutral brand.", "Northloom")
    assert r.verdict != Verdict.SUPPORTED


def test_supported_verdict_requires_citation_guardrail(service):
    """finalize() must downgrade any 'supported' verdict that has no citation."""
    state = {"verdict": "supported", "confidence": 0.9, "citation_ids": [], "rule_flags": []}
    out = service.verifier.finalize(state)
    assert out["verdict"] == "insufficient_evidence" and out["confidence"] <= 0.4


def test_hard_rule_flag_downgrades_supported(service):
    state = {"verdict": "supported", "confidence": 0.9, "citation_ids": ["x"],
             "rule_flags": [{"rule_id": "GC-03", "title": "", "detail": ""}]}
    out = service.verifier.finalize(state)
    assert out["verdict"] == "misleading"


def test_unknown_brand_returns_insufficient_or_unsupported(service):
    r = service.verify("Our sneakers are made from mushroom leather.", "Nobody")
    assert r.verdict in {Verdict.INSUFFICIENT_EVIDENCE, Verdict.UNSUPPORTED}
