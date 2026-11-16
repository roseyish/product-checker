"""Ingredient knowledge base: parsing raw ingredient text and matching it to curated entries
and to the official EU CosIng database."""
import difflib
import html
import json
import re
from functools import lru_cache
from pathlib import Path

DATA_DIR = Path(__file__).resolve().parent.parent / "data"

# Categories that are default 1%-line markers when an entry doesn't say otherwise.
MARKER_CATEGORIES = {"preservative", "chelator"}

# CosIng function -> (our category, plain-words job, claim support it implies on its own).
# Order matters: the first function an ingredient has in this list decides its category.
# Support from a function alone is kept weak (1), except cleansing and UV filtering, which
# CosIng only assigns to ingredients that really do that job. Curated entries always win.
COSING_FUNCTIONS = [
    ("UV FILTER", "uv_filter", "filters UV light", {"sun": 2}),
    ("UV ABSORBER", "uv_filter", "absorbs UV light", {}),
    ("PRESERVATIVE", "preservative", "keeps microbes out", {}),
    ("ANTIDANDRUFF", "active", "helps control dandruff", {"dandruff": 1}),
    ("KERATOLYTIC", "active", "loosens dead skin", {"exfoliation": 1}),
    ("EXFOLIATING", "exfoliant", "exfoliates", {"exfoliation": 1}),
    ("ABRASIVE", "exfoliant", "scrubs", {"exfoliation": 1}),
    ("SURFACTANT - CLEANSING", "surfactant", "cleanses and foams", {"cleansing": 2}),
    ("CLEANSING", "surfactant", "cleanses", {"cleansing": 2}),
    ("PERFUMING", "fragrance", "adds scent", {}),
    ("FRAGRANCE", "fragrance", "adds scent", {}),
    ("COLORANT", "colorant", "adds colour", {}),
    ("HAIR DYEING", "colorant", "dyes hair", {}),
    ("CHELATING", "chelator", "stabilizes the formula", {}),
    ("HAIR CONDITIONING", "conditioner", "conditions hair", {"hair_smooth": 1}),
    ("SOOTHING", "botanical", "calms skin", {"soothing": 1}),
    ("HUMECTANT", "humectant", "holds moisture", {"hydration": 1}),
    ("MOISTURISING", "humectant", "moisturises", {"hydration": 1}),
    ("SKIN PROTECTING", "occlusive", "protects skin", {"barrier": 1}),
    ("EMOLLIENT", "emollient", "softens and smooths skin", {}),
    ("SKIN CONDITIONING - OCCLUSIVE", "occlusive", "seals in moisture", {"hydration": 1}),
    ("EMULSIFYING", "emulsifier", "blends oil and water", {}),
    ("SURFACTANT - EMULSIFYING", "emulsifier", "blends oil and water", {}),
    ("VISCOSITY CONTROLLING", "thickener", "adjusts thickness", {}),
    ("FILM FORMING", "thickener", "forms a light film", {}),
    ("ABSORBENT", "absorbent", "absorbs oil", {"oil_control": 1}),
    ("ANTIOXIDANT", "antioxidant", "protects against oxidation", {}),
    ("ANTIMICROBIAL", "preservative", "limits microbes", {"antibacterial": 1}),
    ("BUFFERING", "ph_adjuster", "adjusts pH", {}),
    ("PH ADJUSTERS", "ph_adjuster", "adjusts pH", {}),
    ("SOLVENT", "solvent", "dissolves other ingredients", {}),
    ("SMOOTHING", "emollient", "smooths the surface", {}),
    ("OPACIFYING", "other", "makes the product opaque", {}),
    ("SKIN CONDITIONING", "emollient", "conditions skin", {}),
]
FUNCTION_INDEX = {f: (cat, job, sup) for f, cat, job, sup in COSING_FUNCTIONS}
FUNCTION_ORDER = [f for f, *_ in COSING_FUNCTIONS]


def norm(text: str) -> str:
    """Canonical form for matching: lowercase, no footnote marks, tidy spacing."""
    t = text.lower().replace("’", "'").replace(" ", " ")
    t = re.sub(r"[*†‡®™°]+", "", t)
    t = re.sub(r"\s*/\s*", "/", t)
    t = re.sub(r"\s+", " ", t)
    return t.strip(" .;:-")


PERCENT_RE = re.compile(r"(\d+(?:\.\d+)?)\s*%")


