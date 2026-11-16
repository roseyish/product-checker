from app.rules.green_claims import check_claim


def ids(claim, evidence=""):
    return {f.rule_id for f in check_claim(claim, evidence)}


def test_vague_term_flagged():
    assert "GC-01" in ids("Our eco-friendly collection is here")


def test_vague_term_not_flagged_when_quantified():
    assert "GC-01" not in ids("Our sustainable line uses 40% recycled cotton in every style")


def test_absolute_needs_quantified_evidence():
    assert "GC-02" in ids("Made from 100% recycled polyester", "We use some recycled polyester.")
    assert "GC-02" not in ids("Made from 100% recycled polyester",
                              "100% of polyester in this line is recycled, verified by third-party audit.")


def test_carbon_claim_needs_methodology():
    assert "GC-03" in ids("We are a carbon neutral brand", "We planted trees.")
    assert "GC-03" not in ids("We are a carbon neutral brand",
                              "Scope 1 and 2 emissions were offset with verified credits.")


def test_percentage_without_denominator():
    assert "GC-04" in ids("Now 30% more recycled")
    assert "GC-04" not in ids("30% of our polyester is recycled")


def test_certification_needs_certificate_reference():
    assert "GC-05" in ids("Our T-shirts are GOTS certified", "We love organic cotton.")
    assert "GC-05" not in ids("Our T-shirts are GOTS certified", "Licence number GOTS-NL-88213.")


def test_comparative_without_baseline():
    assert "GC-06" in ids("Our jeans use less water")
    assert "GC-06" not in ids("Our jeans use 22% less water than in 2022")
