"""Traffic-driven curation: log unknown ingredients, rank them, and merge reviewed entries back.

Loop:
  1. The API calls record_unknown() for every ingredient the knowledge base didn't recognise.
  2. scripts/curation_report.py turns the log into data/curation_queue.csv, most frequent first,
     with suggestions pre-filled (likely typo of a known name, CosIng function).
  3. A reviewer fills in the 'action' column (and entry fields for new ingredients).
  4. scripts/apply_curation.py validates the rows, merges them into ingredients.json,
     and clears the handled names from the log.
"""
import csv
import datetime as dt
import difflib
import json
import threading
from pathlib import Path

from .knowledge import DATA_DIR, KnowledgeBase, norm

LOG_NAME = "unknown_ingredients.json"
IGNORE_NAME = "curation_ignore.json"
QUEUE_NAME = "curation_queue.csv"

QUEUE_COLUMNS = [
    "name", "count", "last_seen", "example_product", "suggestion", "cosing_functions",
    "action", "alias_of", "entry_name", "plain", "job", "category",
    "supports", "flags", "marker", "low_dose_ok", "rinse_ok", "spotlight",
]
ACTIONS = {"alias", "new", "ignore", ""}
CATEGORIES = {
    "solvent", "humectant", "emollient", "oil", "occlusive", "silicone", "emulsifier", "thickener",
    "surfactant", "soap", "preservative", "chelator", "ph_adjuster", "fragrance", "essential_oil",
    "colorant", "uv_filter", "active", "botanical", "conditioner", "protein", "absorbent",
    "antioxidant", "exfoliant", "other",
}

_lock = threading.Lock()


def _today() -> str:
    return dt.date.today().isoformat()


def _read_json(path: Path, default):
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return default


def load_log(data_dir: Path = DATA_DIR) -> dict:
    """{normalized name: {count, first_seen, last_seen, example, raw}}. Upgrades the old {name: count} format."""
    log = _read_json(data_dir / LOG_NAME, {})
    for name, value in list(log.items()):
        if isinstance(value, int):
            log[name] = {"count": value, "first_seen": "", "last_seen": "", "example": "", "raw": name}
    return log


def save_log(log: dict, data_dir: Path = DATA_DIR):
    ordered = dict(sorted(log.items(), key=lambda kv: -kv[1]["count"]))
    (data_dir / LOG_NAME).write_text(json.dumps(ordered, indent=1, ensure_ascii=False), encoding="utf-8")


def load_ignore(data_dir: Path = DATA_DIR) -> set:
    return set(_read_json(data_dir / IGNORE_NAME, []))


def record_unknown(names: list, example: str = "", data_dir: Path = DATA_DIR):
    """Count unrecognised ingredients. Thread-safe; called once per /analyze request."""
    if not names:
        return
    ignore = load_ignore(data_dir)
    with _lock:
        log = load_log(data_dir)
        today = _today()
        for raw in names:
            key = norm(raw)
            if not key or key in ignore:
                continue
            row = log.setdefault(key, {"count": 0, "first_seen": today, "last_seen": today, "example": "", "raw": raw})
            row["count"] += 1
            row["last_seen"] = today
            if example and not row["example"]:
                row["example"] = example[:100]
        save_log(log, data_dir)


# ---------- report ----------

def build_queue(kb: KnowledgeBase, log: dict, limit: int = 100) -> tuple:
    """Rank unknowns by frequency and pre-fill suggestions. Returns (rows, now_resolved_names)."""
    rows, resolved = [], []
    for key, info in sorted(log.items(), key=lambda kv: -kv[1]["count"]):
        entry, how = kb.lookup(info.get("raw") or key)
        if entry is not None:
            resolved.append(key)          # the KB learned it since it was logged
            continue
        if len(rows) >= limit:
            continue
        row = dict.fromkeys(QUEUE_COLUMNS, "")
        row.update(name=info.get("raw") or key, count=info["count"], last_seen=info["last_seen"],
                   example_product=info["example"])
        close = difflib.get_close_matches(key, kb.keys, n=1, cutoff=0.8)
        if close:
            target = kb.index[close[0]]["name"]
            row["suggestion"] = f"looks like {target}"
            row["action"], row["alias_of"] = "alias", target
        cos = kb.cosing.get(key)
        if cos:
            row["cosing_functions"] = ", ".join(cos.get("functions", []))
        if len(key.split()) > 6 or any(w in key for w in ("http", "please", "refer", "contains", "pack")):
            row["suggestion"] = row["suggestion"] or "probably scraped marketing text"
            row["action"] = row["action"] or "ignore"
        rows.append(row)
    return rows, resolved


def write_queue(rows: list, path: Path):
    # utf-8-sig so Excel opens it with the right encoding
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        w = csv.DictWriter(f, fieldnames=QUEUE_COLUMNS)
        w.writeheader()
        w.writerows(rows)


