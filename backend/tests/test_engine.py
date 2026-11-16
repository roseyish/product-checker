import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from engine import analyze
from engine.knowledge import split_ingredients, get_kb


def verdicts(result):
    return {c["claim_short"]: c["verdict"] for c in result["claims"]}


def test_split_keeps_brackets_and_numeric_commas():
    tokens = split_ingredients("Ingredients: Aqua (Water), 1,2-Hexanediol, Butyrospermum Parkii (Shea, Organic) Butter, Parfum.")
    assert tokens == ["Aqua (Water)", "1,2-Hexanediol", "Butyrospermum Parkii (Shea, Organic) Butter", "Parfum"]


def test_lookup_handles_brackets_aliases_and_typos():
    kb = get_kb()
    assert kb.lookup("Butyrospermum Parkii (Shea) Butter")[0]["name"] == "Butyrospermum Parkii Butter"
    assert kb.lookup("CI 77891")[0]["name"] == "Titanium Dioxide"
    assert kb.lookup("Niacinamid")[0]["name"] == "Niacinamide"
    assert kb.lookup("Ceramide XYZ Complex")[1] == "pattern"


def test_official_cosing_names_are_recognised_with_pattern_knowledge():
    kb = get_kb()
    entry, how = kb.lookup("Ceramide NG")
    assert how == "cosing" and entry["supports"]["barrier"] == 2      # ceramide pattern layered on
    entry, how = kb.lookup("Tamarindus Indica Seed Gum")
    assert how == "cosing" and entry["name"] == "Tamarindus Indica Seed Gum"


def test_eu_regulation_flags_apply_to_every_ingredient():
    kb = get_kb()
    assert "restricted" in kb.lookup("Butylphenyl Methylpropional")[0]["flags"]    # Annex II
    assert "fragrance_allergen" in kb.lookup("Linalool")[0]["flags"]               # labelled allergen
    preservative = kb.lookup("Chloroxylenol")[0]                                   # Annex V, not curated
    assert preservative["category"] == "preservative" and preservative["marker"]
    assert kb.lookup("Phenoxyethanol")[0]["regulation"][0]["annex"] == "V"


def test_output_shape_matches_extension_contract():
    r = analyze("Aqua, Glycerin, Niacinamide, Phenoxyethanol", "Brightening serum")
    for key in ["one_liner", "claims", "match_if", "swipe_left_if", "hero_ingredients", "routine",
                "timeline", "watch_flags", "truth_score", "verdict_word", "disclaimer"]:
        assert key in r
    assert set(r["routine"]) == {"when", "how_often", "follow_with", "avoid_with"}
    for c in r["claims"]:
        assert c["verdict"] in ("holds up", "partly true", "doesn't hold up")


def test_deterministic():
    args = ("Aqua, Sodium Laureth Sulfate, Parfum, Ascorbyl Glucoside", "Vitamin C face wash, brightens")
    assert analyze(*args) == analyze(*args)


def test_strong_active_high_in_list_holds_up():
    r = analyze("Aqua, Niacinamide, Glycerin, Zinc PCA, Phenoxyethanol", "Niacinamide serum for oil control")
    v = verdicts(r)
    assert v["Controls oil and shine"] == "holds up"
    assert v["Made with Niacinamide"] == "holds up"


def test_active_after_one_percent_line_is_token_amount():
    r = analyze("Aqua, Glycerin, Cetearyl Alcohol, Phenoxyethanol, Parfum, Niacinamide", "Niacinamide brightening cream")
    v = verdicts(r)
    assert v["Made with Niacinamide"] == "partly true"
    assert v["Brightens, evens tone"] != "holds up"


def test_leave_on_active_in_face_wash_is_downgraded():
    r = analyze("Aqua, Sodium Laureth Sulfate, Cocamidopropyl Betaine, Ascorbic Acid, Sodium Benzoate",
                "Vitamin C brightening face wash")
    assert verdicts(r)["Brightens, evens tone"] != "holds up"


def test_missing_spotlight_ingredient_fails():
    r = analyze("Aqua, Glycerin, Phenoxyethanol", "Tea Tree face gel")
    assert verdicts(r)["Made with Tea Tree"] == "doesn't hold up"


def test_free_from_claims():
    r = analyze("Aqua, Sodium Lauryl Sulfate, Methylparaben", "Paraben-free, sulfate free shampoo for hair")
    v = verdicts(r)
    assert v["Paraben-free"] == "doesn't hold up"
    assert v["Sulfate-free"] == "doesn't hold up"


def test_soap_made_in_bottle_is_detected():
    r = analyze("Water, Potassium Hydroxide, Myristic Acid, Lauric Acid, Glycerin, Fragrance",
                "Soap-free cleanser, deep cleans")
    v = verdicts(r)
    assert v["Soap-free"] == "doesn't hold up"
    assert v["Deep cleans"] == "holds up"


def test_chemical_free_never_holds():
    r = analyze("Aqua, Aloe Barbadensis Leaf Juice", "Chemical free aloe gel")
    assert verdicts(r)["Chemical-free"] == "doesn't hold up"


def test_watch_flags_only_list_present_ingredients():
    r = analyze("Aqua, Glycerin, Parfum, Linalool, Methylisothiazolinone", "Body lotion")
    flags = {f["flag"] for f in r["watch_flags"]}
    assert {"Added fragrance", "Fragrance allergens", "High-allergy preservative"} <= flags
    clean = analyze("Aqua, Glycerin, Squalane, Phenoxyethanol", "Moisturizer")
    assert clean["watch_flags"] == []


def test_sunscreen_needs_filters():
    with_filters = analyze("Aqua, Zinc Oxide, Octocrylene, Glycerin, Phenoxyethanol", "SPF 50 sunscreen")
    without = analyze("Aqua, Glycerin, Phenoxyethanol, Titanium Dioxide", "Day cream with SPF 30")
    assert verdicts(with_filters)["Protects from sun"] == "holds up"
    assert verdicts(without)["Protects from sun"] != "holds up"