# Label text that ends up in scraped ingredient blocks on Indian listings: manufacturer
# addresses, PIN codes, helplines, URLs. Never an ingredient.
NOT_INGREDIENT_RE = re.compile(
    r"\b\d{3}\s?\d{3}\b|toll[- ]?free|customer care|helpline|e-?mail|www\.|https?:|@|"
    r"\b(mfd|mfg|manufactured|marketed|distributed|imported) by\b|\bfloor\b|\broad\b|\bp\.?\s?o\.? box\b|"
    r"\b(pvt|ltd|llp|inc)\b\.?$|best before|batch no|lic\.? no",
    re.I,
)

# Pharmacopoeia grade suffixes common on Indian labels ("Glycerin IP", "Zinc Oxide USP")
GRADE_SUFFIX_RE = re.compile(r"\s+\(?(ip|bp|usp|nf|ph\.? ?eur\.?|i\.p\.|b\.p\.|u\.s\.p\.)\)?$")

# British vs US INCI spellings, and OCR slips ("Cl 77891" for "CI 77891", "HCI" for "HCl")
SPELLING_VARIANTS = [
    (re.compile(r"aluminium"), "aluminum"), (re.compile(r"sulphate"), "sulfate"),
    (re.compile(r"sulphur"), "sulfur"), (re.compile(r"colour"), "color"),
    (re.compile(r"^cl[ -]?(\d{5})"), r"ci \1"), (re.compile(r"^c\.i\.? ?(\d{5})"), r"ci \1"),
    (re.compile(r" hci$"), " hcl"),
]


def split_ingredients(text: str) -> list:
    """Split an INCI statement on commas, ignoring commas inside brackets and in names like 1,2-Hexanediol."""
    t = html.unescape(text)
    t = re.sub(r"^\s*(full\s+)?(key\s+)?ingredients?\s*(list)?\s*[:\-]\s*", "", t, flags=re.I)
    t = re.sub(r"\b(may contain|\[\+/-\]|\+/-)\s*:?", ",", t, flags=re.I)
    parts, buf, depth = [], [], 0
    for i, ch in enumerate(t):
        if ch in "([":
            depth += 1
        elif ch in ")]":
            depth = max(0, depth - 1)
        is_digit_comma = ch == "," and 0 < i < len(t) - 1 and t[i - 1].isdigit() and t[i + 1].isdigit()
        if depth == 0 and ch in ",;•|" and not is_digit_comma:
            parts.append("".join(buf))
            buf = []
        else:
            buf.append(ch)
    parts.append("".join(buf))

    tokens = []
    for p in parts:
        # Marketing text glued after the last ingredient: "Limonene. *Organic ingredient"
        p = re.split(r"\.\s+(?=[A-Z*])", p.strip())[0]
        p = p.strip(" .\"'“”")
        if not p or len(p) > 90 or len(p.split()) > 9 or len(p) < 3 or NOT_INGREDIENT_RE.search(p):
            continue
        tokens.append(p)
    return tokens


def name_candidates(raw: str) -> list:
    """Spellings to try for one token: as written, without brackets, bracket contents, slash parts."""
    n = norm(PERCENT_RE.sub("", raw))
    n = GRADE_SUFFIX_RE.sub("", n) or n
    inner = [norm(m) for m in re.findall(r"\(([^)]*)\)", n)]
    outer = norm(re.sub(r"\([^)]*\)", " ", n))
    out = [n, outer] + inner
    for c in list(out):
        for rx, repl in SPELLING_VARIANTS:
            variant = rx.sub(repl, c)
            if variant != c:
                out.append(variant)
    for c in list(out):
        if "/" in c:
            out += [norm(p) for p in c.split("/")]
    seen, result = set(), []
    for c in out:
        if c and c not in seen and not re.fullmatch(r"[\d. %]+", c):
            seen.add(c)
            result.append(c)
    return result


