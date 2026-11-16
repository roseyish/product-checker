"""Rule engine: turns a parsed ingredient list plus marketing text into the match card.

Everything here is deterministic. The same input always gives the same card, and
every verdict can be traced back to an entry in data/ingredients.json or data/rules.json.
"""
import json
import re
from dataclasses import dataclass, field

from .knowledge import DATA_DIR, get_kb, norm, split_ingredients, PERCENT_RE

RULES = json.loads((DATA_DIR / "rules.json").read_text(encoding="utf-8"))
LEAVE_ON_TAGS = set(RULES["leave_on_tags"])

# Score per claim verdict; truth_score is the average, so the same verdicts
# always produce the same number.
VERDICT_POINTS = {"holds up": 100, "partly true": 50, "doesn't hold up": 0}

# Score bands -> verdict word, so the headline word always agrees with the number.
VERDICT_WORD_BANDS = [
    (85, "Honest"),
    (65, "Mostly true"),
    (40, "Half-true"),
    (15, "Overhyped"),
    (0, "Pure hype"),
]

# Categories that never count as hero ingredients.
NON_HERO = {"solvent", "preservative", "chelator", "ph_adjuster", "fragrance", "colorant", "thickener", "emulsifier"}

MAX_CLAIMS = 6


@dataclass
class Item:
    raw: str              # as written in the list, tidied
    pos: int              # 0-based position
    entry: dict           # matched knowledge, or None
    how: str              # exact / fuzzy / pattern / cosing / None
    pct: float = None     # disclosed percentage, if any
    effective: bool = True  # above the 1% line, or works at low dose
    above_line: bool = True

    @property
    def name(self) -> str:
        return (self.entry or {}).get("name") or self.raw

    @property
    def curated(self) -> bool:
        return self.how in ("exact", "fuzzy")


@dataclass
class Support:
    item: Item
    raw: int        # strength at a normal dose in a leave-on product
    eff: int        # after position and wash-off penalties
    reason: str     # why it was downgraded: "", "low", "rinse"


@dataclass
class Product:
    items: list
    text: str
    domain: str
    type_name: str
    rinse_off: bool
    unknown: list = field(default_factory=list)


def compute_truth_score(claims: list) -> tuple:
    """Derive (truth_score, verdict_word) from the per-claim verdicts."""
    points = [VERDICT_POINTS[c["verdict"]] for c in claims if c.get("verdict") in VERDICT_POINTS]
    if not points:
        return 50, "No claims"
    score = round(sum(points) / len(points))
    for threshold, word in VERDICT_WORD_BANDS:
        if score >= threshold:
            return score, word
    return score, "Pure hype"


# ---------- parsing ----------

def parse_items(ingredients_text: str) -> list:
    kb = get_kb()
    items = []
    for pos, token in enumerate(split_ingredients(ingredients_text)):
        pct_match = PERCENT_RE.search(token)
        raw = re.sub(r"\s+", " ", PERCENT_RE.sub("", token)).strip(" *.()")
        entry, how = kb.lookup(token)
        items.append(Item(raw=raw, pos=pos, entry=entry, how=how,
                          pct=float(pct_match.group(1)) if pct_match else None))

    # The "1% line": everything after the first ingredient that is normally used at
    # 1% or less (preservatives, thickeners, fragrance) is present in small amounts.
    line = next((it.pos for it in items if it.entry and it.entry["marker"] and it.pos > 0), None)
    if line is None:
        line = max(6, round(len(items) * 0.6)) if len(items) > 8 else len(items)
    for it in items:
        it.above_line = it.pos < line or (it.pct is not None and it.pct >= 1)
        low_ok = bool(it.entry and it.entry["low_dose_ok"])
        it.effective = it.above_line or low_ok
    return items


