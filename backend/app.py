import os
import re
import json
import hashlib
from collections import OrderedDict
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from dotenv import load_dotenv
import google.generativeai as genai

# Load variables from .env into the environment (this is where our Gemini API key lives)
load_dotenv()

app = FastAPI(title="Product Checker API")

# CORS lets our Chrome extension (running on a different origin) call this backend
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
genai.configure(api_key=GEMINI_API_KEY)


ANALYSIS_PROMPT_TEMPLATE = """You are a cosmetic chemist writing a short, honest buying guide for a normal shopper. You will be given an ingredient list (in descending order of concentration) and the marketing claims made about the product.

Write for a consumer, not a chemist: short sentences, everyday words, no jargon. When you name a chemical, immediately say what it is in plain words (e.g. "Potassium Hydroxide, a strong soap base").

The result is shown as a compact "match card", so every field must be SHORT. Produce exactly this:

1. one_liner: ONE sentence, max 18 words: what this product really is and does.

2. claims: one entry per DISTINCT marketing claim found in the input. Never merge two claims into one and never skip a claim; if the input contains three claims, output three entries. Judge only claim-vs-ingredient honesty, not product quality.
   - "claim_short": the claim compressed to 3-6 words, e.g. "Evens tone, adds glow".
   - "verdict": exactly one of "holds up", "partly true", "doesn't hold up". Use this rubric:
     * "holds up": the ingredient list contains a well-known active for this claim, high enough in the list to plausibly work.
     * "partly true": an ingredient loosely supports the claim, but it is weak, too low in the list, or the claim overstates it.
     * "doesn't hold up": nothing in the list meaningfully supports the claim.
   - "reality": ONE sentence, max 20 words, of the pattern "Claims X, but can't fully deliver: <ingredient> <plain-words reason>" (or "and it does, thanks to..." when it holds up).

3. match_if: 2-4 entries for when this product IS a good pick.
   - "chip": max 5 words, the person or goal, e.g. "oily skin, deep cleanse".
   - "why": max 12 words naming the ingredient that makes it work.

4. swipe_left_if: entries for real risks.
   - "chip": max 5 words, who should avoid it, e.g. "dry or sensitive skin".
   - "why": max 12 words: what may happen and the ingredient causing it, in plain words.

5. hero_ingredients: the 2-3 ingredients doing the real work in this product.
   - "name": the ingredient name as written in the list.
   - "job": max 10 words, plain words, what it actually does here, e.g. "strong soap base, cuts oil fast".

6. routine: how to use this product for best results, derived from the ingredient types.
   - "when": max 4 words, e.g. "PM only" or "AM and PM".
   - "how_often": max 8 words, e.g. "Once daily, skip if skin feels tight".
   - "follow_with": max 12 words, what to apply after for better results.
   - "avoid_with": max 12 words, what NOT to layer the same day, or "Nothing specific".

7. timeline: one entry per claim whose verdict is "holds up" or "partly true": when results realistically show.
   - "expect": max 4 words, the result, e.g. "Oil control".
   - "when": max 8 words, honest, e.g. "First wash" or "4-6 weeks, if at all".

8. watch_flags: known irritants or allergens actually present in the list (empty list if none).
   - "flag": max 5 words, e.g. "Added fragrance".
   - "why": max 12 words, factual, e.g. "Fragrance, a common trigger for sensitive skin". Never medical advice.

Rules:
- Base EVERYTHING only on the ingredient list given. Never assume an ingredient that is not listed.
- Ingredient concentration is not disclosed, so use position in the list as a rough proxy for amount.
- Be honest: do not soften a real concern, and do not invent concerns that aren't in the list.
- When torn between two verdicts for a claim, pick the harsher one. Marketing gets no benefit of the doubt.
- You are on the shopper's side, not the brand's. Nothing you write is promotional.
- Zero jargon: any chemical you name gets 2-3 plain words of explanation, e.g. "Potassium Hydroxide, a strong soap base".
- Routine and timeline must follow from the ingredients, not generic skincare advice.
- watch_flags only lists ingredients actually in the list; do not invent flags, and stay factual, never medical.
- Respect every max-word limit strictly.

Respond ONLY in this JSON structure, with no markdown formatting, no backticks, just raw JSON:
{{
  "one_liner": "",
  "claims": [{{"claim_short": "", "verdict": "holds up | partly true | doesn't hold up", "reality": ""}}],
  "match_if": [{{"chip": "", "why": ""}}],
  "swipe_left_if": [{{"chip": "", "why": ""}}],
  "hero_ingredients": [{{"name": "", "job": ""}}],
  "routine": {{"when": "", "how_often": "", "follow_with": "", "avoid_with": ""}},
  "timeline": [{{"expect": "", "when": ""}}],
  "watch_flags": [{{"flag": "", "why": ""}}]
}}

Ingredient list: {ingredients}
Marketing claims: {claims}
"""


