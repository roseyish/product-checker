import os

import pytest

os.environ.setdefault("LLM_PROVIDER", "mock")
os.environ.setdefault("VECTOR_STORE", "memory")

from app.config import Settings  # noqa: E402
from app.ingest.loader import load_document  # noqa: E402
from app.service import ThreadCheckService  # noqa: E402

SAMPLES = os.path.join(os.path.dirname(__file__), "..", "data", "samples")


@pytest.fixture(scope="session")
def service() -> ThreadCheckService:
    svc = ThreadCheckService(Settings(llm_provider="mock", vector_store="memory"))
    svc.ingest("northloom-report-2025", load_document(os.path.join(SAMPLES, "northloom_report_2025.md")), "Northloom")
    svc.ingest("veridian-report-2025", load_document(os.path.join(SAMPLES, "veridian_report_2025.md")), "Veridian")
    svc.ingest("green-claims-guidance", load_document(os.path.join(SAMPLES, "green_claims_guidance_summary.md")),
               None, "regulation")
    return svc
