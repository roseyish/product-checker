"""Small CLI for demos.

  python cli.py ingest data/samples/northloom_report_2025.md --source northloom-report-2025 --brand Northloom
  python cli.py verify "Northloom is a carbon neutral brand." --brand Northloom
  python cli.py demo        # loads the sample corpus and verifies a few claims
"""
import argparse

from app.ingest.loader import load_document
from app.service import ThreadCheckService


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    i = sub.add_parser("ingest")
    i.add_argument("path")
    i.add_argument("--source", required=True)
    i.add_argument("--brand")
    i.add_argument("--doc-type", default="report")
    v = sub.add_parser("verify")
    v.add_argument("claim")
    v.add_argument("--brand")
    sub.add_parser("demo")
    a = ap.parse_args()

    svc = ThreadCheckService()
    if a.cmd == "ingest":
        print(f"indexed {svc.ingest(a.source, load_document(a.path), a.brand, a.doc_type)} chunks")
    elif a.cmd == "verify":
        print(svc.verify(a.claim, a.brand).model_dump_json(indent=2))
    else:
        from eval.run_eval import load_corpus
        load_corpus(svc)
        for claim, brand in [("Our outerwear is made with 100% recycled polyester.", "Northloom"),
                             ("Northloom is a carbon neutral brand.", "Northloom"),
                             ("100% of our wool is Responsible Wool Standard certified.", "Veridian")]:
            r = svc.verify(claim, brand)
            print(f"\n{claim}\n  -> {r.verdict.value} ({r.confidence})  flags={[f.rule_id for f in r.rule_flags]}")
            print(f"  {r.rationale}")
            for c in r.citations[:1]:
                print(f"  [{c.chunk_id}] {c.excerpt[:140]}...")


if __name__ == "__main__":
    main()
