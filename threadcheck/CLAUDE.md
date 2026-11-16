# CLAUDE.md

Project: ThreadCheck, an evidence-grounded verifier for fashion sustainability claims.
Owner: Isha Sharma. Read `docs/PROJECT_GUIDE.md` first; it explains every file and design decision.

## What this project is, in one paragraph
A FastAPI service that takes a marketing claim ("our outerwear is 100% recycled polyester") plus a brand
name, retrieves evidence from that brand's own reports and from green-claims regulation stored in
PostgreSQL + pgvector, reranks it, runs six deterministic regulatory rule checks, asks an LLM to audit the
claim against the evidence, and returns a verdict (supported / misleading / unsupported /
insufficient_evidence) with confidence, a two-sentence rationale, and verbatim citations. The pipeline is a
LangGraph StateGraph with six nodes. A code-level guardrail guarantees no "supported" verdict is returned
without a citation.

## Commands
```bash
pip install -r requirements-dev.txt        # deps (dev includes pytest, ruff)
python cli.py demo                         # offline demo, mock provider, no key
pytest -q                                  # 22 tests, all offline
ruff check .                               # lint, must be clean before commit
python -m eval.run_eval                    # offline eval baseline
python scripts/check_provider.py           # verify LLM + embedding endpoint in .env
python -m eval.run_eval --judge            # real eval with the configured LLM
docker compose up --build                  # API :8000 + pgvector :5432
uvicorn app.main:app --reload              # API only, uses .env
```

## Hard rules
1. Never put an API key in code or in a committed file. `.env` is gitignored; `.env.example` has placeholders only.
2. Never weaken `ClaimVerifier.finalize()`. The citation guardrail is the core product guarantee and is tested.
3. Never reproduce copyrighted regulatory text. `data/samples/green_claims_guidance_summary.md` is a paraphrase; keep it that way.
4. Sample brand reports are fictional (Northloom, Veridian). Do not present them as real companies.
5. All tests and the CI eval gate must run with `LLM_PROVIDER=mock` and `VECTOR_STORE=memory`. Do not add tests that need network or a database.
6. Keep every provider behind its interface (`LLM`, `Embedder`, `VectorStore`, reranker `.score()`). New providers are new classes, not branches in the pipeline.
7. Lint config is in `pyproject.toml` (ruff, line length 120). Run `ruff check . --fix` before committing.
8. No em dashes anywhere in code, docs, or commit messages. Use commas, colons, or hyphens.

## Layout
```
app/config.py            Settings (pydantic-settings), all env-overridable
app/schemas.py           API request/response models
app/service.py           composition root: ingest(), verify()
app/main.py              FastAPI app: /health /ingest /verify
app/providers/           llm.py, embeddings.py, reranker.py (real + mock each)
app/ingest/              chunker.py, loader.py
app/retrieval/store.py   PgVectorStore, InMemoryStore
app/rules/green_claims.py  six deterministic rule checks (the agent's tool)
app/graph/               pipeline.py (LangGraph), state.py, prompts.py
eval/                    claims_labelled.jsonl (40), run_eval.py
tests/                   pytest, offline
data/samples/            fictional reports + paraphrased guidance
scripts/check_provider.py
cli.py, Dockerfile, docker-compose.yml, .github/workflows/ci.yml
```

## Current state and next tasks
See "Plan for today" in `docs/PROJECT_GUIDE.md`. Priority order: push to GitHub, get CI green, run real eval
with Gemini, record numbers in README, then roadmap items.
