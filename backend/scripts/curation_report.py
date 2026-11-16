"""Turn the unknown-ingredient log from real traffic into a ranked review sheet.

Usage:
    python scripts/curation_report.py            # top 100 -> data/curation_queue.csv
    python scripts/curation_report.py --limit 300

Open the CSV in Excel and fill the 'action' column for each row:
    alias   it's another spelling of a known ingredient; put the known name in 'alias_of'
    new     a real ingredient we lack; fill entry_name (optional), plain, job, category,
            supports (e.g. "brightening:2; hydration:1"), flags (e.g. "fragrance_allergen"),
            marker / low_dose_ok / rinse_ok (yes/no, blank = default), spotlight (e.g. "kumkumadi")
    ignore  junk from scraping (marketing text, broken tokens); never logged again
    (blank) skip for now

Then run:  python scripts/apply_curation.py
"""
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from engine.curation import QUEUE_NAME, build_queue, load_log, save_log, write_queue  # noqa: E402
from engine.knowledge import DATA_DIR, KnowledgeBase  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--limit", type=int, default=100)
    ap.add_argument("--data-dir", type=Path, default=DATA_DIR)
    args = ap.parse_args()

    kb = KnowledgeBase(args.data_dir)
    log = load_log(args.data_dir)
    rows, resolved = build_queue(kb, log, args.limit)

    if resolved:  # learned since they were logged; drop them from the to-do list
        for key in resolved:
            log.pop(key, None)
        save_log(log, args.data_dir)

    path = args.data_dir / QUEUE_NAME
    write_queue(rows, path)
    total_hits = sum(v["count"] for v in log.values())
    covered = sum(int(r["count"]) for r in rows)
    print(f"{len(log)} unknown ingredients in the log ({total_hits} sightings).")
    if resolved:
        print(f"{len(resolved)} were already resolved by the current knowledge base and were removed.")
    print(f"Wrote the top {len(rows)} to {path}; curating them covers "
          f"{covered}/{total_hits or 1} sightings ({100 * covered // (total_hits or 1)}%).")
    prefilled = sum(1 for r in rows if r["action"])
    if prefilled:
        print(f"{prefilled} rows have a suggested action pre-filled; check them before applying.")


if __name__ == "__main__":
    main()