# Score per claim verdict; truth_score is the average, so the same verdicts
# always produce the same number instead of the model picking one freely.
VERDICT_POINTS = {"holds up": 100, "partly true": 50, "doesn't hold up": 0}

# Score bands -> verdict word, so the headline word always agrees with the number.
VERDICT_WORD_BANDS = [
    (85, "Honest"),
    (65, "Mostly true"),
    (40, "Half-true"),
    (15, "Overhyped"),
    (0, "Pure hype"),
]


def compute_truth_score(claims: list) -> tuple:
    """Derive (truth_score, verdict_word) from the per-claim verdicts."""
    points = [
        VERDICT_POINTS[c["verdict"]]
        for c in claims
        if isinstance(c, dict) and c.get("verdict") in VERDICT_POINTS
    ]
    if not points:
        return 50, "No claims"
    score = round(sum(points) / len(points))
    for threshold, word in VERDICT_WORD_BANDS:
        if score >= threshold:
            return score, word
    return score, "Pure hype"


def normalize_text(text: str) -> str:
    """Collapse case and whitespace so trivial scrape differences hit the same cache entry."""
    return re.sub(r"\s+", " ", text.strip().lower())


# In-memory cache: same product -> same card, no repeat Gemini call.
# OrderedDict so we can drop the oldest entry once the cap is hit.
CACHE_MAX_ENTRIES = 500
analysis_cache = OrderedDict()


class AnalyzeRequest(BaseModel):
    """Body the Chrome extension sends: scraped ingredients plus marketing claims."""
    ingredients: str = ""
    claims: str = ""


@app.get("/health")
def health():
    """Simple check to confirm the server is running."""
    return {"status": "ok"}


@app.post("/analyze")
def analyze(body: AnalyzeRequest):
    """
    Main endpoint the Chrome extension will call.
    Takes scraped ingredients + claims, sends them to Gemini for analysis,
    and returns the structured JSON verdict.
    """
    if not body.ingredients:
        return JSONResponse(status_code=400, content={"error": "No ingredients text provided"})

    cache_key = hashlib.sha256(
        (normalize_text(body.ingredients) + "|" + normalize_text(body.claims)).encode("utf-8")
    ).hexdigest()
    print(f"[analyze] key={cache_key[:8]} cached={cache_key in analysis_cache} "
          f"ingredients={len(body.ingredients)} chars, claims sent: {body.claims!r}")
    if cache_key in analysis_cache:
        return analysis_cache[cache_key]

    prompt = ANALYSIS_PROMPT_TEMPLATE.format(
        ingredients=body.ingredients,
        claims=body.claims or "No separate claims text was captured. Extract the marketing claims yourself from the product text above."
    )

    raw_text = ""
    try:
        # temperature=0 so the same product gives (near-)identical output every run
        model = genai.GenerativeModel(
            "gemini-3.6-flash",
            generation_config={"temperature": 0, "top_p": 1},
        )
        response = model.generate_content(prompt)
        raw_text = response.text.strip()

        # Gemini sometimes wraps JSON in ```json fences even when told not to, so strip if present
        if raw_text.startswith("```"):
            raw_text = raw_text.strip("`")
            raw_text = raw_text.replace("json\n", "", 1)

        result = json.loads(raw_text)

        # Score and verdict word are derived here, not by the model, so they can't drift
        score, verdict_word = compute_truth_score(result.get("claims", []))
        result["truth_score"] = score
        result["verdict_word"] = verdict_word
        # Fixed server-side so it never depends on the model including it
        result["disclaimer"] = "Not medical advice. Patch test new products first."
        print(f"[analyze] key={cache_key[:8]} judged {len(result.get('claims', []))} claims "
              f"-> score={score} ({verdict_word})")

        analysis_cache[cache_key] = result
        if len(analysis_cache) > CACHE_MAX_ENTRIES:
            analysis_cache.popitem(last=False)

        return result

    except json.JSONDecodeError:
        return JSONResponse(status_code=500, content={
            "error": "Gemini returned non-JSON output",
            "raw_output": raw_text
        })
    except Exception as e:
        return JSONResponse(status_code=500, content={"error": f"Gemini analysis failed: {str(e)}"})


if __name__ == "__main__":
    import uvicorn
    uvicorn.run("app:app", port=5000, reload=True)
