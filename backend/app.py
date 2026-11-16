from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from engine import analyze as run_analysis, get_kb
from engine.curation import load_log, record_unknown

app = FastAPI(title="Product Checker API")

# CORS lets our Chrome extension (running on a different origin) call this backend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


class AnalyzeRequest(BaseModel):
    """Body the Chrome extension sends: scraped ingredients plus marketing claims."""
    ingredients: str = ""
    claims: str = ""


@app.get("/health")
def health():
    """Simple check to confirm the server is running."""
    kb = get_kb()
    return {
        "status": "ok",
        "knowledge_version": kb.version,
        "curated_names": len(kb.index),
        "official_names": len(kb.cosing),
        "cosing_synced": kb.cosing_synced,
    }


@app.post("/analyze")
def analyze(body: AnalyzeRequest):
    """
    Main endpoint the Chrome extension calls.
    Takes scraped ingredients + claims and judges them against our own ingredient
    knowledge base. No external AI service is involved, so there is no quota to run out.
    """
    if not body.ingredients:
        return JSONResponse(status_code=400, content={"error": "No ingredients text provided"})

    result = run_analysis(body.ingredients, body.claims)
    cov = result["coverage"]
    print(f"[analyze] {cov['recognized']}/{cov['total']} ingredients recognised, "
          f"{len(result['claims'])} claims -> score={result['truth_score']} ({result['verdict_word']})")
    # Every unrecognised ingredient is counted; the most frequent get curated first
    # (scripts/curation_report.py -> review -> scripts/apply_curation.py).
    record_unknown(cov["unknown"], example=body.claims)
    return result


@app.get("/curation/unknown")
def unknown_ingredients(limit: int = 50):
    """The curation to-do list: unrecognised ingredients from real traffic, most frequent first."""
    log = load_log()
    top = sorted(log.items(), key=lambda kv: -kv[1]["count"])[:limit]
    return {"total_unknown": len(log), "top": [dict(name=k, **v) for k, v in top]}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", port=5000, reload=True)
