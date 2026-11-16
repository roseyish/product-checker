"""Sync the EU CosIng database into data/cosing.json.

CosIng (European Commission, DG GROW) lists ~33,600 cosmetic ingredients with their official
INCI names, functions, CAS/EC numbers, plus ~2,400 regulated substances from the Annexes of
Regulation (EC) 1223/2009:
  II  prohibited          III restricted (max concentration, conditions)
  IV  allowed colorants   V   allowed preservatives   VI  allowed UV filters

Licence: European Commission reuse policy (Decision 2011/833/EU, CC BY 4.0).
Attribution: "Source: European Commission, CosIng database". CosIng itself has no legal
value; the legal text is Regulation (EC) 1223/2009 on EUR-Lex.

The API used here is the one the CosIng website itself calls. It is not officially
documented, so raw pages are cached under data/raw/cosing/ and the built file is committed:
the engine never needs the network at runtime. Re-run monthly.

Usage:
    python scripts/sync_cosing.py             # download (uses cache if fresh) and build
    python scripts/sync_cosing.py --refresh   # ignore the cache and re-download everything
    python scripts/sync_cosing.py --offline   # rebuild from the cache only
"""
import argparse
import datetime as dt
import json
import re
import sys
import time
import urllib.error
import urllib.request
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from engine.knowledge import DATA_DIR, norm  # noqa: E402

# Public key embedded in the CosIng web app config
# (https://ec.europa.eu/growth/tools-databases/cosing/assets/env-json-config.json).
API = "https://webgate.ec.europa.eu/es/search-api/rest/search"
API_KEY = "285a77fd-1257-4271-8507-f0c6b2961203"
PAGE_SIZE = 200          # the API caps pages at 200
PAUSE_SECONDS = 0.5      # be polite to the EC servers
CACHE_DAYS = 30

RAW_DIR = DATA_DIR / "raw" / "cosing"
OUT = DATA_DIR / "cosing.json"


def build_query(item_type: str, prefix: str = "") -> dict:
    """All records of a type, optionally only those whose substanceId starts with `prefix`.

    The search backend refuses to page past 10,000 hits, so big result sets are split by
    id prefix. substanceId is a keyword field, so ranges compare as text: "7" <= id < "8"
    selects every id starting with 7.
    """
    must = [{"term": {"itemType": item_type}}]
    if prefix:
        bounds = {"gte": prefix}
        # Next prefix in text order: "7" -> "8", "19" -> "2"; all nines ("9", "99") has no upper bound.
        # (Using ":" after "9" makes the backend silently return nothing.)
        stem = prefix.rstrip("9")
        if stem:
            bounds["lt"] = stem[:-1] + str(int(stem[-1]) + 1)
        must.append({"range": {"substanceId": bounds}})
    return {"bool": {"must": must}}


def fetch_page(query: dict, page: int, page_size: int = PAGE_SIZE, retries: int = 4) -> dict:
    boundary = uuid.uuid4().hex
    query = json.dumps(query)
    body = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"query\"\r\n"
            f"Content-Type: application/json\r\n\r\n{query}\r\n--{boundary}--\r\n").encode()
    url = f"{API}?apiKey={API_KEY}&text=*&pageSize={page_size}&pageNumber={page}"
    req = urllib.request.Request(url, data=body, method="POST", headers={
        "Content-Type": f"multipart/form-data; boundary={boundary}",
        "User-Agent": "product-checker-kb-sync/1.0",
    })
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=120) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
            if attempt == retries - 1:
                raise
            wait = 5 * (attempt + 1)
            print(f"  page {page}: {e}; retrying in {wait}s")
            time.sleep(wait)


MAX_WINDOW = 9800  # stay under the backend's 10,000-hit paging limit


def count(query: dict) -> int:
    return fetch_page(query, 1, page_size=1).get("totalResults") or 0


def partitions(item_type: str, prefix: str = "") -> list:
    """Split the id space by prefix until every chunk can be paged through completely."""
    n = count(build_query(item_type, prefix))
    time.sleep(PAUSE_SECONDS)
    if n <= MAX_WINDOW:
        return [(prefix, n)] if n else []
    print(f"  {item_type}: prefix '{prefix or '*'}' has {n:,} records, splitting")
    # An id equal to the prefix itself sorts before prefix+"0"; ids are 5+ digits, so skip it
    return [p for d in "0123456789" for p in partitions(item_type, prefix + d)]


