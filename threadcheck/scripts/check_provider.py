"""Verify the configured LLM + embedding endpoint works before ingesting anything.

  python scripts/check_provider.py
"""
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # allow running from any cwd

from app.config import get_settings
from app.providers.embeddings import get_embedder
from app.providers.llm import get_llm


def main() -> int:
    s = get_settings()
    print(f"provider={s.llm_provider}  base_url={s.llm_base_url}")
    print(f"llm_model={s.llm_model}  embed_model={s.embed_model}  embed_dim={s.embed_dim}")
    if s.llm_provider != "openai":
        print("LLM_PROVIDER is not 'openai'; nothing to check (mock mode).")
        return 0
    if not s.llm_api_key or s.llm_api_key.endswith("..."):
        print("FAIL: LLM_API_KEY is not set in .env")
        return 1

    t0 = time.perf_counter()
    out = get_llm(s).complete_json("TASK: verify_claim\nReturn JSON only: {\"ok\": true, \"model_says\": str}",
                                   "Say hello in one word.")
    print(f"chat OK  ({time.perf_counter() - t0:.2f}s): {out}")

    t0 = time.perf_counter()
    v = get_embedder(s).embed(["recycled polyester", "carbon neutral shipping"])
    print(f"embeddings OK  ({time.perf_counter() - t0:.2f}s): shape={v.shape}  "
          f"cos(sim)={float(v[0] @ v[1]):.3f}")
    print("All good. Next: docker compose up --build, or run python -m eval.run_eval --judge")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:  # show the provider's actual error text
        print(f"FAIL: {type(exc).__name__}: {exc}")
        body = getattr(getattr(exc, "response", None), "text", None)
        if body:
            print(body[:800])
        sys.exit(1)
