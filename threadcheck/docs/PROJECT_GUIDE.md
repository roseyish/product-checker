# ThreadCheck: Complete Project Guide

This document explains ThreadCheck from the problem it solves down to every function in the codebase.
It is written so that a developer (or Claude Code) with zero prior context can understand, run, extend, and
defend the project in an interview.

Read in order. Section 1 to 3 give the "why". Sections 4 to 9 are the "what and how", file by file.
Section 10 is how to run it. Section 11 is the plan for finishing it today. Section 12 is interview prep.

---

## 1. The problem

Fashion brands make environmental claims in marketing: "100% recycled", "carbon neutral", "sustainable
collection", "GOTS certified". Regulators in the UK (Competition and Markets Authority, Green Claims Code)
and the EU (Empowering Consumers Directive, Green Claims Directive) now treat vague or overstated claims as
misleading advertising. Fines and forced withdrawals have followed for several large brands.

The hard part is not spotting the claim. It is checking the claim against what the brand itself has
published in its sustainability report, and against what the rules say a claim must disclose. That is a
retrieval and reasoning problem with a strict requirement: the answer must be traceable to a specific
passage. A verifier that says "supported" without showing where is worthless to a compliance analyst.

## 2. What ThreadCheck does

Input: a claim string and a brand name.
Output: a verdict, a confidence score, a short rationale, the exact evidence passages relied on, and a list
of regulatory rule flags.

The four verdicts:

| Verdict | Meaning |
|---|---|
| `supported` | Evidence directly confirms the claim at the stated scope and quantity |
| `misleading` | Evidence partly confirms it, but the claim overstates scope, hides exclusions or offsets, or implies more than the evidence supports |
| `unsupported` | Evidence covers the topic but does not confirm the claim, or contradicts it |
| `insufficient_evidence` | Nothing retrieved addresses the claim |

The distinction between `misleading` and `unsupported` is the heart of the product. "Our outerwear is 100%
recycled polyester" when the report says 62% is misleading, not unsupported: there is real recycled content,
the number is just wrong. "All our cotton is organic" when the report says 9% is unsupported: the claim is
simply false at any reasonable reading.

## 3. Why the design looks the way it does

Four decisions shape everything else. Understand these and the code will make sense.

**Decision 1: the citation guardrail lives in code, not in the prompt.**
LLMs can be prompted to "always cite", and they mostly will. Mostly is not good enough for a compliance
tool. `ClaimVerifier.finalize()` inspects the LLM output after the fact: if the verdict is `supported` and
there are zero valid citation ids, the verdict is rewritten to `insufficient_evidence` and confidence is
capped at 0.4. This cannot be bypassed by a model change, a prompt regression, or a jailbreak in the claim
text. It is unit-tested.

**Decision 2: regulatory rules are a deterministic tool, not LLM judgment.**
Six checks in `app/rules/green_claims.py` are plain Python with regex. They fire on patterns like an
absolute word ("100%", "zero") with no quantified evidence, or a carbon-neutral claim with no mention of
scope or offsets. They are unit-tested, explainable to a lawyer, and their output is fed to the LLM as
structured context. The LLM does the nuanced reading; the rules do the mechanical checks. This is the
"agent calls a tool" pattern, implemented as a graph node.

**Decision 3: evidence is brand-scoped at retrieval time.**
When verifying a Northloom claim, Veridian's report must never appear as evidence, even if it is
semantically similar. Both stores filter: report-type documents must match the brand; regulation-type
documents are always allowed. This prevents a classic RAG failure where a good passage from the wrong
source produces a confident wrong answer.

**Decision 4: everything runs offline with mocks.**
`MockLLM`, `HashEmbedder`, `LexicalReranker`, and `InMemoryStore` let the full pipeline, all 22 tests, and
the CI evaluation gate run with no API key, no model download, and no database. Real providers plug in via
`.env`. This means CI is never blocked by a provider outage and contributors can run everything on a laptop
in seconds.

## 4. Architecture