def detect_product(items: list, text: str) -> tuple:
    """(domain, type name, rinse_off) from the title first, ingredients second."""
    lowered = text.lower()
    hair_words = re.search(r"\b(hair|scalp|shampoo|conditioner|dandruff)\b", lowered)
    for name, domain, rinse in sorted(RULES["product_types"], key=lambda t: -len(t[0])):
        if re.search(r"\b" + re.escape(name) + r"s?\b", lowered):
            if hair_words and domain == "skin" and name in ("serum", "gel", "cream", "lotion", "balm"):
                return "hair", "hair " + name, rinse
            return domain, name, rinse

    top = [it for it in items[:5] if it.entry]
    cats = {it.entry["category"] for it in top}
    domain = "hair" if hair_words else "skin"
    if any(it.entry and it.entry["category"] == "uv_filter" and it.above_line for it in items):
        return "skin", "sunscreen", False
    if cats & {"surfactant", "soap"}:
        return domain, "shampoo" if domain == "hair" else "cleanser", True
    if "conditioner" in cats:
        return "hair", "conditioner", True
    return domain, "hair product" if domain == "hair" else "skincare product", False


SOAP_ENTRY = {"plain": "soap made in the bottle", "job": "classic soap cleanser, high pH", "category": "soap",
              "supports": {"cleansing": 3, "oil_control": 1}, "flags": ["soap"], "marker": False,
              "low_dose_ok": False, "rinse_ok": True}


def build_product(ingredients_text: str, claims_text: str) -> Product:
    items = parse_items(ingredients_text)
    for it in soap_items(items):
        it.entry = dict(SOAP_ENTRY, name=it.entry["name"])
    domain, type_name, rinse_off = detect_product(items, claims_text)
    unknown = [it.raw for it in items if it.entry is None]
    return Product(items=items, text=claims_text.lower(), domain=domain,
                   type_name=type_name, rinse_off=rinse_off, unknown=unknown)


# ---------- evidence ----------

def supports_for(product: Product, tag: str) -> list:
    """Every ingredient that supports a claim tag, strongest first, with honest penalties applied."""
    out = []
    for it in product.items:
        if not it.entry:
            continue
        raw = it.entry["supports"].get(tag, 0)
        if raw <= 0:
            continue
        eff, reason = raw, ""
        if not it.effective:
            eff, reason = eff - 1, "low"
        if product.rinse_off and tag in LEAVE_ON_TAGS and not it.entry["rinse_ok"]:
            eff, reason = eff - 1, "rinse"
        out.append(Support(it, raw, max(0, eff), reason))
    out.sort(key=lambda s: (-s.eff, -s.raw, s.item.pos))
    return out


def strength_verdict(sups: list) -> str:
    """holds up: one strong, well-placed active, or two solid ones working together."""
    if not sups or sups[0].eff == 0:
        return "doesn't hold up"
    if sups[0].eff >= 3:
        return "holds up"
    if len(sups) > 1 and sups[1].eff >= 2 and sups[0].eff + sups[1].eff >= 4:
        return "holds up"
    return "partly true"


def flagged(product: Product, flag: str) -> list:
    """Items carrying a flag, honoring 'only_effective' (e.g. a drop of alcohol at the end is fine)."""
    spec = RULES["flags"][flag]
    hits = [it for it in product.items if it.entry and flag in it.entry["flags"]]
    if spec.get("only_effective"):
        hits = [it for it in hits if it.effective]
    return hits


def soap_items(items: list) -> list:
    """Lye + fatty acids near the top is a soap made in the bottle, even without 'sodium cocoate'."""
    top = items[:8]
    has_alkali = any(it.entry and it.entry["name"] in ("Potassium Hydroxide", "Sodium Hydroxide") for it in top)
    acids = [it for it in top[:6] if it.entry and it.entry["name"] in ("Myristic Acid", "Lauric Acid", "Stearic Acid", "Palmitic Acid")]
    return acids if has_alkali and acids else []


def all_flags(product: Product) -> dict:
    found = {}
    for flag in RULES["flags"]:
        hits = flagged(product, flag)
        if flag == "comedogenic" and product.domain == "hair":
            continue
        if hits:
            found[flag] = hits
    return found