class KnowledgeBase:
    def __init__(self, data_dir: Path = DATA_DIR):
        raw = json.loads((data_dir / "ingredients.json").read_text(encoding="utf-8"))
        self.version = raw["version"]
        self.entries = [self._finish(e) for e in raw["ingredients"]]
        self.index = {}
        for e in self.entries:
            for alias in [e["name"]] + e.get("aliases", []):
                self.index.setdefault(norm(alias), e)
        self.keys = list(self.index)
        self.patterns = [(re.compile(p["match"]), self._finish(dict(p, name=None))) for p in raw["patterns"]]

        # Official layer: every INCI name in the EU CosIng database with its functions and its
        # status in the Annexes of Regulation 1223/2009 (built by scripts/sync_cosing.py).
        self.cosing, self.cosing_synced = {}, None
        cosing_path = data_dir / "cosing.json"
        if cosing_path.exists():
            payload = json.loads(cosing_path.read_text(encoding="utf-8"))
            self.cosing_synced = payload.get("synced")
            self.cosing = payload["ingredients"]
            for entry in list(self.cosing.values()):
                for alt in ("usa", "inn", "ph_eur"):  # US INCI, INN and pharmacopoeia spellings
                    if entry.get(alt):
                        self.cosing.setdefault(norm(entry[alt]), entry)

        self.spotlights = {}
        for e in self.entries:
            for word in e.get("spotlight", []):
                self.spotlights.setdefault(word, []).append(e["name"])

    @staticmethod
    def _finish(e: dict) -> dict:
        e.setdefault("supports", {})
        e.setdefault("flags", [])
        e.setdefault("marker", e.get("category") in MARKER_CATEGORIES)
        e.setdefault("low_dose_ok", False)
        e.setdefault("rinse_ok", False)
        return e

    @lru_cache(maxsize=8192)
    def lookup(self, raw: str):
        """Return (entry, how) where how is exact/cosing/fuzzy/pattern, or (None, None).

        Order: our curated entries, then an exact official CosIng name, then a near-miss of a
        curated name (typos), then name patterns. EU regulatory flags are added to every hit.
        """
        cands = name_candidates(raw)
        entry, how = self._match(cands)
        if entry is None:
            return None, None
        return self._with_regulation(entry, cands), how

    def _match(self, cands: list):
        for c in cands:
            if c in self.index:
                return self.index[c], "exact"
        for c in cands:
            if c in self.cosing:
                return self._from_cosing(c, self.cosing[c]), "cosing"
        for c in cands:
            if len(c) >= 6:
                close = difflib.get_close_matches(c, self.keys, n=1, cutoff=0.92)
                if close:
                    return self.index[close[0]], "fuzzy"
        for c in cands:
            pattern = self._pattern(c)
            if pattern:
                return pattern, "pattern"
        return None, None

    def _pattern(self, name: str):
        for rx, entry in self.patterns:
            if rx.search(name):
                return entry
        return None

    def _from_cosing(self, key: str, cos: dict) -> dict:
        """Entry for an official INCI name we haven't curated: category and job from its
        functions and Annex status, enriched by a matching name pattern when there is one."""
        functions = cos.get("functions", [])
        annexes = {r.get("annex") for r in cos.get("regulation", [])}
        known = [f for f in FUNCTION_ORDER if f in functions]
        category = FUNCTION_INDEX[known[0]][0] if known else "other"
        supports = {}
        for f in known:
            for tag, level in FUNCTION_INDEX[f][2].items():
                supports[tag] = max(supports.get(tag, 0), level)
        jobs = list(dict.fromkeys(FUNCTION_INDEX[f][1] for f in known))[:2]

        # The Annexes say what a substance is allowed to be used as
        if "V" in annexes:
            category = "preservative"
        elif "VI" in annexes:
            category = "uv_filter"
            supports["sun"] = max(supports.get("sun", 0), 2)
        elif "IV" in annexes and category in ("other", "emollient"):
            category = "colorant"

        entry = {
            "name": cos["inci"].title() if cos["inci"].isupper() else cos["inci"],
            "plain": category.replace("_", " ") if category != "other" else "cosmetic ingredient",
            "job": ", ".join(jobs) or "listed in the EU ingredient database",
            "category": category,
            "supports": supports,
            "marker": category in MARKER_CATEGORIES,
        }
        pattern = self._pattern(key)
        if pattern:  # e.g. "...ceramide..." or "...leaf oil": our curated pattern knowledge is richer
            entry.update({k: pattern[k] for k in ("plain", "job", "category", "flags", "low_dose_ok", "rinse_ok")})
            entry["supports"] = {**supports, **pattern["supports"]}
            entry["marker"] = pattern["marker"] or entry["marker"]
        return self._finish(entry)

    def _with_regulation(self, entry: dict, cands: list) -> dict:
        """Add EU status from CosIng: Annex II = prohibited, labelled allergen (Reg 2023/1545)."""
        cos = next((self.cosing[c] for c in cands if c in self.cosing), None)
        if cos is None and entry.get("name"):
            cos = self.cosing.get(norm(entry["name"]))
        if not cos or not cos.get("regulation"):
            return entry
        regs = cos["regulation"]
        flags = list(entry["flags"])
        if any(r.get("label_allergen") for r in regs) and "fragrance_allergen" not in flags:
            flags.append("fragrance_allergen")
        # Only outright bans; conditional Annex II entries ban a grade or process, not the name
        if any(r.get("annex") == "II" and not r.get("conditional") for r in regs) and "restricted" not in flags:
            flags.append("restricted")
        return dict(entry, flags=flags, regulation=regs)


_kb = None


def get_kb() -> KnowledgeBase:
    global _kb
    if _kb is None:
        _kb = KnowledgeBase()
    return _kb