```
                   POST /verify {claim, brand}
                               |
                               v
  +------------------------------------------------------------------+
  |  ClaimVerifier (LangGraph StateGraph over ClaimState)             |
  |                                                                  |
  |  extract ---> retrieve ---> rerank ---> rule_check ---> verify ---> finalize
  |    LLM        embed +       cross-      6 regex        LLM         guardrails
  |  normalise    pgvector      encoder     rules          auditor     + clamp
  |  + type       cosine        or lexical  (tool)                     conf
  |               brand-scoped  top-n                                  |
  +------------------------------------------------------------------+
                               |
                               v
             VerifyResponse {verdict, confidence, rationale,
                             citations[], rule_flags[]}
```

`ThreadCheckService` (app/service.py) is the composition root. It builds one embedder, one store, one
reranker, one LLM, and one `ClaimVerifier`, and exposes two methods: `ingest()` and `verify()`. The FastAPI
app, the CLI, and the eval harness all go through this one class, so behaviour is identical everywhere.

## 5. Data flow, step by step, with a real example

Claim: "Our outerwear is made with 100% recycled polyester." Brand: Northloom.

**Ingest (done earlier).** The Northloom report markdown is split into sentence-aware chunks of about 600
characters with 80 characters of overlap. Each chunk gets an id like `chunk_northloom-report-2025_0000`,
the source, brand, doc_type, and position. Each chunk text is embedded and stored with its vector.

**extract.** The LLM is asked to normalise the claim and classify it. Output:
`{"normalized_claim": "Our outerwear is made with 100% recycled polyester", "claim_type": "material",
"quantities": ["100%"]}`. Normalising strips marketing filler so retrieval is cleaner. The type is passed
to the verifier as context.

**retrieve.** The normalised claim is embedded. The store returns the top 8 chunks by cosine similarity,
excluding any report chunk whose brand is not Northloom. Each hit carries `retrieval_score`.

**rerank.** A cross-encoder (if installed) or the lexical reranker scores each (claim, chunk) pair jointly.
The top 4 are kept as `evidence`, each with `rerank_score`. Reranking matters because bi-encoder similarity
is coarse: a chunk about "recycled LDPE mailer bags" is close in embedding space to a claim about "recycled
polyester outerwear" but a cross-encoder knows they are different things.

**rule_check.** `check_claim(normalized_claim, evidence_text)` runs. For this claim it fires GC-04
(percentage without a clear denominator: "100% recycled polyester" does not say 100% of what). GC-02 does
not fire because the evidence contains a percentage and an audit reference, so the absolute claim has
quantified evidence available even if the number disagrees.

**verify.** The LLM receives the claim, the claim type, the rule flags as a single line, and the evidence
as `[chunk_id] (source) text` blocks. It returns a verdict, a confidence, a rationale, and the chunk ids it
relied on. The node discards any cited id that is not actually in the evidence list, so a hallucinated
citation is dropped before it reaches the user.

**finalize.** Two guardrails, then clamp confidence to [0, 1]:
- `supported` with no citations becomes `insufficient_evidence`, confidence capped at 0.4.
- `supported` with a hard flag (GC-02, GC-03, GC-05) becomes `misleading`, confidence capped at 0.6. These
  three flags mean the evidence structurally cannot support the claim as worded.

**Response.** The service maps cited chunk ids back to their text and builds `VerifyResponse`. For this
claim, the expected result is `misleading`, citing the chunk that says 62% recycled, 38% virgin.

## 6. File-by-file reference

### app/config.py
`Settings` (pydantic-settings). Reads `.env` and environment variables. Fields:

| Field | Default | Purpose |
|---|---|---|
| `llm_provider` | `mock` | `openai` for any OpenAI-compatible endpoint, `mock` for offline |
| `llm_base_url` | OpenAI URL | Gemini, Ollama, vLLM all work by changing this |
| `llm_api_key` | empty | never commit |
| `llm_model` | `gpt-4o-mini` | chat model name at the endpoint |
| `embed_model` | `text-embedding-3-small` | embedding model name |
| `embed_dim` | 1536 | must match the model's output; pgvector table is created with this width |
| `vector_store` | `memory` | `pgvector` in production |
| `database_url` | local postgres | used only when `vector_store=pgvector` |
| `top_k` | 8 | retrieval candidates |
| `rerank_top_n` | 4 | evidence passed to the LLM |
| `chunk_size` / `chunk_overlap` | 600 / 80 | characters |