def fetch_partition(item_type: str, prefix: str, expected: int, folder: Path, use_cache: bool) -> list:
    records, page = [], 1
    while len(records) < expected:
        path = folder / f"{prefix or 'all'}_{page:04d}.json"
        if use_cache and path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
        else:
            for attempt in range(3):  # an empty page before the end is a hiccup, not the end
                data = fetch_page(build_query(item_type, prefix), page)
                if data.get("results"):
                    break
                time.sleep(5 * (attempt + 1))
            if not data.get("results"):
                raise SystemExit(f"{item_type} prefix {prefix}: page {page} stayed empty at "
                                 f"{len(records)}/{expected}. Re-run later; finished pages are cached.")
            path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            time.sleep(PAUSE_SECONDS)
        records += [r["metadata"] for r in data["results"]]
        page += 1
    return records


def download(item_type: str, refresh: bool, offline: bool) -> tuple:
    """All records of one type, cached as raw JSON pages. Returns (records, expected total)."""
    folder = RAW_DIR / item_type
    folder.mkdir(parents=True, exist_ok=True)
    plan_path = folder / "plan.json"
    fresh = plan_path.exists() and plan_path.stat().st_mtime > time.time() - CACHE_DAYS * 86400
    use_cache = offline or (fresh and not refresh)

    if use_cache and plan_path.exists():
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
    elif offline:
        raise SystemExit(f"No cached {item_type} data. Run without --offline first.")
    else:
        for old in folder.glob("*.json"):
            old.unlink()
        plan = partitions(item_type)
        plan_path.write_text(json.dumps(plan), encoding="utf-8")

    total = sum(n for _, n in plan)
    records = []
    for prefix, n in plan:
        records += fetch_partition(item_type, prefix, n, folder, use_cache)
        print(f"  {item_type}: {len(records):,}/{total:,}")
    # Partitions can't overlap, but dedupe anyway in case the backend shifts mid-sync
    unique = {(r.get("substanceId") or [""])[0] + "|" + (r.get("inciName") or [""])[0]: r for r in records}
    return list(unique.values()), total


def first(meta: dict, key: str) -> str:
    vals = [v for v in meta.get(key, []) if v and v != "<empty>"]
    return vals[0].strip() if vals else ""


def split_ids(text: str) -> list:
    """'97660-24-7 / 8038-93-5' -> ['97660-24-7', '8038-93-5']"""
    return [p.strip() for p in re.split(r"[/,;]", text or "") if re.search(r"\d", p)]


