import json
import shutil
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from engine.curation import (apply_queue, build_queue, dump_ingredients, load_ignore, load_log,
                             read_queue, record_unknown, write_queue)
from engine.knowledge import DATA_DIR, KnowledgeBase


@pytest.fixture
def data_dir(tmp_path):
    for name in ("ingredients.json", "rules.json"):
        shutil.copy(DATA_DIR / name, tmp_path / name)
    return tmp_path


def test_dump_round_trips_the_real_file():
    raw = json.loads((DATA_DIR / "ingredients.json").read_text(encoding="utf-8"))
    assert json.loads(dump_ingredients(raw)) == raw


def test_record_counts_and_keeps_example(data_dir):
    record_unknown(["Tamarindus Indica Seed Gum", "Niacinamid3"], example="Glow serum", data_dir=data_dir)
    record_unknown(["tamarindus indica seed gum"], example="Other", data_dir=data_dir)
    log = load_log(data_dir)
    assert log["tamarindus indica seed gum"]["count"] == 2
    assert log["tamarindus indica seed gum"]["example"] == "Glow serum"


def test_old_count_only_log_is_upgraded(data_dir):
    (data_dir / "unknown_ingredients.json").write_text('{"foo extract x": 3}', encoding="utf-8")
    assert load_log(data_dir)["foo extract x"]["count"] == 3


def test_queue_ranks_by_frequency_and_suggests_aliases(data_dir):
    record_unknown(["Glycerine Extra"], data_dir=data_dir)
    record_unknown(["Nyacinamide"] * 3, data_dir=data_dir)
    rows, _ = build_queue(KnowledgeBase(data_dir), load_log(data_dir))
    assert rows[0]["name"] == "Nyacinamide" and rows[0]["count"] == 3
    assert rows[0]["action"] == "alias" and rows[0]["alias_of"] == "Niacinamide"


def test_full_loop_new_alias_ignore(data_dir):
    record_unknown(["Kumkumadi Tailam", "Nyacinamide", "Buy 2 get 1 free on all serums today only"], data_dir=data_dir)
    rows, _ = build_queue(KnowledgeBase(data_dir), load_log(data_dir))
    by_name = {r["name"]: r for r in rows}
    by_name["Kumkumadi Tailam"].update(action="new", plain="ayurvedic saffron oil blend", job="rich oil blend, mild glow",
                                       category="oil", supports="brightening:1; hydration:2", flags="essential_oil",
                                       spotlight="kumkumadi")
    write_queue(rows, data_dir / "q.csv")

    summary = apply_queue(read_queue(data_dir / "q.csv"), data_dir)
    assert summary["errors"] == []
    assert (summary["new"], summary["alias"], summary["ignore"]) == (1, 1, 1)

    kb = KnowledgeBase(data_dir)
    assert kb.lookup("Kumkumadi Tailam")[0]["supports"] == {"brightening": 1, "hydration": 2}
    assert kb.lookup("Nyacinamide")[1] == "exact"
    assert "buy 2 get 1 free on all serums today only" in load_ignore(data_dir)
    assert load_log(data_dir) == {}


def test_invalid_rows_block_the_whole_apply(data_dir):
    before = (data_dir / "ingredients.json").read_text(encoding="utf-8")
    rows = [
        {"name": "Good Extract", "action": "new", "plain": "plant", "job": "does a thing", "category": "botanical"},
        {"name": "Bad One", "action": "new", "plain": "x", "job": "y", "category": "botanical", "supports": "glowiness:5"},
    ]
    summary = apply_queue(rows, data_dir)
    assert len(summary["errors"]) == 1 and "glowiness" in summary["errors"][0]
    assert (data_dir / "ingredients.json").read_text(encoding="utf-8") == before