`get_settings()` is `lru_cache`d so the whole process shares one Settings.

### app/schemas.py
Pydantic models for the API. `Verdict` enum. `Citation` (chunk_id, source, verbatim excerpt, score).
`RuleFlag` (rule_id, title, detail). `VerifyRequest` (claim 5 to 2000 chars, optional brand).
`VerifyResponse`. `IngestRequest` (source id, brand, doc_type, text of at least 50 chars). `IngestResponse`.

### app/providers/llm.py
`LLM` abstract class with one method: `complete_json(system, user) -> dict`.

`OpenAICompatibleLLM`: POSTs to `{base_url}/chat/completions` with `temperature=0` and
`response_format={"type":"json_object"}`. If the server returns 400 mentioning `response_format`, it retries
without it (some local servers do not support it). `_extract_json()` tolerates ```json fences and leading
prose, which smaller models produce.

`MockLLM`: routes on the `TASK:` line of the system prompt.
- `extract_claim`: regex-classifies the claim type, extracts percentages.
- `verify_claim`: sentence-level heuristic. Splits evidence into sentences tagged with their chunk id, picks
  the sentence with the highest claim-word coverage, then checks: are the claim's numbers present in that
  sentence; does the sentence contain a qualifier ("remaining", "not", "does not guarantee", "virgin",
  "below"); is the claim absolute ("100%", "all", "every", "brand"). From those it picks a verdict. This is
  deliberately simple. It exists to exercise the pipeline and give CI a stable baseline, not to be accurate.
- `judge`: returns a fixed faithful score.

`get_llm(settings)` picks the implementation.

### app/providers/embeddings.py
`Embedder` abstract class: `embed(texts) -> np.ndarray` of shape (n, dim), L2-normalised float32.

`OpenAICompatibleEmbedder`: batches of 64, POSTs to `/embeddings` with `dimensions=embed_dim`. If the
server rejects `dimensions`, retries without it. If the returned width does not match `embed_dim`, raises a
clear error telling you what to set. This matters for pgvector: the table column is `VECTOR(dim)` and an
insert with the wrong width fails.

`HashEmbedder`: 512-dim hashed unigram and bigram bag of words with signed hashing. Deterministic, no
download, and cosine similarity is still meaningful for lexical overlap. Used by tests and CI.

### app/providers/reranker.py
Duck-typed: anything with `score(query, docs) -> list[float]`.
`CrossEncoderReranker` wraps `sentence-transformers` `cross-encoder/ms-marco-MiniLM-L-6-v2` and squashes
logits through a sigmoid so scores land in [0, 1]. `LexicalReranker` is a weighted Jaccard that rewards
query coverage. `get_reranker()` tries the cross-encoder and falls back if the package is missing.

### app/ingest/chunker.py
`Chunk` dataclass. `split_sentences()` splits on sentence-ending punctuation followed by a capital, digit,
or quote. `chunk_text()` accumulates sentences until `chunk_size` is exceeded, flushes, and carries a tail
of sentences totalling up to `overlap` characters into the next chunk. Sentence boundaries are preserved
so a citation never starts mid-sentence. Ids are `chunk_{source}_{position:04d}`, which makes them stable
across re-ingests of the same document.

### app/ingest/loader.py
`load_document(path)`: `.pdf` via pypdf, everything else read as UTF-8 text.

### app/retrieval/store.py
`VectorStore` abstract class: `upsert(chunks, vectors)`, `search(query_vec, top_k, brand)`, `count()`.

`InMemoryStore`: NumPy matrix. `upsert` first drops existing chunks from the same source so re-ingesting a
document is idempotent. `search` computes dot products (vectors are unit norm so this is cosine), sorts,
and applies the brand filter: a chunk is skipped if it is a `report` whose brand differs from the query
brand. Regulation chunks (brand None) always pass.

`PgVectorStore`: on construction creates the `vector` extension, the `evidence_chunks` table with a
`VECTOR(dim)` column, an HNSW index with `vector_cosine_ops`, and a brand index. `upsert` uses
`ON CONFLICT (chunk_id) DO UPDATE`. `search` uses the `<=>` cosine distance operator and returns
`1 - distance` as the score. The brand filter is a SQL WHERE clause with the same semantics as the in-memory
version.

### app/rules/green_claims.py
`Flag` dataclass and `check_claim(claim, evidence_text) -> list[Flag]`. The six rules:

| Id | Fires when | Rationale |
|---|---|---|
| GC-01 | Generic term ("sustainable", "eco-friendly", "green", "conscious") with no digit and no named standard | Guidance says generic terms need specifics |
| GC-02 | Absolute term ("100%", "zero", "fully", "all of our") and evidence has no percentage, unit, audit, or verification word | Absolutes need quantified substantiation |
| GC-03 | Carbon term ("carbon neutral", "net zero") and evidence has no scope, offset, credit, or methodology word | Carbon claims must disclose boundary and offsets |
| GC-04 | A percentage appears but no denominator word ("of", "in", "across", "by") | "30% more recycled" of what? |
| GC-05 | A certification name appears but evidence has no licence, certificate, or certificate-number pattern | Labels need traceable certificates |
| GC-06 | Comparative ("more", "less", "reduced") with no "than", "compared", "versus" | Comparisons need a baseline |

All keyword lists are module-level tuples so they are easy to extend and review.

### app/graph/prompts.py
Three system prompts. Each starts with `TASK: <name>` so `MockLLM` can route, and each ends with an explicit
JSON schema. `VERIFY_SYSTEM` defines the four verdicts precisely and instructs the model to use only the
given evidence and to cite chunk ids in brackets. Keep the `TASK:` lines if you edit these.

### app/graph/state.py
`ClaimState` TypedDict with `total=False` so nodes can return partial updates. LangGraph merges them.

### app/graph/pipeline.py
`ClaimVerifier`. Constructor takes the four providers and settings, builds the graph. Six node methods,
each `(ClaimState) -> ClaimState` returning only the keys it changes. `_build()` wires a linear
`StateGraph`: extract, retrieve, rerank, rule_check, verify, finalize, END. `run(claim, brand)` invokes it.

Nodes are ordinary methods so tests call them directly, for example `service.verifier.finalize(state)` to
test the guardrail in isolation without an LLM.

### app/service.py
`ThreadCheckService`. `ingest()` chunks, embeds, upserts, returns chunk count. `verify()` runs the graph and
maps the state into a `VerifyResponse`, resolving cited chunk ids back to full `Citation` objects.

### app/main.py
FastAPI with a lifespan that builds one `ThreadCheckService` at startup and stores it on `app.state`.
`/health` reports version, indexed chunk count, provider, store. `/ingest` returns 400 if nothing indexable.
`/verify` wraps provider exceptions into a 502 with the error text.

### eval/claims_labelled.jsonl
40 claims, 20 per fictional brand. Fields: `id`, `brand`, `claim`, `label`, `evidence_source`. The claims
are designed to include the classic greenwashing patterns:
- Scope narrowing: "carbon neutral brand" when only HQ and warehouses for scope 1 and 2 are covered (c03).
- Mass balance: "every cotton garment contains Better Cotton" when Better Cotton is mass-balance sourced (c05).
- Partial supplier metrics: "cut water use in denim by 22%" when it was two suppliers only (c08).
- Certification name-dropping: "denim produced in bluesign certified facilities" when only 58% of volume is (c15).
- Plain false: "all our cotton is organic" when 9% is (c07).
- Plain true: exact restatements of report figures (c02, c09, c11, and so on).

### eval/run_eval.py
Loads the sample corpus into a fresh service, runs every claim through `verifier.run()`, and computes:
- `verdict_accuracy` and `macro_f1` over the four classes.
- `binary_accuracy_supported_vs_not`: the question a greenwashing screen actually asks.
- `recall@k`: whether any retrieved candidate came from the labelled evidence source.
- `citation_faithfulness`: share of cited chunks whose source equals the labelled source.
- `judge_faithfulness_mean` (with `--judge`): LLM-as-judge grades whether the rationale is grounded only in
  the cited text.
- `latency_p50_s`, `latency_p95_s`.
- A confusion table.
Writes `summary-<timestamp>.json` and `records-<timestamp>.jsonl` to `eval/results/`.

### tests/
`conftest.py` builds a session-scoped service with the mock providers and the sample corpus loaded.
`test_chunker.py`: sentence integrity, size and overlap, id uniqueness.
`test_rules.py`: each rule fires and does not fire in the right cases.
`test_retrieval.py`: ranking, brand filter, idempotent re-ingest, unit-norm embeddings.
`test_pipeline.py`: end-to-end verdicts on known claims, plus the two `finalize()` guardrails tested in
isolation.
`test_api.py`: FastAPI round trip with `TestClient`, and a 422 on a too-short claim.

### Ops files
`Dockerfile`: python 3.12 slim, installs requirements, copies app, data, eval, runs uvicorn on 8000 with a
healthcheck. `docker-compose.yml`: `pgvector/pgvector:pg16` with a healthcheck, and the API depending on it,
with `VECTOR_STORE=pgvector` forced. `.github/workflows/ci.yml`: lint, tests with coverage, then the offline
eval with a regression gate (`recall@8 >= 0.95`, `citation_faithfulness >= 0.85`), then a Docker build.
`pyproject.toml`: ruff config (line length 120, ignores E501, B905, E741) and pytest testpaths.

### scripts/check_provider.py
Reads `.env`, makes one chat call and one embedding call, prints latency and vector shape, and prints the
provider's raw error body on failure. Run this before anything else when setting up a real provider.

### cli.py
`ingest <path> --source <id> --brand <b>`, `verify "<claim>" --brand <b>`, and `demo` which loads the
sample corpus and verifies three claims with printed citations.

## 7. The evaluation story

Offline baseline (mock LLM, hashed embeddings, lexical reranker), from `python -m eval.run_eval`:

| Metric | Value |
|---|---|
| recall@8 | 1.00 |
| citation_faithfulness | 0.925 |
| binary_accuracy | 0.75 |
| verdict_accuracy | 0.575 |
| macro_f1 | 0.514 |

Be honest about what these mean. The corpus is two short documents, about 12 chunks total, so recall@8 of
1.0 is easy. Citation faithfulness is a coarse source-level check. Verdict accuracy of 57.5% is a keyword
heuristic and is expected to be mediocre; its job is to be stable so CI can gate on retrieval and citation
metrics. The production numbers come from running with a real LLM (Gemini) and `--judge`, and those are the
numbers to quote.

## 8. Known limitations and how to talk about them

- Small corpus. Fix: ingest three or four real public sustainability reports (they are PDFs, `loader.py`
  handles them) and re-run. Recall will drop and become meaningful.
- Mock verifier is heuristic. It is not the product; the real LLM is.
- Rules are regex. They will have false positives on unusual phrasing. They are meant as a first-pass
  screen with the LLM handling nuance, and they are individually testable.
- No claim extraction from web pages yet. Claims are supplied directly.
- Cross-encoder reranker is optional; the lexical fallback is weaker. Install `sentence-transformers` for
  production quality.

## 9. Extension points, in order of value

1. Real corpus and real eval numbers (see Section 11).
2. Hybrid retrieval: add BM25 (rank_bm25) alongside vector search and merge with reciprocal rank fusion.
   Numeric claims ("62%") often match better lexically.
3. Claim spotter: given a product page URL, extract candidate environmental claims with the LLM, then
   verify each. This turns ThreadCheck from a single-claim API into a page auditor.
4. Per-rule precision and recall: label which rules should fire for each of the 40 claims and score them.
5. Streamlit review UI: paste a claim, see verdict, citations highlighted, and rule flags.
6. Query rewriting: generate two or three paraphrases of the claim, retrieve for each, union the results.

## 10. Running it

### Offline, no key, two minutes
```bash
pip install -r requirements-dev.txt
python cli.py demo
pytest -q
ruff check .
python -m eval.run_eval
```

### With Gemini (free tier is enough)
1. Get a key at aistudio.google.com/apikey.
2. `cp .env.example .env`, paste the key into `LLM_API_KEY`. The Gemini block is already the active preset:
   ```
   LLM_PROVIDER=openai
   LLM_BASE_URL=https://generativelanguage.googleapis.com/v1beta/openai
   LLM_MODEL=gemini-2.5-flash
   EMBED_MODEL=gemini-embedding-001
   EMBED_DIM=768
   ```
3. For a first run without Docker set `VECTOR_STORE=memory`.
4. `python scripts/check_provider.py`. Expect `chat OK` and `embeddings OK shape=(2, 768)`. If it reports
   a different dimension, set `EMBED_DIM` to that value.
5. `python -m eval.run_eval --judge`. Read `eval/results/summary-*.json`.
6. Free-tier rate limits are per minute. If you see 429s, wait a minute and re-run; everything is idempotent.

### With pgvector
```bash
# set VECTOR_STORE=pgvector in .env
docker compose up --build
curl localhost:8000/health
curl -X POST localhost:8000/ingest -H 'content-type: application/json' \
  -d '{"source":"northloom-report-2025","brand":"Northloom","doc_type":"report","text":"..."}'
