import os
import json
from flask import Flask, request, jsonify
from flask_cors import CORS
from dotenv import load_dotenv
import google.generativeai as genai

# Load variables from .env into the environment (this is where our Gemini API key lives)
load_dotenv()

app = Flask(__name__)

# CORS lets our Chrome extension (running on a different origin) call this backend
CORS(app)

GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY")
genai.configure(api_key=GEMINI_API_KEY)


ANALYSIS_PROMPT_TEMPLATE = """You are a cosmetic chemist analyzing a skincare/haircare product for a consumer. You will be given an ingredient list (in descending order of concentration) and the marketing claims made about the product.

Your job:
1. Identify the 3-5 most functionally important ingredients and briefly explain what each does.
2. For each marketing claim, state whether the ingredient list genuinely supports it, partially supports it, or doesn't support it — and explain why in plain language.
3. Flag any concerns (harsh actives, allergens, parabens, ingredients that contradict the claim, e.g. hidden whitening agents).
4. Give a short, honest, plain-language verdict a normal consumer can understand — no jargon.

Important: ingredient concentration is not disclosed, so use position in the list as a rough proxy for amount, and say so explicitly if relevant.

Respond ONLY in this JSON structure, with no markdown formatting, no backticks, just raw JSON:
{{
  "key_ingredients": [{{"name": "", "role": ""}}],
  "claims_analysis": [{{"claim": "", "verdict": "supported | partially supported | not supported", "explanation": ""}}],
  "concerns": [""],
  "summary": ""
}}

Ingredient list: {ingredients}
Marketing claims: {claims}
"""


@app.route("/health", methods=["GET"])
def health():
    """Simple check to confirm the server is running."""
    return jsonify({"status": "ok"})


@app.route("/analyze", methods=["POST"])
def analyze():
    """
    Main endpoint the Chrome extension will call.
    Takes scraped ingredients + claims, sends them to Gemini for analysis,
    and returns the structured JSON verdict.
    """
    data = request.get_json(silent=True) or {}

    ingredients_text = data.get("ingredients", "")
    claims_text = data.get("claims", "")

    if not ingredients_text:
        return jsonify({"error": "No ingredients text provided"}), 400

    prompt = ANALYSIS_PROMPT_TEMPLATE.format(
        ingredients=ingredients_text,
        claims=claims_text or "No specific claims provided"
    )

    try:
        model = genai.GenerativeModel("gemini-3.6-flash")
        response = model.generate_content(prompt)
        raw_text = response.text.strip()

        # Gemini sometimes wraps JSON in ```json fences even when told not to — strip if present
        if raw_text.startswith("```"):
            raw_text = raw_text.strip("`")
            raw_text = raw_text.replace("json\n", "", 1)

        parsed = json.loads(raw_text)
        return jsonify(parsed)

    except json.JSONDecodeError:
        return jsonify({
            "error": "Gemini returned non-JSON output",
            "raw_output": raw_text
        }), 500
    except Exception as e:
        return jsonify({"error": f"Gemini analysis failed: {str(e)}"}), 500


if __name__ == "__main__":
    app.run(debug=True, port=5000)