def clean(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip()


# Both the 2009 wording ("must be indicated") and the Reg 2023/1545 wording ("shall be indicated",
# sometimes "shall be indicated as 'Cedrus Atlantica Oil/Extract'") mark a labelled fragrance allergen.
ALLERGEN_RE = re.compile(r"(must|shall) be indicated (as .{0,80}? )?in the list of ingredients", re.I)

# Annex II entries like "Petrolatum, except if the full refining history is known..." or
# "Margosa, ext. [from the kernels ... extracted with organic solvents]" ban only a grade or a
# process, not every product carrying that INCI name. Those are kept but marked conditional.
CONDITIONAL_RE = re.compile(r"\b(except|unless|if|when|where)\b|\[from|extracted with|released from", re.I)


def build(ingredients: list, substances: list) -> dict:
    # Regulated substances, keyed by the ingredient id they point to and by CAS number
    regs_by_id, regs_by_cas, regs_by_name = {}, {}, {}
    for m in substances:
        if first(m, "status") and first(m, "status") != "Active":
            continue
        reg = {
            "annex": first(m, "annexNo"),
            "ref": first(m, "refNo"),
            "substance": clean(first(m, "inciName") or first(m, "chemicalName")),
            "max": clean(first(m, "maximumConcentration")),
            "where": clean(first(m, "productTypeBodyParts")),
            "conditions": clean(first(m, "wordingOfConditions") or first(m, "other")),
            "label_allergen": bool(ALLERGEN_RE.search(" ".join(m.get("other", []) + m.get("wordingOfConditions", [])))),
            "sccs": [u for u in m.get("sccsOpinionUrls", []) if u and u != "<empty>"][:3],
        }
        if reg["annex"] == "II" and CONDITIONAL_RE.search(reg["substance"] + " " + reg["conditions"]):
            reg["conditional"] = True
        reg = {k: v for k, v in reg.items() if v not in ("", [], False)}
        for iid in m.get("identifiedIngredient", []):
            regs_by_id.setdefault(iid, []).append(reg)
        for cas in split_ids(first(m, "casNo")):
            regs_by_cas.setdefault(cas, []).append(reg)
        # Common INCI names, e.g. "Cedrus Atlantica Bark Extract; Cedrus Atlantica Bark Oil"
        for names in m.get("nameOfCommonIngredientsGlossary", []):
            for name in re.split(r";", names or ""):
                if name.strip() and name.strip() != "<empty>":
                    regs_by_name.setdefault(norm(name), []).append(reg)

    out, joined = {}, {"id": 0, "cas": 0, "name": 0}
    for m in ingredients:
        inci = clean(first(m, "inciName"))
        if not inci:
            continue
        cas = split_ids(first(m, "casNo"))
        regs = regs_by_id.get(first(m, "substanceId"), [])
        how = "id"
        if not regs:
            regs, how = [r for c in cas for r in regs_by_cas.get(c, [])], "cas"
        if not regs:
            regs, how = regs_by_name.get(norm(inci), []), "name"
        if regs:
            joined[how] += 1
        unique_regs = list({json.dumps(r, sort_keys=True): r for r in regs}.values())
        entry = {
            "inci": inci,
            "functions": [f.strip().upper() for f in m.get("functionName", []) if f.strip()],
            "cas": cas,
            "ec": split_ids(first(m, "ecNo")),
            "status": first(m, "status"),
        }
        for key, field in (("usa", "inciUsaName"), ("inn", "innName"), ("ph_eur", "phEurName")):
            if first(m, field):
                entry[key] = clean(first(m, field))
        if first(m, "perfuming") == "Y":
            entry["perfuming"] = True
        if unique_regs:
            entry["regulation"] = unique_regs
        entry = {k: v for k, v in entry.items() if v not in ("", [])}
        key = norm(inci)
        # Keep the active record when CosIng has duplicates of the same name
        if key not in out or out[key].get("status") != "Active":
            out[key] = entry
    return out, joined


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--refresh", action="store_true")
    ap.add_argument("--offline", action="store_true")
    args = ap.parse_args()

    print("Downloading CosIng ingredients...")
    ingredients, ing_total = download("ingredient", args.refresh, args.offline)
    print("Downloading CosIng regulated substances (Annexes II-VI)...")
    substances, sub_total = download("substance", args.refresh, args.offline)

    print(f"Fetched {len(ingredients):,} ingredient records (expected {ing_total:,}; "
          f"the difference is duplicate records) and {len(substances):,} substances.")
    entries, joined = build(ingredients, substances)

    payload = {
        "source": "European Commission, CosIng database (https://ec.europa.eu/growth/tools-databases/cosing/)",
        "licence": "CC BY 4.0 (Commission Decision 2011/833/EU). CosIng has no legal value; see Regulation (EC) 1223/2009.",
        "synced": dt.date.today().isoformat(),
        "counts": {"ingredients": len(entries), "substances": len(substances)},
        "ingredients": dict(sorted(entries.items())),
    }
    OUT.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")

    with_reg = sum(1 for e in entries.values() if "regulation" in e)
    annexes = {}
    for e in entries.values():
        for r in e.get("regulation", []):
            annexes[r.get("annex", "?")] = annexes.get(r.get("annex", "?"), 0) + 1
    allergens = sum(1 for e in entries.values() if any(r.get("label_allergen") for r in e.get("regulation", [])))
    print(f"\nWrote {len(entries):,} ingredients to {OUT} ({OUT.stat().st_size / 1e6:.1f} MB)")
    print(f"  {with_reg:,} linked to a regulated substance (by CosIng id {joined['id']:,}, "
          f"CAS {joined['cas']:,}, name {joined['name']:,})")
    print(f"  links per annex: {dict(sorted(annexes.items()))}")
    print(f"  {allergens} labelled fragrance allergens")


if __name__ == "__main__":
    main()