curl -X POST localhost:8000/verify -H 'content-type: application/json' \
  -d '{"claim":"Northloom is a carbon neutral brand.","brand":"Northloom"}'
```
Interactive docs at `localhost:8000/docs`.

## 11. Plan for today

Work through these in order. Each step has a clear done condition.

**Step 1: Run it offline (15 min).** Done when `python cli.py demo` prints three verdicts with citations,
`pytest -q` shows 22 passed, `ruff check .` is clean.

**Step 2: Read the core (45 min).** Read `app/graph/pipeline.py`, `app/rules/green_claims.py`,
`app/retrieval/store.py`, `eval/run_eval.py`. Done when you can explain each of the six nodes, name the two
guardrails, and say why brand scoping exists.

**Step 3: GitHub (15 min).** Create a public repo named `threadcheck` under your account. `git init`,
commit, push. Confirm `.env` is not in the commit. Done when the Actions tab shows a green run.

**Step 4: Gemini (20 min).** Follow Section 10. Done when `check_provider.py` passes and
`python -m eval.run_eval --judge` writes a summary.

**Step 5: Record real numbers (10 min).** Replace the "Offline baseline" table in README.md with a second
table titled "Gemini 2.5 Flash" showing the real values. Commit.

**Step 6: One real report (45 min, optional but strong).** Download one public brand sustainability report
PDF. `python cli.py ingest report.pdf --source <brand>-report-2025 --brand <Brand>`. Pick three real
marketing claims from that brand's website and verify them via the CLI. Save the outputs to
`docs/real_world_examples.md`. This gives you a concrete interview story.

**Step 7: Resume (5 min).** Add the GitHub link to the ThreadCheck heading. If the Gemini numbers are
strong, add citation faithfulness and binary accuracy to the second bullet.

## 12. Interview preparation

Questions you should expect, and the shape of a good answer.

**"Walk me through the architecture."** Six-node LangGraph pipeline. Extract normalises and types the
claim. Retrieve does brand-scoped cosine search over pgvector. Rerank uses a cross-encoder to pick the top
four. Rule check is a deterministic tool with six regulatory patterns. Verify is the LLM auditor working
only from the evidence. Finalize applies guardrails in code.

**"Why put the guardrail in code instead of the prompt?"** Prompts are probabilistic; a compliance guarantee
cannot be. The guardrail is deterministic, tested, and survives model swaps and prompt edits.

**"Why rerank at all?"** Bi-encoder similarity is coarse and topic-level. A cross-encoder reads the claim
and the passage together and catches that "recycled mailer bags" is not "recycled polyester outerwear".
It is cheap at top-8.

**"How do you evaluate it?"** Forty labelled claims with the correct evidence source. Retrieval recall,
citation faithfulness at the source level, four-class accuracy and macro-F1, binary supported-vs-not
accuracy, latency percentiles, and an LLM-as-judge check that the rationale is grounded only in the cited
text. The retrieval and citation metrics are gated in CI so a regression fails the build.

**"What are the weaknesses?"** Small corpus, regex rules with false positives, no claim extraction from
web pages, the mock verifier is only a baseline. Say these before being asked.

**"What would you do next?"** Hybrid BM25 plus vector retrieval, a claim spotter for product pages, per-rule
precision and recall, a review UI.

**"Why this problem?"** Greenwashing is a live regulatory issue, the evidence is public, and it is a domain
where "the model said so" is not acceptable. It forced me to design for auditability, which is the property
I think matters most in applied LLM systems.
