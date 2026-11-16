# Knowledge base sources

| File | What it is | Source | Licence | Refresh |
|---|---|---|---|---|
| `ingredients.json` | Curated entries: plain-words job, claim strengths, flags, marketing keywords | Written and reviewed in-house | Ours | Through the curation loop (`scripts/curation_report.py` then `scripts/apply_curation.py`) |
| `rules.json` | Claim phrases, free-from checks, warning flags, product types | Written in-house | Ours | By hand |
| `cosing.json` | ~33,600 official INCI names with functions, CAS/EC numbers, and status in Annexes II to VI of Regulation (EC) 1223/2009, including labelled fragrance allergens (Reg (EU) 2023/1545) | European Commission, CosIng database | CC BY 4.0 (Commission Decision 2011/833/EU) | Monthly: `python scripts/sync_cosing.py` |

## Required attribution

Any public page or app screen that shows data derived from `cosing.json` must credit:

> Source: European Commission, CosIng database (https://ec.europa.eu/growth/tools-databases/cosing/)

CosIng has no legal value. For legal questions, the binding text is Regulation (EC) 1223/2009 on EUR-Lex.

## How the engine uses each layer

1. **Curated entry** (exact name or alias, then a near-miss typo) wins.
2. **Official CosIng name**: category and a short job come from its EU functions and Annex. Support for claims is deliberately weak, because an EU function says what an ingredient is used for, not how well it works. Name patterns (ceramide, peptide, essential oil and so on) add our own knowledge on top.
3. **Name pattern** only, for names in neither list.
4. EU status is added to every match: an unconditional Annex II entry adds the `restricted` flag, and a labelled fragrance allergen adds `fragrance_allergen`. Conditional Annex II entries (a banned grade or process, such as "Petrolatum, except if the full refining history is known") do not flag the ingredient name.

## Not used, and why

EWG Skin Deep, INCIDecoder, Paula's Choice, CosDNA and SkinSort have no open licence and block automated access. Do not scrape them.