def names(items: list, limit: int = 2) -> str:
    picked = [it.name for it in items[:limit]]
    return " and ".join(picked)


# ---------- claims ----------

def find_first(phrases: list, text: str):
    best = None
    for p in phrases:
        m = re.search(p, text)
        if m and (best is None or m.start() < best):
            best = m.start()
    return best


def detect_claims(product: Product) -> list:
    """(position in text, kind, key) for every distinct claim the marketing text makes."""
    text, found = product.text, []
    for key, spec in RULES["benefits"].items():
        if spec["domain"] not in ("any", product.domain):
            continue
        at = find_first(spec["phrases"], text)
        if at is not None:
            found.append((at, "benefit", key))
    for key, spec in RULES["free_from"].items():
        at = find_first(spec["phrases"], text)
        if at is not None:
            found.append((at, "free_from", key))
    for key, spec in RULES["special_claims"].items():
        at = find_first(spec["phrases"], text)
        if at is not None:
            found.append((at, "special", key))

    # "Vitamin C Serum": the title promises a specific ingredient.
    spots = []
    for word in sorted(get_kb().spotlights, key=len, reverse=True):
        m = re.search(r"\b" + re.escape(word) + r"\b", text)
        if m and not any(m.start() >= s and m.end() <= e for s, e, _ in spots):
            spots.append((m.start(), m.end(), word))
    found += [(s, "spotlight", w) for s, _, w in spots[:3]]

    # "Free from sulfates" also matches the oil-free phrase family etc.; keep the first of each key.
    if any(k == "mineral_oil_free" for _, _, k in found):
        found = [f for f in found if f[2] != "oil_free"]
    if any(k == "uva" for _, _, k in found) and not any(k == "sun" for _, _, k in found):
        found.append((min(a for a, _, k in found if k == "uva"), "benefit", "sun"))
    found.sort()
    return found[:MAX_CLAIMS]


def judge_benefit(product: Product, tag: str) -> dict:
    spec = RULES["benefits"][tag]
    short = spec["label"]
    sups = supports_for(product, tag)
    verdict = strength_verdict(sups)
    if verdict == "holds up":
        top = sups[0].item
        reality = f"Claims {short.lower()}, and it does, thanks to {top.name}, {top.entry['plain']}."
    elif verdict == "partly true":
        s = sups[0]
        if s.reason == "low":
            why = "sits too low in the list to do much"
        elif s.reason == "rinse":
            why = "washes off before it can work"
        else:
            why = f"({s.item.entry['plain']}) only helps mildly"
        reality = f"Claims {short.lower()}, but can't fully deliver: {s.item.name} {why}."
    else:
        if sups:
            why = "washes off too fast" if sups[0].reason == "rinse" else "is only a trace amount"
            reality = f"Claims {short.lower()}, but {sups[0].item.name} {why} to matter."
        else:
            reality = f"Claims {short.lower()}, but nothing in the list meaningfully does this."
    return {"claim_short": short, "verdict": verdict, "reality": reality}


def judge_free_from(product: Product, key: str) -> dict:
    spec = RULES["free_from"][key]
    rx = re.compile(spec["match"])
    label = spec["label"]
    hits = [it for it in product.items
            if rx.search(norm(it.raw)) or (it.entry and it.entry.get("name") and rx.search(norm(it.entry["name"])))]
    if key == "soap_free":
        hits += [it for it in flagged(product, "soap") if it not in hits]
    if hits:
        return {"claim_short": label, "verdict": "doesn't hold up",
                "reality": f"Claims {label.lower()}, but the list includes {names(hits)}."}
    soft = [it for f in spec.get("soft_flags", []) for it in flagged(product, f)]
    if soft:
        return {"claim_short": label, "verdict": "partly true",
                "reality": f"Claims {label.lower()}, but {names(soft)} still adds scent."}
    return {"claim_short": label, "verdict": "holds up",
            "reality": f"Claims {label.lower()}, and it is: no {spec['noun']} in the list."}


