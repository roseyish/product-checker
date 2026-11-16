from fastapi.testclient import TestClient

from app.main import app


def test_health_ingest_verify_roundtrip():
    with TestClient(app) as client:
        assert client.get("/health").json()["status"] == "ok"
        text = ("Loomcraft Impact Report 2025. 45% of the cotton in our denim line is certified organic under GOTS, "
                "licence GOTS-LC-10021. The remaining 55% is conventional cotton. " * 3)
        r = client.post("/ingest", json={"source": "loomcraft-2025", "brand": "Loomcraft", "text": text})
        assert r.status_code == 200 and r.json()["chunks_indexed"] >= 1
        v = client.post("/verify", json={"claim": "45% of the cotton in our denim line is GOTS certified organic.",
                                          "brand": "Loomcraft"})
        assert v.status_code == 200
        body = v.json()
        assert body["verdict"] in {"supported", "misleading", "unsupported", "insufficient_evidence"}
        assert "citations" in body and "rule_flags" in body


def test_verify_rejects_short_claim():
    with TestClient(app) as client:
        assert client.post("/verify", json={"claim": "ok"}).status_code == 422
