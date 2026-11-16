"""Evaluation harness.

Metrics:
  * verdict accuracy and macro-F1 over 4 classes
  * binary accuracy (supported vs not-supported), the metric that matters for a greenwashing screen
  * recall@k: did retrieval surface a chunk from the labelled evidence source
  * citation faithfulness: every cited chunk must come from the labelled evidence source
  * optional LLM-as-judge faithfulness of the rationale (--judge)

Usage:
  python -m eval.run_eval                 # mock provider, in-memory store
  LLM_PROVIDER=openai python -m eval.run_eval --judge
"""
from __future__ import annotations

import argparse
import json
import time
from collections import Counter, defaultdict
from pathlib import Path

from app.config import get_settings
from app.graph.prompts import JUDGE_SYSTEM
from app.ingest.loader import load_document
from app.providers.llm import get_llm
from app.service import ThreadCheckService

ROOT = Path(__file__).resolve().parent.parent
SAMPLES = ROOT / "data" / "samples"
LABELS = ["supported", "unsupported", "misleading", "insufficient_evidence"]


def load_corpus(svc: ThreadCheckService) -> None:
    svc.ingest("northloom-report-2025", load_document(SAMPLES / "northloom_report_2025.md"), "Northloom", "report")
    svc.ingest("veridian-report-2025", load_document(SAMPLES / "veridian_report_2025.md"), "Veridian", "report")
    svc.ingest("green-claims-guidance", load_document(SAMPLES / "green_claims_guidance_summary.md"), None, "regulation")


def macro_f1(y_true, y_pred):
    f1s = []
    for lbl in LABELS:
        tp = sum(1 for t, p in zip(y_true, y_pred) if t == lbl and p == lbl)
        fp = sum(1 for t, p in zip(y_true, y_pred) if t != lbl and p == lbl)
        fn = sum(1 for t, p in zip(y_true, y_pred) if t == lbl and p != lbl)
        if tp + fp + fn == 0:
            continue
        prec = tp / (tp + fp) if tp + fp else 0.0
        rec = tp / (tp + fn) if tp + fn else 0.0
        f1s.append(2 * prec * rec / (prec + rec) if prec + rec else 0.0)
    return sum(f1s) / len(f1s) if f1s else 0.0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--claims", default=str(ROOT / "eval" / "claims_labelled.jsonl"))
    ap.add_argument("--judge", action="store_true", help="run LLM-as-judge on rationales")
    ap.add_argument("--out", default=str(ROOT / "eval" / "results"))
    args = ap.parse_args()

    settings = get_settings()
    svc = ThreadCheckService(settings)
    load_corpus(svc)
    judge = get_llm(settings) if args.judge else None

    rows = [json.loads(line) for line in open(args.claims) if line.strip()]
    y_true, y_pred, latencies = [], [], []
    recall_hits = faithful_citations = total_citations = 0
    judge_scores = []
    per_class = defaultdict(Counter)
    records = []

    for r in rows:
        t0 = time.perf_counter()
        st = svc.verifier.run(r["claim"], r["brand"])
        latencies.append(time.perf_counter() - t0)

        pred = st["verdict"]
        y_true.append(r["label"])
        y_pred.append(pred)
        per_class[r["label"]][pred] += 1

        cand_sources = {c["source"] for c in st.get("candidates", [])}
        recall_hits += r["evidence_source"] in cand_sources

        by_id = {e["chunk_id"]: e for e in st.get("evidence", [])}
        for cid in st.get("citation_ids", []):
            total_citations += 1
            faithful_citations += by_id.get(cid, {}).get("source") == r["evidence_source"]

        if judge and st.get("citation_ids"):
            ev = "\n".join(by_id[c]["text"] for c in st["citation_ids"] if c in by_id)
            out = judge.complete_json(JUDGE_SYSTEM, f"RATIONALE: {st['rationale']}\n\nCITED EVIDENCE:\n{ev}")
            judge_scores.append(float(out.get("score", 0.0)))

        records.append({"id": r["id"], "claim": r["claim"], "label": r["label"], "pred": pred,
                        "confidence": st["confidence"], "flags": [f["rule_id"] for f in st.get("rule_flags", [])],
                        "citations": st.get("citation_ids", []), "rationale": st.get("rationale", "")})

    n = len(rows)
    acc = sum(t == p for t, p in zip(y_true, y_pred)) / n
    bin_acc = sum((t == "supported") == (p == "supported") for t, p in zip(y_true, y_pred)) / n
    lat_sorted = sorted(latencies)
    summary = {
        "n_claims": n,
        "provider": settings.llm_provider,
        "verdict_accuracy": round(acc, 3),
        "macro_f1": round(macro_f1(y_true, y_pred), 3),
        "binary_accuracy_supported_vs_not": round(bin_acc, 3),
        f"recall@{settings.top_k}": round(recall_hits / n, 3),
        "citation_faithfulness": round(faithful_citations / total_citations, 3) if total_citations else None,
        "judge_faithfulness_mean": round(sum(judge_scores) / len(judge_scores), 3) if judge_scores else None,
        "latency_p50_s": round(lat_sorted[n // 2], 3),
        "latency_p95_s": round(lat_sorted[int(n * 0.95) - 1], 3),
        "confusion": {k: dict(v) for k, v in per_class.items()},
    }

    Path(args.out).mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    (Path(args.out) / f"summary-{stamp}.json").write_text(json.dumps(summary, indent=2))
    (Path(args.out) / f"records-{stamp}.jsonl").write_text("\n".join(json.dumps(x) for x in records))
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