def judge_special(product: Product, key: str) -> dict:
    label = RULES["special_claims"][key]["label"]
    if key == "chemical_free":
        return {"claim_short": label, "verdict": "doesn't hold up",
                "reality": "Claims chemical-free, but every ingredient, water included, is a chemical."}

    if key == "natural":
        markers = [re.compile(p) for p in RULES["synthetic_markers"]]
        synth = [it for it in product.items
                 if any(m.search(norm(it.entry.get("name") or it.raw if it.entry else it.raw)) for m in markers)]
        if not synth:
            return {"claim_short": label, "verdict": "holds up",
                    "reality": "Claims a natural formula, and the list backs it up."}
        verdict = "partly true" if len(synth) <= 2 else "doesn't hold up"
        return {"claim_short": label, "verdict": verdict,
                "reality": f"Claims natural, but lists synthetics like {names(synth)}."}

    if key == "non_comedogenic":
        risky = [it for it in product.items if it.entry and "comedogenic" in it.entry["flags"]]
        high = [it for it in risky if it.above_line]
        if high:
            return {"claim_short": label, "verdict": "doesn't hold up",
                    "reality": f"Claims it won't clog pores, but {names(high)} can."}
        if risky:
            return {"claim_short": label, "verdict": "partly true",
                    "reality": f"Claims it won't clog pores; {names(risky)} is low, small risk."}
        return {"claim_short": label, "verdict": "holds up",
                "reality": "Claims it won't clog pores, and no known pore-cloggers are listed."}

    # gentle / sensitive skin
    flags = all_flags(product)
    strong = [it for f, hits in flags.items() if RULES["flags"][f]["strength"] == "strong" for it in hits]
    soft = [it for f, hits in flags.items() if RULES["flags"][f]["strength"] == "soft" for it in hits]
    if strong:
        return {"claim_short": label, "verdict": "doesn't hold up",
                "reality": f"Claims gentle, but {names(strong)} can irritate sensitive skin."}
    if soft:
        return {"claim_short": label, "verdict": "partly true",
                "reality": f"Claims gentle, but {names(soft)} may bother very sensitive skin."}
    return {"claim_short": label, "verdict": "holds up",
            "reality": "Claims gentle, and nothing in the list is a known irritant."}


def judge_spotlight(product: Product, word: str) -> dict:
    wanted = set(get_kb().spotlights[word])
    display = word.upper() if len(word) <= 3 else word.title()
    short = f"Made with {display}"
    present = [it for it in product.items if it.entry and it.entry.get("name") in wanted]
    if not present:
        return {"claim_short": short, "verdict": "doesn't hold up",
                "reality": f"Claims {display}, but it isn't in the ingredient list at all."}
    best = min(present, key=lambda it: (not it.effective, it.pos))
    caveat = best.entry.get("spotlight_caveat")
    if caveat:
        return {"claim_short": short, "verdict": "partly true",
                "reality": f"Claims {display}, and it's there, but {caveat}."}
    if best.effective:
        where = f"#{best.pos + 1} in the list" if best.above_line else "at an effective low dose"
        return {"claim_short": short, "verdict": "holds up",
                "reality": f"Claims {display}, and it's there: {best.name}, {where}."}
    return {"claim_short": short, "verdict": "partly true",
            "reality": f"Claims {display}, but {best.name} is near the end, likely a token amount."}


def judge_claims(product: Product) -> tuple:
    claims, claimed_tags = [], []
    for _, kind, key in detect_claims(product):
        if kind == "benefit":
            claims.append(judge_benefit(product, key))
            claimed_tags.append((key, claims[-1]["verdict"]))
        elif kind == "free_from":
            claims.append(judge_free_from(product, key))
        elif kind == "special":
            claims.append(judge_special(product, key))
        else:
            claims.append(judge_spotlight(product, key))
    return claims, claimed_tags


