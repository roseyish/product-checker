"""LLM provider abstraction.

Two implementations:
  * OpenAICompatibleLLM: talks to any /v1/chat/completions endpoint (OpenAI, Ollama, vLLM, Qwen servers).
  * MockLLM: deterministic, key-free responses so tests and CI never depend on a network call.
"""
from __future__ import annotations

import json
import re
from abc import ABC, abstractmethod
from typing import Any

import httpx

from app.config import Settings


class LLM(ABC):
    @abstractmethod
    def complete_json(self, system: str, user: str) -> dict[str, Any]:
        """Return a parsed JSON object. Implementations must raise on invalid JSON."""


def _extract_json(text: str) -> dict[str, Any]:
    """Tolerate ```json fences and leading prose from smaller models."""
    text = text.strip()
    fence = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.S)
    if fence:
        text = fence.group(1)
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError(f"LLM did not return JSON: {text[:200]}")
    return json.loads(text[start : end + 1])


class OpenAICompatibleLLM(LLM):
    def __init__(self, settings: Settings, timeout: float = 60.0):
        self.base_url = settings.llm_base_url.rstrip("/")
        self.model = settings.llm_model
        self.headers = {"Authorization": f"Bearer {settings.llm_api_key}", "Content-Type": "application/json"}
        self.client = httpx.Client(timeout=timeout)

    def complete_json(self, system: str, user: str) -> dict[str, Any]:
        payload = {
            "model": self.model,
            "temperature": 0,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "response_format": {"type": "json_object"},
        }
        resp = self.client.post(f"{self.base_url}/chat/completions", headers=self.headers, json=payload)
        if resp.status_code == 400 and "response_format" in resp.text:
            # Some local servers reject response_format; retry without it.
            payload.pop("response_format")
            resp = self.client.post(f"{self.base_url}/chat/completions", headers=self.headers, json=payload)
        resp.raise_for_status()
        content = resp.json()["choices"][0]["message"]["content"]
        return _extract_json(content)


class MockLLM(LLM):
    """Heuristic stand-in. Good enough to exercise the full pipeline and produce a baseline in eval."""

    def complete_json(self, system: str, user: str) -> dict[str, Any]:
        if "TASK: extract_claim" in system:
            return self._extract(user)
        if "TASK: verify_claim" in system:
            return self._verify(user)
        if "TASK: judge" in system:
            return {"faithful": True, "score": 1.0, "reason": "mock judge"}
        raise ValueError("MockLLM received an unknown task")

    @staticmethod
    def _extract(user: str) -> dict[str, Any]:
        claim = user.split("CLAIM:", 1)[-1].strip()
        lower = claim.lower()
        if re.search(r"\b(carbon[- ]neutral|net[- ]zero|climate[- ]neutral)\b", lower):
            ctype = "carbon"
        elif re.search(r"\b(recycled|organic|regenerative|deadstock)\b", lower):
            ctype = "material"
        elif re.search(r"\b(gots|oeko[- ]?tex|grs|bluesign|fair ?trade|certified)\b", lower):
            ctype = "certification"
        elif re.search(r"\b(water|energy|emissions?|waste)\b", lower):
            ctype = "impact_metric"
        else:
            ctype = "general_environmental"
        return {"normalized_claim": claim.rstrip("."), "claim_type": ctype,
                "quantities": re.findall(r"\d+(?:\.\d+)?\s*%", claim)}

    @staticmethod
    def _verify(user: str) -> dict[str, Any]:
        """Sentence-level lexical heuristic. Picks the best-matching evidence sentence, then checks
        whether the claim's numbers appear in it and whether it carries a qualifier/contradiction."""
        claim_m = re.search(r"CLAIM:\s*(.*?)\n", user, re.S)
        evidence = user.split("EVIDENCE:", 1)[-1]
        claim_text = (claim_m.group(1) if claim_m else "").lower()
        if not evidence.strip() or "[none]" in evidence.lower():
            return {"verdict": "insufficient_evidence", "confidence": 0.3,
                    "rationale": "No evidence retrieved.", "citation_ids": []}

        # map every sentence to the chunk id it belongs to
        sents: list[tuple[str, str]] = []
        for block in re.split(r"\n\n(?=\[chunk_)", evidence.strip()):
            m = re.match(r"\[(chunk_[^\]]+)\]", block)
            cid = m.group(1) if m else ""
            body = re.sub(r"^\[chunk_[^\]]+\]\s*\([^)]*\)\s*", "", block)
            sents += [(cid, x.lower()) for x in re.split(r"(?<=[.!?])\s+", body) if x.strip()]

        stop = {"our", "the", "and", "with", "from", "that", "this", "are", "was", "were", "made", "brand",
                "products", "product", "all", "every", "of", "in", "is", "a", "an", "to", "by", "for", "has"}
        cwords = {w for w in re.findall(r"[a-z]{3,}", claim_text)} - stop
        nums = re.findall(r"\d+(?:\.\d+)?", claim_text)

        def cov(sent: str) -> float:
            return sum(1 for w in cwords if w in sent) / max(1, len(cwords))

        best_cid, best_sent = max(sents, key=lambda p: cov(p[1]))
        coverage = cov(best_sent)
        ids = [best_cid] if best_cid else []
        num_ok = all(n in best_sent for n in nums) if nums else True
        qualifiers = ("not ", "no ", "does not", "do not", "remaining", "except", "excluded", "not included",
                      "not covered", "does not guarantee", "not currently", "below", "downcycled", "virgin",
                      "have not", "is not", "are not")
        qualified = any(q in best_sent for q in qualifiers)
        absolute = bool(re.search(r"\b(100%|all|every|entire|no|zero|company|brand)\b", claim_text))

        if coverage < 0.34:
            return {"verdict": "insufficient_evidence" if coverage < 0.15 else "unsupported",
                    "confidence": round(0.35 + 0.3 * coverage, 2),
                    "rationale": "Evidence does not address the claim's specific assertion.", "citation_ids": ids}
        if nums and not num_ok:
            return {"verdict": "unsupported", "confidence": round(0.55 + 0.3 * coverage, 2),
                    "rationale": "Evidence figures do not match the claim.", "citation_ids": ids}
        if qualified and absolute:
            return {"verdict": "misleading", "confidence": round(0.55 + 0.35 * coverage, 2),
                    "rationale": "Evidence supports a narrower scope than the claim implies.", "citation_ids": ids}
        if qualified and not nums:
            return {"verdict": "unsupported", "confidence": round(0.5 + 0.3 * coverage, 2),
                    "rationale": "Evidence contradicts or qualifies the claim.", "citation_ids": ids}
        return {"verdict": "supported", "confidence": round(0.6 + 0.4 * coverage, 2),
                "rationale": "Evidence restates the claim's subject and quantities.", "citation_ids": ids}


def get_llm(settings: Settings) -> LLM:
    return OpenAICompatibleLLM(settings) if settings.llm_provider == "openai" else MockLLM()
