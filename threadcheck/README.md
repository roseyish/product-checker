# ThreadCheck

Evidence-grounded verification of fashion sustainability claims.

Give it a marketing claim such as "our outerwear is made with 100% recycled polyester" and a brand name.
ThreadCheck retrieves evidence from that brand's own published reports and from regulatory guidance,
reranks it, runs deterministic green-claims rule checks, and returns a verdict
(supported / misleading / unsupported / insufficient evidence) with a confidence score, a two-sentence
rationale, and verbatim citations. Every verdict is auditable back to a specific passage.

Greenwashing in fashion is a live regulatory issue (UK CMA Green Claims Code, EU Empowering Consumers and
Green Claims directives). ThreadCheck is a small, testable reference implementation of how an LLM system can
be held to the same standard: no claim is called "supported" without a citation.

## Architecture

```
claim + brand
    |
    v
[extract]  LLM normalises the claim, types it (material / carbon / certification / ...), pulls out quantities
    |
    v
[retrieve] embed -> pgvector cosine search (HNSW), brand-scoped: other brands' reports are never evidence
    |
    v
[rerank]   cross-encoder (sentence-transformers) if installed, else lexical reranker; keep top-n
    |
    v
[rule_check]  deterministic tool: 6 green-claims rules (vague terms, absolutes, carbon scope,
    |          missing denominators, untraceable certifications, baseline-less comparisons)
    v
[verify]   LLM auditor sees claim + rule flags + evidence, returns verdict, confidence, rationale, cited chunk ids
    |
    v
[finalize] guardrails: "supported" without a citation -> insufficient_evidence;
           "supported" with a hard rule flag (GC-02/03/05) -> misleading
```

The pipeline is a LangGraph `StateGraph`; each node is a plain function so it can be unit-tested alone.
Providers are pluggable behind small interfaces:

| Layer      | Production                                  | Offline / CI                          |
|------------|---------------------------------------------|---------------------------------------|
| LLM        | any OpenAI-compatible `/chat/completions`   | `MockLLM` (deterministic heuristics)  |
| Embeddings | any OpenAI-compatible `/embeddings`         | `HashEmbedder` (hashed n-grams)       |
| Reranker   | `cross-encoder/ms-marco-MiniLM-L-6-v2`      | lexical weighted-Jaccard              |
| Store      | PostgreSQL + pgvector (HNSW, cosine)        | in-memory NumPy                       |

## Quick start

```bash
# 1. offline demo, no key needed
pip install -r requirements-dev.txt
python cli.py demo
pytest -q
python -m eval.run_eval

# 2. real model + pgvector (Gemini preset is the default in .env.example)
cp .env.example .env            # paste your Gemini key into LLM_API_KEY
python scripts/check_provider.py   # confirms chat + embeddings work
docker compose up --build       # API on :8000, Postgres+pgvector on :5432

# ingest a brand report and verify a claim
curl -X POST localhost:8000/ingest -H 'content-type: application/json' \
  -d @- <<'JSON'
{"source":"northloom-report-2025","brand":"Northloom","doc_type":"report","text":"<paste report text>"}
JSON

curl -X POST localhost:8000/verify -H 'content-type: application/json' \
  -d '{"claim":"Northloom is a carbon neutral brand.","brand":"Northloom"}'
```

Works with Google Gemini (via its OpenAI-compatible endpoint), OpenAI, Ollama, vLLM, or any Qwen/Llama
server exposing the OpenAI schema. Presets for all three are in `.env.example`.

## Evaluation

`eval/claims_labelled.jsonl` holds 40 labelled claims (supported / misleading / unsupported) against two
fictional brand reports written to contain realistic traps: scope narrowing, mass-balance sourcing,
offset-based neutrality, certification name-dropping, and partial-supplier metrics.

`python -m eval.run_eval` reports:

| Metric | What it measures |
|---|---|
| `verdict_accuracy`, `macro_f1` | 4-class verdict quality |
| `binary_accuracy_supported_vs_not` | the greenwashing-screen question: is this claim actually backed? |
| `recall@k` | did retrieval surface the labelled evidence source |
| `citation_faithfulness` | share of cited chunks that come from the correct source document |
| `judge_faithfulness_mean` (`--judge`) | LLM-as-judge: is the rationale grounded only in the cited text |
| `latency_p50_s`, `latency_p95_s` | end-to-end pipeline latency |

Offline baseline (mock provider, hashed embeddings, lexical reranker, no network):

| Metric | Baseline |
|---|---|
| recall@8 | 1.00 |
| citation_faithfulness | 0.925 |
| binary accuracy | 0.75 |
| verdict accuracy | 0.575 |
| macro F1 | 0.514 |

The baseline exists so CI has a regression gate (`recall@8 >= 0.95`, `citation_faithfulness >= 0.85`).
Run with a real model and `--judge` to record production numbers; results are written to `eval/results/`.

## Project layout

```
app/
  config.py            pydantic-settings, all knobs env-overridable
  schemas.py           API contracts (VerifyRequest/Response, Citation, RuleFlag)
  service.py           composition root: ingest() and verify()
  main.py              FastAPI: /health /ingest /verify
  providers/           llm.py, embeddings.py, reranker.py
  ingest/              chunker.py (sentence-aware sliding window), loader.py (txt/md/pdf)
  retrieval/store.py   VectorStore: PgVectorStore, InMemoryStore
  rules/green_claims.py  deterministic regulatory rule checks (the agent's tool)
  graph/               LangGraph pipeline, state, prompts
eval/                  labelled claims, run_eval.py
tests/                 22 unit + API tests (pytest, offline)
data/samples/          fictional brand reports + paraphrased guidance summary
Dockerfile, docker-compose.yml, .github/workflows/ci.yml
```

## Design decisions

* **Citations are mandatory for "supported".** The guardrail lives in code, not in the prompt, so a
  model regression cannot silently produce unbacked approvals.
* **Rules are a tool, not a prompt.** Green-claims checks are deterministic Python. They are unit-tested,
  explainable to a compliance reviewer, and their flags are fed to the verifier as structured context.
* **Brand scoping at retrieval time.** A brand's claim can only be evidenced by that brand's documents plus
  regulation. This prevents cross-brand evidence leakage, a common RAG failure.
* **Offline-first testing.** Everything in `tests/` and the CI eval gate runs with no API key and no model
  download, so contributors and CI are never blocked by provider availability.

## Roadmap

* Claim extraction from brand web pages and product listings (crawler + claim spotter)
* Per-rule precision/recall on a larger public claim set
* Hybrid BM25 + vector retrieval and query expansion for numeric claims
* Streamlit review UI for compliance analysts

## Disclaimer

Sample brand reports are fictional. Regulatory text is paraphrased for context and is not legal advice.