# ---------- card sections ----------

def hero_ingredients(product: Product) -> list:
    scored = []
    for it in product.items:
        e = it.entry
        if not e or e["category"] in NON_HERO or e.get("name") is None and not e["supports"]:
            continue
        best = max(e["supports"].values(), default=0)
        score = best * 2 + (2 if it.effective else 0) + (1 if e["category"] == "active" else 0) - it.pos * 0.15
        scored.append((score, it))
    scored.sort(key=lambda s: -s[0])
    return [{"name": it.raw, "job": it.entry["job"]} for _, it in scored[:3]]


def match_if(product: Product, claimed: list) -> list:
    unclaimed = [t for t, s in RULES["benefits"].items()
                 if s["domain"] == product.domain and t not in dict(claimed)]
    if product.domain == "skin" and not product.rinse_off:
        unclaimed += [t for t in ("hydration", "soothing") if t not in dict(claimed)]
    tags = [t for t, _ in claimed] + unclaimed
    out = []
    for tag in tags:
        sups = supports_for(product, tag)
        if strength_verdict(sups) != "holds up":
            continue
        spec = RULES["benefits"][tag]
        out.append({"chip": spec["audience"], "why": spec["why"].format(name=sups[0].item.name)})
        if len(out) == 4:
            break
    if len(out) < 2:
        occlusive = next((it for it in product.items[:6] if it.entry and it.entry["category"] in ("occlusive", "oil")), None)
        if occlusive and not product.rinse_off:
            chip = "dry, frizzy hair" if product.domain == "hair" else "dry skin, needs comfort"
            out.append({"chip": chip, "why": f"{occlusive.name} seals moisture in"})
    if len(out) < 2 and not all_flags(product):
        out.append({"chip": "sensitive skin, easy pick", "why": "No common irritants or allergens in the list"})
    return out[:4]


def watch_flags(flags: dict) -> list:
    out = []
    for flag, hits in flags.items():
        spec = RULES["flags"][flag]
        out.append({"flag": spec["flag"], "why": spec["why"].format(names=names(hits))})
    return out


def swipe_left_if(flags: dict) -> list:
    by_chip = {}
    for flag, hits in flags.items():
        spec = RULES["flags"][flag]
        by_chip.setdefault(spec["avoid"], spec["why"].format(names=names(hits)))
    return [{"chip": c, "why": w} for c, w in list(by_chip.items())[:4]]


def has(product: Product, flag: str) -> bool:
    return bool(flagged(product, flag))


