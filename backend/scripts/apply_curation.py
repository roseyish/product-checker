"""Merge a reviewed data/curation_queue.csv into the knowledge base.

Every row is validated first (claim tags, flags, categories, word limits). If any row is
wrong, nothing is written and the errors are listed, so a half-applied sheet can't happen.

Usage:
    python scripts/apply_curation.py              # apply data/curation_queue.csv
    python scripts/apply_curation.py --dry-run    # validate only
    python scripts/apply_curation.py path/to/other.csv
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from engine.curation import QUEUE_NAME, apply_queue, read_queue  # noqa: E402
from engine.knowledge import DATA_DIR  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("csv", nargs="?", type=Path)
    ap.add_argument("--data-dir", type=Path, default=DATA_DIR)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    path = args.csv or args.data_dir / QUEUE_NAME
    if not path.exists():
        raise SystemExit(f"{path} not found. Run scripts/curation_report.py first.")
    summary = apply_queue(read_queue(path), args.data_dir, dry_run=args.dry_run)

    if summary["errors"]:
        print("Nothing was applied. Fix these rows and run again:")
        for e in summary["errors"]:
            print("  -", e)
        raise SystemExit(1)
    verb = "Would apply" if args.dry_run else "Applied"
    print(f"{verb}: {summary['new']} new, {summary['alias']} aliases, {summary['ignore']} ignored, "
          f"{summary['skipped']} skipped.")
    if not args.dry_run:
        print("Restart the backend to load the updated knowledge base.")


if __name__ == "__main__":
    main()
