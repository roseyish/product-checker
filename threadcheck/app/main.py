from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException

from app import __version__
from app.schemas import IngestRequest, IngestResponse, VerifyRequest, VerifyResponse
from app.service import ThreadCheckService


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.service = ThreadCheckService()
    yield


app = FastAPI(title="ThreadCheck", version=__version__,
              description="Evidence-grounded verification of fashion sustainability claims.",
              lifespan=lifespan)


@app.get("/health")
def health():
    svc: ThreadCheckService = app.state.service
    return {"status": "ok", "version": __version__, "chunks_indexed": svc.store.count(),
            "llm_provider": svc.s.llm_provider, "vector_store": svc.s.vector_store}


@app.post("/ingest", response_model=IngestResponse)
def ingest(req: IngestRequest):
    svc: ThreadCheckService = app.state.service
    n = svc.ingest(req.source, req.text, req.brand, req.doc_type)
    if n == 0:
        raise HTTPException(400, "No indexable text found")
    return IngestResponse(source=req.source, chunks_indexed=n)


@app.post("/verify", response_model=VerifyResponse)
def verify(req: VerifyRequest):
    svc: ThreadCheckService = app.state.service
    try:
        return svc.verify(req.claim, req.brand)
    except Exception as exc:  # surface provider errors cleanly
        raise HTTPException(502, f"Verification failed: {exc}") from exc