def routine(product: Product) -> dict:
    t = product.type_name
    retinoid, acid = has(product, "retinoid"), has(product, "acid_exfoliant")
    bpo = has(product, "benzoyl_peroxide")
    vit_c = any(it.entry and it.entry.get("name") == "Ascorbic Acid" and it.effective for it in product.items)
    harsh = has(product, "harsh_surfactant") or bool(all_flags(product).get("soap"))

    if product.domain == "hair":
        if product.rinse_off:
            dandruff = strength_verdict(supports_for(product, "dandruff")) != "doesn't hold up"
            return {"when": "Wash days",
                    "how_often": "2-3 times weekly, leave 3-5 minutes" if dandruff else "2-3 times a week",
                    "follow_with": "Conditioner on the lengths" if "conditioner" not in t else "Leave-in or light oil on ends",
                    "avoid_with": "Nothing specific"}
        return {"when": "After washing", "how_often": "As needed on lengths",
                "follow_with": "Nothing needed after", "avoid_with": "Nothing specific"}

    if t in ("sunscreen", "sun screen", "sunblock", "sun cream", "sun stick"):
        return {"when": "AM, last skincare step", "how_often": "Daily; reapply every 2-3 hours outdoors",
                "follow_with": "Makeup if you like; nothing else needed", "avoid_with": "Nothing specific"}

    if product.rinse_off:
        return {"when": "AM and PM" if not harsh else "Once a day",
                "how_often": "Once daily, skip if skin feels tight" if harsh or acid or bpo else "Twice daily",
                "follow_with": "A moisturizer; add SPF in the morning",
                "avoid_with": "Other exfoliants in the same routine" if acid or bpo else "Nothing specific"}

    if retinoid:
        return {"when": "PM only", "how_often": "2-3 nights weekly, build up slowly",
                "follow_with": "A plain moisturizer; SPF every morning",
                "avoid_with": "AHA/BHA acids or benzoyl peroxide same night"}
    if acid:
        return {"when": "PM only", "how_often": "2-3 times weekly to start",
                "follow_with": "A moisturizer; SPF every morning",
                "avoid_with": "Retinoids and other acids same night"}
    if bpo:
        return {"when": "AM or PM", "how_often": "Once daily to start",
                "follow_with": "An oil-free moisturizer",
                "avoid_with": "Retinol or vitamin C at the same time"}
    if vit_c:
        return {"when": "AM", "how_often": "Once daily",
                "follow_with": "Moisturizer, then SPF",
                "avoid_with": "Strong acids or benzoyl peroxide"}
    moisturizer = t in ("moisturizer", "moisturiser", "cream", "face cream", "night cream", "day cream", "lotion", "body lotion", "balm", "body butter")
    return {"when": "AM and PM", "how_often": "Twice daily",
            "follow_with": "SPF in the morning" if moisturizer else "Moisturizer, then SPF by day",
            "avoid_with": "Nothing specific"}


def timeline(claimed: list) -> list:
    out = []
    for tag, verdict in claimed:
        spec = RULES["benefits"][tag]
        if verdict == "doesn't hold up" or not spec["when"]:
            continue
        when = spec["when"] + (", if at all" if verdict == "partly true" else "")
        out.append({"expect": spec["expect"], "when": when})
    return out


def one_liner(product: Product, heroes: list, score: int, has_claims: bool) -> str:
    type_name = product.type_name
    article = "An" if type_name[0] in "aeiou" else "A"
    hero_names = [h["name"] for h in heroes[:2]]
    built = f" built on {' and '.join(hero_names)}" if hero_names else ""
    if not has_claims:
        honesty = "no checkable claims found"
    elif score >= 85:
        honesty = "its claims check out"
    elif score >= 65:
        honesty = "most claims check out"
    elif score >= 40:
        honesty = "claims only half hold up"
    else:
        honesty = "marketing oversells it"
    return f"{article} {type_name}{built}; {honesty}."


# ---------- entry point ----------

def analyze(ingredients_text: str, claims_text: str) -> dict:
    product = build_product(ingredients_text, claims_text or "")
    claims, claimed_tags = judge_claims(product)
    score, verdict_word = compute_truth_score(claims)
    flags = all_flags(product)
    heroes = hero_ingredients(product)

    recognized = sum(1 for it in product.items if it.entry is not None)
    return {
        "one_liner": one_liner(product, heroes, score, bool(claims)),
        "claims": claims,
        "match_if": match_if(product, claimed_tags),
        "swipe_left_if": swipe_left_if(flags),
        "hero_ingredients": heroes,
        "routine": routine(product),
        "timeline": timeline(claimed_tags),
        "watch_flags": watch_flags(flags),
        "truth_score": score,
        "verdict_word": verdict_word,
        "disclaimer": "Not medical advice. Patch test new products first.",
        "coverage": {
            "recognized": recognized,
            "total": len(product.items),
            "unknown": product.unknown,
            # curated = our reviewed entries; official = EU CosIng name only; pattern = name-based guess
            "by_source": {
                "curated": sum(1 for it in product.items if it.how in ("exact", "fuzzy")),
                "official": sum(1 for it in product.items if it.how == "cosing"),
                "pattern": sum(1 for it in product.items if it.how == "pattern"),
            },
            "knowledge_version": get_kb().version,
            "cosing_synced": get_kb().cosing_synced,
        },
    }