def read_queue(path: Path) -> list:
    with path.open(newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


# ---------- apply ----------

def _parse_supports(text: str, valid_tags: set) -> dict:
    """'brightening:3; hydration:1' -> {'brightening': 3, 'hydration': 1}"""
    out = {}
    for part in filter(None, (p.strip() for p in text.replace(",", ";").split(";"))):
        tag, _, level = part.partition(":")
        tag = tag.strip()
        if tag not in valid_tags:
            raise ValueError(f"unknown claim tag '{tag}' (valid: {', '.join(sorted(valid_tags))})")
        if level.strip() not in ("1", "2", "3"):
            raise ValueError(f"strength for '{tag}' must be 1, 2 or 3")
        out[tag] = int(level)
    return out


def _parse_list(text: str) -> list:
    return [p.strip() for p in text.replace(",", ";").split(";") if p.strip()]


def _parse_bool(text: str, field: str):
    t = text.strip().lower()
    if t in ("", "default"):
        return None
    if t in ("yes", "y", "true", "1"):
        return True
    if t in ("no", "n", "false", "0"):
        return False
    raise ValueError(f"{field} must be yes/no")


def validate_row(row: dict, kb: KnowledgeBase, rules: dict, new_names: set) -> dict:
    """Turn one reviewed CSV row into a change. Raises ValueError with a readable message."""
    action = row.get("action", "").strip().lower()
    if action not in ACTIONS:
        raise ValueError(f"action must be alias, new, ignore or blank, got '{action}'")
    if action in ("", "ignore"):
        return {"action": action}

    name = row["name"].strip()
    if action == "alias":
        target = row.get("alias_of", "").strip()
        entry = kb.index.get(norm(target))
        if entry is None and target not in new_names:
            raise ValueError(f"alias_of '{target}' is not a known ingredient name")
        return {"action": "alias", "alias": name, "target": entry["name"] if entry else target}

    entry_name = row.get("entry_name", "").strip() or name
    if norm(entry_name) in kb.index:
        raise ValueError(f"'{entry_name}' already exists; use action=alias instead")
    category = row.get("category", "").strip()
    if category not in CATEGORIES:
        raise ValueError(f"category must be one of: {', '.join(sorted(CATEGORIES))}")
    plain, job = row.get("plain", "").strip(), row.get("job", "").strip()
    if not plain or not job:
        raise ValueError("new entries need 'plain' and 'job'")
    if len(job.split()) > 10:
        raise ValueError("'job' must be 10 words or fewer (it is shown on the card)")
    flags = _parse_list(row.get("flags", ""))
    bad = [f for f in flags if f not in rules["flags"]]
    if bad:
        raise ValueError(f"unknown flag(s) {bad} (valid: {', '.join(sorted(rules['flags']))})")

    entry = {"name": entry_name}
    if norm(name) != norm(entry_name):
        entry["aliases"] = [name]
    entry.update(plain=plain, job=job, category=category)
    supports = _parse_supports(row.get("supports", ""), set(rules["benefits"]))
    if supports:
        entry["supports"] = supports
    if flags:
        entry["flags"] = flags
    for field in ("marker", "low_dose_ok", "rinse_ok"):
        value = _parse_bool(row.get(field, ""), field)
        if value is not None:
            entry[field] = value
    spotlight = [s.lower() for s in _parse_list(row.get("spotlight", ""))]
    if spotlight:
        entry["spotlight"] = spotlight
    return {"action": "new", "entry": entry}


def dump_ingredients(raw: dict) -> str:
    """Same layout as the hand-written file: one entry per line, so diffs stay reviewable."""
    def block(items):
        return ",\n".join("    " + json.dumps(i, ensure_ascii=False) for i in items)
    head = {k: v for k, v in raw.items() if k not in ("ingredients", "patterns")}
    lines = ["{"] + [f"  {json.dumps(k)}: {json.dumps(v, ensure_ascii=False)}," for k, v in head.items()]
    lines += ['  "ingredients": [', block(raw["ingredients"]), "  ],",
              '  "patterns": [', block(raw["patterns"]), "  ]", "}"]
    return "\n".join(lines) + "\n"


def apply_queue(rows: list, data_dir: Path = DATA_DIR, dry_run: bool = False) -> dict:
    """Validate every row first; write nothing unless all rows are valid."""
    kb = KnowledgeBase(data_dir)
    rules = json.loads((data_dir / "rules.json").read_text(encoding="utf-8"))
    new_names = {(r.get("entry_name") or r["name"]).strip() for r in rows if r.get("action", "").strip().lower() == "new"}

    changes, errors = [], []
    for i, row in enumerate(rows, start=2):  # row 1 is the header in Excel
        try:
            changes.append((row, validate_row(row, kb, rules, new_names)))
        except ValueError as e:
            errors.append(f"row {i} ({row.get('name', '')}): {e}")
    summary = {"new": 0, "alias": 0, "ignore": 0, "skipped": 0, "errors": errors}
    if errors:
        return summary

    raw = json.loads((data_dir / "ingredients.json").read_text(encoding="utf-8"))
    by_name = {norm(e["name"]): e for e in raw["ingredients"]}
    ignore = load_ignore(data_dir)
    handled = set()

    for row, change in changes:
        action = change["action"]
        if action == "new":
            raw["ingredients"].append(change["entry"])
            by_name[norm(change["entry"]["name"])] = change["entry"]
    for row, change in changes:
        action = change["action"]
        key = norm(row["name"])
        if action == "":
            summary["skipped"] += 1
            continue
        if action == "alias":
            target = by_name[norm(change["target"])]
            aliases = target.setdefault("aliases", [])
            if key not in {norm(a) for a in aliases}:
                aliases.append(change["alias"].lower())
        elif action == "ignore":
            ignore.add(key)
        summary[action] += 1
        handled.add(key)

    if dry_run:
        return summary
    raw["version"] = _today()
    (data_dir / "ingredients.json").write_text(dump_ingredients(raw), encoding="utf-8")
    (data_dir / IGNORE_NAME).write_text(json.dumps(sorted(ignore), indent=1, ensure_ascii=False), encoding="utf-8")
    with _lock:
        log = load_log(data_dir)
        for key in handled:
            log.pop(key, None)
        save_log(log, data_dir)
    return summary
